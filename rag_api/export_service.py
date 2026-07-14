#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""教师导出：汇总学生提问、系统回答摘要与测验作答，序列化为 CSV / Excel。

数据来源：
- 会话（含 owner 归因） + 消息（问答对）
- 测验批次（题干/选项/标准答案） + 学生作答与判分
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Any, Optional

import chroma_store

ANSWER_SUMMARY_MAX = 500

QUESTION_COLUMNS = ["time", "student_name", "student_no", "username", "session_id", "question"]
ANSWER_COLUMNS = [
    "time", "student_name", "student_no", "username", "session_id",
    "question", "answer_summary", "grounded",
]
QUIZ_COLUMNS = [
    "time", "student_name", "student_no", "username", "quiz_id", "q_index",
    "type", "question", "options", "correct_answer", "student_answer", "is_correct",
]

MODULE_COLUMNS = {
    "questions": QUESTION_COLUMNS,
    "answers": ANSWER_COLUMNS,
    "quiz": QUIZ_COLUMNS,
}
MODULE_SHEET = {"questions": "Questions", "answers": "Answer summaries", "quiz": "Quiz"}


def _fmt_time(ts: float) -> str:
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).astimezone().strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    except (ValueError, OSError, OverflowError):
        return ""


def _owner_fields(owner: Optional[dict[str, Any]]) -> dict[str, str]:
    o = owner or {}
    return {
        "student_name": str(o.get("display_name") or ""),
        "student_no": str(o.get("student_no") or ""),
        "username": str(o.get("username") or ""),
    }


def _in_range(ts: float, start: Optional[float], end: Optional[float]) -> bool:
    if start is not None and ts < start:
        return False
    if end is not None and ts > end:
        return False
    return True


def _correct_answer_from_item(item: dict[str, Any]) -> str:
    opts = [str(x) for x in (item.get("options") or [])]
    if "correct_index" in item:
        ci = item.get("correct_index")
        if isinstance(ci, int) and 0 <= ci < len(opts):
            return opts[ci]
    if "correct_indices" in item:
        idxs = item.get("correct_indices") or []
        picked = [opts[i] for i in idxs if isinstance(i, int) and 0 <= i < len(opts)]
        return "; ".join(picked)
    return ""


def gather(
    start: Optional[float],
    end: Optional[float],
    course_ids: Optional[list[str]],
    include_questions: bool,
    include_answers: bool,
    include_quiz: bool,
) -> dict[str, list[dict[str, Any]]]:
    """按筛选条件汇总各模块行数据。course_ids 预留多课程（当前单课程忽略）。"""
    sessions = {s["session_id"]: s for s in chroma_store.sessions_list_all()}
    owners = {sid: s.get("owner") for sid, s in sessions.items()}

    data: dict[str, list[dict[str, Any]]] = {"questions": [], "answers": [], "quiz": []}

    if include_questions or include_answers:
        for sid in sessions:
            owner = _owner_fields(owners.get(sid))
            try:
                msgs = chroma_store.messages_list(sid)
            except Exception:
                continue
            for i, m in enumerate(msgs):
                if m.get("role") != "user":
                    continue
                ts = float(m.get("created_at") or 0)
                if not _in_range(ts, start, end):
                    continue
                question = str(m.get("content") or "").strip()
                if include_questions:
                    row = {"time": _fmt_time(ts), "session_id": sid, "question": question}
                    row.update(owner)
                    data["questions"].append(row)
                if include_answers:
                    ans = ""
                    grounded = ""
                    for j in range(i + 1, len(msgs)):
                        if msgs[j].get("role") == "assistant":
                            ans = str(msgs[j].get("content") or "").strip()
                            extra = msgs[j].get("extra") or {}
                            grounded = "yes" if extra.get("kb_relevant") else "no"
                            break
                    row = {
                        "time": _fmt_time(ts),
                        "session_id": sid,
                        "question": question,
                        "answer_summary": ans[:ANSWER_SUMMARY_MAX],
                        "grounded": grounded,
                    }
                    row.update(owner)
                    data["answers"].append(row)

    if include_quiz:
        answers_map = chroma_store.quiz_answers_map()
        for quiz in chroma_store.quiz_list_all():
            ts = float(quiz.get("created_at") or 0)
            if not _in_range(ts, start, end):
                continue
            sid = quiz.get("session_id") or ""
            owner = _owner_fields(owners.get(sid))
            qid = quiz.get("quiz_id")
            items = (quiz.get("payload") or {}).get("items") or []
            submission = answers_map.get(qid) or {}
            grade_items = (submission.get("grade") or {}).get("items") or []
            for idx, it in enumerate(items):
                g = grade_items[idx] if idx < len(grade_items) else {}
                correct = _correct_answer_from_item(it) or str(g.get("correct_answer") or "")
                student_answer = str(g.get("user_answer") or "") if g else ""
                is_correct = ""
                if g:
                    try:
                        is_correct = "yes" if float(g.get("score", 0)) >= float(g.get("max_score", 0)) and float(g.get("max_score", 0)) > 0 else "no"
                    except (TypeError, ValueError):
                        is_correct = ""
                row = {
                    "time": _fmt_time(ts),
                    "quiz_id": qid,
                    "q_index": idx + 1,
                    "type": str(it.get("type") or ""),
                    "question": str(it.get("question") or ""),
                    "options": " | ".join(str(x) for x in (it.get("options") or [])),
                    "correct_answer": correct,
                    "student_answer": student_answer,
                    "is_correct": is_correct,
                }
                row.update(owner)
                data["quiz"].append(row)

    return data


def selected_modules(include_questions: bool, include_answers: bool, include_quiz: bool) -> list[str]:
    mods: list[str] = []
    if include_questions:
        mods.append("questions")
    if include_answers:
        mods.append("answers")
    if include_quiz:
        mods.append("quiz")
    return mods


def preview(data: dict[str, list[dict[str, Any]]], modules: list[str], limit: int = 50) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for m in modules:
        rows = data.get(m, [])
        out[m] = {
            "columns": MODULE_COLUMNS[m],
            "rows": rows[:limit],
            "total": len(rows),
        }
    return out


def to_csv(data: dict[str, list[dict[str, Any]]], modules: list[str]) -> bytes:
    """输出单个 .csv。多个模块时在同一文件中分段堆叠，段间用标题行分隔。"""
    sio = io.StringIO()
    writer = csv.writer(sio)
    multi = len(modules) > 1
    for mi, m in enumerate(modules):
        cols = MODULE_COLUMNS[m]
        if mi > 0:
            writer.writerow([])
        if multi:
            writer.writerow([f"# {MODULE_SHEET[m]}"])
        writer.writerow(cols)
        for row in data.get(m, []):
            writer.writerow([row.get(c, "") for c in cols])
    return sio.getvalue().encode("utf-8-sig")


def to_xlsx(data: dict[str, list[dict[str, Any]]], modules: list[str]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    wb.remove(wb.active)
    header_font = Font(bold=True)
    for m in modules:
        cols = MODULE_COLUMNS[m]
        ws = wb.create_sheet(title=MODULE_SHEET[m][:31])
        ws.append(cols)
        for cell in ws[1]:
            cell.font = header_font
        for row in data.get(m, []):
            ws.append([row.get(c, "") for c in cols])
        # reasonable column widths
        for col_idx, name in enumerate(cols, start=1):
            width = 18 if name not in ("question", "answer_summary", "options") else 48
            ws.column_dimensions[ws.cell(row=1, column=col_idx).column_letter].width = width
    if not modules:
        wb.create_sheet(title="Empty")
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
