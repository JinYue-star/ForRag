#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用户与登录令牌存储（SQLite）：师生账号、密码哈希、登录令牌、注册码。

数据库文件：RAG_DATA_DIR 下 auth.sqlite。
密码哈希使用标准库 PBKDF2-HMAC-SHA256（无额外依赖，便于 Docker 部署）。
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

_conn_holder: dict[str, sqlite3.Connection] = {}
_lock = threading.Lock()

ROLE_TEACHER = "teacher"
ROLE_STUDENT = "student"
_PBKDF2_ROUNDS = 200_000


def _db_path(data_dir: Path) -> Path:
    return (data_dir / "auth.sqlite").resolve()


def init_auth_db(data_dir: Path) -> None:
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
            init_auth_db(data_dir)
            c = _conn_holder[key]
        return c


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            username_lower TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            display_name TEXT NOT NULL DEFAULT '',
            student_no TEXT NOT NULL DEFAULT '',
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);

        CREATE TABLE IF NOT EXISTS auth_tokens (
            token_hash TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_tokens_user ON auth_tokens(user_id);

        CREATE TABLE IF NOT EXISTS app_config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at REAL NOT NULL
        );
        """
    )
    conn.commit()


# ---------------- password hashing (PBKDF2) ----------------

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds_s, salt_hex, hash_hex = stored.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        rounds = int(rounds_s)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except (ValueError, AttributeError):
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
    return hmac.compare_digest(dk, expected)


# ---------------- users ----------------

def _row_to_user(row: Optional[sqlite3.Row]) -> Optional[dict[str, Any]]:
    if not row:
        return None
    d = dict(row)
    d.pop("password_hash", None)  # never expose the hash
    d["is_active"] = bool(d.get("is_active", 1))
    return d


def user_get_by_username(data_dir: Path, username: str) -> Optional[dict[str, Any]]:
    conn = _conn(data_dir)
    row = conn.execute(
        "SELECT * FROM users WHERE username_lower=?", (username.strip().lower(),)
    ).fetchone()
    return dict(row) if row else None  # includes password_hash for internal auth use


def user_get(data_dir: Path, user_id: str) -> Optional[dict[str, Any]]:
    conn = _conn(data_dir)
    row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return _row_to_user(row)


def user_create(
    data_dir: Path,
    username: str,
    password: str,
    role: str,
    display_name: str = "",
    student_no: str = "",
) -> dict[str, Any]:
    username = (username or "").strip()
    if not username:
        raise ValueError("username_required")
    if len(password or "") < 6:
        raise ValueError("password_too_short")
    if role not in (ROLE_TEACHER, ROLE_STUDENT):
        raise ValueError("invalid_role")
    conn = _conn(data_dir)
    now = time.time()
    uid = uuid.uuid4().hex
    try:
        conn.execute(
            "INSERT INTO users (id, username, username_lower, password_hash, role, "
            "display_name, student_no, is_active, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,1,?,?)",
            (
                uid,
                username,
                username.lower(),
                hash_password(password),
                role,
                (display_name or username).strip(),
                (student_no or "").strip(),
                now,
                now,
            ),
        )
        conn.commit()
    except sqlite3.IntegrityError as e:
        raise ValueError("username_taken") from e
    return user_get(data_dir, uid)  # type: ignore[return-value]


def user_list(data_dir: Path, role: Optional[str] = None) -> list[dict[str, Any]]:
    conn = _conn(data_dir)
    if role:
        rows = conn.execute(
            "SELECT * FROM users WHERE role=? ORDER BY created_at ASC", (role,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM users ORDER BY created_at ASC").fetchall()
    return [_row_to_user(r) for r in rows]  # type: ignore[misc]


def user_set_active(data_dir: Path, user_id: str, active: bool) -> bool:
    conn = _conn(data_dir)
    cur = conn.execute(
        "UPDATE users SET is_active=?, updated_at=? WHERE id=?",
        (1 if active else 0, time.time(), user_id),
    )
    conn.commit()
    return cur.rowcount > 0


def user_delete(data_dir: Path, user_id: str) -> bool:
    conn = _conn(data_dir)
    cur = conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()
    return cur.rowcount > 0


def user_count(data_dir: Path, role: Optional[str] = None) -> int:
    conn = _conn(data_dir)
    if role:
        row = conn.execute("SELECT COUNT(*) AS n FROM users WHERE role=?", (role,)).fetchone()
    else:
        row = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
    return int(row["n"]) if row else 0


def authenticate(data_dir: Path, username: str, password: str) -> Optional[dict[str, Any]]:
    row = user_get_by_username(data_dir, username)
    if not row or not bool(row.get("is_active", 1)):
        return None
    if not verify_password(password, str(row.get("password_hash") or "")):
        return None
    return user_get(data_dir, str(row["id"]))


# ---------------- login tokens ----------------

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_issue(data_dir: Path, user_id: str, ttl_seconds: int) -> tuple[str, float]:
    token = secrets.token_urlsafe(32)
    now = time.time()
    expires = now + max(60, int(ttl_seconds))
    conn = _conn(data_dir)
    conn.execute(
        "INSERT INTO auth_tokens (token_hash, user_id, created_at, expires_at) VALUES (?,?,?,?)",
        (_hash_token(token), user_id, now, expires),
    )
    conn.commit()
    return token, expires


def token_resolve(data_dir: Path, token: str) -> Optional[dict[str, Any]]:
    if not token:
        return None
    conn = _conn(data_dir)
    row = conn.execute(
        "SELECT user_id, expires_at FROM auth_tokens WHERE token_hash=?", (_hash_token(token),)
    ).fetchone()
    if not row:
        return None
    if float(row["expires_at"]) < time.time():
        conn.execute("DELETE FROM auth_tokens WHERE token_hash=?", (_hash_token(token),))
        conn.commit()
        return None
    return user_get(data_dir, str(row["user_id"]))


def token_revoke(data_dir: Path, token: str) -> None:
    conn = _conn(data_dir)
    conn.execute("DELETE FROM auth_tokens WHERE token_hash=?", (_hash_token(token),))
    conn.commit()


def token_purge_expired(data_dir: Path) -> int:
    conn = _conn(data_dir)
    cur = conn.execute("DELETE FROM auth_tokens WHERE expires_at < ?", (time.time(),))
    conn.commit()
    return cur.rowcount


# ---------------- app config (registration code) ----------------

def config_get(data_dir: Path, key: str) -> Optional[str]:
    conn = _conn(data_dir)
    row = conn.execute("SELECT value FROM app_config WHERE key=?", (key,)).fetchone()
    return str(row["value"]) if row else None


def config_set(data_dir: Path, key: str, value: str) -> None:
    conn = _conn(data_dir)
    conn.execute(
        "INSERT INTO app_config (key, value, updated_at) VALUES (?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, value, time.time()),
    )
    conn.commit()


REG_CODE_KEY = "student_reg_code"


def get_registration_code(data_dir: Path) -> Optional[str]:
    return config_get(data_dir, REG_CODE_KEY)


def set_registration_code(data_dir: Path, code: str) -> str:
    code = (code or "").strip()
    if not code:
        code = secrets.token_hex(4)
    config_set(data_dir, REG_CODE_KEY, code)
    return code


def ensure_registration_code(data_dir: Path, default: str = "") -> str:
    existing = get_registration_code(data_dir)
    if existing:
        return existing
    return set_registration_code(data_dir, default or secrets.token_hex(4))
