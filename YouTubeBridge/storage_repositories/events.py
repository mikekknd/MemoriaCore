from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta
from typing import Any

from storage_constants import DEFAULT_CONNECTOR_ID, DEFAULT_CONNECTOR_NAME
from storage_event_utils import infer_super_chat_tier


class EventRepositoryMixin:
    def save_event(self, event: dict) -> dict | None:
        now = datetime.now().isoformat()
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        message_type = str(event.get("message_type", "") or "")
        amount_display = str(event.get("amount_display_string", "") or "")
        try:
            amount_micros = int(event.get("amount_micros", 0) or 0)
        except (TypeError, ValueError):
            amount_micros = 0
        try:
            explicit_tier = int(event.get("sc_tier", 0) or metadata.get("sc_tier", 0) or 0)
        except (TypeError, ValueError):
            explicit_tier = 0
        priority_class = str(event.get("priority_class", "") or "")
        if not priority_class:
            priority_class = "super_chat" if message_type == "superChatEvent" or amount_display or amount_micros > 0 else "normal"
        sc_tier = infer_super_chat_tier(amount_micros, explicit_tier) if priority_class == "super_chat" else 0
        safety_label = str(event.get("safety_label", "") or "").strip() or "unclassified"
        safety_status = str(event.get("safety_status", "") or "").strip() or (
            "completed" if safety_label == "clean" and event.get("safe_message_text") else "pending"
        )
        safe_message_text = str(event.get("safe_message_text", "") or "")
        safety_summary = str(event.get("safety_summary", "") or "")
        safety_reason = str(event.get("safety_reason", "") or "")
        try:
            safety_confidence = float(event.get("safety_confidence", 0) or 0)
        except (TypeError, ValueError):
            safety_confidence = 0.0
        safety_checked_at = str(event.get("safety_checked_at", "") or "")
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO live_events (
                    bridge_session_id, connector_id, video_id, live_chat_id, youtube_message_id,
                    message_type, author_channel_id, author_display_name, author_profile_image_url,
                    message_text, published_at, received_at, status, amount_display_string,
                    currency, amount_micros, sc_tier, priority_class, safety_label,
                    safety_status, safe_message_text, safety_summary, safety_reason,
                    safety_confidence, safety_checked_at, handled_in_closing_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(event.get("bridge_session_id", "") or ""),
                    str(event.get("connector_id", "") or ""),
                    str(event.get("video_id", "") or ""),
                    str(event.get("live_chat_id", "") or ""),
                    str(event.get("youtube_message_id", "") or ""),
                    message_type,
                    str(event.get("author_channel_id", "") or ""),
                    str(event.get("author_display_name", "") or ""),
                    str(event.get("author_profile_image_url", "") or ""),
                    str(event.get("message_text", "") or ""),
                    str(event.get("published_at", "") or ""),
                    str(event.get("received_at", "") or now),
                    str(event.get("status", "active") or "active"),
                    amount_display,
                    str(event.get("currency", "") or ""),
                    amount_micros,
                    sc_tier,
                    priority_class,
                    safety_label,
                    safety_status,
                    safe_message_text,
                    safety_summary,
                    safety_reason,
                    safety_confidence,
                    safety_checked_at,
                    str(event.get("handled_in_closing_at", "") or ""),
                    self._json_dump(metadata),
                ),
            )
            inserted = cursor.rowcount > 0
            row_id = cursor.lastrowid if inserted else None
            conn.commit()
            if not inserted:
                return None
            row = conn.execute("SELECT * FROM live_events WHERE id = ?", (row_id,)).fetchone()
        return self._row_to_event(row)

    def list_events(
        self,
        session_id: str,
        *,
        limit: int = 100,
        after_id: int | None = None,
        uninjected_only: bool = False,
    ) -> list[dict]:
        limit = max(1, min(int(limit or 100), 500))
        params: list[Any] = [session_id]
        where = "bridge_session_id = ?"
        order = "DESC"
        if uninjected_only:
            where += " AND (injected_at IS NULL OR injected_at = '')"
        if after_id is not None:
            where += " AND id > ?"
            params.append(int(after_id))
            order = "ASC"
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM live_events WHERE {where} ORDER BY id {order} LIMIT ?",
                params + [limit],
            ).fetchall()
        events = [self._row_to_event(row) for row in rows if row]
        if after_id is None:
            events.reverse()
        return [event for event in events if event]

    def get_events_by_ids(self, session_id: str, event_ids: list[int], *, limit: int = 100) -> list[dict]:
        ids: list[int] = []
        for raw in event_ids[:limit]:
            try:
                ids.append(int(raw))
            except (TypeError, ValueError):
                continue
        if not ids:
            return []
        placeholders = ",".join("?" * len(ids))
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM live_events WHERE bridge_session_id = ? AND id IN ({placeholders})",
                [session_id] + ids,
            ).fetchall()
        by_id = {int(row["id"]): self._row_to_event(row) for row in rows}
        return [by_id[event_id] for event_id in ids if by_id.get(event_id)]

    def list_summary_events(self, session_id: str, *, limit: int = 2000) -> list[dict]:
        limit = max(1, min(int(limit or 2000), 5000))
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM live_events
                WHERE bridge_session_id = ?
                  AND status = 'active'
                  AND TRIM(COALESCE(message_text, '')) != ''
                ORDER BY id ASC
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()
        return [event for row in rows if (event := self._row_to_event(row))]

    def list_events_pending_safety(self, session_id: str, *, limit: int = 100) -> list[dict]:
        limit = max(1, min(int(limit or 100), 500))
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM live_events
                WHERE bridge_session_id = ?
                  AND status = 'active'
                  AND TRIM(COALESCE(message_text, '')) != ''
                  AND COALESCE(safety_status, 'pending') IN ('pending', 'failed_retryable')
                ORDER BY
                  CASE WHEN priority_class = 'super_chat' THEN 0 ELSE 1 END,
                  sc_tier DESC,
                  id ASC
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()
        return [event for row in rows if (event := self._row_to_event(row))]

    def update_event_safety(
        self,
        event_id: int,
        *,
        status: str,
        label: str,
        safe_message_text: str,
        safety_summary: str = "",
        reason: str = "",
        confidence: float = 0.0,
    ) -> dict | None:
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE live_events
                SET safety_status = ?,
                    safety_label = ?,
                    safe_message_text = ?,
                    safety_summary = ?,
                    safety_reason = ?,
                    safety_confidence = ?,
                    safety_checked_at = ?
                WHERE id = ?
                """,
                (
                    str(status or "completed"),
                    str(label or "unclassified"),
                    str(safe_message_text or ""),
                    str(safety_summary or ""),
                    str(reason or ""),
                    float(confidence or 0.0),
                    now,
                    int(event_id),
                ),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM live_events WHERE id = ?", (int(event_id),)).fetchone()
        return self._row_to_event(row)

    def count_events(self, session_id: str, *, active_only: bool = False) -> int:
        where = "bridge_session_id = ?"
        if active_only:
            where += " AND status = 'active' AND TRIM(COALESCE(message_text, '')) != ''"
        with self._lock, self._connect() as conn:
            row = conn.execute(f"SELECT COUNT(*) AS count FROM live_events WHERE {where}", (session_id,)).fetchone()
        return int(row["count"] or 0) if row else 0

    def mark_events_injected(self, session_id: str, event_ids: list[int]) -> int:
        ids: list[int] = []
        for raw in event_ids:
            try:
                ids.append(int(raw))
            except (TypeError, ValueError):
                continue
        ids = list(dict.fromkeys(ids))
        if not ids:
            return 0
        placeholders = ",".join("?" * len(ids))
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                f"""
                UPDATE live_events
                SET injected_at = ?,
                    injection_count = COALESCE(injection_count, 0) + 1
                WHERE bridge_session_id = ?
                  AND id IN ({placeholders})
                """,
                [now, session_id] + ids,
            )
            conn.commit()
            return int(cursor.rowcount or 0)

    def list_super_chats(
        self,
        session_id: str,
        *,
        unhandled_only: bool = True,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        limit = max(1, min(int(limit or 100), 500))
        offset = max(0, int(offset or 0))
        where = "bridge_session_id = ? AND priority_class = 'super_chat'"
        if unhandled_only:
            where += " AND (handled_in_closing_at IS NULL OR handled_in_closing_at = '')"
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM live_events WHERE {where} ORDER BY sc_tier DESC, id ASC LIMIT ? OFFSET ?",
                (session_id, limit, offset),
            ).fetchall()
        return [event for row in rows if (event := self._row_to_event(row))]

    def mark_super_chats_handled_in_closing(self, session_id: str, event_ids: list[int]) -> int:
        ids: list[int] = []
        for raw in event_ids:
            try:
                ids.append(int(raw))
            except (TypeError, ValueError):
                continue
        ids = list(dict.fromkeys(ids))
        if not ids:
            return 0
        placeholders = ",".join("?" * len(ids))
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                f"""
                UPDATE live_events
                SET handled_in_closing_at = ?
                WHERE bridge_session_id = ?
                  AND priority_class = 'super_chat'
                  AND id IN ({placeholders})
                """,
                [now, session_id] + ids,
            )
            conn.commit()
            return int(cursor.rowcount or 0)

