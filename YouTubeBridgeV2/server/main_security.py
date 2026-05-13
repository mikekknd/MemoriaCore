"""Main-app security boundary for YouTubeBridgeV2 routes."""

from __future__ import annotations

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from YouTubeBridgeV2.server.auth_config import load_v2_api_key_config
from YouTubeBridgeV2.server.security import (
    AuthRequirement,
    PermissionGroup,
    SecurityErrorResponse,
    resolve_permission_context,
)


class V2MainSecurityMiddleware(BaseHTTPMiddleware):
    """Protect main-app `/v2` API/SSE routes with loopback/API-key auth."""

    def __init__(
        self,
        app,
        *,
        storage_getter=None,
        allow_loopback: bool = True,
    ) -> None:
        super().__init__(app)
        self._storage_getter = storage_getter
        self._allow_loopback = allow_loopback

    async def dispatch(self, request, call_next):
        path = request.url.path.rstrip("/") or "/"
        if not _requires_v2_security_check(path):
            return await call_next(request)

        requirement = _auth_requirement(
            path=path,
            method=request.method,
            valid_api_keys=_load_valid_api_keys(self._storage_getter),
            allow_loopback=self._allow_loopback,
        )
        permission = resolve_permission_context(
            request,
            requirement,
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


class V2LoopbackOnlyMiddleware(V2MainSecurityMiddleware):
    """Backward-compatible name for the main-app V2 security middleware."""


def _requires_v2_security_check(path: str) -> bool:
    if path != "/v2" and not path.startswith("/v2/"):
        return False
    return not (path == "/v2/static" or path.startswith("/v2/static/"))


def _load_valid_api_keys(storage_getter) -> dict[str, PermissionGroup]:
    if storage_getter is None:
        return {}
    try:
        storage_manager = storage_getter()
    except Exception:
        return {}
    return load_v2_api_key_config(storage_manager).as_auth_mapping()


def _auth_requirement(
    *,
    path: str,
    method: str,
    valid_api_keys: dict[str, PermissionGroup],
    allow_loopback: bool,
) -> AuthRequirement:
    group, route_id = _route_requirement(path, method)
    return AuthRequirement(
        permission_group=group,
        valid_api_keys=valid_api_keys,
        allow_loopback=allow_loopback,
        loopback_group=PermissionGroup.OPERATOR,
        route_id=route_id,
    )


def _route_requirement(path: str, method: str) -> tuple[PermissionGroup, str]:
    parts = [part for part in path.split("/") if part]
    if len(parts) < 2 or parts[0] != "v2":
        return PermissionGroup.OPERATOR, ""

    http_method = method.upper()
    if parts[1] == "sessions":
        if len(parts) == 2 and http_method == "POST":
            return PermissionGroup.OPERATOR, "create_session"
        if len(parts) == 3 and http_method == "GET":
            return PermissionGroup.OBSERVER, "get_session"
        if len(parts) == 4:
            return _session_child_requirement(parts[3], http_method)

    return PermissionGroup.OPERATOR, "unknown_v2_route"


def _session_child_requirement(child: str, method: str) -> tuple[PermissionGroup, str]:
    if child == "plan" and method == "POST":
        return PermissionGroup.OPERATOR, "bind_plan"
    if child == "phase" and method == "GET":
        return PermissionGroup.OBSERVER, "phase"
    if child == "aftertalk-policy" and method == "POST":
        return PermissionGroup.OPERATOR, "aftertalk_policy"
    if child == "automation-control" and method == "POST":
        return PermissionGroup.OPERATOR, "automation_control"
    if child == "manual-close" and method == "POST":
        return PermissionGroup.OPERATOR, "manual_close"
    if child == "tick" and method == "POST":
        return PermissionGroup.OPERATOR, "tick_session"
    if child == "youtube-events" and method == "POST":
        return PermissionGroup.OPERATOR, "youtube_event_ingest"
    if child == "events" and method == "GET":
        return PermissionGroup.OBSERVER, "events"
    if child == "operator-stream" and method == "GET":
        return PermissionGroup.OBSERVER, "operator_stream"
    if child == "display-stream" and method == "GET":
        return PermissionGroup.DISPLAY, "display_stream"
    return PermissionGroup.OPERATOR, "unknown_v2_route"


__all__ = ["V2LoopbackOnlyMiddleware", "V2MainSecurityMiddleware"]
