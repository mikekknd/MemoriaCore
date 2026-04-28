"""JWT Cookie 驗證 middleware。"""
from __future__ import annotations

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from api.auth_utils import AUTH_COOKIE_NAME, CSRF_HEADER_NAME, decode_jwt
from api.dependencies import get_storage


PUBLIC_ROUTES = {
    ("GET", "/api/v1/health"),
    ("POST", "/api/v1/auth/bypass"),
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/register"),
}

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
ADMIN_PREFIXES = (
    "/api/v1/admin",
    "/api/v1/system",
    "/api/v1/prompts",
    "/api/v1/character",
    "/api/v1/logs",
)


def _json_error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path.rstrip("/") or "/"
        method = request.method.upper()

        if method == "OPTIONS" or not path.startswith("/api/v1"):
            return await call_next(request)
        if (method, path) in PUBLIC_ROUTES:
            return await call_next(request)

        token = request.cookies.get(AUTH_COOKIE_NAME)
        if not token:
            return _json_error(401, "UNAUTHORIZED", "尚未登入")

        try:
            payload = decode_jwt(token)
            storage = get_storage()
            user = storage.get_user_by_id(payload.get("sub"))
            if not user:
                return _json_error(401, "UNAUTHORIZED", "登入狀態已失效")
            if int(user.get("token_version", 0)) != int(payload.get("ver", -1)):
                return _json_error(401, "UNAUTHORIZED", "登入狀態已失效")
        except Exception:
            return _json_error(401, "UNAUTHORIZED", "登入狀態已失效")

        request.state.current_user = user
        request.state.auth_payload = payload

        if method not in SAFE_METHODS:
            csrf_header = request.headers.get(CSRF_HEADER_NAME)
            if not csrf_header or csrf_header != payload.get("csrf"):
                return _json_error(403, "CSRF_REQUIRED", "缺少或無效的 CSRF token")

        if path.startswith(ADMIN_PREFIXES) and user.get("role") != "admin":
            return _json_error(403, "FORBIDDEN", "需要管理員權限")

        return await call_next(request)
