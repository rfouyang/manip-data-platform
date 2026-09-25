from fastapi import APIRouter, Request
from fastapi.responses import Response
from loguru import logger


def create_visualization_router():
    router = APIRouter(prefix="/api/visualization", tags=["visualization"])

    @router.get("/episode")
    def get_episode(request: Request, dataset_id: int, episode_id: str):
        visualizer = request.app.state.context.data_visualizer
        frame_index = visualizer.get_episode(dataset_id, episode_id)
        return frame_index

    @router.get("/series")
    def get_series(request: Request, dataset_id: int, episode_id: str, fields: str, start: int = 0, stop: int | None = None, max_points: int = 2000):
        """fields 为逗号分隔的字段名；曲线按等间隔抽样，stride 在响应中返回。"""
        visualizer = request.app.state.context.data_visualizer
        names = [name for name in fields.split(",") if name]
        options = {"start": start, "max_points": max_points}
        if stop is not None:
            options["stop"] = stop
        curves = visualizer.get_series(dataset_id, episode_id, names, **options)
        return curves

    @router.get("/frame")
    def get_frame(request: Request, dataset_id: int, episode_id: str, index: int):
        visualizer = request.app.state.context.data_visualizer
        frame = visualizer.get_frame(dataset_id, episode_id, index)
        return frame

    @router.get("/color")
    def get_color(request: Request, dataset_id: int, episode_id: str, camera: str, index: int):
        visualizer = request.app.state.context.data_visualizer
        jpeg = visualizer.get_color(dataset_id, episode_id, camera, index)
        response = Response(jpeg, media_type="image/jpeg")
        return response

    @router.get("/depth")
    def get_depth(request: Request, dataset_id: int, episode_id: str, camera: str, index: int):
        """原始 uint16 小端序、行优先；宽高见 /episode 的 depth_resolution（[height, width]）。"""
        visualizer = request.app.state.context.data_visualizer
        raw = visualizer.get_depth(dataset_id, episode_id, camera, index)
        response = Response(raw, media_type="application/octet-stream")
        return response

    return router


def demo_visualization_api():
    from pathlib import Path

    import cv2
    import numpy as np
    from fastapi.testclient import TestClient

    from app.main import create_app
    from config.settings import Settings

    repo_root = Path(__file__).resolve().parents[3]
    db_path = repo_root / "output" / "demo" / "visualization_api" / "demo.sqlite3"
    db_path.unlink(missing_ok=True)
    app = create_app(Settings(db_path=db_path))
    episode_id = "success_episodes/0106_102314"

    with TestClient(app) as client:
        dataset_id = client.post("/api/datasets", json={"path": "place_red_pepper_on_red_tray"}).json()["id"]
        query = {"dataset_id": dataset_id, "episode_id": episode_id}
        frame_index = client.get("/api/visualization/episode", params=query).json()
        camera = next(iter(frame_index["cameras"]))
        height, width = frame_index["cameras"][camera]["depth_resolution"]

        frame_query = {**query, "camera": camera, "index": 1047}
        color = client.get("/api/visualization/color", params=frame_query)
        depth = client.get("/api/visualization/depth", params=frame_query)
        assert color.headers["content-type"] == "image/jpeg"
        assert len(depth.content) == height * width * 2
        decoded = cv2.imdecode(np.frombuffer(color.content, np.uint8), cv2.IMREAD_COLOR)
        assert decoded.shape == (height, width, 3)

        names = frame_index["series_names"][:2]
        curves = client.get("/api/visualization/series", params={**query, "fields": ",".join(names), "max_points": 500}).json()
        assert curves["stride"] >= 1 and len(curves["rows"]) <= 500
        assert len(curves["series"][names[0]][0]) == len(curves["rows"])
        frame = client.get("/api/visualization/frame", params={**query, "index": 1047}).json()
        assert frame["index"] == 1047 and set(frame["values"]) == set(frame_index["series_names"])

        bad_field = client.get("/api/visualization/series", params={**query, "fields": "no/such"})
        bad_frame = client.get("/api/visualization/frame", params={**query, "index": frame_index["length"]})
        assert (bad_field.status_code, bad_frame.status_code) == (400, 400)

        bad_index = client.get("/api/visualization/color", params={**frame_query, "index": frame_index["length"]})
        bad_camera = client.get("/api/visualization/depth", params={**frame_query, "camera": "no_camera"})
        missing = client.get("/api/visualization/episode", params={**query, "episode_id": "no/such"})
        assert (bad_index.status_code, bad_camera.status_code, missing.status_code) == (400, 400, 404)

    logger.info(
        "Visualization API verified: {} rows, {} camera images, color {} bytes, depth {} bytes",
        frame_index["length"], len(frame_index["cameras"][camera]["image_starts"]), len(color.content), len(depth.content),
    )
    logger.info("Series: {} fields, stride={}, {} points; frame values fields={}", len(curves["series"]), curves["stride"], len(curves["rows"]), len(frame["values"]))


def main():
    demo_visualization_api()


if __name__ == "__main__":
    main()
