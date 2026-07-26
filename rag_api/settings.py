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
import auth_store

from doc_qa_assistant import _normalize_llm_hub

# 仓库根目录（rag_api 的上一级）
REPO_ROOT = Path(__file__).resolve().parent.parent

# 直接以 uvicorn 运行时，自动加载仓库根目录下的 .env（若安装了 python-dotenv）。
# Docker 通过 compose 的 env_file 注入，此处为冗余但无害；不覆盖已存在的环境变量。
try:
    from dotenv import load_dotenv

    _env_file = REPO_ROOT / ".env"
    if _env_file.is_file():
        load_dotenv(_env_file, override=False)
except Exception:
    traceback.print_exc()

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
auth_store.init_auth_db(DATA_DIR)
try:
    import exercise_store

    exercise_store.init_exercises(DATA_DIR)
except Exception:
    pass


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

UPLOAD_DIR = Path(os.environ.get("RAG_UPLOAD_DIR", str(REPO_ROOT / ".uploads"))).resolve()
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
# 静态「服务令牌」：仅在未启用用户登录鉴权时由 require_access_token() 校验。
# 与前端登录令牌（HKU_LOGIN_TOKEN / Bearer login token）无关；启用 RAG_REQUIRE_AUTH 后忽略本项。
ACCESS_TOKEN = os.environ.get("RAG_ACCESS_TOKEN", "").strip()
_raw_require_token = os.environ.get("RAG_REQUIRE_ACCESS_TOKEN", "").strip().lower()
if _raw_require_token in {"0", "false", "no", "off"}:
    REQUIRE_ACCESS_TOKEN = False
elif _raw_require_token in {"1", "true", "yes"}:
    REQUIRE_ACCESS_TOKEN = True
else:
    REQUIRE_ACCESS_TOKEN = bool(ACCESS_TOKEN)

# ---- 师生账号鉴权（用户名 + 密码 + 登录令牌） ----
# RAG_REQUIRE_AUTH：显式开关；未显式设置时，只要存在任意用户账号即视为启用。
_raw_require_auth = os.environ.get("RAG_REQUIRE_AUTH", "").strip().lower()
if _raw_require_auth in {"0", "false", "no", "off"}:
    REQUIRE_AUTH: bool | None = False
elif _raw_require_auth in {"1", "true", "yes", "on"}:
    REQUIRE_AUTH = True
else:
    REQUIRE_AUTH = None  # auto：由 auth.py 依据是否存在用户判断
AUTH_TOKEN_TTL_SECONDS = max(300, int(os.environ.get("RAG_AUTH_TOKEN_TTL", str(7 * 24 * 3600))))
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "").strip()
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
ADMIN_DISPLAY_NAME = os.environ.get("ADMIN_DISPLAY_NAME", "").strip()
STUDENT_REGISTER_CODE = os.environ.get("STUDENT_REGISTER_CODE", "").strip()


def bootstrap_auth() -> None:
    """启动时：按环境变量创建管理员/教师账号，并确保存在学生注册码。"""
    if ADMIN_USERNAME and ADMIN_PASSWORD:
        existing = auth_store.user_get_by_username(DATA_DIR, ADMIN_USERNAME)
        if not existing:
            try:
                auth_store.user_create(
                    DATA_DIR,
                    ADMIN_USERNAME,
                    ADMIN_PASSWORD,
                    role=auth_store.ROLE_TEACHER,
                    display_name=ADMIN_DISPLAY_NAME or ADMIN_USERNAME,
                )
            except ValueError:
                traceback.print_exc()
    # 学生自助注册码：环境变量优先，否则自动生成一个并持久化
    auth_store.ensure_registration_code(DATA_DIR, STUDENT_REGISTER_CODE)


try:
    bootstrap_auth()
except Exception:
    traceback.print_exc()

# 默认多语种嵌入模型 BAAI/bge-m3：多语种标杆、8192 长上下文，适配英文/双语课程。
# 换模型会自动以新 embed_model_id 重建向量缓存（无需手动清理）。
# 备选：intfloat/multilingual-e5-large（同级、带 e5 前缀）、多语种小模型 BAAI/bge-small-zh-v1.5（离线/低配）。
# 注意：bge-m3 约 0.5B 参数、需约 2.3GB 内存；低内存/CPU 机器建议改用小模型。
SERVER_EMBED_MODEL = os.environ.get("MS_EMBED_ID", "BAAI/bge-m3")
SERVER_MODEL_ID = os.environ.get("MS_MODEL_ID", "Qwen/Qwen2.5-3B-Instruct")
SERVER_LLM_HUB = _normalize_llm_hub(os.environ.get("LLM_HUB", "auto"))
SERVER_LOW_MEMORY = os.environ.get("RAG_LOW_MEMORY", "").strip().lower() in {"1", "true", "yes"}
ENABLE_LOCAL_LLM = os.environ.get("RAG_ENABLE_LOCAL_LLM", "").strip().lower() in {"1", "true", "yes"}
# 不再内置任何默认密钥：仅从环境变量读取（DASHSCOPE_API_KEY，兼容 QWEN_API_KEY）。
SERVER_API_KEY = (
    os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("QWEN_API_KEY") or ""
).strip()
SERVER_API_MODEL = os.environ.get("QWEN_API_MODEL", "qwen-plus")
SERVER_API_BASE = os.environ.get("QWEN_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1")
MAX_FILES = max(1, int(os.environ.get("RAG_MAX_FILES", "5")))
MAX_FILE_SIZE_MB = max(1, int(os.environ.get("RAG_MAX_FILE_MB", "20")))
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024
MAX_TOP_K = max(1, int(os.environ.get("RAG_MAX_TOP_K", "5")))
MAX_QUESTION_CHARS = max(50, int(os.environ.get("RAG_MAX_QUESTION_CHARS", "1000")))
RATE_LIMIT_WINDOW_SECONDS = max(10, int(os.environ.get("RAG_RATE_LIMIT_WINDOW_SECONDS", "60")))
RATE_LIMIT_MAX_REQUESTS = max(0, int(os.environ.get("RAG_RATE_LIMIT_MAX_REQUESTS", "120")))
# 单条证据进提示词的字符上限：需容纳一个完整块（480 token，中文约 480 字、英文约 1900 字）
# 或其展开后的父级页（RAG_PARENT_MAX_CHARS，默认 2400），否则加大块长会被这里截掉。
PROMPT_CHUNK_CHAR_LIMIT = max(160, int(os.environ.get("RAG_PROMPT_CHUNK_CHAR_LIMIT", "2400")))
KB_MIN_SCORE = float(os.environ.get("RAG_KB_MIN_SCORE", "0.28"))
# 仅检索到 1 条时，需达到更高分才走「依据文档」模式，避免单条低相关被硬套成 RAG 答案
KB_SINGLE_HIT_MIN_SCORE = float(os.environ.get("RAG_KB_SINGLE_HIT_MIN_SCORE", "0.40"))
# 余弦强阈值按 tools/rag_eval.py --grounding-only 在真实 ELEC6081 语料上的标定结果取值
# （bge-small-zh-v1.5，16 份课程材料、65 题：57 题应有依据、8 题应走通识）。
# 判别力主要来自 top2 而非 top1：切题问题通常有多张幻灯片覆盖，跑题问题往往只有一条偶然相似的块。
# 因此改为"要求两条证据都达到 0.62"（second <= top，故两个阈值取同值即表达该规则），
# 实测精确率 0.982、召回 0.982；旧值 0.50/0.60/0.35 是 0.905/1.000，单看 top1 的 0.69 是 1.000/0.754。
KB_STRONG_SCORE = float(os.environ.get("RAG_KB_STRONG_SCORE", "0.62"))
# top1 极高时单条即可判为有依据；需高于跑题问题的最高 top1（实测 0.72）留出余量。
KB_SINGLE_HIT_STRONG_SCORE = float(os.environ.get("RAG_KB_SINGLE_HIT_STRONG_SCORE", "0.75"))
KB_SECOND_HIT_SCORE = float(os.environ.get("RAG_KB_SECOND_HIT_SCORE", "0.62"))
# 开启重排后，命中分是交叉编码器 sigmoid 概率（0~1），语义与余弦不同，故用独立阈值。
# 标定：e5-small + ms-marco-MiniLM，ELEC6081 65 题（eval_6081_e5_rerank.json）。
# 0.80/0.80/0.60 → grounded 精确率 1.000、召回 0.877（0 FP）；默认 0.65/0.75/0.40 会放过 1 道通识假阳性（top≈0.79）。
# 落在 [MIN, STRONG) 的边界命中仍交 LLM 充分性判断补召回。
RERANK_MIN_SCORE = float(os.environ.get("RAG_RERANK_MIN_SCORE", "0.05"))
RERANK_SINGLE_HIT_MIN_SCORE = float(os.environ.get("RAG_RERANK_SINGLE_HIT_MIN_SCORE", "0.12"))
RERANK_STRONG_SCORE = float(os.environ.get("RAG_RERANK_STRONG_SCORE", "0.80"))
RERANK_SINGLE_HIT_STRONG_SCORE = float(os.environ.get("RAG_RERANK_SINGLE_HIT_STRONG_SCORE", "0.80"))
RERANK_SECOND_HIT_SCORE = float(os.environ.get("RAG_RERANK_SECOND_HIT_SCORE", "0.60"))
RERANK_SUFFICIENCY_MARGIN = float(os.environ.get("RAG_RERANK_SUFFICIENCY_MARGIN", "0.10"))
KB_SUFFICIENCY_MARGIN = float(os.environ.get("RAG_KB_SUFFICIENCY_MARGIN", "0.08"))
ENABLE_SUFFICIENCY_JUDGE = os.environ.get(
    "RAG_ENABLE_SUFFICIENCY_JUDGE", "1"
).strip().lower() in {"1", "true", "yes", "on"}
MIN_CITATION_COVERAGE = max(
    0.0, min(1.0, float(os.environ.get("RAG_MIN_CITATION_COVERAGE", "0.95")))
)
# 证据精炼（knowledge refinement）：相对最高分的比例阈值，低于此的候选块不进 prompt（至少保留 1 条）。
EVIDENCE_KEEP_RATIO = float(os.environ.get("RAG_EVIDENCE_KEEP_RATIO", "0.25"))
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
