"""拆分：按条件从一个 LeRobot v3.0 数据集选出完整 episode，存为一个子数据集。

数据、元数据与统计用官方 split_dataset 的函数；视频按 episode 流拷贝，不重新编码（用户确认，2026-09-23）。输出写入新目录，源数据只读。"""
import re
import shutil
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from lerobot.utils.constants import DEFAULT_FEATURES
from loguru import logger

from component.common.dataset_access import LEROBOT_V3
from component.common.output_publish import output_fields, publish, remove_temporary, require_ready, resolve_output, staged_output, temporary_path
from component.common.validation import Checks, require_passed
from util.lerobot_helper import column_values, columns_equal, data_files, episode_table, load_metadata, official_read_failures, open_dataset


REPO_ROOT = Path(__file__).resolve().parents[2]

SPLIT_JOB = "split_subset"
SELECTION_MODES = ("episodes", "tasks", "values")


def parse_episode_spec(spec):
    """解析 "0-99, 120, 150-160" 形式的 episode 编号与范围（范围含两端），返回去重排序的列表。"""
    selected = set()
    for token in re.split(r"[,，\s]+", spec.strip()):
        if not token:
            continue
        match = re.fullmatch(r"(\d+)(?:-(\d+))?", token)
        if match is None:
            raise ValueError(f"无法解析 episode 编号: {token}（格式如 0-99, 120）")
        start = int(match.group(1))
        stop = int(match.group(2)) if match.group(2) else start
        if stop < start:
            raise ValueError(f"范围起点大于终点: {token}")
        selected.update(range(start, stop + 1))
    episodes = sorted(selected)
    return episodes


def string_columns(meta):
    """可用于筛选的逐帧字符串列（如由任务描述生成的 tag）。"""
    columns = [key for key, feature in meta.features.items() if feature["dtype"] == "string"]
    return columns


def episode_values(root, column):
    """每个 episode 在该列出现的值集合（通常每个 episode 只有一个值）。"""
    values = {}
    for path in data_files(root):
        frame = pd.read_parquet(path, columns=["episode_index", column])
        for episode, group in frame.groupby("episode_index"):
            values.setdefault(int(episode), set()).update(group[column].unique().tolist())
    return values


class SubsetSelection:
    """把选择条件解析为 episode 列表：指定编号、按任务（任务描述）、按字符串列的取值。整 episode 选入。"""

    def __init__(self, meta, root):
        self.meta = meta
        self.root = root

    def select(self, mode, **kwargs):
        if mode not in SELECTION_MODES:
            raise ValueError(f"未知的选择方式: {mode}")
        notes = []
        if mode == "episodes":
            episodes = parse_episode_spec(kwargs.get("episodes") or "")
            invalid = [index for index in episodes if index >= self.meta.total_episodes]
            if invalid:
                raise ValueError(f"episode 编号超出范围（共 {self.meta.total_episodes} 个，编号 0–{self.meta.total_episodes - 1}）: {invalid[:10]}")
        elif mode == "tasks":
            chosen = {int(index) for index in kwargs.get("tasks") or []}
            names = {int(row["task_index"]): task for task, row in self.meta.tasks.iterrows()}
            unknown = chosen - set(names)
            if unknown:
                raise ValueError(f"任务编号不存在: {sorted(unknown)}")
            texts = {names[index] for index in chosen}
            episodes = [index for index in range(self.meta.total_episodes) if texts & set(self.meta.episodes[index]["tasks"])]
            mixed = [index for index in episodes if not set(self.meta.episodes[index]["tasks"]) <= texts]
            if mixed:
                notes.append(f"{len(mixed)} 个 episode 同时含有未选任务，按整 episode 选入: {mixed[:10]}")
        else:
            column = kwargs.get("column")
            if column not in string_columns(self.meta):
                raise ValueError(f"数据集中没有可筛选的字符串列: {column}")
            chosen = set(kwargs.get("values") or [])
            per_episode = episode_values(self.root, column)
            episodes = [index for index in range(self.meta.total_episodes) if per_episode.get(index, set()) & chosen]
            mixed = [index for index in episodes if not per_episode[index] <= chosen]
            if mixed:
                notes.append(f"{len(mixed)} 个 episode 的 {column} 含有多个值且部分未选，按整 episode 选入: {mixed[:10]}")
        result = {"episodes": episodes, "notes": notes}
        return result


def subset_options(meta, root):
    """UI 选择项：任务列表与各字符串列的取值，均带 episode 数与帧数。"""
    lengths = [int(meta.episodes[index]["length"]) for index in range(meta.total_episodes)]
    tasks = []
    for task, row in meta.tasks.iterrows():
        members = [index for index in range(meta.total_episodes) if task in meta.episodes[index]["tasks"]]
        tasks.append({"task_index": int(row["task_index"]), "task": task, "episodes": len(members), "frames": sum(lengths[index] for index in members)})
    columns = {}
    for column in string_columns(meta):
        counts = {}
        for index, values in episode_values(root, column).items():
            for value in values:
                item = counts.setdefault(value, {"value": value, "episodes": 0, "frames": 0})
                item["episodes"] += 1
                item["frames"] += lengths[index]
        columns[column] = sorted(counts.values(), key=lambda item: item["value"])
    options = {"episode_count": meta.total_episodes, "total_frames": sum(lengths), "tasks": tasks, "columns": columns}
    return options


def selection_summary(meta, episodes):
    lengths = [int(meta.episodes[index]["length"]) for index in episodes]
    summary = {
        "episode_count": len(episodes),
        "frames": sum(lengths),
        "seconds": round(sum(lengths) / meta.fps, 1),
        "unselected_count": meta.total_episodes - len(episodes),
    }
    return summary


def copy_videos_lossless(source, new_meta, episode_mapping, work_dir, reporter):
    """与官方 split_dataset 的视频步骤相同的文件对应关系，但不重新编码。

    源视频文件中的 episode 全部选中时整文件复制（与官方相同）；只选中部分时，按 episode 流拷贝取出片段，
    再用官方 concatenate_video_files 合并，episode 时间偏移按各片段时长累加（与官方 v2.1 → v3 合并视频相同）。"""
    from lerobot.datasets.video_utils import concatenate_video_files, get_video_duration_in_s

    from util.lerobot_v21_helper import split_video

    meta = source.meta
    video_metadata = {new_index: {} for new_index in episode_mapping.values()}
    for key in meta.video_keys:
        files = {}
        for old_index in episode_mapping:
            episode = meta.episodes[old_index]
            files.setdefault((episode[f"videos/{key}/chunk_index"], episode[f"videos/{key}/file_index"]), []).append(old_index)
        for (chunk, file), kept in sorted(files.items()):
            reporter.check_cancel()
            in_file = [index for index in range(meta.total_episodes)
                       if meta.episodes[index][f"videos/{key}/chunk_index"] == chunk and meta.episodes[index][f"videos/{key}/file_index"] == file]
            relative = meta.video_path.format(video_key=key, chunk_index=chunk, file_index=file)
            target = new_meta.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            kept = sorted(kept, key=lambda index: episode_mapping[index])
            if set(kept) == set(in_file):
                shutil.copy(source.root / relative, target)
                for old_index in kept:
                    episode = meta.episodes[old_index]
                    video_metadata[episode_mapping[old_index]].update({
                        f"videos/{key}/chunk_index": chunk,
                        f"videos/{key}/file_index": file,
                        f"videos/{key}/from_timestamp": episode[f"videos/{key}/from_timestamp"],
                        f"videos/{key}/to_timestamp": episode[f"videos/{key}/to_timestamp"],
                    })
                continue
            segments = []
            for old_index in kept:
                episode = meta.episodes[old_index]
                segment = Path(work_dir) / f"{key}-{old_index:06d}.mp4"
                split_video(source.root / relative, segment, episode[f"videos/{key}/from_timestamp"], episode[f"videos/{key}/to_timestamp"],
                            meta.fps, int(episode["length"]), strip_parameter_sets=False)
                segments.append(segment)
            concatenate_video_files(segments, target)
            offset = 0.0
            for old_index, segment in zip(kept, segments):
                duration = get_video_duration_in_s(segment)
                video_metadata[episode_mapping[old_index]].update({
                    f"videos/{key}/chunk_index": chunk,
                    f"videos/{key}/file_index": file,
                    f"videos/{key}/from_timestamp": offset,
                    f"videos/{key}/to_timestamp": offset + duration,
                })
                offset += duration
                segment.unlink()
    return video_metadata


def validate_subset(temp, params, mapping):
    """用官方 LeRobotDataset 打开子数据集，逐 episode 与源核对：数值列逐值、任务文本、索引重编号、统计；每路视频抽查首/中/末帧逐像素相等。"""
    from util.lerobot_helper import LeRobotEpisode

    checks = Checks()
    check = checks.add
    source = Path(params["dataset_path"])
    checks.standard_dirs(temp)
    reader = open_dataset(temp, repo_id=f"local/{Path(params['output_path']).name}")
    meta = load_metadata(source)
    output_meta = reader.meta
    expected_frames = sum(int(meta.episodes[old]["length"]) for old in mapping)
    check("官方 LeRobotDataset 可打开", output_meta.info.codebase_version == "v3.0")
    check("episode 数与帧数", (output_meta.total_episodes, output_meta.total_frames, len(reader)) == (len(mapping), expected_frames, expected_frames),
          f"{output_meta.total_episodes}/{output_meta.total_frames}")
    others = {key: value for key, value in output_meta.features.items() if key not in DEFAULT_FEATURES}
    check("feature 定义不变（索引列按官方默认定义）", others == {key: value for key, value in meta.features.items() if key not in DEFAULT_FEATURES})
    source_tasks = {int(row["task_index"]): task for task, row in meta.tasks.iterrows()}
    output_tasks = {int(row["task_index"]): task for task, row in output_meta.tasks.iterrows()}
    # 逐 episode 读取，源与输出各只缓存当前一个 data 文件，内存与数据集大小无关。
    index = column_values(temp, "index")
    check("全局 index 连续", np.array_equal(index, np.arange(len(index))))
    cache, output_cache = {}, {}
    columns = [key for key, feature in meta.features.items() if feature["dtype"] not in ("video", "image") and key not in ("episode_index", "index", "task_index")]
    for old, new in mapping.items():
        label = f"episode_{new:06d}（源 {old}）"
        expected = episode_table(source, meta, old, cache).to_pandas()
        actual = episode_table(temp, output_meta, new, output_cache).to_pandas()
        same = len(actual) == len(expected) and all(
            columns_equal(actual[key].to_numpy(), expected[key].to_numpy()) for key in columns)
        check(f"{label} 数值列逐值一致", same)
        check(f"{label} 任务文本一致", [output_tasks[int(index)] for index in actual["task_index"]] == [source_tasks[int(index)] for index in expected["task_index"]])
        check(f"{label} 任务列表与长度", list(output_meta.episodes[new]["tasks"]) == list(meta.episodes[old]["tasks"])
              and int(output_meta.episodes[new]["length"]) == int(meta.episodes[old]["length"]))
        with LeRobotEpisode(temp, new, output_meta) as episode, LeRobotEpisode(source, old, meta) as reference:
            checks.sample_frames(label, episode, reference)
    summary = checks.summary(failed_limit=20)
    return summary


def subset_job(params, reporter):
    """工作进程中运行：抽取子数据集。数据、元数据与统计用官方 split_dataset 的函数；视频不重新编码（copy_videos_lossless）。

    写入临时目录 → 验证 → 发布 → 登记。官方函数内部整体处理，数据与元数据步骤之间响应取消。"""
    from lerobot.datasets.dataset_metadata import LeRobotDatasetMetadata
    from lerobot.datasets.dataset_tools import _copy_and_reindex_data, _copy_and_reindex_episodes_metadata
    from lerobot.datasets.lerobot_dataset import LeRobotDataset

    episodes = sorted(params["episodes"])
    mapping = {old: new for new, old in enumerate(episodes)}
    with staged_output(params["output_path"], reporter) as (temp, output):
        # 视频片段的临时目录，片段合并进 temp 后即删除。
        work = temporary_path(output.parent / f"{output.name}-segments", reporter.task_id)
        try:
            source = LeRobotDataset(f"local/{Path(params['dataset_path']).name}", root=params["dataset_path"], video_backend="pyav")
            meta = source.meta
            # 与官方 split_dataset 创建子数据集元数据的参数相同。
            new_meta = LeRobotDatasetMetadata.create(repo_id=f"local/{output.name}", fps=meta.fps, features=meta.features, robot_type=meta.robot_type,
                                                     root=temp, use_videos=len(meta.video_keys) > 0, chunks_size=meta.chunks_size,
                                                     data_files_size_in_mb=meta.data_files_size_in_mb, video_files_size_in_mb=meta.video_files_size_in_mb)
            reporter.progress(0, 3, "视频（流拷贝）")
            work.mkdir(parents=True)
            video_metadata = copy_videos_lossless(source, new_meta, mapping, work, reporter) if meta.video_keys else None
            reporter.progress(1, 3, "数据与元数据（官方函数）")
            data_metadata = _copy_and_reindex_data(source, new_meta, mapping)
            reporter.check_cancel()
            _copy_and_reindex_episodes_metadata(source, new_meta, mapping, data_metadata, video_metadata)
        finally:
            remove_temporary(work)
        reporter.progress(2, 3, "验证输出")
        reporter.set_status("validating", "验证输出")
        validation = validate_subset(temp, params, mapping)
        require_passed(validation)
        publish(temp, output, reporter)
    dataset = reporter.register_dataset(output.name, output, LEROBOT_V3)
    result = {
        "output_path": str(output),
        "registered_dataset_id": dataset["id"],
        "episode_count": len(episodes),
        "frames": load_metadata(output).total_frames,
        "validation": validation,
        "official_read_failures": official_read_failures(output),
        "report": {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "source_dataset": params["dataset_path"],
            "operation": "抽取子数据集（数据与元数据：官方 split_dataset 的函数；视频：按 episode 流拷贝，不重新编码）",
            "selection": params["selection"],
            "episode_mapping": {str(new): old for old, new in mapping.items()},
            "unselected_episodes": params["unselected"],
        },
    }
    return result


class DataSplitter:
    """抽取子数据集的选择项、预检与后台任务提交。"""

    def __init__(self, dataset_manager, task_manager, output_dir):
        self.dataset_manager = dataset_manager
        self.task_manager = task_manager
        self.output_dir = Path(output_dir)

    def options(self, dataset_id):
        dataset = self.dataset_manager.get_v3(dataset_id)
        options = subset_options(load_metadata(dataset["path"]), dataset["path"])
        return options

    def preview(self, dataset_id, mode, **kwargs):
        """抽取子数据集的预检：选中的 episode、帧数与时长、未选数量与说明，不写任何文件。

        mode：episodes（kwargs episodes 为 "0-99, 120" 形式）、tasks（kwargs tasks 为 task_index 列表）、
        values（kwargs column 为字符串列名，values 为取值列表）。"""
        dataset = self.dataset_manager.get_v3(dataset_id)
        meta = load_metadata(dataset["path"])
        selection = SubsetSelection(meta, dataset["path"]).select(mode, **kwargs)
        episodes = selection["episodes"]
        blocking = []
        if not episodes:
            blocking.append("没有选中任何 episode")
        output = resolve_output(self.output_dir, kwargs.get("output_name") or f"{dataset['name']}_subset")
        preview = {
            "dataset": {"id": dataset["id"], "name": dataset["name"], "path": dataset["path"], "format": dataset["format"]},
            "selection": {"mode": mode, **{key: kwargs[key] for key in ("episodes", "tasks", "column", "values") if key in kwargs}},
            "episodes": episodes,
            "summary": selection_summary(meta, episodes),
            "notes": selection["notes"],
            "source_episode_count": meta.total_episodes,
            **output_fields(self.output_dir, output, blocking),
        }
        return preview

    def submit(self, dataset_id, mode, **kwargs):
        preview = self.preview(dataset_id, mode, **kwargs)
        require_ready(preview)
        selected = set(preview["episodes"])
        params = {
            "dataset_id": preview["dataset"]["id"],
            "dataset_path": preview["dataset"]["path"],
            "output_path": preview["output_path"],
            "selection": preview["selection"],
            "episodes": preview["episodes"],
            "unselected": [index for index in range(preview["source_episode_count"]) if index not in selected],
        }
        task = self.task_manager.submit(SPLIT_JOB, params)
        return task


DEMO_OUTPUT = REPO_ROOT / "output" / "demo" / "data_splitter"


def demo_split():
    """在合成的 3 个 episode 小样本（含深度）上按编号抽取 episode 0 与 2，输出到独立目录。"""
    from component.common.dataset_access import DatasetAccess
    from component.common.task_manager import DemoReporter
    from component.dataset.dataset_manager import DatasetManager
    from util.lerobot_helper import create_demo_dataset
    from util.storage_helper import Storage

    shutil.rmtree(DEMO_OUTPUT, ignore_errors=True)
    DEMO_OUTPUT.mkdir(parents=True)
    manager = DatasetManager(Storage(DEMO_OUTPUT / "demo.sqlite3"), DatasetAccess(), DEMO_OUTPUT)
    splitter = DataSplitter(manager, None, DEMO_OUTPUT / "output")
    dataset = manager.register(create_demo_dataset(DEMO_OUTPUT / "sample", ["put the red pepper on the red tray", "put the green pepper on the red tray", "wave hand"]))
    logger.info("Options: {}", splitter.options(dataset["id"]))
    preview = splitter.preview(dataset["id"], "episodes", episodes="0, 2")
    logger.info("Selected {} {}; blocking {}", preview["episodes"], preview["summary"], preview["blocking"])
    selected = set(preview["episodes"])
    params = {
        "dataset_path": preview["dataset"]["path"],
        "output_path": preview["output_path"],
        "selection": preview["selection"],
        "episodes": preview["episodes"],
        "unselected": [index for index in range(preview["source_episode_count"]) if index not in selected],
    }
    result = subset_job(params, DemoReporter())
    logger.info("Subset written: {} episodes, {} frames, checks={} passed={}", result["episode_count"], result["frames"],
                result["validation"]["check_count"], result["validation"]["passed"])


def main():
    demo_split()


if __name__ == "__main__":
    main()
