"""PDF 图片页 OCR 兜底：输出解析兼容性、触发条件与失败降级。"""

from __future__ import annotations

from typing import Any

import doc_qa_assistant as dqa


def test_rapidocr_output_with_elapse_list_yields_text() -> None:
    # 当前 rapidocr-onnxruntime 返回 (rows, 各阶段耗时列表)，早期版本返回 (rows, 单个耗时)。
    rows = [
        [[[0, 0], [1, 0], [1, 1], [0, 1]], "ELEC6081 Biomedical Signals", 0.98],
        [[[0, 2], [1, 2], [1, 3], [0, 3]], "Sampling theorem", 0.95],
    ]
    for elapse in ([0.1, 0.2, 0.3], 0.4, None):
        lines = dqa._text_lines_from_rapidocr_output((rows, elapse))
        assert lines == ["ELEC6081 Biomedical Signals", "Sampling theorem"], elapse


def test_rapidocr_result_object_with_txts_attribute() -> None:
    class Result:
        txts = ("aliasing", " Nyquist rate ", "")

    assert dqa._text_lines_from_rapidocr_output(Result()) == ["aliasing", "Nyquist rate"]


class _FakePage:
    def __init__(self, text: str) -> None:
        self._text = text

    def get_text(self, _mode: str) -> str:
        return self._text

    def get_pixmap(self, **_kw: Any) -> Any:
        class Pix:
            @staticmethod
            def tobytes(_fmt: str) -> bytes:
                return b"fake-png"

        return Pix()


class _FakeDoc:
    def __init__(self, pages: list[_FakePage]) -> None:
        self._pages = pages
        self.closed = False

    def __len__(self) -> int:
        return len(self._pages)

    def load_page(self, i: int) -> _FakePage:
        return self._pages[i]

    def close(self) -> None:
        self.closed = True


def _patch_pdf(monkeypatch: Any, pages: list[str]) -> None:
    import sys
    import types

    fake_fitz = types.ModuleType("fitz")
    fake_fitz.open = lambda _p: _FakeDoc([_FakePage(t) for t in pages])  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fitz", fake_fitz)


def test_image_only_page_falls_back_to_ocr(monkeypatch: Any, tmp_path: Any) -> None:
    _patch_pdf(monkeypatch, ["", "有文字层的一页" * 20])
    monkeypatch.setattr(
        dqa, "_ocr_pdf_page", lambda _page: "Nyquist sampling theorem requires fs > 2 fmax"
    )
    chunks = dqa.parse_pdf(tmp_path / "slides.pdf")
    first = [c for c in chunks if c.page_label == "第1页"]
    assert first and "Nyquist" in first[0].text
    assert first[0].meta == "PDF 图片页 OCR"
    # 有文字层的页不应触发 OCR
    assert all(c.meta == "PDF" for c in chunks if c.page_label == "第2页")


def test_ocr_disabled_by_env(monkeypatch: Any, tmp_path: Any) -> None:
    _patch_pdf(monkeypatch, [""])
    monkeypatch.setenv("RAG_PDF_OCR", "0")
    called: list[int] = []
    monkeypatch.setattr(dqa, "_ocr_pdf_page", lambda _page: called.append(1) or "text")
    dqa.parse_pdf(tmp_path / "slides.pdf")
    assert not called


def test_ocr_failure_does_not_break_ingestion(monkeypatch: Any, tmp_path: Any) -> None:
    _patch_pdf(monkeypatch, ["", "正常页面文字" * 30])

    def boom(_page: Any) -> str:
        raise RuntimeError("onnxruntime missing")

    monkeypatch.setattr(dqa, "_get_rapid_ocr", boom)
    chunks = dqa.parse_pdf(tmp_path / "slides.pdf")
    assert any("正常页面文字" in c.text for c in chunks)


def test_ocr_page_budget_is_bounded(monkeypatch: Any, tmp_path: Any) -> None:
    _patch_pdf(monkeypatch, ["", "", ""])
    monkeypatch.setattr(dqa, "PDF_OCR_MAX_PAGES", 2)
    seen: list[int] = []
    monkeypatch.setattr(dqa, "_ocr_pdf_page", lambda _page: seen.append(1) or "识别文字内容")
    dqa.parse_pdf(tmp_path / "slides.pdf")
    assert len(seen) == 2
