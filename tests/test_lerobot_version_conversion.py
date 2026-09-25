import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pyarrow.parquet as pq
import pytest

from component.common.dataset_access import LEROBOT_V21, DatasetAccess
from component.common.task_manager import TaskCancelled
from component.conversion.data_converter import DataConverter, convert_job, h5_to_v21_job
from component.conversion.version_converter import dropped_features, v3_to_v21_job, v21_to_v3_job, version_notes
from util.lerobot_helper import load_metadata
from component.dataset.dataset_manager import DatasetManager
from tests.test_lerobot_conversion import Reporter, make_params
from util.lerobot_helper import LeRobotEpisode
from util.lerobot_v21_helper import META_FILES, avc_parameter_sets, compare_v21, strip_parameter_sets
from util.storage_helper import Storage

REPO_ROOT = Path(__file__).resolve().parents[1]
V21_TOOL = REPO_ROOT / "tools" / "lerobot_v21"


def make_v3(tmp_path):
    """由合成 H5 转出的 v3.0（2 个 episode、7 帧，含 RGB 与深度）。"""
    convert_job(make_params(tmp_path), Reporter())
    root = tmp_path / "output" / "converted"
    return root


def v21_params(source, output, count=None):
    meta = load_metadata(source)
    params = {
        "dataset_path": str(source),
        "output_path": str(output),
        "episode_count": count or meta.total_episodes,
        "dropped_features": dropped_features(meta, LEROBOT_V21),
    }
    return params


def test_v3_to_v21_to_v3_keeps_values_and_frames(tmp_path):
    v3 = make_v3(tmp_path)
    v21 = tmp_path / "v21"
    reporter = Reporter()
    result = v3_to_v21_job(v21_params(v3, v21), reporter)
    assert result["validation"]["passed"], result["validation"]["failed"]
    assert reporter.registered[2] == LEROBOT_V21 and result["frames"] == 7
    # v2.1 不支持深度 feature，明确不写入。
    assert result["dropped_features"] == ["observation.images.camera_top_depth"]
    assert sorted(path.name for path in v21.iterdir()) == ["data", "meta", "videos"]
    assert sorted(path.name for path in (v21 / "meta").iterdir()) == sorted(META_FILES)
    assert (v21 / "data" / "chunk-000" / "episode_000001.parquet").exists()
    assert (v21 / "videos" / "chunk-000" / "observation.images.camera_top" / "episode_000001.mp4").exists()
    info = json.loads((v21 / "meta" / "info.json").read_text(encoding="utf-8"))
    assert info["codebase_version"] == "v2.1" and info["total_videos"] == 2 and info["splits"] == {"train": "0:2"}
    assert "observation.images.camera_top_depth" not in info["features"]

    access = DatasetAccess()
    assert access.detect_format(v21) == LEROBOT_V21
    assert [episode["length"] for episode in access.list_episodes(v21)] == [4, 3]
    with LeRobotEpisode(v21, 1) as episode, LeRobotEpisode(v3, 1) as source:
        assert episode.cameras == ["camera_top"] and episode.depth_key("camera_top") is None
        assert np.array_equal(episode.read_series("observation.state"), source.read_series("observation.state"))
        assert np.array_equal(episode.read_color("camera_top", 2), source.read_color("camera_top", 2))

    back = tmp_path / "back"
    result = v21_to_v3_job({"dataset_path": str(v21), "output_path": str(back)}, Reporter())
    assert result["validation"]["passed"], result["validation"]["failed"]
    assert (result["episode_count"], result["frames"]) == (2, 7)
    with LeRobotEpisode(back, 0) as episode, LeRobotEpisode(v3, 0) as source:
        assert np.array_equal(episode.read_series("action"), source.read_series("action"))
        assert np.array_equal(episode.read_color("camera_top", 3), source.read_color("camera_top", 3))
    assert access.list_episodes(v21) and (v21 / "meta" / "info.json").exists()


def test_partial_v21_output_and_cancel_cleanup(tmp_path):
    v3 = make_v3(tmp_path)
    result = v3_to_v21_job(v21_params(v3, tmp_path / "one", count=1), Reporter())
    assert result["validation"]["passed"] and load_metadata(tmp_path / "one").total_episodes == 1
    with pytest.raises(TaskCancelled):
        v3_to_v21_job(v21_params(v3, tmp_path / "cancelled"), Reporter(cancel_after=1))
    assert not list(tmp_path.glob("*cancelled*"))


def reencode_h264(path, fps=30):
    """把 mp4 重新编码为 H.264（SPS/PPS 只在 avcC 头中），模拟以 H.264 保存视频的 v2.1 数据集。"""
    import av

    with av.open(str(path)) as source:
        frames = [frame.to_ndarray(format="rgb24") for frame in source.decode(video=0)]
    with av.open(str(path), "w") as target:
        stream = target.add_stream("libx264", rate=fps)
        stream.width, stream.height, stream.pix_fmt = frames[0].shape[1], frames[0].shape[0], "yuv420p"
        stream.codec_context.gop_size = 2
        for image in frames:
            for packet in stream.encode(av.VideoFrame.from_ndarray(image, format="rgb24")):
                target.mux(packet)
        for packet in stream.encode():
            target.mux(packet)


def test_h264_v21_roundtrip_is_identical(tmp_path):
    """H.264 的 v2.1 → 官方 v3.0 → 平台 v2.1：meta、数据表与逐帧压缩数据都与原始一致。

    官方 concat 合并时会把 SPS/PPS 复制到每个关键帧（h264_mp4toannexb），拆分时去掉这些副本。"""
    original = tmp_path / "original"
    v3_to_v21_job(v21_params(make_v3(tmp_path), original), Reporter())
    for path in sorted((original / "videos").rglob("*.mp4")):
        reencode_h264(path)
    info_path = original / "meta" / "info.json"
    info = json.loads(info_path.read_text(encoding="utf-8"))
    info["features"]["observation.images.camera_top"]["info"]["video.codec"] = "h264"
    info_path.write_text(json.dumps(info, ensure_ascii=False, indent=4), encoding="utf-8", newline="\n")

    v3 = tmp_path / "v3"
    assert v21_to_v3_job({"dataset_path": str(original), "output_path": str(v3)}, Reporter())["validation"]["passed"]
    back = tmp_path / "back"
    assert v3_to_v21_job(v21_params(v3, back), Reporter())["validation"]["passed"]
    report = compare_v21(original, back)
    assert report["files"] == 8 and all(not report[key] for key in ("missing", "extra", "meta_differ", "data_differ", "schema_metadata_differ", "video_differ"))


def test_v21_drops_depth_language_and_image_features():
    # image feature（图像帧存在 parquet 中）没有写出实现：列为不写入，info 不再声明，说明中按类型分别列出。
    features = {"observation.state": {"dtype": "float32"}, "observation.images.cam": {"dtype": "video"}, "observation.images.cam_depth": {"dtype": "video"},
                "observation.images.wrist": {"dtype": "image"}, "language_persistent": {"dtype": "language"}}
    meta = SimpleNamespace(features=features, depth_keys=["observation.images.cam_depth"])
    dropped = dropped_features(meta, LEROBOT_V21)
    assert dropped == ["observation.images.cam_depth", "observation.images.wrist", "language_persistent"]
    assert dropped_features(meta, "lerobot_v3.0") == []
    notes = "\n".join(version_notes(LEROBOT_V21, dropped, meta))
    assert "深度 feature" in notes and "语言标注" in notes and "image feature 暂不支持写出为 v2.1，不写入：observation.images.wrist" in notes


def test_strip_parameter_sets_only_removes_header_copies():
    sps, pps, other_sps = b"\x67\x64\x00\x1e", b"\x68\xee", b"\x67\x42\x00\x0a"
    extradata = b"\x01\x64\x00\x1e\xff\xe1" + len(sps).to_bytes(2, "big") + sps + b"\x01" + len(pps).to_bytes(2, "big") + pps
    length_size, sets = avc_parameter_sets(extradata)
    assert (length_size, sets) == (4, {sps, pps})
    nal = lambda unit: len(unit).to_bytes(4, "big") + unit
    idr = b"\x65\x88\x84"
    assert strip_parameter_sets(nal(sps) + nal(pps) + nal(idr), length_size, sets) == nal(idr)
    # 与头不同的参数集是原始数据的一部分，保留。
    assert strip_parameter_sets(nal(other_sps) + nal(idr), length_size, sets) == nal(other_sps) + nal(idr)


class TaskManagerStub:
    def submit(self, kind, params):
        task = {"kind": kind, "params": params}
        return task


def test_directions_preview_and_submit(tmp_path):
    v3 = make_v3(tmp_path)
    access = DatasetAccess()
    manager = DatasetManager(Storage(tmp_path / "test.sqlite3"), access, tmp_path)
    converter = DataConverter(manager, access, TaskManagerStub(), tmp_path / "out")
    dataset = manager.register(v3)
    # API 总是传入 target（可为 None）与 output_name。
    preview = converter.preview(dataset["id"], target=None, output_name=None)
    assert (preview["kind"], preview["target"], preview["dropped_features"]) == ("lerobot_version", "lerobot_v2.1", ["observation.images.camera_top_depth"])
    task = converter.submit_conversion(dataset["id"], target="lerobot_v2.1", max_episodes=1)
    assert task["kind"] == "conversion_lerobot_v3_to_v21" and task["params"]["episode_count"] == 1

    v3_to_v21_job(v21_params(v3, tmp_path / "v21"), Reporter())
    v21 = manager.register(tmp_path / "v21")
    assert v21["format"] == LEROBOT_V21
    assert converter.preview(v21["id"])["target"] == "lerobot_v3.0"
    with pytest.raises(ValueError, match="不支持只转部分"):
        converter.submit_conversion(v21["id"], max_episodes=1)
    with pytest.raises(ValueError, match="暂不支持的目标格式"):
        converter.preview(v21["id"], target="h5")


def test_h5_to_v21_via_temporary_v3(tmp_path):
    """H5 → v2.1：经临时 v3.0，结果与“先转 v3.0 再转 v2.1”一致；深度不写入，临时目录不残留。"""
    params = make_params(tmp_path)
    params["output_path"] = str(tmp_path / "output" / "direct_v21")
    reporter = Reporter()
    result = h5_to_v21_job(params, reporter)
    assert result["validation"]["passed"] and result["v3_validation"]["passed"], (result["validation"]["failed"], result["v3_validation"]["failed"])
    assert reporter.registered[2] == LEROBOT_V21 and (result["episode_count"], result["frames"]) == (2, 7)
    assert result["dropped_features"] == ["observation.images.camera_top_depth"]
    assert sorted(path.name for path in (tmp_path / "output").iterdir()) == ["direct_v21"]

    two_steps = make_params(tmp_path / "two_steps")
    convert_job(two_steps, Reporter())
    v3_to_v21_job(v21_params(two_steps["output_path"], tmp_path / "two_steps_v21"), Reporter())
    for name in ("episodes.jsonl", "tasks.jsonl", "episodes_stats.jsonl"):
        assert (tmp_path / "output" / "direct_v21" / "meta" / name).read_bytes() == (tmp_path / "two_steps_v21" / "meta" / name).read_bytes(), name
    with LeRobotEpisode(tmp_path / "output" / "direct_v21", 1) as direct, LeRobotEpisode(tmp_path / "two_steps_v21", 1) as stepwise:
        assert np.array_equal(direct.read_series("observation.state"), stepwise.read_series("observation.state"))
        assert direct.depth_key("camera_top") is None


def test_h5_directions_and_default_target(tmp_path):
    params = make_params(tmp_path)
    access = DatasetAccess()
    manager = DatasetManager(Storage(tmp_path / "test.sqlite3"), access, tmp_path)
    converter = DataConverter(manager, access, TaskManagerStub(), tmp_path / "out")
    dataset = manager.register(params["dataset_path"])
    assert converter.preview(dataset["id"], target=None)["target"] == "lerobot_v3.0"
    preview = converter.preview(dataset["id"], target="lerobot_v2.1")
    assert (preview["via"], preview["dropped_features"]) == ("lerobot_v3.0", ["observation.images.camera_top_depth"])
    assert preview["output_path"].endswith("source_lerobot_v21")
    task = converter.submit_conversion(dataset["id"], target="lerobot_v2.1", max_episodes=1)
    assert task["kind"] == "conversion_h5_to_lerobot_v21" and len(task["params"]["episode_ids"]) == 1


def run_v21_tool(code):
    """在隔离的 lerobot 0.3.3 环境中运行代码，返回最后一行 JSON。"""
    completed = subprocess.run(["uv", "run", "python", "-c", code], cwd=V21_TOOL, capture_output=True, text=True, check=True,
                               env={**__import__("os").environ, "VIRTUAL_ENV": ""})
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    return result


@pytest.mark.skipif(not (V21_TOOL / ".venv").exists(), reason="未创建 tools/lerobot_v21 隔离环境（uv sync）")
def test_official_v21_roundtrip(tmp_path):
    """官方 0.3.3 写出的 v2.1 → 平台 v3.0 → 平台 v2.1：元数据逐字节一致、数据表一致，官方 0.3.3 读取结果一致。"""
    official = tmp_path / "official"
    run_v21_tool(f"import json; from check_v21 import create_sample; print(json.dumps(create_sample(r'{official}')))")
    v3 = tmp_path / "v3"
    assert v21_to_v3_job({"dataset_path": str(official), "output_path": str(v3)}, Reporter())["validation"]["passed"]
    back = tmp_path / "back"
    assert v3_to_v21_job(v21_params(v3, back), Reporter())["validation"]["passed"]
    # 官方以文本模式写 meta，Windows 上是 CRLF；平台固定写 LF（与 Linux 上官方写出、Hub 上的数据集一致）。
    for name in META_FILES:
        expected = (official / "meta" / name).read_bytes().replace(b"\r\n", b"\n")
        assert (back / "meta" / name).read_bytes() == expected, name
    report = compare_v21(official, back)
    assert (report["missing"], report["extra"], report["data_differ"], report["schema_metadata_differ"], report["video_differ"]) == ([], [], [], [], [])
    for path in sorted((official / "data").rglob("*.parquet")):
        expected, actual = pq.read_table(path), pq.read_table(back / path.relative_to(official))
        assert actual.equals(expected) and actual.schema.equals(expected.schema, check_metadata=True)
    summaries = [run_v21_tool(f"import json; from check_v21 import check_dataset; print(json.dumps(check_dataset(r'{root}')))") for root in (official, back)]
    assert summaries[0] == summaries[1]


def main():
    import sys

    sys.exit(pytest.main([__file__, "-q"]))


if __name__ == "__main__":
    main()
