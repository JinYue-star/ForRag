#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""课堂练习元数据（SQLite，与 kb.sqlite 同库）。"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any, Optional

import kb_store

STATUS_PUBLISHED = "published"
STATUS_UNPUBLISHED = "unpublished"


def init_exercises(data_dir: Path) -> None:
    kb_store.init_kb_db(data_dir)


def exercise_insert(
    data_dir: Path,
    kb_id: str,
    *,
    title: str,
    quiz_id: str,
    item_count: int,
    status: str = STATUS_PUBLISHED,
    source_filename: Optional[str] = None,
    source_note_id: Optional[str] = None,
    created_by: Optional[str] = None,
) -> dict[str, Any]:
    init_exercises(data_dir)
    eid = uuid.uuid4().hex
    now = time.time()
    st = status if status in (STATUS_PUBLISHED, STATUS_UNPUBLISHED) else STATUS_PUBLISHED
    cx = kb_store.db_conn(data_dir)
    cx.execute(
        "INSERT INTO class_exercises "
        "(id, quiz_id, kb_id, title, status, item_count, source_filename, source_note_id, "
        "created_by, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            eid,
            quiz_id,
            kb_id,
            (title or "").strip() or "Untitled exercise",
            st,
            int(item_count),
            source_filename,
            source_note_id,
            created_by,
            now,
            now,
        ),
    )
    cx.commit()
    row = exercise_get(data_dir, kb_id, eid)
    assert row
    return row


def exercise_get(data_dir: Path, kb_id: str, exercise_id: str) -> Optional[dict[str, Any]]:
    init_exercises(data_dir)
    cx = kb_store._conn(data_dir)  # noqa: SLF001
    row = cx.execute(
        "SELECT * FROM class_exercises WHERE kb_id=? AND id=?",
        (kb_id, exercise_id),
    ).fetchone()
    return dict(row) if row else None


def exercise_get_by_quiz(data_dir: Path, kb_id: str, quiz_id: str) -> Optional[dict[str, Any]]:
    init_exercises(data_dir)
    cx = kb_store._conn(data_dir)  # noqa: SLF001
    row = cx.execute(
        "SELECT * FROM class_exercises WHERE kb_id=? AND quiz_id=?",
        (kb_id, quiz_id),
    ).fetchone()
    return dict(row) if row else None


def exercises_list(
    data_dir: Path,
    kb_id: str,
    *,
    published_only: bool = False,
) -> list[dict[str, Any]]:
    init_exercises(data_dir)
    cx = kb_store._conn(data_dir)  # noqa: SLF001
    if published_only:
        rows = cx.execute(
            "SELECT * FROM class_exercises WHERE kb_id=? AND status=? "
            "ORDER BY updated_at DESC",
            (kb_id, STATUS_PUBLISHED),
        ).fetchall()
    else:
        rows = cx.execute(
            "SELECT * FROM class_exercises WHERE kb_id=? ORDER BY updated_at DESC",
            (kb_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def exercise_update(
    data_dir: Path,
    kb_id: str,
    exercise_id: str,
    *,
    title: Optional[str] = None,
    status: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    cur = exercise_get(data_dir, kb_id, exercise_id)
    if not cur:
        return None
    fields: list[str] = []
    vals: list[Any] = []
    if title is not None:
        fields.append("title=?")
        vals.append(title.strip() or "Untitled exercise")
    if status is not None:
        if status not in (STATUS_PUBLISHED, STATUS_UNPUBLISHED):
            raise ValueError("invalid_status")
        fields.append("status=?")
        vals.append(status)
    if not fields:
        return cur
    fields.append("updated_at=?")
    vals.append(time.time())
    vals.extend([kb_id, exercise_id])
    cx = kb_store._conn(data_dir)  # noqa: SLF001
    cx.execute(
        f"UPDATE class_exercises SET {', '.join(fields)} WHERE kb_id=? AND id=?",
        vals,
    )
    cx.commit()
    return exercise_get(data_dir, kb_id, exercise_id)


def exercise_delete(data_dir: Path, kb_id: str, exercise_id: str) -> Optional[dict[str, Any]]:
    row = exercise_get(data_dir, kb_id, exercise_id)
    if not row:
        return None
    cx = kb_store._conn(data_dir)  # noqa: SLF001
    cx.execute("DELETE FROM class_exercises WHERE kb_id=? AND id=?", (kb_id, exercise_id))
    cx.commit()
    return row
