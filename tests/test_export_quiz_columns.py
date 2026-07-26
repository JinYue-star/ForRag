"""Teacher quiz export: slim columns + import-aligned option/answer encoding."""

from __future__ import annotations

from rag_api.export_service import (
    QUIZ_COLUMNS,
    _correct_answer_from_item,
    _options_cell,
    _student_answer_like_import,
)


def test_quiz_columns_match_teacher_schema():
    assert QUIZ_COLUMNS == [
        "type",
        "question",
        "options",
        "correct_answer",
        "student_answer",
        "is_correct",
    ]


def test_options_and_correct_match_import_encoding():
    single = {
        "type": "single",
        "question": "Which layer?",
        "options": ["Application", "Transport", "Network", "Link"],
        "correct_index": 2,
    }
    assert _options_cell(single) == "Application|Transport|Network|Link"
    assert _correct_answer_from_item(single) == "C"
    assert _student_answer_like_import(single, "Network") == "C"

    tf = {
        "type": "tf",
        "question": "HTTP is transport?",
        "options": ["True", "False"],
        "correct_index": 1,
    }
    assert _options_cell(tf) == "True|False"
    assert _correct_answer_from_item(tf) == "False"
    assert _student_answer_like_import(tf, "True") == "True"

    multi = {
        "type": "multi",
        "question": "Reliable features?",
        "options": ["ACK", "Best-effort", "Retransmission", "Unordered"],
        "correct_indices": [0, 2],
    }
    assert _correct_answer_from_item(multi) == "A|C"
    assert _student_answer_like_import(multi, "0,2") == "A|C"
