#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将 doc_qa_assistant.py 封装成更安全的 FastAPI 服务。

启动：
  py -3.12 -m uvicorn fastapi_service:app --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import hmac
import os
import re
import secrets
import shutil
import time
import traceback
import uuid
from collections import defaultdict, deque
from pathlib import Path
from threading import Lock
from typing import Optional

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from doc_qa_assistant import (
    _normalize_llm_hub,
    build_or_load_index,
    build_prompt,
    generate_answer,
    generate_answer_via_api,
    load_llm,
    route_generation,
    search,
)


def _parse_allowed_origins() -> list[str]:
    raw = os.environ.get(
        "RAG_ALLOWED_ORIGINS",
        "http://127.0.0.1:3000,http://localhost:3000,https://jinyue-star.github.io",
    )
    return [item.strip() for item in raw.split(",") if item.strip()]


UPLOAD_DIR = Path("./.uploads").resolve()
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_SUFFIXES = {".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt", ".csv", ".txt", ".md"}
ALLOWED_ORIGINS = _parse_allowed_origins()
ACCESS_TOKEN = os.environ.get("RAG_ACCESS_TOKEN", "").strip()
SERVER_EMBED_MODEL = os.environ.get("MS_EMBED_ID", "BAAI/bge-small-zh-v1.5")
SERVER_MODEL_ID = os.environ.get("MS_MODEL_ID", "Qwen/Qwen2.5-3B-Instruct")
SERVER_LLM_HUB = _normalize_llm_hub(os.environ.get("LLM_HUB", "auto"))
SERVER_LOW_MEMORY = os.environ.get("RAG_LOW_MEMORY", "").strip().lower() in {"1", "true", "yes"}
ENABLE_LOCAL_LLM = os.environ.get("RAG_ENABLE_LOCAL_LLM", "").strip().lower() in {"1", "true", "yes"}
SERVER_API_KEY = os.environ.get("DASHSCOPE_API_KEY", "").strip()
SERVER_API_MODEL = os.environ.get("QWEN_API_MODEL", "qwen-plus")
SERVER_API_BASE = os.environ.get("QWEN_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1")
MAX_FILES = max(1, int(os.environ.get("RAG_MAX_FILES", "5")))
MAX_FILE_SIZE_MB = max(1, int(os.environ.get("RAG_MAX_FILE_MB", "20")))
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024
MAX_TOP_K = max(1, int(os.environ.get("RAG_MAX_TOP_K", "5")))
MAX_QUESTION_CHARS = max(50, int(os.environ.get("RAG_MAX_QUESTION_CHARS", "1000")))
RATE_LIMIT_WINDOW_SECONDS = max(10, int(os.environ.get("RAG_RATE_LIMIT_WINDOW_SECONDS", "60")))
RATE_LIMIT_MAX_REQUESTS = max(1, int(os.environ.get("RAG_RATE_LIMIT_MAX_REQUESTS", "10")))

app = FastAPI(
    title="Document QA API",
    version="1.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

_local_llm_cache: dict[str, tuple[object, object]] = {}
_rate_limit_records: dict[str, deque[float]] = defaultdict(deque)
_rate_limit_lock = Lock()


class HitItem(BaseModel):
    score: float
    source: str
    page_label: str
    meta: str
    content: str


class QAResponse(BaseModel):
    answer: str
    route: str
    hits: list[HitItem]


def _client_ip(request: Request) -> str:
    for header_name in ("cf-connecting-ip", "x-forwarded-for", "x-real-ip"):
        value = request.headers.get(header_name, "").strip()
        if value:
            return value.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _check_rate_limit(client_ip: str) -> None:
    now = time.time()
    with _rate_limit_lock:
        window = _rate_limit_records[client_ip]
        while window and now - window[0] > RATE_LIMIT_WINDOW_SECONDS:
            window.popleft()
        if len(window) >= RATE_LIMIT_MAX_REQUESTS:
            raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")
        window.append(now)


def _require_access_token(authorization: Optional[str]) -> None:
    if not ACCESS_TOKEN:
        raise HTTPException(status_code=503, detail="服务未完成安全初始化")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="缺少访问令牌")
    token = authorization[7:].strip()
    if not token or not hmac.compare_digest(token, ACCESS_TOKEN):
        raise HTTPException(status_code=401, detail="访问令牌无效")


def _safe_filename(name: str, used_names: set[str]) -> str:
    base_name = Path(name).name
    suffix = Path(base_name).suffix.lower()
    stem = Path(base_name).stem
    safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._") or "upload"
    candidate = f"{safe_stem}{suffix}"
    while candidate in used_names:
        candidate = f"{safe_stem}_{secrets.token_hex(4)}{suffix}"
    used_names.add(candidate)
    return candidate


def _cleanup_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


def _compact_text(text: str, limit: int = 220) -> str:
    cleaned = " ".join(text.split()).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rstrip() + "..."


def _build_fallback_answer(question: str, hits: list[tuple[float, object]]) -> str:
    if not hits:
        return "没有检索到相关内容，请换个问法或上传更相关的文档。"

    lead_score, lead_chunk = hits[0]
    lines = [
        "当前使用快速检索模式，未调用本地大模型。",
        f"最相关内容来自 `{lead_chunk.source}` 的 `{lead_chunk.page_label}`。",
        f"参考摘要：{_compact_text(lead_chunk.text, limit=280)}",
        f"相关度：{lead_score:.4f}",
    ]
    if len(hits) > 1:
        refs = "；".join(f"{chunk.source} {chunk.page_label}" for _, chunk in hits[:3])
        lines.append(f"其他参考：{refs}")
    lines.append(f"问题：{question}")
    return "\n".join(lines)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/v1/qa", response_model=QAResponse)
async def ask_doc_qa(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    question: str = Form(..., description="用户问题"),
    files: list[UploadFile] = File(..., description="一个或多个文档文件"),
    top_k: int = Form(3, description="检索片段条数"),
    max_new_tokens: Optional[int] = Form(None, description="生成 token 上限"),
) -> QAResponse:
    _require_access_token(authorization)
    _check_rate_limit(_client_ip(request))

    if not question.strip():
        raise HTTPException(status_code=400, detail="question 不能为空")
    if len(question) > MAX_QUESTION_CHARS:
        raise HTTPException(status_code=400, detail=f"问题长度不能超过 {MAX_QUESTION_CHARS} 个字符")
    if not files:
        raise HTTPException(status_code=400, detail="至少上传一个文件")
    if len(files) > MAX_FILES:
        raise HTTPException(status_code=400, detail=f"最多只能上传 {MAX_FILES} 个文件")

    request_dir = UPLOAD_DIR / uuid.uuid4().hex
    request_dir.mkdir(parents=True, exist_ok=True)
    saved_paths: list[Path] = []
    used_names: set[str] = set()

    try:
        for f in files:
            if not f.filename:
                continue
            suffix = Path(f.filename).suffix.lower()
            if suffix not in ALLOWED_SUFFIXES:
                raise HTTPException(status_code=400, detail=f"不支持的文件类型: {suffix or '未知'}")

            content = await f.read()
            if not content:
                continue
            if len(content) > MAX_FILE_SIZE_BYTES:
                raise HTTPException(status_code=400, detail=f"单个文件不能超过 {MAX_FILE_SIZE_MB}MB")

            safe_name = _safe_filename(f.filename, used_names)
            dest = request_dir / safe_name
            dest.write_bytes(content)
            saved_paths.append(dest)

        if not saved_paths:
            raise HTTPException(status_code=400, detail="未接收到有效文件")

        limited_top_k = max(1, min(int(top_k), MAX_TOP_K))
        chunks, _embeddings, index, st = build_or_load_index(saved_paths, SERVER_EMBED_MODEL)
        hits = search(question, chunks, index, st, top_k=limited_top_k)
        prompt = build_prompt(question, hits)

        route = route_generation(has_api_key=bool(SERVER_API_KEY))
        if route == "api":
            answer = generate_answer_via_api(
                api_key=SERVER_API_KEY,
                api_model=SERVER_API_MODEL,
                api_base=SERVER_API_BASE,
                user_msg=prompt,
                max_new_tokens=max_new_tokens,
                stream=False,
            )
        elif ENABLE_LOCAL_LLM:
            cache_key = f"{SERVER_MODEL_ID}::{SERVER_LLM_HUB}::{int(SERVER_LOW_MEMORY)}"
            if cache_key not in _local_llm_cache:
                _local_llm_cache[cache_key] = load_llm(
                    model_id=SERVER_MODEL_ID,
                    hub=SERVER_LLM_HUB,
                    cpu_half=SERVER_LOW_MEMORY,
                )
            local_model, tokenizer = _local_llm_cache[cache_key]
            answer = generate_answer(
                model=local_model,
                tokenizer=tokenizer,
                user_msg=prompt,
                max_new_tokens=max_new_tokens,
                stream=False,
            )
            route = "local"
        else:
            answer = _build_fallback_answer(question, hits)
            route = "fallback"
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="服务处理失败，请稍后重试") from e
    finally:
        _cleanup_dir(request_dir)

    return QAResponse(
        answer=answer,
        route=route,
        hits=[
            HitItem(
                score=score,
                source=chunk.source,
                page_label=chunk.page_label,
                meta=chunk.meta,
                content=_compact_text(chunk.text, limit=360),
            )
            for score, chunk in hits
        ],
    )
