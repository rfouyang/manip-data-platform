import cv2
import h5py
import numpy as np
import pytest

from component.common.dataset_access import DatasetAccess
from component.common.task_manager import TaskCancelled
from component.conversion.data_converter import DEFAULT_MAPPING_PATH, DataConverter, convert_job, load_mapping
from component.dataset.dataset_manager import DatasetManager
from util.storage_helper import Storage
from util.lerobot_helper import open_dataset, to_depth_mm

SIZE = 64
ROWS_PER_IMAGE = 3


def write_full_episode(path, images=5, depth_value=1000):
    """16 维映射所需字段齐全的小 episode：每 3 行换一张图像，数值逐行不同，便于核对取帧与错位。"""
    length = images * ROWS_PER_IMAGE
    mapping = load_mapping(DEFAULT_MAPPING_PATH)
    vlen = h5py.vlen_dtype(np.uint8)
    path.parent.mkdir(parents=True)
    with h5py.File(path, "w") as f:
        f.create_group("metadata").attrs.update({"trajectory_length": length, "language_instruction": "demo task"})
        f["camera_color_channel/camera_top"] = b"rgb"
        f["camera_model/camera_top"] = b"demo"
        f["camera_color_resolution/camera_top"] = np.array([SIZE, SIZE])
        f["camera_depth_resolution/camera_top"] = np.array([SIZE, SIZE])
        color = f.create_dataset("camera_observations/color_images/camera_top", (length,), dtype=vlen)
        depth = f.create_dataset("camera_observations/depth_images/camera_top", (length,), dtype=vlen)
        for row in range(length):
            image = row // ROWS_PER_IMAGE
            rgb = np.full((SIZE, SIZE, 3), 40 * image, np.uint8)
            depth_image = np.full((SIZE, SIZE), depth_value + 100 * image, np.uint16)
            depth_image[0, 0] = 0
            color[row] = np.frombuffer(cv2.imencode(".png", rgb)[1].tobytes(), np.uint8)
            depth[row] = np.frombuffer(cv2.imencode(".png", depth_image)[1].tobytes(), np.uint8)
        f["camera_observations/timestamp"] = np.arange(length, dtype=np.int64)
        f["camera_observations/is_intervene"] = np.zeros(length, bool)
        for source, scale in (("master", 1000.0), ("puppet", 1.0)):
            for item in mapping["vector"]:
                values = np.arange(length, dtype=np.float64)[:, None] * 0.01 * scale + np.arange(item["dims"]) * 0.001
                group = f.create_group(f"{source}/{item['field']}")
                group["data"] = values if item["dims"] > 1 else values[:, 0]
                group["timestamp"] = np.arange(length, dtype=np.int64)
                group["is_intervene"] = np.zeros(length, bool)


class Reporter:
    task_id = 7

    def __init__(self, cancel_after=None):
        self.calls = 0
        self.cancel_after = cancel_after
        self.statuses = []

    def progress(self, done, total, message=""):
        pass

    def set_status(self, status, message=""):
        self.statuses.append(status)

    def register_dataset(self, name, path, dataset_format):
        self.registered = (name, str(path), dataset_format)
        dataset = {"id": 1, "name": name}
        return dataset

    def check_cancel(self):
        self.calls += 1
        if self.cancel_after is not None and self.calls > self.cancel_after:
            raise TaskCancelled()


def make_params(tmp_path, episode_images=(5, 4), **kwargs):
    root = tmp_path / "source"
    episode_ids = []
    for number, images in enumerate(episode_images):
        episode_id = f"success_episodes/{number:04d}"
        write_full_episode(root / episode_id / "data" / "trajectory.hdf5", images=images, **kwargs)
        episode_ids.append(episode_id)
    params = {
        "dataset_id": 1,
        "dataset_path": str(root),
        "episode_ids": episode_ids,
        "mapping_path": str(DEFAULT_MAPPING_PATH),
        "output_path": str(tmp_path / "output" / "converted"),
        "task": None,
    }
    return params


def test_conversion_semantics_and_publish(tmp_path):
    params = make_params(tmp_path)
    reporter = Reporter()
    result = convert_job(params, reporter)
    output = tmp_path / "output" / "converted"
    assert result["validation"]["passed"], result["validation"]["failed"]
    assert reporter.statuses == ["validating"]
    # 5 张与 4 张图像，各丢弃末帧。
    assert (result["episode_count"], result["frames"]) == (2, 7)
    assert [path.name for path in (tmp_path / "output").iterdir()] == ["converted"]
    # 只有 LeRobot 标准目录：没有平台扩展文件，也不残留空的 images/。
    assert sorted(path.name for path in output.iterdir()) == ["data", "meta", "videos"]

    dataset = open_dataset(output)
    first, last = dataset[0], dataset[3]
    puppet_row = lambda row: np.float32(row * 0.01)
    # state 取当前图像起始行的 puppet，action 取下一张图像起始行的 puppet；master（×1000）不参与。
    assert first["observation.state"][0].item() == puppet_row(0)
    assert first["action"][0].item() == puppet_row(ROWS_PER_IMAGE)
    assert last["action"][0].item() == puppet_row(4 * ROWS_PER_IMAGE)
    depth = to_depth_mm(last["observation.images.camera_top_depth"])
    assert depth[0, 0] == 0 and depth[1, 1] == 1000 + 100 * 3
    assert first["task"] == "demo task"

    report = result["report"]
    assert report["repo_id"] == "local/converted"
    assert [item["episode_id"] for item in report["episodes"]] == ["success_episodes/0000", "success_episodes/0001"]


def test_cancel_removes_temporary_output(tmp_path):
    params = make_params(tmp_path)
    with pytest.raises(TaskCancelled):
        convert_job(params, Reporter(cancel_after=2))
    assert list((tmp_path / "output").iterdir()) == []


def test_existing_output_is_not_overwritten(tmp_path):
    params = make_params(tmp_path)
    existing = tmp_path / "output" / "converted"
    existing.mkdir(parents=True)
    (existing / "keep.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError):
        convert_job(params, Reporter())
    assert [path.name for path in existing.iterdir()] == ["keep.txt"]


def test_longer_image_field_is_rejected_not_truncated(tmp_path):
    # 取帧只遍历 trajectory_length 行；图像比它长时应拒绝转换，而不是静默丢掉多出的行（直接提交可绕过预检）。
    params = make_params(tmp_path)
    path = tmp_path / "source" / params["episode_ids"][1] / "data" / "trajectory.hdf5"
    with h5py.File(path, "a") as f:
        name = "camera_observations/color_images/camera_top"
        rows = list(f[name][:])
        del f[name]
        f.create_dataset(name, data=np.array(rows + rows[-3:], dtype=object), dtype=h5py.vlen_dtype(np.uint8))
    with pytest.raises(ValueError, match="长度不一致"):
        convert_job(params, Reporter())
    assert list((tmp_path / "output").iterdir()) == []


def test_misaligned_depth_episode_is_skipped(tmp_path):
    # 深度比 RGB 晚一行更新：按 RGB 切换行取到的深度是上一张图像，不写入，跳过并记录原因；其他 episode 正常转换。
    params = make_params(tmp_path)
    path = tmp_path / "source" / params["episode_ids"][1] / "data" / "trajectory.hdf5"
    with h5py.File(path, "a") as f:
        name = "camera_observations/depth_images/camera_top"
        rows = list(f[name][:])
        del f[name]
        f.create_dataset(name, data=np.array([rows[0], *rows[:-1]], dtype=object), dtype=h5py.vlen_dtype(np.uint8))
    result = convert_job(params, Reporter())
    assert result["validation"]["passed"], result["validation"]["failed"]
    assert (result["episode_count"], result["frames"]) == (1, 4)
    assert [item["episode_id"] for item in result["skipped_episodes"]] == [params["episode_ids"][1]]
    assert "RGB 与深度图像切换行不一致" in result["skipped_episodes"][0]["reason"]


def test_single_image_episode_is_reported_and_depth_over_range_counted(tmp_path):
    params = make_params(tmp_path, episode_images=(1, 3), depth_value=4000)
    result = convert_job(params, Reporter())
    assert result["validation"]["passed"], result["validation"]["failed"]
    assert [item["episode_id"] for item in result["skipped_episodes"]] == ["success_episodes/0000"]
    assert result["frames"] == 2
    # 第 2 张图像深度 4100 mm 超出 4095，截断为 4095 写入 LeRobot（不可恢复）；像素数记录在任务结果中。
    assert result["depth_over_range_pixels"] == SIZE * SIZE - 1


def test_v3_dataset_registers_and_inspects(tmp_path):
    params = make_params(tmp_path)
    convert_job(params, Reporter())
    access = DatasetAccess()
    manager = DatasetManager(Storage(tmp_path / "test.sqlite3"), access, tmp_path)
    dataset = manager.register(tmp_path / "output" / "converted")
    assert dataset["format"] == "lerobot_v3.0"
    detail = manager.inspect(dataset["id"])
    assert [episode["id"] for episode in detail["episodes"]] == ["episode_000000", "episode_000001"]
    assert detail["total_frames"] == 7 and detail["problem_count"] == 0
    # 不提供 v3 → H5（用户确认）；v3 只能转为 v2.1。
    converter = DataConverter(manager, access, None, tmp_path / "output")
    with pytest.raises(ValueError, match="暂不支持的目标格式"):
        converter.preview(dataset["id"], target="h5")


def main():
    import sys

    sys.exit(pytest.main([__file__, "-q"]))


if __name__ == "__main__":
    main()
