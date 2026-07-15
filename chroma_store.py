#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ChromaDB 持久化：会话、上传文件元数据、聊天消息、测验批次。"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any, Callable, Optional, TypeVar

import chromadb

_chroma_client: Optional[chromadb.ClientAPI] = None
_chroma_path: Optional[Path] = None
_base_dir: Optional[Path] = None

T = TypeVar("T")


def init_chroma(base_dir: Path) -> None:
    global _chroma_client, _chroma_path, _base_dir
    _base_dir = base_dir.resolve()
    _chroma_path = _base_dir / "chroma"
    _chroma_path.mkdir(parents=True, exist_ok=True)
    _chroma_client = chromadb.PersistentClient(path=str(_chroma_path))


def reset_chroma(base_dir: Path) -> None:
    """清空 Chroma 数据并重新初始化（仅测试或维护用）。"""
    global _chroma_client, _chroma_path
    _chroma_client = None
    _chroma_path = None
    p = base_dir / "chroma"
    if p.exists():
        shutil.rmtree(p, ignore_errors=True)
    init_chroma(base_dir)


def _is_chroma_storage_corruption(exc: BaseException) -> bool:
    s = str(exc).lower()
    return "hnsw" in s or "segment reader" in s


def _chroma_op(fn: Callable[[], T]) -> T:
    """HNSW 索引损坏等持久化错误时清空 chroma/ 并重试一次。"""
    try:
        return fn()
    except Exception as e:
        if _base_dir is None or not _is_chroma_storage_corruption(e):
            raise
        logging.warning(
            "Chroma 持久化异常（%s），将删除 %s 并重建后重试一次。",
            e,
            _base_dir / "chroma",
        )
        reset_chroma(_base_dir)
        return fn()


def _client() -> chromadb.ClientAPI:
    if _chroma_client is None:
        raise RuntimeError("chroma not initialized")
    return _chroma_client


def _col(name: str):
    return _client().get_or_create_collection(name, metadata={"hnsw:space": "cosine"})


# ---------- sessions ----------
def session_insert(
    sid: str,
    secret_hash: str,
    created_at: float,
    last_seen: float,
    owner: Optional[dict[str, Any]] = None,
) -> None:
    def op() -> None:
        c = _col("sessions")
        payload: dict[str, Any] = {
            "secret_hash": secret_hash,
            "created_at": created_at,
            "last_seen": last_seen,
        }
        if owner:
            # 记录发起会话的用户身份，供教师导出归因（不含任何密钥）
            payload["owner"] = {
                "user_id": str(owner.get("user_id") or owner.get("id") or ""),
                "username": str(owner.get("username") or ""),
                "display_name": str(owner.get("display_name") or ""),
                "student_no": str(owner.get("student_no") or ""),
                "role": str(owner.get("role") or ""),
            }
        doc = json.dumps(payload, ensure_ascii=False)
        c.upsert(ids=[sid], documents=[doc], metadatas=[{"kind": "session"}])

    _chroma_op(op)


def session_get(sid: str) -> Optional[dict]:
    def op() -> Optional[dict]:
        c = _col("sessions")
        r = c.get(ids=[sid])
        if not r["ids"]:
            return None
        return json.loads(r["documents"][0])

    return _chroma_op(op)


def session_update_last_seen(sid: str, last_seen: float) -> None:
    row = session_get(sid)
    if not row:
        return
    row["last_seen"] = last_seen

    def op() -> None:
        c = _col("sessions")
        c.update(
            ids=[sid],
            documents=[json.dumps(row, ensure_ascii=False)],
            metadatas=[{"kind": "session"}],
        )

    _chroma_op(op)


def session_delete_record(sid: str) -> None:
    """从 sessions 集合中移除会话记录。"""

    def op() -> None:
        c = _col("sessions")
        c.delete(ids=[sid])

    _chroma_op(op)


# ---------- session files ----------
def file_insert(
    fid: str,
    session_id: str,
    original_name: str,
    stored_rel: str,
    size_bytes: int,
    created_at: float,
) -> None:
    def op() -> None:
        c = _col("session_files")
        doc = json.dumps(
            {
                "session_id": session_id,
                "original_name": original_name,
                "stored_rel": stored_rel,
                "size_bytes": size_bytes,
                "created_at": created_at,
            },
            ensure_ascii=False,
        )
        c.upsert(
            ids=[fid],
            documents=[doc],
            metadatas=[{"session_id": session_id, "created_at": created_at}],
        )

    _chroma_op(op)


def file_list(session_id: str) -> list[dict]:
    def op() -> list[dict]:
        c = _col("session_files")
        r = c.get(where={"session_id": session_id}, include=["documents", "metadatas"])
        out: list[dict] = []
        for i, doc_id in enumerate(r["ids"]):
            d = json.loads(r["documents"][i])
            d["id"] = doc_id
            out.append(d)
        out.sort(key=lambda x: x.get("created_at", 0))
        return out

    return _chroma_op(op)


def file_get(session_id: str, fid: str) -> Optional[dict]:
    def op() -> Optional[dict]:
        c = _col("session_files")
        r = c.get(ids=[fid])
        if not r["ids"]:
            return None
        d = json.loads(r["documents"][0])
        if d.get("session_id") != session_id:
            return None
        d["id"] = fid
        return d

    return _chroma_op(op)


def file_delete(fid: str) -> None:
    try:

        def op() -> None:
            c = _col("session_files")
            c.delete(ids=[fid])

        _chroma_op(op)
    except Exception:
        pass


# ---------- chat messages ----------
def message_add(
    mid: str,
    session_id: str,
    role: str,
    content: str,
    created_at: float,
    extra: Optional[dict[str, Any]] = None,
) -> None:
    def op() -> None:
        c = _col("messages")
        payload = {"role": role, "content": content, "created_at": created_at, "session_id": session_id}
        if extra:
            payload["extra"] = extra
        doc = json.dumps(payload, ensure_ascii=False)
        c.upsert(
            ids=[mid],
            documents=[doc],
            metadatas=[{"session_id": session_id, "role": role, "created_at": created_at}],
        )

    _chroma_op(op)


def messages_list(session_id: str) -> list[dict]:
    def op() -> list[dict]:
        c = _col("messages")
        r = c.get(where={"session_id": session_id}, include=["documents", "metadatas"])
        out: list[dict] = []
        for i, mid in enumerate(r["ids"]):
            d = json.loads(r["documents"][i])
            d["id"] = mid
            out.append(d)
        out.sort(key=lambda x: x.get("created_at", 0))
        return out

    return _chroma_op(op)


def message_get(session_id: str, mid: str) -> Optional[dict]:
    def op() -> Optional[dict]:
        c = _col("messages")
        r = c.get(ids=[mid])
        if not r["ids"]:
            return None
        d = json.loads(r["documents"][0])
        if d.get("session_id") != session_id:
            return None
        d["id"] = mid
        return d

    return _chroma_op(op)


def message_delete(session_id: str, mid: str) -> bool:
    if not message_get(session_id, mid):
        return False
    try:

        def op() -> None:
            c = _col("messages")
            c.delete(ids=[mid])

        _chroma_op(op)
        return True
    except Exception:
        return False


def messages_delete_all(session_id: str) -> int:
    def op() -> int:
        c = _col("messages")
        r = c.get(where={"session_id": session_id}, include=[])
        ids = list(r.get("ids") or [])
        if not ids:
            return 0
        c.delete(ids=ids)
        return len(ids)

    try:
        return _chroma_op(op)
    except Exception:
        return 0


# ---------- quizzes ----------
def quiz_insert(quiz_id: str, session_id: Optional[str], payload: dict, created_at: float) -> None:
    def op() -> None:
        c = _col("quiz_batches")
        doc = json.dumps(payload, ensure_ascii=False)
        meta: dict[str, Any] = {"created_at": created_at}
        if session_id:
            meta["session_id"] = session_id
        c.upsert(ids=[quiz_id], documents=[doc], metadatas=[meta])

    _chroma_op(op)


def quiz_get(quiz_id: str) -> Optional[tuple[Optional[str], dict]]:
    def op() -> Optional[tuple[Optional[str], dict]]:
        c = _col("quiz_batches")
        r = c.get(ids=[quiz_id])
        if not r["ids"]:
            return None
        payload = json.loads(r["documents"][0])
        meta = r["metadatas"][0] if r["metadatas"] else {}
        sid = meta.get("session_id")
        return sid, payload

    return _chroma_op(op)


def quiz_list_question_texts(session_id: str) -> list[str]:
    """已生成过的题目题干，用于去重。"""

    def op() -> list[str]:
        c = _col("quiz_batches")
        r = c.get(where={"session_id": session_id}, include=["documents"])
        texts: list[str] = []
        for doc in r["documents"]:
            try:
                p = json.loads(doc)
                for it in p.get("items") or []:
                    q = str(it.get("question", "")).strip()
                    if q:
                        texts.append(q)
            except (json.JSONDecodeError, TypeError):
                continue
        return texts

    return _chroma_op(op)


def quiz_delete_all_for_session(session_id: str) -> int:
    """删除某会话关联的全部测验批次元数据。"""

    def op() -> int:
        c = _col("quiz_batches")
        r = c.get(where={"session_id": session_id}, include=[])
        ids = list(r.get("ids") or [])
        if not ids:
            return 0
        c.delete(ids=ids)
        return len(ids)

    try:
        return _chroma_op(op)
    except Exception:
        return 0


# ---------- enumeration & exports (teacher) ----------
def sessions_list_all() -> list[dict]:
    """列出全部会话记录（含 owner 归因信息），供教师导出。"""

    def op() -> list[dict]:
        c = _col("sessions")
        r = c.get(include=["documents"])
        out: list[dict] = []
        for i, sid in enumerate(r.get("ids") or []):
            try:
                d = json.loads(r["documents"][i])
            except (json.JSONDecodeError, TypeError):
                d = {}
            d["session_id"] = sid
            d.pop("secret_hash", None)  # 绝不导出密钥
            out.append(d)
        return out

    return _chroma_op(op)


def quiz_list_all() -> list[dict]:
    """列出全部测验批次（含 session_id 与生成时间），供教师导出。"""

    def op() -> list[dict]:
        c = _col("quiz_batches")
        r = c.get(include=["documents", "metadatas"])
        out: list[dict] = []
        for i, qid in enumerate(r.get("ids") or []):
            try:
                payload = json.loads(r["documents"][i])
            except (json.JSONDecodeError, TypeError):
                payload = {}
            meta = r["metadatas"][i] if r.get("metadatas") else {}
            out.append(
                {
                    "quiz_id": qid,
                    "session_id": (meta or {}).get("session_id"),
                    "created_at": float((meta or {}).get("created_at") or 0),
                    "payload": payload,
                }
            )
        return out

    return _chroma_op(op)


def quiz_delete(quiz_id: str) -> bool:
    """删除单个测验批次。"""

    def op() -> bool:
        c = _col("quiz_batches")
        r = c.get(ids=[quiz_id])
        if not r.get("ids"):
            return False
        c.delete(ids=[quiz_id])
        return True

    try:
        return bool(_chroma_op(op))
    except Exception:
        return False


def quiz_answer_save(
    quiz_id: str,
    session_id: Optional[str],
    payload: dict,
    created_at: float,
    *,
    user_id: Optional[str] = None,
) -> None:
    """保存学生对某次测验的作答与判分结果（供教师导出）。

    有 user_id 时用 ``{quiz_id}__{user_id}`` 作主键，支持同一套课堂题多人提交；
    无 user_id 时仍以 quiz_id 为主键（兼容会话生成测验）。
    """

    def op() -> None:
        c = _col("quiz_answers")
        uid = (user_id or "").strip()
        record_id = f"{quiz_id}__{uid}" if uid else quiz_id
        doc = json.dumps(payload, ensure_ascii=False)
        meta: dict[str, Any] = {"created_at": created_at, "quiz_id": quiz_id}
        if session_id:
            meta["session_id"] = session_id
        if uid:
            meta["user_id"] = uid
        c.upsert(ids=[record_id], documents=[doc], metadatas=[meta])

    _chroma_op(op)


def quiz_answers_delete_for_quiz(quiz_id: str) -> int:
    """删除某 quiz_id 下全部作答记录（含复合键）。"""

    def op() -> int:
        c = _col("quiz_answers")
        r = c.get(include=["metadatas"])
        ids: list[str] = []
        for i, rid in enumerate(r.get("ids") or []):
            meta = (r.get("metadatas") or [None])[i] or {}
            qid = str(meta.get("quiz_id") or "")
            if qid == quiz_id or rid == quiz_id or str(rid).startswith(f"{quiz_id}__"):
                ids.append(rid)
        if not ids:
            return 0
        c.delete(ids=ids)
        return len(ids)

    try:
        return int(_chroma_op(op) or 0)
    except Exception:
        return 0


def quiz_answers_map() -> dict[str, dict]:
    """兼容旧导出：quiz_id -> 最近一次作答（同 quiz 多人时取最新）。"""

    submissions = quiz_answers_list()
    out: dict[str, dict] = {}
    for sub in submissions:
        qid = str(sub.get("quiz_id") or "")
        if not qid:
            continue
        prev = out.get(qid)
        if not prev or float(sub.get("created_at") or 0) >= float(prev.get("created_at") or 0):
            out[qid] = sub
    return out


def quiz_answers_list() -> list[dict]:
    """全部作答记录列表（含 quiz_id / user_id / session_id）。"""

    def op() -> list[dict]:
        c = _col("quiz_answers")
        r = c.get(include=["documents", "metadatas"])
        out: list[dict] = []
        for i, rid in enumerate(r.get("ids") or []):
            try:
                payload = json.loads(r["documents"][i])
            except (json.JSONDecodeError, TypeError):
                payload = {}
            meta = r["metadatas"][i] if r.get("metadatas") else {}
            meta = meta or {}
            qid = str(meta.get("quiz_id") or "")
            if not qid:
                # legacy rows used quiz_id as document id
                qid = str(rid).split("__", 1)[0] if "__" in str(rid) else str(rid)
            row = dict(payload)
            row["record_id"] = rid
            row["quiz_id"] = qid
            row["user_id"] = str(meta.get("user_id") or "")
            row["session_id"] = meta.get("session_id")
            row["created_at"] = float(meta.get("created_at") or 0)
            out.append(row)
        return out

    return _chroma_op(op)
