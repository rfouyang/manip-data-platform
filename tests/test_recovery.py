"""异常恢复：每类后台任务在各取消检查点被取消、或中途意外失败时，不发布输出、不残留临时目录、源数据逐字节不变；
失败后重新执行可以正常完成。"""
import hashlib
from pathlib import Path

import pytest

from component.common.dataset_access import LEROBOT_V21, DatasetAccess
from component.common.task_manager import TaskCancelled
from component.conversion.data_converter import convert_job, h5_to_v21_job
from component.conversion.version_converter import v3_to_v21_job, v21_to_v3_job
from component.dataset.dataset_manager import DatasetManager
from component.merge.data_merger import DataMerger, merge_job, merge_params
from component.split.data_splitter import DataSplitter, subset_job
from component.subtask.subtask_editor import SubtaskEditor, subtask_to_task_job, subtask_write_job
from component.tag.data_tagger import DataTagger, tag_job, tag_params
from tests.test_data_splitter import subset_params
from tests.test_data_tagger import make_tasks_v3
from tests.test_lerobot_conversion import Reporter, make_params
from tests.test_lerobot_version_conversion import make_v3, v21_params
from util.storage_helper import Storage


class FailingReporter(Reporter):
    """在第 fail_at 次检查时抛出意外异常，模拟任务中途失败（磁盘、解码等错误）。"""

    def __init__(self, fail_at):
        super().__init__()
        self.fail_at = fail_at

    def check_cancel(self):
        self.calls += 1
        if self.calls == self.fail_at:
            raise RuntimeError("injected failure")


def snapshot(root):
    """目录下全部文件的相对路径 → 内容摘要，用于确认源数据逐字节不变。"""
    files = {path.relative_to(root).as_posix(): hashlib.sha1(path.read_bytes()).hexdigest() for path in sorted(Path(root).rglob("*")) if path.is_file()}
    return files


def leftovers(output):
    """输出目录旁的临时目录（.tmp- 开头，含 H5 → v2.1 的中间 v3.0 与拆分的视频片段）。"""
    names = sorted(path.name for path in Path(output).parent.glob(".tmp-*"))
    return names


def check_recovery(job, make_job_params, sources):
    """先完整运行一次得到检查点数，再在第 1、2、中间与最后一个检查点（发布前）取消，并在中间注入一次失败。"""
    before = {str(root): snapshot(root) for root in sources}
    params = make_job_params(f"{job.__name__}_probe")
    reporter = Reporter()
    job(params, reporter)
    calls = reporter.calls
    assert calls >= 2, f"{job.__name__} 只有 {calls} 个取消检查点"
    points = sorted({0, 1, calls // 2, calls - 1})
    for point in points:
        params = make_job_params(f"{job.__name__}_cancel{point}")
        with pytest.raises(TaskCancelled):
            job(params, Reporter(cancel_after=point))
        assert not Path(params["output_path"]).exists(), (job.__name__, point)
        assert leftovers(params["output_path"]) == [], (job.__name__, point)
    params = make_job_params(f"{job.__name__}_failed")
    with pytest.raises(RuntimeError, match="injected failure"):
        job(params, FailingReporter(fail_at=max(calls // 2, 1)))
    assert not Path(params["output_path"]).exists() and leftovers(params["output_path"]) == []
    # 失败后用同样的参数重新执行（重试）可以完成。
    retried = job(params, Reporter())
    assert Path(retried["output_path"]).exists() and leftovers(params["output_path"]) == []
    for root in sources:
        assert snapshot(root) == before[str(root)], f"{job.__name__} 改动了源数据 {root}"
    result = {"checkpoints": calls, "cancel_points": points}
    return result


def test_h5_conversions_recover(tmp_path):
    source_params = make_params(tmp_path)
    source = Path(source_params["dataset_path"])

    def h5_params(name):
        params = {**source_params, "output_path": str(tmp_path / "out" / name)}
        return params

    check_recovery(convert_job, h5_params, [source])
    check_recovery(h5_to_v21_job, h5_params, [source])


def test_version_conversions_recover(tmp_path):
    v3 = make_v3(tmp_path)
    check_recovery(v3_to_v21_job, lambda name: v21_params(v3, tmp_path / "out" / name), [v3])
    v21 = tmp_path / "v21"
    v3_to_v21_job(v21_params(v3, v21), Reporter())

    def to_v3_params(name):
        params = {"dataset_id": 2, "dataset_path": str(v21), "output_path": str(tmp_path / "out3" / name), "episode_count": 2, "dropped_features": []}
        return params

    check_recovery(v21_to_v3_job, to_v3_params, [v21])


@pytest.fixture
def manager(tmp_path):
    manager = DatasetManager(Storage(tmp_path / "test.sqlite3"), DatasetAccess(), tmp_path)
    return manager


def test_dataset_edits_recover(manager, tmp_path):
    make_tasks_v3(tmp_path / "a")
    make_tasks_v3(tmp_path / "b", offset=10)
    a, b = manager.register(tmp_path / "a")["id"], manager.register(tmp_path / "b")["id"]
    sources = [tmp_path / "a", tmp_path / "b"]

    tagger = DataTagger(manager, None, tmp_path / "out")
    check_recovery(tag_job, lambda name: tag_params(tagger.preview(a, "regex", pattern=r"(\w+) cup", output_name=name)), sources)

    splitter = DataSplitter(manager, None, tmp_path / "out")
    splitter.dataset_id = a
    check_recovery(subset_job, lambda name: subset_params(splitter, "episodes", episodes="1-2", output_name=name)[1], sources)

    merger = DataMerger(manager, None, tmp_path / "out")
    check_recovery(merge_job, lambda name: merge_params(merger.preview([a, b], output_name=name)), sources)

    storage = manager.storage
    editor = SubtaskEditor(manager, storage, None, tmp_path / "out")
    editor.save(a, 1, [{"start": 0, "text": "reach"}, {"start": 2, "text": "grasp"}], ["pick up the green mug"])

    def write_params(name):
        preview = editor.preview_write(a, output_name=name)
        params = {"dataset_id": a, "dataset_path": preview["dataset"]["path"], "output_path": preview["output_path"],
                  "segments": {str(index): segments for index, segments in storage.list_subtask_drafts(a).items()},
                  "tasks": {str(index): edits for index, edits in storage.list_task_drafts(a).items()}}
        return params

    def to_task_params(name):
        params = {**write_params(name), "template": "{task}: {subtask}", "keep_language": True}
        return params

    check_recovery(subtask_write_job, write_params, sources)
    check_recovery(subtask_to_task_job, to_task_params, sources)


def test_v21_output_format_marker(tmp_path):
    """确认恢复测试中 v3 → v2.1 的输出确为 v2.1（避免测试因参数错误而空转）。"""
    v3 = make_v3(tmp_path)
    reporter = Reporter()
    v3_to_v21_job(v21_params(v3, tmp_path / "v21"), reporter)
    assert reporter.registered[2] == LEROBOT_V21
