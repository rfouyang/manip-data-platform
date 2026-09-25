from fastapi import APIRouter, Request
from loguru import logger
from pydantic import BaseModel


class FrameCountRequest(BaseModel):
    dataset_id: int


class ConvertRequest(BaseModel):
    dataset_id: int
    target: str | None = None
    output_name: str | None = None
    max_episodes: int | None = None
    task: str | None = None


def create_conversion_router():
    router = APIRouter(prefix="/api/conversion", tags=["conversion"])

    @router.get("/mapping")
    def get_mapping(request: Request):
        converter = request.app.state.context.data_converter
        mapping = converter.load_mapping()
        return mapping

    @router.get("/preview")
    def preview_conversion(request: Request, dataset_id: int, target: str | None = None, sample_episodes: int = 3, output_name: str | None = None):
        """转换预检，不写任何文件。"""
        converter = request.app.state.context.data_converter
        options = {"target": target, "sample_episodes": sample_episodes}
        if output_name:
            options["output_name"] = output_name
        preview = converter.preview(dataset_id, **options)
        return preview

    @router.post("/frame-count", status_code=202)
    def submit_frame_count(request: Request, body: FrameCountRequest):
        """提交全量取帧统计后台任务，返回任务记录；进度通过 /api/tasks 查询。"""
        converter = request.app.state.context.data_converter
        task = converter.submit_frame_count(body.dataset_id)
        return task

    @router.post("/convert", status_code=202)
    def submit_conversion(request: Request, body: ConvertRequest):
        """提交转换任务（H5 → v3.0、H5 → v2.1、v3.0 → v2.1、v2.1 → v3.0）；max_episodes 用于小样本转换。"""
        converter = request.app.state.context.data_converter
        options = body.model_dump(exclude={"dataset_id"}, exclude_none=True)
        task = converter.submit_conversion(body.dataset_id, **options)
        return task

    return router


def demo_conversion_api():
    from pathlib import Path

    from fastapi.testclient import TestClient

    from app.main import create_app
    from config.settings import Settings

    repo_root = Path(__file__).resolve().parents[3]
    demo_dir = repo_root / "output" / "demo" / "conversion_api"
    db_path = demo_dir / "demo.sqlite3"
    db_path.unlink(missing_ok=True)
    app = create_app(Settings(db_path=db_path, output_dir=demo_dir / "output"))

    with TestClient(app) as client:
        dataset_id = client.post("/api/datasets", json={"path": "place_red_pepper_on_red_tray"}).json()["id"]
        mapping = client.get("/api/conversion/mapping").json()
        assert mapping["action"]["source"] == "puppet" and mapping["action"]["offset_frames"] == 1
        assert mapping["state"]["source"] == "puppet" and mapping["state"]["offset_frames"] == 0

        preview = client.get("/api/conversion/preview", params={"dataset_id": dataset_id, "sample_episodes": 1}).json()
        assert preview["layout"]["size"] == 16
        assert preview["fps"] == 30
        assert preview["convertible_count"] == preview["episode_count"] - len(preview["blocked_episodes"])

        unsupported = client.get("/api/conversion/preview", params={"dataset_id": dataset_id, "target": "h5"})
        missing = client.get("/api/conversion/preview", params={"dataset_id": 999})
        assert (unsupported.status_code, missing.status_code) == (400, 404)

    logger.info(
        "Conversion API verified: target={}, {} 维向量, {} 可转换 episode, 估算 {} 帧, ready={}",
        preview["target"], preview["layout"]["size"], preview["convertible_count"], preview["estimated_frames"], preview["ready"],
    )


def main():
    demo_conversion_api()


if __name__ == "__main__":
    main()
