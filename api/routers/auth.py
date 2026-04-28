"""使用者登入、註冊與個人資料端點。"""
from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from api.auth_utils import (
    clear_auth_cookie,
    create_jwt,
    hash_password,
    issue_token_payload,
    set_auth_cookie,
    verify_password,
)
from api.dependencies import get_current_user, get_storage
from api.models.requests import (
    LoginRequest,
    PasswordChangeRequest,
    ProfileUpdateRequest,
    RegisterRequest,
    validate_password_strength,
)
from api.models.responses import AuthResponseDTO, AuthSessionDTO, AuthUserDTO
from api.session_manager import session_manager


router = APIRouter(prefix="/auth", tags=["auth"])


def _client_ip(request: Request) -> str:
    if os.getenv("MEMORIACORE_TRUST_PROXY_HEADERS", "").strip().lower() in {"1", "true", "yes", "on"}:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",", 1)[0].strip()
    return request.client.host if request.client else "unknown"


def _public_user(user: dict, csrf_token: str | None = None) -> AuthUserDTO:
    return AuthUserDTO(
        id=int(user["id"]),
        username=user["username"],
        nickname=user.get("nickname") or "",
        role=user.get("role") or "user",
        telegram_uid=user.get("telegram_uid"),
        discord_uid=user.get("discord_uid"),
        created_at=user.get("created_at"),
        updated_at=user.get("updated_at"),
        csrf_token=csrf_token,
    )


def _issue_login_response(response: Response, user: dict) -> AuthResponseDTO:
    payload, csrf_token = issue_token_payload(user)
    token = create_jwt(payload)
    set_auth_cookie(response, token)
    return AuthResponseDTO(user=_public_user(user, csrf_token), csrf_token=csrf_token)


@router.post("/register", response_model=AuthResponseDTO)
async def register(body: RegisterRequest, request: Request, response: Response):
    storage = get_storage()
    prefs = storage.load_prefs()
    if prefs.get("registration_enabled", True) is False and await asyncio.to_thread(storage.count_users) > 0:
        raise HTTPException(status_code=403, detail="目前未開放新註冊")

    ip = _client_ip(request)
    register_key = "__register__"
    if storage.is_auth_locked(register_key, ip):
        raise HTTPException(status_code=429, detail="註冊嘗試過於頻繁，請稍後再試")
    storage.record_auth_attempt(register_key, ip, limit=10, lock_minutes=15, window_minutes=15)

    existing = await asyncio.to_thread(storage.get_user_by_username, body.username)
    if existing:
        raise HTTPException(status_code=409, detail="此 username 已被使用")

    password_hash = await asyncio.to_thread(hash_password, body.password)
    try:
        user = await asyncio.to_thread(storage.create_user, body.username, password_hash)
    except ValueError:
        raise HTTPException(status_code=409, detail="此 username 已被使用")
    return _issue_login_response(response, user)


@router.post("/login", response_model=AuthResponseDTO)
async def login(body: LoginRequest, request: Request, response: Response):
    storage = get_storage()
    ip = _client_ip(request)
    if storage.is_auth_locked(body.username, ip):
        raise HTTPException(status_code=429, detail="登入失敗次數過多，請稍後再試")

    user = await asyncio.to_thread(storage.get_user_by_username, body.username)
    if not user or not await asyncio.to_thread(verify_password, body.password, user["password_hash"]):
        storage.record_auth_attempt(body.username, ip, limit=5, lock_minutes=15, window_minutes=15)
        raise HTTPException(status_code=401, detail="帳號或密碼錯誤")

    storage.reset_auth_attempts(body.username, ip)
    return _issue_login_response(response, user)


@router.post("/logout")
async def logout(response: Response, current_user: dict = Depends(get_current_user)):
    storage = get_storage()
    await asyncio.to_thread(storage.increment_user_token_version, current_user["id"])
    clear_auth_cookie(response)
    return {"status": "logged_out"}


@router.get("/me", response_model=AuthUserDTO)
async def me(request: Request, current_user: dict = Depends(get_current_user)):
    payload = getattr(request.state, "auth_payload", {})
    return _public_user(current_user, payload.get("csrf"))


@router.put("/profile", response_model=AuthUserDTO)
async def update_profile(
    body: ProfileUpdateRequest,
    current_user: dict = Depends(get_current_user),
):
    storage = get_storage()
    user = await asyncio.to_thread(
        storage.update_user_profile,
        current_user["id"],
        body.nickname,
        body.telegram_uid,
        body.discord_uid,
    )
    if not user:
        raise HTTPException(status_code=404, detail="找不到使用者")
    return _public_user(user)


@router.put("/password")
async def change_password(
    body: PasswordChangeRequest,
    response: Response,
    current_user: dict = Depends(get_current_user),
):
    storage = get_storage()
    try:
        validate_password_strength(current_user["username"], body.new_password)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if not await asyncio.to_thread(verify_password, body.old_password, current_user["password_hash"]):
        raise HTTPException(status_code=400, detail="舊密碼不正確")
    password_hash = await asyncio.to_thread(hash_password, body.new_password)
    await asyncio.to_thread(storage.update_user_password_hash, current_user["id"], password_hash)
    clear_auth_cookie(response)
    return {"status": "password_changed"}


@router.post("/session", response_model=AuthSessionDTO)
async def create_auth_session(current_user: dict = Depends(get_current_user)):
    channel_class = "private" if current_user.get("role") == "admin" else "public"
    persona_face = "private" if current_user.get("role") == "admin" else "public"
    session = await session_manager.create(
        channel="dashboard",
        channel_uid=str(current_user["id"]),
        user_id=str(current_user["id"]),
        channel_class=channel_class,
        persona_face=persona_face,
    )
    return AuthSessionDTO(session_id=session.session_id)
