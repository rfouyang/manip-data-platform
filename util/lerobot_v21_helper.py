"""LeRobot v2.1 数据集的读取元数据、由 v3.0 写出 v2.1 的低层操作，以及调用官方脚本函数的 v2.1 → v3.0 转换。

v2.1 布局（以 lerobot 0.3.3，即最后一个 v2.1 版本为准）：
- data/chunk-{episode_chunk:03d}/episode_{episode_index:06d}.parquet：每个 episode 一个 parquet，列为非视频 feature；
- videos/chunk-{episode_chunk:03d}/{video_key}/episode_{episode_index:06d}.mp4：每个 episode、每路视频一个 mp4，时间从 0 开始；
- meta/info.json、episodes.jsonl、tasks.jsonl、episodes_stats.jsonl（v2.1 不写 stats.json，由逐 episode 统计汇总）。
"""
import json
import shutil
from pathlib import Path

import av
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from loguru import logger


V21 = "v2.1"
CHUNK_SIZE = 1000
DATA_PATH = "data/chunk-{episode_chunk:03d}/episode_{episode_index:06d}.parquet"
VIDEO_PATH = "videos/chunk-{episode_chunk:03d}/{video_key}/episode_{episode_index:06d}.mp4"
META_FILES = ("info.json", "episodes.jsonl", "tasks.jsonl", "episodes_stats.jsonl")
# v2.1 逐 episode 统计只有这 5 项；v3.0 另有 q01–q99 分位数，写 v2.1 时不保留。
STAT_NAMES = ("min", "max", "mean", "std", "count")


def read_jsonl(path):
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    rows = [json.loads(line) for line in lines if line.strip()]
    return rows


# 与官方 write_json / jsonlines 的序列化一致；换行固定为 LF（官方以文本模式写，Windows 上会变成 CRLF，Hub 上的数据集为 LF）。
def write_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=4), encoding="utf-8", newline="\n")


def write_jsonl(path, rows):
    text = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    Path(path).write_text(text, encoding="utf-8", newline="\n")


class V21Metadata:
    """只读的 v2.1 元数据，提供 LeRobotEpisode 使用的与 LeRobotDatasetMetadata（v3.0）相同的属性和方法。"""

    def __init__(self, root):
        self.root = Path(root)
        self.info = json.loads((self.root / "meta" / "info.json").read_text(encoding="utf-8"))
        if self.info["codebase_version"] != V21:
            raise ValueError(f"不是 LeRobot v2.1 数据集: {self.info['codebase_version']}")
        self.features = self.info["features"]
        self.fps = self.info["fps"]
        self.robot_type = self.info.get("robot_type")
        self.video_keys = [key for key, feature in self.features.items() if feature["dtype"] == "video"]
        self.depth_keys = [key for key in self.video_keys if self.features[key].get("info", {}).get("video.is_depth_map")]
        rows = sorted(read_jsonl(self.root / "meta" / "episodes.jsonl"), key=lambda row: row["episode_index"])
        self.episodes = []
        for row in rows:
            episode = {"episode_index": row["episode_index"], "tasks": row["tasks"], "length": row["length"]}
            # 每个 episode 的视频单独成文件，时间从 0 开始。
            for key in self.video_keys:
                episode[f"videos/{key}/from_timestamp"] = 0.0
            self.episodes.append(episode)
        if [episode["episode_index"] for episode in self.episodes] != list(range(len(self.episodes))):
            raise ValueError(f"episodes.jsonl 中的 episode_index 不连续: {self.root}")
        self.tasks = {row["task_index"]: row["task"] for row in read_jsonl(self.root / "meta" / "tasks.jsonl")}
        self.total_episodes = len(self.episodes)
        self.total_frames = sum(episode["length"] for episode in self.episodes)

    def get_data_file_path(self, episode_index):
        path = Path(self.info["data_path"].format(episode_chunk=episode_index // self.info["chunks_size"], episode_index=episode_index))
        return path

    def get_video_file_path(self, episode_index, video_key):
        path = Path(self.info["video_path"].format(episode_chunk=episode_index // self.info["chunks_size"], video_key=video_key,
                                                  episode_index=episode_index))
        return path


def v21_features(features, dropped):
    """v3.0 features → v2.1 features：官方 convert_info 的逆操作。

    官方 v2.1 → v3.0 只给非视频 feature 加 fps，其余字段（含视频 info、names 缺省等）原样保留；
    这里去掉 dropped 与该 fps，其余原样写回，保证 v2.1 → v3.0 → v2.1 的 info.json 与原始一致。"""
    result = {}
    for key, feature in features.items():
        if key in dropped:
            continue
        item = {name: (list(value) if name == "shape" else value) for name, value in feature.items()}
        if feature["dtype"] != "video":
            item.pop("fps", None)
        result[key] = item
    return result


def v21_info(meta, features, total_episodes, total_frames):
    """字段与顺序同 lerobot 0.3.3 create_empty_dataset_info 及 save_episode 更新后的结果。"""
    video_count = sum(1 for feature in features.values() if feature["dtype"] == "video")
    info = {
        "codebase_version": V21,
        "robot_type": meta.robot_type,
        "total_episodes": total_episodes,
        "total_frames": total_frames,
        "total_tasks": len(meta.tasks),
        "total_videos": total_episodes * video_count,
        "total_chunks": (total_episodes + CHUNK_SIZE - 1) // CHUNK_SIZE,
        "chunks_size": CHUNK_SIZE,
        "fps": meta.fps,
        "splits": {"train": f"0:{total_episodes}"},
        "data_path": DATA_PATH,
        "video_path": VIDEO_PATH if video_count else None,
        "features": features,
    }
    return info


def hf_features(features):
    """datasets 3.x（lerobot 0.3.3 使用）的 parquet 特征元数据。datasets 4.x 写的是 List，旧版不认识，这里按 Sequence 写。"""
    result = {}
    for key, feature in features.items():
        if feature["dtype"] in ("video", "image"):
            continue
        if list(feature["shape"]) == [1]:
            result[key] = {"dtype": feature["dtype"], "_type": "Value"}
        elif len(feature["shape"]) == 1:
            result[key] = {"feature": {"dtype": feature["dtype"], "_type": "Value"}, "length": feature["shape"][0], "_type": "Sequence"}
        else:
            raise ValueError(f"暂不支持多维数值 feature: {key} {feature['shape']}")
    return result


def arrow_type(dtype):
    # datasets 的 Value("string") 对应 arrow string；数值类型与 numpy 同名。
    result = pa.string() if dtype == "string" else pa.from_numpy_dtype(np.dtype(dtype))
    return result


def write_episode_table(table, path, features):
    """table 为该 episode 的 v3.0 数据行（已按 frame_index 排序）；只写 v2.1 features 中的非视频列。"""
    columns = hf_features(features)
    # 与 datasets 3.x 写出的类型一致：定长向量为 fixed_size_list（官方 v2.1 → v3 脚本产出的 v3 中是变长 list）。
    fields = []
    for key, column in columns.items():
        if column["_type"] == "Value":
            fields.append(pa.field(key, arrow_type(column["dtype"])))
        else:
            fields.append(pa.field(key, pa.list_(arrow_type(column["feature"]["dtype"]), column["length"])))
    table = table.select(list(columns)).cast(pa.schema(fields))
    table = table.replace_schema_metadata({"huggingface": json.dumps({"info": {"features": columns}})})
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path)


def avc_parameter_sets(extradata):
    """解析 H.264 avcC 头，返回 (NAL 长度字段字节数, 其中的 SPS/PPS 集合)。"""
    length_size = (extradata[4] & 0x03) + 1
    sets = set()
    offset = 6
    for count_mask in (0x1F, 0xFF):
        count = extradata[offset - 1] & count_mask
        for _ in range(count):
            size = int.from_bytes(extradata[offset: offset + 2], "big")
            sets.add(bytes(extradata[offset + 2: offset + 2 + size]))
            offset += 2 + size
        offset += 1
    result = (length_size, sets)
    return result


def strip_parameter_sets(data, length_size, sets):
    """去掉关键帧中与 avcC 头相同的 SPS/PPS NAL。

    官方 v2.1 → v3.0 用 FFmpeg concat 合并视频时，对 H.264 自动套用 h264_mp4toannexb，把头中的 SPS/PPS 复制到每个关键帧前；
    拆回 v2.1 时去掉这些副本，恢复原始逐帧数据（v2.1 编码写出的 mp4 中 SPS/PPS 只在头里）。"""
    parts = []
    offset = 0
    while offset < len(data):
        size = int.from_bytes(data[offset: offset + length_size], "big")
        unit = data[offset + length_size: offset + length_size + size]
        if not (unit and unit[0] & 0x1F in (7, 8) and unit in sets):
            parts.append(data[offset: offset + length_size + size])
        offset += length_size + size
    stripped = b"".join(parts)
    return stripped


def split_video(source, target, start, stop, fps, length, **kwargs):
    """从 v3.0 合并视频中按 episode 时间范围取出 packet 流拷贝（不重新编码），时间戳平移到 0。

    v3.0 的合并视频由各 episode 独立编码的视频流拷贝拼接而成，每个 episode 起点是关键帧，因此拆分可逐帧精确。
    起点不是关键帧或 packet 数不等于帧数时拒绝，不做近似。
    与 v2.1 编码写出的文件一样不加 faststart（moov 在 mdat 之后）；容器中的 encoder 标签为本机 FFmpeg 版本，原值在 v3.0 中不保留。
    strip_parameter_sets（默认 True）：H.264 去掉官方合并时复制到关键帧的 SPS/PPS，用于还原 v2.1；v3.0 内部重组视频时传 False，packet 原样拷贝。"""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    half_frame = 0.5 / fps
    first_pts = None
    count = 0
    with av.open(str(source)) as source_file, av.open(str(target), "w") as target_file:
        source_stream = source_file.streams.video[0]
        target_stream = target_file.add_stream_from_template(template=source_stream, opaque=True)
        target_stream.time_base = source_stream.time_base
        extradata = source_stream.codec_context.extradata
        strip = kwargs.get("strip_parameter_sets", True)
        avc = avc_parameter_sets(extradata) if strip and source_stream.codec_context.name == "h264" and extradata and extradata[0] == 1 else None
        for packet in source_file.demux(source_stream):
            if packet.dts is None:
                continue
            seconds = float(packet.pts * source_stream.time_base)
            if not start - half_frame <= seconds < stop - half_frame:
                continue
            if first_pts is None:
                if not packet.is_keyframe:
                    raise ValueError(f"episode 起点不是关键帧，无法无损拆分: {source} @ {start:.3f}s")
                first_pts = packet.pts
            if avc is not None and packet.is_keyframe:
                stripped = av.Packet(strip_parameter_sets(bytes(packet), *avc))
                stripped.pts, stripped.dts, stripped.duration, stripped.is_keyframe = packet.pts, packet.dts, packet.duration, True
                stripped.time_base = packet.time_base
                packet = stripped
            packet.pts -= first_pts
            packet.dts -= first_pts
            packet.stream = target_stream
            target_file.mux(packet)
            count += 1
    if count != length:
        raise ValueError(f"拆分得到 {count} 帧，应为 {length}: {source} [{start:.3f}, {stop:.3f})")
    return count


def load_v3_episode_stats(root, features):
    """读取 v3.0 meta/episodes 中的逐 episode 统计，按 v2.1 结构返回 {episode_index: {feature: {stat: list}}}。

    字符串 feature（如由任务描述生成的 tag）官方不计算统计，两个版本中都没有，跳过。"""
    paths = sorted((Path(root) / "meta" / "episodes").rglob("*.parquet"))
    keys = [key for key, feature in features.items() if feature["dtype"] != "string"]
    columns = ["episode_index"] + [f"stats/{key}/{name}" for key in keys for name in STAT_NAMES]
    rows = pa.concat_tables([pq.read_table(path, columns=columns) for path in paths]).to_pylist()
    stats = {}
    for row in rows:
        stats[row["episode_index"]] = {key: {name: np.asarray(row[f"stats/{key}/{name}"]).tolist() for name in STAT_NAMES} for key in keys}
    return stats


def video_packets(path):
    """视频流的逐帧压缩数据与时间戳，用于判断两个 mp4 的视频内容是否逐字节相同（不看容器封装）。"""
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        packets = [(packet.pts, packet.dts, packet.is_keyframe, bytes(packet)) for packet in container.demux(stream) if packet.dts is not None]
        header = (str(stream.time_base), stream.codec_context.name, bytes(stream.codec_context.extradata or b""))
    result = {"header": header, "packets": packets}
    return result


def compare_v21(original, result):
    """比较两个 v2.1 数据集：meta 文件逐字节；parquet 数据表、schema 与元数据；视频逐帧压缩数据、时间戳与编码头。

    返回每类不一致的文件清单；视频另记容器文件字节是否相同（encoder 标签等封装信息在 v3.0 中不保留）。"""
    original, result = Path(original), Path(result)
    report = {"meta_differ": [], "data_differ": [], "schema_metadata_differ": [], "video_differ": [], "video_container_differ": 0,
              "missing": [], "extra": []}
    names_a = {path.relative_to(original).as_posix() for path in original.rglob("*") if path.is_file()}
    names_b = {path.relative_to(result).as_posix() for path in result.rglob("*") if path.is_file()}
    report["missing"] = sorted(names_a - names_b)
    report["extra"] = sorted(names_b - names_a)
    for name in sorted(names_a & names_b):
        a, b = original / name, result / name
        if name.startswith("meta/"):
            if a.read_bytes() != b.read_bytes():
                report["meta_differ"].append(name)
        elif name.endswith(".parquet"):
            table_a, table_b = pq.read_table(a), pq.read_table(b)
            if not table_a.equals(table_b) or not table_a.schema.equals(table_b.schema):
                report["data_differ"].append(name)
            elif table_a.schema.metadata != table_b.schema.metadata:
                report["schema_metadata_differ"].append(name)
        elif name.endswith(".mp4"):
            if video_packets(a) != video_packets(b):
                report["video_differ"].append(name)
            elif a.read_bytes() != b.read_bytes():
                report["video_container_differ"] += 1
    report["files"] = len(names_a)
    return report


def convert_v21_to_v3(source, target):
    """用 lerobot 0.6.1 官方脚本 convert_dataset_v21_to_v30 中的各步函数，从 source 读取、写入 target。

    不调用其 convert_dataset：那会原地改写（把源目录改名为 _old），违反源数据只读。"""
    from lerobot.scripts.convert_dataset_v21_to_v30 import (
        DEFAULT_DATA_FILE_SIZE_IN_MB,
        DEFAULT_VIDEO_FILE_SIZE_IN_MB,
        convert_data,
        convert_episodes_metadata,
        convert_info,
        convert_tasks,
        convert_videos,
        validate_local_dataset_version,
    )

    source, target = Path(source), Path(target)
    validate_local_dataset_version(source)
    convert_info(source, target, DEFAULT_DATA_FILE_SIZE_IN_MB, DEFAULT_VIDEO_FILE_SIZE_IN_MB)
    convert_tasks(source, target)
    episodes_metadata = convert_data(source, target, DEFAULT_DATA_FILE_SIZE_IN_MB)
    videos_metadata = convert_videos(source, target, DEFAULT_VIDEO_FILE_SIZE_IN_MB)
    convert_episodes_metadata(source, target, episodes_metadata, videos_metadata)


REPO_ROOT = Path(__file__).resolve().parents[1]
DEMO_OUTPUT = REPO_ROOT / "output" / "demo" / "lerobot_v21_helper"


def demo_v21_metadata():
    """读取平台转换出的小样本 v2.1（先运行 data_converter 的 demo 生成）。"""
    root = REPO_ROOT / "output" / "demo" / "data_converter" / "converted" / "sample_2_episodes_lerobot_v21"
    if not root.exists():
        logger.info("Skip: {} not found, run component/conversion/data_converter.py first", root)
        return
    meta = V21Metadata(root)
    logger.info("v2.1 {}: {} episodes, {} frames, fps={}, videos={}, tasks={}", root.name, meta.total_episodes, meta.total_frames, meta.fps,
                meta.video_keys, meta.tasks)
    logger.info("Episode 0 data={}, video={}", meta.get_data_file_path(0), meta.get_video_file_path(0, meta.video_keys[0]))


def demo_convert_v21_to_v3():
    source = REPO_ROOT / "output" / "demo" / "data_converter" / "converted" / "sample_2_episodes_lerobot_v21"
    if not source.exists():
        logger.info("Skip: {} not found", source)
        return
    target = DEMO_OUTPUT / "v21_to_v3"
    shutil.rmtree(target, ignore_errors=True)
    convert_v21_to_v3(source, target)
    logger.info("Converted with official functions -> {}: {}", target, sorted(path.name for path in target.iterdir()))


def main():
    demo_v21_metadata()
    demo_convert_v21_to_v3()


if __name__ == "__main__":
    main()
