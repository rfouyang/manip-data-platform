import ctypes
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from component.common.output_publish import temporary_path
from component.common.task_manager import DEMO_JOBS, TaskManager, wait_for
from util.storage_helper import Storage


@pytest.fixture
def manager(tmp_path):
    manager = TaskManager(Storage(tmp_path / "test.sqlite3"), DEMO_JOBS, cancel_timeout=1, poll_interval=0.05)
    manager.start()
    yield manager
    manager.stop()


def test_success_failure_and_serial_order(manager):
    done = manager.submit("demo_sleep", {"steps": 2, "step_seconds": 0.05})
    failing = manager.submit("demo_sleep", {"steps": 3, "fail_at": 1, "step_seconds": 0.05})
    done = wait_for(manager, done["id"], ["succeeded"])
    failing = wait_for(manager, failing["id"], ["failed"])
    assert done["result"] == {"steps": 2}
    assert (done["progress_done"], done["progress_total"]) == (2, 2)
    # 串行执行：后提交的任务在前一个结束后才开始。
    assert failing["started_at"] >= done["finished_at"]
    assert "演示失败于第 1 步" in failing["error"] and "Traceback" in failing["error"]
    assert failing["result"] is None


def test_cooperative_cancel_and_queued_cancel(manager):
    running = manager.submit("demo_sleep", {"steps": 100, "step_seconds": 0.05})
    queued = manager.submit("demo_sleep", {"steps": 1})
    wait_for(manager, running["id"], ["running"])
    assert manager.cancel(queued["id"])["status"] == "cancelled"
    assert manager.cancel(running["id"])["status"] in ("cancelling", "cancelled")
    running = wait_for(manager, running["id"], ["cancelled"])
    assert running["message"] == "已取消"
    assert running["progress_done"] < 100
    # 排队中被取消的任务不会再被执行。
    assert manager.get(queued["id"])["started_at"] is None


def test_cancel_timeout_terminates_worker(manager, tmp_path):
    output = tmp_path / "out" / "result"
    stuck = manager.submit("demo_sleep", {"steps": 100, "step_seconds": 0.1, "ignore_cancel": True, "output_path": str(output)})
    wait_for(manager, stuck["id"], ["running"])
    wait_for_path(temporary_path(output, stuck["id"]))
    manager.cancel(stuck["id"])
    stuck = wait_for(manager, stuck["id"], ["cancelled"])
    assert "终止工作进程" in stuck["message"]
    # 被终止的工作进程来不及清理，临时输出由任务管理器删除。
    assert list((tmp_path / "out").iterdir()) == []


def wait_for_path(path, timeout=30.0):
    deadline = time.monotonic() + timeout
    while not path.exists():
        assert time.monotonic() < deadline, f"未出现 {path}"
        time.sleep(0.05)


def test_retry_rules(manager):
    failing = manager.submit("demo_sleep", {"steps": 1, "fail_at": 0})
    failing = wait_for(manager, failing["id"], ["failed"])
    retried = manager.retry(failing["id"])
    assert (retried["retry_of"], retried["params"], retried["status"]) == (failing["id"], failing["params"], "queued")
    done = wait_for(manager, manager.submit("demo_sleep", {"steps": 1})["id"], ["succeeded"])
    with pytest.raises(ValueError, match="不能重试"):
        manager.retry(done["id"])
    with pytest.raises(ValueError, match="不能取消"):
        manager.cancel(done["id"])
    with pytest.raises(ValueError, match="未知任务类型"):
        manager.submit("no_such_job", {})
    with pytest.raises(LookupError):
        manager.get(999)


def test_worker_does_not_overwrite_interrupted(manager):
    """模拟服务被强制结束后重启：任务已标为 interrupted，残留工作进程应退出且不覆盖状态。"""
    task = manager.submit("demo_sleep", {"steps": 100, "step_seconds": 0.05})
    wait_for(manager, task["id"], ["running"])
    manager.storage.update_task(task["id"], status="interrupted", message="服务重启，任务中断")
    manager.submit("demo_sleep", {"steps": 1})
    wait_for(manager, task["id"] + 1, ["succeeded"])
    task = manager.get(task["id"])
    assert (task["status"], task["message"]) == ("interrupted", "服务重启，任务中断")
    assert task["progress_done"] < 100


def test_restart_interrupts_running_and_keeps_queued(tmp_path):
    storage = Storage(tmp_path / "test.sqlite3")
    # 服务被强制结束时 running 任务的临时输出残留在磁盘上；同目录下其他任务的临时目录与已发布输出不受影响。
    output = tmp_path / "out" / "result"
    running = storage.add_task("demo_sleep", {"steps": 1, "output_path": str(output)})
    storage.update_task(running["id"], status="running")
    queued = storage.add_task("demo_sleep", {"steps": 1, "step_seconds": 0.05})
    orphan = temporary_path(output, running["id"])
    other = temporary_path(output, running["id"] + 10)
    for path in (orphan / "data", other, output):
        path.mkdir(parents=True)
    manager = TaskManager(storage, DEMO_JOBS, poll_interval=0.05)
    manager.start()
    try:
        assert manager.get(running["id"])["status"] == "interrupted"
        assert not orphan.exists() and other.exists() and output.exists()
        # 排队中的任务尚未开始，重启后继续执行。
        assert wait_for(manager, queued["id"], ["succeeded"])["result"] == {"steps": 1}
    finally:
        manager.stop()


def process_alive(pid):
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True
    # Windows 上 os.kill 会直接终止进程，改为查询退出码（259 表示仍在运行）。
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    code = ctypes.c_ulong()
    kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
    kernel32.CloseHandle(handle)
    alive = code.value == 259
    return alive


LAUNCHER = """
import sys
import time

sys.path.insert(0, {root!r})
from component.common.task_manager import DEMO_JOBS, TaskManager
from util.storage_helper import Storage

if __name__ == "__main__":
    manager = TaskManager(Storage({db!r}), DEMO_JOBS, poll_interval=0.05)
    manager.start()
    manager.submit("demo_sleep", {{"steps": 600, "step_seconds": 0.1, "pid_file": {pid_file!r}, "output_path": {output!r}}})
    time.sleep(120)
"""


def test_worker_exits_when_service_is_killed(tmp_path):
    """服务进程被强制结束（任务管理器来不及终止工作进程）：工作进程随之退出，重启后任务标为中断并清理临时输出。"""
    db, pid_file, output = tmp_path / "test.sqlite3", tmp_path / "worker.pid", tmp_path / "out" / "result"
    script = tmp_path / "launcher.py"
    script.write_text(LAUNCHER.format(root=str(Path(__file__).resolve().parents[1]), db=str(db), pid_file=str(pid_file), output=str(output)), encoding="utf-8")
    service = subprocess.Popen([sys.executable, str(script)])
    try:
        deadline = time.monotonic() + 30
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        worker = int(pid_file.read_text(encoding="utf-8"))
        assert process_alive(worker)
    finally:
        service.kill()
    service.wait()
    deadline = time.monotonic() + 10
    while process_alive(worker) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not process_alive(worker)
    storage = Storage(db)
    task = storage.list_tasks()[0]
    assert task["status"] == "running" and temporary_path(output, task["id"]).exists()
    manager = TaskManager(storage, DEMO_JOBS, poll_interval=0.05)
    manager.start()
    manager.stop()
    assert manager.get(task["id"])["status"] == "interrupted"
    assert not temporary_path(output, task["id"]).exists()


def main():
    import sys

    sys.exit(pytest.main([__file__, "-q"]))


if __name__ == "__main__":
    main()
