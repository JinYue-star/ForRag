#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Smoke test for class exercises loop."""
from __future__ import annotations

from rag_api import settings
from rag_api.exercise_service import (
    bank_to_csv,
    create_exercise_from_items,
    delete_exercise,
    extract_questions_from_note_body,
    generate_and_optionally_publish,
    parse_bank_bytes,
    save_questions_to_kb,
    template_csv_bytes,
)
import exercise_store
import kb_store
import chroma_store


def main() -> None:
    raw = template_csv_bytes()
    items, errs = parse_bank_bytes(raw, "t.csv")
    print("parse template", len(items), errs)
    assert not errs and len(items) == 3, (items, errs)

    row = create_exercise_from_items(
        items, title="Smoke Test Quiz", created_by="test", status="published"
    )
    print("exercise", row["id"], row["quiz_id"], row["item_count"])
    got = chroma_store.quiz_get(row["quiz_id"])
    assert got and got[0] is None
    assert len(got[1]["items"]) == 3

    rows = exercise_store.exercises_list(settings.DATA_DIR, settings.KB_ID, published_only=True)
    assert any(r["id"] == row["id"] for r in rows)

    out = save_questions_to_kb(
        [
            {
                "time": "2026-01-01",
                "student_name": "A",
                "username": "a",
                "session_id": "s",
                "question": "What is TCP?",
            },
            {
                "time": "2026-01-01",
                "student_name": "B",
                "username": "b",
                "session_id": "s2",
                "question": "Explain congestion control",
            },
        ],
        fmt="csv",
        owner_id="test",
    )
    print("save-kb", out)
    note = kb_store.note_get(settings.DATA_DIR, settings.KB_ID, out["note_id"])
    assert note
    qs = extract_questions_from_note_body(note["body_markdown"])
    print("extracted", qs)
    assert len(qs) >= 2

    gen = generate_and_optionally_publish(
        out["note_id"], n=3, title="AI Smoke", publish=True, created_by="test"
    )
    print("ai gen", gen["item_count"], bool(gen.get("exercise")))
    assert gen["item_count"] == 3 and gen.get("exercise")

    csv_bytes = bank_to_csv(gen["items"])
    items2, errs2 = parse_bank_bytes(csv_bytes, "bank.csv")
    assert not errs2 and len(items2) == 3, errs2

    # grade path: save composite answer
    chroma_store.quiz_answer_save(
        gen["exercise"]["quiz_id"],
        None,
        {"answers": ["True", "x", "0"], "grade": {"items": []}},
        1.0,
        user_id="u1",
    )
    chroma_store.quiz_answer_save(
        gen["exercise"]["quiz_id"],
        None,
        {"answers": ["False", "y", "1"], "grade": {"items": []}},
        2.0,
        user_id="u2",
    )
    subs = [s for s in chroma_store.quiz_answers_list() if s["quiz_id"] == gen["exercise"]["quiz_id"]]
    print("submissions", len(subs))
    assert len(subs) >= 2

    delete_exercise(row["id"])
    delete_exercise(gen["exercise"]["id"])
    print("OK")


if __name__ == "__main__":
    main()
