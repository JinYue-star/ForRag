#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""鉴权与用户管理路由：登录、注册、当前用户、登出、教师端账号管理、注册码。"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException, Request

import auth_store

from rag_api import settings
from rag_api.auth import auth_required, require_teacher, resolve_current_user
from rag_api.http_common import check_rate_limit, client_ip
from rag_api.schemas import (
    CreateUserRequest,
    LoginRequest,
    LoginResponse,
    RegisterRequest,
    RegistrationCodeResponse,
    UserPublic,
)

router_auth = APIRouter(prefix="/api/v1/auth", tags=["auth"])
router_admin = APIRouter(prefix="/api/v1/admin", tags=["admin"])


def _user_public(u: dict[str, Any]) -> UserPublic:
    return UserPublic(
        id=str(u.get("id") or ""),
        username=str(u.get("username") or ""),
        role=str(u.get("role") or ""),
        display_name=str(u.get("display_name") or ""),
        student_no=str(u.get("student_no") or ""),
        is_active=bool(u.get("is_active", True)),
        created_at=float(u.get("created_at") or 0),
    )


@router_auth.get("/config")
def auth_config() -> dict[str, Any]:
    """前端登录页所需的公开配置（不含任何密钥）。"""
    return {
        "auth_required": auth_required(),
        "registration_open": True,
    }


@router_auth.post("/login", response_model=LoginResponse)
def login(request: Request, body: LoginRequest) -> LoginResponse:
    check_rate_limit(client_ip(request))
    user = auth_store.authenticate(settings.DATA_DIR, body.username, body.password)
    if not user:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token, expires = auth_store.token_issue(
        settings.DATA_DIR, str(user["id"]), settings.AUTH_TOKEN_TTL_SECONDS
    )
    return LoginResponse(token=token, expires_at=expires, user=_user_public(user))


@router_auth.post("/register", response_model=LoginResponse)
def register(request: Request, body: RegisterRequest) -> LoginResponse:
    """学生自助注册：需提供正确的课程注册码；注册后直接登录。"""
    check_rate_limit(client_ip(request))
    expected = auth_store.get_registration_code(settings.DATA_DIR)
    if not expected or body.code.strip() != expected:
        raise HTTPException(status_code=403, detail="注册码无效")
    try:
        user = auth_store.user_create(
            settings.DATA_DIR,
            body.username,
            body.password,
            role=auth_store.ROLE_STUDENT,
            display_name=body.display_name,
            student_no=body.student_no,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=_user_error_detail(str(e))) from e
    token, expires = auth_store.token_issue(
        settings.DATA_DIR, str(user["id"]), settings.AUTH_TOKEN_TTL_SECONDS
    )
    return LoginResponse(token=token, expires_at=expires, user=_user_public(user))


@router_auth.get("/me", response_model=UserPublic)
def me(authorization: Optional[str] = Header(default=None)) -> UserPublic:
    user = resolve_current_user(authorization)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    return _user_public(user)


@router_auth.post("/logout")
def logout(authorization: Optional[str] = Header(default=None)) -> dict[str, str]:
    if authorization and authorization.lower().startswith("bearer "):
        auth_store.token_revoke(settings.DATA_DIR, authorization[7:].strip())
    return {"status": "ok"}


def _user_error_detail(code: str) -> str:
    return {
        "username_required": "请输入用户名",
        "password_too_short": "密码至少 6 位",
        "username_taken": "用户名已被占用",
        "invalid_role": "非法角色",
    }.get(code, "创建用户失败")


# ---------------- 教师端账号管理 ----------------

@router_admin.get("/users", response_model=list[UserPublic])
def list_users(
    role: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
) -> list[UserPublic]:
    require_teacher(resolve_current_user(authorization))
    role_filter = role if role in (auth_store.ROLE_TEACHER, auth_store.ROLE_STUDENT) else None
    return [_user_public(u) for u in auth_store.user_list(settings.DATA_DIR, role_filter)]


@router_admin.post("/users", response_model=UserPublic)
def create_user(
    body: CreateUserRequest,
    authorization: Optional[str] = Header(default=None),
) -> UserPublic:
    require_teacher(resolve_current_user(authorization))
    role = body.role if body.role in (auth_store.ROLE_TEACHER, auth_store.ROLE_STUDENT) else "student"
    try:
        user = auth_store.user_create(
            settings.DATA_DIR,
            body.username,
            body.password,
            role=role,
            display_name=body.display_name,
            student_no=body.student_no,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=_user_error_detail(str(e))) from e
    return _user_public(user)


@router_admin.delete("/users/{user_id}")
def delete_user(
    user_id: str,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, str]:
    actor = require_teacher(resolve_current_user(authorization))
    if user_id == actor.get("id"):
        raise HTTPException(status_code=400, detail="不能删除当前登录账号")
    if not auth_store.user_delete(settings.DATA_DIR, user_id):
        raise HTTPException(status_code=404, detail="用户不存在")
    return {"status": "deleted"}


@router_admin.get("/registration-code", response_model=RegistrationCodeResponse)
def get_reg_code(authorization: Optional[str] = Header(default=None)) -> RegistrationCodeResponse:
    require_teacher(resolve_current_user(authorization))
    code = auth_store.ensure_registration_code(settings.DATA_DIR)
    return RegistrationCodeResponse(code=code)


@router_admin.post("/registration-code", response_model=RegistrationCodeResponse)
def rotate_reg_code(authorization: Optional[str] = Header(default=None)) -> RegistrationCodeResponse:
    require_teacher(resolve_current_user(authorization))
    code = auth_store.set_registration_code(settings.DATA_DIR, "")
    return RegistrationCodeResponse(code=code)
