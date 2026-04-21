#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""鉴权、限流、会话校验与通用 HTTP 辅助函数。"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import shutil
import threading
import time
import uuid
from collections import defaultdict, deque
from pathlib import Path
from typing import Optional

from fastapi import HTTPException, Request

import chroma_store

from rag_api import settings

_rate_limit_records: dict[str, deque[float]] = defaultdict(deque)
_rate_limit_lock = threading.Lock()


def hash_session_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def client_ip(request: Request) -> str:
    for header_name in ("cf-connecting-ip", "x-forwarded-for", "x-real-ip"):
        value = request.headers.get(header_name, "").strip()
        if value:
            return value.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def check_rate_limit(client_ip_value: str) -> None:
    if settings.RATE_LIMIT_MAX_REQUESTS <= 0:
        return
    now = time.time()
    with _rate_limit_lock:
        window = _rate_limit_records[client_ip_value]
        while window and now - window[0] > settings.RATE_LIMIT_WINDOW_SECONDS:
            window.popleft()
        if len(window) >= settings.RATE_LIMIT_MAX_REQUESTS:
            raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")
        window.append(now)


def server_error_detail(exc: BaseException) -> str:
    if os.environ.get("RAG_DEBUG_ERRORS", "").strip().lower() in {"1", "true", "yes"}:
        return f"{type(exc).__name__}: {exc}"[:1200]
    if isinstance(exc, ModuleNotFoundError):
        mod = getattr(exc, "name", "") or ""
        hint = ""
        if mod == "pptx":
            hint = "（包名：pip install python-pptx）"
        elif mod == "docx":
            hint = "（包名：pip install python-docx）"
        elif mod in ("fitz", "pymupdf"):
            hint = "（包名：pip install pymupdf）"
        return (
            f"缺少依赖模块「{mod or '?'}」{hint}。请在运行 uvicorn 的同一 Python 环境中执行："
            "pip install -r requirements.txt"
        )
    msg_l = str(exc).lower()
    if "numpy" in msg_l and (
        "multiarray" in msg_l
        or "compiled using numpy 1.x" in msg_l
        or "cannot run" in msg_l
        or "numpy 2" in msg_l
    ):
        return (
            "NumPy 与 SciPy/scikit-learn 等版本不兼容。"
            "请在服务环境中执行：pip install \"numpy>=1.26,<2\" --force-reinstall，"
            "然后重新安装：pip install scipy scikit-learn --force-reinstall。"
        )
    return (
        "服务处理失败（常见于：未安装 faiss-cpu / sentence-transformers，或 numpy 与 scipy 版本冲突）。"
        "请在运行服务的终端执行 pip install -r requirements.txt；仍失败时在启动前设置环境变量 "
        "RAG_DEBUG_ERRORS=1 查看具体错误。"
    )


def require_access_token(authorization: Optional[str]) -> None:
    if not settings.REQUIRE_ACCESS_TOKEN:
        return
    if not settings.ACCESS_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="已启用访问令牌校验（RAG_REQUIRE_ACCESS_TOKEN=1），但未设置 RAG_ACCESS_TOKEN。请设置强随机令牌后重启，或设为 0 关闭校验。",
        )
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="缺少访问令牌")
    token = authorization[7:].strip()
    if not token or not hmac.compare_digest(token, settings.ACCESS_TOKEN):
        raise HTTPException(status_code=401, detail="访问令牌无效")


def parse_uuid_param(name: str, value: str) -> str:
    try:
        u = uuid.UUID(value)
        return str(u)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"无效的{name}") from e


def verify_session(session_id: str, session_secret: str) -> None:
    row = chroma_store.session_get(session_id)
    if not row:
        raise HTTPException(status_code=404, detail="会话不存在")
    if not hmac.compare_digest(row["secret_hash"], hash_session_secret(session_secret)):
        raise HTTPException(status_code=403, detail="会话密钥无效")
    chroma_store.session_update_last_seen(session_id, time.time())


def safe_filename(name: str, used_names: set[str]) -> str:
    base_name = Path(name).name
    suffix = Path(base_name).suffix.lower()
    stem = Path(base_name).stem
    safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._") or "upload"
    candidate = f"{safe_stem}{suffix}"
    while candidate in used_names:
        candidate = f"{safe_stem}_{secrets.token_hex(4)}{suffix}"
    used_names.add(candidate)
    return candidate


def cleanup_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
