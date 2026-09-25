import cv2
import h5py
import numpy as np

from util.h5_helper import H5Episode


def write_episode(path, length=4, arm_rows=None):
    """写一个与采集样本结构相同的小 H5；颜色按采集端方式把 RGB 数组直接交给 OpenCV 编码。"""
    red_rgb = np.zeros((8, 8, 3), np.uint8)
    red_rgb[..., 0] = 200
    depth = np.full((8, 8), 1234, np.uint16)
    vlen = h5py.vlen_dtype(np.uint8)
    with h5py.File(path, "w") as f:
        f.create_group("metadata").attrs.update({"trajectory_length": length, "language_instruction": "demo"})
        f["camera_color_channel/camera_top"] = b"rgb"
        f["camera_model/camera_top"] = b"demo"
        f["camera_color_resolution/camera_top"] = np.array([8, 8])
        f["camera_depth_resolution/camera_top"] = np.array([8, 8])
        color = f.create_dataset("camera_observations/color_images/camera_top", (length,), dtype=vlen)
        depth_ds = f.create_dataset("camera_observations/depth_images/camera_top", (length,), dtype=vlen)
        for index in range(length):
            color[index] = np.frombuffer(cv2.imencode(".png", red_rgb)[1].tobytes(), np.uint8)
            depth_ds[index] = np.frombuffer(cv2.imencode(".png", depth)[1].tobytes(), np.uint8)
        f["camera_observations/timestamp"] = np.zeros(length, np.int64)
        f["camera_observations/is_intervene"] = np.zeros(length, bool)
        for root in ("master", "puppet"):
            group = f.create_group(f"{root}/arm_left_position_align")
            group["data"] = np.zeros((arm_rows or length, 7))
            group["timestamp"] = np.zeros(length, np.int64)
            group["is_intervene"] = np.zeros(length, bool)
            pose = f.create_group(f"{root}/end_effector_left_pose_align")
            pose["data"] = np.zeros((0,))
            pose["timestamp"] = np.zeros(length, np.int64)
            pose["is_intervene"] = np.zeros(length, bool)


def test_color_channel_and_depth_are_preserved(tmp_path):
    path = tmp_path / "trajectory.hdf5"
    write_episode(path)
    with H5Episode(path) as episode:
        assert episode.read_color("camera_top", 0)[0, 0].tolist() == [200, 0, 0]
        depth = episode.read_depth("camera_top", 0)
        assert depth.dtype == np.uint16 and int(depth[0, 0]) == 1234
        assert episode.image_change_indices("color", "camera_top").tolist() == [0]
        assert {issue["type"] for issue in episode.check()} == {"empty"}


def test_length_mismatch_is_reported(tmp_path):
    path = tmp_path / "trajectory.hdf5"
    write_episode(path, length=4, arm_rows=3)
    with H5Episode(path) as episode:
        issues = [issue for issue in episode.check() if issue["type"] == "length"]
    assert {issue["name"] for issue in issues} == {"master/arm_left_position_align/data", "puppet/arm_left_position_align/data"}


def main():
    import sys

    import pytest

    sys.exit(pytest.main([__file__, "-q"]))


if __name__ == "__main__":
    main()
