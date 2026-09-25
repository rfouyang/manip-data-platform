"""子任务：按时间线把 episode 分成子任务并编辑描述，写入 LeRobot 0.6 官方语言标注列；编辑 episode 的整体描述 task；以及把子任务转为逐帧 task。

格式参考官方 lerobot-annotate：子任务是 language_persistent 中 style="subtask" 的行（role="assistant"、content 为描述、
timestamp 为生效起始帧的时间戳），覆盖整个 episode，每段持续到下一段开始；task 仍为整体描述。
写入用官方 LanguageColumnsWriter 与 StagingValidator；其他语言标注（plan、memory、事件等）原样保留。
编辑先存为草稿（SQLite），生成新数据集时才写文件；源数据只读。"""
import filecmp
import re
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from loguru import logger

from component.common.dataset_access import LEROBOT_V3
from component.common.output_publish import output_fields, publish, require_ready, resolve_output, staged_output
from component.common.validation import Checks, require_passed
from util.lerobot_helper import (
    LANGUAGE_COLUMNS,
    columns_equal,
    data_files,
    language_rows,
    load_metadata,
    open_dataset,
    subtask_segments,
    task_names,
)


SUBTASK_WRITE_JOB = "subtask_write"
SUBTASK_TO_TASK_JOB = "subtask_to_task"
REPO_ROOT = Path(__file__).resolve().parents[2]
# 子任务转 task 的模板：{task} 为原整体描述，{subtask} 为当前子任务。
DEFAULT_TASK_TEMPLATE = "{subtask}"


def check_segments(segments, length):
    """分段须从第 0 帧开始、起始帧严格递增且在 episode 内、描述非空；返回规范化后的分段（去除首尾空白）。"""
    if not segments:
        result = []
        return result
    starts = [int(segment["start"]) for segment in segments]
    if starts[0] != 0:
        raise ValueError("第一段必须从第 0 帧开始")
    if any(later <= earlier for earlier, later in zip(starts, starts[1:])):
        raise ValueError("各段起始帧必须严格递增")
    if starts[-1] >= length:
        raise ValueError(f"起始帧超出 episode 长度 {length}")
    texts = [str(segment.get("text") or "").strip() for segment in segments]
    if not all(texts):
        raise ValueError("子任务描述不能为空")
    result = [{"start": start, "text": text} for start, text in zip(starts, texts)]
    return result


def check_tasks(original, edited):
    """edited 与 episode 原任务列表一一对应；描述去掉首尾空白后不能为空。返回只含改动项的 {原文本: 新文本}。"""
    if len(edited) != len(original):
        raise ValueError(f"task 数量不一致：应为 {len(original)} 个")
    texts = [str(text or "").strip() for text in edited]
    if not all(texts):
        raise ValueError("task 描述不能为空")
    edits = {old: new for old, new in zip(original, texts) if new != old}
    return edits


def segment_table(segments, length, fps):
    """补上结束帧与时长，供页面与导出使用。"""
    rows = []
    for position, segment in enumerate(segments):
        end = segments[position + 1]["start"] - 1 if position + 1 < len(segments) else length - 1
        rows.append({**segment, "end": end, "seconds": round((end - segment["start"] + 1) / fps, 3)})
    return rows


class SubtaskEditor:
    """子任务的读取、草稿编辑、批量替换、导入导出与后台任务提交。"""

    def __init__(self, dataset_manager, storage, task_manager, output_dir):
        self.dataset_manager = dataset_manager
        self.storage = storage
        self.task_manager = task_manager
        self.output_dir = Path(output_dir)

    def current(self, dataset_id):
        """所有 episode 当前的子任务：草稿优先，其次为数据集中已有的标注。
        返回 dataset、meta、names（task_index → 任务描述）与 episodes（{episode: 信息}）。"""
        dataset = self.dataset_manager.get_v3(dataset_id)
        meta = load_metadata(dataset["path"])
        stored = language_rows(dataset["path"])
        drafts = self.storage.list_subtask_drafts(dataset_id)
        task_drafts = self.storage.list_task_drafts(dataset_id)
        names = task_names(meta)
        stamps = episode_timestamps(dataset["path"]) if stored else {}
        episodes = {}
        for index in range(meta.total_episodes):
            episode = meta.episodes[index]
            original = subtask_segments(stored[index]["persistent"], stamps[index]) if index in stored else []
            tasks = list(episode["tasks"])
            edits = task_drafts.get(index, {})
            modified = index in drafts or index in task_drafts
            episodes[index] = {
                "episode_index": index,
                "length": int(episode["length"]),
                "tasks": tasks,
                "edited_tasks": [edits.get(task, task) for task in tasks],
                "task_edits": edits,
                "segments": drafts.get(index, original),
                "original": original,
                "status": "modified" if modified else ("labeled" if original else "unlabeled"),
            }
        result = {"dataset": dataset, "meta": meta, "names": names, "episodes": episodes}
        return result

    def episodes(self, dataset_id):
        """episode 列表：长度、整体任务、段数与状态（modified 已修改草稿 / labeled 已标注 / unlabeled 未标注）；
        以及本数据集当前用到的子任务描述与使用段数（按次数降序），供页面做常用子任务与下拉建议。"""
        current = self.current(dataset_id)
        dataset, meta, episodes = current["dataset"], current["meta"], current["episodes"]
        items = [{key: item[key] for key in ("episode_index", "length", "tasks", "edited_tasks", "status")} | {"segments": len(item["segments"])}
                 for item in episodes.values()]
        usage = Counter(segment["text"] for item in episodes.values() for segment in item["segments"])
        summary = {
            "dataset": {"id": dataset["id"], "name": dataset["name"], "path": dataset["path"]},
            "fps": meta.fps,
            "episodes": items,
            "counts": {status: sum(1 for item in items if item["status"] == status) for status in ("modified", "labeled", "unlabeled")},
            "subtask_texts": [{"text": text, "count": count} for text, count in usage.most_common()],
        }
        return summary

    def episode(self, dataset_id, episode_index):
        current = self.current(dataset_id)
        meta, episodes = current["meta"], current["episodes"]
        if episode_index not in episodes:
            raise LookupError(f"episode 不存在: {episode_index}")
        item = episodes[episode_index]
        detail = {
            **{key: item[key] for key in ("episode_index", "length", "tasks", "edited_tasks", "status")},
            "fps": meta.fps,
            "segments": segment_table(item["segments"], item["length"], meta.fps),
            "original": segment_table(item["original"], item["length"], meta.fps),
        }
        return detail

    def save(self, dataset_id, episode_index, segments, tasks=None):
        """校验并保存为草稿（不改数据文件）。tasks 为与原任务列表一一对应的新描述，None 表示不改 task。
        与数据集中已有内容相同的部分不存草稿（已有草稿则删除），因此只改 task 时不会产生子任务草稿。"""
        episodes = self.current(dataset_id)["episodes"]
        if episode_index not in episodes:
            raise LookupError(f"episode 不存在: {episode_index}")
        item = episodes[episode_index]
        checked = check_segments(segments, item["length"])
        edits = check_tasks(item["tasks"], tasks) if tasks is not None else item["task_edits"]
        self.write_draft(dataset_id, item, checked, edits)
        detail = self.episode(dataset_id, episode_index)
        return detail

    def write_draft(self, dataset_id, item, segments, edits):
        """写入已校验的草稿；与数据集中已有内容相同的部分删除草稿。"""
        index = item["episode_index"]
        if segments != item["original"]:
            self.storage.save_subtask_draft(dataset_id, index, segments)
        else:
            self.storage.delete_subtask_drafts(dataset_id, [index])
        if edits:
            self.storage.save_task_draft(dataset_id, index, edits)
        else:
            self.storage.delete_task_drafts(dataset_id, [index])

    def discard(self, dataset_id, episode_indices=None):
        """放弃草稿（子任务与 task），恢复为数据集中已有的内容；episode_indices 为 None 时放弃全部草稿。返回涉及的 episode 数。"""
        self.dataset_manager.get_v3(dataset_id)
        drafted = set(self.storage.list_subtask_drafts(dataset_id)) | set(self.storage.list_task_drafts(dataset_id))
        if episode_indices is not None:
            drafted &= set(episode_indices)
        self.storage.delete_subtask_drafts(dataset_id, episode_indices)
        self.storage.delete_task_drafts(dataset_id, episode_indices)
        result = {"discarded": len(drafted)}
        return result

    def replace_text(self, dataset_id, find, replace, **kwargs):
        """在全部 episode 当前的描述中查找替换（regex=True 时按正则），有变化的 episode 存为草稿。
        scope：subtask 只改子任务（默认），task 只改整体描述，all 两者都改。"""
        if not find:
            raise ValueError("请填写要查找的文字")
        scope = kwargs.get("scope", "subtask")
        if scope not in ("subtask", "task", "all"):
            raise ValueError(f"未知的替换范围: {scope}")
        try:
            pattern = re.compile(find if kwargs.get("regex") else re.escape(find))
        except re.error as error:
            raise ValueError(f"正则表达式无效: {error}")
        episodes = self.current(dataset_id)["episodes"]
        # 先替换并校验全部 episode（如替换后描述为空、反向引用无效），任一不合法时不写入任何草稿。
        pending = []
        for index, item in episodes.items():
            try:
                segments = item["segments"]
                if scope in ("subtask", "all"):
                    segments = [{"start": segment["start"], "text": pattern.sub(replace, segment["text"]).strip()} for segment in item["segments"]]
                tasks = item["edited_tasks"]
                if scope in ("task", "all"):
                    tasks = [pattern.sub(replace, task).strip() for task in item["edited_tasks"]]
                if segments != item["segments"] or tasks != item["edited_tasks"]:
                    pending.append((item, check_segments(segments, item["length"]), check_tasks(item["tasks"], tasks)))
            except (ValueError, re.error) as error:
                raise ValueError(f"episode {index}: {error}")
        for item, segments, edits in pending:
            self.write_draft(dataset_id, item, segments, edits)
        result = {"changed_episodes": [item["episode_index"] for item, _, _ in pending]}
        return result

    def export_rows(self, dataset_id):
        """当前子任务导出为行：episode_index、start_frame、end_frame、start_time、text。"""
        current = self.current(dataset_id)
        meta, episodes = current["meta"], current["episodes"]
        rows = []
        for index, item in episodes.items():
            for segment in segment_table(item["segments"], item["length"], meta.fps):
                rows.append({"episode_index": index, "start_frame": segment["start"], "end_frame": segment["end"],
                             "start_time": round(segment["start"] / meta.fps, 4), "text": segment["text"]})
        return rows

    def import_rows(self, dataset_id, rows):
        """按 episode 分组导入（start_frame、text；文件中出现的 episode 整体替换为草稿）。任一 episode 不合法时全部不导入。"""
        episodes = self.current(dataset_id)["episodes"]
        grouped = {}
        for number, row in enumerate(rows, start=1):
            try:
                index, start, text = int(row["episode_index"]), int(row["start_frame"]), str(row["text"])
            except (KeyError, TypeError, ValueError):
                raise ValueError(f"第 {number} 行缺少 episode_index、start_frame 或 text，或不是整数")
            if index not in episodes:
                raise ValueError(f"第 {number} 行 episode 不存在: {index}")
            grouped.setdefault(index, []).append({"start": start, "text": text})
        checked = {}
        for index, segments in grouped.items():
            try:
                checked[index] = check_segments(sorted(segments, key=lambda segment: segment["start"]), episodes[index]["length"])
            except ValueError as error:
                raise ValueError(f"episode {index}: {error}")
        for index, segments in checked.items():
            self.storage.save_subtask_draft(dataset_id, index, segments)
        result = {"imported_episodes": sorted(checked)}
        return result

    def preview_write(self, dataset_id, **kwargs):
        """生成新数据集（写入子任务与 task 修改）的预检：各状态的 episode 数、分段数、改了 task 的 episode 数，不写文件。"""
        current = self.current(dataset_id)
        dataset, episodes = current["dataset"], current["episodes"]
        output = resolve_output(self.output_dir, kwargs.get("output_name") or f"{dataset['name']}_subtask")
        counts = {status: sum(1 for item in episodes.values() if item["status"] == status) for status in ("modified", "labeled", "unlabeled")}
        blocking = []
        if not counts["modified"]:
            blocking.append("没有修改过的 episode（草稿）")
        preview = {
            "dataset": {"id": dataset["id"], "name": dataset["name"], "path": dataset["path"]},
            "counts": counts,
            "segments": sum(len(item["segments"]) for item in episodes.values()),
            "subtask_edit_episodes": len(self.storage.list_subtask_drafts(dataset_id)),
            "task_edit_episodes": sum(1 for item in episodes.values() if item["task_edits"]),
            **output_fields(self.output_dir, output, blocking),
        }
        return preview

    def submit_write(self, dataset_id, **kwargs):
        preview = self.preview_write(dataset_id, **kwargs)
        require_ready(preview)
        drafts = self.storage.list_subtask_drafts(dataset_id)
        params = {
            "dataset_id": dataset_id,
            "dataset_path": preview["dataset"]["path"],
            "output_path": preview["output_path"],
            "segments": {str(index): segments for index, segments in drafts.items()},
            "tasks": {str(index): edits for index, edits in self.storage.list_task_drafts(dataset_id).items()},
        }
        task = self.task_manager.submit(SUBTASK_WRITE_JOB, params)
        return task

    def preview_to_task(self, dataset_id, **kwargs):
        """子任务转逐帧 task 的预检：模板示例、将产生的任务数、没有子任务的 episode 数（保留原 task），不写文件。

        使用当前子任务：有草稿的 episode 用草稿，其余用数据集中已写入的；不需要先生成新数据集。keep_language 决定是否保留语言标注列。"""
        current = self.current(dataset_id)
        dataset, meta, names, episodes = current["dataset"], current["meta"], current["names"], current["episodes"]
        template = kwargs.get("template") or DEFAULT_TASK_TEMPLATE
        if "{subtask}" not in template:
            raise ValueError("模板必须包含 {subtask}")
        try:
            template.format(task="", subtask="")
        except (KeyError, IndexError, ValueError) as error:
            raise ValueError(f"模板无效（只能使用 {{task}} 与 {{subtask}}）: {error}")
        texts = set()
        unlabeled = 0
        examples = []
        for index in range(meta.total_episodes):
            segments = episodes[index]["segments"]
            overall = "；".join(episodes[index]["edited_tasks"])
            if not segments:
                unlabeled += 1
                texts.update(episodes[index]["edited_tasks"])
                continue
            for segment in segments:
                text = template.format(task=overall, subtask=segment["text"])
                texts.add(text)
                if len(examples) < 5:
                    examples.append({"episode_index": index, "start": segment["start"], "task": text})
        output = resolve_output(self.output_dir, kwargs.get("output_name") or f"{dataset['name']}_task")
        blocking = []
        if unlabeled == meta.total_episodes:
            blocking.append("还没有子任务：请先在上方选择 episode，在当前帧切分、填写描述并保存草稿")
        preview = {
            "dataset": {"id": dataset["id"], "name": dataset["name"], "path": dataset["path"]},
            "template": template,
            "keep_language": bool(kwargs.get("keep_language", True)),
            "examples": examples,
            "task_count": len(texts),
            "original_task_count": len(names),
            "unlabeled_episodes": unlabeled,
            "draft_episodes": sum(1 for item in episodes.values() if item["status"] == "modified"),
            "episode_count": meta.total_episodes,
            **output_fields(self.output_dir, output, blocking),
        }
        return preview

    def submit_to_task(self, dataset_id, **kwargs):
        preview = self.preview_to_task(dataset_id, **kwargs)
        require_ready(preview)
        params = {
            "dataset_id": dataset_id,
            "dataset_path": preview["dataset"]["path"],
            "output_path": preview["output_path"],
            "template": preview["template"],
            "keep_language": preview["keep_language"],
            "segments": {str(index): segments for index, segments in self.storage.list_subtask_drafts(dataset_id).items()},
            "tasks": {str(index): edits for index, edits in self.storage.list_task_drafts(dataset_id).items()},
        }
        task = self.task_manager.submit(SUBTASK_TO_TASK_JOB, params)
        return task


def episode_timestamps(root):
    """{episode_index: 按 frame_index 排序的逐帧 timestamp（float32，与 parquet 中完全相同）}"""
    stamps = {}
    for path in data_files(root):
        frame = pd.read_parquet(path, columns=["episode_index", "frame_index", "timestamp"]).sort_values(["episode_index", "frame_index"])
        for episode, rows in frame.groupby("episode_index"):
            stamps[int(episode)] = rows["timestamp"].to_numpy()
    return stamps


def copy_dataset(source, target):
    """完整复制 LeRobot 数据集的标准目录（data、meta、videos），再在副本上改写。"""
    for name in ("data", "meta", "videos"):
        if (Path(source) / name).exists():
            shutil.copytree(Path(source) / name, Path(target) / name)


def build_language(source, drafts):
    """每个 episode 写入后的语言行：已有的非 subtask 行（plan、memory、事件等）原样保留；
    有草稿的 episode 用草稿替换 subtask 行，没有草稿的保留原 subtask 行。subtask 行的 timestamp 取起始帧的原始时间戳。"""
    stored = language_rows(source)
    stamps = episode_timestamps(source)
    rows = {}
    for index, frame_stamps in stamps.items():
        existing = stored.get(index, {"persistent": [], "events": []})
        persistent = [row for row in existing["persistent"] if row.get("style") != "subtask" or index not in drafts]
        for segment in drafts.get(index, []):
            persistent.append({"role": "assistant", "content": segment["text"], "style": "subtask",
                               "timestamp": float(frame_stamps[segment["start"]]), "camera": None, "tool_calls": None})
        rows[index] = {"persistent": persistent, "events": existing["events"]}
    return rows


def write_language(root, rows):
    """用官方 StagingValidator 检查暂存数据，再用 LanguageColumnsWriter 改写 data 文件，并登记语言 feature（同 lerobot-annotate）。"""
    from lerobot.annotations.steerable_pipeline.executor import Executor
    from lerobot.annotations.steerable_pipeline.reader import iter_episodes
    from lerobot.annotations.steerable_pipeline.staging import EpisodeStaging
    from lerobot.annotations.steerable_pipeline.validator import StagingValidator
    from lerobot.annotations.steerable_pipeline.writer import LanguageColumnsWriter

    staging_dir = Path(root) / ".subtask_staging"
    try:
        records = list(iter_episodes(Path(root)))
        for record in records:
            episode_rows = rows.get(record.episode_index, {"persistent": [], "events": []})
            # 官方暂存按模块分文件，校验要求 plan 模块只含持续行、interjections 模块只含事件行；写入时按 style 分到两列。
            staging = EpisodeStaging(staging_dir, record.episode_index)
            staging.write("plan", episode_rows["persistent"])
            staging.write("interjections", episode_rows["events"])
        report = StagingValidator().validate(records, staging_dir)
        if not report.ok:
            raise ValueError(f"官方暂存校验未通过（{len(report.errors)} 项）：{'；'.join(report.errors[:3])}")
        LanguageColumnsWriter().write_all(records, staging_dir, Path(root))
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)
    Executor._ensure_annotation_metadata_in_info(Path(root))
    result = {"warnings": report.warnings[:20]}
    return result


def compare_copy(temp, source, checks, skip_columns):
    """除 skip_columns 外全部数据列逐值一致，视频文件逐字节相同。"""
    for source_path, output_path in zip(data_files(source), data_files(temp)):
        expected, actual = pd.read_parquet(source_path), pd.read_parquet(output_path)
        name = output_path.relative_to(temp).as_posix()
        columns = [column for column in expected.columns if column not in skip_columns]
        same = len(actual) == len(expected) and all(columns_equal(actual[column].to_numpy(), expected[column].to_numpy()) for column in columns)
        checks.add(f"{name} 其他数据列逐值一致", same)
    videos = sorted(path.relative_to(source) for path in (Path(source) / "videos").rglob("*.mp4"))
    identical = all((temp / path).exists() and filecmp.cmp(Path(source) / path, temp / path, shallow=False) for path in videos)
    checks.add("视频文件逐字节相同", identical, f"{len(videos)} 个")


def task_edits_of(params):
    """params["tasks"]（JSON 键为字符串）→ {episode_index: {原任务文本: 新文本}}"""
    edits = {int(index): mapping for index, mapping in params.get("tasks", {}).items()}
    return edits


def check_frame_tasks(checks, temp, source, text_of):
    """逐帧核对输出的 task 文本：text_of(episode, frame, 源 task 文本) 为预期值；并核对官方读取的 item["task"]。"""
    source_names = task_names(load_metadata(source))
    reader = open_dataset(temp, repo_id=f"local/{Path(temp).name}")
    new_names = task_names(reader.meta)
    for source_path, output_path in zip(data_files(source), data_files(temp)):
        expected, actual = pd.read_parquet(source_path), pd.read_parquet(output_path)
        want = [text_of(int(episode), int(frame), source_names[int(old)])
                for episode, frame, old in zip(expected["episode_index"], expected["frame_index"], expected["task_index"])]
        got = [new_names[int(index)] for index in actual["task_index"]]
        checks.add(f"{output_path.relative_to(temp).as_posix()} 逐帧 task", got == want)
    probe = [0, len(reader) - 1]
    checks.add("官方读取的 item['task'] 与逐帧 task 一致", all(reader[index]["task"] == new_names[int(reader[index]["task_index"])] for index in probe))


def non_subtask_rows(rows):
    """子任务以外的持续标注行（plan、memory 等），按时间与 style 排序后比较。"""
    kept = sorted((row for row in rows if row.get("style") != "subtask"), key=lambda row: (float(row["timestamp"]), row.get("style") or ""))
    return kept


def validate_write(temp, params, expected_rows, text_of):
    """官方 LeRobotDataset 可打开；写入子任务时：语言 feature 已登记、逐 episode 子任务分段与草稿一致、其他语言标注保留；
    修改 task 时：逐帧 task 与预期一致；其他数据列与视频不变。expected_rows 为 None 表示没有写子任务，text_of 为 None 表示没有改 task。"""
    checks = Checks()
    source = Path(params["dataset_path"])
    reader = open_dataset(temp, repo_id=f"local/{Path(params['output_path']).name}")
    checks.add("官方 LeRobotDataset 可打开", reader.meta.info.codebase_version == "v3.0")
    skip = set()
    if expected_rows is not None:
        skip |= set(LANGUAGE_COLUMNS)
        checks.add("info.json 登记语言 feature", all(reader.meta.features.get(name, {}).get("dtype") == "language" for name in LANGUAGE_COLUMNS))
        written = language_rows(temp)
        stamps = episode_timestamps(temp)
        drafts = {int(index): segments for index, segments in params["segments"].items()}
        for index, expected in expected_rows.items():
            actual = written.get(index, {"persistent": [], "events": []})
            segments = subtask_segments(actual["persistent"], stamps[index])
            want = drafts.get(index, subtask_segments(expected["persistent"], stamps[index]))
            checks.add(f"episode_{index:06d} 子任务分段", segments == want, str(segments[:3]))
            checks.add(f"episode_{index:06d} 其他语言标注保留", non_subtask_rows(actual["persistent"]) == non_subtask_rows(expected["persistent"])
                       and len(actual["events"]) == len(expected["events"]))
    if text_of is not None:
        skip.add("task_index")
        check_frame_tasks(checks, temp, source, text_of)
    compare_copy(temp, source, checks, skip)
    summary = checks.summary(failed_limit=20)
    return summary


def subtask_write_job(params, reporter):
    """工作进程中运行：复制数据集 → 有子任务草稿时用官方写入器写入子任务 → 有 task 草稿时改写逐帧 task → 验证 → 发布 → 登记。

    只改 task 时不写语言标注列（输出不会多出语言 feature）。"""
    drafts = {int(index): segments for index, segments in params["segments"].items()}
    edits = task_edits_of(params)
    text_of = (lambda episode, frame, old: edits.get(episode, {}).get(old, old)) if edits else None
    written = {"warnings": []}
    rows = None
    with staged_output(params["output_path"], reporter) as (temp, output):
        reporter.progress(0, 3, "复制数据集")
        copy_dataset(params["dataset_path"], temp)
        reporter.check_cancel()
        if drafts:
            reporter.progress(1, 3, "写入子任务（官方 LanguageColumnsWriter）")
            rows = build_language(params["dataset_path"], drafts)
            written = write_language(temp, rows)
        if edits:
            reporter.progress(1, 3, "改写 task")
            rewrite_tasks(temp, text_of, drop_language=False)
        reporter.progress(2, 3, "验证输出")
        reporter.set_status("validating", "验证输出")
        validation = validate_write(temp, params, rows, text_of)
        require_passed(validation)
        publish(temp, output, reporter)
    dataset = reporter.register_dataset(output.name, output, LEROBOT_V3)
    result = {
        "output_path": str(output),
        "registered_dataset_id": dataset["id"],
        "episodes_written": len(set(drafts) | set(edits)),
        "segments_written": sum(len(segments) for segments in drafts.values()),
        "task_edit_episodes": len(edits),
        "validation": validation,
        "warnings": written["warnings"],
        "report": {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "source_dataset": params["dataset_path"],
            "operation": "写入子任务（官方 LanguageColumnsWriter，language_persistent 中 style=subtask 的行）与 task 修改",
            "segments": params["segments"],
            "tasks": params.get("tasks", {}),
        },
    }
    return result


def task_stats(values):
    """task_index 的统计，键与 v3.0 逐 episode 统计一致（min、max、mean、std、count 与 q01–q99）。"""
    values = np.asarray(values, dtype=np.float64)
    stats = {"min": [values.min()], "max": [values.max()], "mean": [values.mean()], "std": [values.std()], "count": [len(values)]}
    for name, quantile in (("q01", 0.01), ("q10", 0.10), ("q50", 0.50), ("q90", 0.90), ("q99", 0.99)):
        stats[name] = [float(np.quantile(values, quantile))]
    return stats


def rewrite_tasks(root, text_of, drop_language):
    """在 root（临时副本）上把每帧 task 改为 text_of(episode, frame, 原 task 文本)，并同步任务表、episode 任务列表、
    task_index 统计与 info。drop_language 为 True 时去掉语言标注列。返回新任务列表（按首次出现排序）。"""
    from lerobot.datasets.io_utils import write_tasks

    root = Path(root)
    order, episode_tasks, episode_values = rewrite_frame_tasks(root, text_of, drop_language)
    write_tasks(pd.DataFrame({"task_index": range(len(order))}, index=pd.Index(order, name="task")), root)
    rewrite_episode_tasks(root, episode_tasks, episode_values)
    rewrite_task_info(root, len(order), episode_values, drop_language)
    return order


def rewrite_frame_tasks(root, text_of, drop_language):
    """改写 data 文件的逐帧 task_index。返回新任务列表、每个 episode 用到的任务与逐帧 task_index。"""
    from lerobot.datasets.io_utils import write_table_one_row_group_per_episode

    names = task_names(load_metadata(root))
    order = []
    tables = {}
    for path in data_files(root):
        table = pq.read_table(path)
        episodes = table.column("episode_index").to_numpy()
        frames = table.column("frame_index").to_numpy()
        task_index = table.column("task_index").to_numpy()
        texts = [text_of(int(episode), int(frame), names[int(old)]) for episode, frame, old in zip(episodes, frames, task_index)]
        order.extend(text for text in dict.fromkeys(texts) if text not in order)
        tables[path] = (table, texts)
    new_index = {text: position for position, text in enumerate(order)}
    episode_tasks = {}
    episode_values = {}
    for path, (table, texts) in tables.items():
        values = np.array([new_index[text] for text in texts], dtype=np.int64)
        table = table.set_column(table.schema.get_field_index("task_index"), "task_index", pa.array(values))
        if drop_language:
            table = table.drop_columns([name for name in LANGUAGE_COLUMNS if name in table.column_names])
        write_table_one_row_group_per_episode(table, path)
        for episode, text, value in zip(table.column("episode_index").to_numpy(), texts, values):
            episode_tasks.setdefault(int(episode), [])
            if text not in episode_tasks[int(episode)]:
                episode_tasks[int(episode)].append(text)
            episode_values.setdefault(int(episode), []).append(value)
    result = (order, episode_tasks, episode_values)
    return result


def rewrite_episode_tasks(root, episode_tasks, episode_values):
    """改写 episode 元数据的任务列表与 task_index 统计。用 pyarrow 按原列类型替换，避免 pandas 回写改变 list 列的类型。"""
    for path in sorted((root / "meta" / "episodes").rglob("*.parquet")):
        table = pq.read_table(path)
        episodes = [int(episode) for episode in table.column("episode_index").to_pylist()]
        replaced = {"tasks": [episode_tasks[episode] for episode in episodes]}
        stats = [task_stats(episode_values[episode]) for episode in episodes]
        for name in stats[0] if stats else []:
            replaced[f"stats/task_index/{name}"] = [item[name] for item in stats]
        for column, values in replaced.items():
            if column in table.column_names:
                position = table.schema.get_field_index(column)
                table = table.set_column(position, table.schema.field(position), pa.array(values, type=table.schema.field(position).type))
        pq.write_table(table, path)


def rewrite_task_info(root, task_count, episode_values, drop_language):
    """更新 info.json 的任务数（去掉语言列时同时去掉其 feature）与 stats.json 中 task_index 的汇总统计。"""
    import json

    from lerobot.datasets.io_utils import load_info, write_info, write_stats

    info = load_info(root)
    info.total_tasks = task_count
    if drop_language:
        info.features = {key: value for key, value in info.features.items() if key not in LANGUAGE_COLUMNS}
    write_info(info, root)
    stats_path = root / "meta" / "stats.json"
    if stats_path.exists():
        stats = json.loads(stats_path.read_text(encoding="utf-8"))
        if "task_index" in stats:
            every = np.concatenate([np.asarray(values) for values in episode_values.values()])
            stats["task_index"] = {name: value for name, value in task_stats(every).items() if name in stats["task_index"]}
            write_stats({key: {name: np.asarray(value) for name, value in item.items()} for key, item in stats.items()}, root)


def frame_texts(meta, segments_of, template, edits, stamps):
    """有子任务的 episode 每帧的 task 文本：按模板组合修改后的整体描述与该帧所在段的子任务。"""
    texts_by_episode = {}
    for index in range(meta.total_episodes):
        segments = segments_of(index)
        if not segments:
            continue
        overall = "；".join(edits.get(index, {}).get(task, task) for task in meta.episodes[index]["tasks"])
        starts = [segment["start"] for segment in segments]
        texts = [template.format(task=overall, subtask=segment["text"]) for segment in segments]
        texts_by_episode[index] = [texts[int(np.searchsorted(starts, frame, side="right")) - 1] for frame in range(len(stamps[index]))]
    return texts_by_episode


def subtask_to_task_job(params, reporter):
    """工作进程中运行：复制数据集 → 每帧 task 改为当前子任务（按模板）→ 更新任务表、episode 任务列表与 task_index 统计 → 验证 → 发布 → 登记。

    params["segments"] 为子任务草稿、params["tasks"] 为 task 草稿（都可为空）：有草稿的 episode 用草稿，其余用已写入的内容；
    模板中的 {task} 与没有子任务的 episode 都使用修改后的 task。keep_language 为 True 时子任务草稿同时写入语言标注列，为 False 时去掉语言标注列。"""
    source = Path(params["dataset_path"])
    meta = load_metadata(source)
    stored = language_rows(source)
    stamps = episode_timestamps(source)
    drafts = {int(index): segments for index, segments in params.get("segments", {}).items()}
    edits = task_edits_of(params)
    template = params["template"]

    def segments_of(index):
        # 草稿为空列表表示用户清空了子任务，不能退回已写入的子任务（与预检 drafts.get(index, original) 一致）。
        if index in drafts:
            return drafts[index]
        segments = subtask_segments(stored[index]["persistent"], stamps[index]) if index in stored else []
        return segments

    frame_text = frame_texts(meta, segments_of, template, edits, stamps)

    def text_of(episode, frame, old):
        text = frame_text[episode][frame] if episode in frame_text else edits.get(episode, {}).get(old, old)
        return text

    with staged_output(params["output_path"], reporter) as (temp, output):
        reporter.progress(0, 3, "复制数据集")
        copy_dataset(source, temp)
        reporter.check_cancel()
        if drafts and params["keep_language"]:
            reporter.progress(1, 3, "写入子任务草稿（官方 LanguageColumnsWriter）")
            write_language(temp, build_language(source, drafts))
        reporter.progress(1, 3, "改写逐帧 task")
        order = rewrite_tasks(temp, text_of, drop_language=not params["keep_language"])
        reporter.progress(2, 3, "验证输出")
        reporter.set_status("validating", "验证输出")
        validation = validate_to_task(temp, params, text_of)
        require_passed(validation)
        publish(temp, output, reporter)
    dataset = reporter.register_dataset(output.name, output, LEROBOT_V3)
    result = {
        "output_path": str(output),
        "registered_dataset_id": dataset["id"],
        "task_count": len(order),
        "converted_episodes": len(frame_text),
        "kept_episodes": meta.total_episodes - len(frame_text),
        "keep_language": params["keep_language"],
        "validation": validation,
        "report": {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "source_dataset": params["dataset_path"],
            "operation": "子任务转为逐帧 task",
            "template": template,
            "keep_language": params["keep_language"],
            "draft_segments": params.get("segments", {}),
            "task_edits": params.get("tasks", {}),
        },
    }
    return result


def validate_to_task(temp, params, text_of):
    """官方 LeRobotDataset 可打开；每帧 task 为当前子任务（无子任务的 episode 为修改后的 task）；其他数据列与视频不变。"""
    checks = Checks()
    source = Path(params["dataset_path"])
    reader = open_dataset(temp, repo_id=f"local/{Path(params['output_path']).name}")
    checks.add("官方 LeRobotDataset 可打开", reader.meta.info.codebase_version == "v3.0")
    check_frame_tasks(checks, temp, source, text_of)
    # 语言列在写入草稿或去掉时会变化，不参与“其他列不变”的比较（草稿写入另由官方校验）。
    skip = {"task_index"} | (set(LANGUAGE_COLUMNS) if params.get("segments") or not params["keep_language"] else set())
    compare_copy(temp, source, checks, skip)
    summary = checks.summary(failed_limit=20)
    return summary


DEMO_OUTPUT = REPO_ROOT / "output" / "demo" / "subtask_editor"


def demo_subtask():
    """在合成的 3 个 episode 小样本上给 episode 0（30 帧）分 3 段子任务，写入新数据集，再转为逐帧 task；输出到独立目录。"""
    from component.common.dataset_access import DatasetAccess
    from component.common.task_manager import DemoReporter
    from component.dataset.dataset_manager import DatasetManager
    from util.lerobot_helper import create_demo_dataset
    from util.storage_helper import Storage

    shutil.rmtree(DEMO_OUTPUT, ignore_errors=True)
    DEMO_OUTPUT.mkdir(parents=True)
    storage = Storage(DEMO_OUTPUT / "demo.sqlite3")
    manager = DatasetManager(storage, DatasetAccess(), DEMO_OUTPUT)
    editor = SubtaskEditor(manager, storage, None, DEMO_OUTPUT / "output")
    dataset = manager.register(create_demo_dataset(DEMO_OUTPUT / "sample", ["put the red pepper on the red tray", "put the green pepper on the red tray", "wave hand"]))
    detail = editor.save(dataset["id"], 0, [{"start": 0, "text": "reach the pepper"}, {"start": 10, "text": "grasp the pepper"},
                                            {"start": 20, "text": "place it on the tray"}])
    logger.info("Draft segments: {}", detail["segments"])
    params = {"dataset_id": dataset["id"], "dataset_path": dataset["path"], "output_path": editor.preview_write(dataset["id"])["output_path"],
              "segments": {"0": [{key: segment[key] for key in ("start", "text")} for segment in detail["segments"]]}}
    result = subtask_write_job(params, DemoReporter())
    logger.info("Subtasks written: {} checks passed={}", result["validation"]["check_count"], result["validation"]["passed"])
    written = manager.register(result["output_path"])
    preview = editor.preview_to_task(written["id"], template="{task}. {subtask}")
    logger.info("To task examples: {}", preview["examples"])
    converted = subtask_to_task_job({"dataset_id": written["id"], "dataset_path": written["path"], "output_path": preview["output_path"],
                                     "template": preview["template"], "keep_language": True}, DemoReporter())
    logger.info("Per-frame task: {} tasks, checks {} passed={}", converted["task_count"], converted["validation"]["check_count"], converted["validation"]["passed"])


def main():
    demo_subtask()


if __name__ == "__main__":
    main()
