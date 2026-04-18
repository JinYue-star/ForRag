from __future__ import annotations

import time
import uuid

import pytest
from fastapi.testclient import TestClient

import chroma_store

import fastapi_service
from fastapi_service import app


@pytest.fixture(scope="module", autouse=True)
def reset_db() -> None:
    chroma_store.reset_chroma(fastapi_service._DATA_DIR)


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


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
