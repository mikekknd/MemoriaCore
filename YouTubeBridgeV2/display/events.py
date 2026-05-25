"""Display-safe event normalization for YouTubeBridgeV2."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Mapping


DISPLAY_CONTRACT_VERSION = "v1"


def normalize_display_event(event: object) -> dict[str, object]:
    """Return one display-safe event envelope consumable by Chat Display UI."""

    raw_event = sanitize_display_value(_object_to_dict(event))
    source_event_type = _safe_text(raw_event.get("event_type"))
    event_id = _safe_text(raw_event.get("event_id") or raw_event.get("id"))
    created_at = _iso_text(raw_event.get("created_at") or raw_event.get("createdAt"))
    public_metadata = _object_to_dict(
        raw_event.get("public_metadata")
        or raw_event.get("public_payload")
        or raw_event.get("payload")
        or {}
    )
    display_event = _object_to_dict(public_metadata.get("display_event"))
    if not display_event:
        display_event = _object_to_dict(raw_event.get("display_event"))
    if not display_event and _is_display_event_type(source_event_type):
        display_event = {
            **public_metadata,
            "event_type": source_event_type,
            "event_id": event_id,
        }
    if display_event:
        return _display_event_envelope(
            display_event,
            event_id=event_id,
            source_event_type=source_event_type,
            created_at=created_at,
        )
    return _system_state_envelope(
        public_metadata,
        event_id=event_id,
        source_event_type=source_event_type,
        created_at=created_at,
    )


def sanitize_display_value(value: Any) -> Any:
    """Remove display-forbidden keys and redact display-forbidden text."""

    if isinstance(value, dict):
        return {
            str(key): sanitize_display_value(inner_value)
            for key, inner_value in value.items()
            if not _is_forbidden_key(key)
        }
    if isinstance(value, list):
        return [sanitize_display_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_display_value(item) for item in value)
    if isinstance(value, str):
        return _safe_text(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _display_event_envelope(
    display_event: dict[str, object],
    *,
    event_id: str,
    source_event_type: str,
    created_at: str,
) -> dict[str, object]:
    display_type = _normalize_display_type(display_event.get("event_type"))
    if display_type == "super_chat":
        payload = _super_chat_payload(display_event)
    elif display_type == "character_response":
        payload = _character_response_payload(display_event)
    elif display_type in {"system_state", "closing_status", "aftertalk_status"}:
        payload = _system_payload(display_event, fallback_status=display_type)
        if display_type == "aftertalk_status":
            display_type = "system_state"
    else:
        display_type = "audience_message"
        payload = _audience_payload(display_event)
    return _envelope(
        event_type=display_type,
        event_id=_safe_text(display_event.get("event_id") or event_id),
        source_event_type=source_event_type,
        created_at=created_at,
        public_payload=payload,
    )


def _system_state_envelope(
    metadata: dict[str, object],
    *,
    event_id: str,
    source_event_type: str,
    created_at: str,
) -> dict[str, object]:
    return _envelope(
        event_type="system_state",
        event_id=event_id,
        source_event_type=source_event_type,
        created_at=created_at,
        public_payload=_system_payload(metadata, fallback_status=source_event_type),
    )


def _envelope(
    *,
    event_type: str,
    event_id: str,
    source_event_type: str,
    created_at: str,
    public_payload: dict[str, object],
) -> dict[str, object]:
    envelope: dict[str, object] = {
        "display_contract_version": DISPLAY_CONTRACT_VERSION,
        "event_id": event_id,
        "event_type": event_type,
        "source_event_type": source_event_type,
        "public_payload": sanitize_display_value(public_payload),
    }
    if created_at:
        envelope["created_at"] = created_at
    return sanitize_display_value(envelope)


def _audience_payload(values: Mapping[str, object]) -> dict[str, object]:
    return {
        "author_display_name": _safe_text(
            values.get("author_display_name") or values.get("authorDisplayName")
        ),
        "message_text": _safe_text(
            values.get("message_text")
            or values.get("messageText")
            or values.get("text")
        ),
        "timestamp": _safe_text(
            values.get("timestamp")
            or values.get("published_at")
            or values.get("publishedAt")
        ),
        "display_flags": _display_flags(values),
    }


def _super_chat_payload(values: Mapping[str, object]) -> dict[str, object]:
    super_chat = _object_to_dict(values.get("super_chat") or values.get("superChat"))
    return {
        **_audience_payload(values),
        "amount_display_string": _safe_text(
            values.get("amount_display_string")
            or values.get("amountDisplayString")
            or super_chat.get("amount_display_string")
            or super_chat.get("amountDisplayString")
            or values.get("amount")
        ),
        "currency": _safe_text(values.get("currency") or super_chat.get("currency")),
        "acknowledgement_status": _safe_text(
            values.get("acknowledgement_status")
            or values.get("acknowledgementStatus")
            or super_chat.get("acknowledgement_status")
            or super_chat.get("acknowledgementStatus")
        ),
    }


def _character_response_payload(values: Mapping[str, object]) -> dict[str, object]:
    return {
        "character_name": _safe_text(
            values.get("character_name")
            or values.get("characterName")
            or values.get("speaker_name")
        ),
        "role_label": _safe_text(
            values.get("role_label") or values.get("roleLabel") or values.get("role")
        ),
        "response_text": _safe_text(
            values.get("response_text")
            or values.get("responseText")
            or values.get("message_text")
            or values.get("text")
        ),
        "phase": _safe_text(values.get("phase")),
        "presentation": sanitize_display_value(
            values.get("presentation") or values.get("presentation_metadata") or {}
        ),
    }


def _system_payload(
    values: Mapping[str, object],
    *,
    fallback_status: str,
) -> dict[str, object]:
    payload = _object_to_dict(values.get("payload"))
    summary = _object_to_dict(values.get("summary"))
    nested_summary = _object_to_dict(payload.get("summary"))
    public_summary = _object_to_dict(
        values.get("public_summary") or values.get("publicSummary")
    )
    message = _safe_text(
        values.get("message")
        or public_summary.get("message")
        or nested_summary.get("message")
        or summary.get("message")
        or fallback_status
    )
    phase = _safe_text(
        values.get("phase") or payload.get("phase") or public_summary.get("phase")
    )
    return {
        "phase": phase or "unknown",
        "message": message,
        "status": _safe_text(
            values.get("status") or public_summary.get("status") or fallback_status
        ),
    }


def _display_flags(values: Mapping[str, object]) -> dict[str, bool]:
    raw_flags = (
        values.get("display_flags")
        or values.get("flags")
        or values.get("author_badges")
        or []
    )
    if isinstance(raw_flags, dict):
        keys = [
            key
            for key, value in raw_flags.items()
            if value is True or value == "true" or value == 1
        ]
    elif isinstance(raw_flags, (list, tuple, set)):
        keys = list(raw_flags)
    else:
        keys = []
    flags: dict[str, bool] = {}
    for key in keys:
        normalized = _normalize_flag_key(key)
        if normalized in _DISPLAY_FLAG_ALLOWLIST:
            flags[normalized] = True
    return flags


def _is_display_event_type(value: object) -> bool:
    return _normalize_display_type(value) in {
        "audience_message",
        "character_response",
        "super_chat",
        "system_state",
        "closing_status",
        "aftertalk_status",
    }


def _normalize_display_type(value: object) -> str:
    text = _safe_text(value).lower()
    mapping = {
        "display_message": "audience_message",
        "display_character_response": "character_response",
        "display_super_chat": "super_chat",
        "phase_update": "system_state",
    }
    return mapping.get(text, text)


def _normalize_flag_key(value: object) -> str:
    normalized = _safe_text(value).lower().replace("-", "_").replace(" ", "_")
    if normalized == "sponsor":
        return "member"
    return normalized


def _object_to_dict(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return dict(value)
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "__dict__"):
        return dict(vars(value))
    return {}


def _iso_text(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return _safe_text(value)


def _safe_text(value: object) -> str:
    if value is None:
        return ""
    text = str(value)
    lowered = text.lower()
    if any(pattern in lowered for pattern in _FORBIDDEN_TEXT_PATTERNS):
        return "[redacted]"
    return text


def _is_forbidden_key(key: object) -> bool:
    lowered = str(key).lower()
    return lowered in _FORBIDDEN_KEYS or any(
        pattern in lowered for pattern in _FORBIDDEN_KEY_PATTERNS
    )


_DISPLAY_FLAG_ALLOWLIST = {
    "held_for_review",
    "highlighted",
    "member",
    "moderator",
    "paid_member",
    "pinned",
    "verified",
}

_FORBIDDEN_KEYS = {
    "access_token",
    "authorization",
    "diagnostics",
    "headers",
    "hidden_prompt",
    "operator_controls",
    "operator_only",
    "operator_only_metadata",
    "password",
    "raw_adapter_payload",
    "raw_fact_card",
    "raw_fact_cards",
    "raw_factcard",
    "raw_memoriacore_payload",
    "raw_payload",
    "raw_prompt",
    "raw_super_chat",
    "raw_super_chat_payload",
    "raw_topic_pack",
    "secret",
    "token",
    "topic_pack",
    "topic_pack_fact_cards",
    "youtube_raw",
}

_FORBIDDEN_KEY_PATTERNS = (
    "api-key",
    "api_key",
    "apikey",
    "authorization",
    "client_secret",
    "credential",
    "manual_close",
    "operator_only",
    "refresh_token",
    "secret",
    "token",
)

_FORBIDDEN_TEXT_PATTERNS = (
    "authorization:",
    "bearer ",
    "basic ",
    "client_secret",
    "refresh_token",
    "x-api-key",
)

__all__ = [
    "DISPLAY_CONTRACT_VERSION",
    "normalize_display_event",
    "sanitize_display_value",
]
