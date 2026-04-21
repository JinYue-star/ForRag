#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将 doc_qa_assistant.py 封装成更安全的 FastAPI 服务。

实现已拆至 ``rag_api`` 包（``settings`` / ``routes`` / ``qa_llm`` 等）；本模块仅保留
``uvicorn fastapi_service:app`` 的兼容入口与测试用 ``_DATA_DIR`` 别名。

支持会话持久化上传（含图片 OCR 入库）、ChromaDB 元数据（会话/文件/聊天/测验）、
服务端向量缓存（RAG_CACHE_ROOT）与按文件删除时同步清理缓存。

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

from rag_api import settings
from rag_api.main import app

# 测试与运维脚本兼容：与历史 fastapi_service._DATA_DIR 一致
_DATA_DIR = settings.DATA_DIR

__all__ = ["app", "_DATA_DIR"]
