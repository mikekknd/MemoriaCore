from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta
from typing import Any

from storage_constants import DEFAULT_CONNECTOR_ID, DEFAULT_CONNECTOR_NAME
from storage_event_utils import infer_super_chat_tier


class InteractionRepositoryMixin:
    def create_interaction(self, data: dict[str, Any]) -> dict:
        now = datetime.now().isoformat()
        job_id = str(data.get("job_id") or uuid.uuid4())
        session_id = str(data.get("session_id", "") or "")
        if not session_id:
            raise ValueError("session_id 不可為空")
        event_ids = data.get("event_ids") if isinstance(data.get("event_ids"), list) else []
        character_ids = data.get("character_ids") if isinstance(data.get("character_ids"), list) else []
        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO live_interactions (
                    job_id, session_id, source, priority, status, reason,
                    event_ids_json, memoria_session_id, character_ids_json,
                    content, reply_text, closure_text, created_at, started_at,
                    completed_at, interrupted_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    session_id,
                    str(data.get("source", "youtube_injection") or "youtube_injection"),
                    int(data.get("priority", 100) or 100),
                    str(data.get("status", "queued") or "queued"),
                    str(data.get("reason", "") or ""),
                    self._json_dump(event_ids),
                    str(data.get("memoria_session_id", "") or ""),
                    self._json_dump([str(v).strip() for v in character_ids if str(v).strip()]),
                    str(data.get("content", "") or ""),
                    str(data.get("reply_text", "") or ""),
                    str(data.get("closure_text", "") or ""),
                    now,
                    str(data.get("started_at", "") or ""),
                    str(data.get("completed_at", "") or ""),
                    str(data.get("interrupted_at", "") or ""),
                    self._json_dump(metadata),
                ),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM live_interactions WHERE job_id = ?", (job_id,)).fetchone()
        interaction = self._row_to_interaction(row)
        if not interaction:
            raise RuntimeError("interaction 建立失敗")
        return interaction

    def update_interaction(self, job_id: str, **fields) -> dict | None:
        current = self.get_interaction(job_id)
        if not current:
            return None
        allowed = {
            "source", "priority", "status", "reason", "event_ids", "memoria_session_id",
            "character_ids", "content", "reply_text", "closure_text", "started_at",
            "completed_at", "interrupted_at",
        }
        updates: dict[str, Any] = {}
        for key, value in fields.items():
            if key in allowed:
                updates[key] = value
        metadata_patch = fields.get("metadata")
        if isinstance(metadata_patch, dict):
            merged = dict(current.get("metadata") or {})
            merged.update(metadata_patch)
            updates["metadata"] = merged
        if not updates:
            return current

        columns: list[str] = []
        params: list[Any] = []
        column_map = {
            "event_ids": "event_ids_json",
            "character_ids": "character_ids_json",
            "metadata": "metadata_json",
        }
        for key, value in updates.items():
            column = column_map.get(key, key)
            if key in {"event_ids", "character_ids", "metadata"}:
                value = self._json_dump(value)
            columns.append(f"{column} = ?")
            params.append(value)
        params.append(job_id)
        with self._lock, self._connect() as conn:
            conn.execute(
                f"UPDATE live_interactions SET {', '.join(columns)} WHERE job_id = ?",
                params,
            )
            conn.commit()
            row = conn.execute("SELECT * FROM live_interactions WHERE job_id = ?", (job_id,)).fetchone()
        return self._row_to_interaction(row)

    def append_interaction_visible_message(
        self,
        job_id: str,
        visible_message: dict[str, Any],
        *,
        limit: int = 20,
    ) -> dict | None:
        content = str((visible_message or {}).get("content") or "").strip()
        if not job_id or not content:
            return self.get_interaction(job_id)
        limit = max(1, min(int(limit or 20), 100))
        normalized = {
            "message_id": visible_message.get("message_id"),
            "role": visible_message.get("role") or "assistant",
            "content": content,
            "created_at": visible_message.get("created_at") or visible_message.get("timestamp") or "",
            "timestamp": visible_message.get("timestamp") or visible_message.get("created_at") or "",
            "character_id": visible_message.get("character_id"),
            "character_name": visible_message.get("character_name"),
            "source": visible_message.get("source") or "",
        }

        def message_key(item: dict[str, Any]) -> str:
            raw_id = str(item.get("message_id") or "")
            if raw_id:
                return f"id:{raw_id}"
            return (
                f"text:{item.get('timestamp') or item.get('created_at') or ''}:"
                f"{str(item.get('content') or '')[:120]}"
            )

        normalized_key = message_key(normalized)
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM live_interactions WHERE job_id = ?", (job_id,)).fetchone()
            if row is None:
                return None
            metadata = self._json_load(row["metadata_json"], {})
            if not isinstance(metadata, dict):
                metadata = {}
            visible_messages = [
                item
                for item in metadata.get("visible_messages", [])
                if isinstance(item, dict)
            ]
            if all(message_key(item) != normalized_key for item in visible_messages):
                visible_messages.append(normalized)
            metadata.update({
                "visible_messages": visible_messages[-limit:],
                "last_visible_message": normalized,
                "has_visible_output": True,
            })
            conn.execute(
                "UPDATE live_interactions SET metadata_json = ? WHERE job_id = ?",
                (self._json_dump(metadata), job_id),
            )
            conn.commit()
            updated = conn.execute("SELECT * FROM live_interactions WHERE job_id = ?", (job_id,)).fetchone()
        return self._row_to_interaction(updated)

    def get_interaction(self, job_id: str) -> dict | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM live_interactions WHERE job_id = ?", (job_id,)).fetchone()
        return self._row_to_interaction(row)

    def list_interactions(self, session_id: str, *, limit: int = 100) -> list[dict]:
        limit = max(1, min(int(limit or 100), 500))
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM live_interactions
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()
        return [interaction for row in rows if (interaction := self._row_to_interaction(row))]

    def _finalize_duplicate_running_rows(
        self,
        conn: sqlite3.Connection,
        rows: list[sqlite3.Row],
    ) -> sqlite3.Row | None:
        if not rows:
            return None
        keep = sorted(rows, key=lambda row: (-int(row["priority"] or 0), int(row["id"] or 0)))[0]
        duplicates = [row for row in rows if row["job_id"] != keep["job_id"]]
        if not duplicates:
            return keep
        now = datetime.now().isoformat()
        for row in duplicates:
            metadata = self._json_load(row["metadata_json"], {})
            if not isinstance(metadata, dict):
                metadata = {}
            metadata.update({"duplicate_running_finalized": True})
            conn.execute(
                """
                UPDATE live_interactions
                SET status = 'interrupted',
                    reason = COALESCE(NULLIF(reason, ''), 'duplicate_running_finalized'),
                    completed_at = ?,
                    interrupted_at = COALESCE(NULLIF(interrupted_at, ''), ?),
                    metadata_json = ?
                WHERE job_id = ?
                  AND status = 'running'
                """,
                (now, now, self._json_dump(metadata), row["job_id"]),
            )
        conn.commit()
        return keep

    def claim_interaction(self, job_id: str) -> dict | None:
        """把指定 queued interaction 原子切換成 running。

        若同 session 已有 running job，回傳 None，呼叫端應等待或中斷後重試。
        """
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            target = conn.execute(
                "SELECT * FROM live_interactions WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if not target:
                return None
            if target["status"] in {"running", "presenting"}:
                return self._row_to_interaction(target)
            if target["status"] != "queued":
                return None
            running_rows = conn.execute(
                """
                SELECT * FROM live_interactions
                WHERE session_id = ?
                  AND status IN ('running', 'presenting')
                ORDER BY priority DESC, id ASC
                """,
                (target["session_id"],),
            ).fetchall()
            self._finalize_duplicate_running_rows(conn, running_rows)
            active_count = conn.execute(
                """
                SELECT COUNT(*) AS count FROM live_interactions
                WHERE session_id = ?
                  AND status IN ('running', 'presenting', 'prefetching', 'prefetched')
                """,
                (target["session_id"],),
            ).fetchone()
            if active_count and int(active_count["count"] or 0) > 0:
                return None
            conn.execute(
                """
                UPDATE live_interactions
                SET status = 'running',
                    started_at = COALESCE(NULLIF(started_at, ''), ?)
                WHERE job_id = ?
                  AND status = 'queued'
                """,
                (now, job_id),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM live_interactions WHERE job_id = ?", (job_id,)).fetchone()
        return self._row_to_interaction(row)

    def claim_next_interaction(self, session_id: str) -> dict | None:
        """依 priority 取下一筆 queued job；同 session 同時只允許一筆 running。"""
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            running_rows = conn.execute(
                """
                SELECT * FROM live_interactions
                WHERE session_id = ?
                  AND status IN ('running', 'presenting')
                ORDER BY priority DESC, id ASC
                """,
                (session_id,),
            ).fetchall()
            kept = self._finalize_duplicate_running_rows(conn, running_rows)
            if kept:
                return None
            active_count = conn.execute(
                """
                SELECT COUNT(*) AS count FROM live_interactions
                WHERE session_id = ?
                  AND status IN ('prefetching', 'prefetched')
                """,
                (session_id,),
            ).fetchone()
            if active_count and int(active_count["count"] or 0) > 0:
                return None
            row = conn.execute(
                """
                SELECT * FROM live_interactions
                WHERE session_id = ?
                  AND status = 'queued'
                ORDER BY priority DESC, id ASC
                LIMIT 1
                """,
                (session_id,),
            ).fetchone()
            if not row:
                return None
            conn.execute(
                """
                UPDATE live_interactions
                SET status = 'running',
                    started_at = COALESCE(NULLIF(started_at, ''), ?)
                WHERE job_id = ?
                  AND status = 'queued'
                """,
                (now, row["job_id"]),
            )
            conn.commit()
            claimed = conn.execute(
                "SELECT * FROM live_interactions WHERE job_id = ?",
                (row["job_id"],),
            ).fetchone()
        return self._row_to_interaction(claimed)

    def get_active_interaction(self, session_id: str) -> dict | None:
        now = datetime.now()
        stale_job_ids: list[str] = []
        active_interaction: dict | None = None
        with self._lock, self._connect() as conn:
            running_rows = conn.execute(
                """
                SELECT * FROM live_interactions
                WHERE session_id = ?
                  AND status IN ('running', 'presenting')
                ORDER BY priority DESC, id ASC
                """,
                (session_id,),
            ).fetchall()
            self._finalize_duplicate_running_rows(conn, running_rows)
            rows = conn.execute(
                """
                SELECT * FROM live_interactions
                WHERE session_id = ?
                  AND status IN ('queued', 'running', 'presenting', 'prefetching', 'prefetched', 'interrupt_requested')
                ORDER BY priority DESC, id ASC
                """,
                (session_id,),
            ).fetchall()
            for row in rows:
                interaction = self._row_to_interaction(row)
                if not interaction:
                    continue
                if interaction["status"] != "interrupt_requested":
                    active_interaction = interaction
                    break
                interrupted_at = self._parse_iso(row["interrupted_at"])
                if not interrupted_at or now - interrupted_at <= timedelta(seconds=15):
                    active_interaction = interaction
                    break
                stale_job_ids.append(interaction["job_id"])

            if stale_job_ids:
                placeholders = ",".join("?" * len(stale_job_ids))
                completed_at = now.isoformat()
                for row in rows:
                    if row["job_id"] not in stale_job_ids:
                        continue
                    metadata = self._json_load(row["metadata_json"], {})
                    if isinstance(metadata, dict):
                        metadata.update({"stale_interrupt_finalized": True})
                    else:
                        metadata = {"stale_interrupt_finalized": True}
                    conn.execute(
                        f"""
                        UPDATE live_interactions
                        SET status = 'interrupted',
                            completed_at = ?,
                            metadata_json = ?
                        WHERE job_id = ?
                          AND status = 'interrupt_requested'
                        """,
                        (completed_at, self._json_dump(metadata), row["job_id"]),
                    )
                conn.commit()
        return active_interaction

    @staticmethod
    def _parse_iso(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value))
        except ValueError:
            return None

    def finalize_incomplete_interactions(
        self,
        session_id: str,
        *,
        status: str = "interrupted",
        reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> list[dict]:
        now = datetime.now().isoformat()
        metadata_patch = metadata or {}
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM live_interactions
                WHERE session_id = ?
                  AND status IN (
                      'queued',
                      'running',
                      'presenting',
                      'prefetching',
                      'prefetched',
                      'interrupt_requested'
                  )
                ORDER BY id ASC
                """,
                (session_id,),
            ).fetchall()
            for row in rows:
                merged = self._json_load(row["metadata_json"], {})
                if not isinstance(merged, dict):
                    merged = {}
                merged.update(metadata_patch)
                conn.execute(
                    """
                    UPDATE live_interactions
                    SET status = ?,
                        reason = COALESCE(NULLIF(?, ''), reason),
                        completed_at = ?,
                        interrupted_at = COALESCE(NULLIF(interrupted_at, ''), ?),
                        metadata_json = ?
                    WHERE job_id = ?
                    """,
                    (
                        status,
                        reason[:500],
                        now,
                        now,
                        self._json_dump(merged),
                        row["job_id"],
                    ),
                )
            conn.commit()
            updated = conn.execute(
                """
                SELECT * FROM live_interactions
                WHERE session_id = ?
                  AND completed_at = ?
                ORDER BY id ASC
                """,
                (session_id, now),
            ).fetchall()
        return [interaction for row in updated if (interaction := self._row_to_interaction(row))]

    def request_interrupt(self, session_id: str, *, reason: str = "") -> list[dict]:
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM live_interactions
                WHERE session_id = ?
                  AND status IN ('queued', 'running')
                ORDER BY id ASC
                """,
                (session_id,),
            ).fetchall()
            job_ids = [row["job_id"] for row in rows]
            if job_ids:
                placeholders = ",".join("?" * len(job_ids))
                conn.execute(
                    f"""
                    UPDATE live_interactions
                    SET status = 'interrupt_requested',
                        reason = ?,
                        interrupted_at = ?
                    WHERE job_id IN ({placeholders})
                    """,
                    [reason[:500], now] + job_ids,
                )
                conn.commit()
            updated = conn.execute(
                """
                SELECT * FROM live_interactions
                WHERE session_id = ?
                  AND job_id IN ({})
                ORDER BY id ASC
                """.format(",".join("?" * len(job_ids)) if job_ids else "NULL"),
                [session_id] + job_ids if job_ids else [session_id],
            ).fetchall() if job_ids else []
        return [interaction for row in updated if (interaction := self._row_to_interaction(row))]

