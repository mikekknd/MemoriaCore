"""Admin-only 使用者帳號管理端點。"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from api.auth_utils import hash_password
from api.dependencies import get_storage, require_admin_user
from api.models.requests import (
    AdminPasswordResetRequest,
    AdminUserDeleteRequest,
    validate_password_strength,
)
from api.models.responses import AdminUserDTO, AdminUserDeleteResultDTO, AuthUserDTO

router = APIRouter(prefix="/admin/users", tags=["admin-users"])


def _public_admin_user(user: dict) -> AdminUserDTO:
    return AdminUserDTO(
        id=int(user["id"]),
        username=user["username"],
        nickname=user.get("nickname") or "",
        role=user.get("role") or "user",
        telegram_uid=user.get("telegram_uid"),
        discord_uid=user.get("discord_uid"),
        created_at=user.get("created_at"),
        updated_at=user.get("updated_at"),
        token_version=int(user.get("token_version") or 0),
        stats=user.get("stats") or {},
    )


def _public_auth_user(user: dict) -> AuthUserDTO:
    return AuthUserDTO(
        id=int(user["id"]),
        username=user["username"],
        nickname=user.get("nickname") or "",
        role=user.get("role") or "user",
        telegram_uid=user.get("telegram_uid"),
        discord_uid=user.get("discord_uid"),
        created_at=user.get("created_at"),
        updated_at=user.get("updated_at"),
    )


@router.get("", response_model=list[AdminUserDTO])
async def list_users(current_user: dict = Depends(require_admin_user)):
    storage = get_storage()
    users = await asyncio.to_thread(storage.list_users_with_stats)
    return [_public_admin_user(user) for user in users]


@router.post("/{user_id}/revoke", response_model=AuthUserDTO)
async def revoke_user_sessions(user_id: int, current_user: dict = Depends(require_admin_user)):
    storage = get_storage()
    user = await asyncio.to_thread(storage.increment_user_token_version, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="使用者不存在")
    return _public_auth_user(user)


@router.post("/{user_id}/password", response_model=AuthUserDTO)
async def reset_user_password(
    user_id: int,
    body: AdminPasswordResetRequest,
    current_user: dict = Depends(require_admin_user),
):
    storage = get_storage()
    user = await asyncio.to_thread(storage.get_user_by_id, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="使用者不存在")
    try:
        validate_password_strength(user["username"], body.new_password)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    password_hash = await asyncio.to_thread(hash_password, body.new_password)
    updated = await asyncio.to_thread(storage.update_user_password_hash, user_id, password_hash)
    if not updated:
        raise HTTPException(status_code=404, detail="使用者不存在")
    return _public_auth_user(updated)


@router.delete("/{user_id}", response_model=AdminUserDeleteResultDTO)
async def delete_user(
    user_id: int,
    body: AdminUserDeleteRequest,
    current_user: dict = Depends(require_admin_user),
):
    storage = get_storage()
    target = await asyncio.to_thread(storage.get_user_by_id, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="使用者不存在")
    if int(current_user["id"]) == int(user_id):
        raise HTTPException(status_code=400, detail="不可刪除目前登入中的自己")
    if target.get("role") == "admin" and await asyncio.to_thread(storage.count_admin_users) <= 1:
        raise HTTPException(status_code=400, detail="不可刪除最後一個 admin")
    try:
        result = await asyncio.to_thread(
            storage.delete_user_and_owned_data,
            user_id,
            body.confirm_username,
        )
    except ValueError as exc:
        message = str(exc)
        status = 404 if message == "user not found" else 400
        raise HTTPException(status_code=status, detail=message)
    return AdminUserDeleteResultDTO(status="deleted", **result)
