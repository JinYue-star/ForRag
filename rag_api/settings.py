#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""集中读取环境变量、数据目录初始化与服务级常量（供路由与业务模块共用）。"""

from __future__ import annotations

import os
import shutil
import traceback
from pathlib import Path

import chroma_store
import kb_store

from doc_qa_assistant import _normalize_llm_hub

# 仓库根目录（rag_api 的上一级）
REPO_ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = Path(os.environ.get("RAG_DATA_DIR", str(REPO_ROOT / ".data"))).resolve()
if not os.environ.get("RAG_CACHE_ROOT"):
    os.environ["RAG_CACHE_ROOT"] = str(DATA_DIR / "vector_cache")
Path(os.environ["RAG_CACHE_ROOT"]).mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)
if os.environ.get("RAG_RESET_CHROMA", "").strip().lower() in {"1", "true", "yes"}:
    chroma_store.reset_chroma(DATA_DIR)
else:
    chroma_store.init_chroma(DATA_DIR)
kb_store.init_kb_db(DATA_DIR)


def parse_allowed_origins() -> list[str]:
    raw = os.environ.get(
        "RAG_ALLOWED_ORIGINS",
        "http://127.0.0.1:3000,http://localhost:3000,http://127.0.0.1:5500,http://localhost:5500,"
        "http://127.0.0.1:8000,http://localhost:8000,"
        "https://jinyue-star.github.io",
    )
    return [item.strip() for item in raw.split(",") if item.strip()]


CORS_LAN_ORIGIN_REGEX = (
    r"https?://(localhost|127\.0\.0\.1"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r"|10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|172\.(1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3})(:\d+)?"
)

UPLOAD_DIR = Path("./.uploads").resolve()
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# 旧版本 KB 文件一次性迁移到全局 KB 目录；失败不阻塞启动
try:
    from rag_api.kb_migrate import migrate_global_kb

    migrate_global_kb(DATA_DIR, UPLOAD_DIR, kb_id="default")
except Exception:
    traceback.print_exc()

# 全局知识库（单用户本地版）
KB_ID = os.environ.get("RAG_KB_ID", "default").strip() or "default"
KB_ROOT_REL = f"kb/{KB_ID}"


def kb_root_dir() -> Path:
    """全局 KB 根目录（存笔记与附件原文件）。"""
    p = (UPLOAD_DIR / KB_ROOT_REL).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def kb_notes_dir() -> Path:
    p = (kb_root_dir() / "notes").resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def kb_files_dir() -> Path:
    p = (kb_root_dir() / "files").resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p

ALLOWED_SUFFIXES = {
    ".pdf",
    ".docx",
    ".doc",
    ".xlsx",
    ".xls",
    ".pptx",
    ".ppt",
    ".csv",
    ".txt",
    ".md",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".bmp",
    ".gif",
    ".tif",
    ".tiff",
}

ALLOWED_ORIGINS = parse_allowed_origins()
ACCESS_TOKEN = os.environ.get("RAG_ACCESS_TOKEN", "").strip()
_raw_require_token = os.environ.get("RAG_REQUIRE_ACCESS_TOKEN", "").strip().lower()
if _raw_require_token in {"0", "false", "no", "off"}:
    REQUIRE_ACCESS_TOKEN = False
elif _raw_require_token in {"1", "true", "yes"}:
    REQUIRE_ACCESS_TOKEN = True
else:
    REQUIRE_ACCESS_TOKEN = bool(ACCESS_TOKEN)

SERVER_EMBED_MODEL = os.environ.get("MS_EMBED_ID", "BAAI/bge-small-zh-v1.5")
SERVER_MODEL_ID = os.environ.get("MS_MODEL_ID", "Qwen/Qwen2.5-3B-Instruct")
SERVER_LLM_HUB = _normalize_llm_hub(os.environ.get("LLM_HUB", "auto"))
SERVER_LOW_MEMORY = os.environ.get("RAG_LOW_MEMORY", "").strip().lower() in {"1", "true", "yes"}
ENABLE_LOCAL_LLM = os.environ.get("RAG_ENABLE_LOCAL_LLM", "").strip().lower() in {"1", "true", "yes"}
_DEFAULT_DASHSCOPE_API_KEY = "sk-a9039ea944cb4de792c876d6f731f5d6"
SERVER_API_KEY = (os.environ.get("DASHSCOPE_API_KEY") or _DEFAULT_DASHSCOPE_API_KEY).strip()
SERVER_API_MODEL = os.environ.get("QWEN_API_MODEL", "qwen-plus")
SERVER_API_BASE = os.environ.get("QWEN_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1")
MAX_FILES = max(1, int(os.environ.get("RAG_MAX_FILES", "5")))
MAX_FILE_SIZE_MB = max(1, int(os.environ.get("RAG_MAX_FILE_MB", "20")))
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024
MAX_TOP_K = max(1, int(os.environ.get("RAG_MAX_TOP_K", "5")))
MAX_QUESTION_CHARS = max(50, int(os.environ.get("RAG_MAX_QUESTION_CHARS", "1000")))
RATE_LIMIT_WINDOW_SECONDS = max(10, int(os.environ.get("RAG_RATE_LIMIT_WINDOW_SECONDS", "60")))
RATE_LIMIT_MAX_REQUESTS = max(0, int(os.environ.get("RAG_RATE_LIMIT_MAX_REQUESTS", "120")))
PROMPT_CHUNK_CHAR_LIMIT = max(160, int(os.environ.get("RAG_PROMPT_CHUNK_CHAR_LIMIT", "700")))
KB_MIN_SCORE = float(os.environ.get("RAG_KB_MIN_SCORE", "0.28"))
QUIZ_GEN_MAX_TOKENS = max(256, int(os.environ.get("RAG_QUIZ_GEN_MAX_TOKENS", "1200")))
GRADE_MAX_TOKENS = max(256, int(os.environ.get("RAG_QUIZ_GRADE_MAX_TOKENS", "2000")))
MAX_QUIZ_QUESTIONS_TOTAL = max(1, min(50, int(os.environ.get("RAG_MAX_QUIZ_QUESTIONS", "40"))))


def clear_rag_disk_cache_on_shutdown() -> None:
    raw = os.environ.get("RAG_CLEAR_CACHE_ON_SHUTDOWN", "1").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return
    root = Path(os.environ.get("RAG_CACHE_ROOT") or str(DATA_DIR / "vector_cache")).resolve()
    if not root.exists():
        return
    try:
        shutil.rmtree(root, ignore_errors=True)
    except Exception:
        traceback.print_exc()
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        traceback.print_exc()


def resolve_frontend_static_dir() -> Path | None:
    raw = os.environ.get("RAG_FRONTEND_DIR", "").strip()
    candidates: list[Path] = []
    if raw:
        candidates.append(Path(raw).expanduser().resolve())
    candidates.append(REPO_ROOT.parent / "ForRag-frontend")
    candidates.append(REPO_ROOT / "ForRag-gh-pages")
    for p in candidates:
        if p.is_dir():
            return p
    return None
