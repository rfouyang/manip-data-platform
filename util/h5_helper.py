from pathlib import Path

import cv2
import h5py
import numpy as np
from loguru import logger


EPISODE_FILE = "trajectory.hdf5"
# 顶层非数值分组；其余顶层分组下含 data 的子分组视为数值字段（采集样本为 master/puppet）。
NON_SERIES_ROOTS = {"metadata", "camera_observations", "camera_color_channel", "camera_model", "camera_color_resolution", "camera_depth_resolution"}
IMAGE_KINDS = ("color", "depth")
LFS_POINTER_HEAD = b"version https://git-lfs"


def find_episodes(root):
    """返回 root 下全部 episode 文件，目录形态为 <任务>/success_episodes/<episode>/data/trajectory.hdf5。"""
    paths = sorted(Path(root).rglob(EPISODE_FILE))
    return paths


def is_lfs_pointer(path):
    """未下载的 Git LFS 文件只是一个以固定文本开头的小指针文件。"""
    with open(path, "rb") as file:
        head = file.read(len(LFS_POINTER_HEAD))
    is_pointer = head == LFS_POINTER_HEAD
    return is_pointer


def to_plain(value):
    """把 H5 读出的 bytes / numpy 值转成普通 Python 值，便于日志和 JSON。"""
    if isinstance(value, bytes):
        text = value.decode("utf-8")
        return text
    if isinstance(value, np.generic):
        number = value.item()
        return number
    if isinstance(value, np.ndarray):
        items = value.tolist()
        return items
    return value


class H5Episode:
    """只读访问单个 H5 episode。图像按帧解码，数值按切片读取，不整体加载文件。"""

    def __init__(self, path):
        self.path = Path(path)
        self.file = h5py.File(self.path, "r")
        self.length = int(self.file["metadata"].attrs["trajectory_length"])
        self.cameras = sorted(self.file["camera_observations/color_images"].keys())

        self.series_names = []
        for root in sorted(set(self.file.keys()) - NON_SERIES_ROOTS):
            for name in sorted(self.file[root].keys()):
                if isinstance(self.file[root][name], h5py.Group) and "data" in self.file[root][name]:
                    self.series_names.append(f"{root}/{name}")

    def close(self):
        self.file.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def read_value(self, name):
        raw = self.file[name][()]
        value = to_plain(raw)
        return value

    def metadata(self):
        attrs = self.file["metadata"].attrs
        metadata = {}
        for key in attrs:
            metadata[key] = to_plain(attrs[key])
        return metadata

    def camera_info(self, camera):
        model = self.read_value(f"camera_model/{camera}")
        color_channel = self.read_value(f"camera_color_channel/{camera}")
        # 分辨率存储顺序为 [height, width]，demo 用解码结果核对。
        color_resolution = self.read_value(f"camera_color_resolution/{camera}")
        depth_name = f"camera_depth_resolution/{camera}"
        depth_resolution = self.read_value(depth_name) if depth_name in self.file else None
        camera_info = {
            "model": model,
            "color_channel": color_channel,
            "color_resolution": color_resolution,
            "depth_resolution": depth_resolution,
        }
        return camera_info

    def series_info(self, name):
        data = self.file[f"{name}/data"]
        shape = list(data.shape)
        dtype = str(data.dtype)
        empty = data.size == 0
        # 采集样本不含逐维名称，名称来自转换映射。
        series_info = {"shape": shape, "dtype": dtype, "empty": empty, "names": None}
        return series_info

    def info(self):
        cameras = {}
        for camera in self.cameras:
            cameras[camera] = self.camera_info(camera)

        series = {}
        for name in self.series_names:
            series[name] = self.series_info(name)

        timestamps = self.read_timestamps()
        timestamp_range = [timestamps[0].item(), timestamps[-1].item()]
        metadata = self.metadata()
        issues = self.check()

        info = {
            "path": str(self.path),
            "length": self.length,
            "metadata": metadata,
            "cameras": cameras,
            "series": series,
            "timestamp_range": timestamp_range,
            "issues": issues,
        }
        return info

    def frame_dataset_names(self):
        """应与 trajectory_length 等长的逐帧字段；空 pose 的 data 不在其中。"""
        names = ["camera_observations/timestamp", "camera_observations/is_intervene"]
        for kind in IMAGE_KINDS:
            for camera in self.cameras:
                names.append(f"camera_observations/{kind}_images/{camera}")
        for name in self.series_names:
            names.append(f"{name}/timestamp")
            names.append(f"{name}/is_intervene")
            if self.file[f"{name}/data"].size > 0:
                names.append(f"{name}/data")
        return names

    def check(self):
        """核对逐帧字段长度与 trajectory_length 一致；空 pose 字段单独报告，不补零。"""
        issues = []
        for name in self.series_names:
            if self.file[f"{name}/data"].size == 0:
                issues.append({"type": "empty", "name": f"{name}/data"})

        for name in self.frame_dataset_names():
            if name not in self.file:
                issues.append({"type": "missing", "name": name})
                continue
            length = self.file[name].shape[0]
            if length != self.length:
                issues.append({"type": "length", "name": name, "length": length})
        return issues

    def read_series(self, name, start=0, stop=None):
        values = self.file[f"{name}/data"][start:stop]
        return values

    def read_timestamps(self, start=0, stop=None):
        # 原始时间戳为整数秒，不能恢复亚秒间隔。
        timestamps = self.file["camera_observations/timestamp"][start:stop]
        return timestamps

    def read_is_intervene(self, start=0, stop=None):
        # 从外部 LeRobot 数据导出的文件没有干预标记，返回 None，不补值。
        if "camera_observations/is_intervene" not in self.file:
            return None
        values = self.file["camera_observations/is_intervene"][start:stop]
        return values

    def read_subtasks(self):
        # H5 没有子任务标注。
        return []

    def read_encoded(self, kind, camera, index):
        """返回原始编码字节：color 为 JPEG，depth 为 16 位 PNG。"""
        images = self.file[f"camera_observations/{kind}_images/{camera}"]
        encoded = images[index].tobytes()
        return encoded

    def image_change_indices(self, kind, camera):
        """返回编码字节与上一行不同的行号。样本中数值行约 190 Hz，图像重复约 6 行，此处用于得到实际相机帧。
        需要读取该相机全部编码数据，只用于预检或转换，不用于列表展示。"""
        images = self.file[f"camera_observations/{kind}_images/{camera}"]
        starts = []
        previous = None
        for index in range(self.length):
            current = images[index].tobytes()
            if current != previous:
                starts.append(index)
            previous = current
        indices = np.array(starts)
        return indices

    def read_color(self, camera, index):
        """返回 RGB uint8 图像。

        camera_color_channel 记录编码前数组的通道顺序。样本为 "rgb"：采集端把 RGB 数组直接交给 OpenCV 编码，
        因此 cv2.imdecode 的输出已是 RGB，不能再做 BGR→RGB（红色托盘会变成蓝色，已用样本帧目视核对）。
        """
        buffer = np.frombuffer(self.read_encoded("color", camera, index), np.uint8)
        image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
        channel = self.read_value(f"camera_color_channel/{camera}")
        if channel == "rgb":
            return image
        if channel == "bgr":
            rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            return rgb
        raise ValueError(f"Unknown camera_color_channel {channel!r}: {self.path}")

    def read_depth(self, camera, index):
        """返回原始 uint16 深度像素值；物理单位尚未确认，不做换算。"""
        buffer = np.frombuffer(self.read_encoded("depth", camera, index), np.uint8)
        depth = cv2.imdecode(buffer, cv2.IMREAD_UNCHANGED)
        if depth.dtype != np.uint16 or depth.ndim != 2:
            raise ValueError(f"Unexpected depth image {depth.dtype} {depth.shape}: {self.path} frame {index}")
        return depth


REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = REPO_ROOT / "data"
DEMO_OUTPUT = REPO_ROOT / "output" / "demo" / "h5_helper"


def save_preview(color, depth, name):
    DEMO_OUTPUT.mkdir(parents=True, exist_ok=True)
    # imwrite 按 BGR 写文件，因此 RGB 图像先转回 BGR。
    cv2.imwrite(str(DEMO_OUTPUT / f"{name}_color.png"), cv2.cvtColor(color, cv2.COLOR_RGB2BGR))
    cv2.imwrite(str(DEMO_OUTPUT / f"{name}_depth_raw.png"), depth)
    # 伪彩色仅供人工查看，不代表深度单位或转换结果。
    scale = 255 / max(int(depth.max()), 1)
    preview = cv2.applyColorMap(cv2.convertScaleAbs(depth, alpha=scale), cv2.COLORMAP_TURBO)
    cv2.imwrite(str(DEMO_OUTPUT / f"{name}_depth_preview.png"), preview)


def demo_read_episode(path):
    with H5Episode(path) as episode:
        info = episode.info()
        logger.info("Episode {}: {} frames, cameras={}", path, info["length"], episode.cameras)
        logger.info("Metadata: {}", info["metadata"])
        logger.info("Timestamp range: {}", info["timestamp_range"])
        for name, item in info["series"].items():
            logger.info("  {:<45} {} {} empty={}", name, item["shape"], item["dtype"], item["empty"])
        logger.info("Issues: {}", info["issues"])

        camera = episode.cameras[0]
        camera_info = info["cameras"][camera]
        for index in (0, episode.length // 2, episode.length - 1):
            color = episode.read_color(camera, index)
            depth = episode.read_depth(camera, index)
            assert list(color.shape[:2]) == camera_info["color_resolution"]
            assert list(depth.shape) == camera_info["depth_resolution"]
            non_zero = depth[depth > 0]
            zero_ratio = 1 - non_zero.size / depth.size
            logger.info(
                "Frame {}: color {}, depth {} {}, non-zero min/max={}/{}, zero ratio={:.3f}",
                index, color.shape, depth.shape, depth.dtype, non_zero.min(), non_zero.max(), zero_ratio,
            )
            save_preview(color, depth, f"frame{index:05d}")
        logger.info("Preview images written to {}", DEMO_OUTPUT)

        chunk = episode.read_series("puppet/arm_left_position_align", 0, 10)
        assert chunk.shape == (10, 7)

        timestamps = episode.read_timestamps()
        _, rows_per_second = np.unique(timestamps, return_counts=True)
        # 首尾两秒不完整，只用中间整秒估计行率。
        row_rate = rows_per_second[1:-1].mean()
        color_starts = episode.image_change_indices("color", camera)
        depth_starts = episode.image_change_indices("depth", camera)
        rows_per_image = np.diff(color_starts)
        camera_rate = len(color_starts) * row_rate / episode.length
        logger.info("Rows per timestamp-second: {:.1f}", row_rate)
        logger.info("Distinct color images={}, depth images={}", len(color_starts), len(depth_starts))
        logger.info("Color and depth change at same rows: {}", np.array_equal(color_starts, depth_starts))
        logger.info("Rows per camera image: mean={:.2f} min={} max={}", rows_per_image.mean(), rows_per_image.min(), rows_per_image.max())
        logger.info("Estimated camera rate: {:.1f} images/s", camera_rate)


def schema_signature(episode):
    """字段名、dtype、单帧形状和是否为空，加上相机列表；用于比较 episode 结构。"""
    fields = []
    for name in episode.series_names:
        data = episode.file[f"{name}/data"]
        fields.append((name, str(data.dtype), data.shape[1:], data.size == 0))
    signature = (tuple(fields), tuple(episode.cameras))
    return signature


def demo_scan_episodes(root):
    """仅读取元信息和字段形状，统计全部 episode 的结构是否一致，不解码图像。"""
    paths = find_episodes(root)
    signatures = {}
    lengths = []
    problems = []
    for path in paths:
        if not h5py.is_hdf5(path):
            # 例如未下载的 Git LFS 指针文件；记录后继续扫描其余 episode。
            problems.append((path, f"not an HDF5 file ({path.stat().st_size} bytes)"))
            continue
        with H5Episode(path) as episode:
            signature = schema_signature(episode)
            signatures.setdefault(signature, []).append(path)
            lengths.append(episode.length)
            issues = [issue for issue in episode.check() if issue["type"] != "empty"]
            if issues:
                problems.append((path, issues))

    logger.info("Scanned {} episode files under {}, readable={}", len(paths), root, len(lengths))
    logger.info("Schema variants: {}", len(signatures))
    for (_, cameras), members in signatures.items():
        logger.info("  {} episodes, cameras={}, e.g. {}", len(members), cameras, members[0].parent.parent.name)
    logger.info("Frames: total={} min={} max={} mean={:.1f}", sum(lengths), min(lengths), max(lengths), np.mean(lengths))
    logger.info("Episodes with problems: {}", len(problems))
    for path, issue in problems:
        logger.warning("  {}: {}", path, issue)


def main():
    first_episode = find_episodes(DATA_ROOT)[0]
    demo_read_episode(first_episode)
    demo_scan_episodes(DATA_ROOT)


if __name__ == "__main__":
    main()
