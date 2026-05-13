"""Injectable MemoriaCore runners for YouTubeBridgeV2 runtime ticks.

本模組負責把 runtime tick 的 next action 接到 Memoria adapter contract。
它只透過注入的 transport 與 StorageManager-like methods 工作，不建立
HTTP client、不碰 SQLite、不引用 Legacy YouTubeBridge。
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Protocol

from YouTubeBridgeV2.adapters.memoria import (
    MemoriaAdapterError,
    MemoriaRequestPayload,
    NormalizedMemoriaResponse,
    build_memoria_request,
    classify_memoria_error,
    normalize_memoria_response,
)
from YouTubeBridgeV2.live_episode_plan.runner import (
    LiveEpisodePlanContract,
    LiveEpisodePlanState,
    PlanExecutionStatus,
    next_planned_turn,
    record_planned_turn_result,
)
from YouTubeBridgeV2.runtime.aftertalk import build_aftertalk_turn_request
from YouTubeBridgeV2.runtime.application_service import (
    AdapterDispatchResult,
    RuntimeCommand,
)
from YouTubeBridgeV2.runtime.closing import (
    ClosingPolicy,
    ClosingReason,
    ClosingStartContext,
    build_closing_request,
    finalize_closing,
)
from YouTubeBridgeV2.runtime.phase import (
    AftertalkPolicy,
    LiveSessionPhase,
    PhaseTransition,
    PhaseTransitionReason,
)


class MemoriaTransportProtocol(Protocol):
    """Synchronous transport boundary used by V2 Memoria runners."""

    def send(self, request: MemoriaRequestPayload) -> dict[str, object]:
        """Send one prepared Memoria request and return a raw response payload."""


class MemoriaPlannedShowRunner:
    """Execute the next LiveEpisodePlan planned turn through MemoriaCore."""

    def __init__(self, storage_manager: object, transport: MemoriaTransportProtocol) -> None:
        self._storage_manager = storage_manager
        self._transport = transport

    def run(
        self,
        *,
        command: RuntimeCommand,
        snapshot: object,
        transition: PhaseTransition,
        now: datetime,
    ) -> AdapterDispatchResult:
        session = _require_session(self._storage_manager, command.session_id)
        raw_state = _plan_state_record(session)
        if raw_state is None:
            return _error_result("missing_plan_state", retryable=False)

        plan_state = _plan_state_from_record(raw_state)
        turn_result = next_planned_turn(
            plan_state,
            _object_to_dict(command.payload).get("audience_event_summary"),
        )
        if turn_result.status is PlanExecutionStatus.INVALID:
            return AdapterDispatchResult(
                status="error",
                summary=_redact_public_value(
                    {
                        "error_type": "invalid_plan_state",
                        "retryable": False,
                        "validation_errors": list(turn_result.validation_errors),
                    }
                ),
                retryable=False,
            )

        if turn_result.intent is None:
            _update_session(
                self._storage_manager,
                command.session_id,
                {
                    "plan_completed": True,
                    "live_episode_plan_state": _state_record(
                        plan_state,
                        last_memoria_session_id=_optional_string(
                            raw_state.get("last_memoria_session_id")
                        ),
                    ),
                },
            )
            return AdapterDispatchResult(
                status="ok",
                summary={"plan_completed": True, "message_count": 0},
            )

        request = build_memoria_request(
            turn_result.intent,
            _memoria_context(command, session, raw_state),
        )
        normalized = _send_and_normalize(self._transport, request)
        if isinstance(normalized, MemoriaAdapterError):
            return _adapter_error_result(normalized)

        _append_interactions(
            self._storage_manager,
            session_id=command.session_id,
            phase=LiveSessionPhase.PLANNED_SHOW,
            command_id=command.command_id,
            normalized=normalized,
            now=now,
        )
        recorded = record_planned_turn_result(plan_state, turn_result)
        next_state_record = _state_record(
            recorded.next_state,
            last_memoria_session_id=normalized.memoria_session_id,
        )
        _update_session(
            self._storage_manager,
            command.session_id,
            {
                "plan_completed": recorded.completion_signal.completed,
                "live_episode_plan_state": next_state_record,
                "public_summary": _planned_public_summary(
                    session,
                    recorded.next_state,
                    status=recorded.status,
                ),
            },
        )
        return AdapterDispatchResult(
            status="ok",
            summary=_redact_public_value(
                {
                    **normalized.public_summary,
                    "turn_id": turn_result.intent.turn_id,
                    "message_count": len(normalized.messages),
                    "plan_completed": recorded.completion_signal.completed,
                }
            ),
        )


class MemoriaAftertalkRunner:
    """Execute one aftertalk group-chat turn through MemoriaCore."""

    def __init__(self, storage_manager: object, transport: MemoriaTransportProtocol) -> None:
        self._storage_manager = storage_manager
        self._transport = transport

    def run(
        self,
        *,
        command: RuntimeCommand,
        snapshot: object,
        transition: PhaseTransition,
        now: datetime,
    ) -> AdapterDispatchResult:
        session = _require_session(self._storage_manager, command.session_id)
        raw_state = _plan_state_record(session) or {}
        request_intent = build_aftertalk_turn_request(
            _aftertalk_context(command, session, raw_state, transition)
        )
        if not request_intent.should_dispatch:
            return AdapterDispatchResult(
                status="ok",
                summary={
                    "aftertalk_dispatched": False,
                    "stop_reason": _enum_value(request_intent.stop_reason),
                },
            )

        request = build_memoria_request(
            request_intent,
            _memoria_context(command, session, raw_state),
        )
        normalized = _send_and_normalize(self._transport, request)
        if isinstance(normalized, MemoriaAdapterError):
            return _adapter_error_result(normalized)

        _append_interactions(
            self._storage_manager,
            session_id=command.session_id,
            phase=LiveSessionPhase.AFTERTALK,
            command_id=command.command_id,
            normalized=normalized,
            now=now,
        )
        if raw_state:
            _update_session(
                self._storage_manager,
                command.session_id,
                {
                    "live_episode_plan_state": {
                        **raw_state,
                        "last_memoria_session_id": normalized.memoria_session_id,
                    }
                },
            )
        return AdapterDispatchResult(
            status="ok",
            summary=_redact_public_value(
                {
                    **normalized.public_summary,
                    "message_count": len(normalized.messages),
                }
            ),
        )


class MemoriaClosingRunner:
    """Execute closing final message/finalization through MemoriaCore."""

    def __init__(self, storage_manager: object, transport: MemoriaTransportProtocol) -> None:
        self._storage_manager = storage_manager
        self._transport = transport

    def run(
        self,
        *,
        command: RuntimeCommand,
        snapshot: object,
        transition: PhaseTransition,
        now: datetime,
    ) -> AdapterDispatchResult:
        session = _require_session(self._storage_manager, command.session_id)
        raw_state = _plan_state_record(session) or {}
        context = _closing_context(command, session, transition, now)
        policy = ClosingPolicy()
        closing_request = build_closing_request(
            context,
            _object_to_dict(session.get("public_summary", {})),
            _pending_super_chats(self._storage_manager, command),
            policy,
        )

        adapter_result: object | None = None
        normalized: NormalizedMemoriaResponse | None = None
        if closing_request.should_dispatch:
            request = build_memoria_request(
                closing_request,
                _memoria_context(command, session, raw_state),
            )
            sent = _send_and_normalize(self._transport, request)
            if isinstance(sent, MemoriaAdapterError):
                if sent.retryable:
                    return _adapter_error_result(sent)
                adapter_result = _adapter_error_summary(sent)
            else:
                normalized = sent
                adapter_result = normalized
                _append_interactions(
                    self._storage_manager,
                    session_id=command.session_id,
                    phase=LiveSessionPhase.CLOSING,
                    command_id=command.command_id,
                    normalized=normalized,
                    now=now,
                )

        finalization = finalize_closing(context, adapter_result, policy)
        _append_finalization(
            self._storage_manager,
            command.session_id,
            command.command_id,
            finalization,
            now,
        )
        return AdapterDispatchResult(
            status="ok",
            summary=_redact_public_value(
                {
                    "closing_completion_status": _enum_value(
                        finalization.closing_completion_status
                    ),
                    "status": finalization.status,
                    "message_count": len(normalized.messages) if normalized else 0,
                    **finalization.display_summary,
                }
            ),
        )


def _require_session(storage_manager: object, session_id: str) -> dict[str, object]:
    if not hasattr(storage_manager, "get_v2_session"):
        raise RuntimeError("storage manager missing get_v2_session")
    session = storage_manager.get_v2_session(session_id)
    if session is None:
        raise KeyError(session_id)
    return _object_to_dict(session)


def _update_session(
    storage_manager: object,
    session_id: str,
    patch: dict[str, object],
) -> None:
    if not hasattr(storage_manager, "update_v2_session"):
        raise RuntimeError("storage manager missing update_v2_session")
    storage_manager.update_v2_session(session_id, _redact_public_value(_json_safe_value(patch)))


def _append_interactions(
    storage_manager: object,
    *,
    session_id: str,
    phase: LiveSessionPhase,
    command_id: str,
    normalized: NormalizedMemoriaResponse,
    now: datetime,
) -> None:
    if not hasattr(storage_manager, "append_v2_interaction"):
        raise RuntimeError("storage manager missing append_v2_interaction")
    for index, message in enumerate(normalized.messages):
        message_id = str(message.get("message_id") or index + 1)
        storage_manager.append_v2_interaction(
            session_id,
            _redact_public_value(
                {
                    "interaction_id": f"{session_id}:{command_id}:{phase.value}:{message_id}",
                    "phase": phase.value,
                    "speaker_id": str(message.get("speaker_id", "")),
                    "public_content_summary": {
                        "message_id": message_id,
                        "speaker_id": str(message.get("speaker_id", "")),
                        "content": str(message.get("content", "")),
                        "mode": normalized.mode,
                        "memoria_session_id": normalized.memoria_session_id,
                    },
                    "correlation_id": normalized.correlation.correlation_id,
                    "created_at": now,
                }
            ),
        )


def _append_finalization(
    storage_manager: object,
    session_id: str,
    command_id: str,
    finalization: object,
    now: datetime,
) -> None:
    if not hasattr(storage_manager, "append_v2_finalization"):
        raise RuntimeError("storage manager missing append_v2_finalization")
    data = _object_to_dict(finalization)
    storage_manager.append_v2_finalization(
        session_id,
        _redact_public_value(
            {
                "finalization_id": f"{session_id}:{command_id}:finalization",
                "closing_completion_status": _enum_value(
                    data.get("closing_completion_status", "incomplete")
                ),
                "completed_at": data.get("completed_at") or now,
                "display_summary": data.get("display_summary", {}),
                "error_summary": data.get("error_summary", {}),
            }
        ),
    )


def _send_and_normalize(
    transport: MemoriaTransportProtocol,
    request: MemoriaRequestPayload,
) -> NormalizedMemoriaResponse | MemoriaAdapterError:
    try:
        response = transport.send(request)
    except Exception as exc:
        return classify_memoria_error(exc)
    if not isinstance(response, dict):
        return MemoriaAdapterError(
            error_type="invalid_response",
            retryable=False,
            public_summary={"error_type": "invalid_response", "retryable": False},
        )
    return normalize_memoria_response(response, request.correlation)


def _adapter_error_result(error: MemoriaAdapterError) -> AdapterDispatchResult:
    return AdapterDispatchResult(
        status="error",
        summary=_redact_public_value(error.public_summary),
        retryable=error.retryable,
    )


def _adapter_error_summary(error: MemoriaAdapterError) -> dict[str, object]:
    return _redact_public_value(
        {
            "error_type": error.error_type,
            "retryable": error.retryable,
        }
    )


def _error_result(error_type: str, *, retryable: bool) -> AdapterDispatchResult:
    return AdapterDispatchResult(
        status="error",
        summary={"error_type": error_type, "retryable": retryable},
        retryable=retryable,
    )


def _memoria_context(
    command: RuntimeCommand,
    session: dict[str, object],
    state_record: dict[str, object],
) -> dict[str, object]:
    return _redact_public_value(
        {
            "correlation_id": f"runtime-{command.command_id}",
            "request_id": command.command_id,
            "v2_session_id": command.session_id,
            "memoria_session_id": _optional_string(state_record.get("last_memoria_session_id")),
            "channel_uid": command.session_id,
            "group_name": "youtube_live",
            "public_metadata": session.get("public_summary", {}),
        }
    )


def _aftertalk_context(
    command: RuntimeCommand,
    session: dict[str, object],
    state_record: dict[str, object],
    transition: PhaseTransition,
) -> dict[str, object]:
    duration = _duration_summary(transition)
    return {
        "session_id": command.session_id,
        "aftertalk_policy": _aftertalk_policy(session.get("aftertalk_policy")),
        "duration_reached": bool(duration.get("duration_reached", False)),
        "remaining_time_seconds": duration.get("remaining_time_seconds"),
        "manual_close_requested": bool(session.get("manual_close_requested", False)),
        "public_show_summary": _object_to_dict(session.get("public_summary", {})),
        "speaker_rotation_hint": _speaker_ids_from_state_record(state_record),
        "correlation_id": f"runtime-{command.command_id}",
    }


def _closing_context(
    command: RuntimeCommand,
    session: dict[str, object],
    transition: PhaseTransition,
    now: datetime,
) -> ClosingStartContext:
    duration = _duration_summary(transition)
    return ClosingStartContext(
        session_id=command.session_id,
        closing_reason=_closing_reason(session, transition),
        phase_entered_at=now,
        duration_summary=duration,
        manual_close_requested=bool(session.get("manual_close_requested", False)),
        correlation_id=f"runtime-{command.command_id}",
        completed_at=now,
        finalization_completed=bool(session.get("closing_completed", False)),
    )


def _closing_reason(
    session: dict[str, object],
    transition: PhaseTransition,
) -> ClosingReason:
    reason = transition.reason
    if reason is PhaseTransitionReason.MANUAL_CLOSE or bool(
        session.get("manual_close_requested", False)
    ):
        return ClosingReason.MANUAL_CLOSE
    if reason is PhaseTransitionReason.DURATION_REACHED:
        return ClosingReason.DURATION_REACHED
    if reason is PhaseTransitionReason.INVALID_STATE_RECOVERY:
        return ClosingReason.UNRECOVERABLE_ERROR
    return ClosingReason.PLAN_COMPLETED


def _duration_summary(transition: PhaseTransition) -> dict[str, object]:
    metadata = _object_to_dict(transition.metadata)
    duration = metadata.get("duration_summary")
    if isinstance(duration, dict):
        return dict(duration)
    return {
        "duration_reached": bool(metadata.get("duration_reached", False)),
        "remaining_time_seconds": metadata.get("remaining_time_seconds"),
    }


def _plan_state_record(session: dict[str, object]) -> dict[str, object] | None:
    metadata = session.get("metadata")
    if isinstance(metadata, dict) and isinstance(metadata.get("live_episode_plan_state"), dict):
        return dict(metadata["live_episode_plan_state"])
    state = session.get("live_episode_plan_state")
    if isinstance(state, dict):
        return dict(state)
    return None


def _plan_state_from_record(record: dict[str, object]) -> LiveEpisodePlanState:
    contract_data = _object_to_dict(record.get("contract", {}))
    return LiveEpisodePlanState(
        contract=LiveEpisodePlanContract(
            plan_id=str(contract_data.get("plan_id", "")),
            title=str(contract_data.get("title", "")),
            turns=tuple(_object_to_dict(turn) for turn in _list_value(contract_data.get("turns"))),
            status=_plan_status(contract_data.get("status")),
            validation_errors=tuple(str(item) for item in _list_value(contract_data.get("validation_errors"))),
            public_summary=_object_to_dict(contract_data.get("public_summary", {})),
        ),
        cursor=int(record.get("cursor", 0)),
        completed_turn_ids=tuple(
            str(item) for item in _list_value(record.get("completed_turn_ids"))
        ),
    )


def _state_record(
    state: LiveEpisodePlanState,
    *,
    last_memoria_session_id: str | None,
) -> dict[str, object]:
    record = _json_safe_value(state)
    record["last_memoria_session_id"] = last_memoria_session_id
    return _redact_public_value(record)


def _planned_public_summary(
    session: dict[str, object],
    state: LiveEpisodePlanState,
    *,
    status: PlanExecutionStatus,
) -> dict[str, object]:
    summary = _object_to_dict(session.get("public_summary", {}))
    summary.update(
        {
            "plan_id": state.contract.plan_id,
            "plan_title": state.contract.title,
            "title": state.contract.title,
            "turn_count": len(state.contract.turns),
            "completed_turn_count": len(state.completed_turn_ids),
            "status": status.value,
        }
    )
    return _redact_public_value(summary)


def _speaker_ids_from_state_record(record: dict[str, object]) -> tuple[str, ...]:
    try:
        state = _plan_state_from_record(record)
    except Exception:
        return ("host",)
    speakers: list[str] = []
    for turn in state.contract.turns:
        for speaker_id in _list_value(turn.get("speaker_ids")):
            text = str(speaker_id).strip()
            if text and text not in speakers:
                speakers.append(text)
    return tuple(speakers) or ("host",)


def _aftertalk_policy(value: object) -> AftertalkPolicy:
    if isinstance(value, AftertalkPolicy):
        return value
    try:
        return AftertalkPolicy(str(value))
    except ValueError:
        return AftertalkPolicy.DISABLED


def _plan_status(value: object) -> PlanExecutionStatus:
    if isinstance(value, PlanExecutionStatus):
        return value
    try:
        return PlanExecutionStatus(str(value))
    except ValueError:
        return PlanExecutionStatus.INVALID


def _object_to_dict(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return dict(value)
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "__dict__"):
        return dict(vars(value))
    return {}


def _list_value(value: object) -> list[object]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _list_of_dicts(value: object) -> list[dict[str, object]]:
    return [item for item in _list_value(value) if isinstance(item, dict)]


def _pending_super_chats(
    storage_manager: object,
    command: RuntimeCommand,
) -> list[dict[str, object]]:
    payload = _object_to_dict(command.payload)
    pending = _list_of_dicts(payload.get("pending_super_chats"))
    if hasattr(storage_manager, "list_v2_live_events"):
        for event in storage_manager.list_v2_live_events(command.session_id, 500):
            pending_item = _super_chat_from_event(_object_to_dict(event))
            if pending_item is not None:
                pending.append(pending_item)
    return _redact_public_value(pending)


def _super_chat_from_event(event: dict[str, object]) -> dict[str, object] | None:
    if str(event.get("event_type", "")) != "youtube_super_chat":
        return None
    metadata = _object_to_dict(event.get("public_metadata", {}))
    public_payload = _object_to_dict(metadata.get("public_payload", {}))
    super_chat = _object_to_dict(public_payload.get("super_chat", {}))
    if not super_chat:
        return None
    if str(super_chat.get("acknowledgement_status", "pending")) != "pending":
        return None
    display_event = _object_to_dict(metadata.get("display_event", {}))
    return _redact_public_value(
        {
            "super_chat_id": super_chat.get("super_chat_id") or event.get("event_id"),
            "author_display_name": public_payload.get(
                "author_display_name",
                display_event.get("author_display_name", ""),
            ),
            "amount_display_string": super_chat.get("amount_display_string", ""),
            "message_text": super_chat.get(
                "public_message",
                public_payload.get("message_text", display_event.get("message_text", "")),
            ),
        }
    )


def _json_safe_value(value: Any) -> Any:
    if is_dataclass(value):
        return _json_safe_value(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe_value(inner) for key, inner in value.items()}
    if isinstance(value, list):
        return [_json_safe_value(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe_value(item) for item in value]
    return value


def _enum_value(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    return value


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


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


def _redact_public_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _redact_public_value(inner)
            for key, inner in value.items()
            if str(key).lower() not in _PUBLIC_FORBIDDEN_KEYS
        }
    if isinstance(value, list):
        return [_redact_public_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_public_value(item) for item in value)
    return value


__all__ = [
    "MemoriaAftertalkRunner",
    "MemoriaClosingRunner",
    "MemoriaPlannedShowRunner",
    "MemoriaTransportProtocol",
]
