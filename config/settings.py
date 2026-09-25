import os
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger


class Settings:
    def __init__(self, **kwargs):
        self.root = Path(__file__).resolve().parents[1]
        load_dotenv(self.root / ".env", encoding="utf-8")
        self.host = kwargs.get("host", os.getenv("MANIP_HOST", "127.0.0.1"))
        self.port = int(kwargs.get("port", os.getenv("MANIP_PORT", "8000")))
        self.log_level = kwargs.get("log_level", os.getenv("MANIP_LOG_LEVEL", "INFO"))
        self.data_dir = self._path("data_dir", "data", kwargs)
        self.output_dir = self._path("output_dir", "output", kwargs)
        self.log_dir = self._path("log_dir", "log", kwargs)
        # 数据集登记与后续任务状态；只存登记信息，episode 内容始终从源文件读取。
        self.db_path = self._path("db_path", "output/manip.sqlite3", kwargs)

    def _path(self, name, default, options):
        value = options.get(name, os.getenv(f"MANIP_{name.upper()}", default))
        path = (self.root / Path(value)).resolve()
        return path


def demo_settings():
    settings = Settings()
    logger.info("Service: http://{}:{}", settings.host, settings.port)
    logger.info("Data: {}; output: {}", settings.data_dir, settings.output_dir)
    logger.info("Database: {}", settings.db_path)


def main():
    demo_settings()


if __name__ == "__main__":
    main()
