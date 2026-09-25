from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from loguru import logger


UI_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=UI_DIR)
# 各业务 panel 的脚本放在对应业务目录，只按白名单对外提供，避免暴露模板目录中的其他文件。
PANEL_SCRIPTS = {"dataset", "visualization", "conversion", "tag", "split", "merge", "subtask", "task"}


def create_ui_router():
    router = APIRouter(include_in_schema=False)

    @router.get("/", response_class=HTMLResponse)
    def index(request: Request):
        settings = request.app.state.context.settings
        page_context = {"data_dir": settings.data_dir}
        response = templates.TemplateResponse(request, "templates/base.html", page_context)
        return response

    @router.get("/ui/{panel}/panel.js")
    def panel_script(panel: str):
        if panel not in PANEL_SCRIPTS:
            raise HTTPException(404)
        # no-cache：浏览器每次按 ETag 校验，服务更新后不会继续使用旧脚本（未变化时返回 304，不重复下载）。
        response = FileResponse(UI_DIR / panel / "panel.js", media_type="text/javascript", headers={"Cache-Control": "no-cache"})
        return response

    return router


def demo_ui_router():
    paths = {route.path for route in create_ui_router().routes}
    assert paths == {"/", "/ui/{panel}/panel.js"}
    logger.info("UI router ready: {}", paths)


def main():
    demo_ui_router()


if __name__ == "__main__":
    main()
