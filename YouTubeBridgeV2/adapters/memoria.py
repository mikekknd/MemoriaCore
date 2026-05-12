"""MemoriaCore adapter request/response contracts for YouTubeBridgeV2.

本模組只負責 request mapping、response normalization、error
classification 與 public summary redaction。實際 HTTP transport、
retry loop、storage write、phase transition 與 UI event 不在此處理。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from YouTubeBridgeV2.live_episode_plan.runner import PlannedTurnIntent
from YouTubeBridgeV2.runtime.aftertalk import AftertalkTurnRequest


MEMORIA_CHAT_SYNC_ENDPOINT = "/api/v1/chat/sync"
YOUTUBE_LIVE_SOURCE = "youtube_live_director"
YOUTUBE_LIVE_USER_ID = "__youtube_live__"


@dataclass(frozen=True)
class MemoriaCorrelationMetadata:
    """V2 request 與 MemoriaCore trace/session 的關聯資料."""

    correlation_id: str
    request_id: str
    v2_session_id: str
    memoria_session_id: str | None = None
    trace_id: str | None = None


@dataclass(frozen=True)
class MemoriaRequestPayload:
    """準備交給 MemoriaCore transport 的 request envelope."""

    mode: str
    endpoint: str
    body: dict[str, object]
    correlation: MemoriaCorrelationMetadata
    public_summary: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class NormalizedMemoriaResponse:
    """MemoriaCore response 的 V2 normalized shape."""

    mode: str
    memoria_session_id: str | None
    messages: tuple[dict[str, object], ...]
    correlation: MemoriaCorrelationMetadata
    public_summary: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class MemoriaAdapterError:
    """MemoriaCore adapter error classification."""

    error_type: str
    retryable: bool
    public_summary: dict[str, object] = field(default_factory=dict)
    status_code: int | None = None


def build_memoria_request(
    intent: PlannedTurnIntent | AftertalkTurnRequest,
    context: dict[str, object],
) -> MemoriaRequestPayload:
    """將 planned show 或 aftertalk intent 映射成 MemoriaCore request envelope."""

    correlation = _correlation_from_context(context)
    if isinstance(intent, PlannedTurnIntent):
        return _planned_turn_request(intent, context, correlation)
    if isinstance(intent, AftertalkTurnRequest):
        return _aftertalk_request(intent, context, correlation)
    raise TypeError("unsupported MemoriaCore intent")


def normalize_memoria_response(
    response_payload: dict[str, object],
    correlation_metadata: MemoriaCorrelationMetadata,
) -> NormalizedMemoriaResponse | MemoriaAdapterError:
    """將 MemoriaCore response payload 正規化成 V2 response 或 adapter error."""

    mode = _response_mode(response_payload)
    if mode == "group_chat":
        messages_or_error = _group_chat_messages(response_payload)
    else:
        messages_or_error = _chat_messages(response_payload)

    if isinstance(messages_or_error, MemoriaAdapterError):
        return messages_or_error

    trace_id = response_payload.get("trace_id")
    memoria_session_id = _optional_string(response_payload.get("session_id"))
    correlation = replace(
        correlation_metadata,
        memoria_session_id=memoria_session_id or correlation_metadata.memoria_session_id,
        trace_id=_optional_string(trace_id) or correlation_metadata.trace_id,
    )
    public_summary = _redact_public_value(
        {
            **_correlation_public_summary(correlation),
            "mode": mode,
            "message_count": len(messages_or_error),
            "memoria_session_id": correlation.memoria_session_id,
            "trace_id": correlation.trace_id,
            "summary": response_payload.get("summary", {}),
        }
    )
    return NormalizedMemoriaResponse(
        mode=mode,
        memoria_session_id=correlation.memoria_session_id,
        messages=messages_or_error,
        correlation=correlation,
        public_summary=public_summary,
    )


def classify_memoria_error(error: BaseException) -> MemoriaAdapterError:
    """將 timeout/transport/auth/unknown error 轉成 adapter error，不改 phase."""

    status_code = getattr(error, "status_code", None)
    if isinstance(error, TimeoutError):
        return MemoriaAdapterError(
            error_type="timeout",
            retryable=True,
            public_summary={"error_type": "timeout", "retryable": True},
        )

    if status_code in {401, 403}:
        return MemoriaAdapterError(
            error_type="auth_failure",
            retryable=False,
            status_code=status_code,
            public_summary={
                "error_type": "auth_failure",
                "retryable": False,
                "status_code": status_code,
            },
        )

    if hasattr(error, "status_code") or hasattr(error, "retryable"):
        retryable = bool(getattr(error, "retryable", _status_is_retryable(status_code)))
        summary = {
            "error_type": "transport_failure",
            "retryable": retryable,
        }
        if status_code is not None:
            summary["status_code"] = status_code
        return MemoriaAdapterError(
            error_type="transport_failure",
            retryable=retryable,
            status_code=status_code,
            public_summary=summary,
        )

    return MemoriaAdapterError(
        error_type="unknown",
        retryable=False,
        public_summary={"error_type": "unknown", "retryable": False},
    )


def _planned_turn_request(
    intent: PlannedTurnIntent,
    context: dict[str, object],
    correlation: MemoriaCorrelationMetadata,
) -> MemoriaRequestPayload:
    body = {
        **_live_chat_scope(context, correlation),
        "content": intent.purpose,
        "display_content": context.get("display_content"),
        "session_id": correlation.memoria_session_id,
        "character_ids": list(intent.speaker_ids),
        "group_name": context.get("group_name"),
        "external_context": _planned_external_context(intent, context, correlation),
        "include_speech": False,
        "memory_write_policy": "transient",
    }
    return MemoriaRequestPayload(
        mode="chat",
        endpoint=MEMORIA_CHAT_SYNC_ENDPOINT,
        body=_redact_public_value(body),
        correlation=correlation,
        public_summary={
            "mode": "chat",
            "turn_id": intent.turn_id,
            "speaker_ids": list(intent.speaker_ids),
            **_correlation_public_summary(correlation),
        },
    )


def _aftertalk_request(
    intent: AftertalkTurnRequest,
    context: dict[str, object],
    correlation: MemoriaCorrelationMetadata,
) -> MemoriaRequestPayload:
    if intent.cue is None or not intent.should_dispatch:
        raise ValueError("aftertalk request requires a dispatchable cue")
    speaker_ids = _clean_speaker_ids(intent.cue.speaker_rotation_hint)
    if not speaker_ids:
        raise ValueError("aftertalk request requires at least one speaker")

    body = {
        **_live_chat_scope(context, correlation),
        "content": "Continue aftertalk group chat.",
        "display_content": context.get("display_content"),
        "session_id": correlation.memoria_session_id,
        "character_ids": speaker_ids,
        "group_name": "aftertalk",
        "external_context": _aftertalk_external_context(intent, context, correlation),
        "include_speech": False,
        "memory_write_policy": "transient",
    }
    return MemoriaRequestPayload(
        mode="group_chat",
        endpoint=MEMORIA_CHAT_SYNC_ENDPOINT,
        body=_redact_public_value(body),
        correlation=correlation,
        public_summary={
            "mode": "group_chat",
            "speaker_count": len(speaker_ids),
            **_correlation_public_summary(correlation),
        },
    )


def _chat_messages(
    response_payload: dict[str, object],
) -> tuple[dict[str, object], ...] | MemoriaAdapterError:
    raw_message = response_payload.get("assistant_message")
    if not isinstance(raw_message, dict):
        if "reply" in response_payload:
            message = _normalize_message(response_payload)
            if message is None:
                return _invalid_response("reply speaker metadata is required")
            return (message,)
        raw_messages = response_payload.get("messages")
        if isinstance(raw_messages, list):
            return _group_chat_messages(response_payload)
        raw_turns = response_payload.get("turns")
        if isinstance(raw_turns, list):
            return _group_chat_messages(response_payload)
        return _invalid_response("reply is required")
    message = _normalize_message(raw_message)
    if message is None:
        return _invalid_response("assistant_message speaker metadata is required")
    return (message,)


def _group_chat_messages(
    response_payload: dict[str, object],
) -> tuple[dict[str, object], ...] | MemoriaAdapterError:
    raw_messages = response_payload.get("turns")
    if not isinstance(raw_messages, list):
        raw_messages = response_payload.get("messages")
    if not isinstance(raw_messages, list):
        return _invalid_response("turns list is required")

    normalized: list[dict[str, object]] = []
    for raw_message in raw_messages:
        if not isinstance(raw_message, dict):
            return _invalid_response("message object is required")
        message = _normalize_message(raw_message)
        if message is None:
            return _invalid_response("speaker metadata is required")
        normalized.append(message)
    return tuple(normalized)


def _normalize_message(raw_message: dict[str, object]) -> dict[str, object] | None:
    speaker_id = raw_message.get("speaker_id") or raw_message.get("character_id")
    content = raw_message.get("content") or raw_message.get("text") or raw_message.get("reply")
    if not speaker_id or content is None:
        return None
    return {
        "message_id": str(raw_message.get("message_id", raw_message.get("id", ""))),
        "speaker_id": str(speaker_id),
        "content": str(content),
    }


def _invalid_response(message: str) -> MemoriaAdapterError:
    return MemoriaAdapterError(
        error_type="invalid_response",
        retryable=False,
        public_summary={
            "error_type": "invalid_response",
            "retryable": False,
            "message": message,
        },
    )


def _response_mode(response_payload: dict[str, object]) -> str:
    if isinstance(response_payload.get("turns"), list):
        return "group_chat"
    if isinstance(response_payload.get("messages"), list):
        return "group_chat"
    return "chat"


def _live_chat_scope(
    context: dict[str, object],
    correlation: MemoriaCorrelationMetadata,
) -> dict[str, object]:
    return {
        "channel": "youtube_live",
        "channel_uid": str(context.get("channel_uid") or correlation.v2_session_id),
        "user_id": str(context.get("youtube_live_user_id") or YOUTUBE_LIVE_USER_ID),
        "channel_class": "public",
        "persona_face": "public",
    }


def _planned_external_context(
    intent: PlannedTurnIntent,
    context: dict[str, object],
    correlation: MemoriaCorrelationMetadata,
) -> dict[str, object]:
    group_turn_limit = _group_turn_limit(context, default=max(1, len(intent.speaker_ids)))
    return _redact_public_value(
        {
            "source": YOUTUBE_LIVE_SOURCE,
            "source_session_id": correlation.v2_session_id,
            "context_text": _planned_context_text(intent),
            "visible_events": [],
            "event_ids": [],
            "group_turn_limit": group_turn_limit,
            "summary": {
                "source": YOUTUBE_LIVE_SOURCE,
                "source_session_id": correlation.v2_session_id,
                "event_count": 0,
                "episode_plan_id": intent.plan_id,
                "episode_plan_turn_id": intent.turn_id,
                "episode_plan_mode": "planned_turn",
                "group_turn_limit": group_turn_limit,
                "correlation_id": correlation.correlation_id,
            },
            "live_episode_plan": {
                "mode": "planned_turn",
                "plan_id": intent.plan_id,
                "turn_id": intent.turn_id,
                "turn_index": intent.turn_index,
                "topic_cue": intent.topic_cue,
                "speaker_policy": intent.speaker_policy,
                "audience_summary": intent.audience_summary,
                "audience_handling_hint": intent.audience_handling_hint,
            },
        }
    )


def _aftertalk_external_context(
    intent: AftertalkTurnRequest,
    context: dict[str, object],
    correlation: MemoriaCorrelationMetadata,
) -> dict[str, object]:
    assert intent.cue is not None
    speaker_ids = list(intent.cue.speaker_rotation_hint)
    group_turn_limit = _group_turn_limit(context, default=max(1, len(speaker_ids)))
    public_show_summary = _redact_public_value(intent.cue.public_show_summary)
    return _redact_public_value(
        {
            "source": YOUTUBE_LIVE_SOURCE,
            "source_session_id": correlation.v2_session_id,
            "context_text": _aftertalk_context_text(public_show_summary),
            "visible_events": [],
            "event_ids": [],
            "group_turn_limit": group_turn_limit,
            "summary": {
                "source": YOUTUBE_LIVE_SOURCE,
                "source_session_id": correlation.v2_session_id,
                "event_count": 0,
                "episode_plan_mode": "aftertalk",
                "group_turn_limit": group_turn_limit,
                "correlation_id": correlation.correlation_id,
            },
            "aftertalk": {
                "group_chat_mode": intent.group_chat_mode,
                "cue": {
                    "session_id": intent.cue.session_id,
                    "public_show_summary": public_show_summary,
                    "metadata": intent.cue.metadata,
                },
            },
        }
    )


def _planned_context_text(intent: PlannedTurnIntent) -> str:
    parts = [f"Planned turn purpose: {intent.purpose}"]
    if intent.topic_cue:
        parts.append(f"Topic cue: {intent.topic_cue}")
    if intent.audience_summary:
        parts.append(f"Audience summary: {_redact_public_value(intent.audience_summary)}")
    return "\n".join(parts)


def _aftertalk_context_text(public_show_summary: object) -> str:
    return f"Aftertalk context: {_redact_public_value(public_show_summary)}"


def _correlation_from_context(context: dict[str, object]) -> MemoriaCorrelationMetadata:
    return MemoriaCorrelationMetadata(
        correlation_id=str(context.get("correlation_id", "")),
        request_id=str(context.get("request_id", "")),
        v2_session_id=str(context.get("v2_session_id", "")),
        memoria_session_id=_optional_string(context.get("memoria_session_id")),
        trace_id=_optional_string(context.get("trace_id")),
    )


def _correlation_public_summary(correlation: MemoriaCorrelationMetadata) -> dict[str, object]:
    return {
        "correlation_id": correlation.correlation_id,
        "request_id": correlation.request_id,
        "v2_session_id": correlation.v2_session_id,
    }


def _redact_public_value(value: Any) -> Any:
    forbidden_keys = {
        "hidden_prompt",
        "raw_prompt",
        "raw_payload",
        "raw_memoriacore_payload",
        "memoriacore_raw",
        "topic_pack",
        "raw_topic_pack",
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


def _group_turn_limit(context: dict[str, object], *, default: int) -> int:
    try:
        value = int(context.get("group_turn_limit", default))
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, 12))


def _clean_speaker_ids(raw_speaker_ids: tuple[str, ...]) -> list[str]:
    return [
        speaker_id
        for raw in raw_speaker_ids
        if (speaker_id := str(raw or "").strip())
    ]


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _status_is_retryable(status_code: object) -> bool:
    return isinstance(status_code, int) and status_code >= 500


__all__ = [
    "MemoriaAdapterError",
    "MemoriaCorrelationMetadata",
    "MemoriaRequestPayload",
    "NormalizedMemoriaResponse",
    "build_memoria_request",
    "classify_memoria_error",
    "normalize_memoria_response",
]
