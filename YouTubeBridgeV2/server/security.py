"""Access-control and secret-boundary contracts for YouTubeBridgeV2."""

from __future__ import annotations

from dataclasses import InitVar, dataclass, field
from enum import Enum
from hashlib import sha256
from hmac import compare_digest
from ipaddress import ip_address
from typing import Any, Mapping


class PermissionGroup(str, Enum):
    """V2 API permission group."""

    OPERATOR = "operator"
    DISPLAY = "display"
    OBSERVER = "observer"
    INTERNAL = "internal"


@dataclass(frozen=True)
class _ApiKeyEntry:
    key_fingerprint: str
    permission_group: PermissionGroup


@dataclass(frozen=True)
class AuthRequirement:
    """Route 或 stream 所需的 permission metadata."""

    permission_group: PermissionGroup | str
    valid_api_keys: InitVar[Mapping[str, PermissionGroup | str] | None] = None
    allow_loopback: bool = False
    loopback_group: PermissionGroup | str | None = None
    route_id: str = ""
    _api_key_entries: tuple[_ApiKeyEntry, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self, valid_api_keys: Mapping[str, PermissionGroup | str] | None) -> None:
        entries = tuple(
            _ApiKeyEntry(
                key_fingerprint=_api_key_fingerprint(str(api_key)),
                permission_group=_coerce_group(group),
            )
            for api_key, group in (valid_api_keys or {}).items()
        )
        object.__setattr__(self, "_api_key_entries", entries)


@dataclass(frozen=True)
class PermissionContext:
    """已通過 security boundary 的 caller context."""

    permission_group: PermissionGroup
    auth_method: str
    subject_id: str
    allowed_actions: tuple[str, ...]
    is_loopback: bool = False
    secret_boundary: "SecretBoundary | None" = None


@dataclass(frozen=True)
class SecurityErrorResponse:
    """Sanitized security error response body."""

    status_code: int
    error: dict[str, str]
    correlation_id: str = "security-unavailable"


@dataclass(frozen=True)
class SecretBoundary:
    """Adapter 可用的 secret reference，不把 secret value 放入 public shape."""

    reference_id: str
    secret_kind: str
    secret_value: InitVar[str] = ""
    public_metadata: dict[str, object] = field(default_factory=dict)

    def __post_init__(self, secret_value: str) -> None:
        object.__setattr__(self, "public_metadata", _redact_public_value(self.public_metadata))

    def __repr__(self) -> str:
        return (
            "SecretBoundary("
            f"reference_id={self.reference_id!r}, "
            f"secret_kind={self.secret_kind!r}, "
            f"public_metadata={_redact_public_value(self.public_metadata)!r})"
        )

    def as_adapter_reference(self) -> dict[str, object]:
        """回傳可交給 adapter 的 secret reference，不包含 secret value."""

        return {
            "reference_id": self.reference_id,
            "secret_kind": self.secret_kind,
            "public_metadata": _redact_public_value(self.public_metadata),
        }


def resolve_permission_context(
    request: object,
    requirement: AuthRequirement,
) -> PermissionContext | SecurityErrorResponse:
    """依 request metadata 與 route requirement 解析 permission context."""

    required_group = _coerce_group(requirement.permission_group)
    loopback = _is_loopback_request(request)
    if requirement.allow_loopback and loopback:
        actual_group = (
            _coerce_group(requirement.loopback_group)
            if requirement.loopback_group is not None
            else required_group
        )
        if _is_allowed(actual_group, required_group):
            return _permission_context_or_forbidden(actual_group, "loopback", True, requirement.route_id)
        return _forbidden()

    api_key = _request_api_key(request)
    if not api_key:
        return _unauthorized()

    actual_group = _api_key_group(api_key, requirement)
    if actual_group is None:
        return _unauthorized()
    if not _is_allowed(actual_group, required_group):
        return _forbidden()

    return _permission_context_or_forbidden(actual_group, "api_key", loopback, requirement.route_id)


def sanitize_security_error(
    error: object,
    *,
    correlation_id: str = "security-unavailable",
    status_code: int = 401,
) -> SecurityErrorResponse:
    """將 security exception / dict 轉成不含 secret/raw headers 的 error body."""

    data = error if isinstance(error, dict) else {"message": str(error)}
    code = _safe_error_code(data.get("code"), status_code)
    message = _safe_error_message(code)
    return SecurityErrorResponse(
        status_code=status_code,
        error={
            "code": code,
            "message": message,
        },
        correlation_id=correlation_id,
    )


def _permission_context_or_forbidden(
    group: PermissionGroup,
    auth_method: str,
    is_loopback: bool,
    route_id: str,
) -> PermissionContext | SecurityErrorResponse:
    context = _permission_context(group, auth_method, is_loopback)
    required_action = _required_action(route_id)
    if required_action and required_action not in context.allowed_actions:
        return _forbidden()
    return context


def _permission_context(
    group: PermissionGroup,
    auth_method: str,
    is_loopback: bool,
) -> PermissionContext:
    return PermissionContext(
        permission_group=group,
        auth_method=auth_method,
        subject_id=f"{auth_method}:{group.value}",
        allowed_actions=_allowed_actions(group),
        is_loopback=is_loopback,
    )


def _api_key_group(
    api_key: str,
    requirement: AuthRequirement,
) -> PermissionGroup | None:
    fingerprint = _api_key_fingerprint(api_key)
    for entry in requirement._api_key_entries:
        if compare_digest(fingerprint, entry.key_fingerprint):
            return entry.permission_group
    return None


def _request_api_key(request: object) -> str:
    headers = getattr(request, "headers", {}) or {}
    for key in ("x-youtubebridgev2-api-key", "x-api-key"):
        value = _header_value(headers, key)
        if value:
            return value

    authorization = _header_value(headers, "authorization")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return ""


def _header_value(headers: object, key: str) -> str:
    if hasattr(headers, "get"):
        value = headers.get(key) or headers.get(key.lower()) or headers.get(key.upper())
        return str(value or "").strip()
    return ""


def _is_loopback_request(request: object) -> bool:
    client = getattr(request, "client", None)
    host = getattr(client, "host", "")
    try:
        return ip_address(str(host)).is_loopback
    except ValueError:
        return False


def _is_allowed(actual: PermissionGroup, required: PermissionGroup) -> bool:
    if required is PermissionGroup.INTERNAL:
        return actual is PermissionGroup.INTERNAL
    if actual is PermissionGroup.INTERNAL:
        return False
    if required is PermissionGroup.OBSERVER:
        return actual in {PermissionGroup.OBSERVER, PermissionGroup.OPERATOR}
    if required is PermissionGroup.DISPLAY:
        return actual in {PermissionGroup.DISPLAY, PermissionGroup.OPERATOR}
    if required is PermissionGroup.OPERATOR:
        return actual is PermissionGroup.OPERATOR
    return actual is required


def _allowed_actions(group: PermissionGroup) -> tuple[str, ...]:
    if group is PermissionGroup.OPERATOR:
        return (
            "read_status",
            "read_events",
            "read_operator_stream",
            "read_display_stream",
            "read_tts_queue",
            "update_aftertalk_policy",
            "manual_close",
            "tick_session",
            "ingest_youtube_event",
            "bind_plan",
            "create_session",
            "read_episode_plans",
            "manage_api_keys",
            "ack_tts_delivery",
            "timeout_tts_delivery",
        )
    if group is PermissionGroup.DISPLAY:
        return ("read_display_stream", "read_display_assets")
    if group is PermissionGroup.OBSERVER:
        return ("read_status", "read_events", "read_operator_stream", "read_tts_queue")
    return ("internal_service_call", "delegate_memoria_auth")


_ROUTE_ACTIONS = {
    "aftertalk_policy": "update_aftertalk_policy",
    "bind_plan": "bind_plan",
    "create_session": "create_session",
    "display_stream": "read_display_stream",
    "display_stream_endpoint": "read_display_stream",
    "episode_plans": "read_episode_plans",
    "events": "read_events",
    "get_phase": "read_status",
    "get_session": "read_status",
    "manual_close": "manual_close",
    "manage_api_keys": "manage_api_keys",
    "operator_stream": "read_operator_stream",
    "operator_stream_endpoint": "read_operator_stream",
    "phase": "read_status",
    "read_display_assets": "read_display_assets",
    "read_display_stream": "read_display_stream",
    "read_events": "read_events",
    "read_operator_stream": "read_operator_stream",
    "read_status": "read_status",
    "session_events": "read_events",
    "session_status": "read_status",
    "tick": "tick_session",
    "tick_session": "tick_session",
    "tts_delivery_ack": "ack_tts_delivery",
    "tts_delivery_timeout": "timeout_tts_delivery",
    "tts_queue": "read_tts_queue",
    "update_aftertalk_policy": "update_aftertalk_policy",
    "youtube_event_ingest": "ingest_youtube_event",
    "youtube_events": "ingest_youtube_event",
}


def _required_action(route_id: str) -> str:
    key = str(route_id or "").strip()
    if not key:
        return ""
    return _ROUTE_ACTIONS.get(key, key)


def _coerce_group(value: PermissionGroup | str) -> PermissionGroup:
    if isinstance(value, PermissionGroup):
        return value
    return PermissionGroup(str(value))


def _unauthorized() -> SecurityErrorResponse:
    return SecurityErrorResponse(
        status_code=401,
        error={
            "code": "unauthorized",
            "message": "authentication required",
        },
    )


def _forbidden() -> SecurityErrorResponse:
    return SecurityErrorResponse(
        status_code=403,
        error={
            "code": "forbidden",
            "message": "permission denied",
        },
    )


def _default_error_code(status_code: int) -> str:
    if status_code == 403:
        return "forbidden"
    if status_code == 401:
        return "unauthorized"
    return "security_error"


def _safe_error_code(value: object, status_code: int) -> str:
    code = str(value or "").strip().lower()
    if code in {"unauthorized", "forbidden", "security_error"}:
        return code
    return _default_error_code(status_code)


def _api_key_fingerprint(api_key: str) -> str:
    return sha256(api_key.encode("utf-8")).hexdigest()


def _safe_error_message(code: str) -> str:
    if code == "forbidden":
        return "permission denied"
    if code == "security_error":
        return "security check failed"
    return "authentication required"


def _redact_public_value(value: Any) -> Any:
    forbidden_keys = {
        "authorization",
        "x-youtubebridgev2-api-key",
        "x-api-key",
        "raw_headers",
        "secret",
        "token",
        "access_token",
        "api_key",
        "secret_value",
        "password",
    }
    if isinstance(value, dict):
        return {
            key: _redact_public_value(inner)
            for key, inner in value.items()
            if str(key).lower() not in forbidden_keys
        }
    if isinstance(value, list):
        return [_redact_public_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_public_value(item) for item in value)
    return value


__all__ = [
    "AuthRequirement",
    "PermissionContext",
    "PermissionGroup",
    "SecretBoundary",
    "SecurityErrorResponse",
    "resolve_permission_context",
    "sanitize_security_error",
]
