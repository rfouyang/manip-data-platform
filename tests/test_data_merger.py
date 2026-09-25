import numpy as np
import pytest

from component.common.dataset_access import DatasetAccess
from component.dataset.dataset_manager import DatasetManager
from component.merge.data_merger import DataMerger, merge_job, merge_params
from component.split.data_splitter import DataSplitter, subset_job
from component.tag.data_tagger import DataTagger, tag_job, tag_params
from tests.test_data_tagger import TASKS, make_tasks_v3
from tests.test_lerobot_conversion import Reporter
from util.lerobot_helper import LeRobotEpisode, load_metadata, task_names
from util.storage_helper import Storage


@pytest.fixture
def manager(tmp_path):
    manager = DatasetManager(Storage(tmp_path / "test.sqlite3"), DatasetAccess(), tmp_path)
    return manager


def test_merge_two_datasets_keeps_data_and_renumbers(manager, tmp_path):
    make_tasks_v3(tmp_path / "a")
    make_tasks_v3(tmp_path / "b", offset=10, tasks=["pick up the blue cup", "wave hand", "wave hand", "pick up the red cup"])
    ids = [manager.register(tmp_path / "a")["id"], manager.register(tmp_path / "b")["id"]]
    merger = DataMerger(manager, None, tmp_path / "out")
    preview = merger.preview(ids, output_name="merged")
    assert preview["ready"] and not preview["duplicates"], preview["blocking"]
    assert (preview["total_episodes"], preview["total_frames"], preview["total_tasks"]) == (8, 36, 4)
    result = merge_job(merge_params(preview), Reporter())
    assert result["validation"]["passed"], result["validation"]["failed"]
    assert [source["output_episodes"] for source in result["report"]["sources"]] == [[0, 3], [4, 7]]
    output = tmp_path / "out" / "merged"
    assert sorted(path.name for path in output.iterdir()) == ["data", "meta", "videos"]
    meta = load_metadata(output)
    assert sorted(task_names(meta).values()) == sorted({*TASKS, "pick up the blue cup"})
    # 输出 episode 4 为 b 的 episode 0：数值、任务与画面均来自 b。
    with LeRobotEpisode(output, 4, meta) as episode, LeRobotEpisode(tmp_path / "b", 0) as source:
        assert np.array_equal(episode.read_series("observation.state"), source.read_series("observation.state"))
        assert episode.metadata()["language_instruction"] == "pick up the blue cup"
        assert np.array_equal(episode.read_color("cam", 2), source.read_color("cam", 2))


def test_merge_rejects_different_fields_and_warns_duplicates(manager, tmp_path):
    make_tasks_v3(tmp_path / "a")
    make_tasks_v3(tmp_path / "copy")
    a, copy = manager.register(tmp_path / "a")["id"], manager.register(tmp_path / "copy")["id"]
    merger = DataMerger(manager, None, tmp_path / "out")
    # 内容相同的数据集：可以合并，但逐个 episode 给出重复警告。
    duplicate = merger.preview([a, copy])
    assert duplicate["ready"] and [item["episode"] for item in duplicate["duplicates"]] == [["copy", index] for index in range(4)]
    with pytest.raises(ValueError, match="至少选择 2 个"):
        merger.preview([a])
    with pytest.raises(ValueError, match="不能重复选择"):
        merger.preview([a, a])

    tagger = DataTagger(manager, None, tmp_path / "out")
    tag_job(tag_params(tagger.preview(a, "constant", value="lab_a", column="source")), Reporter())
    tagged = manager.register(tmp_path / "out" / "a_source")["id"]
    mismatch = merger.preview([tagged, copy])
    assert not mismatch["ready"] and mismatch["sources"][1]["differences"] == ["缺少字段 source"]
    assert "copy 与 a_source 不一致（1 项）" in mismatch["blocking"][0]


def test_merge_split_parts_with_depth(manager, tmp_path):
    """含深度的 v3 拆成两部分再合并：RGB 与深度逐帧一致，编号接续。"""
    from tests.test_lerobot_version_conversion import make_v3

    root = make_v3(tmp_path)
    source = manager.register(root)["id"]
    splitter = DataSplitter(manager, None, tmp_path / "parts")
    parts = []
    for name, spec in (("first", "1"), ("second", "0")):
        preview = splitter.preview(source, "episodes", episodes=spec, output_name=name)
        subset_job({"dataset_path": preview["dataset"]["path"], "output_path": preview["output_path"], "selection": preview["selection"],
                    "episodes": preview["episodes"], "unselected": []}, Reporter())
        parts.append(manager.register(preview["output_path"])["id"])
    preview = DataMerger(manager, None, tmp_path / "out").preview(parts, output_name="joined")
    result = merge_job(merge_params(preview), Reporter())
    assert result["validation"]["passed"], result["validation"]["failed"]
    # 平台以封闭 GOP 编码深度，拼接后官方随机读取每个 episode 首帧都成功。
    assert result["official_read_failures"] == []
    with LeRobotEpisode(tmp_path / "out" / "joined", 0) as episode, LeRobotEpisode(root, 1) as reference:
        assert episode.length == reference.length == 3
        for frame in range(3):
            assert np.array_equal(episode.read_depth("camera_top", frame), reference.read_depth("camera_top", frame))


def main():
    import sys

    sys.exit(pytest.main([__file__, "-q"]))


if __name__ == "__main__":
    main()
