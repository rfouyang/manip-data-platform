"""API 层：组件异常统一映射为 404 / 409 / 400，提交前重新预检、未通过时不创建任务。业务逻辑由各组件测试覆盖。"""
from fastapi.testclient import TestClient

from app.main import create_app
from config.settings import Settings
from tests.test_data_tagger import make_tasks_v3


def test_error_status_and_submit_blocking(tmp_path):
    make_tasks_v3(tmp_path / "source")
    app = create_app(Settings(db_path=tmp_path / "test.sqlite3", data_dir=tmp_path, output_dir=tmp_path / "out"))
    with TestClient(app) as client:
        dataset_id = client.post("/api/datasets", json={"path": "source"}).json()["id"]
        assert client.post("/api/datasets", json={"path": "source"}).status_code == 409
        missing = client.get("/api/datasets/999")
        assert missing.status_code == 404 and "999" in missing.json()["detail"]
        assert client.post("/api/split/preview", json={"dataset_id": dataset_id, "mode": "episodes", "episodes": "1-a"}).status_code == 400
        assert client.post("/api/split/preview", json={"dataset_id": dataset_id, "mode": "episodes", "episodes": "1", "output_name": "../x"}).status_code == 400
        rejected = client.post("/api/split", json={"dataset_id": dataset_id, "mode": "episodes", "episodes": ""})
        assert rejected.status_code == 400 and "没有选中任何 episode" in rejected.json()["detail"]
        blocked_tag = client.post("/api/tag", json={"dataset_id": dataset_id, "mode": "regex", "pattern": "cup", "column": "observation.state"})
        assert blocked_tag.status_code == 400 and "列已存在" in blocked_tag.json()["detail"]
        assert client.get("/api/tasks").json() == []
        assert client.get("/api/tasks/1").status_code == 404
