"""FastAPI route contracts for YouTubeBridgeV2.

Routes 只負責 request/response mapping 與 SSE envelope。所有 runtime 行為
委派給 runtime service 或 query service；本模組不直接改 phase、不呼叫
adapter、不碰 storage internals。
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from itertools import chain
from typing import Any, Iterable, Literal

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, ValidationError

from YouTubeBridgeV2.display.events import sanitize_display_value
from YouTubeBridgeV2.server.auth_config import (
    delete_v2_api_key_entry,
    list_v2_api_key_entries,
    upsert_v2_api_key_entry,
)
from YouTubeBridgeV2.query_service import V2QueryServiceError
from YouTubeBridgeV2.runtime.application_service import (
    RuntimeCommand,
    RuntimeCommandType,
)


router = APIRouter(prefix="/v2", tags=["YouTubeBridgeV2"])


class RuntimeServiceNotConfigured(RuntimeError):
    """V2 runtime service 尚未由 application wiring 注入."""


class QueryServiceNotConfigured(RuntimeError):
    """V2 query service 尚未由 application wiring 注入."""


class StorageManagerNotConfigured(RuntimeError):
    """StorageManager 尚未由 application wiring 注入."""


class SessionCreateRequest(BaseModel):
    command_id: str = Field(..., min_length=1)
    session_id: str = Field(..., min_length=1)
    plan_id: str | None = None
    aftertalk_policy: Literal["disabled", "auto"] | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class PlanBindRequest(BaseModel):
    command_id: str = Field(..., min_length=1)
    plan: dict[str, object]


class AftertalkPolicyRequest(BaseModel):
    command_id: str = Field(..., min_length=1)
    aftertalk_policy: Literal["disabled", "auto"]


class AutomationControlRequest(BaseModel):
    command_id: str = Field(..., min_length=1)
    enabled: bool | None = None
    paused: bool | None = None
    reason: str | None = None


class ManualCloseRequest(BaseModel):
    command_id: str = Field(..., min_length=1)
    reason: str | None = None


class TickRequest(BaseModel):
    command_id: str = Field(..., min_length=1)


class YouTubeEventIngestRequest(BaseModel):
    command_id: str = Field(..., min_length=1)
    youtube_event: dict[str, object]
    polling_cursor: dict[str, object] | None = None
    page_info: dict[str, object] | None = None


class ApiKeyCreateRequest(BaseModel):
    key: str = Field(..., min_length=1)
    permission_group: Literal["operator", "display", "observer"]


def get_runtime_service() -> object:
    """FastAPI dependency placeholder for Runtime Application Service."""

    raise RuntimeServiceNotConfigured("runtime service dependency is not configured")


def get_query_service() -> object:
    """FastAPI dependency placeholder for read/query service."""

    raise QueryServiceNotConfigured("query service dependency is not configured")


def get_storage_manager() -> object:
    """FastAPI dependency placeholder for StorageManager-backed prefs."""

    raise StorageManagerNotConfigured("storage manager dependency is not configured")


def get_now() -> datetime:
    """FastAPI dependency for deterministic command timestamps in tests."""

    return datetime.now(timezone.utc)


@router.get("/api-keys", response_model=None)
def list_api_keys_endpoint(
    storage_manager: object = Depends(get_storage_manager),
) -> dict[str, object]:
    """Return sanitized V2 API key entries for operator management."""

    return {
        "api_keys": [
            _object_to_dict(entry)
            for entry in list_v2_api_key_entries(storage_manager)
        ]
    }


@router.post("/api-keys", response_model=None)
def create_api_key_endpoint(
    raw_body: object = Body(...),
    storage_manager: object = Depends(get_storage_manager),
) -> dict[str, object] | JSONResponse:
    """Create or update a V2 API key entry without echoing the raw key."""

    body = _validate_body(ApiKeyCreateRequest, raw_body)
    if isinstance(body, JSONResponse):
        return body
    try:
        entry = upsert_v2_api_key_entry(
            storage_manager,
            key=body.key,
            permission_group=body.permission_group,
        )
    except ValueError:
        return _validation_error_response(raw_body)
    return {
        "status": "ok",
        "api_key": _object_to_dict(entry),
    }


@router.delete("/api-keys/{key_fingerprint}", response_model=None)
def delete_api_key_endpoint(
    key_fingerprint: str,
    storage_manager: object = Depends(get_storage_manager),
) -> dict[str, object]:
    """Revoke a V2 API key by fingerprint."""

    removed = delete_v2_api_key_entry(storage_manager, key_fingerprint=key_fingerprint)
    return {
        "status": "ok",
        "removed": removed,
        "api_keys": [
            _object_to_dict(entry)
            for entry in list_v2_api_key_entries(storage_manager)
        ],
    }


@router.post("/sessions", response_model=None)
def create_session_endpoint(
    request: Request,
    raw_body: object = Body(...),
    runtime_service: object = Depends(get_runtime_service),
    now: datetime = Depends(get_now),
) -> dict[str, object] | JSONResponse:
    """Create a V2 session by delegating to runtime service."""

    body = _validate_body(SessionCreateRequest, raw_body)
    if isinstance(body, JSONResponse):
        return body
    command = _command(
        command_id=body.command_id,
        session_id=body.session_id,
        command_type=RuntimeCommandType.CREATE_SESSION,
        now=now,
        permission_context=_request_permission_context(request),
        payload={
            "plan_id": body.plan_id,
            "aftertalk_policy": body.aftertalk_policy,
            "metadata": body.metadata,
        },
    )
    return _call_runtime(runtime_service, "create_session", command, now)


@router.get("/sessions/{session_id}", response_model=None)
def get_session_endpoint(
    session_id: str,
    request: Request,
    query_service: object = Depends(get_query_service),
) -> dict[str, object] | JSONResponse:
    """Return public V2 session status through query service."""

    try:
        return _status_with_permission_context(
            query_service.get_session(session_id),
            request,
        )
    except V2QueryServiceError:
        return _query_not_found_response(session_id)


@router.post("/sessions/{session_id}/plan", response_model=None)
def bind_plan_endpoint(
    session_id: str,
    request: Request,
    raw_body: object = Body(...),
    runtime_service: object = Depends(get_runtime_service),
    now: datetime = Depends(get_now),
) -> dict[str, object] | JSONResponse:
    """Bind a LiveEpisodePlan by delegating to runtime service."""

    body = _validate_body(PlanBindRequest, raw_body)
    if isinstance(body, JSONResponse):
        return body
    command = _command(
        command_id=body.command_id,
        session_id=session_id,
        command_type=RuntimeCommandType.BIND_PLAN,
        now=now,
        permission_context=_request_permission_context(request),
        payload={"plan": body.plan},
    )
    return _call_runtime(runtime_service, "bind_plan", command, now)


@router.get("/sessions/{session_id}/phase", response_model=None)
def get_phase_endpoint(
    session_id: str,
    request: Request,
    query_service: object = Depends(get_query_service),
) -> dict[str, object] | JSONResponse:
    """Return phase status body through query service."""

    try:
        return _status_with_permission_context(
            query_service.get_phase(session_id),
            request,
        )
    except V2QueryServiceError:
        return _query_not_found_response(session_id)


@router.post("/sessions/{session_id}/aftertalk-policy", response_model=None)
def update_aftertalk_policy_endpoint(
    session_id: str,
    request: Request,
    raw_body: object = Body(...),
    runtime_service: object = Depends(get_runtime_service),
    now: datetime = Depends(get_now),
) -> dict[str, object] | JSONResponse:
    """Update aftertalk policy by delegating to runtime service."""

    body = _validate_body(AftertalkPolicyRequest, raw_body)
    if isinstance(body, JSONResponse):
        return body
    command = _command(
        command_id=body.command_id,
        session_id=session_id,
        command_type=RuntimeCommandType.UPDATE_AFTERTALK_POLICY,
        now=now,
        permission_context=_request_permission_context(request),
        payload={"aftertalk_policy": body.aftertalk_policy},
    )
    return _call_runtime(runtime_service, "update_aftertalk_policy", command, now)


@router.post("/sessions/{session_id}/automation-control", response_model=None)
def update_automation_control_endpoint(
    session_id: str,
    request: Request,
    raw_body: object = Body(...),
    runtime_service: object = Depends(get_runtime_service),
    now: datetime = Depends(get_now),
) -> dict[str, object] | JSONResponse:
    """Update runtime automation safety controls through runtime service."""

    body = _validate_body(AutomationControlRequest, raw_body)
    if isinstance(body, JSONResponse):
        return body
    if body.enabled is None and body.paused is None:
        return _validation_error_response(raw_body)
    command = _command(
        command_id=body.command_id,
        session_id=session_id,
        command_type=RuntimeCommandType.UPDATE_AUTOMATION_CONTROL,
        now=now,
        permission_context=_request_permission_context(request),
        payload={
            "enabled": body.enabled,
            "paused": body.paused,
            "reason": body.reason,
        },
    )
    return _call_runtime(runtime_service, "update_automation_control", command, now)


@router.post("/sessions/{session_id}/manual-close", response_model=None)
def manual_close_endpoint(
    session_id: str,
    request: Request,
    raw_body: object = Body(...),
    runtime_service: object = Depends(get_runtime_service),
    now: datetime = Depends(get_now),
) -> dict[str, object] | JSONResponse:
    """Request manual close by delegating to runtime service."""

    body = _validate_body(ManualCloseRequest, raw_body)
    if isinstance(body, JSONResponse):
        return body
    command = _command(
        command_id=body.command_id,
        session_id=session_id,
        command_type=RuntimeCommandType.MANUAL_CLOSE,
        now=now,
        permission_context=_request_permission_context(request),
        payload={"reason": body.reason},
    )
    return _call_runtime(runtime_service, "request_manual_close", command, now)


@router.post("/sessions/{session_id}/tick", response_model=None)
def tick_session_endpoint(
    session_id: str,
    request: Request,
    raw_body: object = Body(...),
    runtime_service: object = Depends(get_runtime_service),
    now: datetime = Depends(get_now),
) -> dict[str, object] | JSONResponse:
    """Advance one explicit runtime tick through runtime service."""

    body = _validate_body(TickRequest, raw_body)
    if isinstance(body, JSONResponse):
        return body
    command = _command(
        command_id=body.command_id,
        session_id=session_id,
        command_type=RuntimeCommandType.TICK,
        now=now,
        permission_context=_request_permission_context(request),
        payload={},
    )
    return _call_runtime(runtime_service, "tick_session", command, now)


@router.post("/sessions/{session_id}/youtube-events", response_model=None)
def ingest_youtube_event_endpoint(
    session_id: str,
    request: Request,
    raw_body: object = Body(...),
    runtime_service: object = Depends(get_runtime_service),
    now: datetime = Depends(get_now),
) -> dict[str, object] | JSONResponse:
    """Ingest one YouTube event by delegating to runtime service."""

    body = _validate_body(YouTubeEventIngestRequest, raw_body)
    if isinstance(body, JSONResponse):
        return body
    command = _command(
        command_id=body.command_id,
        session_id=session_id,
        command_type=RuntimeCommandType.HANDLE_YOUTUBE_EVENT,
        now=now,
        permission_context=_request_permission_context(request),
        payload={
            "youtube_event": body.youtube_event,
            "polling_cursor": body.polling_cursor,
            "page_info": body.page_info,
        },
    )
    return _call_runtime(runtime_service, "handle_youtube_event", command, now)


@router.get("/sessions/{session_id}/events", response_model=None)
def get_session_events_endpoint(
    session_id: str,
    limit: int = 100,
    query_service: object = Depends(get_query_service),
) -> dict[str, object] | JSONResponse:
    """Return public event history through query service."""

    safe_limit = max(1, min(int(limit), 500))
    try:
        events = query_service.get_session_events(session_id, safe_limit)
    except V2QueryServiceError:
        return _query_not_found_response(session_id)
    return {
        "session_id": session_id,
        "events": _sanitize_public_payload(list(events)),
    }


@router.get("/sessions/{session_id}/operator-stream", response_model=None)
def operator_stream_endpoint(
    session_id: str,
    query_service: object = Depends(get_query_service),
) -> StreamingResponse | JSONResponse:
    """Return operator-safe SSE stream."""

    try:
        events = _prime_event_iterable(query_service.iter_operator_events(session_id))
    except V2QueryServiceError:
        return _query_not_found_response(session_id)
    return StreamingResponse(
        _sse_stream(events, display_safe=False),
        media_type="text/event-stream",
    )


@router.get("/sessions/{session_id}/display-stream", response_model=None)
def display_stream_endpoint(
    session_id: str,
    query_service: object = Depends(get_query_service),
) -> StreamingResponse | JSONResponse:
    """Return display-safe SSE stream."""

    try:
        events = _prime_event_iterable(query_service.iter_display_events(session_id))
    except V2QueryServiceError:
        return _query_not_found_response(session_id)
    return StreamingResponse(
        _sse_stream(events, display_safe=True),
        media_type="text/event-stream",
    )


def _command(
    *,
    command_id: str,
    session_id: str,
    command_type: RuntimeCommandType,
    now: datetime,
    permission_context: object | None = None,
    payload: dict[str, object],
) -> RuntimeCommand:
    return RuntimeCommand(
        command_id=command_id,
        session_id=session_id,
        command_type=command_type,
        issued_at=now,
        permission_context=permission_context,
        payload={key: value for key, value in payload.items() if value is not None},
    )


def _request_permission_context(request: Request) -> object | None:
    return getattr(request.state, "youtubebridge_v2_permission", None)


def _status_with_permission_context(body: object, request: Request) -> dict[str, object]:
    data = _object_to_dict(body).copy()
    permission_context = _request_permission_context(request)
    permission_group = getattr(permission_context, "permission_group", "")
    if permission_group:
        data["permission_group"] = _enum_value(permission_group)
    return _sanitize_public_payload(data)


def _validate_body(model_type: type[BaseModel], raw_body: object) -> BaseModel | JSONResponse:
    try:
        return model_type.model_validate(raw_body)
    except ValidationError:
        return _validation_error_response(raw_body)


def _validation_error_response(raw_body: object) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "validation_error",
                "message": "invalid request body",
            },
            "correlation_id": f"runtime-{_command_id_from_raw(raw_body)}",
        },
    )


def _command_id_from_raw(raw_body: object) -> str:
    if isinstance(raw_body, dict):
        return str(raw_body.get("command_id") or "unknown")
    return "unknown"


def _call_runtime(
    runtime_service: object,
    method_name: str,
    command: RuntimeCommand,
    now: datetime,
) -> dict[str, object] | JSONResponse:
    method = getattr(runtime_service, method_name)
    try:
        result = method(command, now)
    except KeyError:
        return _query_not_found_response(command.session_id)
    except Exception as exc:
        return _service_error_response(command, exc)
    return _service_result_body(result)


def _service_result_body(result: object) -> dict[str, object]:
    data = _object_to_dict(result)
    return _sanitize_public_payload(
        {
            "status": data.get("status", ""),
            "session_id": data.get("session_id", ""),
            "phase": _enum_value(data.get("phase")),
            "events": [_event_body(event) for event in data.get("events", [])],
            "errors": data.get("errors", []),
            "correlation_id": data.get("correlation_id", ""),
        }
    )


def _event_body(event: object) -> dict[str, object]:
    data = _object_to_dict(event)
    return {
        "event_type": data.get("event_type", ""),
        "session_id": data.get("session_id", ""),
        "phase": _enum_value(data.get("phase")),
        "payload": data.get("payload", {}),
        "correlation_id": data.get("correlation_id", ""),
    }


def _service_error_response(command: RuntimeCommand, _exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "service_error",
                "message": "request failed",
            },
            "correlation_id": f"runtime-{command.command_id}",
        },
    )


def _query_not_found_response(session_id: str) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={
            "error": {
                "code": "session_not_found",
                "message": "session not found",
            },
            "correlation_id": f"query-{session_id}",
        },
    )


def _prime_event_iterable(events: Iterable[object]) -> Iterable[object]:
    iterator = iter(events)
    try:
        first = next(iterator)
    except StopIteration:
        return iter(())
    return chain((first,), iterator)


def _sse_stream(
    events: Iterable[object],
    *,
    display_safe: bool,
) -> Iterable[str]:
    for event in events:
        payload = _display_safe_payload(event) if display_safe else _sanitize_public_payload(event)
        yield "data: " + json.dumps(payload, ensure_ascii=False, default=str) + "\n\n"


def _display_safe_payload(event: object) -> object:
    return sanitize_display_value(event)


def _object_to_dict(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return value
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "__dict__"):
        return vars(value)
    return {"value": value}


def _enum_value(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    return value


def _sanitize_public_payload(value: Any) -> Any:
    return _sanitize_payload(value, _PUBLIC_FORBIDDEN_KEYS)


_PUBLIC_FORBIDDEN_KEYS = {
    "hidden_prompt",
    "raw_prompt",
    "raw_payload",
    "raw_memoriacore_payload",
    "raw_adapter_payload",
    "topic_pack",
    "raw_topic_pack",
    "youtube_raw",
    "memoriacore_raw",
    "factcard",
    "fact_card",
    "topic_pack_fact_cards",
    "raw_factcard",
    "raw_fact_card",
    "raw_fact_cards",
    "access_token",
    "authorization",
    "secret",
    "token",
}


def _sanitize_payload(value: Any, forbidden_keys: set[str]) -> Any:
    if isinstance(value, dict):
        return {
            key: _sanitize_payload(inner_value, forbidden_keys)
            for key, inner_value in value.items()
            if str(key).lower() not in forbidden_keys
        }
    if isinstance(value, list):
        return [_sanitize_payload(item, forbidden_keys) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_payload(item, forbidden_keys) for item in value)
    return value


__all__ = [
    "ApiKeyCreateRequest",
    "bind_plan_endpoint",
    "create_session_endpoint",
    "create_api_key_endpoint",
    "delete_api_key_endpoint",
    "display_stream_endpoint",
    "get_now",
    "get_phase_endpoint",
    "get_query_service",
    "get_runtime_service",
    "get_storage_manager",
    "list_api_keys_endpoint",
    "get_session_endpoint",
    "get_session_events_endpoint",
    "ingest_youtube_event_endpoint",
    "manual_close_endpoint",
    "operator_stream_endpoint",
    "router",
    "tick_session_endpoint",
    "update_automation_control_endpoint",
    "update_aftertalk_policy_endpoint",
]
