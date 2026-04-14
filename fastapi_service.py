#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将 doc_qa_assistant.py 封装成 FastAPI 服务，供前端直接调用。

启动：
  uvicorn fastapi_service:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from doc_qa_assistant import (
    build_or_load_index,
    build_prompt,
    generate_answer,
    generate_answer_via_api,
    load_llm,
    route_generation,
    search,
    _normalize_llm_hub,
)


UPLOAD_DIR = Path("./.uploads").resolve()
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Document QA API", version="1.0.0")

# 前后端分离场景下，先放开 CORS 便于联调；上线后建议限定具体域名。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_local_llm_cache: dict[str, tuple[object, object]] = {}


class HitItem(BaseModel):
    score: float
    source: str
    page_label: str
    meta: str


class QAResponse(BaseModel):
    answer: str
    route: str
    hits: list[HitItem]


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/v1/qa", response_model=QAResponse)
async def ask_doc_qa(
    question: str = Form(..., description="用户问题"),
    files: list[UploadFile] = File(..., description="一个或多个文档文件"),
    top_k: int = Form(3, description="检索片段条数"),
    max_new_tokens: Optional[int] = Form(None, description="生成 token 上限"),
    embed_model: str = Form(
        os.environ.get("MS_EMBED_ID", "BAAI/bge-small-zh-v1.5"),
        description="向量模型 ID",
    ),
    model: str = Form(
        os.environ.get("MS_MODEL_ID", "Qwen/Qwen2.5-3B-Instruct"),
        description="本地大模型 ID",
    ),
    llm_hub: str = Form(
        _normalize_llm_hub(os.environ.get("LLM_HUB", "auto")),
        description="auto | modelscope | huggingface",
    ),
    low_memory: bool = Form(False, description="CPU 下是否尝试低内存模式"),
    api_key: Optional[str] = Form(
        os.environ.get("DASHSCOPE_API_KEY", ""),
        description="可选。提供则走千问兼容 API",
    ),
    api_model: str = Form(
        os.environ.get("QWEN_API_MODEL", "qwen-plus"),
        description="千问 API 模型名",
    ),
    api_base: str = Form(
        os.environ.get("QWEN_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        description="千问兼容 API Base URL",
    ),
) -> QAResponse:
    if not question.strip():
        raise HTTPException(status_code=400, detail="question 不能为空")
    if not files:
        raise HTTPException(status_code=400, detail="至少上传一个文件")

    request_dir = UPLOAD_DIR / uuid.uuid4().hex
    request_dir.mkdir(parents=True, exist_ok=True)
    saved_paths: list[Path] = []

    for f in files:
        if not f.filename:
            continue
        safe_name = Path(f.filename).name
        dest = request_dir / safe_name
        content = await f.read()
        dest.write_bytes(content)
        saved_paths.append(dest)

    if not saved_paths:
        raise HTTPException(status_code=400, detail="未接收到有效文件")

    try:
        chunks, _embeddings, index, st = build_or_load_index(saved_paths, embed_model)
        hits = search(question, chunks, index, st, top_k=top_k)
        prompt = build_prompt(question, hits)

        route = route_generation(has_api_key=bool((api_key or "").strip()))
        if route == "api":
            answer = generate_answer_via_api(
                api_key=(api_key or "").strip(),
                api_model=api_model,
                api_base=api_base,
                user_msg=prompt,
                max_new_tokens=max_new_tokens,
                stream=False,
            )
        else:
            cache_key = f"{model}::{llm_hub}::{int(low_memory)}"
            if cache_key not in _local_llm_cache:
                _local_llm_cache[cache_key] = load_llm(
                    model_id=model,
                    hub=llm_hub,
                    cpu_half=low_memory,
                )
            local_model, tokenizer = _local_llm_cache[cache_key]
            answer = generate_answer(
                model=local_model,
                tokenizer=tokenizer,
                user_msg=prompt,
                max_new_tokens=max_new_tokens,
                stream=False,
            )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"服务处理失败: {e}") from e

    return QAResponse(
        answer=answer,
        route=route,
        hits=[
            HitItem(
                score=score,
                source=chunk.source,
                page_label=chunk.page_label,
                meta=chunk.meta,
            )
            for score, chunk in hits
        ],
    )
