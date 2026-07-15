#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""教师导出路由（仅教师）：预览与文件下载（CSV/Excel）。"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import Response

from rag_api import export_service
from rag_api.auth import require_teacher, resolve_current_user
from rag_api.http_common import check_rate_limit, client_ip
from rag_api.schemas import ExportRequest

router_export = APIRouter(prefix="/api/v1/admin/export", tags=["export"])


def _modules(body: ExportRequest) -> list[str]:
    mods = export_service.selected_modules(
        body.include_questions, body.include_answers, body.include_quiz
    )
    if not mods:
        raise HTTPException(status_code=400, detail="请至少选择一个导出模块")
    return mods


@router_export.post("/preview")
def export_preview(
    request: Request,
    body: ExportRequest,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    require_teacher(resolve_current_user(authorization))
    check_rate_limit(client_ip(request))
    modules = _modules(body)
    data = export_service.gather(
        body.start, body.end, body.course_ids,
        body.include_questions, body.include_answers, body.include_quiz,
    )
    return export_service.preview(data, modules)


@router_export.post("/file")
def export_file(
    request: Request,
    body: ExportRequest,
    authorization: Optional[str] = Header(default=None),
) -> Response:
    require_teacher(resolve_current_user(authorization))
    check_rate_limit(client_ip(request))
    modules = _modules(body)
    data = export_service.gather(
        body.start, body.end, body.course_ids,
        body.include_questions, body.include_answers, body.include_quiz,
    )
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    fmt = (body.format or "xlsx").lower()
    if fmt == "csv":
        payload = export_service.to_csv(data, modules)
        return Response(
            content=payload,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="solo-export-{stamp}.csv"'},
        )
    try:
        payload = export_service.to_xlsx(data, modules)
    except ModuleNotFoundError as e:
        raise HTTPException(
            status_code=503,
            detail="缺少 openpyxl 依赖，无法导出 Excel。请安装：pip install openpyxl，或改用 CSV 导出。",
        ) from e
    return Response(
        content=payload,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="solo-export-{stamp}.xlsx"'},
    )


@router_export.post("/save-to-kb")
def export_save_to_kb(
    request: Request,
    body: ExportRequest,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    """将筛选后的学生提问写入课程知识库（分类 Student questions + 附件）。"""
    user = require_teacher(resolve_current_user(authorization))
    check_rate_limit(client_ip(request))
    data = export_service.gather(
        body.start, body.end, body.course_ids,
        True, False, False,
    )
    rows = data.get("questions") or []
    if not rows:
        raise HTTPException(status_code=400, detail="当前筛选条件下没有学生提问可保存")
    from rag_api import exercise_service

    try:
        return exercise_service.save_questions_to_kb(
            rows,
            fmt=body.format or "xlsx",
            owner_id=str(user.get("id") or "") or None,
        )
    except ModuleNotFoundError as e:
        raise HTTPException(
            status_code=503,
            detail="缺少 openpyxl，请改用 CSV 格式保存到知识库",
        ) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
