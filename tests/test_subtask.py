import filecmp
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from component.common.dataset_access import DatasetAccess
from component.conversion.version_converter import dropped_features
from component.dataset.dataset_manager import DatasetManager
from component.merge.data_merger import episode_fingerprints
from component.subtask.subtask_editor import (
    SubtaskEditor,
    build_language,
    check_segments,
    subtask_to_task_job,
    subtask_write_job,
    write_language,
)
from tests.test_data_tagger import make_tasks_v3
from tests.test_lerobot_conversion import Reporter
from util.lerobot_helper import LANGUAGE_COLUMNS, language_rows, load_metadata, open_dataset, task_names
from util.storage_helper import Storage

# make_tasks_v3：4 个 episode，长度 3、4、5、6 帧，30 FPS。
DRAFT = [{"start": 0, "text": "reach the cup"}, {"start": 2, "text": "grasp the cup"}]


@pytest.fixture
def editor(tmp_path):
    make_tasks_v3(tmp_path / "source")
    storage = Storage(tmp_path / "test.sqlite3")
    manager = DatasetManager(storage, DatasetAccess(), tmp_path)
    editor = SubtaskEditor(manager, storage, None, tmp_path / "out")
    editor.manager = manager
    editor.dataset_id = manager.register(tmp_path / "source")["id"]
    return editor


def write_params(editor, dataset_id, **kwargs):
    preview = editor.preview_write(dataset_id, **kwargs)
    assert preview["ready"], preview["blocking"]
    params = {"dataset_id": dataset_id, "dataset_path": preview["dataset"]["path"], "output_path": preview["output_path"],
              "segments": {str(index): segments for index, segments in editor.storage.list_subtask_drafts(dataset_id).items()}}
    return params


def test_check_segments_rules():
    assert check_segments([{"start": 0, "text": " a "}, {"start": 2, "text": "b"}], 4) == [{"start": 0, "text": "a"}, {"start": 2, "text": "b"}]
    assert check_segments([], 4) == []
    for segments, message in [([{"start": 1, "text": "a"}], "第 0 帧"), ([{"start": 0, "text": "a"}, {"start": 0, "text": "b"}], "严格递增"),
                              ([{"start": 0, "text": "a"}, {"start": 4, "text": "b"}], "超出"), ([{"start": 0, "text": " "}], "不能为空")]:
        with pytest.raises(ValueError, match=message):
            check_segments(segments, 4)


def test_drafts_replace_import_export_and_discard(editor):
    dataset_id = editor.dataset_id
    assert editor.episodes(dataset_id)["counts"] == {"modified": 0, "labeled": 0, "unlabeled": 4}
    detail = editor.save(dataset_id, 1, DRAFT)
    assert detail["status"] == "modified" and [segment["end"] for segment in detail["segments"]] == [1, 3]
    editor.save(dataset_id, 3, [{"start": 0, "text": "grasp the cup"}])
    # 常用子任务按使用段数降序（页面标签与下拉建议的来源）。
    assert editor.episodes(dataset_id)["subtask_texts"] == [{"text": "grasp the cup", "count": 2}, {"text": "reach the cup", "count": 1}]
    editor.discard(dataset_id, [3])
    assert editor.replace_text(dataset_id, "cup", "bottle")["changed_episodes"] == [1]
    assert [row["text"] for row in editor.export_rows(dataset_id)] == ["reach the bottle", "grasp the bottle"]
    # 任一 episode 不合法时全部不导入。
    with pytest.raises(ValueError, match="episode 3"):
        editor.import_rows(dataset_id, [{"episode_index": 2, "start_frame": 0, "text": "a"}, {"episode_index": 3, "start_frame": 9, "text": "b"}])
    assert sorted(editor.storage.list_subtask_drafts(dataset_id)) == [1]
    imported = editor.import_rows(dataset_id, [{"episode_index": "2", "start_frame": "3", "text": "b"}, {"episode_index": "2", "start_frame": "0", "text": "a"}])
    assert imported["imported_episodes"] == [2] and editor.episode(dataset_id, 2)["segments"][1]["start"] == 3
    assert editor.discard(dataset_id, [2])["discarded"] == 1
    assert editor.episodes(dataset_id)["counts"]["modified"] == 1


def test_replace_is_all_or_nothing(editor):
    """替换后任一 episode 不合法（描述变空、正则无效）时不写入任何草稿。"""
    dataset_id = editor.dataset_id
    editor.save(dataset_id, 1, DRAFT)
    editor.save(dataset_id, 3, [{"start": 0, "text": "cup"}])
    before = editor.storage.list_subtask_drafts(dataset_id)
    with pytest.raises(ValueError, match="episode 3"):
        editor.replace_text(dataset_id, "cup", "")
    with pytest.raises(ValueError, match="正则表达式无效"):
        editor.replace_text(dataset_id, "(", "x", regex=True)
    with pytest.raises(ValueError, match="episode 1"):
        editor.replace_text(dataset_id, "(cup)", r"\2", regex=True)
    assert editor.storage.list_subtask_drafts(dataset_id) == before


def test_write_job_uses_official_format_and_keeps_other_data(editor, tmp_path):
    editor.save(editor.dataset_id, 1, DRAFT)
    params = write_params(editor, editor.dataset_id)
    result = subtask_write_job(params, Reporter())
    assert result["validation"]["passed"], result["validation"]["failed"]
    output = tmp_path / "out" / "source_subtask"
    reader = open_dataset(output)
    assert all(reader.meta.features[name]["dtype"] == "language" for name in LANGUAGE_COLUMNS)
    # 官方读取：episode 1 从全局第 3 帧开始；每帧都带完整的子任务行，timestamp 等于起始帧的时间戳。
    item = reader[3 + 2]
    assert item["task"] == "Pick up the green cup"
    assert [(row["content"], row["style"], row["role"]) for row in item["language_persistent"]] == [
        ("reach the cup", "subtask", "assistant"), ("grasp the cup", "subtask", "assistant")]
    assert float(item["language_persistent"][1]["timestamp"]) == float(reader[3 + 2]["timestamp"])
    assert list(reader[0]["language_persistent"]) == []
    for path in (tmp_path / "source" / "videos").rglob("*.mp4"):
        assert filecmp.cmp(path, output / path.relative_to(tmp_path / "source"), shallow=False)
    assert LANGUAGE_COLUMNS[0] not in pd.read_parquet(next((tmp_path / "source" / "data").rglob("*.parquet"))).columns


def test_rewrite_keeps_other_annotations_and_unmodified_subtasks(editor, tmp_path):
    """已有标注的数据集：只替换有草稿 episode 的 subtask 行；plan、事件与其他 episode 的子任务原样保留。"""
    labeled = tmp_path / "labeled"
    make_tasks_v3(labeled)
    rows = build_language(labeled, {0: [{"start": 0, "text": "old 0"}], 2: [{"start": 0, "text": "old 2a"}, {"start": 3, "text": "old 2b"}]})
    rows[0]["persistent"].append({"role": "assistant", "content": "plan text", "style": "plan", "timestamp": 0.0, "camera": None, "tool_calls": None})
    # 官方规则：事件 timestamp 与帧时间戳（float32）完全相同；插话须配一条同时刻的语音行（style 为空）与 plan 更新。
    moment = float(np.float32(2 / 30))
    rows[0]["persistent"].append({"role": "assistant", "content": "new plan", "style": "plan", "timestamp": moment, "camera": None, "tool_calls": None})
    rows[0]["events"] += [{"role": "user", "content": "stop", "style": "interjection", "timestamp": moment, "camera": None, "tool_calls": None},
                          {"role": "assistant", "content": None, "style": None, "timestamp": moment, "camera": None,
                           "tool_calls": [{"type": "function", "function": {"name": "say", "arguments": {"text": "ok"}}}]}]
    write_language(labeled, rows)
    dataset_id = editor.manager.register(labeled)["id"]
    status = {item["episode_index"]: item["status"] for item in editor.episodes(dataset_id)["episodes"]}
    assert status == {0: "labeled", 1: "unlabeled", 2: "labeled", 3: "unlabeled"}
    editor.save(dataset_id, 0, [{"start": 0, "text": "new 0a"}, {"start": 1, "text": "new 0b"}])
    result = subtask_write_job(write_params(editor, dataset_id), Reporter())
    assert result["validation"]["passed"], result["validation"]["failed"]
    written = language_rows(tmp_path / "out" / "labeled_subtask")
    assert sorted((row["style"], row["content"]) for row in written[0]["persistent"]) == [
        ("plan", "new plan"), ("plan", "plan text"), ("subtask", "new 0a"), ("subtask", "new 0b")]
    # 同一时刻的事件行顺序由官方写入器决定，这里不比较顺序。
    assert sorted((row["style"] or "", row["content"] or "") for row in written[0]["events"]) == [("", ""), ("interjection", "stop")]
    assert [row["content"] for row in written[2]["persistent"]] == ["old 2a", "old 2b"]


def test_subtask_to_task_and_other_features(editor, tmp_path):
    editor.save(editor.dataset_id, 1, DRAFT)
    written = subtask_write_job(write_params(editor, editor.dataset_id), Reporter())
    labeled_id = editor.manager.register(written["output_path"])["id"]
    preview = editor.preview_to_task(labeled_id, template="{task}: {subtask}", keep_language=False)
    assert preview["ready"] and preview["unlabeled_episodes"] == 3 and preview["task_count"] == 4
    params = {"dataset_id": labeled_id, "dataset_path": preview["dataset"]["path"], "output_path": preview["output_path"],
              "template": preview["template"], "keep_language": False}
    result = subtask_to_task_job(params, Reporter())
    assert result["validation"]["passed"], result["validation"]["failed"]
    reader = open_dataset(result["output_path"])
    assert [reader[index]["task"] for index in range(3, 7)] == ["Pick up the green cup: reach the cup"] * 2 + ["Pick up the green cup: grasp the cup"] * 2
    assert reader[0]["task"] == "pick up the red cup" and LANGUAGE_COLUMNS[0] not in reader.meta.features
    meta = load_metadata(result["output_path"])
    assert list(meta.episodes[1]["tasks"]) == ["Pick up the green cup: reach the cup", "Pick up the green cup: grasp the cup"]
    assert len(task_names(meta)) == meta.total_tasks == 4
    # 语言列：转 v2.1 时去掉；合并的重复检测按内容区分含子任务的 episode。
    source_meta = load_metadata(written["output_path"])
    assert set(LANGUAGE_COLUMNS) <= set(dropped_features(source_meta, "lerobot_v2.1"))
    fingerprints = episode_fingerprints(written["output_path"], source_meta)
    assert fingerprints[0] != fingerprints[2] and len(set(fingerprints.values())) == 4


def test_subtask_to_task_uses_drafts_directly(editor, tmp_path):
    """草稿不需要先生成新数据集：转逐帧 task 直接使用草稿，保留语言列时草稿同时写入。"""
    assert not editor.preview_to_task(editor.dataset_id)["ready"]
    editor.save(editor.dataset_id, 1, DRAFT)
    preview = editor.preview_to_task(editor.dataset_id)
    assert preview["ready"] and preview["draft_episodes"] == 1 and preview["task_count"] == 4
    params = {"dataset_id": editor.dataset_id, "dataset_path": preview["dataset"]["path"], "output_path": preview["output_path"],
              "template": preview["template"], "keep_language": True,
              "segments": {str(index): segments for index, segments in editor.storage.list_subtask_drafts(editor.dataset_id).items()}}
    result = subtask_to_task_job(params, Reporter())
    assert result["validation"]["passed"], result["validation"]["failed"]
    reader = open_dataset(result["output_path"])
    assert [reader[index]["task"] for index in range(3, 7)] == ["reach the cup"] * 2 + ["grasp the cup"] * 2
    assert [row["content"] for row in reader[3]["language_persistent"]] == ["reach the cup", "grasp the cup"]


def test_subtask_to_task_respects_cleared_draft(editor):
    """清空已写入子任务后保存的空草稿：转逐帧 task 按未标注处理，不能退回已写入的子任务。"""
    editor.save(editor.dataset_id, 1, DRAFT)
    editor.save(editor.dataset_id, 2, DRAFT)
    written = subtask_write_job(write_params(editor, editor.dataset_id), Reporter())
    labeled_id = editor.manager.register(written["output_path"])["id"]
    editor.save(labeled_id, 1, [])
    assert editor.storage.list_subtask_drafts(labeled_id) == {1: []}
    preview = editor.preview_to_task(labeled_id, template="{task}: {subtask}", keep_language=False)
    assert preview["ready"] and preview["unlabeled_episodes"] == 3
    params = {"dataset_id": labeled_id, "dataset_path": preview["dataset"]["path"], "output_path": preview["output_path"],
              "template": preview["template"], "keep_language": False,
              "segments": {str(index): segments for index, segments in editor.storage.list_subtask_drafts(labeled_id).items()}}
    result = subtask_to_task_job(params, Reporter())
    assert result["validation"]["passed"], result["validation"]["failed"]
    reader = open_dataset(result["output_path"])
    assert [reader[index]["task"] for index in range(3, 7)] == ["Pick up the green cup"] * 4
    assert reader[7]["task"] == "pick up the red cup: reach the cup"


def test_task_only_edit_writes_tasks_without_language_columns(editor, tmp_path):
    """只改 task、不加子任务：不产生子任务草稿，输出不加语言列；逐帧 task、任务表与 episode 任务列表按修改。"""
    detail = editor.save(editor.dataset_id, 1, [], ["pick up the green mug"])
    assert detail["status"] == "modified" and detail["edited_tasks"] == ["pick up the green mug"] and detail["tasks"] == ["Pick up the green cup"]
    assert editor.storage.list_subtask_drafts(editor.dataset_id) == {}
    with pytest.raises(ValueError, match="不能为空"):
        editor.save(editor.dataset_id, 1, [], [" "])
    preview = editor.preview_write(editor.dataset_id)
    assert preview["ready"] and preview["task_edit_episodes"] == 1 and preview["subtask_edit_episodes"] == 0
    params = write_params(editor, editor.dataset_id)
    params["tasks"] = {str(index): edits for index, edits in editor.storage.list_task_drafts(editor.dataset_id).items()}
    result = subtask_write_job(params, Reporter())
    assert result["validation"]["passed"], result["validation"]["failed"]
    reader = open_dataset(result["output_path"])
    assert LANGUAGE_COLUMNS[0] not in reader.meta.features
    assert [reader[index]["task"] for index in (0, 3, 7)] == ["pick up the red cup", "pick up the green mug", "pick up the red cup"]
    meta = load_metadata(result["output_path"])
    assert sorted(task_names(meta).values()) == ["pick up the green mug", "pick up the red cup", "wave hand"]
    assert list(meta.episodes[1]["tasks"]) == ["pick up the green mug"]
    for path in (tmp_path / "source" / "videos").rglob("*.mp4"):
        assert filecmp.cmp(path, Path(result["output_path"]) / path.relative_to(tmp_path / "source"), shallow=False)
    # 批量替换 task；放弃后恢复原描述。
    assert editor.replace_text(editor.dataset_id, "red cup", "red mug", scope="task")["changed_episodes"] == [0, 2]
    assert editor.discard(editor.dataset_id)["discarded"] == 3
    assert editor.episode(editor.dataset_id, 1)["edited_tasks"] == ["Pick up the green cup"]


def test_task_and_subtask_edits_together(editor):
    """同一 episode 同时改 task 与子任务：生成新数据集两者都写入；转逐帧 task 的 {task} 使用修改后的描述。"""
    editor.save(editor.dataset_id, 1, DRAFT, ["pick up the green mug"])
    params = write_params(editor, editor.dataset_id)
    params["tasks"] = {str(index): edits for index, edits in editor.storage.list_task_drafts(editor.dataset_id).items()}
    result = subtask_write_job(params, Reporter())
    assert result["validation"]["passed"], result["validation"]["failed"]
    reader = open_dataset(result["output_path"])
    assert reader[3]["task"] == "pick up the green mug" and [row["content"] for row in reader[3]["language_persistent"]] == ["reach the cup", "grasp the cup"]
    preview = editor.preview_to_task(editor.dataset_id, template="{task}: {subtask}", output_name="to_task")
    assert preview["examples"][0]["task"] == "pick up the green mug: reach the cup"
