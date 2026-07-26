#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""清理旧版本向量缓存并按当前 CACHE_VERSION 重建。

分块参数（RAG_CHUNK_TOKENS 等）或 CACHE_VERSION 一变，旧缓存的 key 就再也命中不到，
既占磁盘又容易让人误以为"改了分块却没生效"。用法（conda forrag 环境）：

  python tools/rebuild_vector_cache.py --prune                 # 只清理非当前版本的缓存
  python tools/rebuild_vector_cache.py --prune --docs <目录>    # 清理后重建该目录下文档的索引
  python tools/rebuild_vector_cache.py --docs <目录> --dry-run  # 只看会做什么
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# 先导入 settings 让 .env 生效（RAG_CACHE_ROOT / 嵌入模型都从环境变量读）。
from rag_api import settings  # noqa: E402
import doc_qa_assistant as dqa  # noqa: E402

DOC_EXTS = set(dqa.PARSERS.keys())


def _entry_version(entry: Path) -> str:
    manifest = entry / "manifest.json"
    if not manifest.is_file():
        return "(no manifest)"
    try:
        return str(json.loads(manifest.read_text(encoding="utf-8")).get("version", "(none)"))
    except (OSError, json.JSONDecodeError):
        return "(bad manifest)"


def _dir_size_mb(path: Path) -> float:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / 1024 / 1024


def prune_stale(dry_run: bool) -> tuple[int, float]:
    root = dqa._cache_root()
    removed = 0
    freed = 0.0
    for kind in ("parsed", "docs", "bundles"):
        base = root / kind
        if not base.is_dir():
            continue
        for entry in sorted(base.iterdir()):
            if not entry.is_dir():
                continue
            version = _entry_version(entry)
            if version == dqa.CACHE_VERSION:
                continue
            size = _dir_size_mb(entry)
            print(f"[清理] {kind}/{entry.name} version={version} {size:.1f}MB")
            if not dry_run:
                shutil.rmtree(entry, ignore_errors=True)
            removed += 1
            freed += size
    return removed, freed


def collect_docs(docs_dir: Path) -> list[Path]:
    if docs_dir.is_file():
        return [docs_dir]
    return [
        p
        for p in sorted(docs_dir.rglob("*"))
        if p.is_file() and p.suffix.lower() in DOC_EXTS
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description="清理并重建向量缓存")
    ap.add_argument("--docs", default="", help="需要重建索引的目录或单个文件")
    ap.add_argument("--prune", action="store_true", help="删除非当前 CACHE_VERSION 的缓存条目")
    ap.add_argument("--dry-run", action="store_true", help="只打印将要执行的操作")
    args = ap.parse_args()

    print(f"[信息] 缓存目录 {dqa._cache_root()}，当前版本 {dqa.CACHE_VERSION}")
    if args.prune:
        removed, freed = prune_stale(args.dry_run)
        verb = "将删除" if args.dry_run else "已删除"
        print(f"[信息] {verb} {removed} 个过期缓存条目，释放约 {freed:.1f}MB")

    if not args.docs:
        return 0
    docs = collect_docs(Path(args.docs).expanduser())
    if not docs:
        print(f"[错误] 未在 {args.docs} 找到可解析文档", file=sys.stderr)
        return 2
    print(f"[信息] 重建 {len(docs)} 个文件的索引，嵌入模型 {settings.SERVER_EMBED_MODEL}")
    if args.dry_run:
        for p in docs:
            print(f"  {p}")
        return 0
    chunks, _emb, _index, _st = dqa.build_or_load_index(docs, settings.SERVER_EMBED_MODEL)
    print(f"[完成] 索引就绪：{len(chunks)} 个文本块")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
