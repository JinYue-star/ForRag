#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HTTP-level smoke for class exercises APIs."""
from __future__ import annotations

from fastapi.testclient import TestClient

from rag_api.main import create_app
from rag_api.exercise_service import template_csv_bytes


def main() -> None:
    app = create_app()
    client = TestClient(app)

    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "123456"},
    )
    print("login", login.status_code)
    assert login.status_code == 200, login.text
    token = login.json().get("access_token") or login.json().get("token")
    assert token
    headers = {"Authorization": f"Bearer {token}"}

    r = client.get("/api/v1/kb/exercises/template.csv", headers=headers)
    print("template.csv", r.status_code)
    assert r.status_code == 200, r.text
    assert b"type" in r.content

    files = {"file": ("bank.csv", template_csv_bytes(), "text/csv")}
    data = {"title": "HTTP Smoke Quiz", "status": "published"}
    r = client.post("/api/v1/kb/exercises/import", data=data, files=files, headers=headers)
    print("import", r.status_code, r.text[:200])
    assert r.status_code == 200, r.text
    ex = r.json()
    qid = ex["quiz_id"]
    eid = ex["id"]

    r = client.get("/api/v1/kb/exercises", headers=headers)
    assert r.status_code == 200
    assert any(x["id"] == eid for x in r.json())

    r = client.get(f"/api/v1/quiz/{qid}", headers=headers)
    print("get quiz", r.status_code)
    assert r.status_code == 200
    bundle = r.json()
    assert len(bundle["items"]) == 3

    answers = []
    for it in bundle["items"]:
        if it["type"] in ("tf", "single"):
            answers.append((it.get("options") or ["True"])[0])
        else:
            answers.append("0,1")
    r = client.post(f"/api/v1/quiz/{qid}/grade", json={"answers": answers}, headers=headers)
    print("grade", r.status_code)
    assert r.status_code == 200, r.text

    r = client.post(
        "/api/v1/admin/export/save-to-kb",
        headers=headers,
        json={
            "include_questions": True,
            "include_answers": False,
            "include_quiz": False,
            "format": "csv",
        },
    )
    print("save-to-kb", r.status_code, (r.text or "")[:160])
    assert r.status_code in (200, 400)

    r = client.patch(
        f"/api/v1/kb/exercises/{eid}",
        json={"status": "unpublished"},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["status"] == "unpublished"

    r = client.delete(f"/api/v1/kb/exercises/{eid}", headers=headers)
    assert r.status_code == 200
    print("HTTP OK")


if __name__ == "__main__":
    main()
