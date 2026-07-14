#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RAGAS 风格的轻量评估闭环（离线，进程内直跑真实检索/生成管线）。

思路参考 RAGAS（Es et al., 2023）：用 LLM-as-judge 量化
  - faithfulness         回答是否被检索到的证据支持（抗幻觉）
  - answer_relevancy     回答是否切题
  - context_precision    检索到的证据中"相关"的占比
  - context_recall       （需 ground_truth）标准答案要点被证据覆盖的比例
  - correctness          （需 ground_truth）命题级正确性，抓 faithfulness 漏掉的"grounded but wrong"

用途：每次改动检索/嵌入/重排/prompt 前后各跑一次，用同一评估集对比是否变好，
避免"凭感觉"调参。这是给全班用的产品最该补齐的一环。

用法（在 conda forrag 环境）：
  python tools/rag_eval.py --docs <课程材料目录> --eval tools/eval_set.example.json --out report.json

依赖：复用项目现有管线与 DASHSCOPE_API_KEY（评审与作答同一 LLM）。不引入 ragas 包。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from statistics import mean
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# 评估默认不清理磁盘缓存，便于复用索引。
os.environ.setdefault("RAG_CLEAR_CACHE_ON_SHUTDOWN", "0")

# 必须先导入 settings（内部 load_dotenv 把 .env 写入 os.environ），
# 再导入 rag_pipeline —— 后者在模块导入时即读取 RAG_RERANK_MODEL / RAG_ENABLE_RERANK 等环境变量，
# 顺序颠倒会导致 .env 配置读不到、退回默认重排模型。
from rag_api import settings  # noqa: E402
import rag_pipeline  # noqa: E402
from doc_qa_assistant import build_or_load_index  # noqa: E402
from rag_api.qa_llm import extract_json_object, invoke_llm, run_qa_pipeline  # noqa: E402

DOC_EXTS = {
    ".pdf", ".docx", ".doc", ".txt", ".md", ".html",
    ".xlsx", ".xls", ".pptx", ".ppt", ".csv",
    ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tiff",
}


def _collect_docs(docs_dir: Path) -> list[Path]:
    if docs_dir.is_file():
        return [docs_dir]
    out: list[Path] = []
    for p in sorted(docs_dir.rglob("*")):
        if p.is_file() and p.suffix.lower() in DOC_EXTS:
            out.append(p)
    return out


def _judge(prompt: str) -> Optional[dict]:
    text, _route = invoke_llm(prompt, 512, json_object=True)
    return extract_json_object(text or "")


def _score_faithfulness(question: str, answer: str, contexts: list[str]) -> Optional[float]:
    ctx = "\n\n".join(f"[{i+1}] {c}" for i, c in enumerate(contexts)) or "(none)"
    d = _judge(
        "You are a strict RAG evaluator. Decompose the ANSWER into atomic factual claims. "
        "For each claim decide if it is supported by the CONTEXT. "
        'Output ONLY JSON: {"total":int,"supported":int}.\n\n'
        f"QUESTION: {question}\n\nCONTEXT:\n{ctx}\n\nANSWER:\n{answer}"
    )
    if not d:
        return None
    try:
        total = float(d.get("total", 0))
        supported = float(d.get("supported", 0))
        if total <= 0:
            return 1.0
        return max(0.0, min(1.0, supported / total))
    except (TypeError, ValueError):
        return None


def _score_answer_relevancy(question: str, answer: str) -> Optional[float]:
    d = _judge(
        "Rate how directly the ANSWER addresses the QUESTION on a 0.0-1.0 scale "
        "(1.0 = fully on-topic and complete; 0.0 = irrelevant). "
        'Output ONLY JSON: {"score":float}.\n\n'
        f"QUESTION: {question}\n\nANSWER:\n{answer}"
    )
    if not d:
        return None
    try:
        return max(0.0, min(1.0, float(d.get("score", 0))))
    except (TypeError, ValueError):
        return None


def _score_context_precision(question: str, contexts: list[str]) -> Optional[float]:
    if not contexts:
        return 0.0
    ctx = "\n\n".join(f"[{i+1}] {c}" for i, c in enumerate(contexts))
    d = _judge(
        "For each retrieved CONTEXT snippet, decide if it is relevant to answering the QUESTION. "
        'Output ONLY JSON: {"relevant_indices":[1-based ints]}.\n\n'
        f"QUESTION: {question}\n\nCONTEXT:\n{ctx}"
    )
    if not d:
        return None
    try:
        rel = d.get("relevant_indices") or []
        rel = {int(x) for x in rel if isinstance(x, (int, float))}
        return max(0.0, min(1.0, len(rel) / len(contexts)))
    except (TypeError, ValueError):
        return None


def _score_correctness(question: str, answer: str, ground_truth: str) -> Optional[float]:
    """命题级正确性判官：把 ANSWER 拆成原子命题，逐条与 GROUND TRUTH 比对。

    补 faithfulness 抓不到的"grounded but wrong"——回答被检索证据"支持"但与标准答案相悖时，
    faithfulness 仍高，但 correctness 会低。score = correct / (correct + incorrect)。
    """
    d = _judge(
        "You are a strict grader. Decompose the ANSWER into atomic factual claims. For each claim decide, "
        "relative to the GROUND TRUTH, whether it is correct, incorrect, or unverifiable (not addressed by "
        "the ground truth). "
        'Output ONLY JSON: {"total":int,"correct":int,"incorrect":int}.\n\n'
        f"QUESTION: {question}\n\nGROUND TRUTH:\n{ground_truth}\n\nANSWER:\n{answer}"
    )
    if not d:
        return None
    try:
        correct = float(d.get("correct", 0))
        incorrect = float(d.get("incorrect", 0))
        denom = correct + incorrect
        if denom <= 0:
            return None
        return max(0.0, min(1.0, correct / denom))
    except (TypeError, ValueError):
        return None


def _score_context_recall(ground_truth: str, contexts: list[str]) -> Optional[float]:
    ctx = "\n\n".join(f"[{i+1}] {c}" for i, c in enumerate(contexts)) or "(none)"
    d = _judge(
        "Decompose the GROUND TRUTH answer into atomic statements. For each, decide if it can be "
        "attributed to the CONTEXT. "
        'Output ONLY JSON: {"total":int,"covered":int}.\n\n'
        f"GROUND TRUTH:\n{ground_truth}\n\nCONTEXT:\n{ctx}"
    )
    if not d:
        return None
    try:
        total = float(d.get("total", 0))
        covered = float(d.get("covered", 0))
        if total <= 0:
            return None
        return max(0.0, min(1.0, covered / total))
    except (TypeError, ValueError):
        return None


def _avg(vals: list[Optional[float]]) -> Optional[float]:
    xs = [v for v in vals if isinstance(v, (int, float))]
    return round(mean(xs), 4) if xs else None


def main() -> int:
    ap = argparse.ArgumentParser(description="RAGAS-style offline evaluation for the RAG pipeline")
    ap.add_argument("--docs", required=True, help="课程材料目录或单个文件")
    ap.add_argument("--eval", required=True, help="评估集 JSON：[{question, ground_truth?}]")
    ap.add_argument("--top-k", type=int, default=settings.MAX_TOP_K)
    ap.add_argument("--max-new-tokens", type=int, default=700)
    ap.add_argument("--limit", type=int, default=0, help="只评估前 N 条（0 表示全部）")
    ap.add_argument("--out", default="rag_eval_report.json")
    args = ap.parse_args()

    if not settings.SERVER_API_KEY:
        print("[warn] 未配置 DASHSCOPE_API_KEY：作答与评审都需要 LLM，结果不可用。", file=sys.stderr)

    docs = _collect_docs(Path(args.docs).expanduser())
    if not docs:
        print(f"[error] 未在 {args.docs} 找到可解析文档", file=sys.stderr)
        return 2
    eval_items = json.loads(Path(args.eval).read_text(encoding="utf-8"))
    if not isinstance(eval_items, list) or not eval_items:
        print("[error] 评估集需为非空 JSON 数组", file=sys.stderr)
        return 2
    if args.limit and args.limit > 0:
        eval_items = eval_items[: args.limit]

    print(f"[info] 构建索引：{len(docs)} 个文件，嵌入模型 {settings.SERVER_EMBED_MODEL}")
    chunks, _emb, index, st = build_or_load_index(docs, settings.SERVER_EMBED_MODEL)
    print(f"[info] 索引就绪：{len(chunks)} 个块。开始评估 {len(eval_items)} 条问题…")

    results: list[dict[str, Any]] = []
    for i, item in enumerate(eval_items, start=1):
        question = str(item.get("question", "")).strip()
        gt = str(item.get("ground_truth", "") or "").strip()
        if not question:
            continue
        hits = rag_pipeline.hybrid_retrieve(
            question, chunks, index, st,
            lambda prompt, max_tok, **kw: invoke_llm(prompt, max_tok, **kw),
            max(1, int(args.top_k)),
        )
        resp = run_qa_pipeline(question=question, hits=hits, max_new_tokens=args.max_new_tokens)
        contexts = [h.content for h in resp.hits]
        row = {
            "question": question,
            "answer": resp.answer,
            "route": resp.route,
            "kb_relevant": resp.kb_relevant,
            "n_contexts": len(contexts),
            "faithfulness": _score_faithfulness(question, resp.answer, contexts),
            "answer_relevancy": _score_answer_relevancy(question, resp.answer),
            "context_precision": _score_context_precision(question, contexts),
            "context_recall": _score_context_recall(gt, contexts) if gt else None,
            "correctness": _score_correctness(question, resp.answer, gt) if gt else None,
        }
        results.append(row)
        print(
            f"  [{i}/{len(eval_items)}] faith={row['faithfulness']} "
            f"ans_rel={row['answer_relevancy']} ctx_prec={row['context_precision']} "
            f"ctx_rec={row['context_recall']} correct={row['correctness']}"
        )

    summary = {
        "n": len(results),
        "embed_model": settings.SERVER_EMBED_MODEL,
        "rerank_active": rag_pipeline.rerank_active(),
        "faithfulness": _avg([r["faithfulness"] for r in results]),
        "answer_relevancy": _avg([r["answer_relevancy"] for r in results]),
        "context_precision": _avg([r["context_precision"] for r in results]),
        "context_recall": _avg([r["context_recall"] for r in results]),
        "correctness": _avg([r["correctness"] for r in results]),
    }
    report = {"summary": summary, "results": results}
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n===== RAGAS-style summary =====")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    print(f"\n[done] 详细报告写入 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
