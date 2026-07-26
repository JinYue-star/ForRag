"""解析缓存与向量缓存解耦：换嵌入模型不应重跑解析（图片页 OCR 很贵）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import doc_qa_assistant as dqa


def _sample(tmp_path: Path) -> Path:
    doc = tmp_path / "slides.pdf"
    doc.write_bytes(b"%PDF-1.4 fake")
    return doc


def test_parse_cache_key_ignores_embed_model(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("RAG_CACHE_ROOT", str(tmp_path / "cache"))
    doc = _sample(tmp_path)
    key = dqa._parse_cache_paths(doc)["root"].name
    # 解析缓存的 key 里不含嵌入模型，两个模型下的向量缓存 key 必须不同。
    assert key == dqa._parse_cache_paths(doc)["root"].name
    a = dqa._doc_cache_paths(doc, "BAAI/bge-small-zh-v1.5")["root"].name
    b = dqa._doc_cache_paths(doc, "intfloat/multilingual-e5-small")["root"].name
    assert a != b
    assert key not in {a, b}


def test_second_parse_reuses_cache(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("RAG_CACHE_ROOT", str(tmp_path / "cache"))
    doc = _sample(tmp_path)
    calls: list[Path] = []

    def fake_parse(path: Path) -> list[dqa.TextChunk]:
        calls.append(path)
        return [dqa.TextChunk(text="OCR 出来的一页", source=path.name, page_label="第1页", meta="PDF 图片页 OCR")]

    monkeypatch.setattr(dqa, "parse_document", fake_parse)
    first = dqa.parse_document_cached(doc)
    second = dqa.parse_document_cached(doc)
    assert len(calls) == 1, "第二次应命中解析缓存"
    assert [c.text for c in second] == [c.text for c in first]
    assert second[0].meta == "PDF 图片页 OCR"


def test_corrupt_parse_cache_falls_back_to_reparse(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("RAG_CACHE_ROOT", str(tmp_path / "cache"))
    doc = _sample(tmp_path)
    monkeypatch.setattr(
        dqa,
        "parse_document",
        lambda path: [dqa.TextChunk(text="正常内容", source=path.name, page_label="第1页", meta="PDF")],
    )
    dqa.parse_document_cached(doc)
    dqa._parse_cache_paths(doc)["chunks"].write_text("{ not json", encoding="utf-8")
    assert dqa.parse_document_cached(doc)[0].text == "正常内容"


def test_invalidate_removes_parse_cache(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("RAG_CACHE_ROOT", str(tmp_path / "cache"))
    doc = _sample(tmp_path)
    monkeypatch.setattr(
        dqa,
        "parse_document",
        lambda path: [dqa.TextChunk(text="内容", source=path.name, page_label="第1页", meta="PDF")],
    )
    dqa.parse_document_cached(doc)
    assert dqa._parse_cache_paths(doc)["manifest"].is_file()
    dqa.invalidate_caches_for_file(doc, "BAAI/bge-small-zh-v1.5")
    assert not dqa._parse_cache_paths(doc)["manifest"].is_file()
