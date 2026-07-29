"""重排查询选择：中文原问优先用扩写后的英文查询。"""

from __future__ import annotations

import rag_pipeline as rp


def test_english_question_keeps_original() -> None:
    assert rp._pick_rerank_query("What is Nyquist?", ["aliasing", "sampling"]) == "What is Nyquist?"


def test_chinese_question_prefers_latin_expansion() -> None:
    q = "奈奎斯特采样定理的前提是什么？"
    assert rp._pick_rerank_query(q, [q, "Nyquist sampling theorem prerequisites"]) == (
        "Nyquist sampling theorem prerequisites"
    )


def test_chinese_without_latin_fallback() -> None:
    q = "采样定理是什么"
    assert rp._pick_rerank_query(q, [q, "采样率过低"]) == q


def test_english_only_reranker_detection(monkeypatch) -> None:
    # 判定只看模型标识，不依赖本机 .env 选了哪个重排器。
    monkeypatch.setattr(rp, "RERANK_MODEL", ".models/ms-marco-MiniLM-L-6-v2")
    assert rp._english_only_reranker() is True
    monkeypatch.setattr(rp, "RERANK_MODEL", "BAAI/bge-reranker-v2-m3")
    assert rp._english_only_reranker() is False


def test_scores_from_rerank_respects_last_flag(monkeypatch) -> None:
    monkeypatch.setattr(rp, "_rerank_runtime_ok", True)
    monkeypatch.setattr(rp, "RERANK_ENABLED", True)
    monkeypatch.setattr(rp, "_last_scores_from_rerank", True)
    assert rp.scores_from_rerank() is True
    monkeypatch.setattr(rp, "_last_scores_from_rerank", False)
    assert rp.scores_from_rerank() is False
