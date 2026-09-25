"""API 共用：组件异常统一转换为 HTTP 状态码，路由中不再逐个捕获。"""
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from loguru import logger


# 组件约定：资源不存在抛 LookupError，不覆盖已有结果抛 FileExistsError，参数或状态不合法抛 ValueError。
# 响应与 HTTPException 相同（{"detail": 原因}），前端直接显示原因。
ERROR_STATUS = ((LookupError, 404), (FileExistsError, 409), (ValueError, 400))


def error_handler(status):
    async def handle(request, error):
        response = JSONResponse({"detail": str(error)}, status_code=status)
        return response

    return handle


def register_error_handlers(app):
    for error_type, status in ERROR_STATUS:
        app.add_exception_handler(error_type, error_handler(status))


def demo_error_handlers():
    app = FastAPI()
    register_error_handlers(app)
    errors = {"lookup": LookupError("数据集不存在: 9"), "exists": FileExistsError("输出目录已存在"), "value": ValueError("请填写正则表达式")}

    @app.get("/raise/{kind}")
    def raise_error(kind: str):
        raise errors[kind]

    with TestClient(app) as client:
        statuses = {kind: client.get(f"/raise/{kind}").status_code for kind in errors}
        detail = client.get("/raise/value").json()["detail"]
    assert statuses == {"lookup": 404, "exists": 409, "value": 400} and detail == "请填写正则表达式"
    logger.info("Error handlers: {}", statuses)


def main():
    demo_error_handlers()


if __name__ == "__main__":
    main()
