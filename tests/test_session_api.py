from __future__ import annotations

import secrets
import time
import uuid

import pytest
from fastapi.testclient import TestClient

import auth_store
import chroma_store

import fastapi_service
from fastapi_service import app
from rag_api import settings
from rag_api.http_common import hash_session_secret


@pytest.fixture(scope="module", autouse=True)
def reset_db() -> None:
    chroma_store.reset_chroma(fastapi_service._DATA_DIR)


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def auth_on(monkeypatch: pytest.MonkeyPatch):
    """启用登录鉴权。静态 RAG_ACCESS_TOKEN 门闩在登录鉴权开启时应被忽略。"""
    monkeypatch.setattr(settings, "REQUIRE_AUTH", True)
    # Even if a static token is configured, login Bearer must still work.
    monkeypatch.setattr(settings, "REQUIRE_ACCESS_TOKEN", True)
    monkeypatch.setattr(settings, "ACCESS_TOKEN", "pytest-token-secure")
    yield


def _make_student(username: str, password: str = "pass1234") -> dict:
    try:
        return auth_store.user_create(
            settings.DATA_DIR,
            username,
            password,
            role=auth_store.ROLE_STUDENT,
            display_name=username,
        )
    except ValueError:
        row = auth_store.user_get_by_username(settings.DATA_DIR, username)
        assert row is not None
        return auth_store.user_get(settings.DATA_DIR, str(row["id"]))  # type: ignore[return-value]


def _login(client: TestClient, username: str, password: str = "pass1234") -> str:
    r = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert r.status_code == 200, r.text
    return r.json()["token"]


def test_health(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_create_session_requires_token(client: TestClient) -> None:
    r = client.post("/api/v1/sessions")
    assert r.status_code == 401


def test_session_upload_list_delete(client: TestClient) -> None:
    headers = {"Authorization": "Bearer pytest-token-secure"}
    r = client.post("/api/v1/sessions", headers=headers)
    assert r.status_code == 200
    data = r.json()
    sid = data["session_id"]
    sec = data["session_secret"]

    h2 = {**headers, "X-Session-Secret": sec}
    r0 = client.get(f"/api/v1/sessions/{sid}/files", headers=h2)
    assert r0.status_code == 200
    assert r0.json() == []

    files = {"files": ("note.txt", b"hello cache test", "text/plain")}
    r1 = client.post(f"/api/v1/sessions/{sid}/files", headers=h2, files=files)
    assert r1.status_code == 200
    items = r1.json()
    assert len(items) == 1
    assert items[0]["original_name"] == "note.txt"
    fid = items[0]["id"]

    r2 = client.get(f"/api/v1/sessions/{sid}/files", headers=h2)
    assert r2.status_code == 200
    assert len(r2.json()) == 1

    r3 = client.delete(f"/api/v1/sessions/{sid}/files/{fid}", headers=h2)
    assert r3.status_code == 200

    r4 = client.get(f"/api/v1/sessions/{sid}/files", headers=h2)
    assert r4.status_code == 200
    assert r4.json() == []


def test_wrong_session_secret(client: TestClient) -> None:
    headers = {"Authorization": "Bearer pytest-token-secure"}
    r = client.post("/api/v1/sessions", headers=headers)
    sid = r.json()["session_id"]

    r2 = client.get(
        f"/api/v1/sessions/{sid}/files",
        headers={**headers, "X-Session-Secret": "wrong" * 4},
    )
    assert r2.status_code == 403


def test_session_messages_delete_one_and_clear_all(client: TestClient) -> None:
    headers = {"Authorization": "Bearer pytest-token-secure"}
    r = client.post("/api/v1/sessions", headers=headers)
    assert r.status_code == 200
    sid = r.json()["session_id"]
    sec = r.json()["session_secret"]
    h2 = {**headers, "X-Session-Secret": sec}

    mid1 = uuid.uuid4().hex
    mid2 = uuid.uuid4().hex
    chroma_store.message_add(mid1, sid, "user", "hello", time.time())
    chroma_store.message_add(mid2, sid, "assistant", "reply", time.time() + 0.01)

    r0 = client.get(f"/api/v1/sessions/{sid}/messages", headers=h2)
    assert r0.status_code == 200
    assert len(r0.json()) == 2

    r1 = client.delete(f"/api/v1/sessions/{sid}/messages/{mid1}", headers=h2)
    assert r1.status_code == 200
    assert r1.json()["status"] == "deleted"

    r2 = client.get(f"/api/v1/sessions/{sid}/messages", headers=h2)
    assert len(r2.json()) == 1

    r3 = client.delete(f"/api/v1/sessions/{sid}/messages", headers=h2)
    assert r3.status_code == 200
    assert r3.json()["status"] == "ok"
    assert r3.json()["deleted"] == 1

    r4 = client.get(f"/api/v1/sessions/{sid}/messages", headers=h2)
    assert r4.json() == []


def test_owner_can_access_own_session(client: TestClient, auth_on) -> None:
    _make_student("alice_owner")
    token = _login(client, "alice_owner")
    headers = {"Authorization": f"Bearer {token}"}

    r = client.post("/api/v1/sessions", headers=headers)
    assert r.status_code == 200
    sid = r.json()["session_id"]
    sec = r.json()["session_secret"]

    r2 = client.get(
        f"/api/v1/sessions/{sid}/files",
        headers={**headers, "X-Session-Secret": sec},
    )
    assert r2.status_code == 200
    assert r2.json() == []


def test_other_student_cannot_access_session(client: TestClient, auth_on) -> None:
    _make_student("alice_iso")
    _make_student("bob_iso")
    token_a = _login(client, "alice_iso")
    token_b = _login(client, "bob_iso")

    r = client.post("/api/v1/sessions", headers={"Authorization": f"Bearer {token_a}"})
    assert r.status_code == 200
    sid = r.json()["session_id"]
    sec = r.json()["session_secret"]

    r2 = client.get(
        f"/api/v1/sessions/{sid}/files",
        headers={"Authorization": f"Bearer {token_b}", "X-Session-Secret": sec},
    )
    assert r2.status_code == 403
    assert "无权访问" in (r2.json().get("detail") or "")


def test_orphan_session_claimed_by_first_visitor(client: TestClient, auth_on) -> None:
    """无 owner 的历史会话：首个访问者认领，之后他人 403。"""
    _make_student("claimer")
    _make_student("latecomer")
    token_a = _login(client, "claimer")
    token_b = _login(client, "latecomer")

    sid = str(uuid.uuid4())
    secret = secrets.token_hex(32)
    now = time.time()
    # 故意不写 owner，模拟改动前创建的会话
    chroma_store.session_insert(sid, hash_session_secret(secret), now, now, owner=None)
    row = chroma_store.session_get(sid)
    assert row is not None
    assert not (row.get("owner") or {}).get("user_id")

    r1 = client.get(
        f"/api/v1/sessions/{sid}/files",
        headers={"Authorization": f"Bearer {token_a}", "X-Session-Secret": secret},
    )
    assert r1.status_code == 200

    claimed = chroma_store.session_get(sid)
    assert claimed is not None
    assert (claimed.get("owner") or {}).get("user_id")

    r2 = client.get(
        f"/api/v1/sessions/{sid}/files",
        headers={"Authorization": f"Bearer {token_b}", "X-Session-Secret": secret},
    )
    assert r2.status_code == 403


def test_no_auth_mode_still_allows_secret_only(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """RAG_REQUIRE_AUTH=0 时仅验 secret，与改动前一致。"""
    monkeypatch.setattr(settings, "REQUIRE_AUTH", False)
    monkeypatch.setattr(settings, "REQUIRE_ACCESS_TOKEN", True)

    headers = {"Authorization": "Bearer pytest-token-secure"}
    r = client.post("/api/v1/sessions", headers=headers)
    assert r.status_code == 200
    sid = r.json()["session_id"]
    sec = r.json()["session_secret"]

    r2 = client.get(
        f"/api/v1/sessions/{sid}/files",
        headers={**headers, "X-Session-Secret": sec},
    )
    assert r2.status_code == 200


def test_login_token_not_blocked_by_static_access_token(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """登录鉴权开启时，静态 RAG_ACCESS_TOKEN 不得拒绝合法登录 Bearer。"""
    monkeypatch.setattr(settings, "REQUIRE_AUTH", True)
    monkeypatch.setattr(settings, "REQUIRE_ACCESS_TOKEN", True)
    monkeypatch.setattr(settings, "ACCESS_TOKEN", "machine-static-token-not-a-login")

    _make_student("login_vs_static")
    token = _login(client, "login_vs_static")
    r = client.post("/api/v1/sessions", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text

    # Static machine token alone is not a login user when auth is on.
    bad = client.post(
        "/api/v1/sessions",
        headers={"Authorization": "Bearer machine-static-token-not-a-login"},
    )
    assert bad.status_code == 401
