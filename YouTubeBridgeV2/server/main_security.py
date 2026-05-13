"""Main-app security boundary for YouTubeBridgeV2 routes."""

from __future__ import annotations

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from YouTubeBridgeV2.server.security import (
    AuthRequirement,
    PermissionGroup,
    SecurityErrorResponse,
    resolve_permission_context,
)


class V2LoopbackOnlyMiddleware(BaseHTTPMiddleware):
    """Protect main-app `/v2` API/SSE routes with a loopback-only boundary."""

    async def dispatch(self, request, call_next):
        path = request.url.path.rstrip("/") or "/"
        if not _requires_v2_loopback_check(path):
            return await call_next(request)

        permission = resolve_permission_context(
            request,
            AuthRequirement(
                permission_group=PermissionGroup.OPERATOR,
                allow_loopback=True,
                loopback_group=PermissionGroup.OPERATOR,
            ),
        )
        if isinstance(permission, SecurityErrorResponse):
            return JSONResponse(
                status_code=permission.status_code,
                content={
                    "error": permission.error,
                    "correlation_id": permission.correlation_id,
                },
            )
        request.state.youtubebridge_v2_permission = permission
        return await call_next(request)


def _requires_v2_loopback_check(path: str) -> bool:
    if path != "/v2" and not path.startswith("/v2/"):
        return False
    return not (path == "/v2/static" or path.startswith("/v2/static/"))


__all__ = ["V2LoopbackOnlyMiddleware"]
