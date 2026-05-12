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
from typing import Any, Iterable, Literal

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, ValidationError

from YouTubeBridgeV2.runtime.application_service import (
    RuntimeCommand,
    RuntimeCommandType,
)


router = APIRouter(prefix="/v2", tags=["YouTubeBridgeV2"])


class RuntimeServiceNotConfigured(RuntimeError):
    """V2 runtime service 尚未由 application wiring 注入."""


class QueryServiceNotConfigured(RuntimeError):
    """V2 query service 尚未由 application wiring 注入."""


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


class ManualCloseRequest(BaseModel):
    command_id: str = Field(..., min_length=1)
    reason: str | None = None


def get_runtime_service() -> object:
    """FastAPI dependency placeholder for Runtime Application Service."""

    raise RuntimeServiceNotConfigured("runtime service dependency is not configured")


def get_query_service() -> object:
    """FastAPI dependency placeholder for read/query service."""

    raise QueryServiceNotConfigured("query service dependency is not configured")


def get_now() -> datetime:
    """FastAPI dependency for deterministic command timestamps in tests."""

    return datetime.now(timezone.utc)


@router.post("/sessions", response_model=None)
def create_session_endpoint(
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
        payload={
            "plan_id": body.plan_id,
            "aftertalk_policy": body.aftertalk_policy,
            "metadata": body.metadata,
        },
    )
    return _call_runtime(runtime_service, "create_session", command, now)


@router.get("/sessions/{session_id}")
def get_session_endpoint(
    session_id: str,
    query_service: object = Depends(get_query_service),
) -> dict[str, object]:
    """Return public V2 session status through query service."""

    return _sanitize_public_payload(query_service.get_session(session_id))


@router.post("/sessions/{session_id}/plan", response_model=None)
def bind_plan_endpoint(
    session_id: str,
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
        payload={"plan": body.plan},
    )
    return _call_runtime(runtime_service, "bind_plan", command, now)


@router.get("/sessions/{session_id}/phase")
def get_phase_endpoint(
    session_id: str,
    query_service: object = Depends(get_query_service),
) -> dict[str, object]:
    """Return phase status body through query service."""

    return _sanitize_public_payload(query_service.get_phase(session_id))


@router.post("/sessions/{session_id}/aftertalk-policy", response_model=None)
def update_aftertalk_policy_endpoint(
    session_id: str,
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
        payload={"aftertalk_policy": body.aftertalk_policy},
    )
    return _call_runtime(runtime_service, "update_aftertalk_policy", command, now)


@router.post("/sessions/{session_id}/manual-close", response_model=None)
def manual_close_endpoint(
    session_id: str,
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
        payload={"reason": body.reason},
    )
    return _call_runtime(runtime_service, "request_manual_close", command, now)


@router.get("/sessions/{session_id}/events")
def get_session_events_endpoint(
    session_id: str,
    limit: int = 100,
    query_service: object = Depends(get_query_service),
) -> dict[str, object]:
    """Return public event history through query service."""

    safe_limit = max(1, min(int(limit), 500))
    events = query_service.get_session_events(session_id, safe_limit)
    return {
        "session_id": session_id,
        "events": _sanitize_public_payload(list(events)),
    }


@router.get("/sessions/{session_id}/operator-stream")
def operator_stream_endpoint(
    session_id: str,
    query_service: object = Depends(get_query_service),
) -> StreamingResponse:
    """Return operator-safe SSE stream."""

    return StreamingResponse(
        _sse_stream(query_service.iter_operator_events(session_id), display_safe=False),
        media_type="text/event-stream",
    )


@router.get("/sessions/{session_id}/display-stream")
def display_stream_endpoint(
    session_id: str,
    query_service: object = Depends(get_query_service),
) -> StreamingResponse:
    """Return display-safe SSE stream."""

    return StreamingResponse(
        _sse_stream(query_service.iter_display_events(session_id), display_safe=True),
        media_type="text/event-stream",
    )


def _command(
    *,
    command_id: str,
    session_id: str,
    command_type: RuntimeCommandType,
    now: datetime,
    payload: dict[str, object],
) -> RuntimeCommand:
    return RuntimeCommand(
        command_id=command_id,
        session_id=session_id,
        command_type=command_type,
        issued_at=now,
        permission_context=None,
        payload={key: value for key, value in payload.items() if value is not None},
    )


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


def _sse_stream(
    events: Iterable[object],
    *,
    display_safe: bool,
) -> Iterable[str]:
    for event in events:
        payload = _display_safe_payload(event) if display_safe else _sanitize_public_payload(event)
        yield "data: " + json.dumps(payload, ensure_ascii=False, default=str) + "\n\n"


def _display_safe_payload(event: object) -> object:
    return _sanitize_payload(event, _DISPLAY_FORBIDDEN_KEYS)


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


_DISPLAY_FORBIDDEN_KEYS = _PUBLIC_FORBIDDEN_KEYS | {
    "diagnostics",
    "operator_controls",
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
    "bind_plan_endpoint",
    "create_session_endpoint",
    "display_stream_endpoint",
    "get_now",
    "get_phase_endpoint",
    "get_query_service",
    "get_runtime_service",
    "get_session_endpoint",
    "get_session_events_endpoint",
    "manual_close_endpoint",
    "operator_stream_endpoint",
    "router",
    "update_aftertalk_policy_endpoint",
]
