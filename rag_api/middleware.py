#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CORS 与私有网络预检中间件注册。"""

from __future__ import annotations

import os
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
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
