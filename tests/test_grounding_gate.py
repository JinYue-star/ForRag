from __future__ import annotations

from types import SimpleNamespace

import pytest

import rag_pipeline
from rag_api import qa_llm, settings
from tools import rag_eval


def _chunk(name: str = "lecture.pdf", page: str = "page 1") -> SimpleNamespace:
    return SimpleNamespace(
        source=name,
        page_label=page,
        meta="",
        text="Relevant course text.",
        chunk_id=f"{name}-{page}",
        kb_note_id="",
        kb_attachment_id="",
        session_file_id="",
    )


@pytest.fixture()
def strict_rerank_thresholds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rag_pipeline, "rerank_active", lambda: True)
    monkeypatch.setattr(rag_pipeline, "scores_from_rerank", lambda: True)
    monkeypatch.setattr(settings, "RERANK_MIN_SCORE", 0.05)
    monkeypatch.setattr(settings, "RERANK_SINGLE_HIT_MIN_SCORE", 0.12)
    monkeypatch.setattr(settings, "RERANK_STRONG_SCORE", 0.65)
    monkeypatch.setattr(settings, "RERANK_SINGLE_HIT_STRONG_SCORE", 0.75)
    monkeypatch.setattr(settings, "RERANK_SECOND_HIT_SCORE", 0.40)
    monkeypatch.setattr(settings, "RERANK_SUFFICIENCY_MARGIN", 0.10)
    monkeypatch.setattr(settings, "ENABLE_SUFFICIENCY_JUDGE", False)


def test_grounding_gate_uses_stricter_single_hit_threshold(
    strict_rerank_thresholds: None,
) -> None:
    two_hits = [(0.65, _chunk()), (0.40, _chunk(page="page 2"))]
    assert qa_llm.classify_grounding(two_hits) == "grounded"
    assert qa_llm.classify_grounding([(0.65, two_hits[0][1]), (0.39, two_hits[1][1])]) == "weak"
    assert qa_llm.classify_grounding([(0.75, two_hits[0][1]), (0.10, two_hits[1][1])]) == "grounded"
    assert qa_llm.classify_grounding([(0.64, two_hits[0][1]), two_hits[1]]) == "weak"
    assert qa_llm.classify_grounding([(0.04, two_hits[0][1]), two_hits[1]]) == "none"

    assert qa_llm.classify_grounding([(0.75, _chunk())]) == "grounded"
    assert qa_llm.classify_grounding([(0.74, _chunk())]) == "weak"
    assert qa_llm.classify_grounding([(0.11, _chunk())]) == "none"
    assert qa_llm.is_boundary_evidence([(0.64, _chunk()), (0.39, _chunk(page="page 2"))])
    assert not qa_llm.is_boundary_evidence([(0.40, _chunk()), (0.20, _chunk(page="page 2"))])


def test_cosine_fallback_uses_independent_strong_thresholds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rag_pipeline, "rerank_active", lambda: False)
    monkeypatch.setattr(rag_pipeline, "scores_from_rerank", lambda: False)
    monkeypatch.setattr(settings, "KB_MIN_SCORE", 0.28)
    monkeypatch.setattr(settings, "KB_SINGLE_HIT_MIN_SCORE", 0.40)
    monkeypatch.setattr(settings, "KB_STRONG_SCORE", 0.50)
    monkeypatch.setattr(settings, "KB_SINGLE_HIT_STRONG_SCORE", 0.60)
    monkeypatch.setattr(settings, "KB_SECOND_HIT_SCORE", 0.35)

    assert qa_llm.classify_grounding([(0.50, _chunk()), (0.35, _chunk(page="page 2"))]) == "grounded"
    assert qa_llm.classify_grounding([(0.50, _chunk()), (0.34, _chunk(page="page 2"))]) == "weak"
    assert qa_llm.classify_grounding([(0.59, _chunk())]) == "weak"
    assert qa_llm.classify_grounding([(0.60, _chunk())]) == "grounded"


def test_weak_evidence_routes_to_general_knowledge(
    monkeypatch: pytest.MonkeyPatch,
    strict_rerank_thresholds: None,
) -> None:
    monkeypatch.setattr(
        qa_llm,
        "generate_general_knowledge_answer",
        lambda question, max_new_tokens: ("General answer", "api"),
    )
    monkeypatch.setattr(
        qa_llm,
        "generate_strategy_answer",
        lambda *args, **kwargs: pytest.fail("weak evidence must not enter document RAG"),
    )

    response = qa_llm.run_qa_pipeline(
        "Question",
        [(0.40, _chunk()), (0.20, _chunk(page="page 2"))],
        None,
    )

    assert response.answer == "General answer"
    assert response.answer_kind == "general"
    assert response.grounding_label == "weak"
    assert response.kb_relevant is False
    assert response.service_unavailable is False
    assert "AI 通识回答" in (response.no_kb_notice or "")


def test_boundary_evidence_can_be_promoted_by_sufficiency_judge(
    monkeypatch: pytest.MonkeyPatch,
    strict_rerank_thresholds: None,
) -> None:
    monkeypatch.setattr(settings, "ENABLE_SUFFICIENCY_JUDGE", True)
    monkeypatch.setattr(
        qa_llm,
        "evaluate_evidence_sufficiency",
        lambda question, hits: (True, "api", "The excerpts answer all requested parts."),
    )
    monkeypatch.setattr(
        qa_llm,
        "generate_strategy_answer",
        lambda question, hits, max_new_tokens: ("Grounded answer [1].", "api"),
    )

    response = qa_llm.run_qa_pipeline(
        "Question",
        [(0.64, _chunk()), (0.39, _chunk(page="page 2"))],
        None,
    )

    assert response.answer_kind == "grounded"
    assert response.grounding_label == "weak_sufficient"
    assert response.kb_relevant is True
    assert response.sufficiency_checked is True
    assert response.sufficiency_sufficient is True


def test_grounded_api_failure_returns_no_pseudo_answer(
    monkeypatch: pytest.MonkeyPatch,
    strict_rerank_thresholds: None,
) -> None:
    monkeypatch.setattr(
        qa_llm,
        "generate_strategy_answer",
        lambda question, hits, max_new_tokens: ("", "api_error"),
    )

    response = qa_llm.run_qa_pipeline(
        "Question",
        [(0.80, _chunk()), (0.70, _chunk(page="page 2"))],
        None,
    )

    assert response.route == "api_unavailable"
    assert response.answer_kind == "unavailable"
    assert response.service_unavailable is True
    assert response.kb_relevant is True
    assert "无法生成经过证据约束的可靠答案" in response.answer
    assert "参考摘要" not in response.answer
    assert response.hits
    assert response.citations == []


def test_citation_coverage_counts_only_valid_sentence_level_refs() -> None:
    answer = (
        "Answer:\n"
        "The first claim is supported [1]. The second claim has no citation. "
        "An invalid reference does not count [9].\n"
        "- Evidence 1 (lecture.pdf, page 1): support [1]."
    )

    coverage, covered, total = qa_llm.citation_coverage(answer, evidence_count=2)

    assert coverage == pytest.approx(1 / 3)
    assert covered == 1
    assert total == 3


def test_evidence_gap_sentences_and_sources_are_excluded_from_coverage() -> None:
    answer = (
        "该方法在给定条件下成立[1]。\n"
        "1. 其适用范围限定于带限输入[2]。\n"
        "材料未说明该方法在其它条件下的表现。\n\n"
        "证据来源：\n[1] lecture.pdf · page 1\n[2] lecture.pdf · page 2"
    )

    coverage, covered, total = qa_llm.citation_coverage(answer, evidence_count=2)

    assert coverage == 1.0
    assert covered == 2
    assert total == 2


def test_semicolon_clauses_count_as_one_cited_sentence() -> None:
    answer = (
        "采样率必须严格大于最高频率的两倍；否则频谱副本重叠并发生混叠[1]。\n"
        "1. 该结论以带限信号为前提，且重建使用理想低通滤波器[2]。"
    )

    coverage, covered, total = qa_llm.citation_coverage(answer, evidence_count=2)

    assert coverage == 1.0
    assert covered == 2
    assert total == 2


def test_grounded_answer_appends_real_source_metadata(
    monkeypatch: pytest.MonkeyPatch,
    strict_rerank_thresholds: None,
) -> None:
    monkeypatch.setattr(
        qa_llm,
        "generate_strategy_answer",
        lambda question, hits, max_new_tokens: (
            "该结论由课程材料直接支持[1]。",
            "api",
        ),
    )

    response = qa_llm.run_qa_pipeline(
        "Question",
        [(0.80, _chunk()), (0.70, _chunk(name="slides.pptx", page="page 2"))],
        None,
    )

    assert response.answer_kind == "grounded"
    assert response.answer.endswith("证据来源：\n[1] lecture.pdf · page 1")
    assert "slides.pptx" not in response.answer
    assert [c.ref for c in response.citations] == [1]


def test_source_section_is_rebuilt_instead_of_duplicated() -> None:
    hits = [(0.8, _chunk()), (0.7, _chunk(name="slides.pptx", page="page 2"))]
    once = qa_llm.append_source_section("该结论成立[1]，其适用条件由另一处材料给出[2]。", hits)
    twice = qa_llm.append_source_section(once, hits)

    assert once == twice
    assert twice.count(qa_llm.SOURCE_SECTION_TITLE) == 1
    assert twice.endswith("[1] lecture.pdf · page 1\n[2] slides.pptx · page 2")


def test_low_citation_coverage_is_repaired_once(
    monkeypatch: pytest.MonkeyPatch,
    strict_rerank_thresholds: None,
) -> None:
    monkeypatch.setattr(settings, "MIN_CITATION_COVERAGE", 0.95)
    monkeypatch.setattr(
        qa_llm,
        "generate_strategy_answer",
        lambda question, hits, max_new_tokens: ("Answer:\nUnsupported factual claim.", "api"),
    )
    monkeypatch.setattr(
        qa_llm,
        "repair_citation_coverage",
        lambda question, hits, draft, max_new_tokens: (
            "Answer:\nSupported factual claim [1].\n- Evidence 1 (lecture.pdf, page 1): support [1].",
            "api",
        ),
    )

    response = qa_llm.run_qa_pipeline(
        "Question",
        [(0.80, _chunk()), (0.70, _chunk(page="page 2"))],
        None,
    )

    assert response.answer_kind == "grounded"
    assert response.route == "api_citation_repaired"
    assert response.citation_coverage == 1.0


def test_unrepaired_citation_gap_still_returns_grounded_answer(
    monkeypatch: pytest.MonkeyPatch,
    strict_rerank_thresholds: None,
) -> None:
    """引用覆盖不足时修订一次；仍不足则放行草稿+来源，不再拦截。"""
    monkeypatch.setattr(settings, "MIN_CITATION_COVERAGE", 0.95)
    monkeypatch.setattr(
        qa_llm,
        "generate_strategy_answer",
        lambda question, hits, max_new_tokens: ("Answer:\nUnsupported factual claim.", "api"),
    )
    monkeypatch.setattr(
        qa_llm,
        "repair_citation_coverage",
        lambda question, hits, draft, max_new_tokens: ("Still unsupported claim.", "api"),
    )

    response = qa_llm.run_qa_pipeline(
        "Question",
        [(0.80, _chunk()), (0.70, _chunk(page="page 2"))],
        None,
    )

    assert response.answer_kind == "grounded"
    assert "citation_partial" in response.route
    assert response.service_unavailable is False
    assert "Unsupported factual claim" in response.answer or "Still unsupported" in response.answer
    assert qa_llm.SOURCE_SECTION_TITLE in response.answer
    assert response.no_kb_notice and "引用标注" in response.no_kb_notice
    assert response.citation_coverage == 0.0


def test_insufficient_evidence_and_api_failure_is_explicit(
    monkeypatch: pytest.MonkeyPatch,
    strict_rerank_thresholds: None,
) -> None:
    monkeypatch.setattr(
        qa_llm,
        "generate_general_knowledge_answer",
        lambda question, max_new_tokens: ("", "api_error"),
    )

    response = qa_llm.run_qa_pipeline(
        "Question",
        [(0.20, _chunk()), (0.10, _chunk(page="page 2"))],
        None,
    )

    assert response.route == "api_unavailable"
    assert response.answer_kind == "unavailable"
    assert response.grounding_label == "weak"
    assert response.kb_relevant is False
    assert "课程资料不足以可靠回答" in response.answer
    assert "AI 服务当前不可用" in (response.no_kb_notice or "")


def test_calibration_prefers_high_precision_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rag_pipeline, "rerank_active", lambda: True)
    monkeypatch.setattr(rag_pipeline, "scores_from_rerank", lambda: True)
    rows = [
        {"expected_grounding": "grounded", "n_hits": 3, "top_score": 0.90, "second_score": 0.70},
        {"expected_grounding": "grounded", "n_hits": 3, "top_score": 0.72, "second_score": 0.55},
        {"expected_grounding": "general", "n_hits": 3, "top_score": 0.70, "second_score": 0.65},
        {"expected_grounding": "general", "n_hits": 3, "top_score": 0.20, "second_score": 0.10},
    ]

    report = rag_eval._grounding_calibration(rows, target_precision=0.95)

    assert report["score_mode"] == "rerank"
    assert report["multi_hit"]["threshold"] > 0.70
    assert report["multi_hit"]["second_threshold"] >= 0.0
    assert report["multi_hit"]["precision"] == 1.0
    assert report["multi_hit"]["recall"] == 1.0
