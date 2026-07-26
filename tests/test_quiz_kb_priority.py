"""Quiz generation: KB-first hit ordering and search-query construction."""

from __future__ import annotations

from types import SimpleNamespace

from rag_api.qa_llm import build_quiz_search_query, prefer_kb_hits


def test_prefer_kb_hits_puts_course_kb_first():
    session = (0.99, SimpleNamespace(kb_note_id="", kb_attachment_id="", session_file_id="sf1"))
    kb_low = (0.40, SimpleNamespace(kb_note_id="n1", kb_attachment_id="", session_file_id=""))
    kb_hi = (0.70, SimpleNamespace(kb_note_id="", kb_attachment_id="a1", session_file_id=""))
    other = (0.50, SimpleNamespace(kb_note_id="", kb_attachment_id="", session_file_id=""))

    ordered = prefer_kb_hits([session, kb_low, other, kb_hi], limit=3)
    origins = []
    for _sc, ch in ordered:
        if ch.kb_note_id or ch.kb_attachment_id:
            origins.append("kb")
        elif ch.session_file_id:
            origins.append("session")
        else:
            origins.append("other")
    assert origins == ["kb", "kb", "other"]
    assert ordered[0][1] is kb_low[1]
    assert ordered[1][1] is kb_hi[1]


def test_build_quiz_search_query_includes_preceding_user_question():
    segments = [("a1", "Assistant explains Shannon capacity.", 2)]
    messages = [
        {"id": "u1", "role": "user", "content": "What is channel capacity?", "created_at": 1.0},
        {"id": "a1", "role": "assistant", "content": "Assistant explains Shannon capacity.", "created_at": 2.0},
    ]
    q = build_quiz_search_query(segments, messages)
    assert "channel capacity" in q.lower()
    assert "shannon" in q.lower()
