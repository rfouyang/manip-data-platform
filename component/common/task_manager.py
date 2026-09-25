import multiprocessing
import multiprocessing.connection
import os
import signal
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

from loguru import logger

from component.common.output_publish import remove_task_temporaries, temporary_path
from util import log_helper
from util.storage_helper import Storage


ACTIVE_STATUSES = ("running", "validating", "cancelling")
UNFINISHED_STATUSES = ("queued", *ACTIVE_STATUSES)
RETRYABLE_STATUSES = ("failed", "cancelled", "interrupted")
# 协作取消的等待时间；超时后终止工作进程。
DEFAULT_CANCEL_TIMEOUT = 10.0
DEFAULT_POLL_INTERVAL = 0.5


def now():
    value = datetime.now().isoformat(timespec="seconds")
    return value


class TaskCancelled(Exception):
    pass


class TaskReporter:
    """工作进程内使用：写进度、检查取消。只持有可序列化的 db 路径与任务 id。"""

    def __init__(self, db_path, task_id):
        self.storage = Storage(db_path)
        self.task_id = task_id

    def progress(self, done, total, message=""):
        self.storage.update_task(self.task_id, ACTIVE_STATUSES, progress_done=done, progress_total=total, message=message)

    def set_status(self, status, message=""):
        """任务进入新阶段（如 validating）。已请求取消时不覆盖 cancelling。"""
        self.storage.update_task(self.task_id, ("running", "validating"), status=status, message=message)

    def register_dataset(self, name, path, dataset_format):
        """任务产出的数据集在发布（验证并重命名）后登记，登记后才作为可用数据集出现在列表中。"""
        dataset = self.storage.add_dataset(name, path, dataset_format)
        return dataset

    def check_cancel(self):
        """在 episode 或帧块边界调用；已请求取消时抛出 TaskCancelled，由任务清理后退出。
        服务被强制结束后工作进程可能成为孤儿，重启后任务已标为 interrupted，此时同样退出。"""
        if self.storage.get_task(self.task_id)["status"] not in ("running", "validating"):
            raise TaskCancelled()


class DemoReporter:
    """demo 在当前进程直接运行任务函数时代替 TaskReporter：进度写日志，不登记数据集，不取消。"""

    task_id = 0

    def progress(self, done, total, message=""):
        logger.info("Progress {}/{} {}", done, total, message)

    def set_status(self, status, message=""):
        logger.info("Status {}: {}", status, message)

    def register_dataset(self, name, path, dataset_format):
        dataset = {"id": None, "name": name}
        return dataset

    def check_cancel(self):
        pass


def exit_with_parent():
    """服务进程被强制结束时（任务管理器来不及终止工作进程），工作进程随之立即退出，不再继续写入或发布。
    临时输出由重启后的任务管理器清理。"""
    multiprocessing.connection.wait([multiprocessing.parent_process().sentinel])
    os._exit(1)


def run_worker(db_path, task_id, job, params, log_config):
    """工作进程入口。任务结果或失败原因写回数据库，最终状态由此处或 TaskManager 确定。"""
    if multiprocessing.parent_process() is not None:
        threading.Thread(target=exit_with_parent, name="exit-with-parent", daemon=True).start()
    # spawn 的子进程不继承服务入口的 loguru 配置；服务中按同样配置写入同一日志文件，测试与 demo 中为 None，用默认终端输出。
    if log_config:
        log_helper.setup_logger(**log_config)
    # Windows 下 Ctrl+C 会同时发给同一控制台的工作进程；忽略它，由 TaskManager 终止进程并记为 interrupted。
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    storage = Storage(db_path)
    reporter = TaskReporter(db_path, task_id)
    # 最终状态只从活动状态写入，不覆盖重启后标记的 interrupted。
    try:
        result = job(params, reporter)
    except TaskCancelled:
        storage.update_task(task_id, ["cancelling"], status="cancelled", message="已取消", finished_at=now())
        return
    except Exception as error:
        storage.update_task(task_id, ACTIVE_STATUSES, status="failed", error=f"{error}\n{traceback.format_exc()}", message=str(error), finished_at=now())
        return
    finally:
        # 工作进程退出时不执行 atexit，先写完 enqueue 队列中的日志。
        logger.complete()
    # 任务返回后若已请求取消，结果仍完整可用，按完成记录。
    storage.update_task(task_id, ACTIVE_STATUSES, status="succeeded", result=result, message="完成", finished_at=now())


class TaskManager:
    """后台任务：串行执行，每个任务一个工作进程；状态、进度和结果保存在 SQLite。"""

    def __init__(self, storage, jobs, **kwargs):
        self.storage = storage
        self.jobs = dict(jobs)
        self.cancel_timeout = float(kwargs.get("cancel_timeout", DEFAULT_CANCEL_TIMEOUT))
        self.poll_interval = float(kwargs.get("poll_interval", DEFAULT_POLL_INTERVAL))
        self.stop_event = threading.Event()
        self.thread = None

    def start(self):
        # 只中断上次运行中的任务；排队中的任务尚未开始，保留排队，重启后按顺序执行。
        unfinished = [task for task in self.storage.list_tasks(limit=100000) if task["status"] in ACTIVE_STATUSES]
        count = self.storage.update_tasks_status(ACTIVE_STATUSES, "interrupted", message="服务重启，任务中断", finished_at=now())
        if count:
            logger.warning("Marked {} unfinished tasks as interrupted", count)
        for task in unfinished:
            self.remove_temporaries(task)
        self.stop_event.clear()
        self.thread = threading.Thread(target=self.dispatch_loop, name="task-dispatcher", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join()
            self.thread = None

    def submit(self, kind, params):
        if kind not in self.jobs:
            raise ValueError(f"未知任务类型: {kind}")
        task = self.storage.add_task(kind, params)
        logger.info("Task {} queued: {}", task["id"], kind)
        return task

    def get(self, task_id):
        task = self.storage.get_task(task_id)
        if task is None:
            raise LookupError(f"任务不存在: {task_id}")
        return task

    def list(self, **kwargs):
        tasks = self.storage.list_tasks(**kwargs)
        return tasks

    def cancel(self, task_id):
        task = self.get(task_id)
        if self.storage.update_task(task_id, ["queued"], status="cancelled", message="已取消（未开始）", finished_at=now()):
            task = self.get(task_id)
            return task
        if not self.storage.update_task(task_id, ["running", "validating"], status="cancelling", message="正在取消"):
            raise ValueError(f"任务状态为 {task['status']}，不能取消")
        logger.info("Task {} cancelling", task_id)
        task = self.get(task_id)
        return task

    def retry(self, task_id):
        """重试创建新记录，保留原任务作为历史。"""
        task = self.get(task_id)
        if task["status"] not in RETRYABLE_STATUSES:
            raise ValueError(f"任务状态为 {task['status']}，不能重试")
        retried = self.storage.add_task(task["kind"], task["params"], retry_of=task_id)
        logger.info("Task {} retried as {}", task_id, retried["id"])
        return retried

    def remove_temporaries(self, task):
        """清理被终止任务留下的临时输出；只处理带 output_path 的任务，已发布的输出不受影响。"""
        output = task["params"].get("output_path")
        if output:
            removed = remove_task_temporaries(output, task["id"])
            if removed:
                logger.warning("Removed temporary output of task {}: {}", task["id"], [str(path) for path in removed])

    def next_queued(self):
        for task in reversed(self.storage.list_tasks(limit=1000)):
            if task["status"] == "queued":
                return task
        return None

    def dispatch_loop(self):
        while not self.stop_event.is_set():
            task = self.next_queued()
            if task is None or not self.storage.update_task(task["id"], ["queued"], status="running", message="启动中", started_at=now()):
                self.stop_event.wait(self.poll_interval)
                continue
            self.run_task(task)

    def run_task(self, task):
        # Windows 只有 spawn；显式指定使 Linux 行为一致，工作进程不继承父进程的句柄与线程。
        process = multiprocessing.get_context("spawn").Process(
            target=run_worker,
            args=(str(self.storage.db_path), task["id"], self.jobs[task["kind"]], task["params"], log_helper.log_config),
            name=f"task-{task['id']}",
        )
        process.start()
        logger.info("Task {} started in process {}", task["id"], process.pid)
        cancel_requested_at = None
        while process.is_alive():
            process.join(self.poll_interval)
            if self.stop_event.is_set():
                process.terminate()
                process.join()
                # 工作进程可能已写回最终状态，只有未完成时才标为中断。
                if self.storage.update_task(task["id"], UNFINISHED_STATUSES, status="interrupted", message="服务停止，任务中断", finished_at=now()):
                    logger.warning("Task {} interrupted by service stop", task["id"])
                self.remove_temporaries(task)
                return
            status = self.storage.get_task(task["id"])["status"]
            if status == "cancelling":
                cancel_requested_at = cancel_requested_at or time.monotonic()
                if time.monotonic() - cancel_requested_at > self.cancel_timeout:
                    process.terminate()
                    process.join()
                    self.storage.update_task(task["id"], ["cancelling"], status="cancelled", message="已取消（超时后终止工作进程）", finished_at=now())
                    logger.warning("Task {} terminated after cancel timeout", task["id"])
                    self.remove_temporaries(task)
                    return
        # 工作进程异常退出（崩溃、被系统杀死）时没有写回最终状态。
        if self.storage.update_task(task["id"], ACTIVE_STATUSES, status="failed", message=f"工作进程异常退出，exitcode={process.exitcode}", finished_at=now()):
            logger.error("Task {} worker exited abnormally: {}", task["id"], process.exitcode)
            self.remove_temporaries(task)
            return
        final = self.storage.get_task(task["id"])
        logger.info("Task {} finished: {}", task["id"], final["status"])


def demo_sleep_job(params, reporter):
    """演示与测试用任务：分步等待，每步检查取消；可指定在某步失败或忽略取消。
    给出 output_path 时先建临时输出目录且不自行清理，用于验证工作进程被终止后由任务管理器清理。"""
    steps = int(params.get("steps", 5))
    # 测试用：写出工作进程 pid，验证服务被强制结束后工作进程随之退出。
    if params.get("pid_file"):
        Path(params["pid_file"]).write_text(str(os.getpid()), encoding="utf-8")
    if params.get("output_path"):
        temporary_path(params["output_path"], reporter.task_id).mkdir(parents=True, exist_ok=True)
    for step in range(steps):
        if not params.get("ignore_cancel"):
            reporter.check_cancel()
        if params.get("fail_at") == step:
            raise RuntimeError(f"演示失败于第 {step} 步")
        time.sleep(float(params.get("step_seconds", 0.2)))
        reporter.progress(step + 1, steps, f"第 {step + 1}/{steps} 步")
    result = {"steps": steps}
    return result


DEMO_JOBS = {"demo_sleep": demo_sleep_job}
DEMO_OUTPUT = Path(__file__).resolve().parents[2] / "output" / "demo" / "task_manager"


def wait_for(manager, task_id, statuses, timeout=30.0):
    deadline = time.monotonic() + timeout
    task = manager.get(task_id)
    while task["status"] not in statuses:
        if time.monotonic() > deadline:
            raise TimeoutError(f"任务 {task_id} 未在 {timeout}s 内进入 {statuses}，当前 {task['status']}")
        time.sleep(0.1)
        task = manager.get(task_id)
    return task


def demo_task_manager():
    db_path = DEMO_OUTPUT / "demo.sqlite3"
    db_path.unlink(missing_ok=True)
    manager = TaskManager(Storage(db_path), DEMO_JOBS, cancel_timeout=2, poll_interval=0.1)
    manager.start()
    try:
        done = manager.submit("demo_sleep", {"steps": 3})
        failing = manager.submit("demo_sleep", {"steps": 3, "fail_at": 1})
        slow = manager.submit("demo_sleep", {"steps": 50, "step_seconds": 0.1})
        done = wait_for(manager, done["id"], ["succeeded"])
        failing = wait_for(manager, failing["id"], ["failed"])
        wait_for(manager, slow["id"], ["running"])
        manager.cancel(slow["id"])
        slow = wait_for(manager, slow["id"], ["cancelled"])
        retried = manager.retry(failing["id"])
        logger.info("Succeeded: {} progress {}/{}", done["result"], done["progress_done"], done["progress_total"])
        logger.info("Failed: {}", failing["message"])
        logger.info("Cancelled at {}/{}: {}", slow["progress_done"], slow["progress_total"], slow["message"])
        logger.info("Retry of {} created task {} ({})", failing["id"], retried["id"], retried["status"])
    finally:
        manager.stop()
    logger.info("Task status after stop: {}", manager.get(retried["id"])["status"])


def main():
    demo_task_manager()


if __name__ == "__main__":
    main()
