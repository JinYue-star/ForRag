#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CORS 与私有网络预检中间件注册。"""

from __future__ import annotations

import os
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from rag_api import settings


def cors_strict_mode() -> bool:
    return os.environ.get("RAG_CORS_STRICT", "").strip().lower() in {"1", "true", "yes"}


def cors_allow_origins() -> list[str]:
    if cors_strict_mode():
        return settings.ALLOWED_ORIGINS
    return ["*"]


def cors_allow_origin_regex() -> Optional[str]:
    if cors_strict_mode():
        return settings.CORS_LAN_ORIGIN_REGEX
    return None


class PrivateNetworkAccessMiddleware(BaseHTTPMiddleware):
    """Chrome：从公网页或 HTTPS 访问局域网 http API 时，预检需带 Access-Control-Allow-Private-Network。"""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if request.headers.get("access-control-request-private-network", "").lower() == "true":
            response.headers["Access-Control-Allow-Private-Network"] = "true"
        return response


class LoginRequiredMiddleware(BaseHTTPMiddleware):
    """当启用鉴权时，/api/v1/* 需携带有效登录令牌（放行 /api/v1/auth/* 与预检）。"""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if (
            request.method != "OPTIONS"
            and path.startswith("/api/v1/")
            and not path.startswith("/api/v1/auth/")
        ):
            # 延迟导入，避免与 settings 初始化顺序冲突
            from rag_api.auth import auth_required, resolve_current_user

            if auth_required():
                try:
                    resolve_current_user(request.headers.get("authorization"))
                except Exception as exc:  # HTTPException -> JSON 401/403
                    status = getattr(exc, "status_code", 401)
                    detail = getattr(exc, "detail", "请先登录")
                    return JSONResponse({"detail": detail}, status_code=status)
        return await call_next(request)


def setup_middleware(app: FastAPI) -> None:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_allow_origins(),
        allow_origin_regex=cors_allow_origin_regex(),
        allow_credentials=False,
        allow_methods=["POST", "GET", "OPTIONS", "DELETE", "HEAD"],
        allow_headers=["Authorization", "Content-Type", "X-Session-Secret", "Accept", "Origin"],
    )
    app.add_middleware(PrivateNetworkAccessMiddleware)
    app.add_middleware(LoginRequiredMiddleware)
