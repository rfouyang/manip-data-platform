import filecmp

import numpy as np
import pytest

from component.common.dataset_access import DatasetAccess
from component.dataset.dataset_manager import DatasetManager
from component.split.data_splitter import DataSplitter, parse_episode_spec, subset_job
from component.tag.data_tagger import DataTagger, tag_job, tag_params
from tests.test_data_tagger import TASKS, make_tasks_v3
from tests.test_lerobot_conversion import Reporter
from util.lerobot_helper import LeRobotEpisode, load_metadata
from util.storage_helper import Storage


@pytest.fixture
def splitter(tmp_path):
    make_tasks_v3(tmp_path / "source")
    manager = DatasetManager(Storage(tmp_path / "test.sqlite3"), DatasetAccess(), tmp_path)
    splitter = DataSplitter(manager, None, tmp_path / "out")
    splitter.dataset_id = manager.register(tmp_path / "source")["id"]
    return splitter


def test_parse_episode_spec():
    assert parse_episode_spec("0-2, 5，7 5") == [0, 1, 2, 5, 7]
    with pytest.raises(ValueError, match="无法解析"):
        parse_episode_spec("1-a")
    with pytest.raises(ValueError, match="起点大于终点"):
        parse_episode_spec("5-3")


def subset_params(splitter, mode, **kwargs):
    preview = splitter.preview(splitter.dataset_id, mode, **kwargs)
    selected = set(preview["episodes"])
    params = {
        "dataset_path": preview["dataset"]["path"],
        "output_path": preview["output_path"],
        "selection": preview["selection"],
        "episodes": preview["episodes"],
        "unselected": [index for index in range(preview["source_episode_count"]) if index not in selected],
    }
    return preview, params


def test_subset_selection_modes(splitter):
    assert splitter.preview(splitter.dataset_id, "episodes", episodes="1, 3")["episodes"] == [1, 3]
    by_task = splitter.preview(splitter.dataset_id, "tasks", tasks=[0])
    assert by_task["episodes"] == [0, 2] and by_task["summary"] == {"episode_count": 2, "frames": 8, "seconds": 0.3, "unselected_count": 2}
    with pytest.raises(ValueError, match="超出范围"):
        splitter.preview(splitter.dataset_id, "episodes", episodes="4")
    with pytest.raises(ValueError, match="没有可筛选的字符串列"):
        splitter.preview(splitter.dataset_id, "values", column="tag", values=["red"])
    assert not splitter.preview(splitter.dataset_id, "episodes", episodes="")["ready"]


def test_output_name_cannot_escape_output_dir(splitter, tmp_path):
    # 转换、拆分、合并、tag、子任务共用 resolve_output；输出名只能是输出目录下的一级目录名。
    for name in ("../source/extra", str(tmp_path / "elsewhere"), "a/b", ".tmp-x"):
        with pytest.raises(ValueError, match="一级目录名"):
            splitter.preview(splitter.dataset_id, "episodes", episodes="1", output_name=name)
    assert not (tmp_path / "source" / "extra").exists() and not (tmp_path / "elsewhere").exists()


def test_subset_job_keeps_frames_and_remaps(splitter, tmp_path):
    # 源视频只有一个文件，选中部分 episode → 按 episode 流拷贝再合并（不重新编码）。
    preview, params = subset_params(splitter, "episodes", episodes="1, 3", output_name="part")
    result = subset_job(params, Reporter())
    assert result["validation"]["passed"], result["validation"]["failed"]
    assert (result["episode_count"], result["frames"]) == (2, 4 + 6)
    assert result["report"]["episode_mapping"] == {"0": 1, "1": 3} and result["report"]["unselected_episodes"] == [0, 2]
    output = tmp_path / "out" / "part"
    assert sorted(path.name for path in output.iterdir()) == ["data", "meta", "videos"]
    source_meta, output_meta = load_metadata(tmp_path / "source"), load_metadata(output)
    assert [list(output_meta.episodes[index]["tasks"]) for index in range(2)] == [[TASKS[1]], [TASKS[3]]]
    key = source_meta.video_keys[0]
    for new, old in ((0, 1), (1, 3)):
        assert video_packets_of(output, output_meta, new, key) == video_packets_of(tmp_path / "source", source_meta, old, key)

    # 全部选中时整文件复制。
    _, params = subset_params(splitter, "episodes", episodes="0-3", output_name="all")
    assert subset_job(params, Reporter())["validation"]["passed"]
    video = next((tmp_path / "source" / "videos").rglob("*.mp4"))
    assert filecmp.cmp(video, tmp_path / "out" / "all" / video.relative_to(tmp_path / "source"), shallow=False)


def test_subset_by_tag_with_depth(tmp_path):
    """含深度的 v3（H5 转换产物）先生成 tag，再按 tag 值抽取：RGB 与深度逐帧一致。"""
    from tests.test_lerobot_version_conversion import make_v3

    root = make_v3(tmp_path)
    manager = DatasetManager(Storage(tmp_path / "test.sqlite3"), DatasetAccess(), tmp_path)
    tagger = DataTagger(manager, None, tmp_path / "out")
    splitter = DataSplitter(manager, None, tmp_path / "out")
    dataset = manager.register(root)
    preview = tagger.preview(dataset["id"], "regex", pattern=r"demo (\w+)")
    tag_job(tag_params(preview), Reporter())
    splitter.dataset_id = manager.register(tmp_path / "out" / "converted_tag")["id"]
    assert splitter.options(splitter.dataset_id)["columns"]["tag"] == [{"value": "task", "episodes": 2, "frames": 7}]
    preview, params = subset_params(splitter, "values", column="tag", values=["task"])
    params["episodes"] = [1]
    result = subset_job(params, Reporter())
    assert result["validation"]["passed"], result["validation"]["failed"]
    with LeRobotEpisode(tmp_path / "out" / "converted_tag_subset", 0) as episode, LeRobotEpisode(root, 1) as source:
        assert episode.length == source.length == 3
        for frame in range(3):
            assert np.array_equal(episode.read_depth("camera_top", frame), source.read_depth("camera_top", frame))
            assert np.array_equal(episode.read_color("camera_top", frame), source.read_color("camera_top", frame))


def test_split_by_empty_tag_value(splitter, tmp_path):
    """tag 为空的 episode（任务描述未匹配）也可以按空值选出。"""
    tagger = DataTagger(splitter.dataset_manager, None, tmp_path / "out")
    preview = tagger.preview(splitter.dataset_id, "regex", pattern=r"(?i)pick up the (red) cup")
    tag_job(tag_params(preview), Reporter())
    tagged = splitter.dataset_manager.register(tmp_path / "out" / "source_tag")["id"]
    assert [item["value"] for item in splitter.options(tagged)["columns"]["tag"]] == ["", "red"]
    assert splitter.preview(tagged, "values", column="tag", values=[""])["episodes"] == [1, 3]


def video_packets_of(root, meta, index, key):
    import av

    episode = meta.episodes[index]
    with av.open(str(root / meta.get_video_file_path(index, key))) as container:
        stream = container.streams.video[0]
        packets = sorted((float(packet.pts * stream.time_base), bytes(packet)) for packet in container.demux(stream) if packet.dts is not None)
    start, stop, half = episode[f"videos/{key}/from_timestamp"], episode[f"videos/{key}/to_timestamp"], 0.5 / meta.fps
    data = [payload for seconds, payload in packets if start - half <= seconds < stop - half]
    return data


def main():
    import sys

    sys.exit(pytest.main([__file__, "-q"]))


if __name__ == "__main__":
    main()
