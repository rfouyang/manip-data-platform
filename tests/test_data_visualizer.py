import cv2
import numpy as np
import pytest

from component.common.dataset_access import DatasetAccess
from component.dataset.dataset_manager import DatasetManager
from component.visualization.data_visualizer import DataVisualizer
from tests.test_h5_helper import write_episode
from util.storage_helper import Storage


@pytest.fixture
def visualizer(tmp_path):
    episode_dir = tmp_path / "data" / "demo_task" / "success_episodes" / "0001" / "data"
    episode_dir.mkdir(parents=True)
    write_episode(episode_dir / "trajectory.hdf5")
    access = DatasetAccess()
    manager = DatasetManager(Storage(tmp_path / "test.sqlite3"), access, tmp_path / "data")
    manager.register("demo_task")
    visualizer = DataVisualizer(manager, access)
    return visualizer


def test_display_color_keeps_rgb_and_depth_is_raw(visualizer):
    jpeg = visualizer.get_color(1, "success_episodes/0001", "camera_top", 0)
    # 浏览器按标准 JPEG 解码；cv2 标准解码结果为 BGR，红色应在最后一个通道。
    bgr = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    blue, green, red = bgr[4, 4].tolist()
    assert red > 150 and blue < 50 and green < 50

    raw = visualizer.get_depth(1, "success_episodes/0001", "camera_top", 0)
    depth = np.frombuffer(raw, "<u2").reshape(8, 8)
    assert depth.tolist() == [[1234] * 8] * 8


def test_frame_bounds_and_camera_are_checked(visualizer):
    with pytest.raises(ValueError):
        visualizer.get_color(1, "success_episodes/0001", "camera_top", 4)
    with pytest.raises(ValueError):
        visualizer.get_depth(1, "success_episodes/0001", "no_camera", 0)
    frame_index = visualizer.get_episode(1, "success_episodes/0001")
    assert frame_index["length"] == 4
    assert frame_index["cameras"]["camera_top"]["image_starts"] == [0]


def test_series_downsampling_and_frame_values(visualizer):
    episode_id = "success_episodes/0001"
    names = ["puppet/arm_left_position_align"]
    curves = visualizer.get_series(1, episode_id, names)
    assert curves["stride"] == 1 and curves["rows"] == [0, 1, 2, 3]
    assert len(curves["series"][names[0]]) == 7

    sampled = visualizer.get_series(1, episode_id, names, max_points=2)
    assert sampled["stride"] == 2 and sampled["rows"] == [0, 2]
    assert len(sampled["series"][names[0]][0]) == 2

    frame = visualizer.get_frame(1, episode_id, 2)
    # 空的 pose 字段不出现在曲线字段与当前帧数值中，不补零。
    assert "master/end_effector_left_pose_align" not in frame["values"]
    assert frame["values"][names[0]] == [0.0] * 7
    assert frame["index"] == 2 and frame["is_intervene"] is False


def test_series_rejects_empty_and_bad_range(visualizer):
    with pytest.raises(ValueError, match="字段不存在或为空"):
        visualizer.get_series(1, "success_episodes/0001", ["master/end_effector_left_pose_align"])
    with pytest.raises(ValueError, match="行范围无效"):
        visualizer.get_series(1, "success_episodes/0001", ["puppet/arm_left_position_align"], start=3, stop=99)
    with pytest.raises(ValueError, match="max_points"):
        visualizer.get_series(1, "success_episodes/0001", ["puppet/arm_left_position_align"], max_points=0)
    with pytest.raises(ValueError):
        visualizer.get_frame(1, "success_episodes/0001", 4)


def main():
    import sys

    sys.exit(pytest.main([__file__, "-q"]))


if __name__ == "__main__":
    main()
