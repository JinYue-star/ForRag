#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全局知识库笔记在磁盘上的路径与清理（单用户本地版）。"""

from __future__ import annotations

import traceback
from pathlib import Path

import kb_store

from doc_qa_assistant import invalidate_caches_for_file

from rag_api import settings


def kb_note_md_path(kb_id: str, note_id: str) -> Path:
    # kb_id 目前固定为 settings.KB_ID；保留参数便于未来多工作区扩展
    root = settings.kb_notes_dir()
    return (root / f"{note_id}.md").resolve()


def sync_kb_note_body_file(kb_id: str, note_id: str, body: str) -> None:
    path = kb_note_md_path(kb_id, note_id)
    path.write_text(body or "", encoding="utf-8")


def delete_kb_note_body_file(kb_id: str, note_id: str) -> None:
    p = kb_note_md_path(kb_id, note_id)
    if p.is_file():
        try:
            p.unlink()
        except OSError:
            traceback.print_exc()


def purge_kb_note_from_disk(kb_id: str, note_id: str) -> None:
    for row in kb_store.note_files_list(settings.DATA_DIR, kb_id, note_id):
        abs_path = (settings.UPLOAD_DIR / row["stored_rel"]).resolve()
        if abs_path.is_file():
            try:
                invalidate_caches_for_file(abs_path, settings.SERVER_EMBED_MODEL)
            except Exception:
                traceback.print_exc()
            try:
                abs_path.unlink()
            except OSError:
                traceback.print_exc()
    delete_kb_note_body_file(kb_id, note_id)
