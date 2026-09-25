import filecmp
import re

import numpy as np
import pandas as pd
import pytest

from component.common.dataset_access import LEROBOT_V21, DatasetAccess
from component.conversion.version_converter import dropped_features, v3_to_v21_job
from component.dataset.dataset_manager import DatasetManager
from component.tag.data_tagger import DataTagger, extract_tag, tag_job, tag_params
from tests.test_lerobot_conversion import Reporter
from util.lerobot_helper import create_dataset, image_feature, load_metadata, open_dataset, save_episode, vector_feature
from util.storage_helper import Storage

TASKS = ["pick up the red cup", "Pick up the green cup", "pick up the red cup", "wave hand"]


def make_tasks_v3(root, offset=0, tasks=TASKS):
    """4 个 episode、3 个任务的小型 v3.0（64×64 视频）。offset 改变数值，用于生成内容不同的同结构数据集。"""
    dataset = create_dataset(root, 30, {"observation.state": vector_feature(["a", "b"]), "observation.images.cam": image_feature((64, 64, 3))})
    for episode, task in enumerate(tasks):
        for frame in range(3 + episode):
            dataset.add_frame({
                "observation.state": np.array([episode + offset, frame], np.float32),
                "observation.images.cam": np.full((64, 64, 3), 20 * frame, np.uint8),
                "task": task,
            })
        save_episode(dataset)
    dataset.finalize()


@pytest.fixture
def tagger(tmp_path):
    make_tasks_v3(tmp_path / "source")
    manager = DatasetManager(Storage(tmp_path / "test.sqlite3"), DatasetAccess(), tmp_path)
    tagger = DataTagger(manager, None, tmp_path / "out")
    tagger.dataset_id = manager.register(tmp_path / "source")["id"]
    return tagger


def test_extract_tag_rules():
    assert extract_tag(re.compile(r"pick up the (\w+) cup"), "pick up the red cup") == "red"
    assert extract_tag(re.compile(r"(?i)green"), "Pick up the GREEN cup") == "GREEN"
    assert extract_tag(re.compile(r"blue"), "pick up the red cup") is None
    assert extract_tag(re.compile(r"cup()"), "red cup") is None


def test_preview_values_counts_and_blocking(tagger):
    preview = tagger.preview(tagger.dataset_id, "regex", pattern=r"(?i)pick up the (\w+) cup")
    rows = {row["task"]: row for row in preview["tasks"]}
    assert (rows["pick up the red cup"]["value"], rows["pick up the red cup"]["episodes"], rows["pick up the red cup"]["frames"]) == ("red", 2, 3 + 5)
    assert rows["Pick up the green cup"]["value"] == "green"
    # 未匹配的任务取空值，可以提交；预检中逐个列出（matched 为 False）。
    assert not rows["wave hand"]["matched"] and rows["wave hand"]["value"] == "" and preview["ready"] and preview["unmatched_count"] == 1
    assert [item["value"] for item in preview["values"]] == ["", "green", "red"]
    with_default = tagger.preview(tagger.dataset_id, "regex", pattern=r"(?i)pick up the (\w+) cup", default="other", column="color")
    assert with_default["ready"] and [item["value"] for item in with_default["values"]] == ["green", "other", "red"]
    assert with_default["output_path"].endswith("source_color")
    assert "列已存在" in tagger.preview(tagger.dataset_id, "regex", pattern="cup", column="observation.state", default="x")["blocking"][0]
    assert "列名不合法" in tagger.preview(tagger.dataset_id, "regex", pattern="cup", column="bad/name", default="x")["blocking"][0]
    assert "捕获组" in tagger.preview(tagger.dataset_id, "regex", pattern=r"(\w+) (\w+)", default="x")["blocking"][0]
    with pytest.raises(ValueError, match="正则表达式无效"):
        tagger.preview(tagger.dataset_id, "regex", pattern="(")


def test_tag_job_adds_column_and_keeps_everything_else(tagger, tmp_path):
    preview = tagger.preview(tagger.dataset_id, "regex", pattern=r"(?i)pick up the (\w+) cup", default="other")
    params = tag_params(preview)
    reporter = Reporter()
    result = tag_job(params, reporter)
    assert result["validation"]["passed"], result["validation"]["failed"]
    assert reporter.registered[2] == "lerobot_v3.0"
    assert result["values"] == {"red": 8, "green": 4, "other": 6}
    output = tmp_path / "out" / "source_tag"
    reader = open_dataset(output)
    assert [reader[index]["tag"] for index in (0, 3, 7, 12)] == ["red", "green", "red", "other"]
    for path in (tmp_path / "source" / "videos").rglob("*.mp4"):
        assert filecmp.cmp(path, output / path.relative_to(tmp_path / "source"), shallow=False)
    assert "tag" not in pd.read_parquet(next((tmp_path / "source" / "data").rglob("*.parquet"))).columns

    # 带字符串列的数据集可转为 v2.1（字符串列没有统计）。
    meta = load_metadata(output)
    v21 = v3_to_v21_job({"dataset_path": str(output), "output_path": str(tmp_path / "v21"), "episode_count": meta.total_episodes,
                         "dropped_features": dropped_features(meta, LEROBOT_V21)}, Reporter())
    assert v21["validation"]["passed"], v21["validation"]["failed"]


def test_tag_job_unmatched_tasks_get_empty_value(tagger, tmp_path):
    preview = tagger.preview(tagger.dataset_id, "regex", pattern=r"(?i)pick up the (red) cup")
    params = tag_params(preview)
    result = tag_job(params, Reporter())
    assert result["validation"]["passed"], result["validation"]["failed"]
    assert result["values"] == {"red": 8, "": 10}
    reader = open_dataset(tmp_path / "out" / "source_tag")
    assert [reader[index]["tag"] for index in (0, 3, 12)] == ["red", "", ""]


def test_constant_value_fills_whole_column(tagger, tmp_path):
    """整列统一值（如数据来源），不看任务描述；不能为空。"""
    preview = tagger.preview(tagger.dataset_id, "constant", value="cup_lab", column="source")
    assert preview["ready"] and preview["values"] == [{"value": "cup_lab", "episodes": 4, "frames": 18}]
    assert all(row["value"] == "cup_lab" for row in preview["tasks"]) and preview["output_path"].endswith("source_source")
    with pytest.raises(ValueError, match="请填写统一值"):
        tagger.preview(tagger.dataset_id, "constant", value="")
    with pytest.raises(ValueError, match="未知的取值方式"):
        tagger.preview(tagger.dataset_id, "other")
    result = tag_job(tag_params(preview), Reporter())
    assert result["validation"]["passed"], result["validation"]["failed"]
    assert result["values"] == {"cup_lab": 18} and result["report"]["mode"] == "constant"
    reader = open_dataset(tmp_path / "out" / "source_source")
    assert {reader[index]["source"] for index in range(len(reader))} == {"cup_lab"}


def main():
    import sys

    sys.exit(pytest.main([__file__, "-q"]))


if __name__ == "__main__":
    main()
