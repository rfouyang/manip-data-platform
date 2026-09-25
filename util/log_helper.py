import inspect
import logging
import sys
from pathlib import Path

from loguru import logger


class InterceptHandler(logging.Handler):
    """把 uvicorn 等第三方库的标准库 logging 记录转交给 loguru（loguru 官方文档写法）。"""

    def emit(self, record):
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # 跳过 logging 模块自身的栈帧，让 loguru 显示真正发出日志的位置。
        frame = inspect.currentframe()
        depth = 0
        while frame and (depth == 0 or frame.f_code.co_filename == logging.__file__):
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


# 服务入口的日志配置。spawn 的工作进程不继承 loguru 配置，任务管理器把它传给工作进程再调用一次 setup_logger。
log_config = None


def setup_logger(log_dir, level="INFO"):
    """服务入口调用一次：终端输出 + 按天轮转的日志文件，并接管标准库 logging。

    工作进程以同样参数调用本函数，追加写同一日志文件（文件名含日期，轮转时不改名，多进程追加安全）。
    单文件 demo 不调用本函数，直接使用 loguru 默认终端输出。
    """
    global log_config
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "manip_{time:YYYY-MM-DD}.log"

    logger.remove()
    logger.add(sys.stderr, level=level)
    logger.add(log_file, level=level, rotation="00:00", retention="30 days", encoding="utf-8", enqueue=True)
    log_config = {"log_dir": str(log_dir), "level": level}

    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        std_logger = logging.getLogger(name)
        std_logger.handlers = []
        std_logger.propagate = True
    return log_file


REPO_ROOT = Path(__file__).resolve().parents[1]
DEMO_OUTPUT = REPO_ROOT / "output" / "demo" / "log_helper"


def demo_setup_logger():
    log_file = setup_logger(DEMO_OUTPUT)
    logger.info("loguru message, value={}", 42)
    logging.getLogger("uvicorn.error").warning("stdlib message routed to loguru")
    logger.complete()
    written = sorted(DEMO_OUTPUT.glob("manip_*.log"))
    content = written[-1].read_text(encoding="utf-8")
    assert "loguru message, value=42" in content
    assert "stdlib message routed to loguru" in content
    logger.info("Log file pattern {} verified: {}", log_file.name, written[-1])


def main():
    demo_setup_logger()


if __name__ == "__main__":
    main()
