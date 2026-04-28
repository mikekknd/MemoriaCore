"""Auth 共用工具：密碼雜湊、JWT 簽章、Cookie/CSRF 設定。"""
from __future__ import annotations

import base64
import hmac
import hashlib
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from argon2 import PasswordHasher
    from argon2.exceptions import VerifyMismatchError, VerificationError
except ImportError:  # pragma: no cover - 部署環境應安裝 requirements.txt
    PasswordHasher = None
    VerifyMismatchError = VerificationError = Exception


AUTH_COOKIE_NAME = "mc_auth"
CSRF_HEADER_NAME = "X-CSRF-Token"
ACCESS_TOKEN_DAYS = 7
JWT_ALGORITHM = "HS256"
_SECRET_FILE = Path(".memoriacore_jwt_secret")

_password_hasher = PasswordHasher() if PasswordHasher else None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def require_password_hasher():
    if _password_hasher is None:
        raise RuntimeError("缺少 argon2-cffi，請先安裝 requirements.txt")
    return _password_hasher


def hash_password(password: str) -> str:
    return require_password_hasher().hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return require_password_hasher().verify(password_hash, password)
    except (VerifyMismatchError, VerificationError):
        return False


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def get_jwt_secret() -> str:
    secret = os.getenv("MEMORIACORE_JWT_SECRET", "").strip()
    if secret:
        return secret
    if _SECRET_FILE.exists():
        return _SECRET_FILE.read_text(encoding="utf-8").strip()
    secret = secrets.token_urlsafe(48)
    _SECRET_FILE.write_text(secret, encoding="utf-8")
    return secret


def create_jwt(payload: dict[str, Any]) -> str:
    header = {"alg": JWT_ALGORITHM, "typ": "JWT"}
    header_b64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    signature = hmac.new(get_jwt_secret().encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header_b64}.{payload_b64}.{_b64url_encode(signature)}"


def decode_jwt(token: str) -> dict[str, Any]:
    try:
        header_b64, payload_b64, signature_b64 = token.split(".", 2)
    except ValueError as exc:
        raise ValueError("token 格式錯誤") from exc

    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    expected = hmac.new(get_jwt_secret().encode("utf-8"), signing_input, hashlib.sha256).digest()
    actual = _b64url_decode(signature_b64)
    if not hmac.compare_digest(expected, actual):
        raise ValueError("token 簽章無效")

    header = json.loads(_b64url_decode(header_b64))
    if header.get("alg") != JWT_ALGORITHM:
        raise ValueError("token algorithm 不支援")

    payload = json.loads(_b64url_decode(payload_b64))
    exp = int(payload.get("exp", 0))
    if exp <= int(utc_now().timestamp()):
        raise ValueError("token 已過期")
    return payload


def issue_token_payload(user: dict[str, Any]) -> tuple[dict[str, Any], str]:
    csrf_token = secrets.token_urlsafe(32)
    now = utc_now()
    payload = {
        "sub": str(user["id"]),
        "username": user["username"],
        "role": user.get("role", "user"),
        "ver": int(user.get("token_version", 0)),
        "csrf": csrf_token,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=ACCESS_TOKEN_DAYS)).timestamp()),
    }
    return payload, csrf_token


def cookie_secure_default() -> bool:
    raw = os.getenv("MEMORIACORE_COOKIE_SECURE")
    if raw is None:
        return False
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def cookie_samesite_default() -> str:
    return os.getenv("MEMORIACORE_COOKIE_SAMESITE", "lax").strip().lower() or "lax"


def set_auth_cookie(response, token: str) -> None:
    response.set_cookie(
        AUTH_COOKIE_NAME,
        token,
        max_age=ACCESS_TOKEN_DAYS * 24 * 60 * 60,
        httponly=True,
        secure=cookie_secure_default(),
        samesite=cookie_samesite_default(),
        path="/",
    )


def clear_auth_cookie(response) -> None:
    response.delete_cookie(AUTH_COOKIE_NAME, path="/")


def generate_csrf_token() -> str:
    return secrets.token_urlsafe(32)


WEAK_PASSWORDS = {
    "password",
    "password123",
    "1234567890",
    "qwerty12345",
    "adminadmin",
    "letmein123",
    "memoriacore",
}

