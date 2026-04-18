#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
基于本地 Qwen / 千问 API 的文档问答脚本。
支持常见办公格式：PDF / Word(docx) / Excel(xlsx) / PowerPoint(pptx) / CSV / TXT / MD；
以及图片 PNG / JPG / WebP / GIF 等（RapidOCR 识别图中文字后参与检索）。

流程：解析文件 → 分块并记录页码或等价位置 → 向量检索 → 用大模型根据检索片段作答并引用位置。

环境变量：
  MS_MODEL_ID   模型 ID（魔搭与 HuggingFace 上 Qwen 常用同名，如 Qwen/Qwen2.5-3B-Instruct）
  LLM_HUB       加载渠道：auto（先魔搭，失败则 HF）| modelscope | huggingface
  HF_ENDPOINT   可选，国内可用镜像：https://hf-mirror.com
  MS_EMBED_ID   可选，嵌入模型；默认 BAAI/bge-small-zh-v1.5
  DASHSCOPE_API_KEY  可选，若提供则直接走千问兼容 API，不加载本地大模型
  QWEN_API_MODEL     可选，API 模型名，默认 qwen-plus
  QWEN_API_BASE      可选，默认 https://dashscope.aliyuncs.com/compatible-mode/v1
  RAG_STRICT_IMAGE_OCR  设为 1/true 时，若未安装 RapidOCR 则上传图片直接报错；默认关闭（无 OCR 时仍入库占位文本，避免崩溃）

用法示例：
  python doc_qa_assistant.py --files ./a.pdf ./b.docx --question "合同金额是多少？"
  python doc_qa_assistant.py --files ./data.xlsx --question "汇总哪一列？" --top-k 5
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional, Sequence

import numpy as np


def _faiss_write_index(index, dest: Path) -> None:
    """
    将 FAISS 索引写入目标路径。
    Windows 下原生 faiss 对含非 ASCII 的路径可能 fopen 失败，先写入系统临时文件再移动。
    """
    import faiss

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix=".faissindex")
    os.close(fd)
    tmp_path = Path(tmp)
    try:
        faiss.write_index(index, str(tmp_path))
        shutil.move(str(tmp_path), dest)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def _faiss_read_index(src: Path):
    import faiss

    src = Path(src)
    fd, tmp = tempfile.mkstemp(suffix=".faissindex")
    os.close(fd)
    tmp_path = Path(tmp)
    try:
        shutil.copy2(src, tmp_path)
        return faiss.read_index(str(tmp_path))
    finally:
        tmp_path.unlink(missing_ok=True)


# ------------- 数据结构与分块 -------------


@dataclass
class TextChunk:
    text: str
    source: str  # 文件名
    page_label: str  # 人类可读的页码或位置说明
    meta: str  # 额外说明（工作表名等）
    doc_path: str = ""  # 原始文件绝对路径
    chunk_id: str = ""  # 用于缓存/去重的稳定 ID


def _clean_text(s: str) -> str:
    s = s.replace("\x00", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def chunk_by_chars(
    text: str,
    source: str,
    page_label: str,
    meta: str,
    max_chars: int,
    overlap: int,
) -> List[TextChunk]:
    text = _clean_text(text)
    if not text:
        return []
    chunks: List[TextChunk] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + max_chars, n)
        piece = text[start:end]
        chunks.append(TextChunk(text=piece, source=source, page_label=page_label, meta=meta))
        if end >= n:
            break
        start = end - overlap
        if start < 0:
            start = 0
    return chunks


CHUNK_CONFIG = {
    ".pdf": {"max_chars": 900, "overlap": 120},
    ".docx": {"max_chars": 900, "overlap": 100},
    ".xlsx": {"max_chars": 1200, "overlap": 150},
    ".pptx": {"max_chars": 900, "overlap": 100},
    ".csv": {"max_chars": 1200, "overlap": 150},
    ".txt": {"max_chars": 900, "overlap": 80},
    ".md": {"max_chars": 900, "overlap": 80},
    ".png": {"max_chars": 900, "overlap": 80},
    ".jpg": {"max_chars": 900, "overlap": 80},
    ".jpeg": {"max_chars": 900, "overlap": 80},
    ".webp": {"max_chars": 900, "overlap": 80},
    ".bmp": {"max_chars": 900, "overlap": 80},
    ".gif": {"max_chars": 900, "overlap": 80},
    ".tif": {"max_chars": 900, "overlap": 80},
    ".tiff": {"max_chars": 900, "overlap": 80},
}
CACHE_VERSION = "rag_cache_v1"


def _sha1_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _normalized_path(path: Path | str) -> str:
    return str(Path(path).expanduser().resolve())


def _file_fingerprint(path: Path) -> str:
    stat = path.stat()
    payload = {
        "path": _normalized_path(path),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }
    return _sha1_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _chunk_identity(chunk: TextChunk) -> str:
    base = {
        "doc_path": chunk.doc_path,
        "source": chunk.source,
        "page_label": chunk.page_label,
        "meta": chunk.meta,
        "text": chunk.text,
    }
    return _sha1_text(json.dumps(base, ensure_ascii=False, sort_keys=True))


def _finalize_chunks(chunks: Sequence[TextChunk], path: Path) -> List[TextChunk]:
    out: List[TextChunk] = []
    doc_path = _normalized_path(path)
    for chunk in chunks:
        item = TextChunk(
            text=chunk.text,
            source=chunk.source,
            page_label=chunk.page_label,
            meta=chunk.meta,
            doc_path=doc_path,
        )
        item.chunk_id = _chunk_identity(item)
        out.append(item)
    return out


def _chunk_to_dict(chunk: TextChunk) -> dict:
    return {
        "text": chunk.text,
        "source": chunk.source,
        "page_label": chunk.page_label,
        "meta": chunk.meta,
        "doc_path": chunk.doc_path,
        "chunk_id": chunk.chunk_id,
    }


def _chunk_from_dict(data: dict) -> TextChunk:
    return TextChunk(
        text=data["text"],
        source=data["source"],
        page_label=data["page_label"],
        meta=data["meta"],
        doc_path=data.get("doc_path", ""),
        chunk_id=data.get("chunk_id", ""),
    )


# ------------- 各格式解析 -------------


def parse_pdf(path: Path) -> List[TextChunk]:
    import fitz  # pymupdf

    out: List[TextChunk] = []
    doc = fitz.open(path)
    name = path.name
    try:
        for i in range(len(doc)):
            page = doc.load_page(i)
            text = page.get_text("text") or ""
            page_no = i + 1
            label = f"第{page_no}页"
            out.extend(
                chunk_by_chars(
                    text,
                    source=name,
                    page_label=label,
                    meta="PDF",
                    max_chars=900,
                    overlap=120,
                )
            )
    finally:
        doc.close()
    return out


def parse_docx(path: Path) -> List[TextChunk]:
    from docx import Document

    doc = Document(path)
    name = path.name
    paras: List[str] = []
    for p in doc.paragraphs:
        t = _clean_text(p.text)
        if t:
            paras.append(t)
    full = "\n".join(paras)
    # Word 无稳定物理页码，按字数估算「约第 N 页」（按约 1800 字/页）
    chars_per_page = 1800
    out: List[TextChunk] = []
    pos = 0
    for para in paras:
        est_page = pos // chars_per_page + 1
        label = f"约第{est_page}页(估算)"
        out.extend(
            chunk_by_chars(
                para,
                source=name,
                page_label=label,
                meta="Word(按字数估算页码)",
                max_chars=900,
                overlap=100,
            )
        )
        pos += len(para) + 1
    if not out and full:
        out.extend(
            chunk_by_chars(
                full,
                source=name,
                page_label="全文",
                meta="Word",
                max_chars=900,
                overlap=100,
            )
        )
    return out


def parse_xlsx(path: Path) -> List[TextChunk]:
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    name = path.name
    out: List[TextChunk] = []
    try:
        for sheet in wb.worksheets:
            rows: List[str] = []
            for r_idx, row in enumerate(sheet.iter_rows(values_only=True), start=1):
                cells = [str(c).strip() for c in row if c is not None and str(c).strip()]
                if not cells:
                    continue
                line = " | ".join(cells)
                rows.append(f"行{r_idx}: {line}")
            sheet_text = "\n".join(rows)
            label = f"工作表「{sheet.title}」"
            out.extend(
                chunk_by_chars(
                    sheet_text,
                    source=name,
                    page_label=label,
                    meta="Excel(位置为工作表+行)",
                    max_chars=1200,
                    overlap=150,
                )
            )
    finally:
        wb.close()
    return out


def parse_pptx(path: Path) -> List[TextChunk]:
    from pptx import Presentation

    prs = Presentation(path)
    name = path.name
    out: List[TextChunk] = []
    for si, slide in enumerate(prs.slides, start=1):
        parts: List[str] = []
        for shape in slide.shapes:
            if hasattr(shape, "text") and shape.text:
                parts.append(_clean_text(shape.text))
        text = "\n".join(parts)
        label = f"第{si}张幻灯片"
        out.extend(
            chunk_by_chars(
                text,
                source=name,
                page_label=label,
                meta="PPT",
                max_chars=900,
                overlap=100,
            )
        )
    return out


def parse_csv(path: Path) -> List[TextChunk]:
    import pandas as pd

    name = path.name
    try:
        df = pd.read_csv(path, encoding="utf-8")
    except UnicodeDecodeError:
        df = pd.read_csv(path, encoding="gbk")
    lines = []
    for i, row in df.iterrows():
        lines.append(f"行{int(i)+2}: " + " | ".join(str(x) for x in row.values))  # +2: 表头占1，pandas 0-based
    text = "\n".join(lines)
    return chunk_by_chars(
        text,
        source=name,
        page_label="CSV 行号见各行前缀",
        meta="CSV",
        max_chars=1200,
        overlap=150,
    )


def parse_txt(path: Path) -> List[TextChunk]:
    raw = path.read_bytes()
    for enc in ("utf-8", "gbk", "utf-16"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", errors="replace")
    name = path.name
    lines = text.splitlines()
    # 按行范围分块并估算页（约40行/页）
    lines_per_page = 40
    out: List[TextChunk] = []
    buf: List[str] = []
    base_line = 0
    for i, line in enumerate(lines):
        buf.append(line)
        if len(buf) >= lines_per_page:
            chunk_text = "\n".join(buf)
            est = base_line // lines_per_page + 1
            label = f"约第{est}页(按行估算, 行{base_line+1}-{i+1})"
            out.extend(
                chunk_by_chars(
                    chunk_text,
                    source=name,
                    page_label=label,
                    meta="文本",
                    max_chars=900,
                    overlap=80,
                )
            )
            buf = []
            base_line = i + 1
    if buf:
        est = base_line // lines_per_page + 1
        chunk_text = "\n".join(buf)
        label = f"约第{est}页(按行估算, 行{base_line+1}-{len(lines)})"
        out.extend(
            chunk_by_chars(
                chunk_text,
                source=name,
                page_label=label,
                meta="文本",
                max_chars=900,
                overlap=80,
            )
        )
    if not out and text.strip():
        out.extend(
            chunk_by_chars(
                text.strip(),
                source=name,
                page_label="全文",
                meta="文本",
                max_chars=900,
                overlap=80,
            )
        )
    return out


_rapid_ocr_engine: Any = None


def _get_rapid_ocr() -> Any:
    global _rapid_ocr_engine
    if _rapid_ocr_engine is None:
        py = sys.executable
        try:
            import onnxruntime  # noqa: F401  # RapidOCR 依赖；缺省时常表现为 rapidocr 导入失败
        except ImportError as e:
            raise ValueError(
                f"图像 OCR 依赖 onnxruntime。请在运行服务的同一 Python 环境中执行："
                f'"{py}" -m pip install onnxruntime rapidocr-onnxruntime pillow'
                f"\n（若使用 conda：先 conda activate forrag 再安装。）\n原始错误: {e!r}"
            ) from e
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as e:
            raise ValueError(
                f"无法导入 rapidocr_onnxruntime。当前解释器: {py}\n"
                f'请执行: "{py}" -m pip install rapidocr-onnxruntime pillow\n'
                f"原始错误: {e!r}"
            ) from e
        try:
            _rapid_ocr_engine = RapidOCR()
        except Exception as e:
            raise RuntimeError(
                f"RapidOCR 初始化失败（解释器: {py}）。可尝试重装: "
                f'"{py}" -m pip install --force-reinstall onnxruntime rapidocr-onnxruntime\n'
                f"详情: {e!r}"
            ) from e
    return _rapid_ocr_engine


def _text_lines_from_rapidocr_output(ocr_out: object) -> list[str]:
    """从 RapidOCR 返回值中取出文本行（兼容 (result, 耗时) 等结构）。"""
    if ocr_out is None:
        return []
    payload: object = ocr_out
    if isinstance(ocr_out, (list, tuple)) and len(ocr_out) == 2:
        a, b = ocr_out[0], ocr_out[1]
        if isinstance(b, (int, float)):
            payload = a
    rows = payload
    if not isinstance(rows, (list, tuple)):
        return []
    texts: list[str] = []
    for item in rows:
        if item is None:
            continue
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            seg = item[1]
            if isinstance(seg, (list, tuple)) and len(seg) >= 1:
                texts.append(str(seg[0]).strip())
            elif isinstance(seg, str):
                texts.append(seg.strip())
        elif isinstance(item, str):
            texts.append(item.strip())
    return [t for t in texts if t]


def _strict_image_ocr() -> bool:
    """为真时：缺少 OCR 依赖直接报错；否则写入占位文本，避免整次问答失败。"""
    return os.environ.get("RAG_STRICT_IMAGE_OCR", "").strip().lower() in {"1", "true", "yes"}


def _parse_image_placeholder_chunks(name: str, detail: str) -> List[TextChunk]:
    """无 RapidOCR 时仍生成可检索占位块（提示安装依赖）。"""
    text = (
        f"【图片文件】{name}\n"
        f"（未能进行 OCR：{detail}\n"
        f"当前 Python：{sys.executable}\n"
        "请在本环境执行：python -m pip install -r requirements.txt\n"
        "若使用 conda：先 conda activate <你的环境名> 再安装并启动服务。）"
    )
    return chunk_by_chars(
        text,
        source=name,
        page_label="图片",
        meta="图像(无OCR)",
        max_chars=900,
        overlap=80,
    )


def parse_image(path: Path) -> List[TextChunk]:
    """使用 RapidOCR 将图片中的文字识别为文本后分块；依赖缺失时可降级为占位文本。"""
    try:
        from PIL import Image
    except ImportError as e:
        raise ValueError("图像解析需要 pillow：pip install pillow") from e

    p = path.expanduser().resolve()
    name = p.name
    img = Image.open(p)
    if getattr(img, "n_frames", 1) > 1:
        img.seek(0)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    strict = _strict_image_ocr()
    try:
        ocr = _get_rapid_ocr()
    except (ValueError, RuntimeError) as e:
        if strict:
            raise
        return _parse_image_placeholder_chunks(name, str(e))

    tmp_path: Optional[str] = None
    try:
        fd, tmp_path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        img.save(tmp_path, format="PNG")
        ocr_raw = ocr(tmp_path)
    except Exception as e:
        if strict:
            raise
        return _parse_image_placeholder_chunks(name, repr(e))
    finally:
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)

    lines = _text_lines_from_rapidocr_output(ocr_raw)
    text = "\n".join(lines).strip()
    if not text:
        text = "（图片中未识别到文字内容）"

    return chunk_by_chars(
        text,
        source=name,
        page_label="图片",
        meta="图像 OCR",
        max_chars=900,
        overlap=80,
    )


PARSERS = {
    ".pdf": parse_pdf,
    ".docx": parse_docx,
    ".xlsx": parse_xlsx,
    ".pptx": parse_pptx,
    ".csv": parse_csv,
    ".txt": parse_txt,
    ".md": parse_txt,
}

_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff")
for _ext in _IMAGE_EXTS:
    PARSERS[_ext] = parse_image


def parse_document(path: Path) -> List[TextChunk]:
    p = path.expanduser().resolve()
    ext = p.suffix.lower()
    fn = PARSERS.get(ext)
    if not fn:
        raise ValueError(f"不支持的扩展名 {ext}")
    chunks = _finalize_chunks(fn(p), p)
    print(f"[解析] {p.name} → {len(chunks)} 个文本块")
    return chunks


def load_documents(paths: Sequence[Path]) -> List[TextChunk]:
    all_chunks: List[TextChunk] = []
    for p in paths:
        p = p.expanduser().resolve()
        if not p.is_file():
            print(f"[跳过] 不存在: {p}", file=sys.stderr)
            continue
        try:
            all_chunks.extend(parse_document(p))
        except Exception as e:
            print(f"[错误] 解析失败 {p}: {e}", file=sys.stderr)
    return all_chunks


# ------------- 向量检索 -------------


def _log_step(msg: str) -> None:
    print(msg, flush=True)


def _cache_root() -> Path:
    env = os.environ.get("RAG_CACHE_ROOT", "").strip()
    if env:
        root = Path(env).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        return root
    return Path(__file__).resolve().parent / ".rag_cache"


def invalidate_caches_for_file(path: Path, embed_model_id: str) -> None:
    """
    删除指定文件对应的单文档向量缓存，并移除引用该文件路径的整库 bundle 缓存。
    必须在物理文件仍存在时调用（单文档缓存 key 依赖文件指纹）。
    """
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        return
    norm = _normalized_path(p)
    doc = _doc_cache_paths(p, embed_model_id)
    shutil.rmtree(doc["root"], ignore_errors=True)

    bundles_dir = _cache_root() / "bundles"
    if not bundles_dir.is_dir():
        return
    for child in list(bundles_dir.iterdir()):
        if not child.is_dir():
            continue
        man = child / "manifest.json"
        if not man.is_file():
            continue
        try:
            data = json.loads(man.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for item in data.get("files") or []:
            if item.get("path") == norm:
                shutil.rmtree(child, ignore_errors=True)
                break


def _bundle_key(paths: Sequence[Path], embed_model_id: str) -> str:
    payload = {
        "version": CACHE_VERSION,
        "embed_model_id": embed_model_id,
        "files": [
            {"path": _normalized_path(p), "fingerprint": _file_fingerprint(Path(p))}
            for p in sorted((Path(p) for p in paths), key=lambda x: str(x))
        ],
    }
    return _sha1_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _doc_cache_paths(path: Path, embed_model_id: str) -> dict:
    key = _sha1_text(
        json.dumps(
            {
                "version": CACHE_VERSION,
                "embed_model_id": embed_model_id,
                "file": _normalized_path(path),
                "fingerprint": _file_fingerprint(path),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    root = _cache_root() / "docs" / key
    return {
        "root": root,
        "manifest": root / "manifest.json",
        "chunks": root / "chunks.jsonl",
        "embeddings": root / "embeddings.npy",
    }


def _bundle_cache_paths(paths: Sequence[Path], embed_model_id: str) -> dict:
    key = _bundle_key(paths, embed_model_id)
    root = _cache_root() / "bundles" / key
    return {
        "root": root,
        "manifest": root / "manifest.json",
        "chunks": root / "chunks.jsonl",
        "embeddings": root / "embeddings.npy",
        "index": root / "faiss.index",
    }


def _write_chunks(path: Path, chunks: Sequence[TextChunk]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for chunk in chunks:
            f.write(json.dumps(_chunk_to_dict(chunk), ensure_ascii=False) + "\n")


def _read_chunks(path: Path) -> List[TextChunk]:
    chunks: List[TextChunk] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            chunks.append(_chunk_from_dict(json.loads(line)))
    return chunks


def _load_st_model(embed_model_id: str):
    from sentence_transformers import SentenceTransformer

    _log_step(
        f"[嵌入] (1/2) 加载向量模型 {embed_model_id}（首次会下载约百兆，请等进度条）…"
    )
    return SentenceTransformer(embed_model_id)


def _encode_chunks(st, chunks: Sequence[TextChunk]) -> np.ndarray:
    texts = [c.text for c in chunks]
    _log_step(f"[嵌入] (2/2) 编码 {len(texts)} 条文本块 …")
    emb = st.encode(list(texts), show_progress_bar=True, normalize_embeddings=True)
    return np.asarray(emb, dtype=np.float32)


def _build_faiss_index(embeddings: np.ndarray):
    import faiss

    index = faiss.IndexFlatIP(embeddings.shape[1])
    index.add(embeddings)
    return index


def _save_doc_cache(path: Path, embed_model_id: str, chunks: Sequence[TextChunk], embeddings: np.ndarray) -> None:
    files = _doc_cache_paths(path, embed_model_id)
    files["root"].mkdir(parents=True, exist_ok=True)
    _write_chunks(files["chunks"], chunks)
    np.save(files["embeddings"], embeddings)
    manifest = {
        "version": CACHE_VERSION,
        "embed_model_id": embed_model_id,
        "file_path": _normalized_path(path),
        "file_fingerprint": _file_fingerprint(path),
        "chunk_count": len(chunks),
    }
    files["manifest"].write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_doc_cache(path: Path, embed_model_id: str) -> tuple[List[TextChunk], np.ndarray] | None:
    files = _doc_cache_paths(path, embed_model_id)
    if not files["manifest"].is_file() or not files["chunks"].is_file() or not files["embeddings"].is_file():
        return None
    chunks = _read_chunks(files["chunks"])
    embeddings = np.load(files["embeddings"])
    return chunks, np.asarray(embeddings, dtype=np.float32)


def _save_bundle_cache(
    paths: Sequence[Path],
    embed_model_id: str,
    chunks: Sequence[TextChunk],
    embeddings: np.ndarray,
    index,
) -> None:
    import faiss

    files = _bundle_cache_paths(paths, embed_model_id)
    files["root"].mkdir(parents=True, exist_ok=True)
    _write_chunks(files["chunks"], chunks)
    np.save(files["embeddings"], embeddings)
    _faiss_write_index(index, files["index"])
    manifest = {
        "version": CACHE_VERSION,
        "embed_model_id": embed_model_id,
        "bundle_key": _bundle_key(paths, embed_model_id),
        "files": [
            {"path": _normalized_path(p), "fingerprint": _file_fingerprint(Path(p))}
            for p in sorted((Path(p) for p in paths), key=lambda x: str(x))
        ],
        "chunk_count": len(chunks),
    }
    files["manifest"].write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_bundle_cache(paths: Sequence[Path], embed_model_id: str):
    import faiss

    files = _bundle_cache_paths(paths, embed_model_id)
    if not files["manifest"].is_file() or not files["chunks"].is_file() or not files["embeddings"].is_file() or not files["index"].is_file():
        return None
    chunks = _read_chunks(files["chunks"])
    embeddings = np.load(files["embeddings"])
    index = _faiss_read_index(files["index"])
    return chunks, np.asarray(embeddings, dtype=np.float32), index


def build_or_load_index(paths: Sequence[Path], embed_model_id: str):
    cached_bundle = _load_bundle_cache(paths, embed_model_id)
    st = _load_st_model(embed_model_id)
    if cached_bundle is not None:
        chunks, embeddings, index = cached_bundle
        _log_step(f"[缓存] 命中整库缓存，直接复用 {len(chunks)} 个文本块。")
        return chunks, embeddings, index, st

    chunks_all: List[TextChunk] = []
    emb_all: List[np.ndarray] = []
    changed_count = 0
    for path in paths:
        p = Path(path).expanduser().resolve()
        if not p.is_file():
            print(f"[跳过] 不存在: {p}", file=sys.stderr)
            continue
        ext = p.suffix.lower()
        if ext not in PARSERS:
            print(f"[跳过] 不支持的扩展名 {ext}: {p}", file=sys.stderr)
            continue
        cached_doc = _load_doc_cache(p, embed_model_id)
        if cached_doc is not None:
            doc_chunks, doc_emb = cached_doc
            _log_step(f"[缓存] 命中文档缓存：{p.name}（{len(doc_chunks)} 块）")
        else:
            doc_chunks = parse_document(p)
            doc_emb = _encode_chunks(st, doc_chunks)
            _save_doc_cache(p, embed_model_id, doc_chunks, doc_emb)
            changed_count += 1
        chunks_all.extend(doc_chunks)
        emb_all.append(doc_emb)

    if not chunks_all:
        raise ValueError("没有可用的文档块，请检查文件路径与格式。")
    embeddings = np.concatenate(emb_all, axis=0).astype(np.float32, copy=False)
    index = _build_faiss_index(embeddings)
    _save_bundle_cache(paths, embed_model_id, chunks_all, embeddings, index)
    if changed_count:
        _log_step(f"[缓存] 已更新 {changed_count} 个文档缓存，并重建整库索引。")
    return chunks_all, embeddings, index, st


def search(
    query: str,
    chunks: Sequence[TextChunk],
    index,
    st,
    top_k: int,
) -> List[tuple[float, TextChunk]]:
    q = st.encode([query], normalize_embeddings=True)
    q = np.asarray(q, dtype=np.float32)
    n = len(chunks)
    if n == 0:
        return []
    k = max(1, min(int(top_k), n))
    scores, ids = index.search(q, k)
    out: List[tuple[float, TextChunk]] = []
    for score, idx in zip(scores[0], ids[0]):
        if idx < 0:
            continue
        out.append((float(score), chunks[idx]))
    return out


def route_generation(has_api_key: bool) -> str:
    if has_api_key:
        return "api"
    return "local"


# ------------- 大模型（魔搭 / Hugging Face）-------------


def _configure_llm_loading() -> None:
    """减轻「像卡死」：打开 transformers 日志、限制线程，避免占满 CPU。"""
    import os
    import torch

    try:
        import transformers

        transformers.utils.logging.set_verbosity_info()
    except Exception:
        pass
    n = os.cpu_count() or 4
    # 加载大权重时少占线程，系统更不容易假死
    nt = max(1, min(4, n // 2))
    try:
        torch.set_num_threads(nt)
        torch.set_num_interop_threads(1)
    except Exception:
        pass


def _llm_dtype(cpu_half: bool = False):
    import torch

    if torch.cuda.is_available():
        return torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    # 无 GPU：fp32 约需 12GB+ 内存（3B），易因换页长时间无响应。
    # 注意：float16 在很多 CPU 上反而会非常慢，看起来像“完全不出字”。
    if cpu_half:
        cpu_has_bf16 = bool(getattr(getattr(torch.backends, "cpu", None), "has_bf16", False))
        if cpu_has_bf16:
            _log_step("[LLM] 检测到 CPU 支持 bfloat16，使用 bfloat16 以降低内存占用。")
            return torch.bfloat16
        _log_step("[LLM] 警告：当前 CPU 不适合 float16 推理；为避免生成阶段极慢，改回 float32。")
    return torch.float32


def _normalize_llm_hub(name: str) -> str:
    n = (name or "auto").strip().lower()
    if n in ("ms", "modelscope"):
        return "modelscope"
    if n in ("hf", "huggingface"):
        return "huggingface"
    return "auto"


def _from_pretrained_llm(model_id: str, dtype, hub_ms: bool):
    """避免 CPU 上 device_map='auto' 与部分 transformers 版本组合时出现 meta 张量错误。"""
    import torch

    if hub_ms:
        from modelscope import AutoModelForCausalLM
    else:
        from transformers import AutoModelForCausalLM

    def load(**extra):
        return AutoModelForCausalLM.from_pretrained(model_id, trust_remote_code=True, **extra)

    if torch.cuda.is_available():
        _log_step("[LLM] 正在将权重载入 GPU（device_map=auto，请稍候）…")
        try:
            m = load(dtype=dtype, device_map="auto")
        except TypeError:
            m = load(torch_dtype=dtype, device_map="auto")
        _log_step("[LLM] 权重已载入 GPU。")
        return m

    # CPU：整模放内存，不用 accelerate 分片，避免 generate 里 eos/pad 与 meta 张量冲突
    _log_step(
        "[LLM] 正在从磁盘读取并映射权重到内存（CPU 上可能需数分钟，磁盘灯会闪，并非死机）…"
    )
    try:
        m = load(dtype=dtype, device_map=None, low_cpu_mem_usage=True)
    except TypeError:
        m = load(torch_dtype=dtype, device_map=None, low_cpu_mem_usage=True)
    _log_step("[LLM] 权重加载完成，准备推理。")
    return m


def _fix_tokenizer_pad(tokenizer) -> None:
    if tokenizer.pad_token_id is None and tokenizer.eos_token_id is not None:
        tokenizer.pad_token = tokenizer.eos_token


def _sync_gen_config(model, tokenizer) -> None:
    gc = getattr(model, "generation_config", None)
    if gc is None:
        return
    if tokenizer.pad_token_id is not None:
        gc.pad_token_id = int(tokenizer.pad_token_id)
    if tokenizer.eos_token_id is not None:
        gc.eos_token_id = int(tokenizer.eos_token_id)
    # 与 generate(do_sample=False) 一致，避免与仓库里 temperature/top_p/top_k 冲突刷屏
    gc.do_sample = False
    for name in ("temperature", "top_p", "top_k"):
        if hasattr(gc, name):
            try:
                setattr(gc, name, None)
            except Exception:
                pass


def _looks_like_model_dir(p: Path) -> bool:
    return (p / "config.json").is_file() or (p / "tokenizer_config.json").is_file()


def _find_modelscope_local_snapshot(model_id: str) -> str | None:
    """魔搭把模型放在 ~/.cache/modelscope/hub/models/组织/名称/，名称里的点会变成 ___。"""
    base = Path(os.environ.get("MODELSCOPE_CACHE", Path.home() / ".cache" / "modelscope")) / "hub" / "models"
    if not base.is_dir() or "/" not in model_id:
        return None
    org, name = model_id.split("/", 1)
    candidates = [
        base / org / name,
        base / org / name.replace(".", "___"),
    ]
    for c in candidates:
        if _looks_like_model_dir(c):
            return str(c.resolve())
    org_dir = base / org
    if org_dir.is_dir():
        for child in sorted(org_dir.iterdir(), key=lambda x: x.name):
            if child.is_dir() and _looks_like_model_dir(child) and name.split("-")[-1] in child.name:
                return str(child.resolve())
    return None


def load_llm(model_id: str, hub: str = "auto", cpu_half: bool = False):
    """hub: auto | modelscope | huggingface。auto 在魔搭 DNS/网络失败时回退到 Hugging Face。"""
    _configure_llm_loading()
    dtype = _llm_dtype(cpu_half=cpu_half)
    hub_n = _normalize_llm_hub(hub)

    def from_hf():
        from transformers import AutoTokenizer

        _log_step(f"[LLM] 从 Hugging Face 加载: {model_id}（首次会下载权重）")
        _log_step("[LLM] (1/2) 加载 tokenizer …")
        tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
        _fix_tokenizer_pad(tokenizer)
        _log_step("[LLM] (2/2) 加载生成模型权重（最耗时）…")
        model = _from_pretrained_llm(model_id, dtype, hub_ms=False)
        model.eval()
        _sync_gen_config(model, tokenizer)
        return model, tokenizer

    def from_local_dir(local_dir: str, label: str = "本地目录"):
        from transformers import AutoTokenizer

        _log_step(f"[LLM] 从{label}加载（不访问魔搭在线接口）: {local_dir}")
        _log_step("[LLM] (1/2) 加载 tokenizer …")
        tokenizer = AutoTokenizer.from_pretrained(local_dir, trust_remote_code=True)
        _fix_tokenizer_pad(tokenizer)
        _log_step("[LLM] (2/2) 加载生成模型权重 …")
        model = _from_pretrained_llm(local_dir, dtype, hub_ms=False)
        model.eval()
        _sync_gen_config(model, tokenizer)
        return model, tokenizer

    def from_ms_online():
        from modelscope import AutoTokenizer

        _log_step(f"[LLM] 从魔搭 ModelScope 在线加载: {model_id}（首次会下载权重）")
        _log_step("[LLM] (1/2) 加载 tokenizer …")
        tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
        _fix_tokenizer_pad(tokenizer)
        _log_step("[LLM] (2/2) 加载生成模型权重 …")
        model = _from_pretrained_llm(model_id, dtype, hub_ms=True)
        model.eval()
        _sync_gen_config(model, tokenizer)
        return model, tokenizer

    def try_ms_with_local_fallback():
        try:
            return from_ms_online()
        except Exception as e:
            local = _find_modelscope_local_snapshot(model_id)
            if local:
                print(
                    f"[LLM] 魔搭在线校验失败（{type(e).__name__}），改用本机已下载缓存。",
                    file=sys.stderr,
                )
                return from_local_dir(local, label="魔搭缓存")
            raise

    if hub_n == "huggingface":
        return from_hf()
    if hub_n == "modelscope":
        try:
            return try_ms_with_local_fallback()
        except Exception as e:
            print(
                "[LLM] 魔搭不可用且无本地缓存。可改用: --llm-hub huggingface "
                "或设置 $env:HF_ENDPOINT='https://hf-mirror.com'",
                file=sys.stderr,
            )
            raise e

    try:
        return try_ms_with_local_fallback()
    except Exception as e:
        print(
            f"[LLM] 自动模式：魔搭/本地缓存均不可用（{type(e).__name__}），改用 Hugging Face。摘要: {e!s}",
            file=sys.stderr,
        )
        print(
            "[提示] 若 HuggingFace 较慢: $env:HF_ENDPOINT='https://hf-mirror.com'",
            file=sys.stderr,
        )
        return from_hf()


def build_prompt(
    question: str,
    retrieved: List[tuple[float, TextChunk]],
) -> str:
    parts = []
    for i, (score, ch) in enumerate(retrieved, 1):
        ref = f"[片段{i}] 文件: {ch.source} | 位置: {ch.page_label} | {ch.meta}"
        parts.append(f"{ref}\n内容: {ch.text}\n")
    context = "\n".join(parts)
    user_msg = (
        "你是文档问答助手。请仅根据下面「检索到的文档片段」回答问题。\n"
        "要求：\n"
        "1. 先给出简洁答案。\n"
        "2. 在答案中或末尾用括号标明依据：文件名 + 页码/位置（与片段中的「位置」一致）。\n"
        "3. 若片段不足以回答，请自由回答。\n\n"
        f"用户问题：{question}\n\n"
        f"检索到的文档片段：\n{context}"
    )
    return user_msg


def generate_answer(
    model,
    tokenizer,
    user_msg: str,
    max_new_tokens: Optional[int],
    stream: bool = True,
) -> str:
    import torch
    from transformers import TextStreamer

    messages = [{"role": "user", "content": user_msg}]
    # Qwen2.5 / Qwen3 均支持 chat_template
    try:
        text = tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )
    except Exception:
        text = user_msg

    pad_id = tokenizer.pad_token_id
    if pad_id is None:
        pad_id = tokenizer.eos_token_id
    eos_id = tokenizer.eos_token_id

    inputs = tokenizer(text, return_tensors="pt")
    device = next(model.parameters()).device
    inputs = {k: v.to(device) for k, v in inputs.items()}
    gen_kw = {
        "do_sample": False,
        "pad_token_id": pad_id,
        "eos_token_id": eos_id,
    }
    if max_new_tokens is not None:
        gen_kw["max_new_tokens"] = max_new_tokens
    if not stream:
        with torch.no_grad():
            out = model.generate(**inputs, **gen_kw)
        new_tokens = out[0, inputs["input_ids"].shape[1] :]
        reply = tokenizer.decode(new_tokens, skip_special_tokens=True)
        return reply.strip()

    _log_step("[LLM] 开始流式生成回答：")
    streamer = TextStreamer(
        tokenizer,
        skip_prompt=True,
        skip_special_tokens=True,
    )
    with torch.no_grad():
        out = model.generate(**inputs, **gen_kw, streamer=streamer)
    print(flush=True)
    new_tokens = out[0, inputs["input_ids"].shape[1] :]
    reply = tokenizer.decode(new_tokens, skip_special_tokens=True)
    return reply.strip()


def generate_answer_via_api(
    api_key: str,
    api_model: str,
    api_base: str,
    user_msg: str,
    max_new_tokens: Optional[int],
    stream: bool = True,
) -> str:
    import httpx
    from openai import OpenAI
    from openai import APIConnectionError

    timeout = httpx.Timeout(120.0, connect=20.0)
    with httpx.Client(http2=False, trust_env=False, timeout=timeout) as http_client:
        client = OpenAI(
            api_key=api_key,
            base_url=api_base,
            http_client=http_client,
            max_retries=2,
        )
        req = {
            "model": api_model,
            "messages": [{"role": "user", "content": user_msg}],
            "temperature": 0,
            "stream": stream,
        }
        if max_new_tokens is not None:
            req["max_tokens"] = max_new_tokens
        if not stream:
            resp = client.chat.completions.create(**req)
            return (resp.choices[0].message.content or "").strip()

        _log_step(f"[API] 使用千问兼容 API 流式生成：{api_model}")
        chunks: List[str] = []
        try:
            resp = client.chat.completions.create(**req)
            for event in resp:
                delta = event.choices[0].delta.content or ""
                if not delta:
                    continue
                chunks.append(delta)
                print(delta, end="", flush=True)
            print(flush=True)
            return "".join(chunks).strip()
        except APIConnectionError:
            _log_step("[API] 流式连接失败，自动退回非流式请求重试…")
            req["stream"] = False
            resp = client.chat.completions.create(**req)
            text = (resp.choices[0].message.content or "").strip()
            print(text, flush=True)
            return text


def main():
    parser = argparse.ArgumentParser(description="魔搭 Qwen 文档问答（上传办公文件，检索页码与答案）")
    parser.add_argument("--files", nargs="+", required=True, help="一个或多个文件路径")
    parser.add_argument("--question", "-q", required=True, help="用户问题")
    parser.add_argument(
        "--top-k",
        type=int,
        default=3,
        help="向量检索返回的片段条数，默认 3（仅影响送入模型的上下文，不改变索引缓存）",
    )
    parser.add_argument(
        "--max-new-tokens",
        type=int,
        default=None,
        help="生成新 token 上限；不设则不在请求里限制长度（由服务端/模型自行决定）",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("MS_MODEL_ID", "Qwen/Qwen2.5-3B-Instruct"),
        help="模型 ID（魔搭与 HF 上 Qwen 常同名），环境变量 MS_MODEL_ID；例：Qwen/Qwen2.5-7B-Instruct",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("DASHSCOPE_API_KEY", "sk-a9039ea944cb4de792c876d6f731f5d6"),
        help="千问 API Key；提供后将直接走 API，不加载本地大模型。也可设环境变量 DASHSCOPE_API_KEY",
    )
    parser.add_argument(
        "--api-model",
        default=os.environ.get("QWEN_API_MODEL", "qwen-plus"),
        help="千问 API 模型名，默认 qwen-plus；也可设环境变量 QWEN_API_MODEL",
    )
    parser.add_argument(
        "--api-base",
        default=os.environ.get(
            "QWEN_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1"
        ),
        help="千问兼容 API Base URL；也可设环境变量 QWEN_API_BASE",
    )
    parser.add_argument(
        "--llm-hub",
        default=_normalize_llm_hub(os.environ.get("LLM_HUB", "auto")),
        choices=["auto", "modelscope", "huggingface"],
        help="auto=魔搭在线→失败则用本机魔搭缓存→再失败则 HF；modelscope=优先在线，失败用缓存；huggingface=仅 HF。环境变量 LLM_HUB",
    )
    parser.add_argument(
        "--embed-model",
        default=os.environ.get("MS_EMBED_ID", "BAAI/bge-small-zh-v1.5"),
        help="向量检索嵌入模型 ID（默认 HuggingFace 的 bge-small-zh；也可换魔搭上的中文句向量模型并设 MS_EMBED_ID）",
    )
    parser.add_argument(
        "--low-memory",
        action="store_true",
        help="无 GPU 时用 float16 加载大模型，内存约减半（推荐 16GB 以下内存或加载卡住时尝试）",
    )
    parser.add_argument(
        "--no-stream",
        action="store_true",
        help="关闭实时 token 输出，改为生成完成后一次性打印",
    )
    args = parser.parse_args()

    paths = [Path(f) for f in args.files]
    try:
        chunks, _embeddings, index, st = build_or_load_index(paths, args.embed_model)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    hits = search(args.question, chunks, index, st, args.top_k)

    print("\n---------- 检索到的位置（Top-%d）----------" % len(hits))
    for score, ch in hits:
        print(f"  相关度 {score:.4f} | {ch.source} | {ch.page_label} | {ch.meta}")

    print("\n---------- 模型生成回答 ----------")
    route = route_generation(has_api_key=bool(args.api_key.strip()))

    user_msg = build_prompt(args.question, hits)
    if route == "api":
        answer = generate_answer_via_api(
            api_key=args.api_key.strip(),
            api_model=args.api_model,
            api_base=args.api_base,
            user_msg=user_msg,
            max_new_tokens=args.max_new_tokens,
            stream=not args.no_stream,
        )
        if args.no_stream:
            print(answer)
        return

    _log_step("[路由] 当前问题走本地模型生成。")
    model, tokenizer = load_llm(args.model, hub=args.llm_hub, cpu_half=args.low_memory)
    answer = generate_answer(
        model, tokenizer, user_msg, args.max_new_tokens, stream=not args.no_stream
    )
    if args.no_stream:
        print(answer)


if __name__ == "__main__":
    main()
