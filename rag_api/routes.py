#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FastAPI 路由（HTTP 层）：鉴权与参数解析后委托 chroma/kb_store 与 qa 模块。"""

from __future__ import annotations

import secrets
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, File, Form, Header, HTTPException, Request, UploadFile

import chroma_store
import kb_store
import rag_pipeline

from doc_qa_assistant import (
    build_or_load_index,
    invalidate_caches_for_file,
    search,
)

from rag_api import settings
from rag_api.http_common import (
    check_rate_limit,
    cleanup_dir,
    client_ip,
    hash_session_secret,
    parse_uuid_param,
    require_access_token,
    safe_filename,
    server_error_detail,
    verify_session,
)
from rag_api.kb_files import purge_kb_note_from_disk, sync_kb_note_body_file
from rag_api.qa_llm import (
    build_quiz_public,
    generate_quiz_bundle_for_segments,
    grade_quiz_with_llm,
    invoke_llm,
    merge_quiz_segments,
    quiz_generation_fail_detail,
    run_qa_pipeline,
)
from rag_api.schemas import (
    ChatMessageItem,
    KbCategoryCreate,
    KbCategoryPatch,
    KbNoteCreate,
    KbNoteFileItem,
    KbNotePatch,
    QAJobStartResponse,
    QAJobStatusResponse,
    QAResponse,
    QuizBundlePublic,
    QuizGenerateRequest,
    QuizGradeRequest,
    QuizGradeResponse,
    SessionCreateResponse,
    SessionFileItem,
)
from rag_api.session_qa import (
    collect_qa_index_inputs,
    qa_job_get,
    qa_job_put_pending,
    session_qa_lock,
    session_qa_worker,
)

router_health = APIRouter(tags=["health"])
router_v1 = APIRouter(prefix="/api/v1", tags=["v1"])


@router_health.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router_v1.post("/sessions", response_model=SessionCreateResponse)
def create_session(
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> SessionCreateResponse:
    require_access_token(authorization)
    check_rate_limit(client_ip(request))

    sid = str(uuid.uuid4())
    secret = secrets.token_hex(32)
    now = time.time()
    chroma_store.session_insert(sid, hash_session_secret(secret), now, now)
    return SessionCreateResponse(session_id=sid, session_secret=secret)


@router_v1.delete("/sessions/{session_id}")
def delete_session(
    request: Request,
    session_id: str,
    authorization: Optional[str] = Header(default=None),
    x_session_secret: str = Header(..., alias="X-Session-Secret"),
) -> dict[str, str]:
    """删除整个会话：上传文件、聊天记录、测验批次与会话目录。

    注意：全局知识库（KB）不再与会话绑定，删除会话不应清理 KB 数据。
    """
    require_access_token(authorization)
    check_rate_limit(client_ip(request))
    sid = parse_uuid_param("session_id", session_id)
    verify_session(sid, x_session_secret.strip())

    for row in chroma_store.file_list(sid):
        abs_path = (settings.UPLOAD_DIR / row["stored_rel"]).resolve()
        if abs_path.is_file():
            try:
                invalidate_caches_for_file(abs_path, settings.SERVER_EMBED_MODEL)
            except Exception:
                traceback.print_exc()
            try:
                abs_path.unlink()
            except OSError as e:
                raise HTTPException(status_code=500, detail=f"删除文件失败: {e}") from e
        chroma_store.file_delete(row["id"])

    chroma_store.messages_delete_all(sid)
    chroma_store.quiz_delete_all_for_session(sid)
    chroma_store.session_delete_record(sid)
    cleanup_dir(settings.UPLOAD_DIR / sid)
    return {"status": "deleted"}


@router_v1.get("/sessions/{session_id}/files", response_model=list[SessionFileItem])
def list_session_files(
    request: Request,
    session_id: str,
    authorization: Optional[str] = Header(default=None),
    x_session_secret: str = Header(..., alias="X-Session-Secret"),
) -> list[SessionFileItem]:
    require_access_token(authorization)
    check_rate_limit(client_ip(request))
    sid = parse_uuid_param("session_id", session_id)

    verify_session(sid, x_session_secret.strip())
    rows = chroma_store.file_list(sid)
    return [
        SessionFileItem(id=r["id"], original_name=r["original_name"], size_bytes=int(r["size_bytes"]))
        for r in rows
    ]


@router_v1.post("/sessions/{session_id}/files", response_model=list[SessionFileItem])
async def upload_session_files(
    request: Request,
    session_id: str,
    authorization: Optional[str] = Header(default=None),
    x_session_secret: str = Header(..., alias="X-Session-Secret"),
    files: list[UploadFile] = File(...),
) -> list[SessionFileItem]:
    require_access_token(authorization)
    check_rate_limit(client_ip(request))
    sid = parse_uuid_param("session_id", session_id)
    if not files:
        raise HTTPException(status_code=400, detail="至少上传一个文件")

    prepared: list[tuple[str, bytes]] = []
    for f in files:
        if not f.filename:
            continue
        suffix = Path(f.filename).suffix.lower()
        if suffix not in settings.ALLOWED_SUFFIXES:
            raise HTTPException(status_code=400, detail=f"不支持的文件类型: {suffix or '未知'}")
        content = await f.read()
        if not content:
            continue
        if len(content) > settings.MAX_FILE_SIZE_BYTES:
            raise HTTPException(status_code=400, detail=f"单个文件不能超过 {settings.MAX_FILE_SIZE_MB}MB")
        prepared.append((f.filename, content))

    if not prepared:
        raise HTTPException(status_code=400, detail="未接收到有效文件")

    secret = x_session_secret.strip()
    verify_session(sid, secret)
    count = len(chroma_store.file_list(sid))
    if count + len(prepared) > settings.MAX_FILES:
        raise HTTPException(status_code=400, detail=f"最多只能上传 {settings.MAX_FILES} 个文件")

    session_dir = settings.UPLOAD_DIR / sid
    session_dir.mkdir(parents=True, exist_ok=True)

    existing_rels = chroma_store.file_list(sid)
    used_names = {Path(r["stored_rel"]).name for r in existing_rels}

    out: list[SessionFileItem] = []
    now = time.time()

    for orig_filename, content in prepared:
        safe_name = safe_filename(orig_filename, used_names)
        file_id = uuid.uuid4().hex
        disk_name = f"{file_id}_{safe_name}"
        stored_rel = f"{sid}/{disk_name}"
        dest = settings.UPLOAD_DIR / stored_rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)

        verify_session(sid, secret)
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


@router_v1.delete("/sessions/{session_id}/files/{file_id}")
def delete_session_file(
    request: Request,
    session_id: str,
    file_id: str,
    authorization: Optional[str] = Header(default=None),
    x_session_secret: str = Header(..., alias="X-Session-Secret"),
) -> dict[str, str]:
    require_access_token(authorization)
    check_rate_limit(client_ip(request))
    sid = parse_uuid_param("session_id", session_id)
    try:
        fid = uuid.UUID(file_id).hex
    except ValueError as e:
        raise HTTPException(status_code=400, detail="无效的文件 ID") from e

    verify_session(sid, x_session_secret.strip())
    row = chroma_store.file_get(sid, fid)
    if not row:
        raise HTTPException(status_code=404, detail="文件不存在")

    abs_path = (settings.UPLOAD_DIR / row["stored_rel"]).resolve()
    if abs_path.is_file():
        try:
            invalidate_caches_for_file(abs_path, settings.SERVER_EMBED_MODEL)
        except Exception:
            traceback.print_exc()
        try:
            abs_path.unlink()
        except OSError as e:
            raise HTTPException(status_code=500, detail=f"删除文件失败: {e}") from e

    chroma_store.file_delete(fid)

    return {"status": "deleted"}


@router_v1.get("/sessions/{session_id}/kb/categories")
def kb_list_categories(
    request: Request,
    session_id: str,
    authorization: Optional[str] = Header(default=None),
    x_session_secret: str = Header(..., alias="X-Session-Secret"),
) -> list[dict[str, Any]]:
    require_access_token(authorization)
    check_rate_limit(client_ip(request))
    sid = parse_uuid_param("session_id", session_id)
    verify_session(sid, x_session_secret.strip())
    return kb_store.categories_list(settings.DATA_DIR, settings.KB_ID)


@router_v1.post("/sessions/{session_id}/kb/categories")
def kb_create_category(
    request: Request,
    session_id: str,
    body: KbCategoryCreate,
    authorization: Optional[str] = Header(default=None),
    x_session_secret: str = Header(..., alias="X-Session-Secret"),
) -> dict[str, Any]:
    require_access_token(authorization)
    check_rate_limit(client_ip(request))
    sid = parse_uuid_param("session_id", session_id)
    verify_session(sid, x_session_secret.strip())
    return kb_store.category_insert(
        settings.DATA_DIR,
        settings.KB_ID,
        body.name,
        owner_id=body.owner_id,
        sort_order=body.sort_order,
        session_id=str(sid),
    )


@router_v1.patch("/sessions/{session_id}/kb/categories/{category_id}")
def kb_patch_category(
    request: Request,
    session_id: str,
    category_id: str,
    body: KbCategoryPatch,
    authorization: Optional[str] = Header(default=None),
    x_session_secret: str = Header(..., alias="X-Session-Secret"),
) -> dict[str, Any]:
    require_access_token(authorization)
    check_rate_limit(client_ip(request))
    sid = parse_uuid_param("session_id", session_id)
    verify_session(sid, x_session_secret.strip())
    cid = category_id.strip()
    row = kb_store.category_update(
        settings.DATA_DIR,
        settings.KB_ID,
        cid,
        name=body.name,
        sort_order=body.sort_order,
    )
    if not row:
        raise HTTPException(status_code=404, detail="类目不存在")
    return row


@router_v1.delete("/sessions/{session_id}/kb/categories/{category_id}")
def kb_delete_category(
    request: Request,
    session_id: str,
    category_id: str,
    authorization: Optional[str] = Header(default=None),
    x_session_secret: str = Header(..., alias="X-Session-Secret"),
) -> dict[str, str]:
    require_access_token(authorization)
    check_rate_limit(client_ip(request))
    sid = parse_uuid_param("session_id", session_id)
    verify_session(sid, x_session_secret.strip())
    cid = category_id.strip()
    notes = []
    for cat in kb_store.categories_list(settings.DATA_DIR, settings.KB_ID):
        if cat["id"] == cid:
            notes = kb_store.notes_list_for_category(settings.DATA_DIR, settings.KB_ID, cid)
            break
    for n in notes:
        purge_kb_note_from_disk(settings.KB_ID, str(n["id"]))
    if not kb_store.category_delete(settings.DATA_DIR, settings.KB_ID, cid):
        raise HTTPException(status_code=404, detail="类目不存在")
    return {"status": "deleted"}


@router_v1.get("/sessions/{session_id}/kb/categories/{category_id}/notes")
def kb_list_notes(
    request: Request,
    session_id: str,
    category_id: str,
    authorization: Optional[str] = Header(default=None),
    x_session_secret: str = Header(..., alias="X-Session-Secret"),
) -> list[dict[str, Any]]:
    require_access_token(authorization)
    check_rate_limit(client_ip(request))
    sid = parse_uuid_param("session_id", session_id)
    verify_session(sid, x_session_secret.strip())
    cid = category_id.strip()
    if not kb_store.category_get(settings.DATA_DIR, settings.KB_ID, cid):
        raise HTTPException(status_code=404, detail="类目不存在")
    return kb_store.notes_list_for_category(settings.DATA_DIR, settings.KB_ID, cid)


@router_v1.post("/sessions/{session_id}/kb/categories/{category_id}/notes")
def kb_create_note(
    request: Request,
    session_id: str,
    category_id: str,
    body: KbNoteCreate,
    authorization: Optional[str] = Header(default=None),
    x_session_secret: str = Header(..., alias="X-Session-Secret"),
) -> dict[str, Any]:
    require_access_token(authorization)
    check_rate_limit(client_ip(request))
    sid = parse_uuid_param("session_id", session_id)
    verify_session(sid, x_session_secret.strip())
    cid = category_id.strip()
    try:
        row = kb_store.note_insert(
            settings.DATA_DIR,
            settings.KB_ID,
            cid,
            body.title,
            body.body_markdown,
            owner_id=body.owner_id,
            session_id=str(sid),
        )
    except ValueError:
        raise HTTPException(status_code=404, detail="类目不存在") from None
    sync_kb_note_body_file(settings.KB_ID, str(row["id"]), str(row.get("body_markdown") or ""))
    return row


@router_v1.get("/sessions/{session_id}/kb/notes/{note_id}")
def kb_get_note(
    request: Request,
    session_id: str,
    note_id: str,
    authorization: Optional[str] = Header(default=None),
    x_session_secret: str = Header(..., alias="X-Session-Secret"),
) -> dict[str, Any]:
    require_access_token(authorization)
    check_rate_limit(client_ip(request))
    sid = parse_uuid_param("session_id", session_id)
    verify_session(sid, x_session_secret.strip())
    row = kb_store.note_get(settings.DATA_DIR, settings.KB_ID, note_id.strip())
    if not row:
        raise HTTPException(status_code=404, detail="笔记不存在")
    return row


@router_v1.patch("/sessions/{session_id}/kb/notes/{note_id}")
def kb_patch_note(
    request: Request,
    session_id: str,
    note_id: str,
    body: KbNotePatch,
    authorization: Optional[str] = Header(default=None),
    x_session_secret: str = Header(..., alias="X-Session-Secret"),
) -> dict[str, Any]:
    require_access_token(authorization)
    check_rate_limit(client_ip(request))
    sid = parse_uuid_param("session_id", session_id)
    verify_session(sid, x_session_secret.strip())
    nid = note_id.strip()
    try:
        row = kb_store.note_update(
            settings.DATA_DIR,
            settings.KB_ID,
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
        sync_kb_note_body_file(settings.KB_ID, nid, str(row.get("body_markdown") or ""))
    return row


@router_v1.delete("/sessions/{session_id}/kb/notes/{note_id}")
def kb_delete_note(
    request: Request,
    session_id: str,
    note_id: str,
    authorization: Optional[str] = Header(default=None),
    x_session_secret: str = Header(..., alias="X-Session-Secret"),
) -> dict[str, str]:
    require_access_token(authorization)
    check_rate_limit(client_ip(request))
    sid = parse_uuid_param("session_id", session_id)
    verify_session(sid, x_session_secret.strip())
    nid = note_id.strip()
    if not kb_store.note_get(settings.DATA_DIR, settings.KB_ID, nid):
        raise HTTPException(status_code=404, detail="笔记不存在")
    purge_kb_note_from_disk(settings.KB_ID, nid)
    kb_store.note_delete(settings.DATA_DIR, settings.KB_ID, nid)
    return {"status": "deleted"}


@router_v1.get("/sessions/{session_id}/kb/notes/{note_id}/files", response_model=list[KbNoteFileItem])
def kb_list_note_files(
    request: Request,
    session_id: str,
    note_id: str,
    authorization: Optional[str] = Header(default=None),
    x_session_secret: str = Header(..., alias="X-Session-Secret"),
) -> list[KbNoteFileItem]:
    require_access_token(authorization)
    check_rate_limit(client_ip(request))
    sid = parse_uuid_param("session_id", session_id)
    verify_session(sid, x_session_secret.strip())
    nid = note_id.strip()
    if not kb_store.note_get(settings.DATA_DIR, settings.KB_ID, nid):
        raise HTTPException(status_code=404, detail="笔记不存在")
    rows = kb_store.note_files_list(settings.DATA_DIR, settings.KB_ID, nid)
    return [KbNoteFileItem.model_validate(dict(r)) for r in rows]


@router_v1.post("/sessions/{session_id}/kb/notes/{note_id}/files", response_model=KbNoteFileItem)
async def kb_upload_note_file(
    request: Request,
    session_id: str,
    note_id: str,
    authorization: Optional[str] = Header(default=None),
    x_session_secret: str = Header(..., alias="X-Session-Secret"),
    file: UploadFile = File(...),
) -> KbNoteFileItem:
    require_access_token(authorization)
    check_rate_limit(client_ip(request))
    sid = parse_uuid_param("session_id", session_id)
    verify_session(sid, x_session_secret.strip())
    nid = note_id.strip()
    if not kb_store.note_get(settings.DATA_DIR, settings.KB_ID, nid):
        raise HTTPException(status_code=404, detail="笔记不存在")
    if not file.filename:
        raise HTTPException(status_code=400, detail="无效文件名")
    suffix = Path(file.filename).suffix.lower()
    if suffix not in settings.ALLOWED_SUFFIXES:
        raise HTTPException(status_code=400, detail=f"不支持的文件类型: {suffix or '未知'}")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="空文件")
    if len(content) > settings.MAX_FILE_SIZE_BYTES:
        raise HTTPException(status_code=400, detail=f"单个文件不能超过 {settings.MAX_FILE_SIZE_MB}MB")

    attach_id = uuid.uuid4().hex
    kb_dir = settings.kb_files_dir()
    used = {p.name for p in kb_dir.iterdir() if p.is_file()}
    safe_name = safe_filename(file.filename, used)
    disk_name = f"{attach_id}_{safe_name}"
    stored_rel = f"{settings.KB_ROOT_REL}/files/{disk_name}"
    dest = settings.UPLOAD_DIR / stored_rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)

    row = kb_store.note_file_insert(
        settings.DATA_DIR,
        settings.KB_ID,
        nid,
        Path(file.filename).name,
        stored_rel,
        len(content),
        file.content_type,
        session_id=str(sid),
    )
    return KbNoteFileItem.model_validate(dict(row))


@router_v1.delete("/sessions/{session_id}/kb/notes/{note_id}/files/{file_id}")
def kb_delete_note_file(
    request: Request,
    session_id: str,
    note_id: str,
    file_id: str,
    authorization: Optional[str] = Header(default=None),
    x_session_secret: str = Header(..., alias="X-Session-Secret"),
) -> dict[str, str]:
    require_access_token(authorization)
    check_rate_limit(client_ip(request))
    sid = parse_uuid_param("session_id", session_id)
    verify_session(sid, x_session_secret.strip())
    nid = note_id.strip()
    fid = file_id.strip()
    row = kb_store.note_file_get(settings.DATA_DIR, settings.KB_ID, fid)
    if not row or str(row.get("note_id")) != nid:
        raise HTTPException(status_code=404, detail="附件不存在")
    abs_path = (settings.UPLOAD_DIR / row["stored_rel"]).resolve()
    if abs_path.is_file():
        try:
            invalidate_caches_for_file(abs_path, settings.SERVER_EMBED_MODEL)
        except Exception:
            traceback.print_exc()
        try:
            abs_path.unlink()
        except OSError as e:
            raise HTTPException(status_code=500, detail=f"删除文件失败: {e}") from e
    kb_store.note_file_delete(settings.DATA_DIR, settings.KB_ID, fid)
    return {"status": "deleted"}


@router_v1.post("/sessions/{session_id}/qa", response_model=QAResponse)
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
    require_access_token(authorization)
    check_rate_limit(client_ip(request))
    sid = parse_uuid_param("session_id", session_id)

    if not question.strip():
        raise HTTPException(status_code=400, detail="question 不能为空")
    if len(question) > settings.MAX_QUESTION_CHARS:
        raise HTTPException(status_code=400, detail=f"问题长度不能超过 {settings.MAX_QUESTION_CHARS} 个字符")

    verify_session(sid, x_session_secret.strip())
    cat_ids = kb_store.parse_category_ids_json(category_ids)
    saved_paths, chunk_tags, bundle_extra = collect_qa_index_inputs(sid, kb_scope, cat_ids)
    if not saved_paths:
        raise HTTPException(
            status_code=400,
            detail="没有可检索的内容：请上传会话文件和/或在知识库中添加笔记或附件",
        )
    for p in saved_paths:
        if not p.is_file():
            raise HTTPException(status_code=500, detail="服务器上文件缺失，请重新上传或同步知识库")

    limited_top_k = max(1, min(int(top_k), settings.MAX_TOP_K))

    with session_qa_lock(sid):
        try:
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
            resp = run_qa_pipeline(
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
            raise HTTPException(status_code=500, detail=server_error_detail(e)) from None
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


@router_v1.post("/sessions/{session_id}/qa/async", response_model=QAJobStartResponse)
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
    require_access_token(authorization)
    check_rate_limit(client_ip(request))
    sid = parse_uuid_param("session_id", session_id)

    if not question.strip():
        raise HTTPException(status_code=400, detail="question 不能为空")
    if len(question) > settings.MAX_QUESTION_CHARS:
        raise HTTPException(status_code=400, detail=f"问题长度不能超过 {settings.MAX_QUESTION_CHARS} 个字符")

    verify_session(sid, x_session_secret.strip())
    cat_ids = kb_store.parse_category_ids_json(category_ids)
    paths, _, _ = collect_qa_index_inputs(sid, kb_scope, cat_ids)
    if not paths:
        raise HTTPException(
            status_code=400,
            detail="没有可检索的内容：请上传会话文件和/或在知识库中添加笔记或附件",
        )

    job_id = uuid.uuid4().hex
    qa_job_put_pending(job_id)
    secret = x_session_secret.strip()
    t = threading.Thread(
        target=session_qa_worker,
        args=(job_id, sid, secret, question, top_k, max_new_tokens, kb_scope, category_ids),
        daemon=True,
    )
    t.start()
    return QAJobStartResponse(job_id=job_id)


@router_v1.get("/sessions/{session_id}/qa/jobs/{job_id}", response_model=QAJobStatusResponse)
def get_session_qa_job(
    request: Request,
    session_id: str,
    job_id: str,
    authorization: Optional[str] = Header(default=None),
    x_session_secret: str = Header(..., alias="X-Session-Secret"),
) -> QAJobStatusResponse:
    require_access_token(authorization)
    check_rate_limit(client_ip(request))
    sid = parse_uuid_param("session_id", session_id)
    verify_session(sid, x_session_secret.strip())
    jid = job_id.strip()
    row = qa_job_get(jid)
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在")
    return QAJobStatusResponse(
        status=row["status"],
        detail=row.get("detail"),
        assistant_message_id=row.get("assistant_message_id"),
        user_message_id=row.get("user_message_id"),
        result=row.get("result"),
    )


@router_v1.get("/sessions/{session_id}/messages", response_model=list[ChatMessageItem])
def list_session_messages(
    request: Request,
    session_id: str,
    authorization: Optional[str] = Header(default=None),
    x_session_secret: str = Header(..., alias="X-Session-Secret"),
) -> list[ChatMessageItem]:
    require_access_token(authorization)
    check_rate_limit(client_ip(request))
    sid = parse_uuid_param("session_id", session_id)
    verify_session(sid, x_session_secret.strip())
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


@router_v1.delete("/sessions/{session_id}/messages/{message_id}")
def delete_session_message(
    request: Request,
    session_id: str,
    message_id: str,
    authorization: Optional[str] = Header(default=None),
    x_session_secret: str = Header(..., alias="X-Session-Secret"),
) -> dict[str, str]:
    require_access_token(authorization)
    check_rate_limit(client_ip(request))
    sid = parse_uuid_param("session_id", session_id)
    verify_session(sid, x_session_secret.strip())
    mid = message_id.strip()
    if not mid:
        raise HTTPException(status_code=400, detail="无效的消息 id")
    if not chroma_store.message_delete(sid, mid):
        raise HTTPException(status_code=404, detail="消息不存在")
    return {"status": "deleted"}


@router_v1.delete("/sessions/{session_id}/messages")
def delete_all_session_messages(
    request: Request,
    session_id: str,
    authorization: Optional[str] = Header(default=None),
    x_session_secret: str = Header(..., alias="X-Session-Secret"),
) -> dict[str, str | int]:
    require_access_token(authorization)
    check_rate_limit(client_ip(request))
    sid = parse_uuid_param("session_id", session_id)
    verify_session(sid, x_session_secret.strip())
    n = chroma_store.messages_delete_all(sid)
    return {"status": "ok", "deleted": n}


@router_v1.post("/sessions/{session_id}/quiz/generate", response_model=QuizBundlePublic)
def generate_session_quiz(
    request: Request,
    session_id: str,
    body: QuizGenerateRequest,
    authorization: Optional[str] = Header(default=None),
    x_session_secret: str = Header(..., alias="X-Session-Secret"),
) -> QuizBundlePublic:
    require_access_token(authorization)
    check_rate_limit(client_ip(request))
    sid = parse_uuid_param("session_id", session_id)
    verify_session(sid, x_session_secret.strip())
    merged = merge_quiz_segments(body.segments or [])
    if not merged:
        raise HTTPException(status_code=400, detail="至少选择一条消息")
    total_n = sum(s.count for s in merged)
    if total_n > settings.MAX_QUIZ_QUESTIONS_TOTAL:
        raise HTTPException(
            status_code=400,
            detail=f"题目总数不能超过 {settings.MAX_QUIZ_QUESTIONS_TOTAL}（当前为 {total_n}）",
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
    saved_paths = [(settings.UPLOAD_DIR / r["stored_rel"]).resolve() for r in rows]
    for p in saved_paths:
        if not p.is_file():
            raise HTTPException(status_code=500, detail="服务器上文件缺失，请重新上传")

    limited_top_k = max(1, min(settings.MAX_TOP_K, 5))
    with session_qa_lock(sid):
        try:
            chunks, _embeddings, index, st = build_or_load_index(saved_paths, settings.SERVER_EMBED_MODEL)
            hits = search(search_q, chunks, index, st, top_k=limited_top_k)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except Exception as e:
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=server_error_detail(e)) from None

    prev_texts = chroma_store.quiz_list_question_texts(sid)
    forbidden_lower = {t.casefold() for t in prev_texts if t}
    raw_bundle, quiz_fail = generate_quiz_bundle_for_segments(
        hits, resolved_segments, forbidden_lower, total_n
    )
    if not raw_bundle:
        raise HTTPException(status_code=503, detail=quiz_generation_fail_detail(quiz_fail))
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
    return build_quiz_public(quiz_id, raw_bundle["items"])


@router_v1.get("/sessions/{session_id}/quiz/{quiz_id}", response_model=QuizBundlePublic)
def get_session_quiz_bundle(
    request: Request,
    session_id: str,
    quiz_id: str,
    authorization: Optional[str] = Header(default=None),
    x_session_secret: str = Header(..., alias="X-Session-Secret"),
) -> QuizBundlePublic:
    require_access_token(authorization)
    check_rate_limit(client_ip(request))
    sid = parse_uuid_param("session_id", session_id)
    verify_session(sid, x_session_secret.strip())
    qid = quiz_id.strip()
    got = chroma_store.quiz_get(qid)
    if not got:
        raise HTTPException(status_code=404, detail="测验不存在")
    db_sid, payload = got
    if db_sid != sid:
        raise HTTPException(status_code=403, detail="无权访问该测验")
    items = payload.get("items") or []
    return build_quiz_public(qid, items)


@router_v1.post("/qa", response_model=QAResponse)
async def ask_doc_qa(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    question: str = Form(..., description="用户问题"),
    files: list[UploadFile] = File(..., description="一个或多个文档文件"),
    top_k: int = Form(3, description="检索片段条数"),
    max_new_tokens: Optional[int] = Form(None, description="生成 token 上限"),
) -> QAResponse:
    require_access_token(authorization)
    check_rate_limit(client_ip(request))

    if not question.strip():
        raise HTTPException(status_code=400, detail="question 不能为空")
    if len(question) > settings.MAX_QUESTION_CHARS:
        raise HTTPException(status_code=400, detail=f"问题长度不能超过 {settings.MAX_QUESTION_CHARS} 个字符")
    if not files:
        raise HTTPException(status_code=400, detail="至少上传一个文件")
    if len(files) > settings.MAX_FILES:
        raise HTTPException(status_code=400, detail=f"最多只能上传 {settings.MAX_FILES} 个文件")

    request_dir = settings.UPLOAD_DIR / uuid.uuid4().hex
    request_dir.mkdir(parents=True, exist_ok=True)
    saved_paths: list[Path] = []
    used_names: set[str] = set()

    try:
        for f in files:
            if not f.filename:
                continue
            suffix = Path(f.filename).suffix.lower()
            if suffix not in settings.ALLOWED_SUFFIXES:
                raise HTTPException(status_code=400, detail=f"不支持的文件类型: {suffix or '未知'}")

            content = await f.read()
            if not content:
                continue
            if len(content) > settings.MAX_FILE_SIZE_BYTES:
                raise HTTPException(status_code=400, detail=f"单个文件不能超过 {settings.MAX_FILE_SIZE_MB}MB")

            safe_name = safe_filename(f.filename, used_names)
            dest = request_dir / safe_name
            dest.write_bytes(content)
            saved_paths.append(dest)

        if not saved_paths:
            raise HTTPException(status_code=400, detail="未接收到有效文件")

        limited_top_k = max(1, min(int(top_k), settings.MAX_TOP_K))
        chunks, _embeddings, index, st = build_or_load_index(saved_paths, settings.SERVER_EMBED_MODEL)
        hits = rag_pipeline.hybrid_retrieve(
            question,
            chunks,
            index,
            st,
            lambda prompt, max_tok, **kw: invoke_llm(prompt, max_tok, **kw),
            limited_top_k,
        )
        return run_qa_pipeline(
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
        raise HTTPException(status_code=500, detail=server_error_detail(e)) from e
    finally:
        cleanup_dir(request_dir)


@router_v1.post("/sessions/{session_id}/quiz/{quiz_id}/grade", response_model=QuizGradeResponse)
def grade_session_quiz(
    request: Request,
    session_id: str,
    quiz_id: str,
    body: QuizGradeRequest,
    authorization: Optional[str] = Header(default=None),
    x_session_secret: str = Header(..., alias="X-Session-Secret"),
) -> QuizGradeResponse:
    require_access_token(authorization)
    check_rate_limit(client_ip(request))
    sid = parse_uuid_param("session_id", session_id)
    qid = quiz_id.strip()

    verify_session(sid, x_session_secret.strip())
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

    return grade_quiz_with_llm(payload, body.answers)


@router_v1.post("/quiz/{quiz_id}/grade", response_model=QuizGradeResponse)
def grade_standalone_quiz(
    request: Request,
    quiz_id: str,
    body: QuizGradeRequest,
    authorization: Optional[str] = Header(default=None),
) -> QuizGradeResponse:
    require_access_token(authorization)
    check_rate_limit(client_ip(request))
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

    return grade_quiz_with_llm(payload, body.answers)
