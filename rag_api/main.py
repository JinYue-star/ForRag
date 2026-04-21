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
from fastapi.staticfiles import StaticFiles

from rag_api import settings
from rag_api.middleware import setup_middleware
from rag_api.routes import router_health, router_v1


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
    application.include_router(router_v1)

    frontend_dir = settings.resolve_frontend_static_dir()
    if frontend_dir:
        fd = str(frontend_dir)
        # 先挂 /frontend，再挂 /：否则请求 /frontend/quiz.html 会被根挂载当成
        # 「静态目录下的 frontend/quiz.html」而 404（实际文件在根目录 quiz.html）
        application.mount(
            "/frontend",
            StaticFiles(directory=fd, html=True),
            name="frontend_prefixed",
        )
        application.mount(
            "/",
            StaticFiles(directory=fd, html=True),
            name="frontend",
        )
    return application


app = create_app()
