from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta
from typing import Any

from storage_constants import DEFAULT_CONNECTOR_ID, DEFAULT_CONNECTOR_NAME
from storage_event_utils import infer_super_chat_tier


class SummaryRepositoryMixin:
    def update_session_summary_state(
        self,
        session_id: str,
        *,
        summary_status: str,
        summary_id: int | None = None,
        summary_error: str = "",
        finalized_at: str | None = None,
    ) -> dict | None:
        session = self.get_session(session_id)
        if not session:
            return None
        now = datetime.now().isoformat()
        fields = {
            "summary_status": summary_status,
            "summary_error": summary_error[:1000],
            "summary_updated_at": now,
        }
        if summary_id is not None:
            fields["summary_id"] = int(summary_id)
        if finalized_at is not None:
            fields["finalized_at"] = finalized_at
        session.update(fields)
        return self.upsert_session(session)

    def create_summary(self, session_id: str, data: dict) -> dict:
        session = self.get_session(session_id)
        if not session:
            raise ValueError("live session 不存在")
        now = datetime.now().isoformat()
        character_ids = data.get("character_ids")
        if not isinstance(character_ids, list):
            character_ids = session.get("character_ids", [])
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO youtube_live_summaries (
                    session_id, connector_id, video_id, live_chat_id, character_ids_json,
                    title, summary_text, topic_tags_json, key_points_json, qa_pairs_json,
                    audience_mood, memory_text, event_count, source_started_at, source_ended_at,
                    status, created_at, updated_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    session["connector_id"],
                    session.get("video_id", ""),
                    session.get("live_chat_id", ""),
                    self._json_dump(character_ids),
                    str(data.get("title", "") or ""),
                    str(data.get("summary_text", "") or ""),
                    self._json_dump(data.get("topic_tags") if isinstance(data.get("topic_tags"), list) else []),
                    self._json_dump(data.get("key_points") if isinstance(data.get("key_points"), list) else []),
                    self._json_dump(data.get("qa_pairs") if isinstance(data.get("qa_pairs"), list) else []),
                    str(data.get("audience_mood", "") or ""),
                    str(data.get("memory_text", "") or ""),
                    int(data.get("event_count", 0) or 0),
                    str(data.get("source_started_at", "") or ""),
                    str(data.get("source_ended_at", "") or ""),
                    str(data.get("status", "completed") or "completed"),
                    now,
                    now,
                    self._json_dump(data.get("metadata") if isinstance(data.get("metadata"), dict) else {}),
                ),
            )
            summary_id = int(cursor.lastrowid)
            conn.commit()
            row = conn.execute("SELECT * FROM youtube_live_summaries WHERE id = ?", (summary_id,)).fetchone()
        summary = self._row_to_summary(row)
        if not summary:
            raise RuntimeError("直播摘要儲存失敗")
        return summary

    def get_summary(self, summary_id: int) -> dict | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM youtube_live_summaries WHERE id = ?", (int(summary_id),)).fetchone()
        return self._row_to_summary(row)

    def get_session_summary(self, session_id: str) -> dict | None:
        session = self.get_session(session_id)
        if not session:
            return None
        summary_id = session.get("summary_id")
        if summary_id:
            summary = self.get_summary(int(summary_id))
            if summary:
                return summary
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM youtube_live_summaries
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (session_id,),
            ).fetchone()
        return self._row_to_summary(row)

    def get_session_summary_by_phase(self, session_id: str, summary_phase: str) -> dict | None:
        summaries = self.list_session_summaries_by_phase(
            session_id,
            summary_phase=summary_phase,
            limit=1,
        )
        return summaries[0] if summaries else None

    def list_session_summaries_by_phase(
        self,
        session_id: str,
        *,
        summary_phase: str,
        limit: int = 20,
    ) -> list[dict]:
        limit = max(1, min(int(limit or 20), 100))
        phase = str(summary_phase or "").strip()
        if not phase:
            return []
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM youtube_live_summaries
                WHERE session_id = ?
                ORDER BY id DESC
                """,
                (session_id,),
            ).fetchall()
        summaries = [summary for row in rows if (summary := self._row_to_summary(row))]
        return [
            summary for summary in summaries
            if (summary.get("metadata") or {}).get("summary_phase") == phase
        ][:limit]

    def list_summaries(self, *, session_id: str | None = None, limit: int = 100) -> list[dict]:
        limit = max(1, min(int(limit or 100), 500))
        params: list[Any] = []
        where = ""
        if session_id:
            where = "WHERE session_id = ?"
            params.append(session_id)
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM youtube_live_summaries {where} ORDER BY id DESC LIMIT ?",
                params + [limit],
            ).fetchall()
        return [summary for row in rows if (summary := self._row_to_summary(row))]

    def update_summary_metadata(
        self,
        summary_id: int,
        *,
        metadata: dict[str, Any],
        status: str | None = None,
    ) -> dict | None:
        current = self.get_summary(int(summary_id))
        if not current:
            return None
        merged = dict(current.get("metadata") or {})
        merged.update(metadata)
        fields = ["metadata_json = ?", "updated_at = ?"]
        now = datetime.now().isoformat()
        params: list[Any] = [self._json_dump(merged), now]
        if status:
            fields.append("status = ?")
            params.append(status)
        params.append(int(summary_id))
        with self._lock, self._connect() as conn:
            conn.execute(
                f"UPDATE youtube_live_summaries SET {', '.join(fields)} WHERE id = ?",
                params,
            )
            conn.commit()
            row = conn.execute("SELECT * FROM youtube_live_summaries WHERE id = ?", (int(summary_id),)).fetchone()
        return self._row_to_summary(row)

    def cleanup_events(self, *, session_id: str | None = None, retention_days: int = 30) -> int:
        cutoff = (datetime.now() - timedelta(days=max(1, int(retention_days or 30)))).isoformat()
        where = "received_at < ?"
        params: list[Any] = [cutoff]
        if session_id:
            where += " AND bridge_session_id = ?"
            params.append(session_id)
        with self._lock, self._connect() as conn:
            cursor = conn.execute(f"DELETE FROM live_events WHERE {where}", params)
            conn.commit()
            return int(cursor.rowcount or 0)

