#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""将旧的会话级 KB 文件迁移为全局 KB（kb_id=default）。

迁移内容：
- 笔记正文：写入 `.uploads/kb/default/notes/<note_id>.md`（从 SQLite 的 body_markdown 重建）
- 附件文件：将 `.uploads/<sid>/kb/files/<disk_name>` 移至 `.uploads/kb/default/files/<disk_name>`
  并将 kb_note_files.stored_rel 更新为 `kb/default/files/<disk_name>`

设计目标：
- 幂等：重复执行不会破坏数据
- 尽量不删除旧目录（避免误删）；只在成功迁移后可选清理
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path


def migrate_global_kb(data_dir: Path, upload_dir: Path, *, kb_id: str = "default") -> None:
    data_dir = data_dir.resolve()
    upload_dir = upload_dir.resolve()
    marker = data_dir / "kb_global_migrated"
    if marker.exists():
        return

    db_path = (data_dir / "kb.sqlite").resolve()
    if not db_path.exists():
        marker.write_text("no_db\n", encoding="utf-8")
        return

    kb_root = (upload_dir / "kb" / kb_id).resolve()
    notes_dir = (kb_root / "notes").resolve()
    files_dir = (kb_root / "files").resolve()
    notes_dir.mkdir(parents=True, exist_ok=True)
    files_dir.mkdir(parents=True, exist_ok=True)

    cx = sqlite3.connect(db_path, check_same_thread=False)
    cx.row_factory = sqlite3.Row
    try:
        # 1) 重建笔记正文文件
        for row in cx.execute(
            "SELECT id, body_markdown FROM kb_notes WHERE kb_id=? ORDER BY updated_at", (kb_id,)
        ).fetchall():
            nid = str(row["id"])
            body = str(row["body_markdown"] or "")
            (notes_dir / f"{nid}.md").write_text(body, encoding="utf-8")

        # 2) 迁移附件文件 + 更新 stored_rel
        rows = cx.execute(
            "SELECT id, stored_rel FROM kb_note_files WHERE kb_id=? ORDER BY updated_at", (kb_id,)
        ).fetchall()
        for r in rows:
            fid = str(r["id"])
            stored_rel = str(r["stored_rel"] or "")
            if stored_rel.startswith(f"kb/{kb_id}/files/") or stored_rel.startswith(f"kb/{kb_id}\\files\\"):
                continue

            disk_name = Path(stored_rel).name
            if not disk_name:
                continue

            new_rel = f"kb/{kb_id}/files/{disk_name}"
            src = (upload_dir / stored_rel).resolve()

            # 兼容：旧 stored_rel 可能不包含 sid/kb/files 前缀（兜底扫描）
            if not src.is_file():
                for sid_dir in upload_dir.iterdir():
                    if not sid_dir.is_dir():
                        continue
                    if sid_dir.name == "kb":
                        continue
                    cand = (sid_dir / "kb" / "files" / disk_name).resolve()
                    if cand.is_file():
                        src = cand
                        break

            dest = (upload_dir / new_rel).resolve()
            dest.parent.mkdir(parents=True, exist_ok=True)

            if src.is_file():
                if dest.exists():
                    try:
                        if dest.stat().st_size == src.stat().st_size:
                            try:
                                src.unlink()
                            except OSError:
                                pass
                        else:
                            # 冲突：加后缀
                            stem = dest.stem
                            suf = dest.suffix
                            k = 2
                            while True:
                                alt = dest.with_name(f"{stem}_dup{k}{suf}")
                                if not alt.exists():
                                    dest = alt
                                    new_rel = f"kb/{kb_id}/files/{dest.name}"
                                    break
                                k += 1
                            os.replace(str(src), str(dest))
                    except OSError:
                        pass
                else:
                    try:
                        os.replace(str(src), str(dest))
                    except OSError:
                        # 回退：复制
                        try:
                            dest.write_bytes(src.read_bytes())
                        except OSError:
                            continue

            cx.execute("UPDATE kb_note_files SET stored_rel=? WHERE id=?", (new_rel, fid))

        cx.commit()
    finally:
        try:
            cx.close()
        except Exception:
            pass

    marker.write_text(f"ok kb_id={kb_id}\n", encoding="utf-8")

