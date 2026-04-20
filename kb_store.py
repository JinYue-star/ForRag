#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""会话级知识库（SQLite）：一级类目、笔记正文、附件元数据；预留 owner_id。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

_conn_holder: dict[str, sqlite3.Connection] = {}
_lock = threading.Lock()


def _db_path(data_dir: Path) -> Path:
    return (data_dir / "kb.sqlite").resolve()


def init_kb_db(data_dir: Path) -> None:
    """在 RAG_DATA_DIR 下创建/迁移 kb.sqlite。"""
    data_dir = data_dir.resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    path = _db_path(data_dir)
    key = str(path)
    with _lock:
        if key in _conn_holder:
            return
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _ensure_schema(conn)
        _conn_holder[key] = conn


def _conn(data_dir: Path) -> sqlite3.Connection:
    key = str(_db_path(data_dir))
    with _lock:
        c = _conn_holder.get(key)
        if c is None:
            init_kb_db(data_dir)
            c = _conn_holder[key]
        return c


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS kb_categories (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            owner_id TEXT,
            name TEXT NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_kb_cat_session ON kb_categories(session_id);

        CREATE TABLE IF NOT EXISTS kb_notes (
            id TEXT PRIMARY KEY,
            category_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            owner_id TEXT,
            title TEXT NOT NULL,
            body_markdown TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            FOREIGN KEY (category_id) REFERENCES kb_categories(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_kb_notes_session ON kb_notes(session_id);
        CREATE INDEX IF NOT EXISTS idx_kb_notes_cat ON kb_notes(category_id);

        CREATE TABLE IF NOT EXISTS kb_note_files (
            id TEXT PRIMARY KEY,
            note_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            original_name TEXT NOT NULL,
            stored_rel TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            mime TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            FOREIGN KEY (note_id) REFERENCES kb_notes(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_kb_nf_note ON kb_note_files(note_id);
        CREATE INDEX IF NOT EXISTS idx_kb_nf_session ON kb_note_files(session_id);
        """
    )
    conn.commit()


def session_kb_bundle_token(data_dir: Path, session_id: str) -> str:
    """用于向量 bundle 缓存键：任意笔记/附件变更即变化。"""
    cx = _conn(data_dir)
    parts: list[str] = []
    for row in cx.execute(
        "SELECT id, updated_at, title, LENGTH(body_markdown) FROM kb_notes WHERE session_id=? ORDER BY id",
        (session_id,),
    ):
        parts.append(f"n:{row[0]}:{row[1]}:{row[2]}:{row[3]}")
    for row in cx.execute(
        "SELECT id, updated_at, stored_rel, size_bytes FROM kb_note_files WHERE session_id=? ORDER BY id",
        (session_id,),
    ):
        parts.append(f"f:{row[0]}:{row[1]}:{row[2]}:{row[3]}")
    for row in cx.execute(
        "SELECT id, updated_at, sort_order, name FROM kb_categories WHERE session_id=? ORDER BY id",
        (session_id,),
    ):
        parts.append(f"c:{row[0]}:{row[1]}:{row[2]}:{row[3]}")
    raw = "\n".join(parts) if parts else "empty"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


# ---------- categories ----------


def categories_list(data_dir: Path, session_id: str) -> list[dict[str, Any]]:
    cx = _conn(data_dir)
    rows = cx.execute(
        "SELECT id, session_id, owner_id, name, sort_order, created_at, updated_at "
        "FROM kb_categories WHERE session_id=? ORDER BY sort_order ASC, created_at ASC",
        (session_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def category_insert(
    data_dir: Path,
    session_id: str,
    name: str,
    *,
    owner_id: Optional[str] = None,
    sort_order: int = 0,
) -> dict[str, Any]:
    cid = uuid.uuid4().hex
    now = time.time()
    cx = _conn(data_dir)
    cx.execute(
        "INSERT INTO kb_categories (id, session_id, owner_id, name, sort_order, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (cid, session_id, owner_id, name.strip() or "未命名", int(sort_order), now, now),
    )
    cx.commit()
    got = category_get(data_dir, session_id, cid)
    if not got:
        raise RuntimeError("category_insert failed")
    return got


def category_get(data_dir: Path, session_id: str, category_id: str) -> Optional[dict[str, Any]]:
    cx = _conn(data_dir)
    row = cx.execute(
        "SELECT id, session_id, owner_id, name, sort_order, created_at, updated_at FROM kb_categories "
        "WHERE session_id=? AND id=?",
        (session_id, category_id),
    ).fetchone()
    return dict(row) if row else None


def category_update(
    data_dir: Path,
    session_id: str,
    category_id: str,
    *,
    name: Optional[str] = None,
    sort_order: Optional[int] = None,
) -> Optional[dict[str, Any]]:
    cur = category_get(data_dir, session_id, category_id)
    if not cur:
        return None
    now = time.time()
    fields = []
    vals: list[Any] = []
    if name is not None:
        fields.append("name=?")
        vals.append(name.strip() or "未命名")
    if sort_order is not None:
        fields.append("sort_order=?")
        vals.append(int(sort_order))
    if not fields:
        return cur
    fields.append("updated_at=?")
    vals.append(now)
    vals.extend([session_id, category_id])
    cx = _conn(data_dir)
    cx.execute(f"UPDATE kb_categories SET {', '.join(fields)} WHERE session_id=? AND id=?", vals)
    cx.commit()
    return category_get(data_dir, session_id, category_id)


def category_delete(data_dir: Path, session_id: str, category_id: str) -> bool:
    cx = _conn(data_dir)
    cur = cx.execute(
        "DELETE FROM kb_categories WHERE session_id=? AND id=?",
        (session_id, category_id),
    )
    cx.commit()
    return cur.rowcount > 0


# ---------- notes ----------


def notes_list_for_category(data_dir: Path, session_id: str, category_id: str) -> list[dict[str, Any]]:
    cx = _conn(data_dir)
    rows = cx.execute(
        "SELECT id, category_id, session_id, owner_id, title, body_markdown, created_at, updated_at "
        "FROM kb_notes WHERE session_id=? AND category_id=? ORDER BY updated_at DESC",
        (session_id, category_id),
    ).fetchall()
    return [dict(r) for r in rows]


def notes_list_session(data_dir: Path, session_id: str) -> list[dict[str, Any]]:
    cx = _conn(data_dir)
    rows = cx.execute(
        "SELECT id, category_id, session_id, owner_id, title, body_markdown, created_at, updated_at "
        "FROM kb_notes WHERE session_id=? ORDER BY updated_at DESC",
        (session_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def kb_note_ids_for_rag(data_dir: Path, session_id: str, category_ids: Optional[list[str]] = None) -> list[str]:
    """用于构建索引：返回参与检索的笔记 id 列表。"""
    cx = _conn(data_dir)
    if category_ids:
        placeholders = ",".join("?" * len(category_ids))
        q = f"SELECT id FROM kb_notes WHERE session_id=? AND category_id IN ({placeholders}) ORDER BY updated_at"
        rows = cx.execute(q, (session_id, *category_ids)).fetchall()
    else:
        rows = cx.execute(
            "SELECT id FROM kb_notes WHERE session_id=? ORDER BY updated_at",
            (session_id,),
        ).fetchall()
    return [str(r[0]) for r in rows]


def kb_attachment_rows_for_rag(
    data_dir: Path, session_id: str, category_ids: Optional[list[str]] = None
) -> list[dict[str, Any]]:
    """附件行：若限定类目，则只包含这些类目下笔记的附件。"""
    cx = _conn(data_dir)
    if category_ids:
        placeholders = ",".join("?" * len(category_ids))
        q = (
            "SELECT f.id AS id, f.note_id AS note_id, f.stored_rel AS stored_rel, "
            "f.original_name AS original_name, f.size_bytes AS size_bytes "
            "FROM kb_note_files f JOIN kb_notes n ON n.id=f.note_id "
            f"WHERE f.session_id=? AND n.category_id IN ({placeholders}) ORDER BY f.updated_at"
        )
        rows = cx.execute(q, (session_id, *category_ids)).fetchall()
    else:
        rows = cx.execute(
            "SELECT id, note_id, stored_rel, original_name, size_bytes FROM kb_note_files "
            "WHERE session_id=? ORDER BY updated_at",
            (session_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def note_get(data_dir: Path, session_id: str, note_id: str) -> Optional[dict[str, Any]]:
    cx = _conn(data_dir)
    row = cx.execute(
        "SELECT id, category_id, session_id, owner_id, title, body_markdown, created_at, updated_at "
        "FROM kb_notes WHERE session_id=? AND id=?",
        (session_id, note_id),
    ).fetchone()
    return dict(row) if row else None


def note_insert(
    data_dir: Path,
    session_id: str,
    category_id: str,
    title: str,
    body_markdown: str,
    *,
    owner_id: Optional[str] = None,
) -> dict[str, Any]:
    if not category_get(data_dir, session_id, category_id):
        raise ValueError("category_not_found")
    nid = uuid.uuid4().hex
    now = time.time()
    cx = _conn(data_dir)
    cx.execute(
        "INSERT INTO kb_notes (id, category_id, session_id, owner_id, title, body_markdown, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            nid,
            category_id,
            session_id,
            owner_id,
            title.strip() or "无标题",
            body_markdown or "",
            now,
            now,
        ),
    )
    cx.commit()
    row = note_get(data_dir, session_id, nid)
    assert row
    return row


def note_update(
    data_dir: Path,
    session_id: str,
    note_id: str,
    *,
    title: Optional[str] = None,
    body_markdown: Optional[str] = None,
    category_id: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    if not note_get(data_dir, session_id, note_id):
        return None
    if category_id is not None and not category_get(data_dir, session_id, category_id):
        raise ValueError("category_not_found")
    now = time.time()
    fields = []
    vals: list[Any] = []
    if title is not None:
        fields.append("title=?")
        vals.append(title.strip() or "无标题")
    if body_markdown is not None:
        fields.append("body_markdown=?")
        vals.append(body_markdown)
    if category_id is not None:
        fields.append("category_id=?")
        vals.append(category_id)
    if not fields:
        return note_get(data_dir, session_id, note_id)
    fields.append("updated_at=?")
    vals.append(now)
    vals.extend([session_id, note_id])
    cx = _conn(data_dir)
    cx.execute(f"UPDATE kb_notes SET {', '.join(fields)} WHERE session_id=? AND id=?", vals)
    cx.commit()
    return note_get(data_dir, session_id, note_id)


def note_delete(data_dir: Path, session_id: str, note_id: str) -> bool:
    cx = _conn(data_dir)
    cur = cx.execute("DELETE FROM kb_notes WHERE session_id=? AND id=?", (session_id, note_id))
    cx.commit()
    return cur.rowcount > 0


# ---------- note files ----------


def note_files_list(data_dir: Path, session_id: str, note_id: str) -> list[dict[str, Any]]:
    cx = _conn(data_dir)
    rows = cx.execute(
        "SELECT id, note_id, session_id, original_name, stored_rel, size_bytes, mime, created_at, updated_at "
        "FROM kb_note_files WHERE session_id=? AND note_id=? ORDER BY created_at ASC",
        (session_id, note_id),
    ).fetchall()
    return [dict(r) for r in rows]


def note_file_insert(
    data_dir: Path,
    session_id: str,
    note_id: str,
    original_name: str,
    stored_rel: str,
    size_bytes: int,
    mime: Optional[str],
) -> dict[str, Any]:
    if not note_get(data_dir, session_id, note_id):
        raise ValueError("note_not_found")
    fid = uuid.uuid4().hex
    now = time.time()
    cx = _conn(data_dir)
    cx.execute(
        "INSERT INTO kb_note_files (id, note_id, session_id, original_name, stored_rel, size_bytes, mime, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (fid, note_id, session_id, original_name, stored_rel, int(size_bytes), mime, now, now),
    )
    cx.commit()
    row = cx.execute(
        "SELECT id, note_id, session_id, original_name, stored_rel, size_bytes, mime, created_at, updated_at "
        "FROM kb_note_files WHERE id=?",
        (fid,),
    ).fetchone()
    return dict(row) if row else {"id": fid}


def note_file_get(data_dir: Path, session_id: str, file_id: str) -> Optional[dict[str, Any]]:
    cx = _conn(data_dir)
    row = cx.execute(
        "SELECT id, note_id, session_id, original_name, stored_rel, size_bytes, mime, created_at, updated_at "
        "FROM kb_note_files WHERE session_id=? AND id=?",
        (session_id, file_id),
    ).fetchone()
    return dict(row) if row else None


def note_file_delete(data_dir: Path, session_id: str, file_id: str) -> bool:
    cx = _conn(data_dir)
    cur = cx.execute("DELETE FROM kb_note_files WHERE session_id=? AND id=?", (session_id, file_id))
    cx.commit()
    return cur.rowcount > 0


def parse_category_ids_json(raw: Optional[str]) -> Optional[list[str]]:
    if not raw or not str(raw).strip():
        return None
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [str(x) for x in data if str(x).strip()]
    except json.JSONDecodeError:
        return None
    return None
