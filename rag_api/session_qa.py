#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""会话级问答索引输入收集、异步任务与 per-session 检索锁。"""

from __future__ import annotations

import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import HTTPException

import chroma_store
import kb_store
import rag_pipeline

from doc_qa_assistant import _normalized_path, build_or_load_index

from rag_api import settings
from rag_api.http_common import server_error_detail, verify_session
from rag_api.kb_files import kb_note_md_path
from rag_api.qa_llm import invoke_llm, run_qa_pipeline

_session_qa_locks: dict[str, threading.Lock] = {}
_session_qa_guard = threading.Lock()

_qa_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()


def session_qa_lock(session_id: str) -> threading.Lock:
    with _session_qa_guard:
        if session_id not in _session_qa_locks:
            _session_qa_locks[session_id] = threading.Lock()
        return _session_qa_locks[session_id]


def collect_qa_index_inputs(
    session_id: str,
    kb_scope: str,
    category_ids: Optional[list[str]],
) -> tuple[list[Path], dict[str, dict[str, str]], str]:
    scope = (kb_scope or "union").strip().lower()
    if scope not in ("session_files", "kb_only", "union"):
        scope = "union"

    paths: list[Path] = []
    chunk_tags: dict[str, dict[str, str]] = {}
    kb_token = kb_store.session_kb_bundle_token(settings.DATA_DIR, settings.KB_ID)

    if scope in ("session_files", "union"):
        for r in chroma_store.file_list(session_id):
            p = (settings.UPLOAD_DIR / r["stored_rel"]).resolve()
            if p.is_file():
                paths.append(p)
                chunk_tags[_normalized_path(p)] = {"session_file_id": str(r["id"])}

    if scope in ("kb_only", "union"):
        for nid in kb_store.kb_note_ids_for_rag(settings.DATA_DIR, settings.KB_ID, category_ids):
            body_path = kb_note_md_path(settings.KB_ID, nid)
            if body_path.is_file():
                paths.append(body_path)
                chunk_tags[_normalized_path(body_path)] = {"kb_note_id": nid}
        for row in kb_store.kb_attachment_rows_for_rag(settings.DATA_DIR, settings.KB_ID, category_ids):
            p = (settings.UPLOAD_DIR / row["stored_rel"]).resolve()
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


def session_qa_worker(
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
        # 派发路由已用 verify_session_access 校验 owner；worker 仅复查 secret。
        verify_session(sid, secret)
        cat_ids = kb_store.parse_category_ids_json(category_ids_json)
        saved_paths, chunk_tags, bundle_extra = collect_qa_index_inputs(sid, kb_scope, cat_ids)
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
        limited_top_k = max(1, min(int(top_k), settings.MAX_TOP_K))
        with session_qa_lock(sid):
            chunks, _embeddings, index, st = build_or_load_index(
                saved_paths,
                settings.SERVER_EMBED_MODEL,
                bundle_extra=bundle_extra,
                chunk_tags_by_norm_path=chunk_tags,
            )
            hits = rag_pipeline.hybrid_retrieve(
                question,
                chunks,
                index,
                st,
                lambda prompt, max_tok, **kw: invoke_llm(prompt, max_tok, **kw),
                limited_top_k,
            )
            resp = run_qa_pipeline(question=question, hits=hits, max_new_tokens=max_new_tokens)
        now = time.time()
        uid = uuid.uuid4().hex
        aid = uuid.uuid4().hex
        chroma_store.message_add(uid, sid, "user", question.strip(), now)
        extra: dict[str, Any] = {
            "route": resp.route,
            "kb_relevant": resp.kb_relevant,
            "grounding_label": resp.grounding_label,
            "answer_kind": resp.answer_kind,
            "service_unavailable": resp.service_unavailable,
            "sufficiency_checked": resp.sufficiency_checked,
            "sufficiency_sufficient": resp.sufficiency_sufficient,
            "sufficiency_reason": resp.sufficiency_reason,
            "citation_coverage": resp.citation_coverage,
        }
        if resp.no_kb_notice:
            extra["no_kb_notice"] = resp.no_kb_notice
        if resp.citations:
            extra["citations"] = [c.model_dump() for c in resp.citations]
        if (resp.service_unavailable or resp.answer_kind == "verification_failed") and resp.hits:
            extra["sources"] = [
                {
                    "source": h.source,
                    "page_label": h.page_label,
                    "score": h.score,
                }
                for h in resp.hits[:3]
            ]
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
            _qa_jobs[job_id] = {"status": "error", "detail": server_error_detail(e)}


def qa_job_put_pending(job_id: str) -> None:
    with _jobs_lock:
        _qa_jobs[job_id] = {"status": "pending"}


def qa_job_get(job_id: str) -> dict[str, Any] | None:
    with _jobs_lock:
        return _qa_jobs.get(job_id)
