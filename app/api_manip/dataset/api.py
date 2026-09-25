from fastapi import APIRouter, Request
from loguru import logger
from pydantic import BaseModel


class RegisterRequest(BaseModel):
    path: str
    name: str | None = None


def create_dataset_router():
    router = APIRouter(prefix="/api/datasets", tags=["dataset"])

    @router.get("")
    def list_datasets(request: Request):
        datasets = request.app.state.context.dataset_manager.list()
        return datasets

    @router.post("", status_code=201)
    def register_dataset(request: Request, body: RegisterRequest):
        manager = request.app.state.context.dataset_manager
        dataset = manager.register(body.path, name=body.name)
        return dataset

    @router.get("/{dataset_id}")
    def inspect_dataset(request: Request, dataset_id: int):
        manager = request.app.state.context.dataset_manager
        detail = manager.inspect(dataset_id)
        return detail

    @router.get("/{dataset_id}/preview")
    def preview_dataset(request: Request, dataset_id: int, rows: int = 5):
        """字段定义、任务列表与第一个 episode 的前几行原始数据。"""
        manager = request.app.state.context.dataset_manager
        preview = manager.preview(dataset_id, rows=min(max(rows, 1), 50))
        return preview

    @router.delete("/{dataset_id}")
    def remove_dataset(request: Request, dataset_id: int):
        manager = request.app.state.context.dataset_manager
        dataset = manager.remove(dataset_id)
        return dataset

    @router.get("/{dataset_id}/episodes/{episode_id:path}")
    def read_episode(request: Request, dataset_id: int, episode_id: str):
        manager = request.app.state.context.dataset_manager
        episode = manager.episode(dataset_id, episode_id)
        return episode

    return router


def demo_dataset_api():
    from pathlib import Path

    from fastapi.testclient import TestClient

    from app.main import create_app
    from config.settings import Settings

    repo_root = Path(__file__).resolve().parents[3]
    db_path = repo_root / "output" / "demo" / "dataset_api" / "demo.sqlite3"
    db_path.unlink(missing_ok=True)
    app = create_app(Settings(db_path=db_path))

    with TestClient(app) as client:
        created = client.post("/api/datasets", json={"path": "place_red_pepper_on_red_tray"})
        assert created.status_code == 201, created.text
        dataset_id = created.json()["id"]

        duplicate = client.post("/api/datasets", json={"path": "place_red_pepper_on_red_tray"})
        missing = client.post("/api/datasets", json={"path": "no_such_dir"})
        assert duplicate.status_code == 409 and missing.status_code == 400

        listed = client.get("/api/datasets").json()
        assert [item["id"] for item in listed] == [dataset_id]

        detail = client.get(f"/api/datasets/{dataset_id}").json()
        episode_id = detail["episodes"][0]["id"]
        episode = client.get(f"/api/datasets/{dataset_id}/episodes/{episode_id}")
        assert episode.status_code == 200 and episode.json()["length"] > 0

        invalid_id = next(item["id"] for item in detail["episodes"] if item["status"] == "invalid")
        assert client.get(f"/api/datasets/{dataset_id}/episodes/{invalid_id}").status_code == 400
        assert client.get(f"/api/datasets/{dataset_id}/episodes/no/such").status_code == 404
        assert client.get("/api/datasets/999").status_code == 404
        preview = client.get(f"/api/datasets/{dataset_id}/preview").json()
        assert len(preview["rows"]) == 5 and preview["tasks"][0]["task"] and client.get("/api/datasets/999/preview").status_code == 404

        assert client.delete(f"/api/datasets/{dataset_id}").status_code == 200
        assert client.get("/api/datasets").json() == []
        assert client.delete(f"/api/datasets/{dataset_id}").status_code == 404
        assert (repo_root / "data" / "place_red_pepper_on_red_tray").is_dir()

    logger.info("Dataset API verified: {} episodes, first {}", detail["episode_count"], episode_id)


def main():
    demo_dataset_api()


if __name__ == "__main__":
    main()
