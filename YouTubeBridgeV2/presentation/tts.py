"""Presentation/TTS contracts for YouTubeBridgeV2.

本模組只消費已完成的 interaction，建立 display-safe presentation event、
provider-neutral TTS request，以及 delivery ack/timeout 結果。真實 TTS
provider、storage 寫入、phase decision、YouTube polling 與 UI control 都由
呼叫端或其他 adapter 負責。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, MutableMapping


@dataclass(frozen=True)
class PresentationDisplayMetadata:
    """可交給 display/TTS consumer 的公開 presentation metadata."""

    voice_state: str = ""
    visual_state: str = ""
    phase: str = ""
    role_label: str = ""
    subtitle: str = ""
    public_payload: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "voice_state", _safe_text(self.voice_state))
        object.__setattr__(self, "visual_state", _safe_text(self.visual_state))
        object.__setattr__(self, "phase", _safe_text(self.phase))
        object.__setattr__(self, "role_label", _safe_text(self.role_label))
        object.__setattr__(self, "subtitle", _safe_text(self.subtitle))
        object.__setattr__(self, "public_payload", _redact_public_value(self.public_payload))


@dataclass(frozen=True)
class PresentationEvent:
    """已完成 interaction 轉成的 presentation event."""

    event_id: str
    interaction_id: str
    session_id: str
    character_id: str
    character_name: str
    role_label: str
    response_text: str
    completed_at: datetime | None
    display_metadata: PresentationDisplayMetadata
    display_event: dict[str, object]
    should_present: bool
    skip_reason: str | None = None
    voice_id: str = ""
    public_payload: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _safe_text(self.event_id))
        object.__setattr__(self, "interaction_id", _safe_text(self.interaction_id))
        object.__setattr__(self, "session_id", _safe_text(self.session_id))
        object.__setattr__(self, "character_id", _safe_text(self.character_id))
        object.__setattr__(self, "character_name", _safe_text(self.character_name))
        object.__setattr__(self, "role_label", _safe_text(self.role_label))
        object.__setattr__(self, "response_text", _safe_text(self.response_text))
        object.__setattr__(self, "display_event", _redact_public_value(self.display_event))
        object.__setattr__(self, "skip_reason", _optional_text(self.skip_reason))
        object.__setattr__(self, "voice_id", _safe_text(self.voice_id))
        object.__setattr__(self, "public_payload", _redact_public_value(self.public_payload))


@dataclass(frozen=True)
class TTSRequest:
    """Provider-neutral TTS delivery request."""

    delivery_id: str
    event_id: str
    session_id: str
    character_id: str
    text: str
    voice_id: str
    provider: str
    queue_position: int
    status: str = "pending"
    metadata: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "delivery_id", _safe_text(self.delivery_id))
        object.__setattr__(self, "event_id", _safe_text(self.event_id))
        object.__setattr__(self, "session_id", _safe_text(self.session_id))
        object.__setattr__(self, "character_id", _safe_text(self.character_id))
        object.__setattr__(self, "text", _safe_text(self.text))
        object.__setattr__(self, "voice_id", _safe_text(self.voice_id))
        object.__setattr__(self, "provider", _safe_text(self.provider))
        object.__setattr__(self, "queue_position", int(self.queue_position))
        object.__setattr__(self, "status", _safe_text(self.status))
        object.__setattr__(self, "metadata", _redact_public_value(self.metadata))


@dataclass(frozen=True)
class DeliveryAck:
    """Presentation/TTS delivery 成功 ack 結果."""

    delivery_id: str
    status: str
    acknowledged_at: datetime
    duplicate: bool = False
    public_summary: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "delivery_id", _safe_text(self.delivery_id))
        object.__setattr__(self, "status", _safe_text(self.status))
        object.__setattr__(self, "public_summary", _public_summary(self))


@dataclass(frozen=True)
class DeliveryTimeoutResult:
    """Presentation/TTS delivery timeout 結果，不要求 runtime phase 變更."""

    delivery_id: str
    status: str
    timeout_seconds: int
    phase_transition_requested: bool = False
    metadata: dict[str, object] = field(default_factory=dict)
    public_summary: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "delivery_id", _safe_text(self.delivery_id))
        object.__setattr__(self, "status", _safe_text(self.status))
        object.__setattr__(self, "timeout_seconds", int(self.timeout_seconds))
        object.__setattr__(self, "phase_transition_requested", False)
        object.__setattr__(self, "metadata", _redact_public_value(self.metadata))
        object.__setattr__(self, "public_summary", _timeout_summary(self))


def build_presentation_event(interaction: dict[str, object]) -> PresentationEvent:
    """將 completed interaction 轉成 display-safe presentation event."""

    interaction_id = _first_text(interaction, "interaction_id", "id")
    event_id = _first_text(interaction, "event_id", "message_id")
    session_id = _first_text(interaction, "session_id")
    character_id = _first_text(interaction, "character_id", "speaker_id")
    character_name = _first_text(interaction, "character_name", "speaker_name")
    role_label = _first_text(interaction, "role_label")
    response_text = _first_text(interaction, "response_text", "content", "reply")
    completed_at = _coerce_datetime(interaction.get("completed_at"))
    voice_id = _first_text(interaction, "voice_id")
    metadata = _display_metadata(interaction, role_label)

    if _first_text(interaction, "status").lower() != "completed":
        return _skipped_event(
            event_id=event_id,
            interaction_id=interaction_id,
            session_id=session_id,
            character_id=character_id,
            character_name=character_name,
            role_label=role_label,
            completed_at=completed_at,
            display_metadata=metadata,
            voice_id=voice_id,
            reason="interaction_not_completed",
        )

    if not event_id or not interaction_id or not session_id:
        return _skipped_event(
            event_id=event_id,
            interaction_id=interaction_id,
            session_id=session_id,
            character_id=character_id,
            character_name=character_name,
            role_label=role_label,
            completed_at=completed_at,
            display_metadata=metadata,
            voice_id=voice_id,
            reason="missing_identity",
        )

    if not response_text:
        return _skipped_event(
            event_id=event_id,
            interaction_id=interaction_id,
            session_id=session_id,
            character_id=character_id,
            character_name=character_name,
            role_label=role_label,
            completed_at=completed_at,
            display_metadata=metadata,
            voice_id=voice_id,
            reason="missing_response_text",
        )

    display_event = {
        "event_type": "character_response",
        "event_id": event_id,
        "session_id": session_id,
        "character_name": character_name,
        "role_label": role_label,
        "response_text": response_text,
        "presentation": asdict(metadata),
    }
    return PresentationEvent(
        event_id=event_id,
        interaction_id=interaction_id,
        session_id=session_id,
        character_id=character_id,
        character_name=character_name,
        role_label=role_label,
        response_text=response_text,
        completed_at=completed_at,
        display_metadata=metadata,
        display_event=display_event,
        should_present=True,
        skip_reason=None,
        voice_id=voice_id,
        public_payload=_event_public_payload(interaction),
    )


def enqueue_tts_request(
    event: PresentationEvent,
    policy: dict[str, object],
    *,
    queue: list[TTSRequest] | None = None,
) -> TTSRequest | None:
    """依 TTS policy 建立並可選擇放入 provider-neutral queue."""

    if not event.should_present or not event.response_text:
        return None
    if not bool(policy.get("enabled", False)):
        return None

    queue_position = len(queue) + 1 if queue is not None else 1
    request = TTSRequest(
        delivery_id=f"tts-{event.event_id}",
        event_id=event.event_id,
        session_id=event.session_id,
        character_id=event.character_id,
        text=event.response_text,
        voice_id=event.voice_id or _safe_text(policy.get("default_voice_id", "")),
        provider=_safe_text(policy.get("provider", "default")),
        queue_position=queue_position,
        status="pending",
        metadata={
            **event.display_metadata.public_payload,
            "interaction_id": event.interaction_id,
        },
    )
    if queue is not None:
        queue.append(request)
    return request


def record_delivery_ack(
    delivery_id: str,
    *,
    delivery_state: MutableMapping[str, dict[str, object]] | None = None,
    acknowledged_at: datetime | None = None,
) -> DeliveryAck:
    """記錄 delivery ack。若 state 已是 delivered，回傳 idempotent duplicate ack."""

    state = _state_for(delivery_id, delivery_state)
    duplicate = state.get("status") == "delivered"
    state["status"] = "delivered"
    state["acknowledged_at"] = acknowledged_at or _utc_now()
    return DeliveryAck(
        delivery_id=delivery_id,
        status="delivered",
        acknowledged_at=state["acknowledged_at"],
        duplicate=duplicate,
    )


def record_delivery_timeout(
    delivery_id: str,
    *,
    timeout_seconds: int,
    delivery_state: MutableMapping[str, dict[str, object]] | None = None,
    metadata: dict[str, object] | None = None,
) -> DeliveryTimeoutResult:
    """記錄 delivery timeout，不產生 runtime phase request."""

    state = _state_for(delivery_id, delivery_state)
    if state.get("status") == "delivered":
        return DeliveryTimeoutResult(
            delivery_id=delivery_id,
            status="delivered",
            timeout_seconds=int(timeout_seconds),
            phase_transition_requested=False,
            metadata=metadata or {},
            public_summary={
                "delivery_id": delivery_id,
                "status": "delivered",
                "timeout_seconds": int(timeout_seconds),
                "timeout_ignored": True,
                "reason": "already_delivered",
            },
        )

    state["status"] = "timeout"
    state["timeout_seconds"] = int(timeout_seconds)
    return DeliveryTimeoutResult(
        delivery_id=delivery_id,
        status="timeout",
        timeout_seconds=int(timeout_seconds),
        phase_transition_requested=False,
        metadata=metadata or {},
    )


def _display_metadata(
    interaction: dict[str, object],
    role_label: str,
) -> PresentationDisplayMetadata:
    presentation = interaction.get("presentation")
    if not isinstance(presentation, dict):
        presentation = {}
    return PresentationDisplayMetadata(
        voice_state=_safe_text(presentation.get("voice_state")),
        visual_state=_safe_text(presentation.get("visual_state")),
        phase=_first_text(interaction, "phase"),
        role_label=role_label,
        subtitle=_safe_text(presentation.get("subtitle")),
        public_payload=_event_public_payload(interaction),
    )


def _event_public_payload(interaction: dict[str, object]) -> dict[str, object]:
    metadata = interaction.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    allowed_keys = {"correlation_id", "request_id", "trace_id"}
    return _redact_public_value(
        {
            key: value
            for key, value in metadata.items()
            if key in allowed_keys
        }
    )


def _skipped_event(
    *,
    event_id: str,
    interaction_id: str,
    session_id: str,
    character_id: str,
    character_name: str,
    role_label: str,
    completed_at: datetime | None,
    display_metadata: PresentationDisplayMetadata,
    voice_id: str,
    reason: str,
) -> PresentationEvent:
    display_event = {
        "event_type": "presentation_skipped",
        "event_id": event_id,
        "session_id": session_id,
        "reason": reason,
    }
    return PresentationEvent(
        event_id=event_id,
        interaction_id=interaction_id,
        session_id=session_id,
        character_id=character_id,
        character_name=character_name,
        role_label=role_label,
        response_text="",
        completed_at=completed_at,
        display_metadata=display_metadata,
        display_event=display_event,
        should_present=False,
        skip_reason=reason,
        voice_id=voice_id,
        public_payload={},
    )


def _state_for(
    delivery_id: str,
    delivery_state: MutableMapping[str, dict[str, object]] | None,
) -> dict[str, object]:
    if delivery_state is None:
        return {}
    return delivery_state.setdefault(delivery_id, {})


def _public_summary(ack: DeliveryAck) -> dict[str, object]:
    summary = dict(ack.public_summary)
    if not summary:
        summary = {
            "delivery_id": ack.delivery_id,
            "status": ack.status,
        }
    return _redact_public_value(summary)


def _timeout_summary(result: DeliveryTimeoutResult) -> dict[str, object]:
    summary = dict(result.public_summary)
    if not summary:
        summary = {
            "delivery_id": result.delivery_id,
            "status": result.status,
            "timeout_seconds": result.timeout_seconds,
        }
    return _redact_public_value(summary)


def _first_text(
    values: dict[str, object],
    *keys: str,
    default: object = "",
) -> str:
    for key in keys:
        value = values.get(key)
        if value is None:
            continue
        text = _safe_text(value).replace("\r", " ").replace("\n", " ").strip()
        if text:
            return text
    return _safe_text(default)


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    return _safe_text(value)


def _safe_text(value: object) -> str:
    if value is None:
        return ""
    text = str(value)
    lowered = text.lower()
    if any(pattern in lowered for pattern in _FORBIDDEN_TEXT_PATTERNS):
        return "[redacted]"
    return text


def _coerce_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _redact_public_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _redact_public_value(inner_value)
            for key, inner_value in value.items()
            if not _is_forbidden_public_key(key)
        }
    if isinstance(value, list):
        return [_redact_public_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_public_value(item) for item in value)
    if isinstance(value, str):
        return _safe_text(value)
    return value


def _is_forbidden_public_key(key: object) -> bool:
    lowered = str(key).lower()
    return lowered in _FORBIDDEN_KEYS or any(
        pattern in lowered for pattern in _FORBIDDEN_KEY_PATTERNS
    )


_FORBIDDEN_KEYS = {
    "access_token",
    "authorization",
    "headers",
    "hidden_prompt",
    "operator_only_metadata",
    "password",
    "raw_adapter_payload",
    "raw_memoriacore_payload",
    "raw_payload",
    "raw_prompt",
    "secret",
    "token",
    "youtube_raw",
}

_FORBIDDEN_KEY_PATTERNS = (
    "api-key",
    "api_key",
    "apikey",
    "authorization",
    "client_secret",
    "credential",
    "refresh_token",
    "secret",
    "token",
)

_FORBIDDEN_TEXT_PATTERNS = (
    "authorization:",
    "bearer ",
    "basic ",
    "x-api-key",
)


__all__ = [
    "DeliveryAck",
    "DeliveryTimeoutResult",
    "PresentationDisplayMetadata",
    "PresentationEvent",
    "TTSRequest",
    "build_presentation_event",
    "enqueue_tts_request",
    "record_delivery_ack",
    "record_delivery_timeout",
]
