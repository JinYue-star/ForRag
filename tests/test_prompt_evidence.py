"""提示词证据渲染：命中子块时展开父级页，同一父级不重复展开。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from rag_api import qa_llm, settings


def _hit(score: float, *, text: str, parent_id: str = "", parent_text: str = "", page: str = "第1页"):
    return (
        score,
        SimpleNamespace(
            source="lecture.pdf",
            page_label=page,
            meta="PDF",
            text=text,
            parent_id=parent_id,
            parent_text=parent_text,
        ),
    )


def test_prompt_expands_parent_unit_for_hit_chunk() -> None:
    hits = [_hit(0.8, text="子块正文", parent_id="p1", parent_text="整页正文，包含子块正文与上下文。")]
    prompt = qa_llm.build_strategy_prompt("问题", hits)
    assert "整页正文，包含子块正文与上下文。" in prompt


def test_same_parent_is_expanded_only_once() -> None:
    parent = "整页正文" * 20
    hits = [
        _hit(0.8, text="子块甲", parent_id="p1", parent_text=parent),
        _hit(0.7, text="子块乙", parent_id="p1", parent_text=parent),
    ]
    prompt = qa_llm.build_strategy_prompt("问题", hits)
    assert prompt.count(parent) == 1
    assert "与 [1] 同一位置" in prompt
    assert "子块乙" in prompt


def test_chunk_without_parent_falls_back_to_own_text() -> None:
    hits = [_hit(0.8, text="没有父级的块")]
    prompt = qa_llm.build_strategy_prompt("问题", hits)
    assert "没有父级的块" in prompt


def test_prompt_chunk_limit_fits_a_full_chunk_and_parent(monkeypatch: pytest.MonkeyPatch) -> None:
    import doc_qa_assistant as dqa

    # 提示词上限必须至少容纳一个父级单元，否则加大块长会在这里被截断。
    assert settings.PROMPT_CHUNK_CHAR_LIMIT >= dqa.PARENT_MAX_CHARS

    parent = "字" * (dqa.PARENT_MAX_CHARS)
    hits = [_hit(0.8, text="子块", parent_id="p1", parent_text=parent)]
    prompt = qa_llm.build_strategy_prompt("问题", hits)
    assert "..." not in prompt.split("内容: ")[1][: dqa.PARENT_MAX_CHARS]
