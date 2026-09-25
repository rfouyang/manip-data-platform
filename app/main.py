import sys
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from loguru import logger

from app.api_manip.common import register_error_handlers
from app.api_manip.conversion.api import create_conversion_router
from app.api_manip.dataset.api import create_dataset_router
from app.api_manip.merge.api import create_merge_router
from app.api_manip.split.api import create_split_router
from app.api_manip.subtask.api import create_subtask_router
from app.api_manip.tag.api import create_tag_router
from app.api_manip.task_api import create_task_router
from app.api_manip.visualization.api import create_visualization_router
from app.context import AppContext
from app.ui_manip.content import PANEL_SCRIPTS, UI_DIR, create_ui_router
from config.settings import Settings
from util.log_helper import setup_logger


def create_app(settings=None):
    settings = settings if settings is not None else Settings()

    @asynccontextmanager
    async def lifespan(app):
        app.state.context = AppContext(settings)
        app.state.context.task_manager.start()
        logger.info("Service started")
        try:
            yield
        finally:
            app.state.context.task_manager.stop()
            del app.state.context
            logger.info("Service stopped")

    app = FastAPI(title="机器人数据平台", version="0.1.0", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=UI_DIR / "static"), name="static")

    @app.middleware("http")
    async def revalidate_static(request, call_next):
        # 共用脚本与样式每次按 ETag 校验，服务更新后浏览器不会继续使用旧文件（未变化时返回 304）。
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    register_error_handlers(app)
    app.include_router(create_ui_router())
    app.include_router(create_dataset_router())
    app.include_router(create_visualization_router())
    app.include_router(create_conversion_router())
    app.include_router(create_tag_router())
    app.include_router(create_split_router())
    app.include_router(create_merge_router())
    app.include_router(create_subtask_router())
    app.include_router(create_task_router())

    @app.get("/api/health")
    def health():
        status = {"status": "ok", "version": app.version}
        return status

    return app


def demo_service():
    from fastapi.testclient import TestClient

    db_path = Settings().root / "output" / "demo" / "app_main" / "demo.sqlite3"
    settings = Settings(db_path=db_path)
    app = create_app(settings)
    with TestClient(app) as client:
        assert app.state.context.settings is settings
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        page = client.get("/")
        assert page.status_code == 200
        assert "机器人数据平台" in page.text
        assert 'data-panel="dataset"' in page.text
        assert all(client.get(f"/ui/{panel}/panel.js").status_code == 200 for panel in PANEL_SCRIPTS)
        assert client.get("/api/datasets").status_code == 200
        assert client.get("/api/tasks").status_code == 200
        assert client.get("/openapi.json").status_code == 200
        assert client.get("/docs").status_code == 200
    assert not hasattr(app.state, "context")
    logger.info("Service startup, health endpoint and shutdown verified")


def exit_with_error(_):
    sys.exit(1)


# 服务入口统一记录未处理的异常（含变量值的完整回溯），并以非零状态退出。
@logger.catch(onerror=exit_with_error)
def main():
    # 改为 True 只运行服务自检后退出，不启动服务。
    demo_only = False
    if demo_only:
        demo_service()
        return
    settings = Settings()
    log_file = setup_logger(settings.log_dir, settings.log_level)
    logger.info("Log file pattern: {}", log_file)
    app = create_app(settings)
    # log_config=None：uvicorn 不再自行配置 logging，其日志经 InterceptHandler 进入 loguru。
    uvicorn.run(app, host=settings.host, port=settings.port, log_config=None)


if __name__ == "__main__":
    main()
