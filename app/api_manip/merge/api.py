from fastapi import APIRouter, Request
from loguru import logger
from pydantic import BaseModel


class MergeRequest(BaseModel):
    dataset_ids: list[int]
    output_name: str | None = None


def create_merge_router():
    router = APIRouter(prefix="/api/merge", tags=["merge"])

    @router.post("/preview")
    def preview_merge(request: Request, body: MergeRequest):
        """合并预检：字段、FPS、机器人类型逐项比对，重复 episode 检测，不写任何文件。dataset_ids 的顺序即合并顺序。"""
        merger = request.app.state.context.data_merger
        options = body.model_dump(exclude={"dataset_ids"}, exclude_none=True)
        preview = merger.preview(body.dataset_ids, **options)
        return preview

    @router.post("", status_code=202)
    def submit_merge(request: Request, body: MergeRequest):
        """提交合并任务，输出为新数据集；进度通过 /api/tasks 查询。"""
        merger = request.app.state.context.data_merger
        options = body.model_dump(exclude={"dataset_ids"}, exclude_none=True)
        task = merger.submit(body.dataset_ids, **options)
        return task

    return router


def demo_merge_api():
    from pathlib import Path

    from fastapi.testclient import TestClient

    from app.main import create_app
    from config.settings import Settings

    import shutil

    from util.lerobot_helper import create_demo_dataset

    repo_root = Path(__file__).resolve().parents[3]
    demo_dir = repo_root / "output" / "demo" / "merge_api"
    shutil.rmtree(demo_dir, ignore_errors=True)
    # 合成的 3 个 episode 小样本（含深度），不依赖外部样本；登记时的相对路径以 demo_dir 为基准。
    create_demo_dataset(demo_dir / "sample", ["put the red pepper on the red tray", "put the green pepper on the red tray", "wave hand"])
    app = create_app(Settings(db_path=demo_dir / "demo.sqlite3", data_dir=demo_dir, output_dir=demo_dir / "output"))
    # 另转出一个 v2.1 数据集，用于验证只接受 v3.0。
    from component.common.task_manager import DemoReporter
    from component.conversion.version_converter import dropped_features, v3_to_v21_job
    from util.lerobot_helper import load_metadata

    meta = load_metadata(demo_dir / "sample")
    v3_to_v21_job({"dataset_path": str(demo_dir / "sample"), "output_path": str(demo_dir / "sample_v21"), "episode_count": meta.total_episodes,
                   "dropped_features": dropped_features(meta, "lerobot_v2.1")}, DemoReporter())

    with TestClient(app) as client:
        sample = client.post("/api/datasets", json={"path": "sample"}).json()["id"]
        v21 = client.post("/api/datasets", json={"path": "sample_v21"}).json()["id"]
        single = client.post("/api/merge/preview", json={"dataset_ids": [sample]})
        twice = client.post("/api/merge/preview", json={"dataset_ids": [sample, sample]})
        wrong_format = client.post("/api/merge/preview", json={"dataset_ids": [sample, v21]})
        assert (single.status_code, twice.status_code, wrong_format.status_code) == (400, 400, 400)
    logger.info("Merge API verified: {}", [single.json()["detail"], twice.json()["detail"], wrong_format.json()["detail"]])


def main():
    demo_merge_api()


if __name__ == "__main__":
    main()
