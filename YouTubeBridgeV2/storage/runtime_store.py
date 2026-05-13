"""Runtime Application Service storage port for YouTubeBridgeV2.

此 port 把 `RuntimeApplicationService` 需要的 imperative storage methods
轉成 StorageManager-like backend 呼叫。V2 不在此直接碰 SQLite。
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from YouTubeBridgeV2.live_episode_plan.runner import (
    LiveEpisodePlanState,
    validate_episode_plan_contract,
)
from YouTubeBridgeV2.runtime.application_service import (
    AdapterDispatchResult,
    PersistedTransitionRef,
    RecoveryDecision,
    RuntimeCommand,
    RuntimeServiceEvent,
    RuntimeServiceResult,
)
from YouTubeBridgeV2.runtime.phase import (
    LiveSessionPhase,
    PhaseTransition,
    PhaseTransitionReason,
)
from YouTubeBridgeV2.storage.repositories import (
    EventRepository,
    FinalizationRepository,
    InteractionRepository,
    PhaseTransitionRepository,
    SessionRepository,
)


class RuntimeStorageContractError(RuntimeError):
    """StorageManager-like backend 缺少 runtime port 必要 method。"""


class RuntimeStoragePort:
    """`RuntimeApplicationService` 使用的 V2 storage adapter。"""

    def __init__(self, storage_manager: object) -> None:
        self._storage_manager = storage_manager
        self._sessions = SessionRepository(storage_manager)
        self._transitions = PhaseTransitionRepository(storage_manager)
        self._events = EventRepository(storage_manager)
        self._interactions = InteractionRepository(storage_manager)
        self._finalizations = FinalizationRepository(storage_manager)

    def create_session(self, command: RuntimeCommand, now: datetime):
        """建立 V2 session 並回傳 Runtime Phase snapshot。"""

        payload = _object_to_dict(command.payload)
        metadata = _object_to_dict(payload.get("metadata", {}))
        record = {
            "session_id": command.session_id,
            "current_phase": LiveSessionPhase.PLANNED_SHOW.value,
            "session_started_at": now,
            "plan_completed": False,
            "aftertalk_policy": str(payload.get("aftertalk_policy") or "auto"),
            "duration_policy": _duration_policy_record(metadata.get("duration_policy")),
            "manual_close_requested": False,
            "closing_completed": False,
            "public_summary": _sanitize_public_payload(
                {
                    "plan_id": payload.get("plan_id"),
                    "metadata": metadata,
                }
            ),
        }
        return self._sessions.create_session(record)

    def bind_plan(self, command: RuntimeCommand, _now: datetime):
        """保存 LiveEpisodePlan public summary 與可重啟的 cursor state。"""

        plan = _object_to_dict(command.payload.get("plan", {}))
        contract = validate_episode_plan_contract(plan)
        plan_state = LiveEpisodePlanState(contract=contract)
        patch = {
            "plan_id": contract.plan_id,
            "plan_completed": False,
            "live_episode_plan_state": _live_episode_plan_state_record(plan_state),
            "public_summary": _sanitize_public_payload(
                {
                    "plan_id": contract.plan_id,
                    "plan_title": contract.title,
                    "title": contract.title,
                    "turn_count": len(contract.turns),
                    "completed_turn_count": 0,
                    "status": contract.status.value,
                }
            ),
        }
        return self._update_session(command.session_id, patch)

    def start_session(self, command: RuntimeCommand, now: datetime):
        """標記 session 已開始並回傳 snapshot。"""

        return self._update_session(
            command.session_id,
            {
                "current_phase": LiveSessionPhase.PLANNED_SHOW.value,
                "session_started_at": now,
            },
        )

    def read_snapshot(self, session_id: str):
        """讀取 Runtime Phase 所需 snapshot。"""

        return self._sessions.read_live_session_snapshot(session_id)

    def request_manual_close(self, session_id: str, command_id: str, now: datetime):
        """保存 manual close request，phase 轉換仍交給 Runtime Phase 決定。"""

        return self._update_session(
            session_id,
            {
                "manual_close_requested": True,
                "manual_close": {
                    "command_id": command_id,
                    "requested_at": now,
                },
            },
        )

    def update_aftertalk_policy(self, command: RuntimeCommand, _now: datetime):
        """更新 aftertalk policy 並回傳 snapshot。"""

        return self._update_session(
            command.session_id,
            {"aftertalk_policy": str(command.payload.get("aftertalk_policy", "auto"))},
        )

    def finalize_closing(self, command: RuntimeCommand, now: datetime):
        """標記 closing 已完成並保存 finalization summary。"""

        self._finalizations.append_finalization_record(
            command.session_id,
            {
                "finalization_id": f"{command.session_id}:{command.command_id}:finalization",
                "closing_completion_status": "complete",
                "completed_at": now,
                "display_summary": {"message": "closing finalized"},
                "error_summary": {},
            },
        )
        return self._update_session(command.session_id, {"closing_completed": True})

    def persist_transition(
        self,
        session_id: str,
        command_id: str,
        transition: PhaseTransition,
        now: datetime,
    ) -> dict[str, object]:
        """保存 phase transition 並更新 session current phase。"""

        record = {
            "transition_id": f"{session_id}:{command_id}:transition",
            "previous_phase": _enum_value(transition.current_phase),
            "next_phase": _enum_value(transition.next_phase),
            "reason": _enum_value(transition.reason),
            "metadata": transition.metadata,
            "created_at": now,
        }
        stored = self._transitions.append_phase_transition(session_id, record)
        patch: dict[str, object] = {"current_phase": _enum_value(transition.next_phase)}
        if transition.next_phase is LiveSessionPhase.ENDED:
            patch["ended_at"] = now
        self._update_session(session_id, patch)
        return stored

    def persist_service_event(self, event: RuntimeServiceEvent) -> None:
        """保存 runtime service event 的 public projection。"""

        event_data = _object_to_dict(event)
        self._events.append_live_event(
            str(event_data["session_id"]),
            {
                "event_id": _event_id(event_data),
                "event_type": str(event_data.get("event_type", "")),
                "public_metadata": {
                    "phase": _enum_value(event_data.get("phase")),
                    "payload": event_data.get("payload", {}),
                    "correlation_id": event_data.get("correlation_id", ""),
                },
            },
        )

    def persist_youtube_event(
        self,
        session_id: str,
        payload: dict[str, object],
        now: datetime,
    ) -> None:
        """保存 normalized YouTube event public summary。"""

        self._events.append_live_event(
            session_id,
            {
                "event_id": f"{session_id}:youtube:{now.isoformat()}",
                "event_type": "youtube_event",
                "public_metadata": _sanitize_public_payload(payload),
                "created_at": now,
            },
        )

    def persist_error_summary(
        self,
        session_id: str,
        command_id: str,
        summary: dict[str, object],
        retryable: bool,
    ) -> None:
        """保存 adapter/runtime error 的 public summary。"""

        self._events.append_live_event(
            session_id,
            {
                "event_id": f"{session_id}:{command_id}:error",
                "event_type": "adapter_error",
                "public_metadata": {
                    "summary": _sanitize_public_payload(summary),
                    "retryable": retryable,
                },
            },
        )

    def get_command_result(self, command_id: str) -> object | None:
        """讀取 command idempotency result。"""

        if hasattr(self._storage_manager, "get_v2_command_result"):
            result = self._storage_manager.get_v2_command_result(command_id)
            if result is None:
                return None
            return _runtime_result_from_record(result)
        return None

    def save_command_result(self, command_id: str, result: object) -> None:
        """保存 command idempotency result。"""

        if not hasattr(self._storage_manager, "save_v2_command_result"):
            raise RuntimeStorageContractError("storage manager missing save_v2_command_result")
        self._storage_manager.save_v2_command_result(command_id, _json_safe_value(result))

    def _update_session(self, session_id: str, patch: dict[str, object]):
        if not hasattr(self._storage_manager, "update_v2_session"):
            raise RuntimeStorageContractError("storage manager missing update_v2_session")
        self._storage_manager.update_v2_session(
            session_id,
            _sanitize_public_payload(patch),
        )
        return self._sessions.read_live_session_snapshot(session_id)


def _duration_policy_record(raw_policy: object) -> dict[str, object]:
    policy = _object_to_dict(raw_policy or {})
    return {
        "planned_duration_seconds": policy.get("planned_duration_seconds"),
        "auto_finalize_on_duration": bool(policy.get("auto_finalize_on_duration", False)),
        "aftertalk_requires_remaining_time": bool(
            policy.get("aftertalk_requires_remaining_time", True)
        ),
    }


def _event_id(event_data: dict[str, object]) -> str:
    return (
        f"{event_data.get('session_id', '')}:"
        f"{event_data.get('correlation_id', '')}:"
        f"{event_data.get('event_type', '')}"
    )


def _live_episode_plan_state_record(
    state: LiveEpisodePlanState,
    *,
    last_memoria_session_id: str | None = None,
) -> dict[str, object]:
    record = _json_safe_value(state)
    record["last_memoria_session_id"] = last_memoria_session_id
    return _sanitize_public_payload(record)


def _object_to_dict(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return dict(value)
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "__dict__"):
        return dict(vars(value))
    return {}


def _runtime_result_from_record(record: object) -> RuntimeServiceResult:
    data = _object_to_dict(record)
    return RuntimeServiceResult(
        status=str(data.get("status", "")),
        session_id=str(data.get("session_id", "")),
        phase=_optional_phase(data.get("phase")),
        events=[
            _runtime_event_from_record(event)
            for event in _list_value(data.get("events"))
        ],
        errors=[
            _object_to_dict(error)
            for error in _list_value(data.get("errors"))
        ],
        correlation_id=str(data.get("correlation_id", "")),
        transition_ref=_transition_ref_from_record(data.get("transition_ref")),
        adapter_result=_adapter_result_from_record(data.get("adapter_result")),
        recovery_decision=_recovery_decision_from_record(data.get("recovery_decision")),
    )


def _runtime_event_from_record(record: object) -> RuntimeServiceEvent:
    data = _object_to_dict(record)
    return RuntimeServiceEvent(
        event_type=str(data.get("event_type", "")),
        session_id=str(data.get("session_id", "")),
        phase=_optional_phase(data.get("phase")),
        payload=_object_to_dict(data.get("payload", {})),
        correlation_id=str(data.get("correlation_id", "")),
    )


def _transition_ref_from_record(record: object) -> PersistedTransitionRef | None:
    if record is None:
        return None
    data = _object_to_dict(record)
    return PersistedTransitionRef(
        transition_id=str(data.get("transition_id", "")),
        session_id=str(data.get("session_id", "")),
        previous_phase=_enum_value(data.get("previous_phase")),
        next_phase=_phase(data.get("next_phase")),
        reason=_reason(data.get("reason")),
    )


def _adapter_result_from_record(record: object) -> AdapterDispatchResult | None:
    if record is None:
        return None
    data = _object_to_dict(record)
    return AdapterDispatchResult(
        status=str(data.get("status", "")),
        summary=_object_to_dict(data.get("summary", {})),
        retryable=bool(data.get("retryable", False)),
    )


def _recovery_decision_from_record(record: object) -> RecoveryDecision | None:
    if record is None:
        return None
    data = _object_to_dict(record)
    return RecoveryDecision(
        action=str(data.get("action", "")),
        reason=str(data.get("reason", "")),
        session_id=str(data.get("session_id", "")),
    )


def _optional_phase(value: object) -> LiveSessionPhase | str | None:
    if value is None:
        return None
    try:
        return _phase(value)
    except ValueError:
        return str(value)


def _phase(value: object) -> LiveSessionPhase:
    if isinstance(value, LiveSessionPhase):
        return value
    return LiveSessionPhase(str(value))


def _reason(value: object) -> PhaseTransitionReason:
    if isinstance(value, PhaseTransitionReason):
        return value
    return PhaseTransitionReason(str(value))


def _enum_value(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    return value


def _list_value(value: object) -> list[object]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _json_safe_value(value: Any) -> Any:
    if is_dataclass(value):
        return _json_safe_value(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {
            str(key): _json_safe_value(inner_value)
            for key, inner_value in value.items()
        }
    if isinstance(value, list):
        return [_json_safe_value(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe_value(item) for item in value]
    return value


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


def _sanitize_public_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _sanitize_public_payload(inner_value)
            for key, inner_value in value.items()
            if str(key).lower() not in _PUBLIC_FORBIDDEN_KEYS
        }
    if isinstance(value, list):
        return [_sanitize_public_payload(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_public_payload(item) for item in value)
    return value


__all__ = ["RuntimeStorageContractError", "RuntimeStoragePort"]
