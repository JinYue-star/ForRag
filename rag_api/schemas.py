#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HTTP API 请求/响应 Pydantic 模型。"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator


class HitItem(BaseModel):
    score: float = Field(..., description="归一化相关度 0～100，仅用于展示（余弦类为绝对比例，重排 logits 等为批内 min-max）")
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
    score: float = Field(..., description="对应命中的归一化相关度 0～100，与 HitItem.score 一致口径")
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


# ---------------- 师生账号鉴权 ----------------

class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(BaseModel):
    username: str
    password: str
    display_name: str = ""
    student_no: str = ""
    code: str = ""


class UserPublic(BaseModel):
    id: str
    username: str
    role: str
    display_name: str = ""
    student_no: str = ""
    is_active: bool = True
    created_at: float = 0


class LoginResponse(BaseModel):
    token: str
    expires_at: float
    user: UserPublic


class CreateUserRequest(BaseModel):
    username: str
    password: str
    display_name: str = ""
    student_no: str = ""
    role: str = "student"


class RegistrationCodeResponse(BaseModel):
    code: str


class ExportRequest(BaseModel):
    start: Optional[float] = None
    end: Optional[float] = None
    course_ids: Optional[list[str]] = None
    include_questions: bool = True
    include_answers: bool = False
    include_quiz: bool = True
    format: str = "xlsx"  # xlsx | csv
