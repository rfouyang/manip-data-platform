"""合并：把多个字段一致的 LeRobot v3.0 数据集合并为一个数据集（用户定义，2026-09-23）。

写入用官方 aggregate_datasets（视频整文件复制或流拷贝拼接，不重新编码；数据改写编号；任务合并去重；统计汇总）。
平台负责预检（字段、FPS、机器人类型逐项比对，重复 episode 检测）、临时目录、验证、发布与登记。源数据只读。"""
import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from lerobot.configs import VIDEO_ENCODER_INFO_KEYS
from lerobot.utils.constants import DEFAULT_FEATURES
from loguru import logger

from component.common.dataset_access import LEROBOT_V3
from component.common.output_publish import output_fields, publish, require_ready, resolve_output, staged_output
from component.common.validation import Checks, require_passed
from util.lerobot_helper import (
    column_values,
    columns_equal,
    data_columns,
    data_files,
    episode_table,
    load_metadata,
    official_read_failures,
    open_dataset,
    plain_value,
    remove_empty_image_dir,
    task_names,
)


MERGE_JOB = "merge_datasets"
REPO_ROOT = Path(__file__).resolve().parents[2]
# 不参与内容比对的索引列（合并后会重新编号）。
INDEX_COLUMNS = ("episode_index", "index", "task_index")


def feature_differences(reference, other):
    """按官方 features_equal_for_merge 的规则逐项列出差异：视频 feature 忽略编码参数键，其余键须完全相同。"""
    differences = []
    for key in sorted(set(reference) | set(other)):
        if key not in other:
            differences.append(f"缺少字段 {key}")
            continue
        if key not in reference:
            differences.append(f"多出字段 {key}")
            continue
        a, b = dict(reference[key]), dict(other[key])
        if a.get("dtype") == "video":
            a["info"] = {name: value for name, value in (a.get("info") or {}).items() if name not in VIDEO_ENCODER_INFO_KEYS}
            b["info"] = {name: value for name, value in (b.get("info") or {}).items() if name not in VIDEO_ENCODER_INFO_KEYS}
        for name in sorted(set(a) | set(b)):
            if name == "info" and a.get("dtype") == "video":
                for info_key in sorted(set(a["info"]) | set(b["info"])):
                    if a["info"].get(info_key) != b["info"].get(info_key):
                        differences.append(f"{key}.info.{info_key}: {a['info'].get(info_key)!r} ≠ {b['info'].get(info_key)!r}")
            elif normalize(a.get(name)) != normalize(b.get(name)):
                differences.append(f"{key}.{name}: {a.get(name)!r} ≠ {b.get(name)!r}")
    return differences


def normalize(value):
    # 元组与列表（shape）按内容比较，与官方 features_equal_for_merge 读回 info.json 后的比较一致。
    result = list(value) if isinstance(value, tuple) else value
    return result


def encoder_differences(metas):
    """视频编码参数（crf、g 等）不一致时，官方合并把该键记为 null；只影响记录，不影响视频数据。"""
    notes = []
    reference = metas[0].features
    for key, feature in reference.items():
        if feature["dtype"] != "video":
            continue
        for info_key in sorted(VIDEO_ENCODER_INFO_KEYS):
            values = {repr((meta.features.get(key, {}).get("info") or {}).get(info_key)) for meta in metas}
            if len(values) > 1:
                notes.append(f"{key} 的 {info_key} 各数据集不同（{'、'.join(sorted(values))}），合并后记为 null")
    return notes


def column_bytes(values):
    """一列的逐帧值转为字节：向量列按数组拼接，字符串列按文本拼接，标量列直接取内存；嵌套结构（语言列）按 JSON 文本。"""
    if values.dtype != object:
        data = values.tobytes()
    elif isinstance(values[0], str):
        data = "\x1f".join(values).encode("utf-8")
    elif isinstance(values[0], np.ndarray) and values[0].dtype != object:
        data = np.stack(values).tobytes()
    else:
        data = json.dumps([plain_value(value) for value in values], ensure_ascii=False, sort_keys=True).encode("utf-8")
    return data


def episode_fingerprints(root, meta):
    """每个 episode 的内容指纹：除索引列外全部数值与字符串列逐帧的字节，加上任务描述。用于发现重复合并的 episode。"""
    names = task_names(meta)
    fingerprints = {}
    for path in data_files(root):
        frame = pd.read_parquet(path)
        columns = [column for column in frame.columns if column not in INDEX_COLUMNS]
        for episode, rows in frame.groupby("episode_index"):
            rows = rows.sort_values("frame_index")
            digest = hashlib.sha1()
            for column in columns:
                digest.update(column_bytes(rows[column].to_numpy()))
            digest.update("|".join(names[int(index)] for index in rows["task_index"].unique()).encode("utf-8"))
            fingerprints[int(episode)] = digest.hexdigest()
    return fingerprints


def find_duplicates(datasets, metas):
    """按内容指纹找出重复的 episode（包括同一数据集内部），每项记录重复者与首次出现的位置。"""
    duplicates = []
    seen = {}
    for dataset, meta in zip(datasets, metas):
        for episode, fingerprint in episode_fingerprints(dataset["path"], meta).items():
            if fingerprint in seen:
                duplicates.append({"episode": [dataset["name"], episode], "same_as": seen[fingerprint]})
            else:
                seen[fingerprint] = [dataset["name"], episode]
    return duplicates


class DataMerger:
    """合并的预检与后台任务提交。"""

    def __init__(self, dataset_manager, task_manager, output_dir):
        self.dataset_manager = dataset_manager
        self.task_manager = task_manager
        self.output_dir = Path(output_dir)

    def preview(self, dataset_ids, **kwargs):
        """合并预检：各数据集概况、与第一个数据集的字段/FPS/机器人类型比对、重复 episode、合并后总数，不写任何文件。

        dataset_ids 的顺序即合并顺序（输出 episode 按此顺序依次排列）。"""
        if len(dataset_ids) < 2:
            raise ValueError("请至少选择 2 个数据集")
        if len(set(dataset_ids)) != len(dataset_ids):
            raise ValueError("同一个数据集不能重复选择")
        datasets = [self.dataset_manager.get(dataset_id) for dataset_id in dataset_ids]
        others = [dataset for dataset in datasets if dataset["format"] != LEROBOT_V3]
        if others:
            raise ValueError(f"只支持 LeRobot v3.0 数据集：{'、'.join(dataset['name'] for dataset in others)}")
        if len({Path(dataset["path"]).resolve() for dataset in datasets}) != len(datasets):
            raise ValueError("所选数据集中有相同的目录")
        metas = [load_metadata(dataset["path"]) for dataset in datasets]
        reference = metas[0]
        blocking = []
        sources = []
        for dataset, meta in zip(datasets, metas):
            differences = feature_differences(reference.features, meta.features)
            if meta.fps != reference.fps:
                differences.insert(0, f"fps: {meta.fps} ≠ {reference.fps}")
            if meta.robot_type != reference.robot_type:
                differences.insert(0, f"robot_type: {meta.robot_type!r} ≠ {reference.robot_type!r}")
            if differences:
                blocking.append(f"{dataset['name']} 与 {datasets[0]['name']} 不一致（{len(differences)} 项）")
            sources.append({
                "id": dataset["id"],
                "name": dataset["name"],
                "path": dataset["path"],
                "episodes": meta.total_episodes,
                "frames": meta.total_frames,
                "tasks": len(meta.tasks),
                "fps": meta.fps,
                "robot_type": meta.robot_type,
                "differences": differences,
            })
        # 字段不一致时不能合并，也不必计算内容指纹。
        duplicates = find_duplicates(datasets, metas) if not blocking else []
        all_tasks = set()
        for meta in metas:
            all_tasks.update(task_names(meta).values())
        output = resolve_output(self.output_dir, kwargs.get("output_name") or f"{datasets[0]['name']}_merged")
        notes = encoder_differences(metas) if not blocking else []
        preview = {
            "sources": sources,
            "reference": datasets[0]["name"],
            "total_episodes": sum(meta.total_episodes for meta in metas),
            "total_frames": sum(meta.total_frames for meta in metas),
            "total_tasks": len(all_tasks),
            "duplicates": duplicates,
            "notes": notes,
            **output_fields(self.output_dir, output, blocking),
        }
        return preview

    def submit(self, dataset_ids, **kwargs):
        preview = self.preview(dataset_ids, **kwargs)
        require_ready(preview)
        task = self.task_manager.submit(MERGE_JOB, merge_params(preview))
        return task


def merge_params(preview):
    params = {
        "sources": [{"id": source["id"], "name": source["name"], "path": source["path"]} for source in preview["sources"]],
        "output_path": preview["output_path"],
        "duplicates": preview["duplicates"],
        "notes": preview["notes"],
    }
    return params


def episode_mapping(metas):
    """输出 episode 编号 → (来源数据集序号, 源 episode 编号)；官方按选择顺序依次追加。"""
    mapping = []
    for position, meta in enumerate(metas):
        mapping.extend((position, episode) for episode in range(meta.total_episodes))
    return mapping


def validate_merge(temp, params, metas):
    """用官方 LeRobotDataset 打开结果，逐 episode 与来源核对：数值与字符串列逐值、任务文本、长度；每路视频抽查首/中/末帧逐像素一致。"""
    from util.lerobot_helper import LeRobotEpisode

    checks = Checks()
    check = checks.add
    checks.standard_dirs(temp)
    reader = open_dataset(temp, repo_id=f"local/{Path(params['output_path']).name}")
    output_meta = reader.meta
    total_episodes = sum(meta.total_episodes for meta in metas)
    total_frames = sum(meta.total_frames for meta in metas)
    check("官方 LeRobotDataset 可打开", output_meta.info.codebase_version == "v3.0")
    check("episode 数与帧数为各来源之和", (output_meta.total_episodes, output_meta.total_frames, len(reader)) == (total_episodes, total_frames, total_frames),
          f"{output_meta.total_episodes}/{output_meta.total_frames}")
    source_tasks = [task_names(meta) for meta in metas]
    output_tasks = task_names(output_meta)
    check("任务为各来源的并集", set(output_tasks.values()) == set().union(*[set(names.values()) for names in source_tasks]))
    # 逐值比较只覆盖输出中存在的列，丢失的 feature 需单独核对（索引列按官方默认定义，视频编码参数可能记为 null）。
    reference = {key: value for key, value in metas[0].features.items() if key not in DEFAULT_FEATURES}
    others = {key: value for key, value in output_meta.features.items() if key not in DEFAULT_FEATURES}
    differences = feature_differences(reference, others)
    check("feature 定义与来源一致", not differences, "；".join(differences))
    roots = [Path(source["path"]) for source in params["sources"]]
    output_columns, expected_columns = sorted(data_columns(temp)), sorted(data_columns(roots[0]))
    check("数据列与来源一致", output_columns == expected_columns, f"{output_columns} / {expected_columns}")
    # 逐 episode 读取，来源与输出各只缓存当前一个 data 文件，内存与数据集大小无关。
    index = column_values(temp, "index")
    check("全局 index 连续", np.array_equal(np.sort(index), np.arange(len(index))))
    mapping = episode_mapping(metas)
    columns = [column for column in output_columns if column not in INDEX_COLUMNS]
    cache, output_cache = {}, {}
    for new, (position, old) in enumerate(mapping):
        label = f"episode_{new:06d}（{params['sources'][position]['name']} 的 {old}）"
        meta = metas[position]
        expected = episode_table(roots[position], meta, old, cache).to_pandas()
        actual = episode_table(temp, output_meta, new, output_cache).to_pandas()
        same = len(actual) == len(expected) and all(
            columns_equal(actual[column].to_numpy(), expected[column].to_numpy()) for column in columns)
        check(f"{label} 数据列逐值一致", same)
        check(f"{label} 任务文本一致", [output_tasks[int(index)] for index in actual["task_index"]] == [source_tasks[position][int(index)] for index in expected["task_index"]])
        with LeRobotEpisode(temp, new, output_meta) as episode, LeRobotEpisode(roots[position], old, meta) as reference:
            checks.sample_frames(label, episode, reference)
    summary = checks.summary(failed_limit=20)
    return summary


def merge_job(params, reporter):
    """工作进程中运行：官方 aggregate_datasets 写入临时目录 → 验证 → 发布 → 登记。

    官方函数内部整体处理全部来源，只能在各阶段之间响应取消。"""
    from lerobot.datasets.aggregate import aggregate_datasets

    roots = [Path(source["path"]) for source in params["sources"]]
    with staged_output(params["output_path"], reporter) as (temp, output):
        reporter.progress(0, 2, "官方 aggregate_datasets 合并中")
        aggregate_datasets(repo_ids=[f"local/{root.name}" for root in roots], aggr_repo_id=f"local/{output.name}", roots=roots, aggr_root=temp)
        remove_empty_image_dir(temp)
        reporter.check_cancel()
        reporter.progress(1, 2, "验证输出")
        reporter.set_status("validating", "验证输出")
        metas = [load_metadata(root) for root in roots]
        validation = validate_merge(temp, params, metas)
        require_passed(validation)
        publish(temp, output, reporter)
    dataset = reporter.register_dataset(output.name, output, LEROBOT_V3)
    offsets = np.cumsum([0, *[meta.total_episodes for meta in metas]])
    sources = [{**source, "episodes": meta.total_episodes, "frames": meta.total_frames,
                "output_episodes": [int(offsets[position]), int(offsets[position + 1]) - 1]}
               for position, (source, meta) in enumerate(zip(params["sources"], metas))]
    output_meta = load_metadata(output)
    normalized = [key for key in DEFAULT_FEATURES if any(meta.features[key] != output_meta.features[key] for meta in metas)]
    result = {
        "output_path": str(output),
        "registered_dataset_id": dataset["id"],
        "episode_count": output_meta.total_episodes,
        "frames": output_meta.total_frames,
        "tasks": len(output_meta.tasks),
        "validation": validation,
        # 源数据集以开放 GOP 编码的深度视频，合并后 episode 相邻关系改变，官方 PyAV 随机读取个别首帧可能失败（数据完整，见 DEPTH_X265_PARAMS）。
        "official_read_failures": official_read_failures(output, output_meta),
        "report": {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "operation": "合并（官方 aggregate_datasets，视频不重新编码）",
            "sources": sources,
            "duplicates": params["duplicates"],
            "notes": params["notes"],
            "normalized_features": {key: "定义按官方默认值重写（去掉 fps 等附加键，dtype/shape/names 不变）" for key in normalized},
        },
    }
    return result


DEMO_OUTPUT = REPO_ROOT / "output" / "demo" / "data_merger"


def demo_merge():
    """把合成的 3 个 episode 小样本（含深度）按编号拆成两个子集，再合并回来，输出到独立目录。"""
    from component.common.dataset_access import DatasetAccess
    from component.common.task_manager import DemoReporter
    from component.dataset.dataset_manager import DatasetManager
    from component.split.data_splitter import DataSplitter, subset_job
    from util.lerobot_helper import create_demo_dataset
    from util.storage_helper import Storage

    shutil.rmtree(DEMO_OUTPUT, ignore_errors=True)
    DEMO_OUTPUT.mkdir(parents=True)
    manager = DatasetManager(Storage(DEMO_OUTPUT / "demo.sqlite3"), DatasetAccess(), DEMO_OUTPUT)
    splitter = DataSplitter(manager, None, DEMO_OUTPUT / "output")
    merger = DataMerger(manager, None, DEMO_OUTPUT / "output")
    source = manager.register(create_demo_dataset(DEMO_OUTPUT / "sample", ["put the red pepper on the red tray", "put the green pepper on the red tray", "wave hand"]))
    parts = []
    for name, spec in (("part_a", "0"), ("part_b", "1-2")):
        preview = splitter.preview(source["id"], "episodes", episodes=spec, output_name=name)
        selected = set(preview["episodes"])
        subset_job({"dataset_path": preview["dataset"]["path"], "output_path": preview["output_path"], "selection": preview["selection"],
                    "episodes": preview["episodes"], "unselected": [index for index in range(3) if index not in selected]}, DemoReporter())
        parts.append(manager.register(preview["output_path"])["id"])
    preview = merger.preview(parts, output_name="merged")
    logger.info("Merge preview: {} episodes, {} frames, blocking {}, duplicates {}", preview["total_episodes"], preview["total_frames"],
                preview["blocking"], preview["duplicates"])
    result = merge_job(merge_params(preview), DemoReporter())
    logger.info("Merged: {} episodes, {} frames, checks={} passed={}", result["episode_count"], result["frames"],
                result["validation"]["check_count"], result["validation"]["passed"])
    again = merger.preview([parts[0], source["id"]], output_name="with_duplicates")
    logger.info("Duplicates detected: {}", again["duplicates"])


def main():
    demo_merge()


if __name__ == "__main__":
    main()
