"""数据加 tag：新增一列（默认 tag）写入 LeRobot v3.0 数据集。取值方式为用正则从任务描述抽取，或整列填统一值（如数据来源，便于合并后区分）。

输出写入新目录，源数据只读。"""
import filecmp
import re
import shutil
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from lerobot.utils.constants import DEFAULT_FEATURES
from loguru import logger

from component.common.dataset_access import LEROBOT_V3
from component.common.output_publish import output_fields, publish, require_ready, resolve_output, staged_output
from component.common.validation import Checks, require_passed
from util.lerobot_helper import columns_equal, data_files, load_metadata, open_dataset


TAG_JOB = "tag_column"
# regex：从任务描述抽取；constant：整列统一值（用户要求，2026-09-24）。
TAG_MODES = ("regex", "constant")
DEFAULT_TAG_COLUMN = "tag"
# 列名同时作为 parquet 列与 feature 名；官方要求不含 "/"，这里限制为常规标识符。
COLUMN_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
# 新列为字符串 feature，每帧一个值；shape 须为元组，官方按 shape == (1,) 判断标量列。
TAG_FEATURE = {"dtype": "string", "shape": (1,), "names": None}
REPO_ROOT = Path(__file__).resolve().parents[2]


def extract_tag(pattern, task):
    """正则在任务描述中搜索：有捕获组取第 1 组，否则取整个匹配；未匹配或结果为空返回 None。"""
    match = pattern.search(task)
    if match is None:
        return None
    value = match.group(1) if pattern.groups else match.group(0)
    value = value if value else None
    return value


def tag_values(root, tags):
    """按 data 文件顺序为每一帧取其 task_index 对应的值。"""
    parts = []
    for path in data_files(root):
        task_index = pd.read_parquet(path, columns=["task_index"])["task_index"].to_numpy()
        parts.append(np.array([tags[int(index)] for index in task_index], dtype=object))
    values = np.concatenate(parts)
    return values


def task_usage(root, meta):
    """每个任务的帧数（按逐帧 task_index 统计）与包含该任务的 episode 数。"""
    frames = {int(index): 0 for index in meta.tasks["task_index"]}
    for path in data_files(root):
        for index, count in pd.read_parquet(path, columns=["task_index"])["task_index"].value_counts().items():
            frames[int(index)] += int(count)
    episodes = {task: 0 for task in meta.tasks.index}
    for index in range(meta.total_episodes):
        for task in meta.episodes[index]["tasks"]:
            episodes[task] += 1
    usage = (frames, episodes)
    return usage


def value_summary(rows):
    """每个取值涉及的 episode 数与帧数，按取值排序。"""
    summary = []
    for value in sorted({row["value"] for row in rows}):
        members = [row for row in rows if row["value"] == value]
        summary.append({"value": value, "episodes": sum(row["episodes"] for row in members), "frames": sum(row["frames"] for row in members)})
    return summary


class DataTagger:
    """新增 tag 列的预检与后台任务提交。"""

    def __init__(self, dataset_manager, task_manager, output_dir):
        self.dataset_manager = dataset_manager
        self.task_manager = task_manager
        self.output_dir = Path(output_dir)

    def preview(self, dataset_id, mode, **kwargs):
        """新增列的预检：每个任务得到的值、episode 数与帧数，不写任何文件。

        mode 为 regex 时 kwargs pattern 为正则，未匹配的任务取 default，不填时为空字符串（用户确认，2026-09-24）；
        mode 为 constant 时 kwargs value 为整列统一值，不能为空。column 默认 tag。"""
        if mode not in TAG_MODES:
            raise ValueError(f"未知的取值方式: {mode}")
        dataset = self.dataset_manager.get_v3(dataset_id)
        column = kwargs.get("column") or DEFAULT_TAG_COLUMN
        pattern = kwargs.get("pattern") or ""
        default = kwargs.get("default") or ""
        constant = kwargs.get("value") or ""
        blocking = []
        meta = load_metadata(dataset["path"])
        if not COLUMN_NAME.match(column):
            blocking.append(f"列名不合法: {column}（字母或下划线开头，只含字母、数字、下划线和点）")
        elif column in meta.features:
            blocking.append(f"列已存在: {column}")
        compiled = None
        if mode == "regex":
            if not pattern:
                raise ValueError("请填写正则表达式")
            try:
                compiled = re.compile(pattern)
            except re.error as error:
                raise ValueError(f"正则表达式无效: {error}")
            if compiled.groups > 1:
                blocking.append(f"正则表达式有 {compiled.groups} 个捕获组；只取第 1 组，请把其余分组改为 (?:...)")
        elif not constant:
            raise ValueError("请填写统一值")

        frames, episodes = task_usage(dataset["path"], meta)
        rows = []
        for task, row in meta.tasks.iterrows():
            value = extract_tag(compiled, task) if mode == "regex" else constant
            rows.append({
                "task_index": int(row["task_index"]),
                "task": task,
                "value": value if value is not None else default,
                "matched": value is not None,
                "episodes": episodes[task],
                "frames": frames[int(row["task_index"])],
            })
        unmatched = [row for row in rows if not row["matched"]]
        # 列名不合法时已阻塞；默认输出名改用 tag 后缀，避免列名中的 / 使输出名被拒绝而看不到阻塞原因。
        suffix = column if COLUMN_NAME.match(column) else DEFAULT_TAG_COLUMN
        output = resolve_output(self.output_dir, kwargs.get("output_name") or f"{dataset['name']}_{suffix}")
        preview = {
            "dataset": {"id": dataset["id"], "name": dataset["name"], "path": dataset["path"], "format": dataset["format"]},
            "column": column,
            "mode": mode,
            "pattern": pattern if mode == "regex" else None,
            "default": default if mode == "regex" else None,
            "value": constant if mode == "constant" else None,
            "tasks": rows,
            "values": value_summary(rows),
            "unmatched_count": len(unmatched),
            "episode_count": meta.total_episodes,
            "total_frames": meta.total_frames,
            **output_fields(self.output_dir, output, blocking),
        }
        return preview

    def submit(self, dataset_id, mode, **kwargs):
        preview = self.preview(dataset_id, mode, **kwargs)
        require_ready(preview)
        task = self.task_manager.submit(TAG_JOB, tag_params(preview))
        return task


def tag_params(preview):
    """由预检结果生成任务参数：每个 task_index 对应的值在预检时确定，任务中不再重新计算。"""
    params = {
        "dataset_id": preview["dataset"]["id"],
        "dataset_path": preview["dataset"]["path"],
        "output_path": preview["output_path"],
        "column": preview["column"],
        "mode": preview["mode"],
        "pattern": preview["pattern"],
        "default": preview["default"],
        "value": preview["value"],
        "tags": {str(row["task_index"]): row["value"] for row in preview["tasks"]},
    }
    return params


def validate_tag_output(temp, params, tags):
    """用官方 LeRobotDataset 打开结果，核对：原有列与源逐值一致、新列按 task_index 取值、视频文件逐字节相同、计数与元数据一致。"""
    checks = Checks()
    check = checks.add
    source = Path(params["dataset_path"])
    column = params["column"]
    checks.standard_dirs(temp)
    reader = open_dataset(temp, repo_id=f"local/{Path(params['output_path']).name}")
    meta = load_metadata(source)
    check("官方 LeRobotDataset 可打开", reader.meta.info.codebase_version == "v3.0", reader.meta.info.codebase_version)
    check("episode 数与帧数不变", (reader.meta.total_episodes, reader.meta.total_frames) == (meta.total_episodes, meta.total_frames),
          f"{reader.meta.total_episodes}/{reader.meta.total_frames}")
    check(f"{column} feature 定义", reader.meta.features.get(column) == TAG_FEATURE, str(reader.meta.features.get(column)))
    # 官方 LeRobotDatasetMetadata.create 用默认定义覆盖 5 个索引列（官方 v2.1 → v3 转换加的 fps 键因此去掉）；
    # 这些列只允许该变化（dtype/shape/names 不变），其余 feature 定义须完全相同。
    others = {key: value for key, value in reader.meta.features.items() if key != column and key not in DEFAULT_FEATURES}
    check("其他 feature 定义不变", others == {key: value for key, value in meta.features.items() if key not in DEFAULT_FEATURES})
    core = ("dtype", "shape", "names")
    check("索引列 dtype/shape/names 不变", all(
        [reader.meta.features[key].get(name) for name in core] == [meta.features[key].get(name) for name in core] for key in DEFAULT_FEATURES))
    output_files = data_files(temp)
    check("data 文件对应", [path.relative_to(temp) for path in output_files] == [path.relative_to(source) for path in data_files(source)])
    for source_path, output_path in zip(data_files(source), output_files):
        expected = pd.read_parquet(source_path)
        actual = pd.read_parquet(output_path)
        name = output_path.relative_to(temp).as_posix()
        same = list(actual.columns) == [*expected.columns, column] and all(
            columns_equal(actual[key].to_numpy(), expected[key].to_numpy()) for key in expected.columns)
        check(f"{name} 原有列逐值一致", same)
        wanted = np.array([tags[int(index)] for index in expected["task_index"]], dtype=object)
        check(f"{name} {column} 按 task_index 取值", np.array_equal(actual[column].to_numpy(dtype=object), wanted))
    videos = sorted(path.relative_to(source) for path in (source / "videos").rglob("*.mp4"))
    copied = sorted(path.relative_to(temp) for path in (temp / "videos").rglob("*.mp4"))
    check("视频文件清单一致", videos == copied, f"{len(copied)} / {len(videos)}")
    identical = all(filecmp.cmp(source / path, temp / path, shallow=False) for path in videos if (temp / path).exists())
    check("视频文件逐字节相同", identical)
    summary = checks.summary()
    return summary


def tag_job(params, reporter):
    """工作进程中运行：官方 add_features 写入临时目录（视频整文件复制）→ 验证 → 发布 → 登记。

    官方函数内部整体处理全部数据，只能在各阶段之间响应取消。"""
    from lerobot.datasets.dataset_tools import add_features
    from lerobot.datasets.lerobot_dataset import LeRobotDataset

    tags = {int(index): value for index, value in params["tags"].items()}
    with staged_output(params["output_path"], reporter) as (temp, output):
        reporter.progress(0, 3, "生成逐帧值")
        values = tag_values(params["dataset_path"], tags)
        reporter.check_cancel()
        reporter.progress(1, 3, "官方 add_features 写出")
        source = LeRobotDataset(f"local/{Path(params['dataset_path']).name}", root=params["dataset_path"], video_backend="pyav")
        add_features(source, {params["column"]: (values, dict(TAG_FEATURE))}, output_dir=temp, repo_id=f"local/{output.name}")
        reporter.check_cancel()
        reporter.progress(2, 3, "验证输出")
        reporter.set_status("validating", "验证输出")
        validation = validate_tag_output(temp, params, tags)
        require_passed(validation)
        publish(temp, output, reporter)
    dataset = reporter.register_dataset(output.name, output, LEROBOT_V3)
    counts = pd.Series(values).value_counts()
    source_features = load_metadata(params["dataset_path"]).features
    normalized = [key for key in DEFAULT_FEATURES if source_features[key] != DEFAULT_FEATURES[key]]
    result = {
        "output_path": str(output),
        "registered_dataset_id": dataset["id"],
        "column": params["column"],
        "values": {str(value): int(count) for value, count in counts.items()},
        "validation": validation,
        "report": {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "source_dataset": params["dataset_path"],
            "operation": "新增列（官方 add_features）：" + ("整列统一值" if params["mode"] == "constant" else "正则从任务描述抽取"),
            "column": params["column"],
            "mode": params["mode"],
            "pattern": params["pattern"],
            "default": params["default"],
            "value": params["value"],
            "task_values": params["tags"],
            "normalized_features": {key: "定义按官方默认值重写（去掉 fps 等附加键，dtype/shape/names 不变）" for key in normalized},
        },
    }
    return result


DEMO_OUTPUT = REPO_ROOT / "output" / "demo" / "data_tagger"


def demo_tag():
    """在合成的 3 个 episode 小样本（含深度）上预检并生成 tag 列，输出到独立目录。"""
    from component.common.dataset_access import DatasetAccess
    from component.common.task_manager import DemoReporter
    from component.dataset.dataset_manager import DatasetManager
    from util.lerobot_helper import create_demo_dataset
    from util.storage_helper import Storage

    shutil.rmtree(DEMO_OUTPUT, ignore_errors=True)
    DEMO_OUTPUT.mkdir(parents=True)
    manager = DatasetManager(Storage(DEMO_OUTPUT / "demo.sqlite3"), DatasetAccess(), DEMO_OUTPUT)
    tagger = DataTagger(manager, None, DEMO_OUTPUT / "output")
    dataset = manager.register(create_demo_dataset(DEMO_OUTPUT / "sample", ["put the red pepper on the red tray", "put the green pepper on the red tray", "wave hand"]))
    preview = tagger.preview(dataset["id"], "regex", pattern=r"put the (\w+ \w+) on")
    for row in preview["tasks"]:
        logger.info("task {} -> {} ({} episodes, {} frames)", row["task"], row["value"], row["episodes"], row["frames"])
    logger.info("Blocking: {}", preview["blocking"])
    unmatched = tagger.preview(dataset["id"], "regex", pattern=r"green (\w+)")
    logger.info("Unmatched tasks get empty value: {}", [(row["task"], row["value"]) for row in unmatched["tasks"]])
    constant = tagger.preview(dataset["id"], "constant", value="demo_batch", column="source")
    logger.info("Constant value: {}", constant["values"])
    result = tag_job(tag_params(preview), DemoReporter())
    logger.info("Tag column written: {} values={} checks={} passed={}", result["output_path"], result["values"],
                result["validation"]["check_count"], result["validation"]["passed"])


def main():
    demo_tag()


if __name__ == "__main__":
    main()
