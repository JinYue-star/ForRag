#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""问答与测验相关的 LLM 调用、Prompt 构造及判分逻辑（与 HTTP 层解耦）。"""

from __future__ import annotations

import json
import logging
import re
import traceback
from typing import Any, Optional

import rag_pipeline

from doc_qa_assistant import (
    generate_answer,
    generate_answer_via_api,
    load_llm,
    route_generation,
)

from rag_api import settings
from rag_api.schemas import (
    CitationItem,
    HitItem,
    QAResponse,
    QuizBundlePublic,
    QuizGradeItemResult,
    QuizGradeResponse,
    QuizItemPublic,
    QuizSegmentSpec,
)

_local_llm_cache: dict[str, tuple[object, object]] = {}


def compact_text(text: str, limit: int = 220) -> str:
    cleaned = " ".join(text.split()).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3].rstrip() + "..."


def _normalize_hit_scores_to_pct(raw: list[float]) -> list[float]:
    """将本批检索分数映射到 0～100 的展示百分比（越大越相关）。

    - 余弦 / FAISS 内积（L2 归一化向量）：常见落在 [-1, 1] 或 [0, 1]，按线性比例换算。
    - 重排器等无界分数：对本批 top 结果做 min-max 归一化。
    """
    if not raw:
        return []
    lo = min(raw)
    hi = max(raw)
    eps = 1e-12
    if hi <= 1.0 + 1e-5 and lo >= -1.0 - 1e-5:
        if lo >= -eps:
            return [max(0.0, min(100.0, float(s) * 100.0)) for s in raw]
        return [max(0.0, min(100.0, (float(s) + 1.0) * 50.0)) for s in raw]
    if hi - lo < eps:
        return [100.0] * len(raw)
    return [max(0.0, min(100.0, (float(s) - lo) / (hi - lo) * 100.0)) for s in raw]


def build_fallback_answer(question: str, hits: list[tuple[float, object]]) -> str:
    if not hits:
        return "没有检索到相关内容，请换个问法或上传更相关的文档。"

    lead_score, lead_chunk = hits[0]
    lines = [
        "当前使用快速检索模式，未调用本地大模型。",
        f"最相关内容来自 `{lead_chunk.source}` 的 `{lead_chunk.page_label}`。",
        f"参考摘要：{compact_text(lead_chunk.text, limit=280)}",
        f"相关度：{lead_score:.4f}",
    ]
    if len(hits) > 1:
        refs = "；".join(f"{chunk.source} {chunk.page_label}" for _, chunk in hits[:3])
        lines.append(f"其他参考：{refs}")
    lines.append(f"问题：{question}")
    return "\n".join(lines)


def build_strategy_prompt(question: str, hits: list[tuple[float, object]]) -> str:
    if not hits:
        return (
            "你是文档问答助手。\n"
            "当前没有检索到任何有效文档片段。请明确告诉用户证据不足，"
            "并建议重新上传更相关的文档或换个问法。\n\n"
            f"用户问题：{question}"
        )

    evidence_blocks = []
    for idx, (score, chunk) in enumerate(hits, start=1):
        evidence_blocks.append(
            "\n".join(
                [
                    f"[{idx}] 来源: {chunk.source}",
                    f"位置: {chunk.page_label}",
                    f"说明: {chunk.meta}",
                    f"相关度: {score:.4f}",
                    f"内容: {compact_text(chunk.text, limit=settings.PROMPT_CHUNK_CHAR_LIMIT)}",
                ]
            )
        )

    evidence_text = "\n\n".join(evidence_blocks)
    return (
        "你是一个严谨的文档问答助手。"
        "请只依据下面给出的检索证据回答，不要引入证据外事实。\n\n"
        "【输出格式】必须严格按下述结构书写：单独一行以 Answer: 开头写结论；"
        "随后每条证据单独一行，以 - Evidence k 开头（k 为正整数，与检索证据块序号一致）。\n"
        "Answer:\n"
        "<一到两句直接结论；关键分句或句末写半角 [1]、[2] 等，编号须与下方 Evidence 的 k 一致，且仅引用实际用到的证据>\n"
        "\n"
        "- Evidence 1 (与检索块「来源」一致的文件名, 与「位置」一致的位置描述): "
        "一两句说明如何支撑结论，勿大段照抄；行末再写 [1]。\n"
        "- Evidence 2 (...): ... [2]\n"
        "依此类推；仅列出在 Answer 中实际引用过的证据。\n"
        "若连续两条来自同一文件、仅位置不同，从第二条起可将括号内写为：(same file, 与「位置」字段一致)，仍以英文 same file 开头。\n\n"
        "【规则】\n"
        "1. Evidence 序号 k 与检索证据块标题行的 [k] 必须一致；[k] 须为半角方括号。\n"
        "2. 文件名、位置必须与对应检索证据中的来源、位置一致，不得编造。\n"
        "3. 若证据不充分，在 Answer 中明确写不确定或无法从材料确认，并说明缺什么；Evidence 可列出相关但不足的片段。\n"
        "4. 不要复述整段原文，不要编造结论。\n\n"
        f"用户问题：{question}\n\n"
        f"检索证据：\n{evidence_text}"
    )


def invoke_llm(
    user_msg: str,
    max_new_tokens: Optional[int],
    *,
    json_object: bool = False,
) -> tuple[str, str]:
    route = route_generation(has_api_key=bool(settings.SERVER_API_KEY))
    if route == "api":
        try:
            answer = generate_answer_via_api(
                api_key=settings.SERVER_API_KEY,
                api_model=settings.SERVER_API_MODEL,
                api_base=settings.SERVER_API_BASE,
                user_msg=user_msg,
                max_new_tokens=max_new_tokens,
                stream=False,
                json_object=json_object,
            )
            return (answer or "").strip(), "api"
        except Exception:
            traceback.print_exc()
            return "", "api_error"

    if settings.ENABLE_LOCAL_LLM:
        try:
            cache_key = f"{settings.SERVER_MODEL_ID}::{settings.SERVER_LLM_HUB}::{int(settings.SERVER_LOW_MEMORY)}"
            if cache_key not in _local_llm_cache:
                _local_llm_cache[cache_key] = load_llm(
                    model_id=settings.SERVER_MODEL_ID,
                    hub=settings.SERVER_LLM_HUB,
                    cpu_half=settings.SERVER_LOW_MEMORY,
                )
            local_model, tokenizer = _local_llm_cache[cache_key]
            answer = generate_answer(
                model=local_model,
                tokenizer=tokenizer,
                user_msg=user_msg,
                max_new_tokens=max_new_tokens,
                stream=False,
            )
            return (answer or "").strip(), "local"
        except Exception:
            traceback.print_exc()
            return "", "local_error"

    return "", "fallback"


def generate_strategy_answer(
    question: str,
    hits: list[tuple[float, object]],
    max_new_tokens: Optional[int],
) -> tuple[str, str]:
    prompt = build_strategy_prompt(question, hits)
    text, route = invoke_llm(prompt, max_new_tokens)
    if text:
        return text, route
    fb = build_fallback_answer(question, hits)
    if route.startswith("api"):
        return fb, "api_fallback"
    if route.startswith("local"):
        return fb, "local_fallback"
    return fb, "fallback"


def hits_are_relevant(hits: list[tuple[float, object]]) -> bool:
    if not hits:
        return False
    return float(hits[0][0]) >= settings.KB_MIN_SCORE


def build_no_kb_prompt(question: str) -> str:
    return (
        "你是可靠的助手。用户已上传文档作为知识库，但检索结果表明：当前知识库中未找到与问题直接相关、"
        "或相关度足够高的片段。\n"
        "请先简要说明这一情况（一至两段话）。然后基于你的通用知识回答用户问题；若问题强依赖未提供的专有材料，"
        "请明确说明无法从通用知识确认。请勿编造文档引用。\n\n"
        f"用户问题：{question}"
    )


def generate_general_knowledge_answer(
    question: str,
    max_new_tokens: Optional[int],
) -> tuple[str, str]:
    prompt = build_no_kb_prompt(question)
    text, route = invoke_llm(prompt, max_new_tokens)
    if text:
        return text, route
    return (
        "【说明】知识库中未检索到与问题足够相关的内容，且当前未配置可用的语言模型（请设置 DASHSCOPE_API_KEY "
        "或启用本地模型 RAG_ENABLE_LOCAL_LLM），无法生成基于通用知识的回答。"
    ), "fallback"


def llm_available() -> bool:
    return bool(settings.SERVER_API_KEY) or settings.ENABLE_LOCAL_LLM


def extract_json_object(text: str) -> Optional[dict]:
    raw = (text or "").strip()
    if not raw:
        return None
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
    if fence:
        raw = fence.group(1).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None


def extract_json_items_loose(text: str) -> Optional[dict]:
    raw = text or ""
    d = extract_json_object(raw)
    if isinstance(d, dict) and isinstance(d.get("items"), list):
        return d
    for needle in ('"items"', "'items'"):
        idx = raw.find(needle)
        if idx < 0:
            continue
        sub = raw[idx:]
        br = sub.find("[")
        if br < 0:
            continue
        start = idx + br
        depth = 0
        for i in range(start, len(raw)):
            c = raw[i]
            if c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
                if depth == 0:
                    try:
                        arr = json.loads(raw[start : i + 1])
                        if isinstance(arr, list):
                            return {"items": arr}
                    except json.JSONDecodeError:
                        pass
                    break
    return None


def quiz_type_counts_tf_single_multi(n: int) -> tuple[int, int, int]:
    if n <= 0:
        return 0, 0, 0
    if n == 1:
        return 0, 1, 0
    if n == 2:
        return 1, 1, 0
    tf_n = max(1, n // 4)
    multi_n = max(1, n // 4)
    single_n = n - tf_n - multi_n
    if single_n < 1:
        single_n = 1
        rem = n - single_n
        tf_n = rem // 2
        multi_n = rem - tf_n
    return tf_n, single_n, multi_n


def coerce_quiz_index(val: Any) -> Optional[int]:
    if isinstance(val, bool):
        return None
    if isinstance(val, int):
        return val
    if isinstance(val, float) and val == int(val):
        return int(val)
    if isinstance(val, str) and val.strip().lstrip("-").isdigit():
        return int(val.strip())
    return None


def normalize_quiz_items_flexible(data: dict, n: int, forbidden_questions: set[str]) -> Optional[list[dict]]:
    items = data.get("items")
    if not isinstance(items, list) or len(items) != n:
        return None
    out: list[dict] = []
    seen_q: set[str] = set()
    for it in items:
        if not isinstance(it, dict):
            return None
        t = str(it.get("type", "")).lower().strip()
        if t not in ("tf", "single", "multi"):
            return None
        q = str(it.get("question", "")).strip()
        if not q:
            return None
        q_key = q.casefold()
        if q_key in seen_q:
            return None
        seen_q.add(q_key)
        if q_key in forbidden_questions:
            return None
        row: dict = {"type": t, "question": q}
        if t == "tf":
            opts = it.get("options")
            if not isinstance(opts, list) or len(opts) != 2:
                return None
            raw_opts = [str(x).strip() for x in opts]
            a0, a1 = raw_opts[0].casefold(), raw_opts[1].casefold()
            if {a0, a1} != {"true", "false"}:
                return None
            ci_raw = coerce_quiz_index(it.get("correct_index"))
            if ci_raw is None or ci_raw not in (0, 1):
                return None
            correct_word = raw_opts[ci_raw].casefold()
            if correct_word not in ("true", "false"):
                return None
            row["options"] = ["True", "False"]
            row["correct_index"] = 0 if correct_word == "true" else 1
        elif t == "single":
            opts = it.get("options")
            if not isinstance(opts, list) or len(opts) < 2 or len(opts) > 6:
                return None
            row["options"] = [str(x).strip() for x in opts]
            ci = coerce_quiz_index(it.get("correct_index"))
            if ci is None or ci < 0 or ci >= len(row["options"]):
                return None
            row["correct_index"] = ci
        else:
            opts = it.get("options")
            if not isinstance(opts, list) or len(opts) < 3 or len(opts) > 8:
                return None
            row["options"] = [str(x).strip() for x in opts]
            cis_raw = it.get("correct_indices")
            if not isinstance(cis_raw, list) or len(cis_raw) < 2:
                return None
            cis: list[int] = []
            for x in cis_raw:
                j = coerce_quiz_index(x)
                if j is None or j < 0 or j >= len(row["options"]):
                    return None
                cis.append(j)
            cis = sorted(set(cis))
            if len(cis) < 2:
                return None
            row["correct_indices"] = cis
        out.append(row)
    return out


def build_quiz_generation_prompt_v3(
    total_n: int,
    segment_blocks: str,
    hits: list[tuple[float, object]],
    forbidden_lines: list[str],
) -> str:
    tf_n, single_n, multi_n = quiz_type_counts_tf_single_multi(total_n)
    evidence_blocks = []
    for idx, (score, chunk) in enumerate(hits[:5], start=1):
        evidence_blocks.append(
            f"[{idx}] source:{chunk.source} location:{chunk.page_label} relevance:{score:.4f}\n"
            f"{compact_text(chunk.text, limit=500)}"
        )
    evidence_text = "\n\n".join(evidence_blocks)
    forbid = "\n".join(f"- {line[:200]}" for line in forbidden_lines[:80]) if forbidden_lines else "(none)"
    return (
        "You are an expert educator. Design a quiz that helps learners **understand concepts**, not just memorize phrases. "
        "Use clear English. Each question should have a concise stem, test one main idea, and include a short "
        "explanation-worthy distractor rationale (implicitly, via plausible wrong options).\n\n"
        f"You MUST output exactly {total_n} items in total, matching the per-segment counts in the segment block below.\n"
        "Question type counts (must match exactly):\n"
        f'- type "tf" (True/False): {tf_n} items\n'
        f'- type "single" (single-choice): {single_n} items — provide exactly 4 options unless only 2 are pedagogically justified; prefer 4.\n'
        f'- type "multi" (multiple-select): {multi_n} items — provide 4–6 options and field "correct_indices": a sorted JSON array '
        "of distinct 0-based indices (at least two correct options).\n\n"
        "JSON schema per item:\n"
        '- tf: {"type":"tf","question":"...","options":["True","False"],"correct_index":0 or 1}\n'
        '- single: {"type":"single","question":"...","options":["A","B","C","D"],"correct_index":0..3}\n'
        '- multi: {"type":"multi","question":"...","options":[...],"correct_indices":[0,2]}\n\n'
        "Rules: Ground every question in the segment text and retrieval evidence; avoid duplicate or near-duplicate stems; "
        "do not repeat any question similar to these prior stems:\n"
        f"{forbid}\n\n"
        "Output ONE JSON object only, no markdown fences, no commentary. Shape: "
        '{"items":[ ... exactly '
        f"{total_n} "
        "objects ... ]}\n\n"
        "### Segment requirements (counts per assistant excerpt)\n"
        f"{segment_blocks}\n\n"
        "### Retrieval evidence\n"
        f"{evidence_text}"
    )


def fallback_quiz_bundle_from_hits(
    hits: list[tuple[float, object]],
    resolved_segments: list[tuple[str, str, int]],
    forbidden_lower: set[str],
    total_n: int,
) -> Optional[dict]:
    snippets: list[str] = []
    for _mid, excerpt, _cnt in resolved_segments:
        t = compact_text(excerpt, 500)
        if t:
            snippets.append(t)
    for _score, chunk in hits:
        t = compact_text(getattr(chunk, "text", "") or "", 500)
        if t:
            snippets.append(t)
    if not snippets:
        return None
    tf_n, single_n, multi_n = quiz_type_counts_tf_single_multi(total_n)
    single_n += multi_n
    items: list[dict] = []
    ti = si = 0
    for idx in range(total_n):
        base = snippets[idx % len(snippets)]
        if ti < tf_n:
            ti += 1
            excerpt = compact_text(base, 320)
            q = (
                f"True or False: The following accurately reflects the source material: "
                f'"{excerpt}"'
            )
            if len(base) > 320:
                q += " …"
            if q.casefold() in forbidden_lower:
                q = f"{q} (item {idx + 1})"
            items.append({"type": "tf", "question": q, "options": ["True", "False"], "correct_index": 0})
        else:
            si += 1
            opts = [
                f"Main takeaway: {compact_text(base, 120)}",
                "A plausible but unsupported inference",
                "An irrelevant detail",
                "The opposite of the correct conclusion",
            ]
            q = f"Single choice — what does the excerpt best support?\n【Excerpt】{compact_text(base, 360)}"
            if len(base) > 360:
                q += "…"
            if q.casefold() in forbidden_lower:
                q = f"{q} (#{idx + 1})"
            items.append({"type": "single", "question": q, "options": opts, "correct_index": 0})

    normalized = normalize_quiz_items_flexible({"items": items}, total_n, forbidden_lower)
    if not normalized:
        return None
    return {"items": normalized}


def merge_quiz_segments(segments: list[QuizSegmentSpec]) -> list[QuizSegmentSpec]:
    acc: dict[str, int] = {}
    for s in segments:
        k = s.message_id.strip()
        acc[k] = acc.get(k, 0) + s.count
    return [QuizSegmentSpec(message_id=k, count=v) for k, v in acc.items()]


def generate_quiz_bundle_for_segments(
    hits: list[tuple[float, object]],
    resolved_segments: list[tuple[str, str, int]],
    forbidden_lower: set[str],
    total_n: int,
) -> tuple[Optional[dict], str]:
    if total_n <= 0:
        return None, "bad_total"
    last_fail = "llm_empty"
    prev_texts = [t for t in forbidden_lower if t]
    lines: list[str] = []
    for i, (mid, excerpt, cnt) in enumerate(resolved_segments, start=1):
        lines.append(
            f"Segment {i} (message_id={mid}): write exactly {cnt} question(s) grounded in:\n"
            f"{compact_text(excerpt, limit=900)}"
        )
    segment_blocks = "\n\n".join(lines)
    base_prompt = build_quiz_generation_prompt_v3(total_n, segment_blocks, hits, prev_texts)
    max_tok = min(12000, max(512, settings.QUIZ_GEN_MAX_TOKENS, 400 + total_n * 320))

    if llm_available():
        for attempt in range(3):
            extra = ""
            if attempt == 1:
                extra = (
                    "\n\n[Retry] Previous output failed validation. Output ONE JSON object only; "
                    f'top-level key "items" must be an array of length exactly {total_n}. '
                    "No markdown, no prose outside JSON."
                )
            elif attempt == 2:
                extra = (
                    f'\n\n[Retry] Minimal output: {{"items":[...]}} with {total_n} objects. '
                    'Types: tf | single | multi only; multi must include "correct_indices" (array of ints).'
                )
            prompt = base_prompt + extra
            use_json_mode = attempt == 0
            text, _route = invoke_llm(prompt, max_tok, json_object=use_json_mode)
            if not (text or "").strip():
                last_fail = "llm_empty"
                continue
            data = extract_json_object(text) or extract_json_items_loose(text)
            if not isinstance(data, dict):
                last_fail = "bad_json"
                continue
            items = normalize_quiz_items_flexible(data, total_n, forbidden_lower)
            if not items:
                last_fail = "bad_items"
                continue
            return {"items": items}, "ok"

    fb = fallback_quiz_bundle_from_hits(hits, resolved_segments, forbidden_lower, total_n)
    if fb:
        logging.warning(
            "quiz/generate: fallback items (tf/single) from retrieval — LLM JSON failed or API error"
        )
        return fb, "ok"

    if not llm_available():
        return None, "no_llm"
    return None, last_fail


def quiz_generation_fail_detail(code: str) -> str:
    if code == "no_llm":
        return "无法生成测验：未配置可用的语言模型（请设置 DASHSCOPE_API_KEY 或 RAG_ENABLE_LOCAL_LLM=1）。"
    if code == "bad_total":
        return "无法生成测验：题目总数无效。"
    if code == "llm_empty":
        return (
            "无法生成测验：大模型无返回或 API 调用失败。请检查 DASHSCOPE_API_KEY 是否有效、网络与账户额度，"
            "或稍后重试。排障可设置环境变量 RAG_DEBUG_ERRORS=1 查看服务端日志。"
        )
    if code == "bad_json":
        return "无法生成测验：模型返回内容无法解析为 JSON。请减少题目数量或稍后重试。"
    if code == "bad_items":
        return (
            "无法生成测验：题目格式未通过校验（题量与各题型数量须符合要求）。请减少单次出题数量或稍后重试。"
        )
    return "无法生成测验（请配置 DASHSCOPE_API_KEY 或本地模型，或稍后重试）。"


def build_quiz_public(quiz_id: str, items: list[dict]) -> QuizBundlePublic:
    pub_items: list[QuizItemPublic] = []
    for i, it in enumerate(items):
        t = str(it.get("type", "single")).lower()
        opts = None
        if t in ("tf", "single", "multi"):
            opts = [str(x) for x in (it.get("options") or [])]
        pub_items.append(
            QuizItemPublic(
                index=i,
                type=t,
                question=str(it.get("question", "")).strip(),
                options=opts,
            )
        )
    return QuizBundlePublic(quiz_id=quiz_id, items=pub_items)


def run_qa_pipeline(
    question: str,
    hits: list[tuple[float, object]],
    max_new_tokens: Optional[int],
) -> QAResponse:
    kb_rel = hits_are_relevant(hits)
    no_kb_notice: Optional[str] = None

    if not kb_rel:
        answer, route = generate_general_knowledge_answer(question, max_new_tokens)
        no_kb_notice = "当前知识库中未检索到与问题足够相关的片段，以下为基于模型通用知识的回答（仅供参考，非文档结论）。"
    else:
        answer, route = generate_strategy_answer(question, hits, max_new_tokens)

    raw_scores = [float(s) for s, _ in hits]
    pct_scores = _normalize_hit_scores_to_pct(raw_scores)
    hit_items = [
        HitItem(
            score=pct_scores[i],
            source=chunk.source,
            page_label=chunk.page_label,
            meta=chunk.meta,
            content=compact_text(chunk.text, limit=360),
            chunk_id=getattr(chunk, "chunk_id", "") or "",
            kb_note_id=(getattr(chunk, "kb_note_id", "") or None),
            kb_attachment_id=(getattr(chunk, "kb_attachment_id", "") or None),
            session_file_id=(getattr(chunk, "session_file_id", "") or None),
        )
        for i, (_, chunk) in enumerate(hits)
    ]

    cite_raw = rag_pipeline.build_citations(answer, hits) if kb_rel else []
    pct_by_ref = {idx + 1: pct_scores[idx] for idx in range(len(pct_scores))}
    for row in cite_raw:
        r = int(row.get("ref", 0))
        if r in pct_by_ref:
            row["score"] = pct_by_ref[r]
    citations = [CitationItem(**c) for c in cite_raw]

    return QAResponse(
        answer=answer,
        route=route,
        hits=hit_items,
        kb_relevant=kb_rel,
        no_kb_notice=no_kb_notice,
        quiz=None,
        citations=citations,
    )


def format_correct_for_item(it: dict) -> str:
    t = str(it.get("type", "")).lower()
    opts = it.get("options") or []
    if t == "tf" or t == "single":
        ci = coerce_quiz_index(it.get("correct_index"))
        if ci is not None and 0 <= ci < len(opts):
            return f"{chr(65 + ci)}. {opts[ci]}"
        return str(it.get("correct_index", ""))
    if t == "multi":
        cis = it.get("correct_indices") or []
        parts: list[str] = []
        if isinstance(cis, list):
            js = sorted({coerce_quiz_index(x) for x in cis if coerce_quiz_index(x) is not None})
            for j in js:
                if 0 <= j < len(opts):
                    parts.append(f"{chr(65 + j)}. {opts[j]}")
        return "; ".join(parts)
    return ""


def grade_quiz_with_llm(payload: dict, user_answers: list[str]) -> QuizGradeResponse:
    items = payload.get("items") or []
    n = len(items)
    if n == 0:
        return QuizGradeResponse(
            total_score=0.0,
            max_total_score=100.0,
            items=[],
            analysis="测验题目为空，无法判分。",
        )
    per_hint = round(100.0 / n, 2)
    grading_input = []
    for i, it in enumerate(items):
        ua = user_answers[i] if i < len(user_answers) else ""
        grading_input.append(
            {
                "index": i,
                "type": it.get("type"),
                "question": it.get("question"),
                "standard": format_correct_for_item(it),
                "user_answer": (ua or "").strip(),
            }
        )
    prompt = (
        f"You are an expert grader. Score exactly {n} items; total must sum to 100 points across items.\n"
        "Types: tf / single / multi. For tf and single, compare the user answer string to the keyed option text. "
        "For multi, the user answer is a comma-separated list of option indices (e.g. \"0,2\"); award full credit only "
        "if the set matches correct_indices; partial credit if clearly justified.\n"
        f"Target max_score per item ≈ {per_hint} (adjust so item max_scores sum to 100).\n"
        "Output ONE JSON object only:\n"
        '{"total_score":number,"max_total_score":100,"items":['
        '{"index":0,"question":"echo the stem text","question_type":"tf|single|multi","score":number,"max_score":number,'
        '"user_answer":"...","correct_answer":"...","comment":"brief feedback in English"},...],'
        '"analysis":"overall feedback and study tips in English"}\n\n'
        f"Problems and answers: {json.dumps(grading_input, ensure_ascii=False)}"
    )
    text, _r = invoke_llm(prompt, settings.GRADE_MAX_TOKENS)
    parsed = extract_json_object(text or "")
    per_default = 100.0 / n
    if isinstance(parsed, dict) and isinstance(parsed.get("items"), list):
        try:
            total = float(parsed.get("total_score", 0))
            max_tot = float(parsed.get("max_total_score", 100))
            analysis = str(parsed.get("analysis", "") or "").strip() or (text or "（模型未返回解析）")
            out_items: list[QuizGradeItemResult] = []
            for row in parsed["items"]:
                if not isinstance(row, dict):
                    continue
                idx = int(row.get("index", 0))
                stem = str(row.get("question", "") or "").strip()
                if not stem and 0 <= idx < len(items):
                    stem = str(items[idx].get("question") or "").strip()
                out_items.append(
                    QuizGradeItemResult(
                        index=idx,
                        question=stem,
                        question_type=str(row.get("question_type", "")),
                        score=float(row.get("score", 0)),
                        max_score=float(row.get("max_score", per_default)),
                        user_answer=str(row.get("user_answer", "")),
                        correct_answer=str(row.get("correct_answer", "")),
                        comment=str(row.get("comment", "")),
                    )
                )
            out_items.sort(key=lambda x: x.index)
            return QuizGradeResponse(
                total_score=total,
                max_total_score=max_tot,
                items=out_items,
                analysis=analysis,
            )
        except (TypeError, ValueError):
            pass
    return QuizGradeResponse(
        total_score=0.0,
        max_total_score=100.0,
        items=[],
        analysis=text or "判分结果解析失败，请稍后重试。",
    )
