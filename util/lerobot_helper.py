import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.compute as pc
import pyarrow.parquet as pq
from loguru import logger

from lerobot.configs.video import DepthEncoderConfig, encoder_config_from_video_info
from lerobot.datasets.dataset_metadata import LeRobotDatasetMetadata
from lerobot.datasets.depth_utils import dequantize_depth
from lerobot.datasets.lerobot_dataset import LeRobotDataset
from lerobot.datasets.video_utils import FrameTimestampError, decode_video_frames

from util.lerobot_v21_helper import V21, V21Metadata


# Windows 上 torchcodec 需要系统 FFmpeg 动态库，通常加载失败；显式使用 PyAV，避免每次打开都告警后回退。
VIDEO_BACKEND = "pyav"
# 线性量化，0–4.095 m 对应 12 位代码 0–4095：每级正好 1 mm。源深度为整数毫米，≤4095 mm 时编码无损。
DEPTH_MAX_MM = 4095
DEPTH_QUANT = {"depth_min": 0.0, "depth_max": DEPTH_MAX_MM / 1000, "use_log": False}


# 封闭 GOP：x265 默认开放 GOP，每个 episode 末尾可能出现引用本段更早帧的 B 帧（RASL）；多个 episode 拼成一个视频后，
# 官方 PyAV 读取先跳到目标前一个关键帧再解码，会把下一个 episode 的首帧当作重复 POC 丢弃，读取报错。
# 实测官方录制同样出现；open-gop=0 后仍为无损，官方随机读取全部成功（2026-09-24）。
DEPTH_X265_PARAMS = "lossless=1:open-gop=0"


def depth_encoder():
    encoder = DepthEncoderConfig(**DEPTH_QUANT, extra_options={"x265-params": DEPTH_X265_PARAMS})
    return encoder


def image_feature(shape, is_depth=False):
    feature = {"dtype": "video", "shape": tuple(shape), "names": ["height", "width", "channels"]}
    if is_depth:
        feature["info"] = {"is_depth_map": True}
    return feature


def vector_feature(names):
    feature = {"dtype": "float32", "shape": (len(names),), "names": list(names)}
    return feature


def create_dataset(root, fps, features, **kwargs):
    """创建写入模式的 v3.0 数据集。root 必须不存在（LeRobot 拒绝写入已有目录）。"""
    root = Path(root)
    dataset = LeRobotDataset.create(
        repo_id=kwargs.get("repo_id", f"local/{root.name}"),
        fps=fps,
        features=features,
        root=root,
        robot_type=kwargs.get("robot_type"),
        use_videos=True,
        depth_encoder=depth_encoder(),
        video_backend=VIDEO_BACKEND,
    )
    return dataset


def save_episode(dataset):
    # 默认并行编码会为每路视频另起进程池；转换本身已在工作进程中，逐路编码使资源占用有界。
    dataset.save_episode(parallel_encoding=False)


def remove_empty_image_dir(root):
    """LeRobot 编码视频后删除逐帧临时图片，但保留空的 images/<feature>/ 目录；标准数据集没有 images/，发布前删除。
    其中仍有文件说明视频编码未完成，拒绝发布。"""
    image_dir = Path(root) / "images"
    if not image_dir.exists():
        return
    files = [path for path in image_dir.rglob("*") if path.is_file()]
    if files:
        raise ValueError(f"images/ 中残留 {len(files)} 个临时图像文件，视频编码可能未完成: {files[0]}")
    shutil.rmtree(image_dir)


def open_dataset(root, **kwargs):
    root = Path(root)
    dataset = LeRobotDataset(kwargs.get("repo_id", f"local/{root.name}"), root=root, video_backend=VIDEO_BACKEND)
    return dataset


def to_depth_mm(value):
    """读回的深度为 float32 毫米张量 (1, H, W)，转为 uint16 (H, W)。"""
    depth = np.rint(value.numpy().squeeze()).astype(np.uint16)
    return depth


def to_rgb_uint8(value):
    """读回的 RGB 为 float32 (3, H, W)，取值 0–1，转为 uint8 (H, W, 3)。"""
    rgb = np.rint(value.numpy().transpose(1, 2, 0) * 255).clip(0, 255).astype(np.uint8)
    return rgb


IMAGE_PREFIX = "observation.images."
# 平台约定：深度 feature 名为对应 RGB feature 名加 _depth。
DEPTH_SUFFIX = "_depth"
# LeRobot 自动生成的逐帧列，不作为曲线字段。
INDEX_COLUMNS = ("timestamp", "frame_index", "episode_index", "index", "task_index")
NUMERIC_DTYPES = ("float32", "float64", "int32", "int64", "bool")
# 与 LeRobotDataset 默认值一致：按时间戳在视频中定位帧的容差。
VIDEO_TOLERANCE_S = 1e-4


def episode_id_of(episode_index):
    episode_id = f"episode_{episode_index:06d}"
    return episode_id


def episode_index_of(episode_id):
    prefix = "episode_"
    if not episode_id.startswith(prefix) or not episode_id[len(prefix):].isdigit():
        raise LookupError(f"episode 不存在: {episode_id}")
    index = int(episode_id[len(prefix):])
    return index


def codebase_version(root):
    info = json.loads((Path(root) / "meta" / "info.json").read_text(encoding="utf-8"))
    version = info.get("codebase_version")
    return version


def info_summary(root):
    """meta/info.json 中的 episode 数与帧数（v2.1 与 v3.0 相同），只读一个小文件，供数据集列表显示。
    登记后目录可能被移走或删除，此时返回 None，列表照常显示其他数据集。"""
    path = Path(root) / "meta" / "info.json"
    if not path.exists():
        return None
    info = json.loads(path.read_text(encoding="utf-8"))
    summary = {"episodes": info["total_episodes"], "frames": info["total_frames"]}
    return summary


def load_metadata(root):
    """v3.0 用官方 LeRobotDatasetMetadata；v2.1 用 V21Metadata（提供相同的读取接口，lerobot 0.6.1 不再读取 v2.1）。"""
    root = Path(root)
    if codebase_version(root) == V21:
        metadata = V21Metadata(root)
        return metadata
    metadata = LeRobotDatasetMetadata(f"local/{root.name}", root=root)
    return metadata


def task_names(meta):
    """{task_index: 任务描述}。v3.0 的 meta.tasks 是以任务描述为索引的表，v2.1（V21Metadata）已是字典。"""
    if isinstance(meta.tasks, dict):
        names = dict(meta.tasks)
        return names
    names = {int(row["task_index"]): task for task, row in meta.tasks.iterrows()}
    return names


def local_repo_id(params):
    """LeRobot 的 repo_id 不写入数据集文件；本地读取以 root 为准，repo_id 只作标识，上传 Hub 时再指定真实名称。"""
    repo_id = f"local/{Path(params['output_path']).name}"
    return repo_id


def data_files(root):
    """v3.0 的全部 data 文件，按路径排序（与官方 add_features 等编辑函数遍历的顺序相同）。"""
    files = sorted((Path(root) / "data").glob("*/*.parquet"))
    return files


def column_values(root, name):
    """全部 data 文件中某一列的值（按文件顺序拼接），只读这一列，不把整表读入内存。"""
    values = np.concatenate([pq.read_table(path, columns=[name]).column(name).to_numpy() for path in data_files(root)])
    return values


def data_columns(root):
    """data 文件的列名（读第一个文件的 schema，不读数据）。"""
    names = pq.read_schema(data_files(root)[0]).names
    return names


def episode_table(root, meta, episode_index, cache):
    """该 episode 的 v3.0 数据行，按 frame_index 排序。cache 只保留当前 data 文件（每个不超过约 100 MB）。"""
    path = Path(root) / meta.get_data_file_path(episode_index)
    if path not in cache:
        cache.clear()
        cache[path] = pq.read_table(path)
    table = cache[path]
    table = table.filter(pc.equal(table["episode_index"], episode_index)).sort_by("frame_index")
    return table


# lerobot 0.6 的语言标注列（官方 lerobot-annotate 写入）：language_persistent 为整个 episode 的持续状态行（subtask、plan、memory 等，
# 各带生效起始 timestamp），复制到该 episode 的每一帧；language_events 为发生在某一帧的事件行。
LANGUAGE_PERSISTENT = "language_persistent"
LANGUAGE_EVENTS = "language_events"
LANGUAGE_COLUMNS = (LANGUAGE_PERSISTENT, LANGUAGE_EVENTS)


def language_rows(root):
    """每个 episode 的语言标注：{episode_index: {"persistent": [行], "events": [带 timestamp 的行]}}；数据中没有语言列时返回空字典。

    persistent 在该 episode 每一帧相同，只取首帧；events 只取非空的帧，并补上该帧的 timestamp（官方暂存格式要求）。"""
    result = {}
    for path in data_files(root):
        names = pq.read_schema(path).names
        if LANGUAGE_PERSISTENT not in names:
            continue
        table = pq.read_table(path, columns=["episode_index", "timestamp", *[name for name in LANGUAGE_COLUMNS if name in names]])
        episodes = table.column("episode_index").to_numpy()
        timestamps = table.column("timestamp").to_numpy()
        starts = np.flatnonzero(np.r_[True, episodes[1:] != episodes[:-1]])
        for start in starts:
            episode = int(episodes[start])
            persistent = table.column(LANGUAGE_PERSISTENT)[int(start)].as_py() or []
            result.setdefault(episode, {"persistent": persistent, "events": []})
        if LANGUAGE_EVENTS in names:
            events = table.column(LANGUAGE_EVENTS)
            for index in np.flatnonzero(pc.fill_null(pc.list_value_length(events), 0).to_numpy()):
                for row in events[int(index)].as_py():
                    result[int(episodes[index])]["events"].append({**row, "timestamp": float(timestamps[index])})
    return result


def subtask_segments(persistent, frame_timestamps):
    """由 style=subtask 的行得到分段 [{"start": 起始帧, "text": 描述}]（按开始时间排序；timestamp 对应到最近的帧）。"""
    rows = sorted((row for row in persistent if row.get("style") == "subtask"), key=lambda row: float(row["timestamp"]))
    stamps = np.asarray(frame_timestamps, dtype=np.float64)
    segments = [{"start": int(np.abs(stamps - float(row["timestamp"])).argmin()), "text": row.get("content") or ""} for row in rows]
    return segments


def plain_value(value):
    """数组、列表、字典逐层转为 Python 基本类型，用于比较含嵌套结构的列（如语言列）。"""
    if isinstance(value, np.ndarray):
        value = value.tolist()
    if isinstance(value, dict):
        result = {key: plain_value(item) for key, item in value.items()}
        return result
    if isinstance(value, (list, tuple)):
        result = [plain_value(item) for item in value]
        return result
    if isinstance(value, np.generic):
        value = value.item()
    return value


def columns_equal(actual, expected):
    """比较两列逐帧值（pandas 列的 to_numpy()）：数值与定长向量按数组比较；字符串与嵌套结构（语言列）按 Python 值比较。"""
    if len(actual) != len(expected):
        return False
    if len(actual) == 0:
        return True
    first = actual[0]
    if isinstance(first, (str, dict, list)) or first is None or (isinstance(first, np.ndarray) and first.dtype == object):
        equal = [plain_value(item) for item in actual] == [plain_value(item) for item in expected]
        return equal
    equal = bool(np.array_equal(np.stack(actual), np.stack(expected)))
    return equal


def head_rows(root, meta, count):
    """第一个 episode 的前 count 行（全部非视频列），按 frame_index 排序；数据文件只读前若干行组。"""
    path = Path(root) / meta.get_data_file_path(0)
    table = next(pq.ParquetFile(path).iter_batches(batch_size=max(count * 20, 100))).to_pandas()
    table = table[table["episode_index"] == 0].sort_values("frame_index").head(count)
    return table


class LeRobotEpisode:
    """以 H5Episode 相同的接口只读访问 v3.0 或 v2.1 数据集中的一个 episode，供数据集详情与逐帧可视化复用。

    行号即 frame_index；每一帧都有图像。图像按需从视频解码单帧，数值在首次使用时从 parquet 读取该 episode 的行。
    只支持以视频保存的图像 feature；深度按 feature 记录的量化参数还原为毫米。"""

    def __init__(self, root, episode_index, meta=None):
        self.root = Path(root)
        self.meta = meta if meta is not None else load_metadata(self.root)
        if not 0 <= episode_index < self.meta.total_episodes:
            raise LookupError(f"episode 不存在: {episode_id_of(episode_index)}")
        self.episode_index = episode_index
        self.episode = self.meta.episodes[episode_index]
        # 缓存键使用 info.json 的修改时间。
        self.path = self.root / "meta" / "info.json"
        self.length = int(self.episode["length"])
        depth_keys = set(self.meta.depth_keys)
        self.color_keys = {}
        for key in self.meta.video_keys:
            if key not in depth_keys:
                camera = key[len(IMAGE_PREFIX):] if key.startswith(IMAGE_PREFIX) else key
                self.color_keys[camera] = key
        self.cameras = sorted(self.color_keys)
        self.series_names = [
            key for key, feature in self.meta.features.items()
            if key not in INDEX_COLUMNS and feature["dtype"] in NUMERIC_DTYPES and len(feature["shape"]) == 1
        ]
        self.table = None

    def close(self):
        self.table = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def depth_key(self, camera):
        key = f"{self.color_keys[camera]}{DEPTH_SUFFIX}"
        key = key if key in self.meta.depth_keys else None
        return key

    def rows(self):
        """该 episode 的 parquet 行，按 frame_index 排序。"""
        if self.table is None:
            path = self.root / self.meta.get_data_file_path(self.episode_index)
            columns = ["frame_index", "timestamp", *self.series_names]
            table = pd.read_parquet(path, columns=columns, filters=[("episode_index", "==", self.episode_index)])
            self.table = table.sort_values("frame_index").reset_index(drop=True)
        return self.table

    def metadata(self):
        metadata = {
            "language_instruction": "；".join(self.episode["tasks"]),
            "collection_time": None,
            "episode_index": self.episode_index,
            "fps": self.meta.fps,
        }
        return metadata

    def camera_info(self, camera):
        color_shape = self.meta.features[self.color_keys[camera]]["shape"]
        depth_key = self.depth_key(camera)
        depth_resolution = list(self.meta.features[depth_key]["shape"][:2]) if depth_key else None
        camera_info = {
            "model": None,
            "color_channel": "rgb",
            "color_resolution": list(color_shape[:2]),
            "depth_resolution": depth_resolution,
            "color_feature": self.color_keys[camera],
            "depth_feature": depth_key,
        }
        return camera_info

    def series_info(self, name):
        feature = self.meta.features[name]
        series_info = {
            "shape": [self.length, *feature["shape"]],
            "dtype": feature["dtype"],
            "empty": False,
            "names": feature.get("names"),
        }
        return series_info

    def check(self):
        rows = self.rows()
        issues = []
        if len(rows) != self.length:
            issues.append({"type": "length", "name": "data", "length": len(rows)})
        elif not np.array_equal(rows["frame_index"].to_numpy(), np.arange(self.length)):
            issues.append({"type": "frame_index", "name": "data/frame_index"})
        return issues

    def info(self):
        cameras = {camera: self.camera_info(camera) for camera in self.cameras}
        series = {name: self.series_info(name) for name in self.series_names}
        timestamps = self.read_timestamps()
        info = {
            "path": str(self.root),
            "length": self.length,
            "metadata": self.metadata(),
            "cameras": cameras,
            "series": series,
            "timestamp_range": [timestamps[0].item(), timestamps[-1].item()],
            "issues": self.check(),
        }
        return info

    def read_series(self, name, start=0, stop=None):
        values = np.stack(self.rows()[name].to_numpy()[start:stop])
        return values

    def read_timestamps(self, start=0, stop=None):
        # LeRobot 时间戳为 frame_index / fps（秒）。
        timestamps = self.rows()["timestamp"].to_numpy()[start:stop]
        return timestamps

    def read_is_intervene(self, start=0, stop=None):
        # LeRobot 数据集没有干预标记。
        return None

    def read_subtasks(self):
        """该 episode 的子任务分段 [{"start", "text"}]；没有语言标注列时为空列表。"""
        if LANGUAGE_PERSISTENT not in self.meta.features:
            return []
        path = self.root / self.meta.get_data_file_path(self.episode_index)
        rows = pd.read_parquet(path, columns=["frame_index", LANGUAGE_PERSISTENT], filters=[("episode_index", "==", self.episode_index)])
        first = rows.sort_values("frame_index")[LANGUAGE_PERSISTENT].iloc[0]
        persistent = plain_value(first) if first is not None else []
        segments = subtask_segments(persistent, self.read_timestamps())
        return segments

    def image_change_indices(self, kind, camera):
        indices = np.arange(self.length)
        return indices

    def decode(self, key, index, is_depth):
        start = self.episode[f"videos/{key}/from_timestamp"]
        timestamp = start + index / self.meta.fps
        video_path = self.root / self.meta.get_video_file_path(self.episode_index, key)
        try:
            frames = decode_video_frames(video_path, [timestamp], VIDEO_TOLERANCE_S, VIDEO_BACKEND, return_uint8=True, is_depth=is_depth)
        except FrameTimestampError:
            # 开放 GOP 编码的视频在 episode 边界可能被官方读取丢帧（见 DEPTH_X265_PARAMS），改为从本 episode 起点顺序解码。
            frames = decode_from_episode_start(video_path, start, timestamp, is_depth)
        return frames

    def read_color(self, camera, index):
        """返回 RGB uint8 (H, W, 3)；视频为有损编码，与原始图像不逐像素一致。"""
        frames = self.decode(self.color_keys[camera], index, is_depth=False)
        rgb = frames[0].numpy().transpose(1, 2, 0)
        return rgb

    def read_depth(self, camera, index):
        """返回 uint16 毫米深度 (H, W)，按 feature 记录的量化参数还原。"""
        key = self.depth_key(camera)
        if key is None:
            raise ValueError(f"相机没有深度: {camera}")
        codes = self.decode(key, index, is_depth=True)
        encoder = encoder_config_from_video_info(self.meta.features[key]["info"])
        depth = dequantize_depth(codes, depth_min=encoder.depth_min, depth_max=encoder.depth_max, shift=encoder.shift,
                                 use_log=encoder.use_log, output_unit="mm", output_tensor=False)
        depth = depth.reshape(depth.shape[-2:])
        return depth


def decode_from_episode_start(video_path, start, timestamp, is_depth):
    """从 episode 起点（拼接前各 episode 独立编码，起点是关键帧）顺序解码到 timestamp，输出格式与官方 decode_video_frames 相同。"""
    import av
    import torch

    with av.open(str(video_path)) as container:
        stream = container.streams.video[0]
        container.seek(round(start / stream.time_base), backward=True, any_frame=False, stream=stream)
        for frame in container.decode(stream):
            if frame.pts is None:
                continue
            seconds = float(frame.pts * stream.time_base)
            if abs(seconds - timestamp) < VIDEO_TOLERANCE_S:
                if is_depth:
                    frames = torch.from_numpy(frame.to_ndarray(format="gray12le")).unsqueeze(0).unsqueeze(0)
                else:
                    frames = torch.from_numpy(frame.to_ndarray(format="rgb24")).permute(2, 0, 1).unsqueeze(0)
                return frames
            if seconds > timestamp:
                break
    raise FrameTimestampError(f"找不到 {timestamp:.4f}s 的帧: {video_path}")


def official_read_failures(root, meta=None):
    """用官方 decode_video_frames（PyAV）逐个读取每个 episode 每路视频的首帧，返回读取失败的 [episode, 视频 feature]。

    首帧是开放 GOP 拼接问题唯一会出现的位置（见 DEPTH_X265_PARAMS）；数据本身完整，平台读取会自动改为从 episode 起点解码。"""
    root = Path(root)
    meta = meta if meta is not None else load_metadata(root)
    failures = []
    for index in range(meta.total_episodes):
        episode = meta.episodes[index]
        for key in meta.video_keys:
            timestamp = episode[f"videos/{key}/from_timestamp"]
            is_depth = key in meta.depth_keys
            try:
                decode_video_frames(root / meta.get_video_file_path(index, key), [timestamp], VIDEO_TOLERANCE_S, VIDEO_BACKEND,
                                    return_uint8=True, is_depth=is_depth)
            except FrameTimestampError:
                failures.append([index, key])
    return failures


def list_episodes(root):
    """由 meta/episodes 汇总各 episode，不读取数据文件。"""
    meta = load_metadata(root)
    summaries = []
    for index in range(meta.total_episodes):
        episode = meta.episodes[index]
        summaries.append({
            "id": episode_id_of(index),
            "status": "ok",
            "length": int(episode["length"]),
            "task": "；".join(episode["tasks"]),
            "collection_time": None,
            "issues": [],
            "error": None,
        })
    return summaries


def create_demo_dataset(root, tasks, **kwargs):
    """demo 用的小型合成 v3.0（不依赖外部样本）：每个任务一个 episode，第 i 个 episode 30 + 10i 帧；
    64×64 RGB 与深度（observation.images.cam / cam_depth），2 维 state，数值逐帧不同。"""
    fps = int(kwargs.get("fps", 30))
    features = {
        "observation.state": vector_feature(["a", "b"]),
        "observation.images.cam": image_feature((64, 64, 3)),
        "observation.images.cam_depth": image_feature((64, 64, 1), is_depth=True),
    }
    dataset = create_dataset(root, fps, features)
    for episode, task in enumerate(tasks):
        for frame in range(30 + 10 * episode):
            depth = np.full((64, 64, 1), 500 + 20 * frame + 100 * episode, np.uint16)
            dataset.add_frame({
                "observation.state": np.array([episode, frame], np.float32),
                "observation.images.cam": np.full((64, 64, 3), (8 * frame + 60 * episode) % 256, np.uint8),
                "observation.images.cam_depth": depth,
                "task": task,
            })
        save_episode(dataset)
    dataset.finalize()
    remove_empty_image_dir(root)
    root = Path(root)
    return root


REPO_ROOT = Path(__file__).resolve().parents[1]
DEMO_OUTPUT = REPO_ROOT / "output" / "demo" / "lerobot_helper"


def demo_lerobot_roundtrip():
    root = DEMO_OUTPUT / "roundtrip"
    shutil.rmtree(root, ignore_errors=True)
    root.parent.mkdir(parents=True, exist_ok=True)
    features = {
        "observation.state": vector_feature(["a", "b"]),
        "observation.images.cam": image_feature((64, 64, 3)),
        "observation.images.cam_depth": image_feature((64, 64, 1), is_depth=True),
    }
    dataset = create_dataset(root, 30, features)
    depths = []
    for index in range(10):
        depth = np.full((64, 64), 400 + 300 * index, np.uint16)
        depth[0, 0] = 0
        depths.append(depth)
        color = np.full((64, 64, 3), 20 * index, np.uint8)
        dataset.add_frame({
            "observation.state": np.array([index, -index], np.float32),
            "observation.images.cam": color,
            "observation.images.cam_depth": depth[..., None],
            "task": "demo",
        })
    save_episode(dataset)
    dataset.finalize()

    reader = open_dataset(root)
    errors = []
    for index in range(len(reader)):
        item = reader[index]
        errors.append(int(np.abs(to_depth_mm(item["observation.images.cam_depth"]).astype(int) - depths[index]).max()))
    assert len(reader) == 10 and max(errors) == 0, errors
    logger.info("LeRobot roundtrip: {} frames, depth max error {} mm (values 0 and 400–3100 mm)", len(reader), max(errors))

    with LeRobotEpisode(root, 0) as episode:
        assert episode.cameras == ["cam"] and episode.series_names == ["observation.state"]
        assert np.array_equal(episode.read_depth("cam", 7), depths[7])
        assert episode.read_series("observation.state", 2, 4).tolist() == [[2.0, -2.0], [3.0, -3.0]]
        assert episode.read_color("cam", 3).shape == (64, 64, 3) and episode.check() == []
        info = episode.info()
    logger.info("LeRobotEpisode: {} frames, cameras {}, timestamps {}", info["length"], info["cameras"], info["timestamp_range"])


def main():
    demo_lerobot_roundtrip()


if __name__ == "__main__":
    main()
