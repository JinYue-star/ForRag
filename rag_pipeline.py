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

from doc_qa_assistant import TextChunk, chunk_embed_text, search

logger = logging.getLogger(__name__)


def _flag_on(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


_reranker_lock = threading.Lock()
_reranker_model: Any = None
_reranker_id: str = ""
# 运行期是否真正跑通重排（模型加载/推理失败后置 False），用于门控选择正确的分数口径。
_rerank_runtime_ok: bool = True

# 查询扩写（multi-query）与 HyDE 默认开启：对"学生口语化提问 vs 课件术语"错配最有效（各仅一次 LLM 调用）。
REWRITE_ENABLED = _flag_on("RAG_ENABLE_REWRITE", True)
HYDE_ENABLED = _flag_on("RAG_ENABLE_HYDE", True)
HYBRID_ENABLED = _flag_on("RAG_ENABLE_HYBRID", True)
# Cross-encoder 重排默认开启：对"检索到但排序不佳"直接改善，是业界标配。
RERANK_ENABLED = _flag_on("RAG_ENABLE_RERANK", True)
# 纠错式重查（CRAG 轻量版）：首轮命中偏弱时，自动改写一次查询并重检，取更优者。硬上限 1 次。
CORRECTIVE_ENABLED = _flag_on("RAG_ENABLE_CORRECTIVE", True)
# 默认多语种重排器 bge-reranker-v2-m3（适配中英混排的英文课，标准 CrossEncoder 接口即可加载）。
# 备选：Alibaba-NLP/gte-multilingual-reranker-base（更小更快，需 trust_remote_code）。
RERANK_MODEL = os.environ.get("RAG_RERANK_MODEL", "BAAI/bge-reranker-v2-m3").strip()
HYBRID_POOL = max(8, min(96, int(os.environ.get("RAG_HYBRID_CANDIDATES", "36"))))
DENSE_PER_QUERY = max(4, min(32, int(os.environ.get("RAG_DENSE_PER_QUERY", "12"))))
BM25_TOP = max(4, min(64, int(os.environ.get("RAG_BM25_TOP", "20"))))
RRF_K = max(10, min(120, int(os.environ.get("RAG_RRF_K", "60"))))


def rerank_active() -> bool:
    """当前检索是否以重排概率作为命中分（决定下游门控用哪套阈值）。

    重排模型加载/推理失败时会退回稠密余弦分，此时返回 False，
    使 CRAG 门控改用余弦阈值，避免把余弦分错当成 0~1 概率误判相关性。
    """
    return RERANK_ENABLED and HYBRID_ENABLED and _rerank_runtime_ok


def _tokenize(s: str) -> list[str]:
    s = (s or "").strip().lower()
    if not s:
        return []
    parts = re.findall(r"[\u4e00-\u9fff]|[a-z0-9]+(?:\.[a-z0-9]+)?", s, flags=re.I)
    return parts if parts else [s[:64]]


def _expand_queries_llm(invoke_llm, question: str) -> dict[str, Any]:
    """一次 LLM 调用同时完成 Multi-query 改写 + HyDE 假想答案。

    返回 {"queries": [..], "hyde": "..."}。失败则仅用原问、无 HyDE。
    - Multi-query（RAG-Fusion）：把问题同义扩展/拆分为多条子查询，覆盖更全。
    - HyDE（Gao et al., 2022）：让 LLM 先写一段"假想答案"，用它做稠密检索，缓解问句与资料措辞不一致。
    """
    q0 = (question or "").strip()
    if not q0:
        return {"queries": [], "hyde": ""}
    if not REWRITE_ENABLED and not HYDE_ENABLED:
        return {"queries": [q0], "hyde": ""}
    want_hyde = HYDE_ENABLED
    want_rewrite = REWRITE_ENABLED
    prompt = (
        "You help a document retrieval system. Given a user question, output ONLY a JSON object, no prose.\n"
        + (
            '- "queries": an array of 1-3 concise search queries (synonym-expanded or split sub-questions) that best retrieve relevant course material.\n'
            if want_rewrite
            else '- "queries": an array with just the original question.\n'
        )
        + (
            '- "hypothetical": a short (2-4 sentence) hypothetical passage that would answer the question, as if quoted from course notes.\n'
            if want_hyde
            else ""
        )
        + 'Shape: {"queries":[...]' + (',"hypothetical":"..."' if want_hyde else "") + "}\n"
        f"User question: {q0}"
    )
    queries = [q0]
    hyde = ""
    try:
        text, _route = invoke_llm(prompt, 320, json_object=True)
        data = json.loads(text) if text else None
        if isinstance(data, dict):
            arr = data.get("queries")
            if isinstance(arr, list):
                out = [str(x).strip() for x in arr if str(x).strip()]
                if out:
                    queries = out[:3]
            if want_hyde:
                hyde = str(data.get("hypothetical") or "").strip()[:800]
    except Exception as e:
        logger.debug("query expand skip: %s", e)
    if q0 not in queries:
        queries = [q0] + queries
    return {"queries": queries[:4], "hyde": hyde}


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

    tokenized_corpus = [_tokenize(chunk_embed_text(c)) for c in chunks]
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

        trust = os.environ.get("RAG_RERANK_TRUST_REMOTE_CODE", "").strip().lower() in {"1", "true", "yes", "on"}
        try:
            _reranker_model = CrossEncoder(model_id, trust_remote_code=trust)
        except TypeError:
            # 老版本 sentence-transformers 不支持该参数
            _reranker_model = CrossEncoder(model_id)
        _reranker_id = model_id
        return _reranker_model


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -30.0, 30.0)))


def _rerank(
    question: str,
    candidates: list[tuple[float, int, TextChunk]],
    top_k: int,
) -> list[tuple[float, TextChunk]]:
    """重排：用 cross-encoder 打分并排序，分数经 sigmoid 归一到 0~1（相关度概率）。

    关闭或异常时退回融合顺序，沿用稠密余弦分，保证与旧行为兼容。
    candidates: (dense_score, chunk_index, chunk)
    """
    if not candidates:
        return []
    global _rerank_runtime_ok
    if not RERANK_ENABLED:
        return [(float(sc), ch) for sc, _idx, ch in candidates[:top_k]]
    try:
        ce = _get_reranker(RERANK_MODEL)
        pairs = [(question, chunk_embed_text(c)[:1000]) for _sc, _idx, c in candidates]
        raw = ce.predict(pairs, show_progress_bar=False)
        probs = _sigmoid(np.asarray(raw, dtype=np.float32).reshape(-1))
        order = np.argsort(-probs)[:top_k]
        _rerank_runtime_ok = True
        return [(float(probs[i]), candidates[i][2]) for i in order]
    except Exception as e:
        # 加载/推理失败：退回融合顺序 + 稠密余弦分，并标记本进程重排不可用。
        _rerank_runtime_ok = False
        logger.warning("rerank unavailable, falling back to fusion order: %s", e)
        return [(float(sc), ch) for sc, _idx, ch in candidates[:top_k]]


def _corrective_trigger() -> float:
    """低于该 top 分即触发一次纠错重查。重排开启用概率口径，否则用余弦口径。"""
    from rag_api import settings

    override = os.environ.get("RAG_CORRECTIVE_TRIGGER")
    if override and override.strip():
        try:
            return float(override)
        except ValueError:
            pass
    if rerank_active():
        return float(settings.RERANK_STRONG_SCORE)
    return float(settings.KB_SINGLE_HIT_MIN_SCORE)


def _corrective_rewrite_llm(invoke_llm, question: str, weak_hits: list[tuple[float, TextChunk]]) -> str:
    """首轮偏弱时，让 LLM 产出一条更具体、更贴近课件术语的检索查询（单次调用）。"""
    snippets = " ".join(chunk_embed_text(c)[:200] for _s, c in weak_hits[:2])
    prompt = (
        "A first retrieval attempt for a student's question returned only weak matches from course "
        "materials. Rewrite the question into ONE improved search query: more specific, disambiguated, "
        "and using precise terminology likely present in lecture notes. Output ONLY JSON, no prose.\n"
        'Shape: {"query":"..."}\n'
        f"Question: {question}\n"
        f"Weak snippets: {snippets[:600]}"
    )
    try:
        text, _route = invoke_llm(prompt, 120, json_object=True)
        data = json.loads(text) if text else None
        if isinstance(data, dict):
            return str(data.get("query") or "").strip()[:400]
    except Exception as e:
        logger.debug("corrective rewrite skip: %s", e)
    return ""


def hybrid_retrieve(
    question: str,
    chunks: list[TextChunk],
    index,
    st,
    invoke_llm,
    final_top_k: int,
    allow_correction: bool = True,
) -> list[tuple[float, TextChunk]]:
    """返回 (score, chunk) 列表，score 为融合/重排后的相关度（越大越好）。

    allow_correction: 内部递归守卫。仅首轮为 True；纠错重查时置 False，确保最多一次重查（硬上限）。
    """
    if not chunks:
        return []

    expanded = _expand_queries_llm(invoke_llm, question)
    queries = expanded.get("queries") or [question.strip()]
    hyde = expanded.get("hyde") or ""
    # HyDE 假想答案作为额外的稠密检索查询（不参与 BM25，避免长文本引入噪声）。
    dense_queries = queries + ([hyde] if hyde else [])

    if not HYBRID_ENABLED:
        return search(queries[0], chunks, index, st, top_k=final_top_k)

    dense_best = _dense_best_scores(dense_queries, chunks, index, st, DENSE_PER_QUERY)
    dense_rank = sorted(dense_best.keys(), key=lambda i: -dense_best[i])[:HYBRID_POOL]

    bm25_rank = _bm25_top_indices(chunks, queries, BM25_TOP)

    if bm25_rank:
        fused_order = _rrf_fuse([dense_rank, bm25_rank])[:HYBRID_POOL]
    else:
        fused_order = dense_rank[:HYBRID_POOL]

    candidates: list[tuple[float, int, TextChunk]] = []
    seen: set[int] = set()
    for idx in fused_order:
        if idx < 0 or idx >= len(chunks) or idx in seen:
            continue
        seen.add(idx)
        sc = dense_best.get(idx, 0.01)
        candidates.append((float(sc), idx, chunks[idx]))

    reranked = _rerank(question.strip(), candidates, min(final_top_k, len(candidates)))

    # 纠错式重查（硬上限 1 次）：首轮 top 分偏弱时改写查询再检一次，取 top 分更高者。
    if CORRECTIVE_ENABLED and allow_correction and reranked:
        top = float(reranked[0][0])
        if top < _corrective_trigger():
            new_q = _corrective_rewrite_llm(invoke_llm, question, reranked)
            if new_q and new_q.strip().lower() != question.strip().lower():
                alt = hybrid_retrieve(
                    new_q, chunks, index, st, invoke_llm, final_top_k, allow_correction=False
                )
                if alt and float(alt[0][0]) > top:
                    return alt
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
