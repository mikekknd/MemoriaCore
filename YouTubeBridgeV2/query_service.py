"""Public read/query service for YouTubeBridgeV2."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any, Iterable


class V2QueryServiceError(RuntimeError):
    """V2 query service 無法讀取指定資料時的錯誤."""


class V2QueryService:
    """提供 V2 HTTP/SSE route 所需的 public read model."""

    def __init__(self, storage_manager: object) -> None:
        self._storage_manager = storage_manager

    def get_session(self, session_id: str) -> dict[str, object]:
        """回傳 session public status。"""

        record = self._session_record(session_id)
        return _sanitize_public_payload(
            {
                "session_id": session_id,
                "phase": _enum_value(record.get("current_phase", "unknown")),
                "aftertalk_policy": _enum_value(record.get("aftertalk_policy", "auto")),
                "plan_completed": bool(record.get("plan_completed", False)),
                "manual_close_requested": bool(record.get("manual_close_requested", False)),
                "closing_completion_status": _closing_status(record),
                "automation_control": _automation_control(record),
                "public_summary": record.get("public_summary", {}),
            }
        )

    def get_phase(self, session_id: str) -> dict[str, object]:
        """回傳 phase status body。"""

        record = self._session_record(session_id)
        return _sanitize_public_payload(
            {
                "session_id": session_id,
                "phase": _enum_value(record.get("current_phase", "unknown")),
                "aftertalk_policy": _enum_value(record.get("aftertalk_policy", "auto")),
                "plan_completed": bool(record.get("plan_completed", False)),
                "manual_close_requested": bool(record.get("manual_close_requested", False)),
                "closing_completion_status": _closing_status(record),
                "automation_control": _automation_control(record),
            }
        )

    def get_session_events(self, session_id: str, limit: int) -> list[dict[str, object]]:
        """回傳 session event history 的 public projection。"""

        self._session_record(session_id)
        return [_event_body(event) for event in self._events(session_id, limit)]

    def iter_operator_events(self, session_id: str) -> Iterable[dict[str, object]]:
        """產生 operator-safe SSE event。"""

        events = self.get_session_events(session_id, 100)
        yield _sanitize_public_payload(
            {
                "event_type": "operator_status",
                "session_id": session_id,
                "payload": self.get_phase(session_id),
                "diagnostics": {"event_count": len(events)},
            }
        )
        for event in events:
            yield _sanitize_public_payload(event)

    def iter_display_events(self, session_id: str) -> Iterable[dict[str, object]]:
        """產生 display-safe SSE event。"""

        for event in self.get_session_events(session_id, 100):
            yield _sanitize_display_payload(event)

    def _session_record(self, session_id: str) -> dict[str, object]:
        if not hasattr(self._storage_manager, "get_v2_session"):
            raise V2QueryServiceError("storage manager missing get_v2_session")
        record = self._storage_manager.get_v2_session(session_id)
        if record is None:
            raise V2QueryServiceError(f"session not found: {session_id}")
        return _object_to_dict(record)

    def _events(self, session_id: str, limit: int) -> list[dict[str, object]]:
        safe_limit = max(1, min(int(limit), 500))
        if hasattr(self._storage_manager, "list_v2_live_events"):
            return [
                _object_to_dict(event)
                for event in self._storage_manager.list_v2_live_events(session_id, safe_limit)
            ]
        return []


def _event_body(event: dict[str, object]) -> dict[str, object]:
    public_payload = event.get(
        "public_payload",
        event.get("public_metadata", event.get("payload", {})),
    )
    return _sanitize_public_payload(
        {
            "event_id": str(event.get("event_id", "")),
            "event_type": str(event.get("event_type", "")),
            "public_payload": public_payload,
        }
    )


def _closing_status(record: dict[str, object]) -> str:
    if bool(record.get("closing_completed", False)):
        return "complete"
    return "incomplete"


def _automation_control(record: dict[str, object]) -> dict[str, object]:
    metadata = record.get("metadata", {})
    if isinstance(metadata, dict):
        raw = metadata.get("automation_control", {})
        if isinstance(raw, dict):
            return _sanitize_public_payload(
                {
                    "enabled": bool(raw.get("enabled", True)),
                    "paused": bool(raw.get("paused", False)),
                    "reason": str(raw.get("reason", "") or ""),
                }
            )
    return {"enabled": True, "paused": False, "reason": ""}


def _object_to_dict(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return dict(value)
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "__dict__"):
        return dict(vars(value))
    return {"value": value}


def _enum_value(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
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


_DISPLAY_FORBIDDEN_KEYS = _PUBLIC_FORBIDDEN_KEYS | {
    "diagnostics",
    "operator_controls",
}


def _sanitize_public_payload(value: Any) -> Any:
    return _sanitize_payload(value, _PUBLIC_FORBIDDEN_KEYS)


def _sanitize_display_payload(value: Any) -> Any:
    return _sanitize_payload(value, _DISPLAY_FORBIDDEN_KEYS)


def _sanitize_payload(value: Any, forbidden_keys: set[str]) -> Any:
    if isinstance(value, dict):
        return {
            key: _sanitize_payload(inner_value, forbidden_keys)
            for key, inner_value in value.items()
            if str(key).lower() not in forbidden_keys
        }
    if isinstance(value, list):
        return [_sanitize_payload(item, forbidden_keys) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_payload(item, forbidden_keys) for item in value)
    return value


__all__ = ["V2QueryService", "V2QueryServiceError"]
