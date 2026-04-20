#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RAG 检索管线：查询改写、混合检索（dense + BM25 + RRF）、可选 cross-encoder 重排。"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from typing import Any, Optional, Sequence

import numpy as np

from doc_qa_assistant import TextChunk, search

logger = logging.getLogger(__name__)

_reranker_lock = threading.Lock()
_reranker_model: Any = None
_reranker_id: str = ""

REWRITE_ENABLED = os.environ.get("RAG_ENABLE_REWRITE", "").strip().lower() in {"1", "true", "yes"}
HYBRID_ENABLED = os.environ.get("RAG_ENABLE_HYBRID", "1").strip().lower() not in {"0", "false", "no", "off"}
RERANK_ENABLED = os.environ.get("RAG_ENABLE_RERANK", "").strip().lower() in {"1", "true", "yes"}
RERANK_MODEL = os.environ.get("RAG_RERANK_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2").strip()
HYBRID_POOL = max(8, min(96, int(os.environ.get("RAG_HYBRID_CANDIDATES", "36"))))
DENSE_PER_QUERY = max(4, min(32, int(os.environ.get("RAG_DENSE_PER_QUERY", "12"))))
BM25_TOP = max(4, min(64, int(os.environ.get("RAG_BM25_TOP", "20"))))
RRF_K = max(10, min(120, int(os.environ.get("RAG_RRF_K", "60"))))


def _tokenize(s: str) -> list[str]:
    s = (s or "").strip().lower()
    if not s:
        return []
    parts = re.findall(r"[\u4e00-\u9fff]|[a-z0-9]+(?:\.[a-z0-9]+)?", s, flags=re.I)
    return parts if parts else [s[:64]]


def _rewrite_queries_llm(invoke_llm, question: str) -> list[str]:
    """返回 1～3 条检索用查询；失败则仅原问。"""
    q0 = question.strip()
    if not q0:
        return []
    if not REWRITE_ENABLED:
        return [q0]
    prompt = (
        "将用户问题改写成 1 到 3 条适合文档检索的查询（可同义扩展或拆分子问题）。"
        "只输出 JSON 数组，元素为字符串，不要其它文字。\n"
        f"用户问题：{q0}"
    )
    try:
        text, _route = invoke_llm(prompt, 256, json_object=False)
        if not text:
            return [q0]
        arr = json.loads(text)
        if isinstance(arr, list):
            out = [str(x).strip() for x in arr if str(x).strip()]
            if 1 <= len(out) <= 5:
                return out[:3]
    except Exception as e:
        logger.debug("query rewrite skip: %s", e)
    return [q0]


def _bm25_scores_for_doc(
    doc_tf: dict[str, int],
    doc_len: int,
    avgdl: float,
    query_terms: list[str],
    idf: dict[str, float],
    k1: float = 1.5,
    b: float = 0.75,
) -> float:
    score = 0.0
    for t in query_terms:
        if t not in doc_tf:
            continue
        tf = doc_tf[t]
        idf_t = idf.get(t, 0.0)
        denom = tf + k1 * (1.0 - b + b * (doc_len / max(avgdl, 1e-6)))
        score += idf_t * (tf * (k1 + 1.0)) / denom
    return score


def _bm25_top_indices(chunks: Sequence[TextChunk], queries: list[str], top_n: int) -> list[int]:
    import math

    tokenized_corpus = [_tokenize(c.text) for c in chunks]
    if not any(tokenized_corpus):
        return []
    N = len(tokenized_corpus)
    dl = [max(1, len(d)) for d in tokenized_corpus]
    avgdl = sum(dl) / max(N, 1)
    df: dict[str, int] = {}
    for doc in tokenized_corpus:
        for t in set(doc):
            df[t] = df.get(t, 0) + 1
    idf: dict[str, float] = {}
    for t, dfi in df.items():
        idf[t] = math.log((N - dfi + 0.5) / (dfi + 0.5) + 1.0)

    scores_agg = np.zeros(N, dtype=np.float64)
    for q in queries:
        q_terms = _tokenize(q)
        if not q_terms:
            continue
        for i, doc in enumerate(tokenized_corpus):
            tf: dict[str, int] = {}
            for t in doc:
                tf[t] = tf.get(t, 0) + 1
            scores_agg[i] = max(
                scores_agg[i],
                _bm25_scores_for_doc(tf, dl[i], avgdl, q_terms, idf),
            )
    order = np.argsort(-scores_agg)[:top_n]
    return [int(i) for i in order if scores_agg[int(i)] > 1e-9]


def _dense_best_scores(
    queries: list[str],
    chunks: Sequence[TextChunk],
    index,
    st,
    k_each: int,
) -> dict[int, float]:
    """chunk_index -> max cosine score across queries."""
    best: dict[int, float] = {}
    id_to_i = {c.chunk_id: i for i, c in enumerate(chunks) if c.chunk_id}
    for q in queries:
        for score, ch in search(q, chunks, index, st, top_k=min(k_each, len(chunks))):
            idx = id_to_i.get(ch.chunk_id)
            if idx is None:
                try:
                    idx = chunks.index(ch)
                except ValueError:
                    continue
            prev = best.get(idx, -1.0)
            if float(score) > prev:
                best[idx] = float(score)
    return best


def _rrf_fuse(rank_lists: list[list[int]], k: int = RRF_K) -> list[int]:
    scores: dict[int, float] = {}
    for ranks in rank_lists:
        for r, idx in enumerate(ranks):
            scores[idx] = scores.get(idx, 0.0) + 1.0 / (k + r + 1)
    return sorted(scores.keys(), key=lambda i: -scores[i])


def _get_reranker(model_id: str):
    global _reranker_model, _reranker_id
    with _reranker_lock:
        if _reranker_model is not None and _reranker_id == model_id:
            return _reranker_model
        from sentence_transformers import CrossEncoder

        _reranker_model = CrossEncoder(model_id)
        _reranker_id = model_id
        return _reranker_model


def _rerank(
    question: str,
    candidates: list[tuple[int, TextChunk]],
    top_k: int,
) -> list[tuple[float, TextChunk]]:
    if not RERANK_ENABLED or not candidates:
        return candidates[:top_k]
    try:
        ce = _get_reranker(RERANK_MODEL)
        pairs = [(question, c.text[:800]) for _, c in candidates]
        raw = ce.predict(pairs, show_progress_bar=False)
        scores = np.asarray(raw, dtype=np.float32).reshape(-1)
        order = np.argsort(-scores)[:top_k]
        return [(float(scores[i]), candidates[i][1]) for i in order]
    except Exception as e:
        logger.warning("rerank disabled: %s", e)
        return candidates[:top_k]


def hybrid_retrieve(
    question: str,
    chunks: list[TextChunk],
    index,
    st,
    invoke_llm,
    final_top_k: int,
) -> list[tuple[float, TextChunk]]:
    """返回 (score, chunk) 列表，score 为融合/重排后的相关度（越大越好）。"""
    if not chunks:
        return []

    queries = _rewrite_queries_llm(invoke_llm, question)
    if not queries:
        queries = [question.strip()]

    if not HYBRID_ENABLED:
        return search(queries[0], chunks, index, st, top_k=final_top_k)

    dense_best = _dense_best_scores(queries, chunks, index, st, DENSE_PER_QUERY)
    dense_rank = sorted(dense_best.keys(), key=lambda i: -dense_best[i])[:HYBRID_POOL]

    bm25_rank = _bm25_top_indices(chunks, queries, BM25_TOP)

    if bm25_rank:
        fused_order = _rrf_fuse([dense_rank, bm25_rank])[:HYBRID_POOL]
    else:
        fused_order = dense_rank[:HYBRID_POOL]

    candidates: list[tuple[int, TextChunk]] = []
    seen: set[int] = set()
    for idx in fused_order:
        if idx < 0 or idx >= len(chunks) or idx in seen:
            continue
        seen.add(idx)
        sc = dense_best.get(idx, 0.01)
        candidates.append((idx, chunks[idx]))

    reranked = _rerank(question.strip(), candidates, min(final_top_k, len(candidates)))
    return reranked


def parse_citation_refs(answer: str) -> list[int]:
    """解析回答中的 [1]、[2] 引用序号。"""
    found = re.findall(r"\[(\d{1,2})\]", answer or "")
    out: list[int] = []
    for x in found:
        try:
            n = int(x)
            if 1 <= n <= 50 and n not in out:
                out.append(n)
        except ValueError:
            continue
    return out


def build_citations(
    answer: str,
    hits: list[tuple[float, TextChunk]],
) -> list[dict[str, Any]]:
    """将 [n] 映射到 hits 列表（1-based）。"""
    refs = parse_citation_refs(answer)
    out: list[dict[str, Any]] = []
    for r in refs:
        if 1 <= r <= len(hits):
            score, ch = hits[r - 1]
            out.append(
                {
                    "ref": r,
                    "score": float(score),
                    "source_label": f"{ch.source} · {ch.page_label}",
                    "excerpt": (ch.text or "")[:400],
                    "chunk_id": ch.chunk_id,
                    "kb_note_id": ch.kb_note_id or None,
                    "kb_attachment_id": ch.kb_attachment_id or None,
                    "session_file_id": ch.session_file_id or None,
                }
            )
    return out
