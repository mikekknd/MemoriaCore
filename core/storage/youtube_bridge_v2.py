from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from core.runtime_paths import runtime_file


class YouTubeBridgeV2RepositoryMixin:
    """YouTubeBridgeV2 durable storage methods exposed through StorageManager."""

    _YOUTUBE_BRIDGE_V2_DB = runtime_file("youtubebridge_v2.db")

    def _init_youtube_bridge_v2_db(self):
        db_path = getattr(self, "youtube_bridge_v2_db_path", None) or self._YOUTUBE_BRIDGE_V2_DB
        conn = sqlite3.connect(db_path, timeout=15.0)
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS yb2_sessions (
                session_id TEXT PRIMARY KEY,
                current_phase TEXT NOT NULL,
                session_started_at TEXT NOT NULL,
                plan_completed INTEGER NOT NULL DEFAULT 0,
                aftertalk_policy TEXT NOT NULL DEFAULT 'auto',
                duration_policy_json TEXT NOT NULL DEFAULT '{}',
                manual_close_requested INTEGER NOT NULL DEFAULT 0,
                closing_completed INTEGER NOT NULL DEFAULT 0,
                public_summary_json TEXT NOT NULL DEFAULT '{}',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                plan_id TEXT DEFAULT NULL,
                manual_close_json TEXT NOT NULL DEFAULT '{}',
                ended_at TEXT DEFAULT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_yb2_sessions_phase "
            "ON yb2_sessions(current_phase)"
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS yb2_phase_transitions (
                transition_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                previous_phase TEXT NOT NULL,
                next_phase TEXT NOT NULL,
                reason TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES yb2_sessions(session_id)
            )
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_yb2_phase_transitions_session "
            "ON yb2_phase_transitions(session_id, created_at)"
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS yb2_live_events (
                event_seq INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                public_metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES yb2_sessions(session_id)
            )
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_yb2_live_events_session_seq "
            "ON yb2_live_events(session_id, event_seq)"
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS yb2_interactions (
                interaction_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                phase TEXT NOT NULL,
                speaker_id TEXT NOT NULL DEFAULT '',
                public_content_summary_json TEXT NOT NULL DEFAULT '{}',
                correlation_id TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES yb2_sessions(session_id)
            )
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_yb2_interactions_session "
            "ON yb2_interactions(session_id, created_at)"
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS yb2_finalizations (
                finalization_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                closing_completion_status TEXT NOT NULL,
                completed_at TEXT NOT NULL,
                display_summary_json TEXT NOT NULL DEFAULT '{}',
                error_summary_json TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY (session_id) REFERENCES yb2_sessions(session_id)
            )
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_yb2_finalizations_session "
            "ON yb2_finalizations(session_id, completed_at)"
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS yb2_command_results (
                command_id TEXT PRIMARY KEY,
                result_json TEXT NOT NULL,
                saved_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
        return conn

    def create_v2_session(self, record: dict[str, object]) -> dict[str, object]:
        safe_record = _sanitize_public_value(record)
        now = _now_iso()
        session_id = str(safe_record["session_id"])
        duplicate = False
        with closing(self._init_youtube_bridge_v2_db()) as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    """
                    INSERT INTO yb2_sessions (
                        session_id, current_phase, session_started_at, plan_completed,
                        aftertalk_policy, duration_policy_json, manual_close_requested,
                        closing_completed, public_summary_json, metadata_json, plan_id,
                        manual_close_json, ended_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        _string_value(safe_record.get("current_phase", "planned_show")),
                        _datetime_text(safe_record.get("session_started_at")),
                        _bool_int(safe_record.get("plan_completed", False)),
                        _string_value(safe_record.get("aftertalk_policy", "auto")),
                        _json_text(safe_record.get("duration_policy", {})),
                        _bool_int(safe_record.get("manual_close_requested", False)),
                        _bool_int(safe_record.get("closing_completed", False)),
                        _json_text(safe_record.get("public_summary", {})),
                        _json_text(safe_record.get("metadata", {})),
                        _optional_string(safe_record.get("plan_id")),
                        _json_text(safe_record.get("manual_close", {})),
                        _optional_datetime_text(safe_record.get("ended_at")),
                        now,
                        now,
                    ),
                )
                conn.commit()
            except sqlite3.IntegrityError:
                conn.rollback()
                duplicate = True
        if duplicate:
            existing = self.get_v2_session(session_id)
            if existing is not None:
                return existing
            raise KeyError(session_id)
        return self.get_v2_session(session_id)

    def get_v2_session(self, session_id: str) -> dict[str, object] | None:
        with closing(self._init_youtube_bridge_v2_db()) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT session_id, current_phase, session_started_at, plan_completed,
                       aftertalk_policy, duration_policy_json, manual_close_requested,
                       closing_completed, public_summary_json, metadata_json, plan_id,
                       manual_close_json, ended_at, created_at, updated_at
                FROM yb2_sessions
                WHERE session_id = ?
                """,
                (session_id,),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return _session_from_row(row)

    def list_v2_sessions_for_recovery(self, limit: int = 100) -> list[dict[str, object]]:
        safe_limit = max(1, min(int(limit), 500))
        with closing(self._init_youtube_bridge_v2_db()) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT session_id, current_phase, session_started_at, plan_completed,
                       aftertalk_policy, duration_policy_json, manual_close_requested,
                       closing_completed, public_summary_json, metadata_json, plan_id,
                       manual_close_json, ended_at, created_at, updated_at
                FROM yb2_sessions
                WHERE current_phase != 'ended'
                ORDER BY updated_at ASC, created_at ASC
                LIMIT ?
                """,
                (safe_limit,),
            )
            rows = cursor.fetchall()
        return [_session_from_row(row) for row in rows]

    def update_v2_session(self, session_id: str, patch: dict[str, object]) -> dict[str, object]:
        current = self.get_v2_session(session_id)
        if current is None:
            raise KeyError(session_id)

        safe_patch = _sanitize_public_value(patch)
        metadata = dict(current.get("metadata", {}))
        known_columns: dict[str, object] = {}

        for key, value in safe_patch.items():
            if key in {
                "current_phase",
                "session_started_at",
                "plan_completed",
                "aftertalk_policy",
                "duration_policy",
                "manual_close_requested",
                "closing_completed",
                "public_summary",
                "plan_id",
                "manual_close",
                "ended_at",
            }:
                known_columns[key] = value
            else:
                metadata[key] = value

        values = {
            "current_phase": _string_value(known_columns.get("current_phase", current["current_phase"])),
            "session_started_at": _datetime_text(
                known_columns.get("session_started_at", current["session_started_at"])
            ),
            "plan_completed": _bool_int(known_columns.get("plan_completed", current["plan_completed"])),
            "aftertalk_policy": _string_value(
                known_columns.get("aftertalk_policy", current["aftertalk_policy"])
            ),
            "duration_policy_json": _json_text(
                known_columns.get("duration_policy", current["duration_policy"])
            ),
            "manual_close_requested": _bool_int(
                known_columns.get("manual_close_requested", current["manual_close_requested"])
            ),
            "closing_completed": _bool_int(
                known_columns.get("closing_completed", current["closing_completed"])
            ),
            "public_summary_json": _json_text(
                known_columns.get("public_summary", current.get("public_summary", {}))
            ),
            "metadata_json": _json_text(metadata),
            "plan_id": _optional_string(known_columns.get("plan_id", current.get("plan_id"))),
            "manual_close_json": _json_text(
                known_columns.get("manual_close", current.get("manual_close", {}))
            ),
            "ended_at": _optional_datetime_text(known_columns.get("ended_at", current.get("ended_at"))),
            "updated_at": _now_iso(),
        }
        with closing(self._init_youtube_bridge_v2_db()) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE yb2_sessions
                SET current_phase = ?, session_started_at = ?, plan_completed = ?,
                    aftertalk_policy = ?, duration_policy_json = ?,
                    manual_close_requested = ?, closing_completed = ?,
                    public_summary_json = ?, metadata_json = ?, plan_id = ?,
                    manual_close_json = ?, ended_at = ?, updated_at = ?
                WHERE session_id = ?
                """,
                (
                    values["current_phase"],
                    values["session_started_at"],
                    values["plan_completed"],
                    values["aftertalk_policy"],
                    values["duration_policy_json"],
                    values["manual_close_requested"],
                    values["closing_completed"],
                    values["public_summary_json"],
                    values["metadata_json"],
                    values["plan_id"],
                    values["manual_close_json"],
                    values["ended_at"],
                    values["updated_at"],
                    session_id,
                ),
            )
            conn.commit()
        return self.get_v2_session(session_id)

    def get_v2_phase_transition(self, transition_id: str) -> dict[str, object] | None:
        with closing(self._init_youtube_bridge_v2_db()) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT transition_id, session_id, previous_phase, next_phase, reason,
                       metadata_json, created_at
                FROM yb2_phase_transitions
                WHERE transition_id = ?
                """,
                (transition_id,),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return _transition_from_row(row)

    def append_v2_phase_transition(
        self,
        session_id: str,
        record: dict[str, object],
    ) -> dict[str, object]:
        safe_record = _sanitize_public_value(record)
        transition_id = str(safe_record["transition_id"])
        existing = self.get_v2_phase_transition(transition_id)
        if existing is not None:
            return existing

        with closing(self._init_youtube_bridge_v2_db()) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR IGNORE INTO yb2_phase_transitions (
                    transition_id, session_id, previous_phase, next_phase, reason,
                    metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    transition_id,
                    session_id,
                    _string_value(safe_record.get("previous_phase", "")),
                    _string_value(safe_record.get("next_phase", "")),
                    _string_value(safe_record.get("reason", "")),
                    _json_text(safe_record.get("metadata", {})),
                    _datetime_text(safe_record.get("created_at")),
                ),
            )
            conn.commit()
        stored = self.get_v2_phase_transition(transition_id)
        if stored is None:
            raise KeyError(transition_id)
        return stored

    def append_v2_live_event(
        self,
        session_id: str,
        record: dict[str, object],
    ) -> dict[str, object]:
        safe_record = _sanitize_public_value(record)
        event_id = str(safe_record.get("event_id") or f"{session_id}:event:{_now_iso()}")
        with closing(self._init_youtube_bridge_v2_db()) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO yb2_live_events (
                    event_id, session_id, event_type, public_metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    session_id,
                    str(safe_record.get("event_type", "")),
                    _json_text(safe_record.get("public_metadata", safe_record.get("public_payload", {}))),
                    _datetime_text(safe_record.get("created_at")),
                ),
            )
            event_seq = cursor.lastrowid
            conn.commit()
            cursor.execute(
                """
                SELECT event_seq, event_id, session_id, event_type, public_metadata_json, created_at
                FROM yb2_live_events
                WHERE event_seq = ?
                """,
                (event_seq,),
            )
            row = cursor.fetchone()
        return _event_from_row(row)

    def list_v2_live_events(self, session_id: str, limit: int = 100) -> list[dict[str, object]]:
        safe_limit = max(1, min(int(limit), 500))
        with closing(self._init_youtube_bridge_v2_db()) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT event_seq, event_id, session_id, event_type, public_metadata_json, created_at
                FROM (
                    SELECT event_seq, event_id, session_id, event_type, public_metadata_json, created_at
                    FROM yb2_live_events
                    WHERE session_id = ?
                    ORDER BY event_seq DESC
                    LIMIT ?
                )
                ORDER BY event_seq ASC
                """,
                (session_id, safe_limit),
            )
            rows = cursor.fetchall()
        return [_event_from_row(row) for row in rows]

    def append_v2_interaction(
        self,
        session_id: str,
        record: dict[str, object],
    ) -> dict[str, object]:
        safe_record = _sanitize_public_value(record)
        interaction_id = str(safe_record["interaction_id"])
        with closing(self._init_youtube_bridge_v2_db()) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO yb2_interactions (
                    interaction_id, session_id, phase, speaker_id,
                    public_content_summary_json, correlation_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    interaction_id,
                    session_id,
                    _string_value(safe_record.get("phase", "")),
                    str(safe_record.get("speaker_id", "")),
                    _json_text(safe_record.get("public_content_summary", {})),
                    str(safe_record.get("correlation_id", "")),
                    _datetime_text(safe_record.get("created_at")),
                ),
            )
            conn.commit()
            cursor.execute(
                """
                SELECT interaction_id, session_id, phase, speaker_id,
                       public_content_summary_json, correlation_id, created_at
                FROM yb2_interactions
                WHERE interaction_id = ?
                """,
                (interaction_id,),
            )
            row = cursor.fetchone()
        return _interaction_from_row(row)

    def append_v2_finalization(
        self,
        session_id: str,
        record: dict[str, object],
    ) -> dict[str, object]:
        safe_record = _sanitize_public_value(record)
        finalization_id = str(safe_record["finalization_id"])
        completed_at = _datetime_text(safe_record.get("completed_at"))
        with closing(self._init_youtube_bridge_v2_db()) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO yb2_finalizations (
                    finalization_id, session_id, closing_completion_status, completed_at,
                    display_summary_json, error_summary_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    finalization_id,
                    session_id,
                    _string_value(safe_record.get("closing_completion_status", "")),
                    completed_at,
                    _json_text(safe_record.get("display_summary", {})),
                    _json_text(safe_record.get("error_summary", {})),
                ),
            )
            cursor.execute(
                """
                UPDATE yb2_sessions
                SET closing_completed = 1, updated_at = ?
                WHERE session_id = ?
                """,
                (_now_iso(), session_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(session_id)
            conn.commit()
            cursor.execute(
                """
                SELECT finalization_id, session_id, closing_completion_status, completed_at,
                       display_summary_json, error_summary_json
                FROM yb2_finalizations
                WHERE finalization_id = ?
                """,
                (finalization_id,),
            )
            row = cursor.fetchone()
        return _finalization_from_row(row)

    def get_v2_command_result(self, command_id: str) -> dict[str, object] | None:
        with closing(self._init_youtube_bridge_v2_db()) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT result_json FROM yb2_command_results WHERE command_id = ?",
                (command_id,),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return _json_value(row[0])

    def save_v2_command_result(self, command_id: str, result: object) -> None:
        safe_result = _sanitize_public_value(_json_safe(result))
        with closing(self._init_youtube_bridge_v2_db()) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO yb2_command_results (command_id, result_json, saved_at)
                VALUES (?, ?, ?)
                """,
                (command_id, _json_text(safe_result), _now_iso()),
            )
            conn.commit()


_PRIVATE_KEYS = {
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


def _session_from_row(row: tuple[object, ...]) -> dict[str, object]:
    return {
        "session_id": row[0],
        "current_phase": row[1],
        "session_started_at": _datetime_value(row[2]),
        "plan_completed": bool(row[3]),
        "aftertalk_policy": row[4],
        "duration_policy": _json_value(row[5]),
        "manual_close_requested": bool(row[6]),
        "closing_completed": bool(row[7]),
        "public_summary": _json_value(row[8]),
        "metadata": _json_value(row[9]),
        "plan_id": row[10],
        "manual_close": _json_value(row[11]),
        "ended_at": _optional_datetime_value(row[12]),
        "created_at": _datetime_value(row[13]),
        "updated_at": _datetime_value(row[14]),
    }


def _transition_from_row(row: tuple[object, ...]) -> dict[str, object]:
    return {
        "transition_id": row[0],
        "session_id": row[1],
        "previous_phase": row[2],
        "next_phase": row[3],
        "reason": row[4],
        "metadata": _json_value(row[5]),
        "created_at": _datetime_value(row[6]),
    }


def _event_from_row(row: tuple[object, ...]) -> dict[str, object]:
    return {
        "event_seq": row[0],
        "event_id": row[1],
        "session_id": row[2],
        "event_type": row[3],
        "public_metadata": _json_value(row[4]),
        "created_at": _datetime_value(row[5]),
    }


def _interaction_from_row(row: tuple[object, ...]) -> dict[str, object]:
    return {
        "interaction_id": row[0],
        "session_id": row[1],
        "phase": row[2],
        "speaker_id": row[3],
        "public_content_summary": _json_value(row[4]),
        "correlation_id": row[5],
        "created_at": _datetime_value(row[6]),
    }


def _finalization_from_row(row: tuple[object, ...]) -> dict[str, object]:
    return {
        "finalization_id": row[0],
        "session_id": row[1],
        "closing_completion_status": row[2],
        "completed_at": _datetime_value(row[3]),
        "display_summary": _json_value(row[4]),
        "error_summary": _json_value(row[5]),
    }


def _json_text(value: object) -> str:
    return json.dumps(_sanitize_public_value(_json_safe(value)), ensure_ascii=False, sort_keys=True)


def _json_value(value: object) -> object:
    if not value:
        return {}
    if isinstance(value, str):
        return json.loads(value)
    return value


def _json_safe(value: object) -> object:
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(inner_value) for key, inner_value in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    return value


def _sanitize_public_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _sanitize_public_value(inner_value)
            for key, inner_value in value.items()
            if str(key).lower() not in _PRIVATE_KEYS
        }
    if isinstance(value, list):
        return [_sanitize_public_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_public_value(item) for item in value)
    return value


def _string_value(value: object) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return _string_value(value)


def _bool_int(value: object) -> int:
    return 1 if bool(value) else 0


def _datetime_text(value: object) -> str:
    if value is None:
        return _now_iso()
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _optional_datetime_text(value: object) -> str | None:
    if value is None:
        return None
    return _datetime_text(value)


def _datetime_value(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _optional_datetime_value(value: object) -> datetime | None:
    if value is None:
        return None
    return _datetime_value(value)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
