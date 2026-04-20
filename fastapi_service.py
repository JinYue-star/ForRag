#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将 doc_qa_assistant.py 封装成更安全的 FastAPI 服务。

支持会话持久化上传（含图片 OCR 入库）、ChromaDB 元数据（会话/文件/聊天/测验）、服务端向量缓存（RAG_CACHE_ROOT）与按文件删除时同步清理缓存。

启动（允许局域网其它机器访问，需放行防火墙端口）：
  生产环境建议：set RAG_ACCESS_TOKEN=你的强随机令牌（设置后即默认要求请求带 Bearer）
  py -3.12 -m uvicorn fastapi_service:app --host 0.0.0.0 --port 8000
  本地未设置 RAG_ACCESS_TOKEN 时默认不校验 Bearer；若仍要强制校验：set RAG_REQUIRE_ACCESS_TOKEN=1（须同时配置 RAG_ACCESS_TOKEN）

环境变量：
  RAG_ALLOWED_ORIGINS  逗号分隔的 CORS 白名单（仅当 RAG_CORS_STRICT=1 时生效）
  RAG_CORS_STRICT      设为 1/true 时启用白名单+私网正则；默认关闭（允许任意 Origin，避免跨域被拦）
  RAG_CACHE_ROOT       向量缓存目录（默认项目下 .data/vector_cache）
  RAG_CLEAR_CACHE_ON_SHUTDOWN  默认 1：进程退出（Ctrl+C / 停止 uvicorn）时清空 RAG_CACHE_ROOT 下全部向量缓存；设为 0 可关闭
  RAG_DATA_DIR         持久化数据根目录（默认项目下 .data，其下 chroma/ 为 Chroma 库）
  RAG_RESET_CHROMA     设为 1/true/yes 时启动前清空并重建 chroma/（修复 HNSW 损坏；会丢失会话与 Chroma 内元数据，上传文件仍在 .uploads）
  RAG_FRONTEND_DIR     可选，静态前端目录绝对路径；不设时优先使用与 ForRag 同级的 ForRag-frontend，否则用仓库内 ForRag-gh-pages
  RAG_RATE_LIMIT_MAX_REQUESTS  单 IP 在时间窗口内最大请求数，默认 120；设为 0 关闭限流
  RAG_DEBUG_ERRORS           设为 1/true 时，500 错误返回异常类型与信息（仅排障用）
  RAG_ACCESS_TOKEN         非空时默认启用 Bearer 校验；未设置时本地默认不校验（便于开箱）
  RAG_REQUIRE_ACCESS_TOKEN 显式设为 1/true 则强制校验（须已配置 RAG_ACCESS_TOKEN）；设为 0 则关闭校验
  RAG_STRICT_IMAGE_OCR       默认关闭；设为 1 时若未安装图像 OCR 依赖则直接报错（不写入占位文本）
  RAG_ENABLE_REWRITE         设为 1 时问答前对查询做 LLM 改写（需可用 API 或本地模型）
  RAG_ENABLE_HYBRID          默认开启；设为 0 关闭 dense+BM25 混合检索
  RAG_ENABLE_RERANK          设为 1 时启用 cross-encoder 重排（见 RAG_RERANK_MODEL）
  RAG_EMBED_MODEL_PATH       可选：嵌入模型的本地目录（已提前下载时用），设了则优先本地加载（避免网络问题）
  HF_ENDPOINT                可选；未设置且未设 RAG_USE_OFFICIAL_HF=1 时默认 https://hf-mirror.com
  RAG_USE_OFFICIAL_HF        设为 1 时关闭上述默认镜像，强制走官方 Hub
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import shutil
import threading
import time
import traceback
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator
from starlette.middleware.base import BaseHTTPMiddleware

import chroma_store
import kb_store
import rag_pipeline

from doc_qa_assistant import (
    _normalize_llm_hub,
    _normalized_path,
    build_or_load_index,
    generate_answer,
    generate_answer_via_api,
    invalidate_caches_for_file,
    load_llm,
    route_generation,
    search,
)

# ---------- 默认数据目录与向量缓存根目录 ----------
_REPO_ROOT = Path(__file__).resolve().parent
_DATA_DIR = Path(os.environ.get("RAG_DATA_DIR", str(_REPO_ROOT / ".data"))).resolve()
if not os.environ.get("RAG_CACHE_ROOT"):
    os.environ["RAG_CACHE_ROOT"] = str(_DATA_DIR / "vector_cache")
Path(os.environ["RAG_CACHE_ROOT"]).mkdir(parents=True, exist_ok=True)
_DATA_DIR.mkdir(parents=True, exist_ok=True)
if os.environ.get("RAG_RESET_CHROMA", "").strip().lower() in {"1", "true", "yes"}:
    chroma_store.reset_chroma(_DATA_DIR)
else:
    chroma_store.init_chroma(_DATA_DIR)
kb_store.init_kb_db(_DATA_DIR)


def _parse_allowed_origins() -> list[str]:
    raw = os.environ.get(
        "RAG_ALLOWED_ORIGINS",
        "http://127.0.0.1:3000,http://localhost:3000,http://127.0.0.1:5500,http://localhost:5500,"
        "http://127.0.0.1:8000,http://localhost:8000,"
        "https://jinyue-star.github.io",
    )
    return [item.strip() for item in raw.split(",") if item.strip()]


# 与 allow_origins 并列：匹配私网 IP 任意端口，避免手机/同事用 http://172.x/192.168.x 访问时被 CORS 拦
_CORS_LAN_ORIGIN_REGEX = (
    r"https?://(localhost|127\.0\.0\.1"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r"|10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|172\.(1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3})(:\d+)?"
)


UPLOAD_DIR = Path("./.uploads").resolve()
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

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
ALLOWED_ORIGINS = _parse_allowed_origins()
ACCESS_TOKEN = os.environ.get("RAG_ACCESS_TOKEN", "").strip()
_raw_require_token = os.environ.get("RAG_REQUIRE_ACCESS_TOKEN", "").strip().lower()
if _raw_require_token in {"0", "false", "no", "off"}:
    REQUIRE_ACCESS_TOKEN = False
elif _raw_require_token in {"1", "true", "yes"}:
    REQUIRE_ACCESS_TOKEN = True
else:
    # 未显式配置时：仅当已设置 RAG_ACCESS_TOKEN 时才要求 Bearer，避免本地未配令牌即 503
    REQUIRE_ACCESS_TOKEN = bool(ACCESS_TOKEN)
SERVER_EMBED_MODEL = os.environ.get("MS_EMBED_ID", "BAAI/bge-small-zh-v1.5")
SERVER_MODEL_ID = os.environ.get("MS_MODEL_ID", "Qwen/Qwen2.5-3B-Instruct")
SERVER_LLM_HUB = _normalize_llm_hub(os.environ.get("LLM_HUB", "auto"))
SERVER_LOW_MEMORY = os.environ.get("RAG_LOW_MEMORY", "").strip().lower() in {"1", "true", "yes"}
ENABLE_LOCAL_LLM = os.environ.get("RAG_ENABLE_LOCAL_LLM", "").strip().lower() in {"1", "true", "yes"}
# 未设置环境变量时使用内置 Key；生产环境建议仅使用 DASHSCOPE_API_KEY，勿将真实 Key 提交到公开仓库
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
# 单 IP 在时间窗口内最多请求次数；默认放宽（会话/列表/问答/测验易连点触发）。设为 0 表示关闭限流。
RATE_LIMIT_MAX_REQUESTS = max(0, int(os.environ.get("RAG_RATE_LIMIT_MAX_REQUESTS", "120")))
PROMPT_CHUNK_CHAR_LIMIT = max(160, int(os.environ.get("RAG_PROMPT_CHUNK_CHAR_LIMIT", "700")))
KB_MIN_SCORE = float(os.environ.get("RAG_KB_MIN_SCORE", "0.28"))
QUIZ_GEN_MAX_TOKENS = max(256, int(os.environ.get("RAG_QUIZ_GEN_MAX_TOKENS", "1200")))
GRADE_MAX_TOKENS = max(256, int(os.environ.get("RAG_QUIZ_GRADE_MAX_TOKENS", "2000")))
# 单次测验总题量上限（各段 count 之和）
MAX_QUIZ_QUESTIONS_TOTAL = max(1, min(50, int(os.environ.get("RAG_MAX_QUIZ_QUESTIONS", "40"))))


def _clear_rag_disk_cache_on_shutdown() -> None:
    """进程退出时删除 RAG_CACHE_ROOT 下全部磁盘向量缓存（文档块 / bundle 索引），不删 Chroma 与上传文件。"""
    raw = os.environ.get("RAG_CLEAR_CACHE_ON_SHUTDOWN", "1").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return
    root = Path(os.environ.get("RAG_CACHE_ROOT") or str(_DATA_DIR / "vector_cache")).resolve()
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


@asynccontextmanager
async def _app_lifespan(app: FastAPI):
    yield
    _clear_rag_disk_cache_on_shutdown()


app = FastAPI(
    title="Document QA API",
    version="2.0.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=_app_lifespan,
)

def _cors_strict_mode() -> bool:
    """设为 1/true 时仅允许 RAG_ALLOWED_ORIGINS + 私网正则，否则默认允许任意 Origin（*）。"""
    return os.environ.get("RAG_CORS_STRICT", "").strip().lower() in {"1", "true", "yes"}


def _cors_allow_origins() -> list[str]:
    if _cors_strict_mode():
        return ALLOWED_ORIGINS
    # 默认 *：避免局域网 / 多端口 / 手机访问时漏配 Origin 导致 net::ERR_BLOCKED_BY_RESPONSE
    return ["*"]


def _cors_allow_origin_regex() -> Optional[str]:
    if _cors_strict_mode():
        return _CORS_LAN_ORIGIN_REGEX
    return None


class _PrivateNetworkAccessMiddleware(BaseHTTPMiddleware):
    """Chrome：从公网页或 HTTPS 访问局域网 http API 时，预检需带 Access-Control-Allow-Private-Network。"""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if request.headers.get("access-control-request-private-network", "").lower() == "true":
            response.headers["Access-Control-Allow-Private-Network"] = "true"
        return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_allow_origins(),
    allow_origin_regex=_cors_allow_origin_regex(),
    allow_credentials=False,
    allow_methods=["POST", "GET", "OPTIONS", "DELETE", "HEAD"],
    allow_headers=["Authorization", "Content-Type", "X-Session-Secret", "Accept", "Origin"],
)
app.add_middleware(_PrivateNetworkAccessMiddleware)

_local_llm_cache: dict[str, tuple[object, object]] = {}
_rate_limit_records: dict[str, deque[float]] = defaultdict(deque)
_rate_limit_lock = threading.Lock()
_session_qa_locks: dict[str, threading.Lock] = {}
_session_qa_guard = threading.Lock()


def _session_qa_lock(session_id: str) -> threading.Lock:
    with _session_qa_guard:
        if session_id not in _session_qa_locks:
            _session_qa_locks[session_id] = threading.Lock()
        return _session_qa_locks[session_id]


_qa_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()


def _kb_note_md_path(session_id: str, note_id: str) -> Path:
    return (UPLOAD_DIR / session_id / "kb" / "notes" / f"{note_id}.md").resolve()


def _sync_kb_note_body_file(session_id: str, note_id: str, body: str) -> None:
    path = _kb_note_md_path(session_id, note_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body or "", encoding="utf-8")


def _delete_kb_note_body_file(session_id: str, note_id: str) -> None:
    p = _kb_note_md_path(session_id, note_id)
    if p.is_file():
        try:
            p.unlink()
        except OSError:
            traceback.print_exc()


def _purge_kb_note_from_disk(session_id: str, note_id: str) -> None:
    for row in kb_store.note_files_list(_DATA_DIR, session_id, note_id):
        abs_path = (UPLOAD_DIR / row["stored_rel"]).resolve()
        if abs_path.is_file():
            try:
                invalidate_caches_for_file(abs_path, SERVER_EMBED_MODEL)
            except Exception:
                traceback.print_exc()
            try:
                abs_path.unlink()
            except OSError:
                traceback.print_exc()
    _delete_kb_note_body_file(session_id, note_id)


def _collect_qa_index_inputs(
    session_id: str,
    kb_scope: str,
    category_ids: Optional[list[str]],
) -> tuple[list[Path], dict[str, dict[str, str]], str]:
    """
    返回：参与建索引的路径列表、按规范化路径附加的 chunk 元数据、用于 bundle 缓存的 kb 指纹段。
    """
    scope = (kb_scope or "union").strip().lower()
    if scope not in ("session_files", "kb_only", "union"):
        scope = "union"

    paths: list[Path] = []
    chunk_tags: dict[str, dict[str, str]] = {}
    kb_token = kb_store.session_kb_bundle_token(_DATA_DIR, session_id)

    if scope in ("session_files", "union"):
        for r in chroma_store.file_list(session_id):
            p = (UPLOAD_DIR / r["stored_rel"]).resolve()
            if p.is_file():
                paths.append(p)
                chunk_tags[_normalized_path(p)] = {"session_file_id": str(r["id"])}

    if scope in ("kb_only", "union"):
        for nid in kb_store.kb_note_ids_for_rag(_DATA_DIR, session_id, category_ids):
            body_path = _kb_note_md_path(session_id, nid)
            if body_path.is_file():
                paths.append(body_path)
                chunk_tags[_normalized_path(body_path)] = {"kb_note_id": nid}
        for row in kb_store.kb_attachment_rows_for_rag(_DATA_DIR, session_id, category_ids):
            p = (UPLOAD_DIR / row["stored_rel"]).resolve()
            if p.is_file():
                paths.append(p)
                chunk_tags[_normalized_path(p)] = {
                    "kb_note_id": str(row["note_id"]),
                    "kb_attachment_id": str(row["id"]),
                }

    seen: set[str] = set()
    uniq: list[Path] = []
    for p in paths:
        key = str(p.resolve())
        if key not in seen:
            seen.add(key)
            uniq.append(p)

    bundle_extra = kb_token if scope in ("kb_only", "union") else ""
    return uniq, chunk_tags, bundle_extra


def _session_qa_worker(
    job_id: str,
    sid: str,
    secret: str,
    question: str,
    top_k: int,
    max_new_tokens: Optional[int],
    kb_scope: str = "union",
    category_ids_json: Optional[str] = None,
) -> None:
    try:
        _verify_session(sid, secret)
        cat_ids = kb_store.parse_category_ids_json(category_ids_json)
        saved_paths, chunk_tags, bundle_extra = _collect_qa_index_inputs(sid, kb_scope, cat_ids)
        if not saved_paths:
            with _jobs_lock:
                _qa_jobs[job_id] = {
                    "status": "error",
                    "detail": "没有可检索的内容：请上传会话文件和/或在知识库中添加笔记或附件",
                }
            return
        for p in saved_paths:
            if not p.is_file():
                with _jobs_lock:
                    _qa_jobs[job_id] = {"status": "error", "detail": "服务器上文件缺失，请重新上传或同步知识库"}
                return
        limited_top_k = max(1, min(int(top_k), MAX_TOP_K))
        with _session_qa_lock(sid):
            chunks, _embeddings, index, st = build_or_load_index(
                saved_paths,
                SERVER_EMBED_MODEL,
                bundle_extra=bundle_extra,
                chunk_tags_by_norm_path=chunk_tags,
            )
            hits = rag_pipeline.hybrid_retrieve(
                question,
                chunks,
                index,
                st,
                lambda prompt, max_tok, **kw: _invoke_llm(prompt, max_tok, **kw),
                limited_top_k,
            )
            resp = _run_qa_pipeline(question=question, hits=hits, max_new_tokens=max_new_tokens)
        now = time.time()
        uid = uuid.uuid4().hex
        aid = uuid.uuid4().hex
        chroma_store.message_add(uid, sid, "user", question.strip(), now)
        extra: dict[str, Any] = {"route": resp.route, "kb_relevant": resp.kb_relevant}
        if resp.no_kb_notice:
            extra["no_kb_notice"] = resp.no_kb_notice
        if resp.citations:
            extra["citations"] = [c.model_dump() for c in resp.citations]
        chroma_store.message_add(aid, sid, "assistant", resp.answer, now + 0.001, extra=extra)
        with _jobs_lock:
            _qa_jobs[job_id] = {
                "status": "done",
                "assistant_message_id": aid,
                "user_message_id": uid,
                "result": (
                    resp.model_dump()
                    if hasattr(resp, "model_dump")
                    else resp.dict()  # type: ignore[no-untyped-call]
                ),
            }
    except HTTPException as he:
        with _jobs_lock:
            _qa_jobs[job_id] = {"status": "error", "detail": str(he.detail)}
    except Exception as e:
        traceback.print_exc()
        with _jobs_lock:
            _qa_jobs[job_id] = {"status": "error", "detail": _server_error_detail(e)}


class HitItem(BaseModel):
    score: float
    source: str
    page_label: str
    meta: str
    content: str
    chunk_id: str = ""
    kb_note_id: Optional[str] = None
    kb_attachment_id: Optional[str] = None
    session_file_id: Optional[str] = None


class CitationItem(BaseModel):
    ref: int
    score: float
    source_label: str
    excerpt: str
    chunk_id: str = ""
    kb_note_id: Optional[str] = None
    kb_attachment_id: Optional[str] = None
    session_file_id: Optional[str] = None


class QuizItemPublic(BaseModel):
    index: int
    type: str
    question: str
    options: Optional[list[str]] = None


class QuizBundlePublic(BaseModel):
    quiz_id: str
    items: list[QuizItemPublic]


class QAResponse(BaseModel):
    answer: str
    route: str
    hits: list[HitItem]
    kb_relevant: bool = True
    no_kb_notice: Optional[str] = None
    quiz: Optional[QuizBundlePublic] = None
    citations: list[CitationItem] = Field(default_factory=list)


class QuizGradeRequest(BaseModel):
    answers: list[str]


class QuizGradeItemResult(BaseModel):
    index: int
    question: str = ""
    question_type: str
    score: float
    max_score: float
    user_answer: str
    correct_answer: str
    comment: str


class QuizGradeResponse(BaseModel):
    total_score: float
    max_total_score: float
    items: list[QuizGradeItemResult]
    analysis: str


class SessionCreateResponse(BaseModel):
    session_id: str
    session_secret: str


class SessionFileItem(BaseModel):
    id: str
    original_name: str
    size_bytes: int


class KbCategoryCreate(BaseModel):
    name: str
    sort_order: int = 0
    owner_id: Optional[str] = None


class KbCategoryPatch(BaseModel):
    name: Optional[str] = None
    sort_order: Optional[int] = None


class KbNoteCreate(BaseModel):
    title: str
    body_markdown: str = ""
    owner_id: Optional[str] = None


class KbNotePatch(BaseModel):
    title: Optional[str] = None
    body_markdown: Optional[str] = None
    category_id: Optional[str] = None


class KbNoteFileItem(BaseModel):
    id: str
    note_id: str
    session_id: str = ""
    original_name: str
    stored_rel: str
    size_bytes: int
    mime: Optional[str] = None
    created_at: float = 0
    updated_at: float = 0


class ChatMessageItem(BaseModel):
    id: str
    role: str
    content: str
    created_at: float
    extra: Optional[dict[str, Any]] = None


class QAJobStartResponse(BaseModel):
    job_id: str


class QAJobStatusResponse(BaseModel):
    status: str
    detail: Optional[str] = None
    assistant_message_id: Optional[str] = None
    user_message_id: Optional[str] = None
    result: Optional[dict[str, Any]] = None


class QuizSegmentSpec(BaseModel):
    """一段对话（通常选助手消息）上要生成的题量。"""

    message_id: str
    count: int = Field(1, ge=1, le=20)


class QuizGenerateRequest(BaseModel):
    """优先使用 segments；仅传 message_ids 时为兼容旧前端（每条按 1 题）。"""

    segments: Optional[list[QuizSegmentSpec]] = None
    message_ids: Optional[list[str]] = None

    @model_validator(mode="after")
    def _coalesce_segments(self):
        if self.segments:
            return self
        if self.message_ids:
            self.segments = [QuizSegmentSpec(message_id=m, count=1) for m in self.message_ids]
            return self
        raise ValueError("请提供 segments 或 message_ids")


def _hash_session_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _client_ip(request: Request) -> str:
    for header_name in ("cf-connecting-ip", "x-forwarded-for", "x-real-ip"):
        value = request.headers.get(header_name, "").strip()
        if value:
            return value.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _check_rate_limit(client_ip: str) -> None:
    if RATE_LIMIT_MAX_REQUESTS <= 0:
        return
    now = time.time()
    with _rate_limit_lock:
        window = _rate_limit_records[client_ip]
        while window and now - window[0] > RATE_LIMIT_WINDOW_SECONDS:
            window.popleft()
        if len(window) >= RATE_LIMIT_MAX_REQUESTS:
            raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")
        window.append(now)


def _server_error_detail(exc: BaseException) -> str:
    """将内部异常转换为用户可见文案；开发排障可设 RAG_DEBUG_ERRORS=1。"""
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


def _require_access_token(authorization: Optional[str]) -> None:
    if not REQUIRE_ACCESS_TOKEN:
        return
    if not ACCESS_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="已启用访问令牌校验（RAG_REQUIRE_ACCESS_TOKEN=1），但未设置 RAG_ACCESS_TOKEN。请设置强随机令牌后重启，或设为 0 关闭校验。",
        )
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="缺少访问令牌")
    token = authorization[7:].strip()
    if not token or not hmac.compare_digest(token, ACCESS_TOKEN):
        raise HTTPException(status_code=401, detail="访问令牌无效")


def _parse_uuid_param(name: str, value: str) -> str:
    try:
        u = uuid.UUID(value)
        return str(u)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"无效的{name}") from e


def _verify_session(session_id: str, session_secret: str) -> None:
    row = chroma_store.session_get(session_id)
    if not row:
        raise HTTPException(status_code=404, detail="会话不存在")
    if not hmac.compare_digest(row["secret_hash"], _hash_session_secret(session_secret)):
        raise HTTPException(status_code=403, detail="会话密钥无效")
    chroma_store.session_update_last_seen(session_id, time.time())


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
    return cleaned[: limit - 3].rstrip() + "..."


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


def _build_strategy_prompt(question: str, hits: list[tuple[float, object]]) -> str:
    if not hits:
        return (
            "你是文档问答助手。\n"
            "当前没有检索到任何有效文档片段。请明确告诉用户证据不足，"
            "并建议重新上传更相关的文档或换个问法。\n\n"
            f"用户问题：{question}"
        )

    evidence_blocks = []
    for idx, (score, chunk) in enumerate(hits, start=1):
        evidence_blocks.append(
            "\n".join(
                [
                    f"[{idx}] 来源: {chunk.source}",
                    f"位置: {chunk.page_label}",
                    f"说明: {chunk.meta}",
                    f"相关度: {score:.4f}",
                    f"内容: {_compact_text(chunk.text, limit=PROMPT_CHUNK_CHAR_LIMIT)}",
                ]
            )
        )

    evidence_text = "\n\n".join(evidence_blocks)
    return (
        "你是一个严谨的文档问答助手。"
        "请只依据下面给出的检索证据回答，不要引入证据外事实。\n"
        "回答策略：\n"
        "1. 先给出简洁直接的结论。\n"
        "2. 再补充关键依据；每条依据须在句末或段末用半角方括号引用证据编号，例如 [1]、[2]，编号必须与下方证据条目序号一致。\n"
        "3. 若证据不充分，明确指出“不确定”并说明缺什么信息。\n"
        "4. 不要复述整段原文，不要编造编号或结论。\n\n"
        f"用户问题：{question}\n\n"
        f"检索证据：\n{evidence_text}"
    )


def _invoke_llm(
    user_msg: str,
    max_new_tokens: Optional[int],
    *,
    json_object: bool = False,
) -> tuple[str, str]:
    route = route_generation(has_api_key=bool(SERVER_API_KEY))
    if route == "api":
        try:
            answer = generate_answer_via_api(
                api_key=SERVER_API_KEY,
                api_model=SERVER_API_MODEL,
                api_base=SERVER_API_BASE,
                user_msg=user_msg,
                max_new_tokens=max_new_tokens,
                stream=False,
                json_object=json_object,
            )
            return (answer or "").strip(), "api"
        except Exception:
            traceback.print_exc()
            return "", "api_error"

    if ENABLE_LOCAL_LLM:
        try:
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
                user_msg=user_msg,
                max_new_tokens=max_new_tokens,
                stream=False,
            )
            return (answer or "").strip(), "local"
        except Exception:
            traceback.print_exc()
            return "", "local_error"

    return "", "fallback"


def _generate_strategy_answer(
    question: str,
    hits: list[tuple[float, object]],
    max_new_tokens: Optional[int],
) -> tuple[str, str]:
    prompt = _build_strategy_prompt(question, hits)
    text, route = _invoke_llm(prompt, max_new_tokens)
    if text:
        return text, route
    fb = _build_fallback_answer(question, hits)
    if route.startswith("api"):
        return fb, "api_fallback"
    if route.startswith("local"):
        return fb, "local_fallback"
    return fb, "fallback"


def _hits_are_relevant(hits: list[tuple[float, object]]) -> bool:
    if not hits:
        return False
    return float(hits[0][0]) >= KB_MIN_SCORE


def _build_no_kb_prompt(question: str) -> str:
    return (
        "你是可靠的助手。用户已上传文档作为知识库，但检索结果表明：当前知识库中未找到与问题直接相关、"
        "或相关度足够高的片段。\n"
        "请先简要说明这一情况（一至两段话）。然后基于你的通用知识回答用户问题；若问题强依赖未提供的专有材料，"
        "请明确说明无法从通用知识确认。请勿编造文档引用。\n\n"
        f"用户问题：{question}"
    )


def _generate_general_knowledge_answer(
    question: str,
    max_new_tokens: Optional[int],
) -> tuple[str, str]:
    prompt = _build_no_kb_prompt(question)
    text, route = _invoke_llm(prompt, max_new_tokens)
    if text:
        return text, route
    return (
        "【说明】知识库中未检索到与问题足够相关的内容，且当前未配置可用的语言模型（请设置 DASHSCOPE_API_KEY "
        "或启用本地模型 RAG_ENABLE_LOCAL_LLM），无法生成基于通用知识的回答。"
    ), "fallback"


def _llm_available() -> bool:
    return bool(SERVER_API_KEY) or ENABLE_LOCAL_LLM


def _extract_json_object(text: str) -> Optional[dict]:
    raw = (text or "").strip()
    if not raw:
        return None
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
    if fence:
        raw = fence.group(1).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None


def _extract_json_items_loose(text: str) -> Optional[dict]:
    """模型偶发只输出半截 JSON 或多嵌套引号时，尝试从原文中抠出 items 数组。"""
    raw = text or ""
    d = _extract_json_object(raw)
    if isinstance(d, dict) and isinstance(d.get("items"), list):
        return d
    for needle in ('"items"', "'items'"):
        idx = raw.find(needle)
        if idx < 0:
            continue
        sub = raw[idx:]
        br = sub.find("[")
        if br < 0:
            continue
        start = idx + br
        depth = 0
        for i in range(start, len(raw)):
            c = raw[i]
            if c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
                if depth == 0:
                    try:
                        arr = json.loads(raw[start : i + 1])
                        if isinstance(arr, list):
                            return {"items": arr}
                    except json.JSONDecodeError:
                        pass
                    break
    return None


def _quiz_type_counts_tf_single_multi(n: int) -> tuple[int, int, int]:
    """Target mix: True/False (tf), single-select (single), multi-select (multi); sums to n."""
    if n <= 0:
        return 0, 0, 0
    if n == 1:
        return 0, 1, 0
    if n == 2:
        return 1, 1, 0
    tf_n = max(1, n // 4)
    multi_n = max(1, n // 4)
    single_n = n - tf_n - multi_n
    if single_n < 1:
        single_n = 1
        rem = n - single_n
        tf_n = rem // 2
        multi_n = rem - tf_n
    return tf_n, single_n, multi_n


def _coerce_quiz_index(val: Any) -> Optional[int]:
    if isinstance(val, bool):
        return None
    if isinstance(val, int):
        return val
    if isinstance(val, float) and val == int(val):
        return int(val)
    if isinstance(val, str) and val.strip().lstrip("-").isdigit():
        return int(val.strip())
    return None


def _normalize_quiz_items_flexible(data: dict, n: int, forbidden_questions: set[str]) -> Optional[list[dict]]:
    """Exactly n items: types tf | single | multi; English stems expected from prompt."""
    items = data.get("items")
    if not isinstance(items, list) or len(items) != n:
        return None
    out: list[dict] = []
    seen_q: set[str] = set()
    for it in items:
        if not isinstance(it, dict):
            return None
        t = str(it.get("type", "")).lower().strip()
        if t not in ("tf", "single", "multi"):
            return None
        q = str(it.get("question", "")).strip()
        if not q:
            return None
        q_key = q.casefold()
        if q_key in seen_q:
            return None
        seen_q.add(q_key)
        if q_key in forbidden_questions:
            return None
        row: dict = {"type": t, "question": q}
        if t == "tf":
            opts = it.get("options")
            if not isinstance(opts, list) or len(opts) != 2:
                return None
            raw_opts = [str(x).strip() for x in opts]
            a0, a1 = raw_opts[0].casefold(), raw_opts[1].casefold()
            if {a0, a1} != {"true", "false"}:
                return None
            ci_raw = _coerce_quiz_index(it.get("correct_index"))
            if ci_raw is None or ci_raw not in (0, 1):
                return None
            correct_word = raw_opts[ci_raw].casefold()
            if correct_word not in ("true", "false"):
                return None
            row["options"] = ["True", "False"]
            row["correct_index"] = 0 if correct_word == "true" else 1
        elif t == "single":
            opts = it.get("options")
            if not isinstance(opts, list) or len(opts) < 2 or len(opts) > 6:
                return None
            row["options"] = [str(x).strip() for x in opts]
            ci = _coerce_quiz_index(it.get("correct_index"))
            if ci is None or ci < 0 or ci >= len(row["options"]):
                return None
            row["correct_index"] = ci
        else:
            opts = it.get("options")
            if not isinstance(opts, list) or len(opts) < 3 or len(opts) > 8:
                return None
            row["options"] = [str(x).strip() for x in opts]
            cis_raw = it.get("correct_indices")
            if not isinstance(cis_raw, list) or len(cis_raw) < 2:
                return None
            cis: list[int] = []
            for x in cis_raw:
                j = _coerce_quiz_index(x)
                if j is None or j < 0 or j >= len(row["options"]):
                    return None
                cis.append(j)
            cis = sorted(set(cis))
            if len(cis) < 2:
                return None
            row["correct_indices"] = cis
        out.append(row)
    return out


def _build_quiz_generation_prompt_v3(
    total_n: int,
    segment_blocks: str,
    hits: list[tuple[float, object]],
    forbidden_lines: list[str],
) -> str:
    tf_n, single_n, multi_n = _quiz_type_counts_tf_single_multi(total_n)
    evidence_blocks = []
    for idx, (score, chunk) in enumerate(hits[:5], start=1):
        evidence_blocks.append(
            f"[{idx}] source:{chunk.source} location:{chunk.page_label} relevance:{score:.4f}\n"
            f"{_compact_text(chunk.text, limit=500)}"
        )
    evidence_text = "\n\n".join(evidence_blocks)
    forbid = "\n".join(f"- {line[:200]}" for line in forbidden_lines[:80]) if forbidden_lines else "(none)"
    return (
        "You are an expert educator. Design a quiz that helps learners **understand concepts**, not just memorize phrases. "
        "Use clear English. Each question should have a concise stem, test one main idea, and include a short "
        "explanation-worthy distractor rationale (implicitly, via plausible wrong options).\n\n"
        f"You MUST output exactly {total_n} items in total, matching the per-segment counts in the segment block below.\n"
        "Question type counts (must match exactly):\n"
        f"- type \"tf\" (True/False): {tf_n} items\n"
        f"- type \"single\" (single-choice): {single_n} items — provide exactly 4 options unless only 2 are pedagogically justified; prefer 4.\n"
        f"- type \"multi\" (multiple-select): {multi_n} items — provide 4–6 options and field \"correct_indices\": a sorted JSON array "
        "of distinct 0-based indices (at least two correct options).\n\n"
        "JSON schema per item:\n"
        "- tf: {\"type\":\"tf\",\"question\":\"...\",\"options\":[\"True\",\"False\"],\"correct_index\":0 or 1}\n"
        "- single: {\"type\":\"single\",\"question\":\"...\",\"options\":[\"A\",\"B\",\"C\",\"D\"],\"correct_index\":0..3}\n"
        "- multi: {\"type\":\"multi\",\"question\":\"...\",\"options\":[...],\"correct_indices\":[0,2]}\n\n"
        "Rules: Ground every question in the segment text and retrieval evidence; avoid duplicate or near-duplicate stems; "
        "do not repeat any question similar to these prior stems:\n"
        f"{forbid}\n\n"
        "Output ONE JSON object only, no markdown fences, no commentary. Shape: "
        '{"items":[ ... exactly '
        f"{total_n} "
        "objects ... ]}\n\n"
        "### Segment requirements (counts per assistant excerpt)\n"
        f"{segment_blocks}\n\n"
        "### Retrieval evidence\n"
        f"{evidence_text}"
    )


def _fallback_quiz_bundle_from_hits(
    hits: list[tuple[float, object]],
    resolved_segments: list[tuple[str, str, int]],
    forbidden_lower: set[str],
    total_n: int,
) -> Optional[dict]:
    """
    大模型不可用或 JSON 校验失败时，用摘录生成 tf/single 兜底（英文），避免 503。
    """
    snippets: list[str] = []
    for _mid, excerpt, _cnt in resolved_segments:
        t = _compact_text(excerpt, 500)
        if t:
            snippets.append(t)
    for _score, chunk in hits:
        t = _compact_text(getattr(chunk, "text", "") or "", 500)
        if t:
            snippets.append(t)
    if not snippets:
        return None
    tf_n, single_n, multi_n = _quiz_type_counts_tf_single_multi(total_n)
    # Reliable offline authoring: multi-select is folded into single-choice here.
    single_n += multi_n
    items: list[dict] = []
    ti = si = 0
    for idx in range(total_n):
        base = snippets[idx % len(snippets)]
        if ti < tf_n:
            ti += 1
            excerpt = _compact_text(base, 320)
            q = (
                f"True or False: The following accurately reflects the source material: "
                f"\"{excerpt}\""
            )
            if len(base) > 320:
                q += " …"
            if q.casefold() in forbidden_lower:
                q = f"{q} (item {idx + 1})"
            items.append({"type": "tf", "question": q, "options": ["True", "False"], "correct_index": 0})
        else:
            si += 1
            opts = [
                f"Main takeaway: {_compact_text(base, 120)}",
                "A plausible but unsupported inference",
                "An irrelevant detail",
                "The opposite of the correct conclusion",
            ]
            q = f"Single choice — what does the excerpt best support?\n【Excerpt】{_compact_text(base, 360)}"
            if len(base) > 360:
                q += "…"
            if q.casefold() in forbidden_lower:
                q = f"{q} (#{idx + 1})"
            items.append({"type": "single", "question": q, "options": opts, "correct_index": 0})

    normalized = _normalize_quiz_items_flexible({"items": items}, total_n, forbidden_lower)
    if not normalized:
        return None
    return {"items": normalized}


def _merge_quiz_segments(segments: list[QuizSegmentSpec]) -> list[QuizSegmentSpec]:
    acc: dict[str, int] = {}
    for s in segments:
        k = s.message_id.strip()
        acc[k] = acc.get(k, 0) + s.count
    return [QuizSegmentSpec(message_id=k, count=v) for k, v in acc.items()]


def _generate_quiz_bundle_for_segments(
    hits: list[tuple[float, object]],
    resolved_segments: list[tuple[str, str, int]],
    forbidden_lower: set[str],
    total_n: int,
) -> tuple[Optional[dict], str]:
    """返回 (bundle 或 None, 失败原因码)。原因码用于日志与 503 明细。"""
    if total_n <= 0:
        return None, "bad_total"
    last_fail = "llm_empty"
    prev_texts = [t for t in forbidden_lower if t]
    lines: list[str] = []
    for i, (mid, excerpt, cnt) in enumerate(resolved_segments, start=1):
        lines.append(
            f"Segment {i} (message_id={mid}): write exactly {cnt} question(s) grounded in:\n"
            f"{_compact_text(excerpt, limit=900)}"
        )
    segment_blocks = "\n\n".join(lines)
    base_prompt = _build_quiz_generation_prompt_v3(total_n, segment_blocks, hits, prev_texts)
    max_tok = min(12000, max(512, QUIZ_GEN_MAX_TOKENS, 400 + total_n * 320))

    if _llm_available():
        for attempt in range(3):
            extra = ""
            if attempt == 1:
                extra = (
                    "\n\n[Retry] Previous output failed validation. Output ONE JSON object only; "
                    f"top-level key \"items\" must be an array of length exactly {total_n}. "
                    "No markdown, no prose outside JSON."
                )
            elif attempt == 2:
                extra = (
                    f"\n\n[Retry] Minimal output: {{\"items\":[...]}} with {total_n} objects. "
                    "Types: tf | single | multi only; multi must include \"correct_indices\" (array of ints)."
                )
            prompt = base_prompt + extra
            # 首次请求优先 JSON 模式（兼容接口不支持时会自动回退）；重试改用普通生成更易出完整长 JSON
            use_json_mode = attempt == 0
            text, _route = _invoke_llm(prompt, max_tok, json_object=use_json_mode)
            if not (text or "").strip():
                last_fail = "llm_empty"
                continue
            data = _extract_json_object(text) or _extract_json_items_loose(text)
            if not isinstance(data, dict):
                last_fail = "bad_json"
                continue
            items = _normalize_quiz_items_flexible(data, total_n, forbidden_lower)
            if not items:
                last_fail = "bad_items"
                continue
            return {"items": items}, "ok"

    fb = _fallback_quiz_bundle_from_hits(hits, resolved_segments, forbidden_lower, total_n)
    if fb:
        logging.warning(
            "quiz/generate: fallback items (tf/single) from retrieval — LLM JSON failed or API error"
        )
        return fb, "ok"

    if not _llm_available():
        return None, "no_llm"
    return None, last_fail


def _quiz_generation_fail_detail(code: str) -> str:
    """测验生成失败时返回给客户端的说明（503 body）。"""
    if code == "no_llm":
        return "无法生成测验：未配置可用的语言模型（请设置 DASHSCOPE_API_KEY 或 RAG_ENABLE_LOCAL_LLM=1）。"
    if code == "bad_total":
        return "无法生成测验：题目总数无效。"
    if code == "llm_empty":
        return (
            "无法生成测验：大模型无返回或 API 调用失败。请检查 DASHSCOPE_API_KEY 是否有效、网络与账户额度，"
            "或稍后重试。排障可设置环境变量 RAG_DEBUG_ERRORS=1 查看服务端日志。"
        )
    if code == "bad_json":
        return "无法生成测验：模型返回内容无法解析为 JSON。请减少题目数量或稍后重试。"
    if code == "bad_items":
        return (
            "无法生成测验：题目格式未通过校验（题量与各题型数量须符合要求）。请减少单次出题数量或稍后重试。"
        )
    return "无法生成测验（请配置 DASHSCOPE_API_KEY 或本地模型，或稍后重试）。"


def _build_quiz_public(quiz_id: str, items: list[dict]) -> QuizBundlePublic:
    pub_items: list[QuizItemPublic] = []
    for i, it in enumerate(items):
        t = str(it.get("type", "single")).lower()
        opts = None
        if t in ("tf", "single", "multi"):
            opts = [str(x) for x in (it.get("options") or [])]
        pub_items.append(
            QuizItemPublic(
                index=i,
                type=t,
                question=str(it.get("question", "")).strip(),
                options=opts,
            )
        )
    return QuizBundlePublic(quiz_id=quiz_id, items=pub_items)


def _run_qa_pipeline(
    question: str,
    hits: list[tuple[float, object]],
    max_new_tokens: Optional[int],
) -> QAResponse:
    kb_rel = _hits_are_relevant(hits)
    no_kb_notice: Optional[str] = None

    if not kb_rel:
        answer, route = _generate_general_knowledge_answer(question, max_new_tokens)
        no_kb_notice = "当前知识库中未检索到与问题足够相关的片段，以下为基于模型通用知识的回答（仅供参考，非文档结论）。"
    else:
        answer, route = _generate_strategy_answer(question, hits, max_new_tokens)

    hit_items = [
        HitItem(
            score=score,
            source=chunk.source,
            page_label=chunk.page_label,
            meta=chunk.meta,
            content=_compact_text(chunk.text, limit=360),
            chunk_id=getattr(chunk, "chunk_id", "") or "",
            kb_note_id=(getattr(chunk, "kb_note_id", "") or None),
            kb_attachment_id=(getattr(chunk, "kb_attachment_id", "") or None),
            session_file_id=(getattr(chunk, "session_file_id", "") or None),
        )
        for score, chunk in hits
    ]

    cite_raw = rag_pipeline.build_citations(answer, hits) if kb_rel else []
    citations = [CitationItem(**c) for c in cite_raw]

    return QAResponse(
        answer=answer,
        route=route,
        hits=hit_items,
        kb_relevant=kb_rel,
        no_kb_notice=no_kb_notice,
        quiz=None,
        citations=citations,
    )


def _format_correct_for_item(it: dict) -> str:
    t = str(it.get("type", "")).lower()
    opts = it.get("options") or []
    if t == "tf" or t == "single":
        ci = _coerce_quiz_index(it.get("correct_index"))
        if ci is not None and 0 <= ci < len(opts):
            return f"{chr(65 + ci)}. {opts[ci]}"
        return str(it.get("correct_index", ""))
    if t == "multi":
        cis = it.get("correct_indices") or []
        parts: list[str] = []
        if isinstance(cis, list):
            js = sorted({_coerce_quiz_index(x) for x in cis if _coerce_quiz_index(x) is not None})
            for j in js:
                if 0 <= j < len(opts):
                    parts.append(f"{chr(65 + j)}. {opts[j]}")
        return "; ".join(parts)
    return ""


def _grade_quiz_with_llm(payload: dict, user_answers: list[str]) -> QuizGradeResponse:
    items = payload.get("items") or []
    n = len(items)
    if n == 0:
        return QuizGradeResponse(
            total_score=0.0,
            max_total_score=100.0,
            items=[],
            analysis="测验题目为空，无法判分。",
        )
    per_hint = round(100.0 / n, 2)
    grading_input = []
    for i, it in enumerate(items):
        ua = user_answers[i] if i < len(user_answers) else ""
        grading_input.append(
            {
                "index": i,
                "type": it.get("type"),
                "question": it.get("question"),
                "standard": _format_correct_for_item(it),
                "user_answer": (ua or "").strip(),
            }
        )
    prompt = (
        f"You are an expert grader. Score exactly {n} items; total must sum to 100 points across items.\n"
        "Types: tf / single / multi. For tf and single, compare the user answer string to the keyed option text. "
        "For multi, the user answer is a comma-separated list of option indices (e.g. \"0,2\"); award full credit only "
        "if the set matches correct_indices; partial credit if clearly justified.\n"
        f"Target max_score per item ≈ {per_hint} (adjust so item max_scores sum to 100).\n"
        "Output ONE JSON object only:\n"
        '{"total_score":number,"max_total_score":100,"items":['
        '{"index":0,"question":"echo the stem text","question_type":"tf|single|multi","score":number,"max_score":number,'
        '"user_answer":"...","correct_answer":"...","comment":"brief feedback in English"},...],'
        '"analysis":"overall feedback and study tips in English"}\n\n'
        f"Problems and answers: {json.dumps(grading_input, ensure_ascii=False)}"
    )
    text, _r = _invoke_llm(prompt, GRADE_MAX_TOKENS)
    parsed = _extract_json_object(text or "")
    per_default = 100.0 / n
    if isinstance(parsed, dict) and isinstance(parsed.get("items"), list):
        try:
            total = float(parsed.get("total_score", 0))
            max_tot = float(parsed.get("max_total_score", 100))
            analysis = str(parsed.get("analysis", "") or "").strip() or (text or "（模型未返回解析）")
            out_items: list[QuizGradeItemResult] = []
            for row in parsed["items"]:
                if not isinstance(row, dict):
                    continue
                idx = int(row.get("index", 0))
                stem = str(row.get("question", "") or "").strip()
                if not stem and 0 <= idx < len(items):
                    stem = str(items[idx].get("question") or "").strip()
                out_items.append(
                    QuizGradeItemResult(
                        index=idx,
                        question=stem,
                        question_type=str(row.get("question_type", "")),
                        score=float(row.get("score", 0)),
                        max_score=float(row.get("max_score", per_default)),
                        user_answer=str(row.get("user_answer", "")),
                        correct_answer=str(row.get("correct_answer", "")),
                        comment=str(row.get("comment", "")),
                    )
                )
            out_items.sort(key=lambda x: x.index)
            return QuizGradeResponse(
                total_score=total,
                max_total_score=max_tot,
                items=out_items,
                analysis=analysis,
            )
        except (TypeError, ValueError):
            pass
    return QuizGradeResponse(
        total_score=0.0,
        max_total_score=100.0,
        items=[],
        analysis=text or "判分结果解析失败，请稍后重试。",
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/v1/sessions", response_model=SessionCreateResponse)
def create_session(
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> SessionCreateResponse:
    """创建会话，返回 session_secret（仅显示一次，请妥善保存）。"""
    _require_access_token(authorization)
    _check_rate_limit(_client_ip(request))

    sid = str(uuid.uuid4())
    secret = secrets.token_hex(32)
    now = time.time()
    chroma_store.session_insert(sid, _hash_session_secret(secret), now, now)
    return SessionCreateResponse(session_id=sid, session_secret=secret)


@app.get("/api/v1/sessions/{session_id}/files", response_model=list[SessionFileItem])
def list_session_files(
    request: Request,
    session_id: str,
    authorization: Optional[str] = Header(default=None),
    x_session_secret: str = Header(..., alias="X-Session-Secret"),
) -> list[SessionFileItem]:
    _require_access_token(authorization)
    _check_rate_limit(_client_ip(request))
    sid = _parse_uuid_param("session_id", session_id)

    _verify_session(sid, x_session_secret.strip())
    rows = chroma_store.file_list(sid)
    return [
        SessionFileItem(id=r["id"], original_name=r["original_name"], size_bytes=int(r["size_bytes"]))
        for r in rows
    ]


@app.post("/api/v1/sessions/{session_id}/files", response_model=list[SessionFileItem])
async def upload_session_files(
    request: Request,
    session_id: str,
    authorization: Optional[str] = Header(default=None),
    x_session_secret: str = Header(..., alias="X-Session-Secret"),
    files: list[UploadFile] = File(...),
) -> list[SessionFileItem]:
    _require_access_token(authorization)
    _check_rate_limit(_client_ip(request))
    sid = _parse_uuid_param("session_id", session_id)
    if not files:
        raise HTTPException(status_code=400, detail="至少上传一个文件")

    # 先读入并校验，避免先写入后才发现后续文件不合法
    prepared: list[tuple[str, bytes]] = []
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
        prepared.append((f.filename, content))

    if not prepared:
        raise HTTPException(status_code=400, detail="未接收到有效文件")

    secret = x_session_secret.strip()
    _verify_session(sid, secret)
    count = len(chroma_store.file_list(sid))
    if count + len(prepared) > MAX_FILES:
        raise HTTPException(status_code=400, detail=f"最多只能上传 {MAX_FILES} 个文件")

    session_dir = UPLOAD_DIR / sid
    session_dir.mkdir(parents=True, exist_ok=True)

    existing_rels = chroma_store.file_list(sid)
    used_names = {Path(r["stored_rel"]).name for r in existing_rels}

    out: list[SessionFileItem] = []
    now = time.time()

    for orig_filename, content in prepared:
        safe_name = _safe_filename(orig_filename, used_names)
        file_id = uuid.uuid4().hex
        disk_name = f"{file_id}_{safe_name}"
        stored_rel = f"{sid}/{disk_name}"
        dest = UPLOAD_DIR / stored_rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)

        _verify_session(sid, secret)
        chroma_store.file_insert(
            file_id,
            sid,
            Path(orig_filename).name,
            stored_rel,
            len(content),
            now,
        )
        out.append(SessionFileItem(id=file_id, original_name=Path(orig_filename).name, size_bytes=len(content)))

    return out


@app.delete("/api/v1/sessions/{session_id}/files/{file_id}")
def delete_session_file(
    request: Request,
    session_id: str,
    file_id: str,
    authorization: Optional[str] = Header(default=None),
    x_session_secret: str = Header(..., alias="X-Session-Secret"),
) -> dict[str, str]:
    _require_access_token(authorization)
    _check_rate_limit(_client_ip(request))
    sid = _parse_uuid_param("session_id", session_id)
    try:
        fid = uuid.UUID(file_id).hex
    except ValueError as e:
        raise HTTPException(status_code=400, detail="无效的文件 ID") from e

    _verify_session(sid, x_session_secret.strip())
    row = chroma_store.file_get(sid, fid)
    if not row:
        raise HTTPException(status_code=404, detail="文件不存在")

    abs_path = (UPLOAD_DIR / row["stored_rel"]).resolve()
    if abs_path.is_file():
        try:
            invalidate_caches_for_file(abs_path, SERVER_EMBED_MODEL)
        except Exception:
            traceback.print_exc()
        try:
            abs_path.unlink()
        except OSError as e:
            raise HTTPException(status_code=500, detail=f"删除文件失败: {e}") from e

    chroma_store.file_delete(fid)

    return {"status": "deleted"}


@app.get("/api/v1/sessions/{session_id}/kb/categories")
def kb_list_categories(
    request: Request,
    session_id: str,
    authorization: Optional[str] = Header(default=None),
    x_session_secret: str = Header(..., alias="X-Session-Secret"),
) -> list[dict[str, Any]]:
    _require_access_token(authorization)
    _check_rate_limit(_client_ip(request))
    sid = _parse_uuid_param("session_id", session_id)
    _verify_session(sid, x_session_secret.strip())
    return kb_store.categories_list(_DATA_DIR, sid)


@app.post("/api/v1/sessions/{session_id}/kb/categories")
def kb_create_category(
    request: Request,
    session_id: str,
    body: KbCategoryCreate,
    authorization: Optional[str] = Header(default=None),
    x_session_secret: str = Header(..., alias="X-Session-Secret"),
) -> dict[str, Any]:
    _require_access_token(authorization)
    _check_rate_limit(_client_ip(request))
    sid = _parse_uuid_param("session_id", session_id)
    _verify_session(sid, x_session_secret.strip())
    return kb_store.category_insert(
        _DATA_DIR,
        sid,
        body.name,
        owner_id=body.owner_id,
        sort_order=body.sort_order,
    )


@app.patch("/api/v1/sessions/{session_id}/kb/categories/{category_id}")
def kb_patch_category(
    request: Request,
    session_id: str,
    category_id: str,
    body: KbCategoryPatch,
    authorization: Optional[str] = Header(default=None),
    x_session_secret: str = Header(..., alias="X-Session-Secret"),
) -> dict[str, Any]:
    _require_access_token(authorization)
    _check_rate_limit(_client_ip(request))
    sid = _parse_uuid_param("session_id", session_id)
    _verify_session(sid, x_session_secret.strip())
    cid = category_id.strip()
    row = kb_store.category_update(
        _DATA_DIR,
        sid,
        cid,
        name=body.name,
        sort_order=body.sort_order,
    )
    if not row:
        raise HTTPException(status_code=404, detail="类目不存在")
    return row


@app.delete("/api/v1/sessions/{session_id}/kb/categories/{category_id}")
def kb_delete_category(
    request: Request,
    session_id: str,
    category_id: str,
    authorization: Optional[str] = Header(default=None),
    x_session_secret: str = Header(..., alias="X-Session-Secret"),
) -> dict[str, str]:
    _require_access_token(authorization)
    _check_rate_limit(_client_ip(request))
    sid = _parse_uuid_param("session_id", session_id)
    _verify_session(sid, x_session_secret.strip())
    cid = category_id.strip()
    notes = []
    for cat in kb_store.categories_list(_DATA_DIR, sid):
        if cat["id"] == cid:
            notes = kb_store.notes_list_for_category(_DATA_DIR, sid, cid)
            break
    for n in notes:
        _purge_kb_note_from_disk(sid, str(n["id"]))
    if not kb_store.category_delete(_DATA_DIR, sid, cid):
        raise HTTPException(status_code=404, detail="类目不存在")
    return {"status": "deleted"}


@app.get("/api/v1/sessions/{session_id}/kb/categories/{category_id}/notes")
def kb_list_notes(
    request: Request,
    session_id: str,
    category_id: str,
    authorization: Optional[str] = Header(default=None),
    x_session_secret: str = Header(..., alias="X-Session-Secret"),
) -> list[dict[str, Any]]:
    _require_access_token(authorization)
    _check_rate_limit(_client_ip(request))
    sid = _parse_uuid_param("session_id", session_id)
    _verify_session(sid, x_session_secret.strip())
    cid = category_id.strip()
    if not kb_store.category_get(_DATA_DIR, sid, cid):
        raise HTTPException(status_code=404, detail="类目不存在")
    return kb_store.notes_list_for_category(_DATA_DIR, sid, cid)


@app.post("/api/v1/sessions/{session_id}/kb/categories/{category_id}/notes")
def kb_create_note(
    request: Request,
    session_id: str,
    category_id: str,
    body: KbNoteCreate,
    authorization: Optional[str] = Header(default=None),
    x_session_secret: str = Header(..., alias="X-Session-Secret"),
) -> dict[str, Any]:
    _require_access_token(authorization)
    _check_rate_limit(_client_ip(request))
    sid = _parse_uuid_param("session_id", session_id)
    _verify_session(sid, x_session_secret.strip())
    cid = category_id.strip()
    try:
        row = kb_store.note_insert(
            _DATA_DIR,
            sid,
            cid,
            body.title,
            body.body_markdown,
            owner_id=body.owner_id,
        )
    except ValueError:
        raise HTTPException(status_code=404, detail="类目不存在") from None
    _sync_kb_note_body_file(sid, str(row["id"]), str(row.get("body_markdown") or ""))
    return row


@app.get("/api/v1/sessions/{session_id}/kb/notes/{note_id}")
def kb_get_note(
    request: Request,
    session_id: str,
    note_id: str,
    authorization: Optional[str] = Header(default=None),
    x_session_secret: str = Header(..., alias="X-Session-Secret"),
) -> dict[str, Any]:
    _require_access_token(authorization)
    _check_rate_limit(_client_ip(request))
    sid = _parse_uuid_param("session_id", session_id)
    _verify_session(sid, x_session_secret.strip())
    row = kb_store.note_get(_DATA_DIR, sid, note_id.strip())
    if not row:
        raise HTTPException(status_code=404, detail="笔记不存在")
    return row


@app.patch("/api/v1/sessions/{session_id}/kb/notes/{note_id}")
def kb_patch_note(
    request: Request,
    session_id: str,
    note_id: str,
    body: KbNotePatch,
    authorization: Optional[str] = Header(default=None),
    x_session_secret: str = Header(..., alias="X-Session-Secret"),
) -> dict[str, Any]:
    _require_access_token(authorization)
    _check_rate_limit(_client_ip(request))
    sid = _parse_uuid_param("session_id", session_id)
    _verify_session(sid, x_session_secret.strip())
    nid = note_id.strip()
    try:
        row = kb_store.note_update(
            _DATA_DIR,
            sid,
            nid,
            title=body.title,
            body_markdown=body.body_markdown,
            category_id=body.category_id,
        )
    except ValueError as e:
        if "category_not_found" in str(e):
            raise HTTPException(status_code=404, detail="目标类目不存在") from e
        raise
    if not row:
        raise HTTPException(status_code=404, detail="笔记不存在")
    if body.body_markdown is not None:
        _sync_kb_note_body_file(sid, nid, str(row.get("body_markdown") or ""))
    return row


@app.delete("/api/v1/sessions/{session_id}/kb/notes/{note_id}")
def kb_delete_note(
    request: Request,
    session_id: str,
    note_id: str,
    authorization: Optional[str] = Header(default=None),
    x_session_secret: str = Header(..., alias="X-Session-Secret"),
) -> dict[str, str]:
    _require_access_token(authorization)
    _check_rate_limit(_client_ip(request))
    sid = _parse_uuid_param("session_id", session_id)
    _verify_session(sid, x_session_secret.strip())
    nid = note_id.strip()
    if not kb_store.note_get(_DATA_DIR, sid, nid):
        raise HTTPException(status_code=404, detail="笔记不存在")
    _purge_kb_note_from_disk(sid, nid)
    kb_store.note_delete(_DATA_DIR, sid, nid)
    return {"status": "deleted"}


@app.get("/api/v1/sessions/{session_id}/kb/notes/{note_id}/files", response_model=list[KbNoteFileItem])
def kb_list_note_files(
    request: Request,
    session_id: str,
    note_id: str,
    authorization: Optional[str] = Header(default=None),
    x_session_secret: str = Header(..., alias="X-Session-Secret"),
) -> list[KbNoteFileItem]:
    _require_access_token(authorization)
    _check_rate_limit(_client_ip(request))
    sid = _parse_uuid_param("session_id", session_id)
    _verify_session(sid, x_session_secret.strip())
    nid = note_id.strip()
    if not kb_store.note_get(_DATA_DIR, sid, nid):
        raise HTTPException(status_code=404, detail="笔记不存在")
    rows = kb_store.note_files_list(_DATA_DIR, sid, nid)
    return [KbNoteFileItem.model_validate(dict(r)) for r in rows]


@app.post("/api/v1/sessions/{session_id}/kb/notes/{note_id}/files", response_model=KbNoteFileItem)
async def kb_upload_note_file(
    request: Request,
    session_id: str,
    note_id: str,
    authorization: Optional[str] = Header(default=None),
    x_session_secret: str = Header(..., alias="X-Session-Secret"),
    file: UploadFile = File(...),
) -> KbNoteFileItem:
    _require_access_token(authorization)
    _check_rate_limit(_client_ip(request))
    sid = _parse_uuid_param("session_id", session_id)
    _verify_session(sid, x_session_secret.strip())
    nid = note_id.strip()
    if not kb_store.note_get(_DATA_DIR, sid, nid):
        raise HTTPException(status_code=404, detail="笔记不存在")
    if not file.filename:
        raise HTTPException(status_code=400, detail="无效文件名")
    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(status_code=400, detail=f"不支持的文件类型: {suffix or '未知'}")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="空文件")
    if len(content) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(status_code=400, detail=f"单个文件不能超过 {MAX_FILE_SIZE_MB}MB")

    attach_id = uuid.uuid4().hex
    kb_dir = UPLOAD_DIR / sid / "kb" / "files"
    kb_dir.mkdir(parents=True, exist_ok=True)
    used = {p.name for p in kb_dir.iterdir() if p.is_file()}
    safe_name = _safe_filename(file.filename, used)
    disk_name = f"{attach_id}_{safe_name}"
    stored_rel = f"{sid}/kb/files/{disk_name}"
    dest = UPLOAD_DIR / stored_rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)

    row = kb_store.note_file_insert(
        _DATA_DIR,
        sid,
        nid,
        Path(file.filename).name,
        stored_rel,
        len(content),
        file.content_type,
    )
    return KbNoteFileItem.model_validate(dict(row))


@app.delete("/api/v1/sessions/{session_id}/kb/notes/{note_id}/files/{file_id}")
def kb_delete_note_file(
    request: Request,
    session_id: str,
    note_id: str,
    file_id: str,
    authorization: Optional[str] = Header(default=None),
    x_session_secret: str = Header(..., alias="X-Session-Secret"),
) -> dict[str, str]:
    _require_access_token(authorization)
    _check_rate_limit(_client_ip(request))
    sid = _parse_uuid_param("session_id", session_id)
    _verify_session(sid, x_session_secret.strip())
    nid = note_id.strip()
    fid = file_id.strip()
    row = kb_store.note_file_get(_DATA_DIR, sid, fid)
    if not row or str(row.get("note_id")) != nid:
        raise HTTPException(status_code=404, detail="附件不存在")
    abs_path = (UPLOAD_DIR / row["stored_rel"]).resolve()
    if abs_path.is_file():
        try:
            invalidate_caches_for_file(abs_path, SERVER_EMBED_MODEL)
        except Exception:
            traceback.print_exc()
        try:
            abs_path.unlink()
        except OSError as e:
            raise HTTPException(status_code=500, detail=f"删除文件失败: {e}") from e
    kb_store.note_file_delete(_DATA_DIR, sid, fid)
    return {"status": "deleted"}


@app.post("/api/v1/sessions/{session_id}/qa", response_model=QAResponse)
def ask_session_qa(
    request: Request,
    session_id: str,
    authorization: Optional[str] = Header(default=None),
    x_session_secret: str = Header(..., alias="X-Session-Secret"),
    question: str = Form(..., description="用户问题"),
    top_k: int = Form(3, description="检索片段条数"),
    max_new_tokens: Optional[int] = Form(None, description="生成 token 上限"),
    kb_scope: str = Form("union", description="session_files | kb_only | union"),
    category_ids: Optional[str] = Form(
        None, description='可选：限定知识库类目 id 的 JSON 数组，如 ["uuid1"]'
    ),
) -> QAResponse:
    _require_access_token(authorization)
    _check_rate_limit(_client_ip(request))
    sid = _parse_uuid_param("session_id", session_id)

    if not question.strip():
        raise HTTPException(status_code=400, detail="question 不能为空")
    if len(question) > MAX_QUESTION_CHARS:
        raise HTTPException(status_code=400, detail=f"问题长度不能超过 {MAX_QUESTION_CHARS} 个字符")

    _verify_session(sid, x_session_secret.strip())
    cat_ids = kb_store.parse_category_ids_json(category_ids)
    saved_paths, chunk_tags, bundle_extra = _collect_qa_index_inputs(sid, kb_scope, cat_ids)
    if not saved_paths:
        raise HTTPException(
            status_code=400,
            detail="没有可检索的内容：请上传会话文件和/或在知识库中添加笔记或附件",
        )
    for p in saved_paths:
        if not p.is_file():
            raise HTTPException(status_code=500, detail="服务器上文件缺失，请重新上传或同步知识库")

    limited_top_k = max(1, min(int(top_k), MAX_TOP_K))

    with _session_qa_lock(sid):
        try:
            chunks, _embeddings, index, st = build_or_load_index(
                saved_paths,
                SERVER_EMBED_MODEL,
                bundle_extra=bundle_extra,
                chunk_tags_by_norm_path=chunk_tags,
            )
            hits = rag_pipeline.hybrid_retrieve(
                question,
                chunks,
                index,
                st,
                lambda prompt, max_tok, **kw: _invoke_llm(prompt, max_tok, **kw),
                limited_top_k,
            )
            resp = _run_qa_pipeline(
                question=question,
                hits=hits,
                max_new_tokens=max_new_tokens,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except HTTPException:
            raise
        except Exception as e:
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=_server_error_detail(e)) from None
    now = time.time()
    uid = uuid.uuid4().hex
    aid = uuid.uuid4().hex
    chroma_store.message_add(uid, sid, "user", question.strip(), now)
    extra: dict[str, Any] = {"route": resp.route, "kb_relevant": resp.kb_relevant}
    if resp.no_kb_notice:
        extra["no_kb_notice"] = resp.no_kb_notice
    if resp.citations:
        extra["citations"] = [c.model_dump() for c in resp.citations]
    chroma_store.message_add(aid, sid, "assistant", resp.answer, now + 0.001, extra=extra)
    return resp


@app.post("/api/v1/sessions/{session_id}/qa/async", response_model=QAJobStartResponse)
def ask_session_qa_async(
    request: Request,
    session_id: str,
    authorization: Optional[str] = Header(default=None),
    x_session_secret: str = Header(..., alias="X-Session-Secret"),
    question: str = Form(..., description="用户问题"),
    top_k: int = Form(3, description="检索片段条数"),
    max_new_tokens: Optional[int] = Form(None, description="生成 token 上限"),
    kb_scope: str = Form("union", description="session_files | kb_only | union"),
    category_ids: Optional[str] = Form(None, description="可选：类目 id JSON 数组"),
) -> QAJobStartResponse:
    _require_access_token(authorization)
    _check_rate_limit(_client_ip(request))
    sid = _parse_uuid_param("session_id", session_id)

    if not question.strip():
        raise HTTPException(status_code=400, detail="question 不能为空")
    if len(question) > MAX_QUESTION_CHARS:
        raise HTTPException(status_code=400, detail=f"问题长度不能超过 {MAX_QUESTION_CHARS} 个字符")

    _verify_session(sid, x_session_secret.strip())
    cat_ids = kb_store.parse_category_ids_json(category_ids)
    paths, _, _ = _collect_qa_index_inputs(sid, kb_scope, cat_ids)
    if not paths:
        raise HTTPException(
            status_code=400,
            detail="没有可检索的内容：请上传会话文件和/或在知识库中添加笔记或附件",
        )

    job_id = uuid.uuid4().hex
    with _jobs_lock:
        _qa_jobs[job_id] = {"status": "pending"}
    secret = x_session_secret.strip()
    t = threading.Thread(
        target=_session_qa_worker,
        args=(job_id, sid, secret, question, top_k, max_new_tokens, kb_scope, category_ids),
        daemon=True,
    )
    t.start()
    return QAJobStartResponse(job_id=job_id)


@app.get("/api/v1/sessions/{session_id}/qa/jobs/{job_id}", response_model=QAJobStatusResponse)
def get_session_qa_job(
    request: Request,
    session_id: str,
    job_id: str,
    authorization: Optional[str] = Header(default=None),
    x_session_secret: str = Header(..., alias="X-Session-Secret"),
) -> QAJobStatusResponse:
    _require_access_token(authorization)
    _check_rate_limit(_client_ip(request))
    sid = _parse_uuid_param("session_id", session_id)
    _verify_session(sid, x_session_secret.strip())
    jid = job_id.strip()
    with _jobs_lock:
        row = _qa_jobs.get(jid)
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在")
    return QAJobStatusResponse(
        status=row["status"],
        detail=row.get("detail"),
        assistant_message_id=row.get("assistant_message_id"),
        user_message_id=row.get("user_message_id"),
        result=row.get("result"),
    )


@app.get("/api/v1/sessions/{session_id}/messages", response_model=list[ChatMessageItem])
def list_session_messages(
    request: Request,
    session_id: str,
    authorization: Optional[str] = Header(default=None),
    x_session_secret: str = Header(..., alias="X-Session-Secret"),
) -> list[ChatMessageItem]:
    _require_access_token(authorization)
    _check_rate_limit(_client_ip(request))
    sid = _parse_uuid_param("session_id", session_id)
    _verify_session(sid, x_session_secret.strip())
    raw = chroma_store.messages_list(sid)
    return [
        ChatMessageItem(
            id=m["id"],
            role=m["role"],
            content=m["content"],
            created_at=float(m["created_at"]),
            extra=m.get("extra"),
        )
        for m in raw
    ]


@app.delete("/api/v1/sessions/{session_id}/messages/{message_id}")
def delete_session_message(
    request: Request,
    session_id: str,
    message_id: str,
    authorization: Optional[str] = Header(default=None),
    x_session_secret: str = Header(..., alias="X-Session-Secret"),
) -> dict[str, str]:
    """删除单条聊天记录。"""
    _require_access_token(authorization)
    _check_rate_limit(_client_ip(request))
    sid = _parse_uuid_param("session_id", session_id)
    _verify_session(sid, x_session_secret.strip())
    mid = message_id.strip()
    if not mid:
        raise HTTPException(status_code=400, detail="无效的消息 id")
    if not chroma_store.message_delete(sid, mid):
        raise HTTPException(status_code=404, detail="消息不存在")
    return {"status": "deleted"}


@app.delete("/api/v1/sessions/{session_id}/messages")
def delete_all_session_messages(
    request: Request,
    session_id: str,
    authorization: Optional[str] = Header(default=None),
    x_session_secret: str = Header(..., alias="X-Session-Secret"),
) -> dict[str, str | int]:
    """清空本会话全部聊天记录。"""
    _require_access_token(authorization)
    _check_rate_limit(_client_ip(request))
    sid = _parse_uuid_param("session_id", session_id)
    _verify_session(sid, x_session_secret.strip())
    n = chroma_store.messages_delete_all(sid)
    return {"status": "ok", "deleted": n}


@app.post("/api/v1/sessions/{session_id}/quiz/generate", response_model=QuizBundlePublic)
def generate_session_quiz(
    request: Request,
    session_id: str,
    body: QuizGenerateRequest,
    authorization: Optional[str] = Header(default=None),
    x_session_secret: str = Header(..., alias="X-Session-Secret"),
) -> QuizBundlePublic:
    _require_access_token(authorization)
    _check_rate_limit(_client_ip(request))
    sid = _parse_uuid_param("session_id", session_id)
    _verify_session(sid, x_session_secret.strip())
    merged = _merge_quiz_segments(body.segments or [])
    if not merged:
        raise HTTPException(status_code=400, detail="至少选择一条消息")
    total_n = sum(s.count for s in merged)
    if total_n > MAX_QUIZ_QUESTIONS_TOTAL:
        raise HTTPException(
            status_code=400,
            detail=f"题目总数不能超过 {MAX_QUIZ_QUESTIONS_TOTAL}（当前为 {total_n}）",
        )

    resolved_segments: list[tuple[str, str, int]] = []
    msgs_for_context: list[dict[str, Any]] = []
    for s in merged:
        mid = s.message_id.strip()
        m = chroma_store.message_get(sid, mid)
        if not m:
            raise HTTPException(status_code=400, detail=f"无效的消息 id: {mid}")
        if m.get("role") != "assistant":
            raise HTTPException(status_code=400, detail=f"消息 {mid} 不是助手消息，请只勾选助手回复")
        excerpt = str(m.get("content") or "")
        resolved_segments.append((mid, excerpt, s.count))
        msgs_for_context.append(m)
    msgs_for_context.sort(key=lambda x: float(x["created_at"]))
    context = "\n\n".join(f"{m['role']}: {m['content']}" for m in msgs_for_context)
    last_user = next((m["content"] for m in reversed(msgs_for_context) if m["role"] == "user"), None)
    search_q = (last_user or context)[:800]

    rows = chroma_store.file_list(sid)
    if not rows:
        raise HTTPException(status_code=400, detail="会话中还没有文件，请先上传")
    saved_paths = [(UPLOAD_DIR / r["stored_rel"]).resolve() for r in rows]
    for p in saved_paths:
        if not p.is_file():
            raise HTTPException(status_code=500, detail="服务器上文件缺失，请重新上传")

    limited_top_k = max(1, min(MAX_TOP_K, 5))
    with _session_qa_lock(sid):
        try:
            chunks, _embeddings, index, st = build_or_load_index(saved_paths, SERVER_EMBED_MODEL)
            hits = search(search_q, chunks, index, st, top_k=limited_top_k)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except Exception as e:
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=_server_error_detail(e)) from None

    prev_texts = chroma_store.quiz_list_question_texts(sid)
    forbidden_lower = {t.casefold() for t in prev_texts if t}
    raw_bundle, quiz_fail = _generate_quiz_bundle_for_segments(
        hits, resolved_segments, forbidden_lower, total_n
    )
    if not raw_bundle:
        raise HTTPException(status_code=503, detail=_quiz_generation_fail_detail(quiz_fail))
    quiz_id = uuid.uuid4().hex
    seg_meta = [{"message_id": s.message_id, "count": s.count} for s in merged]
    payload: dict[str, Any] = {
        "items": raw_bundle["items"],
        "meta": {
            "segments": seg_meta,
            "message_ids": [s.message_id for s in merged],
            "total_n": total_n,
            "context_preview": context[:800],
        },
    }
    chroma_store.quiz_insert(quiz_id, sid, payload, time.time())
    return _build_quiz_public(quiz_id, raw_bundle["items"])


@app.get("/api/v1/sessions/{session_id}/quiz/{quiz_id}", response_model=QuizBundlePublic)
def get_session_quiz_bundle(
    request: Request,
    session_id: str,
    quiz_id: str,
    authorization: Optional[str] = Header(default=None),
    x_session_secret: str = Header(..., alias="X-Session-Secret"),
) -> QuizBundlePublic:
    _require_access_token(authorization)
    _check_rate_limit(_client_ip(request))
    sid = _parse_uuid_param("session_id", session_id)
    _verify_session(sid, x_session_secret.strip())
    qid = quiz_id.strip()
    got = chroma_store.quiz_get(qid)
    if not got:
        raise HTTPException(status_code=404, detail="测验不存在")
    db_sid, payload = got
    if db_sid != sid:
        raise HTTPException(status_code=403, detail="无权访问该测验")
    items = payload.get("items") or []
    return _build_quiz_public(qid, items)


@app.post("/api/v1/qa", response_model=QAResponse)
async def ask_doc_qa(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    question: str = Form(..., description="用户问题"),
    files: list[UploadFile] = File(..., description="一个或多个文档文件"),
    top_k: int = Form(3, description="检索片段条数"),
    max_new_tokens: Optional[int] = Form(None, description="生成 token 上限"),
) -> QAResponse:
    """一次性上传问答（文件不持久化）；会话模式请用 /api/v1/sessions/...。"""
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
        hits = rag_pipeline.hybrid_retrieve(
            question,
            chunks,
            index,
            st,
            lambda prompt, max_tok, **kw: _invoke_llm(prompt, max_tok, **kw),
            limited_top_k,
        )
        return _run_qa_pipeline(
            question=question,
            hits=hits,
            max_new_tokens=max_new_tokens,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=_server_error_detail(e)) from e
    finally:
        _cleanup_dir(request_dir)


@app.post("/api/v1/sessions/{session_id}/quiz/{quiz_id}/grade", response_model=QuizGradeResponse)
def grade_session_quiz(
    request: Request,
    session_id: str,
    quiz_id: str,
    body: QuizGradeRequest,
    authorization: Optional[str] = Header(default=None),
    x_session_secret: str = Header(..., alias="X-Session-Secret"),
) -> QuizGradeResponse:
    _require_access_token(authorization)
    _check_rate_limit(_client_ip(request))
    sid = _parse_uuid_param("session_id", session_id)
    qid = quiz_id.strip()

    _verify_session(sid, x_session_secret.strip())
    got = chroma_store.quiz_get(qid)
    if not got:
        raise HTTPException(status_code=404, detail="测验不存在或已过期")
    db_sid, payload = got
    if db_sid is None:
        raise HTTPException(status_code=400, detail="该测验请使用 POST /api/v1/quiz/{quiz_id}/grade")
    if db_sid != sid:
        raise HTTPException(status_code=403, detail="无权访问该测验")
    expected = len(payload.get("items") or [])
    if expected <= 0:
        raise HTTPException(status_code=400, detail="测验数据无效")
    if len(body.answers) != expected:
        raise HTTPException(status_code=400, detail=f"请提交恰好 {expected} 条答案")

    return _grade_quiz_with_llm(payload, body.answers)


@app.post("/api/v1/quiz/{quiz_id}/grade", response_model=QuizGradeResponse)
def grade_standalone_quiz(
    request: Request,
    quiz_id: str,
    body: QuizGradeRequest,
    authorization: Optional[str] = Header(default=None),
) -> QuizGradeResponse:
    """用于一次性 /api/v1/qa（无会话）产生的测验判分。"""
    _require_access_token(authorization)
    _check_rate_limit(_client_ip(request))
    qid = quiz_id.strip()

    got = chroma_store.quiz_get(qid)
    if not got:
        raise HTTPException(status_code=404, detail="测验不存在或已过期")
    db_sid, payload = got
    if db_sid is not None:
        raise HTTPException(status_code=400, detail="请使用会话判分接口")
    expected = len(payload.get("items") or [])
    if expected <= 0:
        raise HTTPException(status_code=400, detail="测验数据无效")
    if len(body.answers) != expected:
        raise HTTPException(status_code=400, detail=f"请提交恰好 {expected} 条答案")

    return _grade_quiz_with_llm(payload, body.answers)


# 同一端口提供前端静态页（局域网内用浏览器直接访问根路径即可测试）
def _resolve_frontend_static_dir() -> Optional[Path]:
    """优先 RAG_FRONTEND_DIR；否则与仓库同级的 ForRag-frontend；再否则仓库内 ForRag-gh-pages。"""
    raw = os.environ.get("RAG_FRONTEND_DIR", "").strip()
    candidates: list[Path] = []
    if raw:
        candidates.append(Path(raw).expanduser().resolve())
    candidates.append(_REPO_ROOT.parent / "ForRag-frontend")
    candidates.append(_REPO_ROOT / "ForRag-gh-pages")
    for p in candidates:
        if p.is_dir():
            return p
    return None


_FRONTEND_DIR = _resolve_frontend_static_dir()
if _FRONTEND_DIR:
    app.mount(
        "/",
        StaticFiles(directory=str(_FRONTEND_DIR), html=True),
        name="frontend",
    )
