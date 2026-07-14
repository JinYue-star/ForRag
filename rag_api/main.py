#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FastAPI 应用入口：生命周期、中间件、路由注册与静态前端挂载。

启动：py -3.12 -m uvicorn fastapi_service:app --host 0.0.0.0 --port 8000
或：py -3.12 -m uvicorn rag_api.main:app --host 0.0.0.0 --port 8000

环境变量说明见仓库 README 或原 fastapi_service 模块文档字符串。
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from rag_api import settings
from rag_api.auth_routes import router_admin, router_auth
from rag_api.export_routes import router_export
from rag_api.middleware import setup_middleware
from rag_api.routes import router_health, router_v1


class SafeStaticFiles(StaticFiles):
    """静态文件服务：把非法路径（如 Windows WinError 123 的畸形 URL）归一成 404，
    而非让 os.stat 抛 OSError 造成 500 + 堆栈刷屏。"""

    def lookup_path(self, path: str):  # noqa: ANN201
        try:
            return super().lookup_path(path)
        except (OSError, ValueError):
            return "", None


@asynccontextmanager
async def app_lifespan(app: FastAPI):
    yield
    settings.clear_rag_disk_cache_on_shutdown()


def create_app() -> FastAPI:
    application = FastAPI(
        title="Document QA API",
        version="2.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=app_lifespan,
    )
    setup_middleware(application)
    application.include_router(router_health)
    application.include_router(router_auth)
    application.include_router(router_admin)
    application.include_router(router_export)
    application.include_router(router_v1)

    frontend_dir = settings.resolve_frontend_static_dir()
    if frontend_dir:
        fd = str(frontend_dir)

        # 入口落地页：根路径优先返回角色选择页（landing.html），
        # 该显式路由需在下方静态挂载之前注册，才能优先于 StaticFiles 的默认 index.html。
        landing_file = frontend_dir / "landing.html"
        if landing_file.is_file():
            @application.get("/", include_in_schema=False)
            def _root_landing() -> FileResponse:  # noqa: ANN202
                return FileResponse(str(landing_file))

        # 先挂 /frontend，再挂 /：否则请求 /frontend/quiz.html 会被根挂载当成
        # 「静态目录下的 frontend/quiz.html」而 404（实际文件在根目录 quiz.html）
        application.mount(
            "/frontend",
            SafeStaticFiles(directory=fd, html=True),
            name="frontend_prefixed",
        )
        application.mount(
            "/",
            SafeStaticFiles(directory=fd, html=True),
            name="frontend",
        )
    return application


app = create_app()
