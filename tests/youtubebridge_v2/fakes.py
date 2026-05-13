from __future__ import annotations

from copy import deepcopy
from datetime import datetime

from YouTubeBridgeV2.runtime.application_service import AdapterDispatchResult
from YouTubeBridgeV2.runtime.phase import LiveSessionPhase


class InMemoryV2StorageManager:
    """Integration tests 使用的 StorageManager-like fake。"""

    def __init__(self) -> None:
        self.sessions: dict[str, dict[str, object]] = {}
        self.transitions: dict[str, dict[str, object]] = {}
        self.live_events: list[dict[str, object]] = []
        self.interactions: list[dict[str, object]] = []
        self.finalizations: list[dict[str, object]] = []
        self.tts_deliveries: list[dict[str, object]] = []
        self.command_results: dict[str, object] = {}

    def create_v2_session(self, record: dict[str, object]) -> dict[str, object]:
        stored = deepcopy(record)
        self.sessions[str(stored["session_id"])] = stored
        return deepcopy(stored)

    def get_v2_session(self, session_id: str) -> dict[str, object] | None:
        record = self.sessions.get(session_id)
        return deepcopy(record) if record is not None else None

    def update_v2_session(self, session_id: str, patch: dict[str, object]) -> dict[str, object]:
        current = self.sessions[session_id]
        current.update(deepcopy(patch))
        return deepcopy(current)

    def get_v2_phase_transition(self, transition_id: str) -> dict[str, object] | None:
        record = self.transitions.get(transition_id)
        return deepcopy(record) if record is not None else None

    def append_v2_phase_transition(
        self,
        _session_id: str,
        record: dict[str, object],
    ) -> dict[str, object]:
        stored = deepcopy(record)
        self.transitions[str(stored["transition_id"])] = stored
        return deepcopy(stored)

    def append_v2_live_event(
        self,
        session_id: str,
        record: dict[str, object],
    ) -> dict[str, object]:
        stored = deepcopy(record)
        if not stored.get("event_id"):
            stored["event_id"] = f"evt-{len(self.live_events) + 1}"
        stored["session_id"] = session_id
        self.live_events.append(stored)
        return deepcopy(stored)

    def list_v2_live_events(
        self,
        session_id: str,
        limit: int = 100,
    ) -> list[dict[str, object]]:
        events = [event for event in self.live_events if event.get("session_id") == session_id]
        return deepcopy(events[-limit:])

    def append_v2_interaction(
        self,
        session_id: str,
        record: dict[str, object],
    ) -> dict[str, object]:
        stored = deepcopy(record)
        stored["session_id"] = session_id
        self.interactions.append(stored)
        return deepcopy(stored)

    def append_v2_finalization(
        self,
        session_id: str,
        record: dict[str, object],
    ) -> dict[str, object]:
        stored = deepcopy(record)
        stored["session_id"] = session_id
        self.finalizations.append(stored)
        self.update_v2_session(session_id, {"closing_completed": True})
        return deepcopy(stored)

    def append_v2_tts_request(
        self,
        session_id: str,
        record: dict[str, object],
    ) -> dict[str, object]:
        stored = deepcopy(record)
        stored["session_id"] = session_id
        self.tts_deliveries.append(stored)
        return deepcopy(stored)

    def list_v2_tts_deliveries(
        self,
        session_id: str,
        limit: int = 100,
        status: str | None = None,
    ) -> list[dict[str, object]]:
        deliveries = [
            item
            for item in self.tts_deliveries
            if item.get("session_id") == session_id
            and (status is None or item.get("status") == status)
        ]
        return deepcopy(deliveries[-limit:])

    def ack_v2_tts_delivery(
        self,
        session_id: str,
        delivery_id: str,
        record: dict[str, object],
    ) -> dict[str, object]:
        for item in self.tts_deliveries:
            if item.get("session_id") == session_id and item.get("delivery_id") == delivery_id:
                duplicate = item.get("status") == "delivered"
                item["status"] = "delivered"
                item["acknowledged_at"] = record.get("acknowledged_at")
                return deepcopy(
                    {
                        **item,
                        "duplicate": duplicate,
                        "phase_transition_requested": False,
                    }
                )
        raise KeyError(delivery_id)

    def timeout_v2_tts_delivery(
        self,
        session_id: str,
        delivery_id: str,
        record: dict[str, object],
    ) -> dict[str, object]:
        for item in self.tts_deliveries:
            if item.get("session_id") == session_id and item.get("delivery_id") == delivery_id:
                timeout_seconds = int(record.get("timeout_seconds", 0) or 0)
                if item.get("status") == "delivered":
                    return deepcopy(
                        {
                            **item,
                            "timeout_seconds": timeout_seconds,
                            "timeout_ignored": True,
                            "phase_transition_requested": False,
                        }
                    )
                item["status"] = "timeout"
                item["timeout_seconds"] = timeout_seconds
                item["metadata"] = {
                    **dict(item.get("metadata", {})),
                    **dict(record.get("metadata", {})),
                }
                return deepcopy({**item, "phase_transition_requested": False})
        raise KeyError(delivery_id)

    def get_v2_command_result(self, command_id: str) -> object | None:
        return self.command_results.get(command_id)

    def save_v2_command_result(self, command_id: str, result: object) -> None:
        self.command_results[command_id] = result


class FakePlannedShowRunner:
    def __init__(self, storage_manager: InMemoryV2StorageManager) -> None:
        self.storage_manager = storage_manager
        self.calls: list[dict[str, object]] = []

    def run(self, *, command, snapshot, transition, now: datetime) -> AdapterDispatchResult:
        self.calls.append(
            {
                "command": command,
                "snapshot": snapshot,
                "transition": transition,
                "now": now,
            }
        )
        self.storage_manager.update_v2_session(
            command.session_id,
            {
                "plan_completed": True,
                "last_planned_turn_at": now,
            },
        )
        self.storage_manager.append_v2_interaction(
            command.session_id,
            {
                "interaction_id": f"planned-{len(self.calls)}",
                "phase": LiveSessionPhase.PLANNED_SHOW.value,
                "speaker_id": "host",
                "public_content_summary": {
                    "text": "planned show turn",
                    "hidden_prompt": "must not leak",
                },
                "correlation_id": f"runtime-{command.command_id}",
                "created_at": now,
            },
        )
        return AdapterDispatchResult(
            status="ok",
            summary={
                "message": "planned show advanced",
                "raw_topic_pack": "must not leak",
            },
        )


class FakeAftertalkRunner:
    def __init__(self, storage_manager: InMemoryV2StorageManager) -> None:
        self.storage_manager = storage_manager
        self.calls: list[dict[str, object]] = []

    def run(self, *, command, snapshot, transition, now: datetime) -> AdapterDispatchResult:
        self.calls.append(
            {
                "command": command,
                "snapshot": snapshot,
                "transition": transition,
                "now": now,
            }
        )
        self.storage_manager.append_v2_interaction(
            command.session_id,
            {
                "interaction_id": f"aftertalk-{len(self.calls)}",
                "phase": LiveSessionPhase.AFTERTALK.value,
                "speaker_id": "cast",
                "public_content_summary": {
                    "text": "aftertalk response",
                    "raw_memoriacore_payload": {"token": "must not leak"},
                },
                "correlation_id": f"runtime-{command.command_id}",
                "created_at": now,
            },
        )
        return AdapterDispatchResult(
            status="ok",
            summary={
                "message": "aftertalk continued",
                "raw_payload": {"token": "must not leak"},
            },
        )


class FakeClosingRunner:
    def __init__(self, storage_manager: InMemoryV2StorageManager) -> None:
        self.storage_manager = storage_manager
        self.calls: list[dict[str, object]] = []

    def run(self, *, command, snapshot, transition, now: datetime) -> AdapterDispatchResult:
        self.calls.append(
            {
                "command": command,
                "snapshot": snapshot,
                "transition": transition,
                "now": now,
            }
        )
        self.storage_manager.append_v2_finalization(
            command.session_id,
            {
                "finalization_id": f"closing-{len(self.calls)}",
                "closing_completion_status": "complete",
                "completed_at": now,
                "display_summary": {
                    "text": "closing complete",
                    "hidden_prompt": "must not leak",
                },
                "error_summary": {},
            },
        )
        return AdapterDispatchResult(
            status="ok",
            summary={
                "message": "closing completed",
                "raw_memoriacore_payload": {"token": "must not leak"},
            },
        )
