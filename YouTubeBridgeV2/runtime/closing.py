"""Pure closing contracts for YouTubeBridgeV2.

Closing 只建立 final message intent、Super Chat acknowledgement action、
finalization result 與 display-safe event。MemoriaCore HTTP、YouTube stop、
storage write、UI rendering 與 TTS delivery 都由呼叫端或其他 adapter 負責。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime
from enum import Enum
from typing import Any


class ClosingReason(str, Enum):
    """進入 closing phase 的原因."""

    DURATION_REACHED = "duration_reached"
    MANUAL_CLOSE = "manual_close"
    PLAN_COMPLETED = "plan_completed"
    STREAM_ENDED = "stream_ended"
    UNRECOVERABLE_ERROR = "unrecoverable_error"


class ClosingCompletionStatus(str, Enum):
    """Runtime Phase 可消費的 closing completion status."""

    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_TERMINAL = "failed_terminal"


@dataclass(frozen=True)
class ClosingStartContext:
    """closing 開始時的可測 input summary."""

    session_id: str
    closing_reason: ClosingReason | str
    phase_entered_at: datetime
    duration_summary: dict[str, object]
    manual_close_requested: bool
    correlation_id: str = ""
    completed_at: datetime | None = None
    acknowledged_super_chat_ids: tuple[str, ...] = ()
    final_message_sent: bool = False
    finalization_completed: bool = False


@dataclass(frozen=True)
class ClosingPolicy:
    """closing request/finalization policy."""

    final_message_enabled: bool = True
    acknowledge_super_chats: bool = True
    terminal_error_allows_system_summary: bool = True
    visibility: str = "public"


@dataclass(frozen=True)
class ClosingSuperChatAction:
    """closing phase 中一筆 Super Chat acknowledgement action."""

    super_chat_id: str
    action_type: str
    status: str
    author_display_name: str
    amount_display_string: str
    public_message: str
    error_summary: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ClosingRequest:
    """交給 Runtime Application Service / MemoriaCore Adapter 的 closing intent."""

    session_id: str
    closing_reason: ClosingReason
    summary: dict[str, object]
    super_chat_actions: tuple[ClosingSuperChatAction, ...]
    visibility: str
    should_dispatch: bool
    adapter_intent: str | None
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ClosingDisplayEvent:
    """display-safe closing status event."""

    event_type: str
    session_id: str
    status: str
    message: str
    public_summary: dict[str, object] = field(default_factory=dict)
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ClosingFinalizationResult:
    """closing finalization result 與 Runtime Phase completion signal."""

    session_id: str
    status: str
    completed_at: datetime | None
    closing_completion_status: ClosingCompletionStatus
    display_summary: dict[str, object]
    error_summary: dict[str, object]
    display_event: ClosingDisplayEvent


def build_closing_request(
    context: ClosingStartContext,
    summary: dict[str, object],
    pending_super_chats: list[dict[str, object]],
    policy: ClosingPolicy,
) -> ClosingRequest:
    """建立 display-safe closing request，不執行 adapter/storage/UI side effects."""

    reason = _coerce_reason(context.closing_reason)
    metadata = _request_metadata(context, reason)
    should_dispatch = bool(policy.final_message_enabled and not context.final_message_sent)
    if context.final_message_sent:
        metadata["idempotent_skip"] = "final_message_already_sent"

    return ClosingRequest(
        session_id=context.session_id,
        closing_reason=reason,
        summary=_redact_public_value(summary),
        super_chat_actions=_super_chat_actions(context, pending_super_chats, policy),
        visibility=str(policy.visibility or "public"),
        should_dispatch=should_dispatch,
        adapter_intent="memoriacore_closing_message" if should_dispatch else None,
        metadata=metadata,
    )


def finalize_closing(
    context: ClosingStartContext,
    adapter_result: object | None,
    policy: ClosingPolicy,
) -> ClosingFinalizationResult:
    """將 adapter/system result 轉成 closing finalization status，不推進 phase."""

    if context.finalization_completed:
        return _finalization_result(
            context,
            status="complete",
            completion_status=ClosingCompletionStatus.COMPLETE,
            display_summary={"finalization": "already_completed"},
            error_summary={},
        )

    if not policy.final_message_enabled:
        return _finalization_result(
            context,
            status="complete",
            completion_status=ClosingCompletionStatus.COMPLETE,
            display_summary={"finalization": "system_only"},
            error_summary={},
        )

    result = _object_to_dict(adapter_result)
    error_type = _optional_string(result.get("error_type"))
    if error_type:
        retryable = bool(result.get("retryable", False))
        error_summary = _redact_public_value(
            {
                "error_type": error_type,
                "retryable": retryable,
            }
        )
        if retryable:
            return _finalization_result(
                context,
                status="failed_retryable",
                completion_status=ClosingCompletionStatus.FAILED_RETRYABLE,
                display_summary={"finalization": "adapter_retryable_error"},
                error_summary=error_summary,
            )

        if policy.terminal_error_allows_system_summary:
            error_summary["fallback"] = "system_summary"
            return _finalization_result(
                context,
                status="complete",
                completion_status=ClosingCompletionStatus.COMPLETE,
                display_summary={"finalization": "system_summary"},
                error_summary=error_summary,
            )

        return _finalization_result(
            context,
            status="failed_terminal",
            completion_status=ClosingCompletionStatus.FAILED_TERMINAL,
            display_summary={"finalization": "adapter_terminal_error"},
            error_summary=error_summary,
        )

    if adapter_result is None:
        return _finalization_result(
            context,
            status="incomplete",
            completion_status=ClosingCompletionStatus.INCOMPLETE,
            display_summary={"finalization": "waiting_for_adapter"},
            error_summary={},
        )

    return _finalization_result(
        context,
        status="complete",
        completion_status=ClosingCompletionStatus.COMPLETE,
        display_summary=_success_display_summary(result),
        error_summary={},
    )


def _super_chat_actions(
    context: ClosingStartContext,
    pending_super_chats: list[dict[str, object]],
    policy: ClosingPolicy,
) -> tuple[ClosingSuperChatAction, ...]:
    if not policy.acknowledge_super_chats:
        return ()

    seen_ids = {str(item) for item in context.acknowledged_super_chat_ids}
    actions: list[ClosingSuperChatAction] = []
    for raw_item in pending_super_chats:
        action = _super_chat_action(raw_item, seen_ids)
        if action is not None:
            actions.append(action)
            if action.super_chat_id:
                seen_ids.add(action.super_chat_id)
    return tuple(actions)


def _super_chat_action(
    raw_item: dict[str, object],
    acknowledged_ids: set[str],
) -> ClosingSuperChatAction | None:
    super_chat_id = _first_text(raw_item, "id", "event_id", "super_chat_id")
    author = _first_text(raw_item, "author_display_name", "author", "author_name")

    if super_chat_id and super_chat_id in acknowledged_ids:
        return None

    if not super_chat_id or not author:
        return ClosingSuperChatAction(
            super_chat_id=super_chat_id,
            action_type="skipped",
            status="invalid",
            author_display_name=author,
            amount_display_string="",
            public_message="",
            error_summary={
                "error_type": "invalid_super_chat",
                "reason": "missing_id_or_author",
            },
        )

    return ClosingSuperChatAction(
        super_chat_id=super_chat_id,
        action_type="acknowledge",
        status="pending",
        author_display_name=author,
        amount_display_string=_first_text(raw_item, "amount_display_string", "amount"),
        public_message=_first_text(raw_item, "message_text", "text", "message"),
        error_summary={},
    )


def _request_metadata(
    context: ClosingStartContext,
    reason: ClosingReason,
) -> dict[str, object]:
    return _redact_public_value(
        {
            "closing_reason": reason.value,
            "duration_summary": context.duration_summary,
            "manual_close_requested": context.manual_close_requested,
            "correlation_id": context.correlation_id,
        }
    )


def _finalization_result(
    context: ClosingStartContext,
    *,
    status: str,
    completion_status: ClosingCompletionStatus,
    display_summary: dict[str, object],
    error_summary: dict[str, object],
) -> ClosingFinalizationResult:
    safe_summary = _redact_public_value(display_summary)
    safe_error = _redact_public_value(error_summary)
    display_event = ClosingDisplayEvent(
        event_type="closing_status",
        session_id=context.session_id,
        status=status,
        message=_display_message(completion_status),
        public_summary=safe_summary,
        metadata={
            "closing_reason": _coerce_reason(context.closing_reason).value,
            "correlation_id": context.correlation_id,
        },
    )
    return ClosingFinalizationResult(
        session_id=context.session_id,
        status=status,
        completed_at=context.completed_at,
        closing_completion_status=completion_status,
        display_summary=safe_summary,
        error_summary=safe_error,
        display_event=display_event,
    )


def _success_display_summary(result: dict[str, object]) -> dict[str, object]:
    public_summary = result.get("public_summary")
    if isinstance(public_summary, dict):
        return _redact_public_value(public_summary)
    return _redact_public_value(
        {
            "message_count": result.get("message_count", 0),
            "status": result.get("status", "ok"),
        }
    )


def _display_message(completion_status: ClosingCompletionStatus) -> str:
    if completion_status is ClosingCompletionStatus.COMPLETE:
        return "closing complete"
    if completion_status is ClosingCompletionStatus.FAILED_RETRYABLE:
        return "closing retryable failure"
    if completion_status is ClosingCompletionStatus.FAILED_TERMINAL:
        return "closing terminal failure"
    return "closing incomplete"


def _coerce_reason(value: ClosingReason | str) -> ClosingReason:
    if isinstance(value, ClosingReason):
        return value
    return ClosingReason(str(value))


def _object_to_dict(value: object | None) -> dict[str, object]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if is_dataclass(value):
        return asdict(value)
    raw = {
        key: getattr(value, key)
        for key in ("error_type", "retryable", "public_summary", "status", "message_count")
        if hasattr(value, key)
    }
    return raw


def _first_text(raw_item: dict[str, object], *keys: str) -> str:
    for key in keys:
        value = raw_item.get(key)
        if value is not None:
            text = str(value).replace("\r", " ").replace("\n", " ").strip()
            if text:
                return text
    return ""


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _redact_public_value(value: Any) -> Any:
    forbidden_keys = {
        "hidden_prompt",
        "raw_prompt",
        "topic_pack",
        "raw_topic_pack",
        "factcard",
        "fact_card",
        "raw_factcard",
        "raw_fact_card",
        "raw_payload",
        "raw_memoriacore_payload",
        "raw_super_chat",
        "raw_super_chat_payload",
        "memoriacore_raw",
        "youtube_raw",
        "topic_pack_fact_cards",
        "raw_fact_cards",
        "program_segment_plan",
        "legacy_director",
        "access_token",
        "authorization",
        "secret",
        "token",
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
    "ClosingCompletionStatus",
    "ClosingDisplayEvent",
    "ClosingFinalizationResult",
    "ClosingPolicy",
    "ClosingReason",
    "ClosingRequest",
    "ClosingStartContext",
    "ClosingSuperChatAction",
    "build_closing_request",
    "finalize_closing",
]
