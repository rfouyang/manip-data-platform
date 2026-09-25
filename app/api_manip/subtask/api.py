from fastapi import APIRouter, Request
from loguru import logger
from pydantic import BaseModel


class Segment(BaseModel):
    start: int
    text: str


class SegmentsBody(BaseModel):
    segments: list[Segment]
    # 与 episode 原任务列表一一对应的新 task 描述；不传表示不改 task。
    tasks: list[str] | None = None


class DiscardBody(BaseModel):
    episode_indices: list[int] | None = None


class ReplaceBody(BaseModel):
    find: str
    replace: str = ""
    regex: bool = False
    scope: str = "subtask"


class ImportBody(BaseModel):
    rows: list[dict]


class WriteRequest(BaseModel):
    dataset_id: int
    output_name: str | None = None


class ToTaskRequest(BaseModel):
    dataset_id: int
    template: str | None = None
    keep_language: bool = True
    output_name: str | None = None


def create_subtask_router():
    router = APIRouter(prefix="/api/subtask", tags=["subtask"])

    @router.get("/{dataset_id}/episodes")
    def list_episodes(request: Request, dataset_id: int):
        """episode 列表与子任务状态（modified 草稿 / labeled 已标注 / unlabeled 未标注）。"""
        editor = request.app.state.context.subtask_editor
        summary = editor.episodes(dataset_id)
        return summary

    @router.get("/{dataset_id}/episodes/{episode_index}")
    def get_episode(request: Request, dataset_id: int, episode_index: int):
        editor = request.app.state.context.subtask_editor
        detail = editor.episode(dataset_id, episode_index)
        return detail

    @router.put("/{dataset_id}/episodes/{episode_index}")
    def save_episode(request: Request, dataset_id: int, episode_index: int, body: SegmentsBody):
        """保存子任务与 task 为草稿，不改数据文件。"""
        editor = request.app.state.context.subtask_editor
        segments = [segment.model_dump() for segment in body.segments]
        detail = editor.save(dataset_id, episode_index, segments, body.tasks)
        return detail

    @router.post("/{dataset_id}/discard")
    def discard(request: Request, dataset_id: int, body: DiscardBody):
        editor = request.app.state.context.subtask_editor
        result = editor.discard(dataset_id, body.episode_indices)
        return result

    @router.post("/{dataset_id}/replace")
    def replace_text(request: Request, dataset_id: int, body: ReplaceBody):
        editor = request.app.state.context.subtask_editor
        result = editor.replace_text(dataset_id, body.find, body.replace, regex=body.regex, scope=body.scope)
        return result

    @router.get("/{dataset_id}/export")
    def export_rows(request: Request, dataset_id: int):
        editor = request.app.state.context.subtask_editor
        rows = editor.export_rows(dataset_id)
        return rows

    @router.post("/{dataset_id}/import")
    def import_rows(request: Request, dataset_id: int, body: ImportBody):
        editor = request.app.state.context.subtask_editor
        result = editor.import_rows(dataset_id, body.rows)
        return result

    @router.post("/write/preview")
    def preview_write(request: Request, body: WriteRequest):
        editor = request.app.state.context.subtask_editor
        preview = editor.preview_write(body.dataset_id, **body.model_dump(exclude={"dataset_id"}, exclude_none=True))
        return preview

    @router.post("/write", status_code=202)
    def submit_write(request: Request, body: WriteRequest):
        """把草稿写入新数据集（子任务写官方语言标注列，task 修改写任务表与逐帧 task_index）；进度通过 /api/tasks 查询。"""
        editor = request.app.state.context.subtask_editor
        task = editor.submit_write(body.dataset_id, **body.model_dump(exclude={"dataset_id"}, exclude_none=True))
        return task

    @router.post("/to-task/preview")
    def preview_to_task(request: Request, body: ToTaskRequest):
        editor = request.app.state.context.subtask_editor
        preview = editor.preview_to_task(body.dataset_id, **body.model_dump(exclude={"dataset_id"}, exclude_none=True))
        return preview

    @router.post("/to-task", status_code=202)
    def submit_to_task(request: Request, body: ToTaskRequest):
        """子任务转为逐帧 task，输出为新数据集。"""
        editor = request.app.state.context.subtask_editor
        task = editor.submit_to_task(body.dataset_id, **body.model_dump(exclude={"dataset_id"}, exclude_none=True))
        return task

    return router


def demo_subtask_api():
    from pathlib import Path

    from fastapi.testclient import TestClient

    from app.main import create_app
    from config.settings import Settings

    import shutil

    from util.lerobot_helper import create_demo_dataset

    repo_root = Path(__file__).resolve().parents[3]
    demo_dir = repo_root / "output" / "demo" / "subtask_api"
    shutil.rmtree(demo_dir, ignore_errors=True)
    # 合成的 3 个 episode 小样本（30、40、50 帧），不依赖外部样本；登记时的相对路径以 demo_dir 为基准。
    create_demo_dataset(demo_dir / "sample", ["put the red pepper on the red tray", "put the green pepper on the red tray", "wave hand"])
    app = create_app(Settings(db_path=demo_dir / "demo.sqlite3", data_dir=demo_dir, output_dir=demo_dir / "output"))

    with TestClient(app) as client:
        dataset_id = client.post("/api/datasets", json={"path": "sample"}).json()["id"]
        summary = client.get(f"/api/subtask/{dataset_id}/episodes").json()
        assert summary["counts"]["modified"] == 0
        saved = client.put(f"/api/subtask/{dataset_id}/episodes/0", json={"segments": [{"start": 0, "text": "reach"}, {"start": 15, "text": "grasp"}]})
        assert saved.status_code == 200 and saved.json()["status"] == "modified"
        bad = client.put(f"/api/subtask/{dataset_id}/episodes/0", json={"segments": [{"start": 5, "text": "reach"}]})
        assert bad.status_code == 400
        replaced = client.post(f"/api/subtask/{dataset_id}/replace", json={"find": "grasp", "replace": "grasp the pepper"}).json()
        assert replaced["changed_episodes"] == [0]
        rows = client.get(f"/api/subtask/{dataset_id}/export").json()
        assert [row["text"] for row in rows] == ["reach", "grasp the pepper"]
        preview = client.post("/api/subtask/write/preview", json={"dataset_id": dataset_id}).json()
        assert preview["ready"] and preview["counts"]["modified"] == 1
        # 转逐帧 task 直接使用草稿；task 可单独修改（不产生子任务草稿）。
        to_task = client.post("/api/subtask/to-task/preview", json={"dataset_id": dataset_id}).json()
        assert to_task["ready"] and to_task["draft_episodes"] == 1
        task_only = client.put(f"/api/subtask/{dataset_id}/episodes/1", json={"segments": [], "tasks": ["put the pepper on the tray"]}).json()
        assert task_only["edited_tasks"] == ["put the pepper on the tray"] and task_only["segments"] == []
        discarded = client.post(f"/api/subtask/{dataset_id}/discard", json={}).json()
        assert discarded["discarded"] == 2
    logger.info("Subtask API verified: {} episodes, export {}", len(summary["episodes"]), rows)


def main():
    demo_subtask_api()


if __name__ == "__main__":
    main()
