from fastapi import APIRouter, Request
from loguru import logger
from pydantic import BaseModel


class SplitRequest(BaseModel):
    dataset_id: int
    mode: str
    episodes: str | None = None
    tasks: list[int] | None = None
    column: str | None = None
    values: list[str] | None = None
    output_name: str | None = None


def create_split_router():
    router = APIRouter(prefix="/api/split", tags=["split"])

    @router.get("/options")
    def split_options(request: Request, dataset_id: int):
        """选择项：任务列表与字符串列（如 tag）的取值，均带 episode 数与帧数。"""
        splitter = request.app.state.context.data_splitter
        options = splitter.options(dataset_id)
        return options

    @router.post("/preview")
    def preview_split(request: Request, body: SplitRequest):
        """抽取子数据集的预检，不写任何文件。"""
        splitter = request.app.state.context.data_splitter
        options = body.model_dump(exclude={"dataset_id", "mode"}, exclude_none=True)
        preview = splitter.preview(body.dataset_id, body.mode, **options)
        return preview

    @router.post("", status_code=202)
    def submit_split(request: Request, body: SplitRequest):
        """提交抽取子数据集任务，输出为新数据集；进度通过 /api/tasks 查询。"""
        splitter = request.app.state.context.data_splitter
        options = body.model_dump(exclude={"dataset_id", "mode"}, exclude_none=True)
        task = splitter.submit(body.dataset_id, body.mode, **options)
        return task

    return router


def demo_split_api():
    from pathlib import Path

    from fastapi.testclient import TestClient

    from app.main import create_app
    from config.settings import Settings

    import shutil

    from util.lerobot_helper import create_demo_dataset

    repo_root = Path(__file__).resolve().parents[3]
    demo_dir = repo_root / "output" / "demo" / "split_api"
    shutil.rmtree(demo_dir, ignore_errors=True)
    # 合成的 3 个 episode 小样本（含深度），不依赖外部样本；登记时的相对路径以 demo_dir 为基准。
    create_demo_dataset(demo_dir / "sample", ["put the red pepper on the red tray", "put the green pepper on the red tray", "wave hand"])
    app = create_app(Settings(db_path=demo_dir / "demo.sqlite3", data_dir=demo_dir, output_dir=demo_dir / "output"))

    with TestClient(app) as client:
        dataset_id = client.post("/api/datasets", json={"path": "sample"}).json()["id"]
        options = client.get("/api/split/options", params={"dataset_id": dataset_id}).json()
        assert options["episode_count"] == 3 and [task["episodes"] for task in options["tasks"]] == [1, 1, 1] and options["columns"] == {}
        subset = client.post("/api/split/preview", json={"dataset_id": dataset_id, "mode": "episodes", "episodes": "0, 2"}).json()
        assert subset["episodes"] == [0, 2] and subset["summary"]["unselected_count"] == 1 and subset["ready"]
        by_task = client.post("/api/split/preview", json={"dataset_id": dataset_id, "mode": "tasks", "tasks": [0]}).json()
        assert by_task["episodes"] == [0]
        out_of_range = client.post("/api/split/preview", json={"dataset_id": dataset_id, "mode": "episodes", "episodes": "5"})
        empty = client.post("/api/split", json={"dataset_id": dataset_id, "mode": "episodes", "episodes": ""})
        assert (out_of_range.status_code, empty.status_code) == (400, 400)
    logger.info("Split API verified: options {} tasks, preview {}", len(options["tasks"]), subset["summary"])


def main():
    demo_split_api()


if __name__ == "__main__":
    main()
