from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta
from typing import Any

from storage_constants import DEFAULT_CONNECTOR_ID, DEFAULT_CONNECTOR_NAME
from storage_event_utils import infer_super_chat_tier


class DirectorStateRepositoryMixin:
    def get_director_state(self, session_id: str) -> dict:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM live_director_state WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return self._row_to_director_state(row, session_id)

    def update_director_state(self, session_id: str, **fields) -> dict:
        current = self.get_director_state(session_id)
        now = datetime.now().isoformat()
        merged_metadata = dict(current.get("metadata") or {})
        if isinstance(fields.get("metadata"), dict):
            merged_metadata.update(fields["metadata"])
        next_state = {
            "director_enabled": bool(fields.get("director_enabled", current.get("director_enabled", False))),
            "idle_seconds": int(fields.get("idle_seconds", current.get("idle_seconds", 60)) or 60),
            "last_director_action_at": str(fields.get("last_director_action_at", current.get("last_director_action_at", "")) or ""),
            "current_topic": str(fields.get("current_topic", current.get("current_topic", "")) or ""),
            "consecutive_ai_turns": int(fields.get("consecutive_ai_turns", current.get("consecutive_ai_turns", 0)) or 0),
            "last_seen_event_id": int(fields.get("last_seen_event_id", current.get("last_seen_event_id", 0)) or 0),
            "status": str(fields.get("status", current.get("status", "stopped")) or "stopped"),
            "metadata": merged_metadata,
        }
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO live_director_state (
                    session_id, director_enabled, idle_seconds, last_director_action_at,
                    current_topic, consecutive_ai_turns, last_seen_event_id,
                    status, updated_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    director_enabled=excluded.director_enabled,
                    idle_seconds=excluded.idle_seconds,
                    last_director_action_at=excluded.last_director_action_at,
                    current_topic=excluded.current_topic,
                    consecutive_ai_turns=excluded.consecutive_ai_turns,
                    last_seen_event_id=excluded.last_seen_event_id,
                    status=excluded.status,
                    updated_at=excluded.updated_at,
                    metadata_json=excluded.metadata_json
                """,
                (
                    session_id,
                    1 if next_state["director_enabled"] else 0,
                    next_state["idle_seconds"],
                    next_state["last_director_action_at"],
                    next_state["current_topic"],
                    next_state["consecutive_ai_turns"],
                    next_state["last_seen_event_id"],
                    next_state["status"],
                    now,
                    self._json_dump(next_state["metadata"]),
                ),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM live_director_state WHERE session_id = ?", (session_id,)).fetchone()
        return self._row_to_director_state(row, session_id)

