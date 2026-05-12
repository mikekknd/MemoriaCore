"""Pure Aftertalk intent builder for YouTubeBridgeV2.

Aftertalk 只產生 MemoriaCore group chat intent。transport、storage、
LLM output、Legacy director 與 UI rendering 都不在本模組處理。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .phase import AftertalkPolicy


class AftertalkStopReason(str, Enum):
    """Aftertalk 無法繼續或停止的 deterministic reason."""

    DURATION_REACHED = "duration_reached"
    MANUAL_CLOSE = "manual_close"
    DISABLED = "disabled"
    INVALID_POLICY = "invalid_policy"
    ADAPTER_ERROR = "adapter_error"
    COMPLETED_BY_POLICY = "completed_by_policy"


@dataclass(frozen=True)
class AftertalkCue:
    """交給 MemoriaCore Adapter 的 display-safe aftertalk cue."""

    session_id: str
    public_show_summary: dict[str, object]
    speaker_rotation_hint: tuple[str, ...]
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class AftertalkTurnRequest:
    """Aftertalk group chat request intent 或 stop decision."""

    session_id: str
    should_dispatch: bool
    group_chat_mode: str | None
    adapter_intent: str | None
    cue: AftertalkCue | None
    stop_reason: AftertalkStopReason | None
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class AftertalkSessionSummary:
    """供 storage/UI/observability 使用的 redacted aftertalk summary."""

    session_id: str
    status: str
    stop_reason: AftertalkStopReason | None
    public_summary: dict[str, object] = field(default_factory=dict)
    metadata: dict[str, object] = field(default_factory=dict)


def build_aftertalk_turn_request(aftertalk_context: dict[str, object]) -> AftertalkTurnRequest:
    """依 aftertalk context 建立 group chat request intent 或 stop decision."""

    session_id = str(aftertalk_context.get("session_id", ""))
    stop_reason = _stop_reason(aftertalk_context)
    metadata = {
        "correlation_id": str(aftertalk_context.get("correlation_id", "")),
        "legacy_director_used": False,
    }

    if stop_reason is not None:
        return AftertalkTurnRequest(
            session_id=session_id,
            should_dispatch=False,
            group_chat_mode=None,
            adapter_intent=None,
            cue=None,
            stop_reason=stop_reason,
            metadata=metadata,
        )

    cue = AftertalkCue(
        session_id=session_id,
        public_show_summary=_public_show_summary(aftertalk_context),
        speaker_rotation_hint=_speaker_rotation_hint(aftertalk_context),
        metadata={
            "remaining_time_seconds": aftertalk_context.get("remaining_time_seconds"),
            "correlation_id": metadata["correlation_id"],
        },
    )
    return AftertalkTurnRequest(
        session_id=session_id,
        should_dispatch=True,
        group_chat_mode="aftertalk",
        adapter_intent="memoriacore_group_chat",
        cue=cue,
        stop_reason=None,
        metadata=metadata,
    )


def summarize_aftertalk_result(aftertalk_result: dict[str, object]) -> AftertalkSessionSummary:
    """將 adapter/module result 摘要成 public-safe aftertalk summary."""

    return AftertalkSessionSummary(
        session_id=str(aftertalk_result.get("session_id", "")),
        status=str(aftertalk_result.get("status", "")),
        stop_reason=_coerce_stop_reason(aftertalk_result.get("stop_reason")),
        public_summary=_public_result_summary(aftertalk_result),
        metadata={
            "legacy_director_used": False,
        },
    )


def _stop_reason(aftertalk_context: dict[str, object]) -> AftertalkStopReason | None:
    if bool(aftertalk_context.get("manual_close_requested", False)):
        return AftertalkStopReason.MANUAL_CLOSE

    policy = _coerce_policy(aftertalk_context.get("aftertalk_policy"))
    if policy is None:
        return AftertalkStopReason.INVALID_POLICY

    if policy is AftertalkPolicy.DISABLED:
        return AftertalkStopReason.DISABLED

    if bool(aftertalk_context.get("duration_reached", False)):
        return AftertalkStopReason.DURATION_REACHED

    return None


def _coerce_policy(value: object) -> AftertalkPolicy | None:
    if isinstance(value, AftertalkPolicy):
        return value
    try:
        return AftertalkPolicy(str(value))
    except ValueError:
        return None


def _coerce_stop_reason(value: object) -> AftertalkStopReason | None:
    if value is None:
        return None
    if isinstance(value, AftertalkStopReason):
        return value
    try:
        return AftertalkStopReason(str(value))
    except ValueError:
        return None


def _public_show_summary(aftertalk_context: dict[str, object]) -> dict[str, object]:
    raw_summary = aftertalk_context.get("public_show_summary", {})
    if not isinstance(raw_summary, dict):
        return {}
    allowed_keys = {
        "title",
        "completed_turn_count",
        "public_topics",
        "public_recap",
        "episode_id",
    }
    return {
        key: _redact_public_value(value)
        for key, value in raw_summary.items()
        if key in allowed_keys
    }


def _speaker_rotation_hint(aftertalk_context: dict[str, object]) -> tuple[str, ...]:
    value = aftertalk_context.get("speaker_rotation_hint", ())
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    return ()


def _public_result_summary(aftertalk_result: dict[str, object]) -> dict[str, object]:
    allowed_keys = {
        "message_count",
        "last_speaker_id",
        "public_recap",
        "retryable",
    }
    return {
        key: _redact_public_value(value)
        for key, value in aftertalk_result.items()
        if key in allowed_keys
    }


def _redact_public_value(value: Any) -> Any:
    forbidden_keys = {
        "hidden_prompt",
        "raw_topic_pack",
        "raw_factcard",
        "raw_fact_card",
        "raw_payload",
        "raw_memoriacore_payload",
        "memoriacore_raw",
        "youtube_raw",
        "topic_pack_fact_cards",
        "raw_fact_cards",
        "program_segment_plan",
        "legacy_director",
    }

    if isinstance(value, dict):
        return {
            key: _redact_public_value(inner_value)
            for key, inner_value in value.items()
            if str(key).lower() not in forbidden_keys
        }
    if isinstance(value, list):
        return [_redact_public_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_public_value(item) for item in value)
    return value


__all__ = [
    "AftertalkCue",
    "AftertalkSessionSummary",
    "AftertalkStopReason",
    "AftertalkTurnRequest",
    "build_aftertalk_turn_request",
    "summarize_aftertalk_result",
]
