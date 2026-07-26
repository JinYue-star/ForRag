from __future__ import annotations

import shutil

import pytest
from fastapi.testclient import TestClient

import fastapi_service
import kb_store
from rag_api import settings


@pytest.fixture(autouse=True)
def reset_global_kb() -> None:
    # 清空全局 KB（不影响会话文件与 Chroma；避免测试间互相污染）
    try:
        kb_store.kb_delete_all(fastapi_service._DATA_DIR, settings.UPLOAD_DIR, settings.KB_ID)
    except Exception:
        # 某些环境可能尚未初始化 kb.sqlite；忽略
        pass
    try:
        shutil.rmtree(settings.UPLOAD_DIR / "kb" / settings.KB_ID, ignore_errors=True)
    except Exception:
        pass


def _auth_headers(token: str, secret: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-Session-Secret": secret}


def test_global_kb_shared_across_sessions_and_not_deleted_with_session(client: TestClient) -> None:
    token = "pytest-token-secure"

    # session 1: create KB content
    r1 = client.post("/api/v1/sessions", headers={"Authorization": f"Bearer {token}"})
    assert r1.status_code == 200
    sid1 = r1.json()["session_id"]
    sec1 = r1.json()["session_secret"]
    h1 = _auth_headers(token, sec1)

    rc = client.post(
        f"/api/v1/sessions/{sid1}/kb/categories",
        headers=h1,
        json={"name": "cat-a", "sort_order": 0, "owner_id": None},
    )
    assert rc.status_code == 200
    cid = rc.json()["id"]

    rn = client.post(
        f"/api/v1/sessions/{sid1}/kb/categories/{cid}/notes",
        headers=h1,
        json={"title": "note-a", "body_markdown": "KB body: hello", "owner_id": None},
    )
    assert rn.status_code == 200
    nid = rn.json()["id"]

    files = {"file": ("kb-attach.txt", b"kb attach bytes", "text/plain")}
    rf = client.post(f"/api/v1/sessions/{sid1}/kb/notes/{nid}/files", headers=h1, files=files)
    assert rf.status_code == 200
    fid = rf.json()["id"]
    assert str(rf.json()["stored_rel"]).startswith(f"kb/{settings.KB_ID}/files/")

    # session 2: should see the same KB
    r2 = client.post("/api/v1/sessions", headers={"Authorization": f"Bearer {token}"})
    assert r2.status_code == 200
    sid2 = r2.json()["session_id"]
    sec2 = r2.json()["session_secret"]
    h2 = _auth_headers(token, sec2)

    rc2 = client.get(f"/api/v1/sessions/{sid2}/kb/categories", headers=h2)
    assert rc2.status_code == 200
    assert any(x["id"] == cid for x in rc2.json())

    rn2 = client.get(f"/api/v1/sessions/{sid2}/kb/categories/{cid}/notes", headers=h2)
    assert rn2.status_code == 200
    assert any(x["id"] == nid for x in rn2.json())

    rf2 = client.get(f"/api/v1/sessions/{sid2}/kb/notes/{nid}/files", headers=h2)
    assert rf2.status_code == 200
    assert any(x["id"] == fid for x in rf2.json())

    # delete session 2: must NOT delete global KB
    rd = client.delete(f"/api/v1/sessions/{sid2}", headers=h2)
    assert rd.status_code == 200
    assert rd.json()["status"] == "deleted"

    # session 3: KB still present
    r3 = client.post("/api/v1/sessions", headers={"Authorization": f"Bearer {token}"})
    assert r3.status_code == 200
    sid3 = r3.json()["session_id"]
    sec3 = r3.json()["session_secret"]
    h3 = _auth_headers(token, sec3)

    rc3 = client.get(f"/api/v1/sessions/{sid3}/kb/categories", headers=h3)
    assert rc3.status_code == 200
    assert any(x["id"] == cid for x in rc3.json())


def test_kb_attachment_download_and_delete_note(client: TestClient) -> None:
    token = "pytest-token-secure"
    r = client.post("/api/v1/sessions", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    sid = r.json()["session_id"]
    sec = r.json()["session_secret"]
    h = _auth_headers(token, sec)

    rc = client.post(
        f"/api/v1/sessions/{sid}/kb/categories",
        headers=h,
        json={"name": "cat-dl", "sort_order": 0},
    )
    assert rc.status_code == 200
    cid = rc.json()["id"]

    rn = client.post(
        f"/api/v1/sessions/{sid}/kb/categories/{cid}/notes",
        headers=h,
        json={"title": "note-dl", "body_markdown": ""},
    )
    assert rn.status_code == 200
    nid = rn.json()["id"]

    payload = b"%PDF-1.4 test-bytes-for-download"
    rf = client.post(
        f"/api/v1/sessions/{sid}/kb/notes/{nid}/files",
        headers=h,
        files={"file": ("sample.pdf", payload, "application/pdf")},
    )
    assert rf.status_code == 200
    fid = rf.json()["id"]

    rd = client.get(f"/api/v1/sessions/{sid}/kb/notes/{nid}/files/{fid}", headers=h)
    assert rd.status_code == 200
    assert rd.content == payload

    bad = client.get(
        f"/api/v1/sessions/{sid}/kb/notes/{nid}/files/{fid}",
        headers=_auth_headers(token, "wrong" * 8),
    )
    assert bad.status_code == 403

    # Wrong note id for this file → 404
    rn2 = client.post(
        f"/api/v1/sessions/{sid}/kb/categories/{cid}/notes",
        headers=h,
        json={"title": "other", "body_markdown": ""},
    )
    other_nid = rn2.json()["id"]
    cross = client.get(
        f"/api/v1/sessions/{sid}/kb/notes/{other_nid}/files/{fid}",
        headers=h,
    )
    assert cross.status_code == 404

    # Remove attachment (teacher) then re-upload for note-level delete check
    rm = client.delete(f"/api/v1/sessions/{sid}/kb/notes/{nid}/files/{fid}", headers=h)
    assert rm.status_code == 200
    assert rm.json()["status"] == "deleted"
    gone_file = client.get(f"/api/v1/sessions/{sid}/kb/notes/{nid}/files/{fid}", headers=h)
    assert gone_file.status_code == 404

    rf2 = client.post(
        f"/api/v1/sessions/{sid}/kb/notes/{nid}/files",
        headers=h,
        files={"file": ("sample2.pdf", payload, "application/pdf")},
    )
    assert rf2.status_code == 200
    fid2 = rf2.json()["id"]

    del_note = client.delete(f"/api/v1/sessions/{sid}/kb/notes/{nid}", headers=h)
    assert del_note.status_code == 200
    assert del_note.json()["status"] == "deleted"

    gone = client.get(f"/api/v1/sessions/{sid}/kb/notes/{nid}/files/{fid2}", headers=h)
    assert gone.status_code == 404

