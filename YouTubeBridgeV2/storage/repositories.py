"""StorageManager-like repository adapters for YouTubeBridgeV2.

本模組只定義 V2 repository contract 到注入式 StorageManager-like 邊界的映射。
不得在此 import sqlite3/aiosqlite，也不得自行持有 SQLite connection。
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime
from typing import Any

from YouTubeBridgeV2.runtime.phase import (
    AftertalkPolicy,
    DurationPolicy,
    LiveSessionPhase,
    LiveSessionSnapshot,
    PhaseTransition,
    PhaseTransitionReason,
)


class StorageRecordNotFound(KeyError):
    """StorageManager boundary 找不到 V2 record."""


class StorageContractError(ValueError):
    """StorageManager 回傳資料不符合 V2 storage contract."""


class StorageBackendNotConfigured(StorageContractError):
    """V2 storage adapter skeleton 尚未注入實際 backend."""


class StorageManagerBackedRepository:
    """聚合 V2 storage repositories，委派給注入的 StorageManager-like 物件."""

    def __init__(self, storage_manager: object | None = None) -> None:
        self.storage_manager = _require_storage_manager(storage_manager)
        self.sessions = SessionRepository(self.storage_manager)
        self.phase_transitions = PhaseTransitionRepository(self.storage_manager)
        self.events = EventRepository(self.storage_manager)
        self.interactions = InteractionRepository(self.storage_manager)
        self.finalizations = FinalizationRepository(self.storage_manager)


class SessionRepository:
    """V2 session lifecycle 與 Runtime Phase snapshot read repository."""

    def __init__(self, storage_manager: object | None = None) -> None:
        self.storage_manager = _require_storage_manager(storage_manager)

    def create_session(self, session_record: dict[str, object]) -> LiveSessionSnapshot:
        record = _redact_public_value(_object_to_dict(session_record))
        if not hasattr(self.storage_manager, "create_v2_session"):
            raise StorageContractError("StorageManager missing create_v2_session")
        stored = self.storage_manager.create_v2_session(record)
        return _snapshot_from_record(stored)

    def read_live_session_snapshot(self, session_id: str) -> LiveSessionSnapshot:
        if not hasattr(self.storage_manager, "get_v2_session"):
            raise StorageContractError("StorageManager missing get_v2_session")
        record = self.storage_manager.get_v2_session(session_id)
        if record is None:
            raise StorageRecordNotFound(session_id)
        return _snapshot_from_record(record)


class PhaseTransitionRepository:
    """Append-only phase transition repository with transition-id idempotency."""

    def __init__(self, storage_manager: object | None = None) -> None:
        self.storage_manager = _require_storage_manager(storage_manager)

    def append_phase_transition(
        self,
        session_id: str,
        transition: PhaseTransition | dict[str, object],
    ) -> dict[str, object]:
        record = _transition_record(session_id, transition)
        transition_id = str(record["transition_id"])

        existing = _call_optional(
            self.storage_manager,
            "get_v2_phase_transition",
            transition_id,
        )
        if existing is not None:
            return _redact_public_value(existing)

        if not hasattr(self.storage_manager, "append_v2_phase_transition"):
            raise StorageContractError("StorageManager missing append_v2_phase_transition")
        stored = self.storage_manager.append_v2_phase_transition(session_id, record)
        return _redact_public_value(stored)


class EventRepository:
    """V2 normalized event append repository."""

    def __init__(self, storage_manager: object | None = None) -> None:
        self.storage_manager = _require_storage_manager(storage_manager)

    def append_live_event(
        self,
        session_id: str,
        event: dict[str, object],
    ) -> dict[str, object]:
        record = _event_record(session_id, event)
        if not hasattr(self.storage_manager, "append_v2_live_event"):
            raise StorageContractError("StorageManager missing append_v2_live_event")
        stored = self.storage_manager.append_v2_live_event(session_id, record)
        return _redact_public_value(stored)


class InteractionRepository:
    """V2 planned show / aftertalk response summary repository."""

    def __init__(self, storage_manager: object | None = None) -> None:
        self.storage_manager = _require_storage_manager(storage_manager)

    def append_interaction(
        self,
        session_id: str,
        interaction: dict[str, object],
    ) -> dict[str, object]:
        record = _interaction_record(session_id, interaction)
        if not hasattr(self.storage_manager, "append_v2_interaction"):
            raise StorageContractError("StorageManager missing append_v2_interaction")
        stored = self.storage_manager.append_v2_interaction(session_id, record)
        return _redact_public_value(stored)


class FinalizationRepository:
    """V2 closing finalization repository."""

    def __init__(self, storage_manager: object | None = None) -> None:
        self.storage_manager = _require_storage_manager(storage_manager)

    def append_finalization_record(
        self,
        session_id: str,
        finalization: object,
    ) -> dict[str, object]:
        record = _finalization_record(session_id, finalization)
        if not hasattr(self.storage_manager, "append_v2_finalization"):
            raise StorageContractError("StorageManager missing append_v2_finalization")
        stored = self.storage_manager.append_v2_finalization(session_id, record)
        return _redact_public_value(stored)


def read_live_session_snapshot(session_id: str) -> LiveSessionSnapshot:
    """從已設定的預設 repository 讀取 Runtime Phase snapshot."""

    return _default_repository().sessions.read_live_session_snapshot(session_id)


def append_phase_transition(
    session_id: str,
    transition: PhaseTransition | dict[str, object],
) -> dict[str, object]:
    """透過已設定的預設 repository append phase transition."""

    return _default_repository().phase_transitions.append_phase_transition(
        session_id,
        transition,
    )


def append_live_event(session_id: str, event: dict[str, object]) -> dict[str, object]:
    """透過已設定的預設 repository append normalized event."""

    return _default_repository().events.append_live_event(session_id, event)


def append_interaction(
    session_id: str,
    interaction: dict[str, object],
) -> dict[str, object]:
    """透過已設定的預設 repository append interaction summary."""

    return _default_repository().interactions.append_interaction(session_id, interaction)


def _default_repository() -> StorageManagerBackedRepository:
    return StorageManagerBackedRepository()


def _require_storage_manager(storage_manager: object | None) -> object:
    if storage_manager is None:
        raise StorageBackendNotConfigured(
            "YouTubeBridgeV2 storage backend is not configured; "
            "pass a StorageManager-like object that implements the V2 storage methods."
        )
    return storage_manager


def _snapshot_from_record(record: object) -> LiveSessionSnapshot:
    data = _object_to_dict(record)
    missing = [
        key
        for key in (
            "current_phase",
            "session_started_at",
            "plan_completed",
            "aftertalk_policy",
            "duration_policy",
        )
        if key not in data
    ]
    if missing:
        raise StorageContractError(f"session snapshot missing fields: {', '.join(missing)}")

    return LiveSessionSnapshot(
        current_phase=_coerce_phase(data["current_phase"]),
        session_started_at=_coerce_datetime(data["session_started_at"]),
        plan_completed=bool(data["plan_completed"]),
        aftertalk_policy=_coerce_aftertalk_policy(data["aftertalk_policy"]),
        duration_policy=_duration_policy(data["duration_policy"]),
        manual_close_requested=bool(data.get("manual_close_requested", False)),
        closing_completed=bool(data.get("closing_completed", False)),
    )


def _transition_record(
    session_id: str,
    transition: PhaseTransition | dict[str, object],
) -> dict[str, object]:
    data = _object_to_dict(transition)
    transition_id_value = data.get("transition_id")
    if transition_id_value is None or not str(transition_id_value).strip():
        raise StorageContractError("transition missing transition_id")
    transition_id = str(transition_id_value)
    previous_phase = data.get("previous_phase", data.get("current_phase"))
    if previous_phase is None:
        raise StorageContractError("transition missing previous_phase")
    if "next_phase" not in data or "reason" not in data:
        raise StorageContractError("transition missing next_phase or reason")

    return _redact_public_value(
        {
            "transition_id": transition_id,
            "session_id": session_id,
            "previous_phase": _phase_value(previous_phase),
            "next_phase": _phase_value(data["next_phase"]),
            "reason": _reason_value(data["reason"]),
            "metadata": data.get("metadata", {}),
            "created_at": data.get("created_at"),
        }
    )


def _event_record(session_id: str, event: dict[str, object]) -> dict[str, object]:
    data = _object_to_dict(event)
    return _redact_public_value(
        {
            "session_id": session_id,
            "event_id": str(data.get("event_id", "")),
            "event_type": str(data.get("event_type", "")),
            "public_metadata": data.get("public_metadata", {}),
            "created_at": data.get("created_at"),
        }
    )


def _interaction_record(
    session_id: str,
    interaction: dict[str, object],
) -> dict[str, object]:
    data = _object_to_dict(interaction)
    return _redact_public_value(
        {
            "session_id": session_id,
            "interaction_id": str(data.get("interaction_id", "")),
            "phase": str(data.get("phase", "")),
            "speaker_id": str(data.get("speaker_id", "")),
            "public_content_summary": data.get("public_content_summary", {}),
            "correlation_id": str(data.get("correlation_id", "")),
            "created_at": data.get("created_at"),
        }
    )


def _finalization_record(session_id: str, finalization: object) -> dict[str, object]:
    data = _object_to_dict(finalization)
    completion_status = data.get(
        "closing_completion_status",
        data.get("status", "incomplete"),
    )
    return _redact_public_value(
        {
            "session_id": session_id,
            "finalization_id": str(data.get("finalization_id", "")),
            "closing_completion_status": _enum_value(completion_status),
            "completed_at": data.get("completed_at"),
            "display_summary": data.get("display_summary", {}),
            "error_summary": data.get("error_summary", {}),
        }
    )


def _duration_policy(raw_policy: object) -> DurationPolicy:
    data = _object_to_dict(raw_policy)
    return DurationPolicy(
        planned_duration_seconds=_optional_int(data.get("planned_duration_seconds")),
        auto_finalize_on_duration=bool(data.get("auto_finalize_on_duration", False)),
        aftertalk_requires_remaining_time=bool(
            data.get("aftertalk_requires_remaining_time", True)
        ),
    )


def _coerce_phase(value: object) -> LiveSessionPhase:
    if isinstance(value, LiveSessionPhase):
        return value
    return LiveSessionPhase(str(value))


def _coerce_aftertalk_policy(value: object) -> AftertalkPolicy:
    if isinstance(value, AftertalkPolicy):
        return value
    return AftertalkPolicy(str(value))


def _coerce_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise StorageContractError("session_started_at must be datetime or ISO string")


def _phase_value(value: object) -> str:
    if isinstance(value, LiveSessionPhase):
        return value.value
    return str(value)


def _reason_value(value: object) -> str:
    if isinstance(value, PhaseTransitionReason):
        return value.value
    return str(value)


def _enum_value(value: object) -> str:
    if hasattr(value, "value"):
        return str(getattr(value, "value"))
    return str(value)


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return int(value)


def _object_to_dict(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return dict(value)
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "__dict__"):
        return dict(vars(value))
    raise StorageContractError("record must be mapping or dataclass")


def _call_optional(target: object, method_name: str, *args: object) -> object | None:
    method = getattr(target, method_name, None)
    if method is None:
        return None
    return method(*args)


def _redact_public_value(value: Any) -> Any:
    forbidden_keys = {
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
    "EventRepository",
    "FinalizationRepository",
    "InteractionRepository",
    "PhaseTransitionRepository",
    "SessionRepository",
    "StorageBackendNotConfigured",
    "StorageContractError",
    "StorageManagerBackedRepository",
    "StorageRecordNotFound",
    "append_interaction",
    "append_live_event",
    "append_phase_transition",
    "read_live_session_snapshot",
]
