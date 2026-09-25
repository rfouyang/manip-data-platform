from fastapi import APIRouter, Request
from loguru import logger
from pydantic import BaseModel


class TagRequest(BaseModel):
    dataset_id: int
    mode: str = "regex"
    pattern: str | None = None
    value: str | None = None
    column: str | None = None
    default: str | None = None
    output_name: str | None = None


def create_tag_router():
    router = APIRouter(prefix="/api/tag", tags=["tag"])

    @router.post("/preview")
    def preview_tag(request: Request, body: TagRequest):
        """新增列的预检（mode 为 regex：正则从任务描述抽取；constant：整列统一值），不写任何文件。"""
        tagger = request.app.state.context.data_tagger
        options = body.model_dump(exclude={"dataset_id", "mode"}, exclude_none=True)
        preview = tagger.preview(body.dataset_id, body.mode, **options)
        return preview

    @router.post("", status_code=202)
    def submit_tag(request: Request, body: TagRequest):
        """提交生成列任务，输出为新数据集；进度通过 /api/tasks 查询。"""
        tagger = request.app.state.context.data_tagger
        options = body.model_dump(exclude={"dataset_id", "mode"}, exclude_none=True)
        task = tagger.submit(body.dataset_id, body.mode, **options)
        return task

    return router


def demo_tag_api():
    from pathlib import Path

    from fastapi.testclient import TestClient

    from app.main import create_app
    from config.settings import Settings

    import shutil

    from util.lerobot_helper import create_demo_dataset

    repo_root = Path(__file__).resolve().parents[3]
    demo_dir = repo_root / "output" / "demo" / "tag_api"
    shutil.rmtree(demo_dir, ignore_errors=True)
    # 合成的 3 个 episode 小样本（含深度），不依赖外部样本；登记时的相对路径以 demo_dir 为基准。
    create_demo_dataset(demo_dir / "sample", ["put the red pepper on the red tray", "put the green pepper on the red tray", "wave hand"])
    app = create_app(Settings(db_path=demo_dir / "demo.sqlite3", data_dir=demo_dir, output_dir=demo_dir / "output"))

    with TestClient(app) as client:
        dataset_id = client.post("/api/datasets", json={"path": "sample"}).json()["id"]
        preview = client.post("/api/tag/preview", json={"dataset_id": dataset_id, "pattern": r"put the (\w+ \w+) on"}).json()
        # “wave hand”未匹配，取空值；值按文本排序。
        assert preview["ready"] and preview["column"] == "tag" and [item["value"] for item in preview["values"]] == ["", "green pepper", "red pepper"]
        unmatched = client.post("/api/tag/preview", json={"dataset_id": dataset_id, "pattern": "blue"}).json()
        # 未匹配的任务取空值，不阻止提交。
        assert unmatched["ready"] and unmatched["unmatched_count"] == 3 and unmatched["tasks"][0]["value"] == ""
        invalid = client.post("/api/tag/preview", json={"dataset_id": dataset_id, "pattern": "("})
        rejected = client.post("/api/tag", json={"dataset_id": dataset_id, "pattern": "blue", "column": "observation.state"})
        assert (invalid.status_code, rejected.status_code) == (400, 400)
        constant = client.post("/api/tag/preview", json={"dataset_id": dataset_id, "mode": "constant", "value": "demo_batch", "column": "source"}).json()
        assert constant["ready"] and constant["values"] == [{"value": "demo_batch", "episodes": 3, "frames": 120}]
        empty = client.post("/api/tag/preview", json={"dataset_id": dataset_id, "mode": "constant", "value": ""})
        assert empty.status_code == 400
    logger.info("Tag API verified: {} -> {}", preview["tasks"][0]["task"], preview["values"])


def main():
    demo_tag_api()


if __name__ == "__main__":
    main()
