"""后台任务输出的临时目录与发布：先写 .tmp- 临时目录，验证后重命名发布；失败或取消时删除临时目录。转换与数据集编辑共用。"""
import shutil
from contextlib import contextmanager
from pathlib import Path

from loguru import logger


def resolve_output(output_dir, name):
    """用户填写的输出名只能是 output_dir 下的一级目录名，防止写到输出目录以外（包括原始数据目录）。"""
    output_dir = Path(output_dir).resolve()
    path = (output_dir / name).resolve()
    if Path(name).name != name or name.startswith(".") or path.parent != output_dir:
        raise ValueError(f"输出名只能是一级目录名，不能含路径分隔符或以 . 开头: {name}")
    return path


def output_fields(output_dir, output, blocking):
    """预检结果末尾的公共字段；输出目录已存在时追加阻塞原因（不覆盖已有结果）。剩余空间按输出目录所在磁盘计算。"""
    if output.exists():
        blocking.append(f"输出目录已存在，不覆盖已有结果: {output}")
    disk = Path(output_dir)
    while not disk.exists():
        disk = disk.parent
    fields = {"output_path": str(output), "output_exists": output.exists(), "free_bytes": shutil.disk_usage(disk).free,
              "blocking": blocking, "ready": not blocking}
    return fields


def require_ready(preview):
    """提交任务前重新预检，有阻塞原因时拒绝。"""
    if not preview["ready"]:
        raise ValueError("；".join(preview["blocking"]))


def temporary_path(output, task_id):
    """输出目录同级的临时目录；.tmp- 开头的目录不会被当作可用数据集。"""
    output = Path(output)
    temp = output.parent / f".tmp-{output.name}-task{task_id}"
    return temp


def remove_temporary(temp):
    """删除未发布的临时输出。删除失败只记录，不掩盖原始异常。"""
    try:
        shutil.rmtree(temp)
    except FileNotFoundError:
        pass
    except OSError as error:
        logger.error("Failed to remove temporary output {}: {}", temp, error)


def remove_task_temporaries(output, task_id):
    """按命名规则删除某任务在输出目录旁的全部临时目录（含 H5 → v2.1 的中间 v3.0、拆分的视频片段）。
    工作进程被终止（取消超时、服务停止、崩溃、重启时中断）时来不及自行清理，由任务管理器调用。返回删除的目录。"""
    paths = sorted(Path(output).parent.glob(f".tmp-*-task{task_id}"))
    for path in paths:
        remove_temporary(path)
    return paths


@contextmanager
def staged_output(output_path, reporter):
    """任务输出的临时目录：确认不覆盖已有结果后交给任务写入，块内出错或取消时删除。任务在块内验证并 publish。"""
    output = Path(output_path)
    if output.exists():
        raise FileExistsError(f"输出目录已存在，不覆盖已有结果: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = temporary_path(output, reporter.task_id)
    try:
        yield temp, output
    except BaseException:
        remove_temporary(temp)
        raise


def publish(temp, output, reporter):
    """最后一次检查取消，确认不覆盖已有结果后重命名发布。"""
    reporter.check_cancel()
    if Path(output).exists():
        raise FileExistsError(f"输出目录已存在，不覆盖已有结果: {output}")
    Path(temp).rename(output)


REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_OUTPUT = REPO_ROOT / "output" / "demo" / "output_publish"


def demo_output_publish():
    from component.common.task_manager import DemoReporter

    shutil.rmtree(DEMO_OUTPUT, ignore_errors=True)
    output = DEMO_OUTPUT / "result"
    temp = temporary_path(output, DemoReporter.task_id)
    temp.mkdir(parents=True)
    (temp / "data.txt").write_text("ok", encoding="utf-8")
    publish(temp, output, DemoReporter())
    assert (output / "data.txt").exists() and not temp.exists()
    try:
        publish(temp, output, DemoReporter())
        raise AssertionError("existing output should not be overwritten")
    except FileExistsError as error:
        logger.info("Rejected: {}", error)
    remove_temporary(temp)
    logger.info("Published {} and refused to overwrite it", output)


def demo_resolve_output():
    assert resolve_output(DEMO_OUTPUT, "sample_v3") == (DEMO_OUTPUT / "sample_v3").resolve()
    for name in ("../data/x", "a/b", "a\\b", "C:/Windows/Temp/x", "..", ".tmp-x", ""):
        try:
            resolve_output(DEMO_OUTPUT, name)
            raise AssertionError(f"output name should be rejected: {name!r}")
        except ValueError as error:
            logger.info("Rejected: {}", error)


def main():
    demo_output_publish()
    demo_resolve_output()


if __name__ == "__main__":
    main()
