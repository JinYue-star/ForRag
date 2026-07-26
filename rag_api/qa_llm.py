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


SOURCE_SECTION_TITLE = "证据来源："


def strip_source_section(answer: str) -> str:
    """去掉系统追加的来源清单，便于重复计算覆盖率或再次修订。"""
    text = answer or ""
    marker = re.search(rf"(?m)^\s*{re.escape(SOURCE_SECTION_TITLE)}\s*$", text)
    if not marker:
        return text.strip()
    return text[: marker.start()].rstrip()


def _format_source_line(ref: int, chunk: object) -> str:
    location = (getattr(chunk, "page_label", "") or "").strip()
    source = (getattr(chunk, "source", "") or "").strip() or "未知来源"
    return f"[{ref}] {source} · {location}" if location else f"[{ref}] {source}"


def build_source_section(
    answer: str,
    hits: list[tuple[float, object]],
    *,
    fallback_all: bool = False,
) -> str:
    """按回答中实际出现的 [k]，用检索元数据生成可核对的来源清单。

    ``fallback_all=True``：正文没有有效引用时，列出全部检索命中（软放行场景）。
    """
    refs = rag_pipeline.parse_citation_refs(answer)
    lines: list[str] = []
    for ref in sorted(ref for ref in refs if 1 <= ref <= len(hits)):
        lines.append(_format_source_line(ref, hits[ref - 1][1]))
    if not lines and fallback_all and hits:
        lines = [_format_source_line(i + 1, chunk) for i, (_, chunk) in enumerate(hits)]
    if not lines:
        return ""
    return "\n".join([SOURCE_SECTION_TITLE, *lines])


def append_source_section(
    answer: str,
    hits: list[tuple[float, object]],
    *,
    fallback_all: bool = False,
) -> str:
    body = strip_source_section(answer)
    section = build_source_section(body, hits, fallback_all=fallback_all)
    if not section:
        return body
    return f"{body}\n\n{section}"


def build_service_unavailable_answer(
    _hits: list[tuple[float, object]],
    *,
    evidence_sufficient: bool,
) -> str:
    if evidence_sufficient:
        lead = "已检索到可能相关的课程资料，但 AI 服务当前不可用，无法生成经过证据约束的可靠答案。"
    else:
        lead = "课程资料不足以可靠回答该问题，且 AI 服务当前不可用，无法生成通识性答案。"
    lines = [
        f"【暂时无法回答】{lead}",
        "请稍后重试，或补充更直接相关的课程材料。",
    ]
    return "\n".join(lines)


def _evidence_body(chunk: object, idx: int, parent_first_ref: dict[str, int]) -> str:
    """证据正文：命中子块时展开其所在页/幻灯片（parent-child retrieval）。

    子块适合嵌入与重排（语义集中），但作答需要上下文，因此提示词里给父级整段。
    同一父级被多次命中时只展开一次，后续命中只给子块片段，避免重复占用上下文。
    """
    limit = settings.PROMPT_CHUNK_CHAR_LIMIT
    text = getattr(chunk, "text", "") or ""
    parent_id = (getattr(chunk, "parent_id", "") or "").strip()
    parent_text = (getattr(chunk, "parent_text", "") or "").strip()
    if parent_id and parent_text:
        first = parent_first_ref.get(parent_id)
        if first is None:
            parent_first_ref[parent_id] = idx
            return compact_text(parent_text, limit=limit)
        return f"（与 [{first}] 同一位置，此处仅列该处的相关片段）{compact_text(text, limit=limit)}"
    return compact_text(text, limit=limit)


def build_strategy_prompt(question: str, hits: list[tuple[float, object]]) -> str:
    if not hits:
        return build_no_kb_prompt(question)

    evidence_blocks = []
    parent_first_ref: dict[str, int] = {}
    for idx, (score, chunk) in enumerate(hits, start=1):
        evidence_blocks.append(
            "\n".join(
                [
                    f"[{idx}] 来源: {chunk.source}",
                    f"位置: {chunk.page_label}",
                    f"说明: {chunk.meta}",
                    f"相关度: {score:.4f}",
                    f"内容: {_evidence_body(chunk, idx, parent_first_ref)}",
                ]
            )
        )

    evidence_text = "\n\n".join(evidence_blocks)
    return (
        "你面向【课程学习、作业与学术答辩备询】等场景。身份：严谨的文档依据型助手。回答须学术化表达："
        "用语准确、逻辑清楚；先给可核对的核心结论，再作必要展开；避免口语化与模糊断言。\n"
        "只依据下述检索证据作答；对证据中未出现的信息不得当作事实写出。若作合理归纳或推断，"
        "须点明是「从材料可归纳/可推出」，且不得外推到证据无法支撑的程度。\n\n"
        "【学术写作要求】\n"
        "• 对术语可在证据范围内简要界定；若多段证据有细微表述差异，应如实反映或说明以何者为准、为何如此。\n"
        "• 若问题涉及比较、条件或适用边界，写清「适用于…」「前提为…」等，避免过宽结论。\n"
        "• 若证据仅部分覆盖问题，须明确写「材料未充分说明/未涉及…」，可指出尚需何种材料，勿补写虚构细节。\n"
        "• 直接陈述学术内容，不要写「证据[k]中提到…」「材料[k]显示…」这类元描述，编号只出现在句末标注处。\n"
        "• 公式与符号一律用纯文本书写，如 fs > 2*fmax、O(n log n)；不得使用 LaTeX 记法或 \\( \\)、$$ 等包裹。\n\n"
        "【输出格式】直接给出连贯的学术性回答，不要使用「结论」「说明」「证据边界」这类分段标题，"
        "也不要输出小标题、前言或收尾寄语。\n"
        "• 开头一到两句给出可核对的核心判断，随后自然展开必要的定义、条件、机制与适用边界；"
        "内容较多时可用 1. 2. 3. 分点，但每点仍是完整陈述句，不加点内标题。\n"
        "• **每个事实性句子或分句末尾都必须紧跟其依据的证据编号**，如「……结论[1]。」「……条件[2][3]。」"
        "编号与检索证据块序号一致，仅标实际用到的证据。\n"
        f"• 不要自行编写「{SOURCE_SECTION_TITLE}」，也不要罗列文件名或页码——来源清单由系统按真实元数据自动追加。\n\n"
        "【规则】\n"
        "1. 引用编号 k 必须与检索证据块 [k] 对应，方括号为半角；句级引用要覆盖每条事实性陈述，"
        "不要把多句合并后只在段末标一次；纯过渡句可不标。\n"
        "2. 不得编写、猜测或改写文件名与页码位置。\n"
        "3. 证据不足时，在正文相应位置直陈「材料未说明…」，不要另起标题或段落。\n"
        "4. 勿复述全段原文，勿编造引文、数据、结论。\n\n"
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
    return "", route


# 句末标点或换行处断句；英文句点仅在其后是空白时才算句末，避免切开 0.5、fs 2.0 这类数字。
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?])|(?<=\.)(?=\s)|\n")

# 声明材料缺口的句子（如「材料未说明…」）陈述的是证据不存在，无需也无法引用，故不计入分母。
_EVIDENCE_GAP_RE = re.compile(
    r"(材料|资料|文档|讲义|课件|证据)[^。！？!?]{0,20}(未|没有|不足|无从)"
    r"|未(充分)?(说明|涉及|提及|给出|讨论|定义|覆盖)"
    r"|无法(仅)?(从|凭|依据)?[^。！？!?]{0,16}(确认|确定|判断|得出)"
)


def citation_coverage(answer: str, evidence_count: int) -> tuple[float, int, int]:
    """计算正文中事实性句子携带有效 [n] 引用的比例。

    系统追加的来源清单不是模型断言，声明材料缺口的句子也无从引用，二者都不计入分母。
    以句号级标点切句：分号连接的从句属同一断言，句末带 [k] 即视为已引用。
    """
    text = strip_source_section(answer)
    answer_match = re.search(r"(?im)^\s*Answer\s*:\s*", text)
    if answer_match:
        text = text[answer_match.end() :]
    boundary_match = re.search(r"(?m)^\s*证据边界\s*[:：]", text)
    if boundary_match:
        text = text[: boundary_match.start()]
    evidence_match = re.search(r"(?im)^\s*-\s*Evidence\s+\d+\b", text)
    if evidence_match:
        text = text[: evidence_match.start()]
    segments = _SENTENCE_SPLIT_RE.split(text)
    factual: list[str] = []
    for segment in segments:
        cleaned = re.sub(r"\[\d+\]", "", segment)
        cleaned = re.sub(r"^[\s#>*\-•\d.()（）]+", "", cleaned).strip()
        if len(cleaned) < 6 or cleaned.casefold() in {"answer:", "answer"}:
            continue
        if _EVIDENCE_GAP_RE.search(cleaned):
            continue
        factual.append(segment)
    if not factual:
        return 0.0, 0, 0
    covered = 0
    for segment in factual:
        refs = [int(ref) for ref in re.findall(r"\[(\d+)\]", segment)]
        if any(1 <= ref <= evidence_count for ref in refs):
            covered += 1
    return covered / len(factual), covered, len(factual)


def repair_citation_coverage(
    question: str,
    hits: list[tuple[float, object]],
    draft: str,
    max_new_tokens: Optional[int],
) -> tuple[str, str]:
    """要求模型仅重写现有答案，补齐句级引用；不允许新增事实。"""
    prompt = (
        f"{build_strategy_prompt(question, hits)}\n\n"
        "【引用校验修订】下方草稿未通过句级引用覆盖率检查。请按上述格式完整重写一次："
        "每个事实性句子或分句都必须在句末带至少一个有效 [n]；"
        "只能使用上面的检索证据和已有编号，不得新增事实，不得输出修订说明。\n\n"
        f"待修订草稿：\n{strip_source_section(draft)}"
    )
    return invoke_llm(prompt, max_new_tokens)


def _grounding_thresholds() -> tuple[float, float, float, float, float]:
    """返回 (multi_min, single_min, multi_strong, single_strong, second_support)。

    重排开启时命中分是 0~1 概率，用 RERANK_* 阈值；否则用余弦 KB_* 阈值。
    """
    if rag_pipeline.scores_from_rerank():
        return (
            settings.RERANK_MIN_SCORE,
            settings.RERANK_SINGLE_HIT_MIN_SCORE,
            settings.RERANK_STRONG_SCORE,
            settings.RERANK_SINGLE_HIT_STRONG_SCORE,
            settings.RERANK_SECOND_HIT_SCORE,
        )
    return (
        settings.KB_MIN_SCORE,
        settings.KB_SINGLE_HIT_MIN_SCORE,
        settings.KB_STRONG_SCORE,
        settings.KB_SINGLE_HIT_STRONG_SCORE,
        settings.KB_SECOND_HIT_SCORE,
    )


def classify_grounding(hits: list[tuple[float, object]]) -> str:
    """CRAG 风格的检索质量评估（Yan et al., 2024 的轻量实现）。

    返回 "grounded" | "weak" | "none"：
    - none：置信度不足 → 走通识回答，避免把不相关材料硬套成 RAG 结论。
    - weak：存在候选资料但不足以支撑结论 → 不进入文档型 RAG，改走通识回答。
    - grounded：足够可信。
    """
    if not hits:
        return "none"
    top = float(hits[0][0])
    multi_min, single_min, multi_strong, single_strong, second_support = _grounding_thresholds()
    if top < multi_min:
        return "none"
    if len(hits) == 1 and top < single_min:
        return "none"
    if len(hits) == 1:
        return "grounded" if top >= single_strong else "weak"
    second = float(hits[1][0])
    if top >= single_strong:
        return "grounded"
    if top >= multi_strong and second >= second_support:
        return "grounded"
    return "weak"


def is_boundary_evidence(hits: list[tuple[float, object]]) -> bool:
    """只让接近强阈值的 weak 命中进入 LLM 充分性判断，避免无谓调用。"""
    if not hits:
        return False
    _multi_min, _single_min, multi_strong, single_strong, second_support = (
        _grounding_thresholds()
    )
    margin = (
        settings.RERANK_SUFFICIENCY_MARGIN
        if rag_pipeline.scores_from_rerank()
        else settings.KB_SUFFICIENCY_MARGIN
    )
    top = float(hits[0][0])
    if len(hits) == 1:
        return top >= single_strong - margin
    second = float(hits[1][0])
    return top >= single_strong - margin or (
        top >= multi_strong - margin and second >= second_support - margin
    )


def hits_are_relevant(hits: list[tuple[float, object]]) -> bool:
    return classify_grounding(hits) == "grounded"


def refine_evidence(hits: list[tuple[float, object]]) -> list[tuple[float, object]]:
    """CRAG 知识精炼：丢弃相对最高分过低的候选块，减少喂给 LLM 的噪声证据。

    始终至少保留 1 条；对相关度接近最高分的块全部保留。
    """
    if len(hits) <= 1:
        return list(hits)
    top = float(hits[0][0])
    if top <= 0:
        return list(hits)
    floor = top * max(0.0, min(0.95, settings.EVIDENCE_KEEP_RATIO))
    kept = [h for h in hits if float(h[0]) >= floor]
    return kept or [hits[0]]


def reorder_lost_in_the_middle(
    hits: list[tuple[float, object]],
) -> list[tuple[float, object]]:
    """Lost-in-the-Middle（Liu et al., 2023）：把最相关证据放在首尾，最弱的放中间。

    输入需按相关度降序。输出示例（按秩）：0,2,4,…,5,3,1。
    """
    if len(hits) <= 2:
        return list(hits)
    left: list[tuple[float, object]] = []
    right: list[tuple[float, object]] = []
    for i, h in enumerate(hits):
        (left if i % 2 == 0 else right).append(h)
    return left + right[::-1]


def build_no_kb_prompt(question: str) -> str:
    return (
        "【情境】知识库未返回足够相关、可据之作答的文档片段（或仅有一条置信度偏低的命中）。\n"
        "【要求】请直接运用你的通识与学科规范理解作答，以适合【课程、作业与学术答辩备询】的中文学术风格书写：\n"
        "• 结构：可先给出最简明的核心判断或定义，再分点说明（定义—要点—条件/例外—局限）；长答注意层级标题或编号。\n"
        "• 表达：使用准确、可核对的学科用语，必要时在括号中保留标准英文术词；避免口语化、营销式或与问题无关的铺陈。\n"
        "• 诚信：不得编造不存在的论文、数据、标准号或课本文页；不伪造「某文献称…」式引用。若需说明常见通说，可写"
        "「在多数教材/通论中常表述为…」并点明为一般性通识，非用户上传材料。\n"
        "• 边界：对存在学派分歧、或强依赖题设材料而未给出的内容，须标注不确定性或列明需补充的已知条件；"
        "对前沿/政策类问题注明时效性。\n"
        "• 不要依据不存在的知识库命中强行敷衍；与问题强依赖未提供内部材料时，明确说明无法仅凭通识作确定性结论。\n\n"
        "【输出格式】直接给出连贯的学术性回答，不要使用「结论」「说明」「证据边界」这类分段标题。"
        "开头一到两句给出核心判断或定义，随后展开要点、条件与局限；内容较多时可用 1. 2. 3. 分点。\n"
        "• 公式与符号一律用纯文本书写，如 a^2 + b^2 = c^2、fs > 2*fmax、O(n log n)；"
        "不得使用 LaTeX 记法或 \\( \\)、$$ 等包裹。\n"
        "不要使用 [1] 这类引用编号，也不要罗列文件名或页码——本回答没有课程材料依据。\n\n"
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
    return "", route


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


def evaluate_evidence_sufficiency(
    question: str,
    hits: list[tuple[float, object]],
) -> tuple[bool, str, str]:
    """对分数边界内的证据做一次严格充分性判断；失败时安全地返回不足。"""
    if not settings.ENABLE_SUFFICIENCY_JUDGE or not hits:
        return False, "disabled", ""
    evidence = "\n\n".join(
        f"[{idx}] {chunk.source} | {chunk.page_label}\n"
        f"{compact_text(chunk.text, limit=600)}"
        for idx, (_score, chunk) in enumerate(hits[:5], start=1)
    )
    prompt = (
        "You are a strict evidence-sufficiency gate for a course RAG system. "
        "Decide whether the supplied excerpts alone are sufficient to answer every material-dependent "
        "part of the question accurately. Semantic relatedness is not enough. Mark sufficient=false "
        "when evidence is partial, merely topical, ambiguous, conflicting, or missing requested facts. "
        "Do not use your own knowledge to fill gaps. Output ONLY JSON: "
        '{"sufficient":true|false,"reason":"brief reason","missing_aspects":["..."]}.\n\n'
        f"QUESTION:\n{question}\n\nEVIDENCE:\n{evidence}"
    )
    raw, route = invoke_llm(prompt, 400, json_object=True)
    parsed = extract_json_object(raw or "")
    if not isinstance(parsed, dict) or not isinstance(parsed.get("sufficient"), bool):
        return False, route, ""
    reason = str(parsed.get("reason", "") or "").strip()
    missing = parsed.get("missing_aspects")
    if isinstance(missing, list):
        missing_text = "；".join(str(item).strip() for item in missing if str(item).strip())
        if missing_text:
            reason = f"{reason} 缺失：{missing_text}".strip()
    return bool(parsed["sufficient"]), route, reason


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
        # 可选教学元数据（Bloom 认知层级/难度/解析）——存在即透传，缺失不报错。
        bloom = str(it.get("bloom", "") or "").strip().lower()
        if bloom in ("remember", "understand", "apply", "analyze", "evaluate", "create"):
            row["bloom"] = bloom
        diff = str(it.get("difficulty", "") or "").strip().lower()
        if diff in ("easy", "medium", "hard"):
            row["difficulty"] = diff
        expl = str(it.get("explanation", "") or "").strip()
        if expl:
            row["explanation"] = expl[:500]
        out.append(row)
    return out


def _quiz_evidence_origin(chunk: object) -> str:
    if getattr(chunk, "kb_note_id", None) or getattr(chunk, "kb_attachment_id", None):
        return "course_kb"
    if getattr(chunk, "session_file_id", None):
        return "session_file"
    return "source"


def prefer_kb_hits(
    hits: list[tuple[float, object]],
    limit: int = 5,
) -> list[tuple[float, object]]:
    """同一批检索结果内：课程知识库命中优先，会话附件仅作补充。"""
    kb: list[tuple[float, object]] = []
    session: list[tuple[float, object]] = []
    other: list[tuple[float, object]] = []
    for item in hits:
        origin = _quiz_evidence_origin(item[1])
        if origin == "course_kb":
            kb.append(item)
        elif origin == "session_file":
            session.append(item)
        else:
            other.append(item)
    return (kb + other + session)[: max(0, limit)]


def build_quiz_search_query(
    resolved_segments: list[tuple[str, str, int]],
    session_messages: list[dict[str, Any]],
) -> str:
    """用勾选助手回复对应的用户问题 + 回复摘要构造检索查询。"""
    parts: list[str] = []
    selected = {mid for mid, _excerpt, _cnt in resolved_segments}
    ordered = sorted(session_messages, key=lambda m: float(m.get("created_at") or 0))
    for i, m in enumerate(ordered):
        if str(m.get("id") or "") not in selected:
            continue
        for j in range(i - 1, -1, -1):
            if ordered[j].get("role") == "user":
                uq = compact_text(str(ordered[j].get("content") or ""), 220)
                if uq:
                    parts.append(uq)
                break
    for _mid, excerpt, _cnt in resolved_segments:
        t = compact_text(excerpt, 280)
        if t:
            parts.append(t)
    joined = "\n".join(parts).strip()
    return joined[:800] if joined else "course quiz concepts"


def build_quiz_generation_prompt_v3(
    total_n: int,
    segment_blocks: str,
    hits: list[tuple[float, object]],
    forbidden_lines: list[str],
) -> str:
    tf_n, single_n, multi_n = quiz_type_counts_tf_single_multi(total_n)
    evidence_blocks = []
    for idx, (score, chunk) in enumerate(hits[:5], start=1):
        origin = _quiz_evidence_origin(chunk)
        evidence_blocks.append(
            f"[{idx}] origin:{origin} source:{chunk.source} location:{chunk.page_label} "
            f"relevance:{score:.4f}\n{compact_text(chunk.text, limit=500)}"
        )
    evidence_text = "\n\n".join(evidence_blocks) or (
        "(no retrieval hits — ground questions carefully in the selected segment text only)"
    )
    forbid = "\n".join(f"- {line[:200]}" for line in forbidden_lines[:80]) if forbidden_lines else "(none)"
    return (
        "You are an expert educator. Design a quiz that helps learners **understand concepts**, not just memorize phrases. "
        "Use clear English. Each question should have a concise stem and test one main idea.\n\n"
        "### Source priority\n"
        "Selected assistant segments define WHAT topics to test. "
        "Retrieval evidence labeled origin:course_kb is authoritative course material — prefer it for facts and answer keys. "
        "Evidence labeled origin:session_file is optional supplemental material uploaded in this chat; "
        "use it only when it clarifies the same topic, and prefer course_kb if they conflict.\n\n"
        "### Cognitive level (Bloom's taxonomy)\n"
        "Spread items across Bloom levels and TAG each item with a \"bloom\" field "
        '(one of: "remember","understand","apply","analyze","evaluate"). '
        "At least half of the choice items must be higher-order (apply/analyze/evaluate), not mere recall. "
        'Also tag each item with a "difficulty" field ("easy","medium","hard") and a one-sentence "explanation" '
        "of why the correct answer is correct.\n\n"
        "### Distractor quality (over-generate, then filter)\n"
        "For every choice item, FIRST internally brainstorm 5–6 candidate distractors that target common student "
        "misconceptions or partial understanding, THEN keep only the most plausible, discriminating ones as the "
        "final options. Distractors must be homogeneous in length/style with the key and must not be obviously wrong, "
        "joke, or 'all/none of the above' options.\n\n"
        f"You MUST output exactly {total_n} items in total, matching the per-segment counts in the segment block below.\n"
        "Question type counts (must match exactly):\n"
        f'- type "tf" (True/False): {tf_n} items\n'
        f'- type "single" (single-choice): {single_n} items — provide exactly 4 options unless only 2 are pedagogically justified; prefer 4.\n'
        f'- type "multi" (multiple-select): {multi_n} items — provide 4–6 options and field "correct_indices": a sorted JSON array '
        "of distinct 0-based indices (at least two correct options).\n\n"
        "JSON schema per item (bloom/difficulty/explanation are required tags):\n"
        '- tf: {"type":"tf","question":"...","options":["True","False"],"correct_index":0 or 1,"bloom":"understand","difficulty":"easy","explanation":"..."}\n'
        '- single: {"type":"single","question":"...","options":["A","B","C","D"],"correct_index":0..3,"bloom":"apply","difficulty":"medium","explanation":"..."}\n'
        '- multi: {"type":"multi","question":"...","options":[...],"correct_indices":[0,2],"bloom":"analyze","difficulty":"hard","explanation":"..."}\n\n'
        "Rules: Ground every question in the segment topics and retrieval evidence (course_kb first); "
        "avoid duplicate or near-duplicate stems; "
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


def generate_quiz_bundle_from_student_questions(
    student_questions: list[str],
    total_n: int,
) -> tuple[Optional[dict], str]:
    """根据教师知识库中保存的学生提问，整理生成带答案的测验题。"""
    qs = [str(q).strip() for q in student_questions if str(q).strip()]
    if total_n <= 0:
        return None, "bad_total"
    if not qs:
        return None, "bad_total"

    tf_n, single_n, multi_n = quiz_type_counts_tf_single_multi(total_n)
    listed = "\n".join(f"{i}. {compact_text(q, limit=400)}" for i, q in enumerate(qs[:40], start=1))
    prompt = (
        "You are an expert course assessment designer. Using the STUDENT QUESTIONS below as themes "
        "(misconceptions, hard topics, and what students asked), write a mixed quiz with clear answer keys.\n"
        f"Produce exactly {total_n} items: tf={tf_n}, single={single_n}, multi={multi_n}.\n"
        "Rules:\n"
        '- type "tf": options must be ["True","False"] and "correct_index" 0 or 1.\n'
        '- type "single": 4 options, "correct_index" 0..3.\n'
        '- type "multi": 4–6 options, "correct_indices" sorted unique ints (at least 2).\n'
        "Ground questions in the academic themes of the student questions; do NOT copy them verbatim as stems when possible—"
        "rewrite into clear exam items. English preferred if source is English; match the source language otherwise.\n"
        'Output ONE JSON object only: {"items":[...]} with no markdown.\n\n'
        f"STUDENT QUESTIONS:\n{listed}\n"
    )
    max_tok = min(12000, max(512, settings.QUIZ_GEN_MAX_TOKENS, 400 + total_n * 320))
    last_fail = "llm_empty"
    if llm_available():
        for attempt in range(3):
            extra = ""
            if attempt:
                extra = (
                    f'\n\n[Retry] Output only {{"items":[...]}} length {total_n}. '
                    "Types tf|single|multi only; multi needs correct_indices."
                )
            text, _route = invoke_llm(prompt + extra, max_tok, json_object=(attempt == 0))
            if not (text or "").strip():
                last_fail = "llm_empty"
                continue
            data = extract_json_object(text) or extract_json_items_loose(text)
            if not isinstance(data, dict):
                last_fail = "bad_json"
                continue
            items = normalize_quiz_items_flexible(data, total_n, set())
            if not items:
                last_fail = "bad_items"
                continue
            return {"items": items}, "ok"

    # Offline / LLM-failure fallback: simple single-choice from student stems
    items = []
    for i in range(total_n):
        stem = qs[i % len(qs)]
        items.append(
            {
                "type": "single",
                "question": (
                    f"[{i + 1}] Which statement best matches this student topic: "
                    f"{compact_text(stem, limit=160)}?"
                ),
                "options": [
                    "A precise course-aligned statement of the concept",
                    "An unrelated definition from another chapter",
                    "A claim that contradicts the lecture notes",
                    "A purely anecdotal personal opinion",
                ],
                "correct_index": 0,
            }
        )
    normalized = normalize_quiz_items_flexible({"items": items}, total_n, set())
    if normalized:
        logging.warning("quiz/generate-from-questions: using fallback items (LLM unavailable or invalid)")
        return {"items": normalized}, "ok"
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
    label = classify_grounding(hits)
    sufficiency_checked = False
    sufficiency_sufficient: Optional[bool] = None
    sufficiency_reason: Optional[str] = None
    if (
        label == "weak"
        and settings.ENABLE_SUFFICIENCY_JUDGE
        and is_boundary_evidence(hits)
    ):
        sufficiency_checked = True
        sufficiency_sufficient, _judge_route, sufficiency_reason = evaluate_evidence_sufficiency(
            question,
            hits,
        )
    kb_rel = label == "grounded" or sufficiency_sufficient is True
    response_label = "weak_sufficient" if label == "weak" and kb_rel else label
    no_kb_notice: Optional[str] = None
    service_unavailable = False
    citation_coverage_score: Optional[float] = None

    if kb_rel:
        # 只有高置信证据才允许进入文档约束型 RAG。
        used_hits = reorder_lost_in_the_middle(refine_evidence(list(hits)))
        answer, route = generate_strategy_answer(question, used_hits, max_new_tokens)
        answer_kind = "grounded"
    else:
        # weak 与 none 均不使用课程片段组织结论，只请求明确标注的通识回答。
        used_hits = reorder_lost_in_the_middle(list(hits))
        answer, route = generate_general_knowledge_answer(question, max_new_tokens)
        answer_kind = "general"
        no_kb_notice = (
            "课程资料证据不足；以下为 AI 通识回答，不代表课程材料结论，引用时请另行核对可靠来源。"
        )

    if kb_rel and answer:
        citation_coverage_score, _covered, _total = citation_coverage(answer, len(used_hits))
        if citation_coverage_score < settings.MIN_CITATION_COVERAGE:
            repaired, repair_route = repair_citation_coverage(
                question,
                used_hits,
                answer,
                max_new_tokens,
            )
            repaired_coverage, _rc, _rt = citation_coverage(repaired or "", len(used_hits))
            if repaired and repaired_coverage >= settings.MIN_CITATION_COVERAGE:
                answer = repaired
                route = f"{repair_route}_citation_repaired"
                citation_coverage_score = repaired_coverage
            else:
                # Soft policy: still return the course-grounded draft (+ sources).
                # Prefer the repaired text when it improves coverage; never blank the answer.
                if repaired and repaired.strip() and repaired_coverage >= (citation_coverage_score or 0.0):
                    answer = repaired
                    citation_coverage_score = repaired_coverage
                    route = f"{repair_route}_citation_partial"
                else:
                    route = f"{route}_citation_partial"
                answer_kind = "grounded"
                no_kb_notice = (
                    "部分句子的引用标注可能不完整；以下仍依据课程检索片段生成，文末来源供核对。"
                )
        if answer_kind == "grounded":
            # 来源清单由检索元数据生成，模型不参与，避免文件名或页码被编造。
            # 引用覆盖不足时：即使正文缺 [n]，仍附上本次检索命中列表供核对。
            answer = append_source_section(
                answer,
                used_hits,
                fallback_all=(citation_coverage_score or 0.0) < settings.MIN_CITATION_COVERAGE,
            )

    if not answer:
        service_unavailable = True
        answer_kind = "unavailable"
        answer = build_service_unavailable_answer(
            used_hits,
            evidence_sufficient=kb_rel,
        )
        if route.startswith("api"):
            route = "api_unavailable"
        elif route.startswith("local"):
            route = "local_unavailable"
        else:
            route = "llm_unavailable"
        no_kb_notice = (
            "AI 服务当前不可用，系统已停止生成结论；下列候选来源仅供核对，不构成答案。"
            if kb_rel
            else "课程资料证据不足且 AI 服务当前不可用，系统无法可靠回答。"
        )

    raw_scores = [float(s) for s, _ in used_hits]
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
        for i, (_, chunk) in enumerate(used_hits)
    ]

    cite_raw = (
        rag_pipeline.build_citations(answer, used_hits)
        if kb_rel and answer_kind == "grounded" and not service_unavailable
        else []
    )
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
        grounding_label=response_label,
        answer_kind=answer_kind,
        service_unavailable=service_unavailable,
        sufficiency_checked=sufficiency_checked,
        sufficiency_sufficient=sufficiency_sufficient,
        sufficiency_reason=sufficiency_reason,
        citation_coverage=citation_coverage_score,
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
