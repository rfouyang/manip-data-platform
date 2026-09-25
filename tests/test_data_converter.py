import pytest

from component.common.dataset_access import DatasetAccess
from component.conversion.data_converter import DataConverter, output_frame_count
from component.dataset.dataset_manager import DatasetManager
from tests.test_dataset_manager import make_dataset
from util.storage_helper import Storage


@pytest.fixture
def converter(tmp_path):
    data_dir = tmp_path / "data"
    make_dataset(data_dir / "demo_task")
    access = DatasetAccess()
    manager = DatasetManager(Storage(tmp_path / "test.sqlite3"), access, data_dir)
    manager.register("demo_task")
    converter = DataConverter(manager, access, None, tmp_path / "output")
    return converter


def test_preview_reports_mapping_and_skips_unreadable(converter):
    preview = converter.preview(1, sample_episodes=1)
    assert preview["layout"]["size"] == 16
    assert [entry["slice"] for entry in preview["layout"]["entries"]] == [[0, 7], [7, 8], [8, 15], [15, 16]]
    assert preview["mapping"]["action"] | {"comment": ""} == {"source": "puppet", "offset_frames": 1, "comment": ""}
    assert preview["mapping"]["state"]["source"] == "puppet"
    # 合成数据只有左臂字段，右臂缺失应作为阻塞问题报告，不静默跳过。
    missing = {issue["name"] for issue in preview["field_issues"][0]["issues"]}
    assert "puppet/arm_right_position_align/data" in missing
    # master 不参与映射，不应因 master 字段产生问题。
    assert not any(name.startswith("master/") for name in missing)
    assert preview["ready"] is False
    # LFS 指针 episode 计入不可读，不参与转换。
    assert [episode["id"] for episode in preview["blocked_episodes"]] == ["success_episodes/0003"]
    assert preview["convertible_count"] == 2


def test_preview_refuses_existing_output(converter, tmp_path):
    output = converter.output_path(converter.dataset_manager.get(1))
    output.mkdir(parents=True)
    preview = converter.preview(1, sample_episodes=1)
    assert any("输出目录已存在" in item for item in preview["blocking"])
    assert preview["ready"] is False


def test_preview_rejects_unsupported_target_and_missing_dataset(converter):
    # H5 可转 v3.0 与 v2.1（经 v3.0），不能转为 H5。
    with pytest.raises(ValueError, match="暂不支持的目标格式"):
        converter.preview(1, target="h5")
    with pytest.raises(LookupError):
        converter.preview(999)


def test_frame_selection_follows_camera_images(converter, tmp_path):
    """合成 episode 的 4 行只有 1 张图像，取帧后应为 1 帧。"""
    mapping = converter.load_mapping()
    assert mapping["frame_selection"]["mode"] == "camera_image_starts"
    samples = converter.sample_frames(converter.dataset_manager.get(1), ["success_episodes/0001"], mapping)
    assert samples[0]["rows"] == 4 and samples[0]["frames"] == 1
    # 唯一一帧没有下一帧作为 action，按 last_frame=drop 不输出，不补值。
    assert samples[0]["output_frames"] == 0


def test_action_next_frame_drops_last_frame(converter):
    mapping = converter.load_mapping()
    assert mapping["last_frame"]["policy"] == "drop"
    assert output_frame_count(333, mapping) == 332
    preview = converter.preview(1, sample_episodes=1)
    assert preview["estimated_frames"] == preview["estimated_images"] - preview["convertible_count"]


def main():
    import sys

    sys.exit(pytest.main([__file__, "-q"]))


if __name__ == "__main__":
    main()
