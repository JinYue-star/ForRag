#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""登录令牌解析与角色校验（FastAPI 层）。

鉴权模型：
- 前端登录后获得 login token，通过 ``Authorization: Bearer <token>`` 携带。
- ``REQUIRE_AUTH`` 为 True（或 auto 且已存在用户）时，受保护接口必须携带有效令牌。
- 教师专属接口（写知识库、导出）额外要求 role == teacher。
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import HTTPException

import auth_store

from rag_api import settings


def auth_required() -> bool:
    """是否强制登录：显式开关优先；auto 模式下只要存在任意用户即启用。"""
    if settings.REQUIRE_AUTH is True:
        return True
    if settings.REQUIRE_AUTH is False:
        return False
    try:
        return auth_store.user_count(settings.DATA_DIR) > 0
    except Exception:
        return False


def _bearer_token(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    value = authorization.strip()
    if value.lower().startswith("bearer "):
        return value[7:].strip() or None
    return None


def resolve_current_user(authorization: Optional[str]) -> Optional[dict[str, Any]]:
    """解析当前登录用户。

    - 未启用鉴权：返回 None（本地开箱模式，不阻塞）。
    - 已启用鉴权：无有效令牌抛 401；有效则返回用户 dict。
    """
    token = _bearer_token(authorization)
    if not auth_required():
        # 即使未强制，也尽量解析出用户（便于按角色区分行为）
        return auth_store.token_resolve(settings.DATA_DIR, token) if token else None
    if not token:
        raise HTTPException(status_code=401, detail="请先登录")
    user = auth_store.token_resolve(settings.DATA_DIR, token)
    if not user:
        raise HTTPException(status_code=401, detail="登录已过期或无效，请重新登录")
    if not bool(user.get("is_active", True)):
        raise HTTPException(status_code=403, detail="账号已被禁用")
    return user


def require_teacher(user: Optional[dict[str, Any]]) -> dict[str, Any]:
    """教师专属操作校验。

    - 已启用鉴权：必须为 teacher，否则 403。
    - 未启用鉴权（本地开箱）：允许通过（便于单机调试），但若解析出学生用户则拒绝。
    """
    if user is None:
        if auth_required():
            raise HTTPException(status_code=401, detail="请先登录")
        return {"id": "", "role": auth_store.ROLE_TEACHER, "username": "local"}
    if user.get("role") != auth_store.ROLE_TEACHER:
        raise HTTPException(status_code=403, detail="仅教师可执行该操作")
    return user


def require_login(user: Optional[dict[str, Any]]) -> dict[str, Any]:
    if user is None:
        if auth_required():
            raise HTTPException(status_code=401, detail="请先登录")
        return {"id": "", "role": "", "username": "local"}
    return user
