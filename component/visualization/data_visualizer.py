import math
from pathlib import Path

import cv2
import numpy as np
from loguru import logger


# 相机图像边界需要读取整个 episode 的图像字节（约 0.2 s），按 episode 缓存，限制条目数。
IMAGE_STARTS_CACHE_SIZE = 64
# 曲线默认最多返回的点数，超出按等间隔抽样。
DEFAULT_MAX_POINTS = 2000


class DataVisualizer:
    """逐帧查看：episode 帧索引、按行读取 RGB 与原始深度。

    H5 数值行约 190 Hz，图像每约 6 行才更新一次；这里始终按行号定位，并提供相机图像边界供前端跳转。
    """

    def __init__(self, dataset_manager, dataset_access, **kwargs):
        self.dataset_manager = dataset_manager
        self.dataset_access = dataset_access
        # RGB 仅为显示重新编码为 JPEG，不作为转换输出。
        self.jpeg_quality = kwargs.get("jpeg_quality", 95)
        self.image_starts_cache = {}

    def open_episode(self, dataset_id, episode_id):
        dataset = self.dataset_manager.get(dataset_id)
        episode = self.dataset_access.open_episode(dataset["path"], episode_id)
        return episode

    def check_frame(self, episode, camera, index):
        if camera not in episode.cameras:
            raise ValueError(f"相机不存在: {camera}")
        if not 0 <= index < episode.length:
            raise ValueError(f"行号超出范围: {index}，有效范围 0–{episode.length - 1}")

    def image_starts(self, episode, camera):
        # 路由在线程池中并发执行：淘汰与读取都容忍其他请求同时修改缓存（缺项时重新计算）。
        key = (str(episode.path), episode.path.stat().st_mtime_ns, camera)
        starts = self.image_starts_cache.get(key)
        if starts is None:
            starts = episode.image_change_indices("color", camera).tolist()
            if len(self.image_starts_cache) >= IMAGE_STARTS_CACHE_SIZE:
                self.image_starts_cache.pop(next(iter(self.image_starts_cache), None), None)
            self.image_starts_cache[key] = starts
        return starts

    def get_episode(self, dataset_id, episode_id):
        dataset_format = self.dataset_manager.get(dataset_id)["format"]
        with self.open_episode(dataset_id, episode_id) as episode:
            cameras = {}
            for camera in episode.cameras:
                camera_info = episode.camera_info(camera)
                camera_info["image_starts"] = self.image_starts(episode, camera)
                cameras[camera] = camera_info
            series_names = self.available_series(episode)
            # v3 的逐维名称（如关节名）用于曲线图例与数值表；H5 没有名称时为 None。
            series_labels = {name: episode.series_info(name).get("names") for name in series_names}
            timestamps = episode.read_timestamps().tolist()
            is_intervene = episode.read_is_intervene()
            is_intervene = None if is_intervene is None else is_intervene.tolist()
            subtasks = episode.read_subtasks()
            length = episode.length

        frame_index = {
            "dataset_id": dataset_id,
            "episode_id": episode_id,
            "length": length,
            "cameras": cameras,
            "format": dataset_format,
            # H5 为采集端整数秒；LeRobot 为 frame_index / fps 生成的秒。
            "timestamp_unit": "整数秒" if dataset_format == "h5" else "秒（frame_index / fps）",
            "series_names": series_names,
            "series_labels": series_labels,
            "timestamps": timestamps,
            "is_intervene": is_intervene,
            # 子任务分段（LeRobot 0.6 语言标注列中 style=subtask 的行）；没有时为空列表。
            "subtasks": subtasks,
        }
        return frame_index

    def available_series(self, episode):
        """可用于曲线的字段：排除空数组（样本中 4 个 pose 字段为空）。"""
        names = []
        for name in episode.series_names:
            if not episode.series_info(name)["empty"]:
                names.append(name)
        return names

    def check_series(self, episode, names):
        available = self.available_series(episode)
        for name in names:
            if name not in available:
                raise ValueError(f"字段不存在或为空: {name}")

    def get_series(self, dataset_id, episode_id, names, **kwargs):
        """返回若干字段的曲线数据。

        点数超过 max_points 时按等间隔抽样（只用于绘图；当前帧面板显示未抽样的原值）。
        """
        with self.open_episode(dataset_id, episode_id) as episode:
            self.check_series(episode, names)
            start = int(kwargs.get("start", 0))
            stop = int(kwargs.get("stop", episode.length))
            if not 0 <= start < stop <= episode.length:
                raise ValueError(f"行范围无效: {start}–{stop}，有效范围 0–{episode.length}")
            max_points = int(kwargs.get("max_points", DEFAULT_MAX_POINTS))
            if max_points < 1:
                raise ValueError(f"max_points 必须为正整数: {max_points}")
            stride = max(1, math.ceil((stop - start) / max_points))

            rows = list(range(start, stop, stride))
            series = {}
            for name in names:
                values = episode.read_series(name, start, stop)[::stride]
                columns = np.atleast_2d(values.T)
                series[name] = columns.tolist()

        curves = {
            "episode_id": episode_id,
            "start": start,
            "stop": stop,
            "stride": stride,
            "rows": rows,
            "series": series,
        }
        return curves

    def get_frame(self, dataset_id, episode_id, index):
        """返回某一行的原值：各字段数值、整数秒时间戳和干预标记，不做抽样或换算。"""
        with self.open_episode(dataset_id, episode_id) as episode:
            if not 0 <= index < episode.length:
                raise ValueError(f"行号超出范围: {index}，有效范围 0–{episode.length - 1}")
            values = {}
            for name in self.available_series(episode):
                row = episode.read_series(name, index, index + 1)[0]
                values[name] = np.atleast_1d(row).tolist()
            timestamp = episode.read_timestamps(index, index + 1)[0].item()
            intervene = episode.read_is_intervene(index, index + 1)
            is_intervene = None if intervene is None else bool(intervene[0])

        frame = {
            "episode_id": episode_id,
            "index": index,
            "timestamp": timestamp,
            "is_intervene": is_intervene,
            "values": values,
        }
        return frame

    def get_color(self, dataset_id, episode_id, camera, index):
        """返回该行 RGB 图像的显示用 JPEG 字节。"""
        with self.open_episode(dataset_id, episode_id) as episode:
            self.check_frame(episode, camera, index)
            rgb = episode.read_color(camera, index)
        # imencode 按 BGR 解释数组，先把 RGB 转回 BGR，浏览器才会显示正确颜色。
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        _, encoded = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality])
        jpeg = encoded.tobytes()
        return jpeg

    def get_depth(self, dataset_id, episode_id, camera, index):
        """返回该行 uint16 深度（毫米），按行优先、小端序排列，不做换算。"""
        with self.open_episode(dataset_id, episode_id) as episode:
            self.check_frame(episode, camera, index)
            depth = episode.read_depth(camera, index)
        raw = depth.astype("<u2").tobytes()
        return raw


REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_OUTPUT = REPO_ROOT / "output" / "demo" / "data_visualizer"


def demo_data_visualizer():
    import time

    from component.common.dataset_access import DatasetAccess
    from component.dataset.dataset_manager import DatasetManager
    from util.storage_helper import Storage

    db_path = DEMO_OUTPUT / "demo.sqlite3"
    db_path.unlink(missing_ok=True)
    access = DatasetAccess()
    manager = DatasetManager(Storage(db_path), access, REPO_ROOT / "data")
    visualizer = DataVisualizer(manager, access)
    dataset = manager.register("place_red_pepper_on_red_tray")
    episode_id = "success_episodes/0106_102314"

    started = time.perf_counter()
    frame_index = visualizer.get_episode(dataset["id"], episode_id)
    first_ms = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    visualizer.get_episode(dataset["id"], episode_id)
    cached_ms = (time.perf_counter() - started) * 1000
    starts = frame_index["cameras"]["camera_top"]["image_starts"]
    logger.info("Episode index: {} rows, {} camera images, first {:.0f} ms, cached {:.0f} ms", frame_index["length"], len(starts), first_ms, cached_ms)

    jpeg = visualizer.get_color(dataset["id"], episode_id, "camera_top", 1047)
    raw = visualizer.get_depth(dataset["id"], episode_id, "camera_top", 1047)
    DEMO_OUTPUT.mkdir(parents=True, exist_ok=True)
    (DEMO_OUTPUT / "frame01047.jpg").write_bytes(jpeg)

    # 显示用 JPEG 按标准方式解码后，托盘区域应为红色。
    decoded = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    tray_rgb = decoded[250:350, 220:340].reshape(-1, 3).mean(0)[::-1]
    depth = np.frombuffer(raw, "<u2").reshape(480, 640)
    assert tray_rgb[0] > 2 * tray_rgb[2], tray_rgb
    logger.info("Color JPEG {} bytes, tray mean RGB={}; depth raw {} bytes, center={}", len(jpeg), tray_rgb.round(1), len(raw), depth[240, 320])

    names = frame_index["series_names"][:2]
    curves = visualizer.get_series(dataset["id"], episode_id, names)
    frame = visualizer.get_frame(dataset["id"], episode_id, 1047)
    logger.info("Series {}: stride={}, points={}, dims={}", names[0], curves["stride"], len(curves["rows"]), len(curves["series"][names[0]]))
    logger.info("Frame 1047 timestamp={} intervene={}", frame["timestamp"], frame["is_intervene"])
    logger.info("Frame 1047 {} = {}", names[0], frame["values"][names[0]])

    downsampled = visualizer.get_series(dataset["id"], episode_id, names, max_points=200)
    assert downsampled["stride"] > 1 and len(downsampled["rows"]) <= 200

    try:
        visualizer.get_series(dataset["id"], episode_id, ["master/end_effector_left_pose_align"])
        raise AssertionError("empty series should be rejected")
    except ValueError as error:
        logger.info("Rejected: {}", error)

    try:
        visualizer.get_color(dataset["id"], episode_id, "camera_top", frame_index["length"])
        raise AssertionError("out-of-range index should be rejected")
    except ValueError as error:
        logger.info("Rejected: {}", error)


def main():
    demo_data_visualizer()


if __name__ == "__main__":
    main()
