"""YouTubeBridgeV2 runtime application service contracts.

本模組是 runtime workflow 的 side-effect boundary。它負責讀取 snapshot、
呼叫純 Runtime Phase decision、依 next action dispatch 注入的 runtime
dependency，並回傳已 redacted 的 service result。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Mapping

from YouTubeBridgeV2.adapters.youtube import (
    NormalizedYouTubeEvent,
    YouTubePollingCursor,
    normalize_youtube_event,
)
from .phase import (
    AftertalkPolicy,
    LiveSessionPhase,
    LiveSessionSnapshot,
    PhaseTransition,
    PhaseTransitionReason,
    advance_phase,
)


class RuntimeCommandType(str, Enum):
    """Runtime Application Service 接受的 command 類型."""

    CREATE_SESSION = "create_session"
    BIND_PLAN = "bind_plan"
    START_SESSION = "start_session"
    TICK = "tick"
    HANDLE_YOUTUBE_EVENT = "handle_youtube_event"
    UPDATE_AFTERTALK_POLICY = "update_aftertalk_policy"
    MANUAL_CLOSE = "manual_close"
    FINALIZE_CLOSING = "finalize_closing"
    RECOVER = "recover"


@dataclass(frozen=True)
class RuntimeCommand:
    """所有 runtime action 共用的 typed command envelope."""

    command_id: str
    session_id: str
    command_type: RuntimeCommandType | str
    issued_at: datetime
    permission_context: object | None = None
    payload: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class RuntimeServiceEvent:
    """可發送到 operator/display/observer 的 public service event."""

    event_type: str
    session_id: str
    phase: LiveSessionPhase | str | None
    payload: dict[str, object]
    correlation_id: str


@dataclass(frozen=True)
class PersistedTransitionRef:
    """已保存 phase transition 的 reference."""

    transition_id: str
    session_id: str
    previous_phase: LiveSessionPhase | str
    next_phase: LiveSessionPhase
    reason: PhaseTransitionReason


@dataclass(frozen=True)
class AdapterDispatchResult:
    """Adapter 或 runtime module dispatch 的 normalized result."""

    status: str
    summary: dict[str, object] = field(default_factory=dict)
    retryable: bool = False


@dataclass(frozen=True)
class RecoveryDecision:
    """Crash/restart recovery 的 deterministic decision."""

    action: str
    reason: str
    session_id: str


@dataclass(frozen=True)
class RuntimeServiceResult:
    """Runtime command 的 stable result shape."""

    status: str
    session_id: str
    phase: LiveSessionPhase | str | None
    events: list[RuntimeServiceEvent]
    errors: list[dict[str, object]]
    correlation_id: str
    transition_ref: PersistedTransitionRef | None = None
    adapter_result: AdapterDispatchResult | None = None
    recovery_decision: RecoveryDecision | None = None


class _NoopRunner:
    def run(
        self,
        *,
        command: RuntimeCommand,
        snapshot: LiveSessionSnapshot,
        transition: PhaseTransition,
        now: datetime,
    ) -> AdapterDispatchResult:
        return AdapterDispatchResult(status="ok", summary={"message": "no-op"})


class RuntimeApplicationService:
    """協調 V2 runtime command、phase decision 與注入式 side effects."""

    def __init__(
        self,
        *,
        storage: object,
        phase_advancer: Callable[[LiveSessionSnapshot, datetime], PhaseTransition] = advance_phase,
        planned_show_runner: object | None = None,
        aftertalk: object | None = None,
        closing: object | None = None,
    ) -> None:
        self._storage = storage
        self._phase_advancer = phase_advancer
        self._planned_show_runner = planned_show_runner or _NoopRunner()
        self._aftertalk = aftertalk or _NoopRunner()
        self._closing = closing or _NoopRunner()

    def create_session(
        self,
        command: RuntimeCommand,
        now: datetime,
    ) -> RuntimeServiceResult:
        """建立 session 並保存 command result."""

        existing = self._existing_result(command)
        if existing is not None:
            return existing

        snapshot = self._storage.create_session(command, now)
        event = self._event(
            event_type="session_created",
            command=command,
            phase=snapshot.current_phase,
            payload={"phase": _phase_value(snapshot.current_phase)},
        )
        self._persist_event(event)
        result = RuntimeServiceResult(
            status="ok",
            session_id=command.session_id,
            phase=snapshot.current_phase,
            events=[event],
            errors=[],
            correlation_id=_correlation_id(command),
        )
        self._save_result(command, result)
        return result

    def bind_plan(self, command: RuntimeCommand, now: datetime) -> RuntimeServiceResult:
        return self._storage_delegate(command, now, "bind_plan", "plan_bound")

    def start_session(self, command: RuntimeCommand, now: datetime) -> RuntimeServiceResult:
        return self._storage_delegate(command, now, "start_session", "session_started")

    def tick_session(
        self,
        command: RuntimeCommand,
        now: datetime,
    ) -> RuntimeServiceResult:
        """讀取最新 snapshot，推進 phase decision 並 dispatch next action."""

        existing = self._existing_result(command)
        if existing is not None:
            return existing

        snapshot = self._storage.read_snapshot(command.session_id)
        return self._advance_and_dispatch(command, now, snapshot)

    def handle_youtube_event(
        self,
        command: RuntimeCommand,
        now: datetime,
    ) -> RuntimeServiceResult:
        existing = self._existing_result(command)
        if existing is not None:
            return existing

        cursor = _youtube_cursor_for_command(command, self._storage)
        try:
            youtube_payload, advanced_cursor = _youtube_runtime_event_payload(
                command.payload,
                cursor=cursor,
            )
        except ValueError as exc:
            result = _youtube_contract_error(command, str(exc))
            self._save_result(command, result)
            return result

        if hasattr(self._storage, "persist_youtube_event"):
            self._storage.persist_youtube_event(command.session_id, youtube_payload, now)
        if advanced_cursor is not None and hasattr(self._storage, "save_youtube_polling_cursor"):
            self._storage.save_youtube_polling_cursor(command.session_id, advanced_cursor, now)
        if youtube_payload.get("should_dispatch") is False:
            snapshot = self._storage.read_snapshot(command.session_id)
            return self._youtube_duplicate_result(command, snapshot, youtube_payload)
        snapshot = self._storage.read_snapshot(command.session_id)
        return self._advance_and_dispatch(command, now, snapshot)

    def _youtube_duplicate_result(
        self,
        command: RuntimeCommand,
        snapshot: LiveSessionSnapshot,
        youtube_payload: dict[str, object],
    ) -> RuntimeServiceResult:
        summary = {
            "youtube_event": "duplicate",
            "event_id": str(youtube_payload.get("event_id", "")),
        }
        event = self._event(
            event_type="youtube_event_ignored",
            command=command,
            phase=snapshot.current_phase,
            payload=summary,
        )
        self._persist_event(event)
        result = RuntimeServiceResult(
            status="ok",
            session_id=command.session_id,
            phase=snapshot.current_phase,
            events=[event],
            errors=[],
            correlation_id=_correlation_id(command),
            adapter_result=AdapterDispatchResult(status="ok", summary=summary),
        )
        self._save_result(command, result)
        return result

    def update_aftertalk_policy(
        self,
        command: RuntimeCommand,
        now: datetime,
    ) -> RuntimeServiceResult:
        return self._storage_delegate(
            command,
            now,
            "update_aftertalk_policy",
            "aftertalk_policy_updated",
        )

    def request_manual_close(
        self,
        command: RuntimeCommand,
        now: datetime,
    ) -> RuntimeServiceResult:
        """要求 manual close，並讓 Runtime Phase 決定後續 closing action."""

        existing = self._existing_result(command)
        if existing is not None:
            return existing

        snapshot = self._storage.request_manual_close(
            command.session_id,
            command.command_id,
            now,
        )
        return self._advance_and_dispatch(command, now, snapshot)

    def finalize_closing(
        self,
        command: RuntimeCommand,
        now: datetime,
    ) -> RuntimeServiceResult:
        return self._storage_delegate(command, now, "finalize_closing", "closing_finalized")

    def recover_session(
        self,
        command: RuntimeCommand,
        now: datetime,
    ) -> RuntimeServiceResult:
        """從 storage snapshot 恢復 runtime action，不依賴 process-local state."""

        existing = self._existing_result(command)
        if existing is not None:
            return existing

        snapshot = self._storage.read_snapshot(command.session_id)
        recovery_decision = _recovery_decision(command.session_id, snapshot)
        result = self._advance_and_dispatch(
            command,
            now,
            snapshot,
            recovery_decision=recovery_decision,
        )
        return result

    def _advance_and_dispatch(
        self,
        command: RuntimeCommand,
        now: datetime,
        snapshot: LiveSessionSnapshot,
        *,
        recovery_decision: RecoveryDecision | None = None,
    ) -> RuntimeServiceResult:
        contract_error = self._validate_snapshot(command, snapshot)
        if contract_error is not None:
            return contract_error

        transition = self._phase_advancer(snapshot, now)

        try:
            transition_ref = self._persist_transition(command, transition, now)
        except Exception as exc:
            result = RuntimeServiceResult(
                status="error",
                session_id=command.session_id,
                phase=transition.next_phase,
                events=[],
                errors=[
                    {
                        "code": "storage_write_failed",
                        "message": str(exc),
                    }
                ],
                correlation_id=_correlation_id(command),
                recovery_decision=recovery_decision,
            )
            self._save_result(command, result)
            return result

        adapter_result = self._dispatch_next_action(command, snapshot, transition, now)
        status = _result_status(adapter_result)
        errors = _errors_for_adapter_result(adapter_result)

        if adapter_result.status == "error":
            safe_summary = _sanitize_public_payload(adapter_result.summary)
            self._storage.persist_error_summary(
                command.session_id,
                command.command_id,
                safe_summary,
                adapter_result.retryable,
            )

        event = self._event(
            event_type=_event_type(adapter_result),
            command=command,
            phase=transition.next_phase,
            payload={
                "next_action": transition.next_action,
                "adapter_summary": _sanitize_public_payload(adapter_result.summary),
            },
        )
        self._persist_event(event)
        result = RuntimeServiceResult(
            status=status,
            session_id=command.session_id,
            phase=transition.next_phase,
            events=[event],
            errors=errors,
            correlation_id=_correlation_id(command),
            transition_ref=transition_ref,
            adapter_result=AdapterDispatchResult(
                status=adapter_result.status,
                summary=_sanitize_public_payload(adapter_result.summary),
                retryable=adapter_result.retryable,
            ),
            recovery_decision=recovery_decision,
        )
        self._save_result(command, result)
        return result

    def _dispatch_next_action(
        self,
        command: RuntimeCommand,
        snapshot: LiveSessionSnapshot,
        transition: PhaseTransition,
        now: datetime,
    ) -> AdapterDispatchResult:
        runner = self._runner_for_action(transition.next_action)
        if runner is None:
            return AdapterDispatchResult(status="ok", summary={"message": "no dispatch"})
        result = runner.run(
            command=command,
            snapshot=snapshot,
            transition=transition,
            now=now,
        )
        if isinstance(result, AdapterDispatchResult):
            return result
        return AdapterDispatchResult(status="ok", summary={"result": str(result)})

    def _runner_for_action(self, next_action: str) -> object | None:
        if next_action == "run_planned_show":
            return self._planned_show_runner
        if next_action in {"start_aftertalk", "continue_aftertalk"}:
            return self._aftertalk
        if next_action == "start_closing":
            return self._closing
        return None

    def _validate_snapshot(
        self,
        command: RuntimeCommand,
        snapshot: LiveSessionSnapshot,
    ) -> RuntimeServiceResult | None:
        if not snapshot.plan_completed:
            return None
        if _coerce_aftertalk_policy(snapshot.aftertalk_policy) is not None:
            return None

        result = RuntimeServiceResult(
            status="contract_error",
            session_id=command.session_id,
            phase=snapshot.current_phase,
            events=[],
            errors=[
                {
                    "code": "invalid_aftertalk_policy",
                    "message": "completed plan requires a valid aftertalk_policy",
                }
            ],
            correlation_id=_correlation_id(command),
        )
        self._save_result(command, result)
        return result

    def _persist_transition(
        self,
        command: RuntimeCommand,
        transition: PhaseTransition,
        now: datetime,
    ) -> PersistedTransitionRef:
        raw_ref = self._storage.persist_transition(
            command.session_id,
            command.command_id,
            transition,
            now,
        )
        return _coerce_transition_ref(raw_ref)

    def _storage_delegate(
        self,
        command: RuntimeCommand,
        now: datetime,
        method_name: str,
        event_type: str,
    ) -> RuntimeServiceResult:
        existing = self._existing_result(command)
        if existing is not None:
            return existing

        method = getattr(self._storage, method_name)
        snapshot = method(command, now)
        event = self._event(
            event_type=event_type,
            command=command,
            phase=snapshot.current_phase,
            payload={"phase": _phase_value(snapshot.current_phase)},
        )
        self._persist_event(event)
        result = RuntimeServiceResult(
            status="ok",
            session_id=command.session_id,
            phase=snapshot.current_phase,
            events=[event],
            errors=[],
            correlation_id=_correlation_id(command),
        )
        self._save_result(command, result)
        return result

    def _event(
        self,
        *,
        event_type: str,
        command: RuntimeCommand,
        phase: LiveSessionPhase | str | None,
        payload: dict[str, object],
    ) -> RuntimeServiceEvent:
        return RuntimeServiceEvent(
            event_type=event_type,
            session_id=command.session_id,
            phase=phase,
            payload=_sanitize_public_payload(payload),
            correlation_id=_correlation_id(command),
        )

    def _persist_event(self, event: RuntimeServiceEvent) -> None:
        if hasattr(self._storage, "persist_service_event"):
            self._storage.persist_service_event(event)

    def _existing_result(self, command: RuntimeCommand) -> RuntimeServiceResult | None:
        if not hasattr(self._storage, "get_command_result"):
            return None
        return self._storage.get_command_result(command.command_id)

    def _save_result(self, command: RuntimeCommand, result: RuntimeServiceResult) -> None:
        if hasattr(self._storage, "save_command_result"):
            self._storage.save_command_result(command.command_id, result)


def _result_status(adapter_result: AdapterDispatchResult) -> str:
    if adapter_result.status != "error":
        return "ok"
    if adapter_result.retryable:
        return "retryable_error"
    return "error"


def _errors_for_adapter_result(adapter_result: AdapterDispatchResult) -> list[dict[str, object]]:
    if adapter_result.status != "error":
        return []
    return [
        {
            "code": "adapter_error",
            "retryable": adapter_result.retryable,
            "summary": _sanitize_public_payload(adapter_result.summary),
        }
    ]


def _youtube_runtime_event_payload(
    payload: dict[str, object],
    *,
    cursor: YouTubePollingCursor | None = None,
) -> tuple[dict[str, object], YouTubePollingCursor | None]:
    raw_event = payload.get("youtube_event", payload.get("raw_event", payload))
    if isinstance(raw_event, NormalizedYouTubeEvent):
        normalized = raw_event
    elif isinstance(raw_event, Mapping):
        normalized = normalize_youtube_event(raw_event, cursor=cursor)
    else:
        raise ValueError("youtube_event must be a mapping")

    advanced_cursor = _advance_youtube_cursor(cursor, payload, normalized.event_id)
    runtime_payload = _sanitize_public_payload(
        {
            "event_id": normalized.event_id,
            "event_type": f"youtube_{normalized.event_type}",
            "public_payload": normalized.public_payload,
            "display_event": normalized.display_event,
            "duplicate": normalized.duplicate,
            "should_dispatch": normalized.should_dispatch,
        }
    )
    return runtime_payload, advanced_cursor


def _youtube_cursor_for_command(
    command: RuntimeCommand,
    storage: object,
) -> YouTubePollingCursor | None:
    payload_cursor = command.payload.get("polling_cursor")
    if payload_cursor is not None:
        return _youtube_polling_cursor(payload_cursor)
    if hasattr(storage, "load_youtube_polling_cursor"):
        return storage.load_youtube_polling_cursor(command.session_id)
    return None


def _advance_youtube_cursor(
    cursor: YouTubePollingCursor | None,
    payload: dict[str, object],
    event_id: str,
) -> YouTubePollingCursor | None:
    if cursor is None:
        return None
    page_info = _mapping(payload.get("page_info"))
    advance_kwargs: dict[str, object] = {"seen_event_ids": (event_id,)}
    if "next_page_token" in page_info:
        advance_kwargs["next_page_token"] = page_info["next_page_token"]
    elif "next_page_token" in payload:
        advance_kwargs["next_page_token"] = payload["next_page_token"]
    if "polling_interval_millis" in page_info:
        advance_kwargs["polling_interval_millis"] = page_info["polling_interval_millis"]
    elif "polling_interval_millis" in payload:
        advance_kwargs["polling_interval_millis"] = payload["polling_interval_millis"]
    return cursor.advance(**advance_kwargs)


def _youtube_polling_cursor(value: object) -> YouTubePollingCursor:
    if isinstance(value, YouTubePollingCursor):
        return value
    data = _mapping(value)
    return YouTubePollingCursor(
        live_chat_id=str(data.get("live_chat_id", "")),
        next_page_token=data.get("next_page_token"),
        polling_interval_millis=data.get("polling_interval_millis"),
        seen_event_ids=_list_value(data.get("seen_event_ids")),
    )


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _list_value(value: object) -> list[object]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _youtube_contract_error(command: RuntimeCommand, message: str) -> RuntimeServiceResult:
    return RuntimeServiceResult(
        status="contract_error",
        session_id=command.session_id,
        phase=None,
        events=[],
        errors=[
            {
                "code": "invalid_youtube_event_payload",
                "message": message,
            }
        ],
        correlation_id=_correlation_id(command),
    )


def _event_type(adapter_result: AdapterDispatchResult) -> str:
    if adapter_result.status == "error":
        return "adapter_error"
    return "runtime_action_dispatched"


def _recovery_decision(
    session_id: str,
    snapshot: LiveSessionSnapshot,
) -> RecoveryDecision:
    if (
        _phase_value(snapshot.current_phase) == LiveSessionPhase.CLOSING.value
        and not snapshot.closing_completed
    ):
        return RecoveryDecision(
            action="resume_closing",
            reason="closing_incomplete",
            session_id=session_id,
        )
    return RecoveryDecision(
        action="evaluate_phase",
        reason="snapshot_loaded",
        session_id=session_id,
    )


def _coerce_transition_ref(raw_ref: object) -> PersistedTransitionRef:
    if isinstance(raw_ref, PersistedTransitionRef):
        return raw_ref
    if isinstance(raw_ref, dict):
        return PersistedTransitionRef(
            transition_id=str(raw_ref["transition_id"]),
            session_id=str(raw_ref["session_id"]),
            previous_phase=raw_ref["previous_phase"],
            next_phase=_coerce_phase(raw_ref["next_phase"]),
            reason=_coerce_reason(raw_ref["reason"]),
        )
    raise TypeError("persist_transition must return PersistedTransitionRef or dict")


def _coerce_phase(value: LiveSessionPhase | str) -> LiveSessionPhase:
    if isinstance(value, LiveSessionPhase):
        return value
    return LiveSessionPhase(str(value))


def _coerce_reason(value: PhaseTransitionReason | str) -> PhaseTransitionReason:
    if isinstance(value, PhaseTransitionReason):
        return value
    return PhaseTransitionReason(str(value))


def _coerce_aftertalk_policy(value: AftertalkPolicy | str | None) -> AftertalkPolicy | None:
    if isinstance(value, AftertalkPolicy):
        return value
    try:
        return AftertalkPolicy(str(value))
    except ValueError:
        return None


def _sanitize_public_payload(value: Any) -> Any:
    forbidden_keys = {
        "hidden_prompt",
        "raw_prompt",
        "raw_payload",
        "raw_memoriacore_payload",
        "raw_adapter_payload",
        "raw_topic_pack",
        "topic_pack",
        "topic_pack_fact_cards",
        "factcard",
        "fact_card",
        "raw_factcard",
        "raw_fact_card",
        "raw_fact_cards",
        "memoriacore_raw",
        "youtube_raw",
        "access_token",
        "authorization",
        "secret",
        "token",
    }

    if isinstance(value, dict):
        return {
            key: _sanitize_public_payload(inner)
            for key, inner in value.items()
            if str(key).lower() not in forbidden_keys
        }
    if isinstance(value, list):
        return [_sanitize_public_payload(item) for item in value]
    return value


def _phase_value(phase: LiveSessionPhase | str | None) -> str | None:
    if phase is None:
        return None
    if isinstance(phase, LiveSessionPhase):
        return phase.value
    return str(phase)


def _correlation_id(command: RuntimeCommand) -> str:
    return f"runtime-{command.command_id}"


__all__ = [
    "AdapterDispatchResult",
    "PersistedTransitionRef",
    "RecoveryDecision",
    "RuntimeApplicationService",
    "RuntimeCommand",
    "RuntimeCommandType",
    "RuntimeServiceEvent",
    "RuntimeServiceResult",
]
