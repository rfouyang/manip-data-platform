from pathlib import Path

from loguru import logger

from component.common.dataset_access import DatasetAccess
from component.common.task_manager import TaskManager
from component.conversion.data_converter import (
    CONVERT_JOB,
    FRAME_COUNT_JOB,
    H5_TO_V21_JOB,
    DataConverter,
    convert_job,
    frame_count_job,
    h5_to_v21_job,
)
from component.conversion.version_converter import V3_TO_V21_JOB, V21_TO_V3_JOB, v3_to_v21_job, v21_to_v3_job
from component.dataset.dataset_manager import DatasetManager
from component.merge.data_merger import MERGE_JOB, DataMerger, merge_job
from component.split.data_splitter import SPLIT_JOB, DataSplitter, subset_job
from component.subtask.subtask_editor import (
    SUBTASK_TO_TASK_JOB,
    SUBTASK_WRITE_JOB,
    SubtaskEditor,
    subtask_to_task_job,
    subtask_write_job,
)
from component.tag.data_tagger import TAG_JOB, DataTagger, tag_job
from component.visualization.data_visualizer import DataVisualizer
from config.settings import Settings
from util.storage_helper import Storage


class AppContext:
    def __init__(self, settings=None):
        self.settings = settings if settings is not None else Settings()
        self.storage = Storage(self.settings.db_path)
        self.dataset_access = DatasetAccess()
        # 任务类型到工作进程函数的登记；函数须为模块级，才能传给 spawn 工作进程。
        self.task_manager = TaskManager(self.storage, {
            FRAME_COUNT_JOB: frame_count_job,
            CONVERT_JOB: convert_job,
            H5_TO_V21_JOB: h5_to_v21_job,
            V3_TO_V21_JOB: v3_to_v21_job,
            V21_TO_V3_JOB: v21_to_v3_job,
            TAG_JOB: tag_job,
            SPLIT_JOB: subset_job,
            MERGE_JOB: merge_job,
            SUBTASK_WRITE_JOB: subtask_write_job,
            SUBTASK_TO_TASK_JOB: subtask_to_task_job,
        })
        self.dataset_manager = DatasetManager(self.storage, self.dataset_access, self.settings.data_dir)
        self.data_visualizer = DataVisualizer(self.dataset_manager, self.dataset_access)
        self.data_converter = DataConverter(self.dataset_manager, self.dataset_access, self.task_manager, self.settings.output_dir)
        self.data_tagger = DataTagger(self.dataset_manager, self.task_manager, self.settings.output_dir)
        self.data_splitter = DataSplitter(self.dataset_manager, self.task_manager, self.settings.output_dir)
        self.data_merger = DataMerger(self.dataset_manager, self.task_manager, self.settings.output_dir)
        self.subtask_editor = SubtaskEditor(self.dataset_manager, self.storage, self.task_manager, self.settings.output_dir)


def demo_context():
    db_path = Path(__file__).resolve().parents[1] / "output" / "demo" / "context" / "demo.sqlite3"
    settings = Settings(db_path=db_path)
    context = AppContext(settings)
    assert context.settings is settings
    assert context.dataset_manager.storage is context.storage
    assert context.data_converter.task_manager is context.task_manager
    logger.info("AppContext ready: data={}, db={}", settings.data_dir, settings.db_path)


def main():
    demo_context()


if __name__ == "__main__":
    main()
