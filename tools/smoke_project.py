#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Project-wide API smoke (auth / session / kb / exercises / export)."""
from __future__ import annotations

import json
import sys
from typing import Any

from fastapi.testclient import TestClient

from rag_api.main import create_app


def _ok(name: str, cond: bool, detail: str = "") -> dict[str, Any]:
    row = {"name": name, "pass": cond, "detail": detail[:200]}
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail[:120]}" if detail else ""))
    return row


def main() -> int:
    app = create_app()
    c = TestClient(app)
    results: list[dict[str, Any]] = []

    r = c.get("/health")
    results.append(_ok("health", r.status_code == 200, r.text[:80]))

    r = c.post("/api/v1/auth/login", json={"username": "admin", "password": "123456"})
    token = (r.json() or {}).get("token") if r.status_code == 200 else None
    results.append(_ok("auth.login", bool(token), f"status={r.status_code}"))
    if not token:
        print(json.dumps({"summary": results}, ensure_ascii=False, indent=2))
        return 1
    h = {"Authorization": f"Bearer {token}"}

    r = c.get("/api/v1/auth/me", headers=h)
    role = (r.json() or {}).get("role") if r.status_code == 200 else None
    results.append(_ok("auth.me", role == "teacher", f"role={role}"))

    r = c.post("/api/v1/sessions", headers=h)
    sid = (r.json() or {}).get("session_id") if r.status_code == 200 else None
    sec = (r.json() or {}).get("session_secret") if r.status_code == 200 else None
    results.append(_ok("session.create", bool(sid and sec), f"status={r.status_code}"))
    hs = {**h, "X-Session-Secret": sec or ""}

    if sid:
        r = c.get(f"/api/v1/sessions/{sid}/kb/categories", headers=hs)
        results.append(_ok("kb.categories", r.status_code == 200, f"n={len(r.json()) if r.status_code==200 else '-'}"))

        r = c.post(
            f"/api/v1/sessions/{sid}/files",
            headers=hs,
            files={"files": ("smoke.txt", b"TCP provides reliable transport.", "text/plain")},
        )
        results.append(_ok("session.upload", r.status_code == 200, f"status={r.status_code}"))

    r = c.get("/api/v1/kb/exercises", headers=h)
    results.append(_ok("exercises.list", r.status_code == 200, f"n={len(r.json()) if r.status_code==200 else '-'}"))

    r = c.get("/api/v1/kb/exercises/template.csv", headers=h)
    results.append(_ok("exercises.template", r.status_code == 200 and b"type" in r.content))

    r = c.post(
        "/api/v1/admin/export/preview",
        headers=h,
        json={"include_questions": True, "include_answers": False, "include_quiz": True, "format": "csv"},
    )
    results.append(_ok("export.preview", r.status_code == 200, f"keys={list((r.json() or {}).keys())}"))

    # Optional QA — may fail without usable LLM; record but don't hard-fail project if 502
    if sid:
        r = c.post(
            f"/api/v1/sessions/{sid}/qa",
            headers=hs,
            data={"question": "What is TCP?", "kb_scope": "session_files", "top_k": "3"},
        )
        qa_ok = r.status_code == 200 and bool((r.json() or {}).get("answer"))
        results.append(_ok("session.qa", qa_ok, f"status={r.status_code}"))

    passed = sum(1 for x in results if x["pass"])
    total = len(results)
    print(f"\nSUMMARY {passed}/{total} passed")
    out = {"passed": passed, "total": total, "results": results}
    Path = __import__("pathlib").Path
    Path("docs/project_smoke_report.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # Fail only on core auth/session/kb/exercises/export; QA optional
    core = [x for x in results if not x["name"].endswith(".qa")]
    return 0 if all(x["pass"] for x in core) else 1


if __name__ == "__main__":
    sys.exit(main())
