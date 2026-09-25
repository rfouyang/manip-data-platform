from fastapi import APIRouter, HTTPException, Request
from loguru import logger


def create_task_router():
    """公共任务接口：查询、取消、重试。提交由各业务 API 负责。"""
    router = APIRouter(prefix="/api/tasks", tags=["task"])

    @router.get("")
    def list_tasks(request: Request, limit: int = 50):
        tasks = request.app.state.context.task_manager.list(limit=limit)
        return tasks

    @router.get("/{task_id}")
    def get_task(request: Request, task_id: int):
        try:
            task = request.app.state.context.task_manager.get(task_id)
        except LookupError as error:
            raise HTTPException(404, str(error))
        return task

    @router.post("/{task_id}/cancel")
    def cancel_task(request: Request, task_id: int):
        try:
            task = request.app.state.context.task_manager.cancel(task_id)
        except LookupError as error:
            raise HTTPException(404, str(error))
        except ValueError as error:
            raise HTTPException(409, str(error))
        return task

    @router.post("/{task_id}/retry", status_code=202)
    def retry_task(request: Request, task_id: int):
        try:
            task = request.app.state.context.task_manager.retry(task_id)
        except LookupError as error:
            raise HTTPException(404, str(error))
        except ValueError as error:
            raise HTTPException(409, str(error))
        return task

    return router


def demo_task_api():
    import time
    from pathlib import Path

    from fastapi.testclient import TestClient

    from app.main import create_app
    from config.settings import Settings

    repo_root = Path(__file__).resolve().parents[2]
    demo_dir = repo_root / "output" / "demo" / "task_api"
    db_path = demo_dir / "demo.sqlite3"
    db_path.unlink(missing_ok=True)
    app = create_app(Settings(db_path=db_path, output_dir=demo_dir / "output"))

    with TestClient(app) as client:
        dataset_id = client.post("/api/datasets", json={"path": "place_red_pepper_on_red_tray"}).json()["id"]
        submitted = client.post("/api/conversion/frame-count", json={"dataset_id": dataset_id})
        assert submitted.status_code == 202
        task_id = submitted.json()["id"]
        task = client.get(f"/api/tasks/{task_id}").json()
        while task["progress_done"] < 3:
            time.sleep(0.2)
            task = client.get(f"/api/tasks/{task_id}").json()
        cancelled = client.post(f"/api/tasks/{task_id}/cancel").json()
        assert cancelled["status"] in ("cancelling", "cancelled")
        while task["status"] != "cancelled":
            time.sleep(0.2)
            task = client.get(f"/api/tasks/{task_id}").json()
        retried = client.post(f"/api/tasks/{task_id}/retry")
        assert retried.status_code == 202 and retried.json()["params"] == task["params"]
        assert client.post(f"/api/tasks/{task_id}/cancel").status_code == 409
        assert client.get("/api/tasks/999").status_code == 404
        tasks = client.get("/api/tasks").json()
    logger.info("Task API verified: cancelled at {}/{}, retry task {}, {} tasks listed",
                task["progress_done"], task["progress_total"], retried.json()["id"], len(tasks))


def main():
    demo_task_api()


if __name__ == "__main__":
    main()
