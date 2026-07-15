#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""课堂练习 API：模板、导入、列表、上下架、从笔记 AI 出题、题库导出。"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

import chroma_store
import exercise_store

from rag_api import exercise_service, settings
from rag_api.auth import require_login, require_teacher, resolve_current_user
from rag_api.http_common import check_rate_limit, client_ip, require_access_token
from rag_api.qa_llm import build_quiz_public, grade_quiz_with_llm
from rag_api.schemas import QuizBundlePublic, QuizGradeRequest, QuizGradeResponse

router_exercises = APIRouter(prefix="/api/v1/kb/exercises", tags=["exercises"])
router_quiz_public = APIRouter(prefix="/api/v1/quiz", tags=["quiz"])


class ExercisePatch(BaseModel):
    title: Optional[str] = None
    status: Optional[str] = None


class GenerateFromNoteRequest(BaseModel):
    note_id: str
    n: int = Field(default=5, ge=1, le=20)
    title: Optional[str] = None
    publish: bool = False


class ExportBankRequest(BaseModel):
    format: str = "xlsx"  # xlsx | csv
    items: Optional[list[dict[str, Any]]] = None
    quiz_id: Optional[str] = None


def _is_teacher(user: Optional[dict[str, Any]]) -> bool:
    return bool(user and user.get("role") == "teacher")


@router_exercises.get("/template.csv")
def download_template_csv(
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> Response:
    require_access_token(authorization)
    require_login(resolve_current_user(authorization))
    check_rate_limit(client_ip(request))
    return Response(
        content=exercise_service.template_csv_bytes(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="class-exercise-template.csv"'},
    )


@router_exercises.get("/template.xlsx")
def download_template_xlsx(
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> Response:
    require_access_token(authorization)
    require_login(resolve_current_user(authorization))
    check_rate_limit(client_ip(request))
    try:
        payload = exercise_service.template_xlsx_bytes()
    except ModuleNotFoundError as e:
        raise HTTPException(status_code=503, detail="缺少 openpyxl，无法生成 Excel 模板") from e
    return Response(
        content=payload,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="class-exercise-template.xlsx"'},
    )


@router_exercises.get("")
def list_exercises(
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> list[dict[str, Any]]:
    require_access_token(authorization)
    user = require_login(resolve_current_user(authorization))
    check_rate_limit(client_ip(request))
    published_only = not _is_teacher(user)
    # no-auth local mode: treat as teacher (empty role)
    if user.get("role") in ("", None) and not settings.REQUIRE_AUTH:
        published_only = False
    rows = exercise_store.exercises_list(
        settings.DATA_DIR, settings.KB_ID, published_only=published_only
    )
    return rows


@router_exercises.post("/import")
async def import_exercise(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    title: str = Form(...),
    file: UploadFile = File(...),
    status: str = Form(default="published"),
) -> dict[str, Any]:
    require_access_token(authorization)
    user = require_teacher(resolve_current_user(authorization))
    check_rate_limit(client_ip(request))
    if not file.filename:
        raise HTTPException(status_code=400, detail="无效文件名")
    suffix = Path(file.filename).suffix.lower()
    if suffix not in (".csv", ".xlsx", ".xlsm"):
        raise HTTPException(status_code=400, detail="仅支持 .csv 或 .xlsx")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="空文件")
    items, errors = exercise_service.parse_bank_bytes(content, file.filename)
    if errors:
        raise HTTPException(status_code=400, detail="; ".join(errors[:8]))
    st = status if status in ("published", "unpublished") else "published"
    row = exercise_service.create_exercise_from_items(
        items,
        title=title,
        created_by=str(user.get("id") or "") or None,
        source_filename=Path(file.filename).name,
        status=st,
    )
    return row


@router_exercises.patch("/{exercise_id}")
def patch_exercise(
    request: Request,
    exercise_id: str,
    body: ExercisePatch,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    require_access_token(authorization)
    require_teacher(resolve_current_user(authorization))
    check_rate_limit(client_ip(request))
    try:
        row = exercise_store.exercise_update(
            settings.DATA_DIR,
            settings.KB_ID,
            exercise_id.strip(),
            title=body.title,
            status=body.status,
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="status 须为 published 或 unpublished") from None
    if not row:
        raise HTTPException(status_code=404, detail="练习不存在")
    return row


@router_exercises.delete("/{exercise_id}")
def delete_exercise(
    request: Request,
    exercise_id: str,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, str]:
    require_access_token(authorization)
    require_teacher(resolve_current_user(authorization))
    check_rate_limit(client_ip(request))
    if not exercise_service.delete_exercise(exercise_id.strip()):
        raise HTTPException(status_code=404, detail="练习不存在")
    return {"status": "ok"}


@router_exercises.post("/generate-from-note")
def generate_from_note(
    request: Request,
    body: GenerateFromNoteRequest,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    require_access_token(authorization)
    user = require_teacher(resolve_current_user(authorization))
    check_rate_limit(client_ip(request))
    try:
        return exercise_service.generate_and_optionally_publish(
            body.note_id.strip(),
            n=body.n,
            title=body.title,
            publish=body.publish,
            created_by=str(user.get("id") or "") or None,
        )
    except ValueError as e:
        code = str(e)
        if code == "note_not_found":
            raise HTTPException(status_code=404, detail="笔记不存在") from e
        if code == "no_questions_in_note":
            raise HTTPException(status_code=400, detail="笔记中未解析到学生提问列表") from e
        raise HTTPException(status_code=400, detail=code) from e
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@router_exercises.post("/export-bank")
def export_bank(
    request: Request,
    body: ExportBankRequest,
    authorization: Optional[str] = Header(default=None),
) -> Response:
    require_access_token(authorization)
    require_teacher(resolve_current_user(authorization))
    check_rate_limit(client_ip(request))
    items = body.items
    if not items and body.quiz_id:
        got = chroma_store.quiz_get(body.quiz_id.strip())
        if not got:
            raise HTTPException(status_code=404, detail="测验不存在")
        items = (got[1] or {}).get("items") or []
    if not items:
        raise HTTPException(status_code=400, detail="无题目可导出")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    fmt = (body.format or "xlsx").lower()
    if fmt == "csv":
        payload = exercise_service.bank_to_csv(items)
        return Response(
            content=payload,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="quiz-bank-{stamp}.csv"'},
        )
    try:
        payload = exercise_service.bank_to_xlsx(items)
    except ModuleNotFoundError as e:
        raise HTTPException(status_code=503, detail="缺少 openpyxl") from e
    return Response(
        content=payload,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="quiz-bank-{stamp}.xlsx"'},
    )


@router_quiz_public.get("/{quiz_id}", response_model=QuizBundlePublic)
def get_public_quiz(
    request: Request,
    quiz_id: str,
    authorization: Optional[str] = Header(default=None),
) -> QuizBundlePublic:
    require_access_token(authorization)
    user = require_login(resolve_current_user(authorization))
    check_rate_limit(client_ip(request))
    qid = quiz_id.strip()
    ex = exercise_store.exercise_get_by_quiz(settings.DATA_DIR, settings.KB_ID, qid)
    if not ex:
        raise HTTPException(status_code=404, detail="测验不存在")
    teacher = _is_teacher(user) or (
        user.get("role") in ("", None) and not settings.REQUIRE_AUTH
    )
    if ex.get("status") != exercise_store.STATUS_PUBLISHED and not teacher:
        raise HTTPException(status_code=403, detail="该练习尚未发布")
    got = chroma_store.quiz_get(qid)
    if not got:
        raise HTTPException(status_code=404, detail="测验内容缺失")
    _sid, payload = got
    return build_quiz_public(qid, payload.get("items") or [])


@router_quiz_public.post("/{quiz_id}/grade", response_model=QuizGradeResponse)
def grade_public_quiz(
    request: Request,
    quiz_id: str,
    body: QuizGradeRequest,
    authorization: Optional[str] = Header(default=None),
) -> QuizGradeResponse:
    """课堂练习判分（覆盖/增强 routes 中同路径行为：校验发布状态并按用户存答）。"""
    require_access_token(authorization)
    user = require_login(resolve_current_user(authorization))
    check_rate_limit(client_ip(request))
    qid = quiz_id.strip()
    ex = exercise_store.exercise_get_by_quiz(settings.DATA_DIR, settings.KB_ID, qid)
    teacher = _is_teacher(user) or (
        user.get("role") in ("", None) and not settings.REQUIRE_AUTH
    )
    if ex:
        if ex.get("status") != exercise_store.STATUS_PUBLISHED and not teacher:
            raise HTTPException(status_code=403, detail="该练习尚未发布")
    got = chroma_store.quiz_get(qid)
    if not got:
        raise HTTPException(status_code=404, detail="测验不存在或已过期")
    db_sid, payload = got
    if db_sid is not None and not ex:
        raise HTTPException(status_code=400, detail="请使用会话判分接口")
    expected = len(payload.get("items") or [])
    if expected <= 0:
        raise HTTPException(status_code=400, detail="测验数据无效")
    if len(body.answers) != expected:
        raise HTTPException(status_code=400, detail=f"请提交恰好 {expected} 条答案")

    result = grade_quiz_with_llm(payload, body.answers)
    uid = str(user.get("id") or "").strip() or None
    try:
        chroma_store.quiz_answer_save(
            qid,
            None,
            {
                "answers": list(body.answers),
                "grade": result.model_dump(),
                "owner": {
                    "user_id": uid,
                    "username": user.get("username"),
                    "display_name": user.get("display_name"),
                    "student_no": user.get("student_no"),
                    "role": user.get("role"),
                },
            },
            time.time(),
            user_id=uid,
        )
    except Exception:
        pass
    return result
