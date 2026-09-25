"""LeRobot 版本转换：v3.0 → v2.1（按 episode 写出数据与流拷贝拆出的视频）与 v2.1 → v3.0（官方转换函数），以及两者的输出验证。

H5 → v2.1 经临时 v3.0 后也调用这里的 write_v21 与 validate_v21_output。输出写入新目录，源数据只读。"""
import shutil
from datetime import datetime
from importlib.metadata import version
from pathlib import Path

import pyarrow.parquet as pq
from loguru import logger

from component.common.dataset_access import LEROBOT_V3, LEROBOT_V21
from component.common.output_publish import publish, staged_output
from component.common.validation import Checks, require_passed
from util.lerobot_helper import (
    LANGUAGE_COLUMNS,
    LeRobotEpisode,
    columns_equal,
    episode_table,
    load_metadata,
    local_repo_id,
    open_dataset,
)
from util.lerobot_v21_helper import (
    CHUNK_SIZE,
    DATA_PATH,
    META_FILES,
    STAT_NAMES,
    VIDEO_PATH,
    V21Metadata,
    convert_v21_to_v3,
    load_v3_episode_stats,
    read_jsonl,
    split_video,
    v21_features,
    v21_info,
    write_episode_table,
    write_json,
    write_jsonl,
)

V3_TO_V21_JOB = "conversion_lerobot_v3_to_v21"
V21_TO_V3_JOB = "conversion_lerobot_v21_to_v3"
LEROBOT_VERSION = version("lerobot")
REPO_ROOT = Path(__file__).resolve().parents[2]


def dropped_features(meta, target):
    """转为 v2.1 时去掉的 feature：深度（lerobot 0.3.3 要求图像统计为 3 通道，实测带深度无法加载；用户确认，2026-09-23），
    lerobot 0.6 的语言标注列（v2.1 没有 language 类型），以及以图像帧存在 parquet 中的 image feature（只实现了视频 feature 的拆分写出）。"""
    dropped = []
    if target == LEROBOT_V21:
        other = [key for key, feature in meta.features.items() if feature["dtype"] in ("language", "image")]
        dropped = list(dict.fromkeys([*meta.depth_keys, *other]))
    return dropped


def version_notes(target, dropped, meta):
    if target == LEROBOT_V21:
        notes = [
            "作为官方 v2.1 → v3.0 的逆操作：由官方转换得到的 v3.0 转回后，meta、数据表与逐帧视频数据与原始 v2.1 一致。",
            "每个 episode 写为单独的 parquet 与 mp4（v2.1 布局，以 lerobot 0.3.3 为准），meta 换行为 LF。",
            "视频按 episode 从合并视频中流拷贝拆出，不重新编码；H.264 去掉官方合并时复制到关键帧的 SPS/PPS。",
            "数值列、帧序号、时间戳、全局 index 与任务原样保留；feature 定义原样写回，只去掉官方转换时加的 fps。",
            "逐 episode 统计只保留 min/max/mean/std/count；v2.1 没有 q01–q99 分位数。",
            "mp4 容器中的 encoder 标签与 btrt 最大码率由本机 FFmpeg 写入，与原始文件可能不同（不影响视频数据）。",
        ]
        language = [key for key in dropped if key in LANGUAGE_COLUMNS]
        depth = [key for key in dropped if key in meta.depth_keys]
        image = [key for key in dropped if key not in language and key not in depth]
        if depth:
            notes.append(f"v2.1 不支持深度 feature（官方 v2.1 代码要求图像为 3 通道），不写入：{'、'.join(depth)}。")
        if language:
            notes.append(f"v2.1 没有语言标注类型，不写入：{'、'.join(language)}（子任务会丢失；需要时先在“任务标注”页转为逐帧任务再转换）。")
        if image:
            notes.append(f"以图像帧存储的 image feature 暂不支持写出为 v2.1，不写入：{'、'.join(image)}。")
    else:
        notes = [
            f"使用 lerobot {LEROBOT_VERSION} 官方 convert_dataset_v21_to_v30 的各步函数，从源目录读取、写入新目录，不修改源数据。",
            "视频按 episode 流拷贝合并，不重新编码。",
            "统计由 v2.1 逐 episode 统计汇总，不含分位数（与官方脚本一致）。",
            "整体转换全部 episode，不支持只转部分；转换过程中只能在各阶段之间响应取消。",
        ]
    return notes


def write_v21(temp, params, meta, features, reporter):
    """逐 episode 从 v3.0 写出 v2.1：数据行、按 episode 拆分的视频，以及 meta 下 4 个文件。"""
    source = Path(params["dataset_path"])
    count = params["episode_count"]
    video_keys = [key for key, feature in features.items() if feature["dtype"] == "video"]
    stats = load_v3_episode_stats(source, features)
    cache = {}
    episodes = []
    episodes_stats = []
    for index in range(count):
        reporter.check_cancel()
        episode = meta.episodes[index]
        length = int(episode["length"])
        chunk = index // CHUNK_SIZE
        table = episode_table(source, meta, index, cache)
        write_episode_table(table, temp / DATA_PATH.format(episode_chunk=chunk, episode_index=index), features)
        for key in video_keys:
            target = temp / VIDEO_PATH.format(episode_chunk=chunk, video_key=key, episode_index=index)
            split_video(source / meta.get_video_file_path(index, key), target, episode[f"videos/{key}/from_timestamp"],
                        episode[f"videos/{key}/to_timestamp"], meta.fps, length)
        episodes.append({"episode_index": index, "tasks": list(episode["tasks"]), "length": length})
        episodes_stats.append({"episode_index": index, "stats": stats[index]})
        reporter.progress(index + 1, count, f"episode_{index:06d}: {length} 帧")
    (temp / "meta").mkdir(parents=True)
    info = v21_info(meta, features, count, sum(item["length"] for item in episodes))
    write_json(temp / "meta" / "info.json", info)
    tasks = [{"task_index": int(row["task_index"]), "task": task} for task, row in meta.tasks.iterrows()]
    write_jsonl(temp / "meta" / "tasks.jsonl", sorted(tasks, key=lambda item: item["task_index"]))
    write_jsonl(temp / "meta" / "episodes.jsonl", episodes)
    write_jsonl(temp / "meta" / "episodes_stats.jsonl", episodes_stats)
    written = {"episodes": episodes}
    return written


def compare_episodes(checks, output, output_meta, source, source_meta, count):
    """逐 episode 比较两个 LeRobot 数据集：全部数值列逐值相等；每路 RGB 与深度抽查首、中、末帧逐像素相等（视频为流拷贝）。"""
    cache = {}
    columns = [key for key, feature in output_meta.features.items() if feature["dtype"] not in ("video", "image")]
    for index in range(count):
        label = f"episode_{index:06d}"
        if isinstance(output_meta, V21Metadata):
            actual = pq.read_table(Path(output) / output_meta.get_data_file_path(index)).to_pandas()
            expected = episode_table(source, source_meta, index, cache).to_pandas()
        else:
            actual = episode_table(output, output_meta, index, cache).to_pandas()
            expected = pq.read_table(Path(source) / source_meta.get_data_file_path(index)).to_pandas()
        for column in columns:
            equal = len(actual) == len(expected) and columns_equal(actual[column].to_numpy(), expected[column].to_numpy())
            checks.add(f"{label} {column} 与源一致", equal)
        with LeRobotEpisode(output, index, output_meta) as episode, LeRobotEpisode(source, index, source_meta) as reference:
            checks.add(f"{label} 长度一致", episode.length == reference.length == len(actual), f"{episode.length} / {reference.length}")
            issues = episode.check()
            checks.add(f"{label} 结构自检", not issues, str(issues))
            checks.sample_frames(label, episode, reference)


def validate_v21_output(temp, params, meta, features, written):
    """重新读取写出的 v2.1，与 v3.0 源核对布局、元数据、统计、数值与视频帧。"""
    checks = Checks()
    check = checks.add
    checks.standard_dirs(temp)
    meta_files = sorted(path.name for path in (temp / "meta").iterdir())
    check("meta 只含 v2.1 文件", meta_files == sorted(META_FILES), str(meta_files))
    output_meta = V21Metadata(temp)
    count = params["episode_count"]
    check("episode 数", output_meta.total_episodes == output_meta.info["total_episodes"] == count, f"{output_meta.total_episodes} / {count}")
    expected_frames = sum(item["length"] for item in written["episodes"])
    check("总帧数", output_meta.total_frames == output_meta.info["total_frames"] == expected_frames, str(output_meta.total_frames))
    check("fps", output_meta.fps == meta.fps, str(output_meta.fps))
    check("feature 列表", list(output_meta.features) == list(features), str(list(output_meta.features)))
    stats = {row["episode_index"]: row["stats"] for row in read_jsonl(temp / "meta" / "episodes_stats.jsonl")}
    counts = [stats[item["episode_index"]]["frame_index"]["count"] == [item["length"]] for item in written["episodes"]]
    check("逐 episode 统计的 count = 帧数", all(counts))
    check("统计只含 v2.1 的 5 项", all(sorted(values) == sorted(STAT_NAMES) for row in stats.values() for values in row.values()))
    compare_episodes(checks, temp, output_meta, Path(params["dataset_path"]), meta, count)
    summary = checks.summary()
    return summary


def validate_v3_from_v21(temp, params):
    """用官方 LeRobotDataset 打开转换结果，并与 v2.1 源核对计数、数值与视频帧。"""
    checks = Checks()
    check = checks.add
    source = Path(params["dataset_path"])
    source_meta = V21Metadata(source)
    checks.standard_dirs(temp)
    reader = open_dataset(temp, repo_id=local_repo_id(params))
    check("官方 LeRobotDataset 可打开且为 v3.0", reader.meta.info.codebase_version == "v3.0", reader.meta.info.codebase_version)
    check("episode 数", reader.meta.total_episodes == source_meta.total_episodes, f"{reader.meta.total_episodes} / {source_meta.total_episodes}")
    check("总帧数", reader.meta.total_frames == len(reader) == source_meta.total_frames, f"{reader.meta.total_frames} / {source_meta.total_frames}")
    check("fps", reader.meta.fps == source_meta.fps, str(reader.meta.fps))
    compare_episodes(checks, temp, load_metadata(temp), source, source_meta, source_meta.total_episodes)
    summary = checks.summary()
    return summary


def version_report(params, source_format, target, meta):
    report = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "source_dataset": params["dataset_path"],
        "source_format": source_format,
        "target": target,
        "lerobot_version": LEROBOT_VERSION,
        "repo_id": local_repo_id(params),
        "notes": version_notes(target, params.get("dropped_features", []), meta),
    }
    return report


def v3_to_v21_job(params, reporter):
    """工作进程中运行：v3.0 → v2.1，写入临时目录 → 验证 → 重命名发布 → 登记。失败或取消时删除临时目录。"""
    meta = load_metadata(params["dataset_path"])
    features = v21_features(meta.features, set(params["dropped_features"]))
    with staged_output(params["output_path"], reporter) as (temp, output):
        written = write_v21(temp, params, meta, features, reporter)
        reporter.set_status("validating", "验证输出")
        validation = validate_v21_output(temp, params, meta, features, written)
        require_passed(validation)
        publish(temp, output, reporter)
    dataset = reporter.register_dataset(output.name, output, LEROBOT_V21)
    result = {
        "output_path": str(output),
        "registered_dataset_id": dataset["id"],
        "episode_count": params["episode_count"],
        "frames": sum(item["length"] for item in written["episodes"]),
        "dropped_features": params["dropped_features"],
        "validation": validation,
        "report": version_report(params, LEROBOT_V3, LEROBOT_V21, meta),
    }
    return result


def v21_to_v3_job(params, reporter):
    """工作进程中运行：v2.1 → v3.0（官方转换函数），写入临时目录 → 验证 → 重命名发布 → 登记。

    官方函数内部整体处理全部 episode，只能在各阶段之间响应取消。"""
    with staged_output(params["output_path"], reporter) as (temp, output):
        reporter.progress(0, 2, "官方函数转换中")
        convert_v21_to_v3(params["dataset_path"], temp)
        reporter.check_cancel()
        reporter.progress(1, 2, "转换完成，验证中")
        reporter.set_status("validating", "验证输出")
        validation = validate_v3_from_v21(temp, params)
        require_passed(validation)
        publish(temp, output, reporter)
    dataset = reporter.register_dataset(output.name, output, LEROBOT_V3)
    meta = load_metadata(output)
    result = {
        "output_path": str(output),
        "registered_dataset_id": dataset["id"],
        "episode_count": meta.total_episodes,
        "frames": meta.total_frames,
        "dropped_features": [],
        "validation": validation,
        "report": version_report(params, LEROBOT_V21, LEROBOT_V3, meta),
    }
    return result


DEMO_OUTPUT = REPO_ROOT / "output" / "demo" / "version_converter"


def demo_version_roundtrip():
    """合成的 3 个 episode 小样本（含深度）v3.0 → v2.1 → v3.0，结果写到独立目录。"""
    from component.common.task_manager import DemoReporter
    from util.lerobot_helper import create_demo_dataset

    shutil.rmtree(DEMO_OUTPUT, ignore_errors=True)
    source = DEMO_OUTPUT / "sample"
    v21 = DEMO_OUTPUT / "sample_lerobot_v21"
    v3 = DEMO_OUTPUT / "sample_lerobot_v21_lerobot_v3"
    create_demo_dataset(source, ["pick up the red cup", "pick up the green cup", "wave hand"])
    meta = load_metadata(source)
    params = {"dataset_path": str(source), "output_path": str(v21), "episode_count": meta.total_episodes,
              "dropped_features": dropped_features(meta, LEROBOT_V21)}
    result = v3_to_v21_job(params, DemoReporter())
    logger.info("v3 -> v2.1: {} episodes, {} frames, dropped {}, checks {} passed={}", result["episode_count"], result["frames"],
                result["dropped_features"], result["validation"]["check_count"], result["validation"]["passed"])
    result = v21_to_v3_job({"dataset_path": str(v21), "output_path": str(v3)}, DemoReporter())
    logger.info("v2.1 -> v3: {} episodes, {} frames, checks {} passed={}", result["episode_count"], result["frames"],
                result["validation"]["check_count"], result["validation"]["passed"])
    for path in (source, v21, v3):
        size = sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
        logger.info("  {} {:.1f} MB: {}", path.name, size / 1e6, sorted(item.name for item in path.iterdir()))


def main():
    demo_version_roundtrip()


if __name__ == "__main__":
    main()
