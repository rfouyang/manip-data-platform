import json
import sqlite3
from datetime import datetime
from pathlib import Path

from loguru import logger


SCHEMA = """
CREATE TABLE IF NOT EXISTS datasets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    path TEXT NOT NULL UNIQUE,
    format TEXT NOT NULL,
    created_at TEXT NOT NULL
)
"""

TASK_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    params TEXT NOT NULL,
    status TEXT NOT NULL,
    progress_done INTEGER NOT NULL DEFAULT 0,
    progress_total INTEGER NOT NULL DEFAULT 0,
    message TEXT NOT NULL DEFAULT '',
    result TEXT,
    error TEXT,
    retry_of INTEGER,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
)
"""
# 子任务编辑草稿：每个 (数据集, episode) 一行，segments 为 [{"start": 起始帧, "text": 描述}] 的 JSON；生成新数据集前不改数据文件。
SUBTASK_DRAFT_SCHEMA = """
CREATE TABLE IF NOT EXISTS subtask_drafts (
    dataset_id INTEGER NOT NULL,
    episode_index INTEGER NOT NULL,
    segments TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (dataset_id, episode_index)
)
"""
# task 编辑草稿：每个 (数据集, episode) 一行，tasks 为 {原任务文本: 新文本} 的 JSON（只含改动的任务）。
TASK_DRAFT_SCHEMA = """
CREATE TABLE IF NOT EXISTS task_drafts (
    dataset_id INTEGER NOT NULL,
    episode_index INTEGER NOT NULL,
    tasks TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (dataset_id, episode_index)
)
"""
TASK_COLUMNS = {"status", "progress_done", "progress_total", "message", "result", "error", "started_at", "finished_at"}


class Storage:
    """SQLite 持久化登记信息。每次操作单独打开连接，可在 FastAPI 线程池和后续工作进程中安全使用。"""

    def __init__(self, db_path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.execute(SCHEMA)
        self.execute(TASK_SCHEMA)
        self.execute(SUBTASK_DRAFT_SCHEMA)
        self.execute(TASK_DRAFT_SCHEMA)

    def execute(self, sql, params=()):
        """执行一条 SQL，返回 (结果行, 新插入行 id)。"""
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                cursor = connection.execute(sql, params)
                rows = [dict(row) for row in cursor.fetchall()]
                result = (rows, cursor.lastrowid)
        finally:
            connection.close()
        return result

    def execute_count(self, sql, params=()):
        """执行一条更新语句，返回受影响行数，用于带状态条件的原子更新。"""
        connection = sqlite3.connect(self.db_path)
        try:
            with connection:
                count = connection.execute(sql, params).rowcount
        finally:
            connection.close()
        return count

    def add_dataset(self, name, path, dataset_format):
        created_at = datetime.now().isoformat(timespec="seconds")
        sql = "INSERT INTO datasets (name, path, format, created_at) VALUES (?, ?, ?, ?)"
        _, dataset_id = self.execute(sql, (name, str(path), dataset_format, created_at))
        dataset = self.get_dataset(dataset_id)
        return dataset

    def list_datasets(self):
        rows, _ = self.execute("SELECT * FROM datasets ORDER BY id")
        return rows

    def get_dataset(self, dataset_id):
        rows, _ = self.execute("SELECT * FROM datasets WHERE id = ?", (dataset_id,))
        dataset = rows[0] if rows else None
        return dataset

    def delete_dataset(self, dataset_id):
        self.execute("DELETE FROM datasets WHERE id = ?", (dataset_id,))
        self.execute("DELETE FROM subtask_drafts WHERE dataset_id = ?", (dataset_id,))
        self.execute("DELETE FROM task_drafts WHERE dataset_id = ?", (dataset_id,))

    def find_dataset_by_path(self, path):
        rows, _ = self.execute("SELECT * FROM datasets WHERE path = ?", (str(path),))
        dataset = rows[0] if rows else None
        return dataset

    def save_subtask_draft(self, dataset_id, episode_index, segments):
        updated_at = datetime.now().isoformat(timespec="seconds")
        sql = ("INSERT INTO subtask_drafts (dataset_id, episode_index, segments, updated_at) VALUES (?, ?, ?, ?) "
               "ON CONFLICT (dataset_id, episode_index) DO UPDATE SET segments = excluded.segments, updated_at = excluded.updated_at")
        self.execute(sql, (dataset_id, episode_index, json.dumps(segments, ensure_ascii=False), updated_at))

    def list_subtask_drafts(self, dataset_id):
        """{episode_index: segments}"""
        rows, _ = self.execute("SELECT episode_index, segments FROM subtask_drafts WHERE dataset_id = ? ORDER BY episode_index", (dataset_id,))
        drafts = {row["episode_index"]: json.loads(row["segments"]) for row in rows}
        return drafts

    def delete_subtask_drafts(self, dataset_id, episode_indices=None):
        """删除草稿；episode_indices 为 None 时删除该数据集的全部草稿。返回删除行数。"""
        if episode_indices is None:
            count = self.execute_count("DELETE FROM subtask_drafts WHERE dataset_id = ?", (dataset_id,))
            return count
        count = 0
        for episode_index in episode_indices:
            count += self.execute_count("DELETE FROM subtask_drafts WHERE dataset_id = ? AND episode_index = ?", (dataset_id, episode_index))
        return count

    def save_task_draft(self, dataset_id, episode_index, tasks):
        updated_at = datetime.now().isoformat(timespec="seconds")
        sql = ("INSERT INTO task_drafts (dataset_id, episode_index, tasks, updated_at) VALUES (?, ?, ?, ?) "
               "ON CONFLICT (dataset_id, episode_index) DO UPDATE SET tasks = excluded.tasks, updated_at = excluded.updated_at")
        self.execute(sql, (dataset_id, episode_index, json.dumps(tasks, ensure_ascii=False), updated_at))

    def list_task_drafts(self, dataset_id):
        """{episode_index: {原任务文本: 新文本}}"""
        rows, _ = self.execute("SELECT episode_index, tasks FROM task_drafts WHERE dataset_id = ? ORDER BY episode_index", (dataset_id,))
        drafts = {row["episode_index"]: json.loads(row["tasks"]) for row in rows}
        return drafts

    def delete_task_drafts(self, dataset_id, episode_indices=None):
        """删除 task 草稿；episode_indices 为 None 时删除该数据集的全部 task 草稿。返回删除行数。"""
        if episode_indices is None:
            count = self.execute_count("DELETE FROM task_drafts WHERE dataset_id = ?", (dataset_id,))
            return count
        count = 0
        for episode_index in episode_indices:
            count += self.execute_count("DELETE FROM task_drafts WHERE dataset_id = ? AND episode_index = ?", (dataset_id, episode_index))
        return count

    def add_task(self, kind, params, **kwargs):
        created_at = datetime.now().isoformat(timespec="seconds")
        sql = "INSERT INTO tasks (kind, params, status, retry_of, created_at) VALUES (?, ?, 'queued', ?, ?)"
        _, task_id = self.execute(sql, (kind, json.dumps(params, ensure_ascii=False), kwargs.get("retry_of"), created_at))
        task = self.get_task(task_id)
        return task

    def get_task(self, task_id):
        rows, _ = self.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        task = decode_task(rows[0]) if rows else None
        return task

    def list_tasks(self, **kwargs):
        limit = int(kwargs.get("limit", 50))
        rows, _ = self.execute("SELECT * FROM tasks ORDER BY id DESC LIMIT ?", (limit,))
        tasks = [decode_task(row) for row in rows]
        return tasks

    def update_task(self, task_id, from_statuses=None, **fields):
        """更新任务字段。给出 from_statuses 时只在当前状态属于其中时更新，返回是否更新成功。"""
        unknown = set(fields) - TASK_COLUMNS
        if unknown:
            raise ValueError(f"未知任务字段: {sorted(unknown)}")
        if "result" in fields and fields["result"] is not None:
            fields["result"] = json.dumps(fields["result"], ensure_ascii=False)
        assignments = ", ".join(f"{name} = ?" for name in fields)
        sql = f"UPDATE tasks SET {assignments} WHERE id = ?"
        params = [*fields.values(), task_id]
        if from_statuses:
            sql += f" AND status IN ({', '.join('?' for _ in from_statuses)})"
            params += list(from_statuses)
        updated = self.execute_count(sql, params) > 0
        return updated

    def update_tasks_status(self, from_statuses, status, **fields):
        """批量改状态，用于服务启动时把未完成任务标为 interrupted。返回受影响任务数。"""
        assignments = ", ".join(["status = ?", *(f"{name} = ?" for name in fields)])
        placeholders = ", ".join("?" for _ in from_statuses)
        sql = f"UPDATE tasks SET {assignments} WHERE status IN ({placeholders})"
        count = self.execute_count(sql, [status, *fields.values(), *from_statuses])
        return count


def decode_task(row):
    task = dict(row)
    task["params"] = json.loads(task["params"])
    task["result"] = json.loads(task["result"]) if task["result"] else None
    return task


REPO_ROOT = Path(__file__).resolve().parents[1]
DEMO_OUTPUT = REPO_ROOT / "output" / "demo" / "storage_helper"


def demo_storage():
    db_path = DEMO_OUTPUT / "demo.sqlite3"
    db_path.unlink(missing_ok=True)
    storage = Storage(db_path)
    dataset = storage.add_dataset("demo", "/tmp/demo", "h5")
    assert storage.get_dataset(dataset["id"]) == dataset
    assert storage.find_dataset_by_path("/tmp/demo") == dataset
    assert storage.list_datasets() == [dataset]
    assert storage.get_dataset(999) is None
    storage.delete_dataset(dataset["id"])
    assert storage.list_datasets() == []
    dataset = storage.add_dataset("demo", "/tmp/demo", "h5")
    try:
        storage.add_dataset("again", "/tmp/demo", "h5")
        raise AssertionError("duplicate path should be rejected")
    except sqlite3.IntegrityError:
        pass
    task = storage.add_task("demo", {"seconds": 1})
    assert task["status"] == "queued" and task["params"] == {"seconds": 1}
    assert storage.update_task(task["id"], ["queued"], status="running")
    assert not storage.update_task(task["id"], ["queued"], status="running")
    storage.update_task(task["id"], status="succeeded", result={"ok": True})
    assert storage.get_task(task["id"])["result"] == {"ok": True}
    assert storage.list_tasks()[0]["id"] == task["id"]
    logger.info("Storage verified at {}: {}", db_path, dataset)


def main():
    demo_storage()


if __name__ == "__main__":
    main()
