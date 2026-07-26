"""分块行为测试：token 口径、段落边界、短块合并与去重、父子块。"""

from __future__ import annotations

import doc_qa_assistant as dqa


def test_normalize_block_text_keeps_paragraph_breaks() -> None:
    raw = "第一段  第一行\n第一段第二行\n\n\n第二段\t内容"
    out = dqa.normalize_block_text(raw)
    assert out == "第一段 第一行\n第一段第二行\n\n第二段 内容"


def test_chunking_splits_on_paragraph_boundary() -> None:
    para_a = "A" * 300
    para_b = "B" * 300
    chunks = dqa.chunk_by_chars(
        f"{para_a}\n\n{para_b}",
        source="t.txt",
        page_label="全文",
        meta="文本",
        max_chars=340,
        overlap=0,
    )
    # 段落边界可用时不应在 340 字符处硬切，第一块必须只含 A 段。
    assert chunks[0].text == para_a
    assert chunks[1].text.startswith("B")


def test_token_budget_gives_english_more_characters_than_chinese() -> None:
    english = "digital logic design lecture notes " * 20
    chinese = "数字逻辑设计课程讲义内容说明" * 20
    assert dqa.token_budget_to_chars(english, 480) > dqa.token_budget_to_chars(chinese, 480) * 3


def test_chunk_params_reads_single_config_source() -> None:
    assert dqa.chunk_params(".pdf") == (
        dqa.CHUNK_CONFIG["_default"]["max_tokens"],
        dqa.CHUNK_CONFIG["_default"]["overlap_ratio"],
    )
    tabular_tokens, tabular_overlap = dqa.chunk_params(".csv")
    assert tabular_tokens == dqa.CHUNK_CONFIG[".csv"]["max_tokens"]
    assert tabular_overlap == dqa.CHUNK_CONFIG["_default"]["overlap_ratio"]


def _chunk(text: str, label: str, meta: str = "PPT") -> dqa.TextChunk:
    return dqa.TextChunk(text=text, source="deck.pptx", page_label=label, meta=meta)


def test_short_units_merge_across_pages_with_honest_label() -> None:
    body = "内容" * 300  # 约 600 token，已超预算，不会被继续并入
    chunks = [
        _chunk("Cell Format", "第1张幻灯片"),
        _chunk("Frequency Reuse", "第2张幻灯片"),
        _chunk(body, "第3张幻灯片"),
    ]
    merged = dqa._merge_short_chunks(chunks, ".pptx")
    assert len(merged) == 2
    assert merged[0].page_label == "第1-2张幻灯片"
    assert "Cell Format" in merged[0].text and "Frequency Reuse" in merged[0].text
    assert merged[1].page_label == "第3张幻灯片"


def test_merge_respects_token_budget() -> None:
    long_body = "内容" * 400  # 约 800 token，已超默认 480 预算
    chunks = [_chunk("标题页", "第1页", meta="PDF"), _chunk(long_body, "第2页", meta="PDF")]
    merged = dqa._merge_short_chunks(chunks, ".pdf")
    assert len(merged) == 2, "合并会超出 token 预算时应保持原样"


def test_duplicate_chunk_text_keeps_first_position_only() -> None:
    header = "ELEC6098 Electronic Commerce" + " detail" * 30
    chunks = [_chunk(header, "第1张幻灯片"), _chunk(header, "第2张幻灯片")]
    out = dqa._dedupe_chunks(chunks)
    assert len(out) == 1
    assert out[0].page_label == "第1张幻灯片"


def test_child_chunks_carry_parent_unit_text() -> None:
    page = ("段落一。" * 60) + "\n\n" + ("段落二。" * 60)
    children = dqa.chunk_unit_text(page, source="a.pdf", page_label="第1页", meta="PDF", ext=".pdf")
    assert len(children) > 1
    assert all(child.parent_text == dqa.normalize_block_text(page) for child in children)


def test_finalize_shares_parent_id_and_drops_redundant_parent(tmp_path) -> None:
    doc = tmp_path / "a.pdf"
    doc.write_bytes(b"%PDF-1.4")
    page = ("段落一。" * 60) + "\n\n" + ("段落二。" * 60)
    children = dqa._finalize_chunks(
        dqa.chunk_unit_text(page, source="a.pdf", page_label="第1页", meta="PDF", ext=".pdf"),
        doc,
    )
    assert len({child.parent_id for child in children}) == 1
    assert all(child.parent_text for child in children)

    single = dqa._finalize_chunks(
        dqa.chunk_unit_text("短短一页内容。", source="a.pdf", page_label="第2页", meta="PDF", ext=".pdf"),
        doc,
    )
    assert single[0].parent_text == "", "子块等于父级时无需重复缓存父级文本"
    assert single[0].parent_id


def test_chunk_cache_roundtrip_preserves_parent_fields() -> None:
    chunk = dqa.TextChunk(
        text="子块",
        source="a.pdf",
        page_label="第1页",
        meta="PDF",
        parent_id="pid",
        parent_text="整页文本",
    )
    restored = dqa._chunk_from_dict(dqa._chunk_to_dict(chunk))
    assert restored.parent_id == "pid"
    assert restored.parent_text == "整页文本"
