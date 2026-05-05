from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta
from typing import Any

from storage_constants import DEFAULT_CONNECTOR_ID, DEFAULT_CONNECTOR_NAME
from storage_event_utils import infer_super_chat_tier


class SessionRepositoryMixin:
    @staticmethod
    def generate_session_id() -> str:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"yt_{stamp}_{uuid.uuid4().hex[:8]}"

    def upsert_session(self, config: dict) -> dict:
        now = datetime.now().isoformat()
        session_id = str(config.get("session_id", "")).strip() or self.generate_session_id()
        if not self.get_connector(str(config.get("connector_id", "") or "")):
            raise ValueError("connector_id 不存在")
        with self._lock, self._connect() as conn:
            existing = self.get_session(session_id)
            created_at = existing["created_at"] if existing else now
            video_id = str(config.get("video_id", "") or "")
            live_chat_id = str(config.get("live_chat_id", "") or "")
            source_changed = bool(
                existing
                and (
                    video_id != str(existing.get("video_id", "") or "")
                    or live_chat_id != str(existing.get("live_chat_id", "") or "")
                )
            )
            if source_changed:
                started_at = ""
                finalized_at = ""
                summary_status = "pending"
                summary_id = None
                summary_error = ""
                summary_updated_at = ""
            else:
                started_at = str(config.get("started_at", existing.get("started_at", "") if existing else "") or "")
                finalized_at = str(config.get("finalized_at", existing.get("finalized_at", "") if existing else "") or "")
                summary_status = str(config.get("summary_status", existing.get("summary_status", "pending") if existing else "pending") or "pending")
                summary_id = config.get("summary_id", existing.get("summary_id") if existing else None)
                summary_error = str(config.get("summary_error", existing.get("summary_error", "") if existing else "") or "")
                summary_updated_at = str(config.get("summary_updated_at", existing.get("summary_updated_at", "") if existing else "") or "")
            row_data = {
                "session_id": session_id,
                "connector_id": str(config.get("connector_id", "") or ""),
                "display_name": str(config.get("display_name", "") or ""),
                "video_id": video_id,
                "live_chat_id": live_chat_id,
                "target_memoria_session_id": str(config.get("target_memoria_session_id", "") or ""),
                "character_ids_json": self._json_dump(config.get("character_ids") if isinstance(config.get("character_ids"), list) else []),
                "status": str(config.get("status", existing.get("status", "stopped") if existing else "stopped") or "stopped"),
                "auto_connect": 1 if config.get("auto_connect", True) else 0,
                "auto_inject": 1 if config.get("auto_inject", False) else 0,
                "inject_interval_seconds": int(config.get("inject_interval_seconds", 30) or 30),
                "min_pending_events": int(config.get("min_pending_events", 1) or 1),
                "max_pending_events": int(config.get("max_pending_events", 12) or 12),
                "dynamic_inject_enabled": 1 if config.get("dynamic_inject_enabled", True) else 0,
                "max_context_messages": int(config.get("max_context_messages", 50) or 50),
                "max_context_chars": int(config.get("max_context_chars", 8000) or 8000),
                "retention_days": int(config.get("retention_days", 30) or 30),
                "planned_duration_minutes": self._int_or_default(config.get("planned_duration_minutes", 30), 30),
                "auto_finalize_on_duration": 1 if config.get("auto_finalize_on_duration", True) else 0,
                "auto_delete_after_processed": 1 if config.get("auto_delete_after_processed", True) else 0,
                "director_guidance": str(config.get("director_guidance", "") or ""),
                "auto_test_events_enabled": 1 if config.get("auto_test_events_enabled", False) else 0,
                "test_event_min_seconds": int(config.get("test_event_min_seconds", 20) or 20),
                "test_event_max_seconds": int(config.get("test_event_max_seconds", 45) or 45),
                "test_event_count_per_tick": int(config.get("test_event_count_per_tick", 3) or 3),
                "test_event_use_llm": 1 if config.get("test_event_use_llm", True) else 0,
                "test_super_chat_count_per_tick": int(config.get("test_super_chat_count_per_tick", 0) or 0),
                "test_malicious_sc_enabled": 1 if config.get("test_malicious_sc_enabled", False) else 0,
                "test_sc_burst_mode": 1 if config.get("test_sc_burst_mode", False) else 0,
                "sc_interrupt_cooldown_seconds": int(config.get("sc_interrupt_cooldown_seconds", 30) or 30),
                "max_sc_per_batch": int(config.get("max_sc_per_batch", 5) or 5),
                "director_anchor_every_turns": int(config.get("director_anchor_every_turns", 2) or 2),
                "director_group_turn_limit": int(config.get("director_group_turn_limit", 3) or 3),
                "director_max_chat_batches_before_anchor": int(config.get("director_max_chat_batches_before_anchor", 2) or 2),
                "director_offtopic_policy": str(config.get("director_offtopic_policy", "defer") or "defer"),
                "director_sc_burst_policy": str(config.get("director_sc_burst_policy", "summarize_batch") or "summarize_batch"),
                "research_enabled": 1 if config.get("research_enabled", False) else 0,
                "research_cooldown_seconds": int(config.get("research_cooldown_seconds", 300) or 300),
                "research_max_per_session": int(config.get("research_max_per_session", 12) or 12),
                "auto_sc_thanks_on_finalize": 1 if config.get("auto_sc_thanks_on_finalize", True) else 0,
                "started_at": started_at,
                "finalized_at": finalized_at,
                "summary_status": summary_status,
                "summary_id": summary_id,
                "summary_error": summary_error,
                "summary_updated_at": summary_updated_at,
                "created_at": created_at,
                "updated_at": now,
            }
            columns = list(row_data.keys())
            placeholders = ", ".join("?" for _ in columns)
            update_clause = ",\n                    ".join(
                f"{column}=excluded.{column}" for column in columns if column not in {"session_id", "created_at"}
            )
            conn.execute(
                f"""
                INSERT INTO live_sessions ({", ".join(columns)})
                VALUES ({placeholders})
                ON CONFLICT(session_id) DO UPDATE SET
                    {update_clause}
                """,
                [row_data[column] for column in columns],
            )
            conn.commit()
        saved = self.get_session(session_id)
        if not saved:
            raise RuntimeError("live session 儲存失敗")
        return saved

    def list_sessions(self) -> list[dict]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT * FROM live_sessions ORDER BY updated_at DESC, session_id").fetchall()
        return [self._row_to_session(row) for row in rows if row]

    def cleanup_ended_sessions(self, *, limit: int = 1) -> dict[str, Any]:
        """刪除最近 ended runtime sessions 與其本機 runtime 資料。

        這是 E2E 測試用 cleanup，不刪除 YouTube summary 本身；summary metadata
        會由 delete_session 標記 runtime_session_deleted。
        """
        limit = max(1, min(int(limit or 1), 50))
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT session_id FROM live_sessions
                WHERE status = 'ended'
                ORDER BY
                    COALESCE(NULLIF(finalized_at, ''), updated_at) DESC,
                    updated_at DESC,
                    session_id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        deleted_ids: list[str] = []
        for row in rows:
            session_id = str(row["session_id"])
            if self.delete_session(session_id, delete_runtime_data=True):
                deleted_ids.append(session_id)
        return {
            "deleted_count": len(deleted_ids),
            "deleted_session_ids": deleted_ids,
        }

    def get_session(self, session_id: str) -> dict | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM live_sessions WHERE session_id = ?", (session_id,)).fetchone()
        return self._row_to_session(row)

    def update_session_fields(self, session_id: str, **fields) -> dict | None:
        session = self.get_session(session_id)
        if not session:
            return None
        session.update(fields)
        return self.upsert_session(session)

    def delete_session(self, session_id: str, *, delete_runtime_data: bool = True) -> bool:
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            cursor = conn.execute("DELETE FROM live_sessions WHERE session_id = ?", (session_id,))
            if delete_runtime_data:
                conn.execute("DELETE FROM live_events WHERE bridge_session_id = ?", (session_id,))
                conn.execute("DELETE FROM live_interactions WHERE session_id = ?", (session_id,))
                conn.execute("DELETE FROM live_director_state WHERE session_id = ?", (session_id,))
                conn.execute("DELETE FROM live_session_topic_packs WHERE session_id = ?", (session_id,))
                conn.execute("DELETE FROM topic_pack_entry_usages WHERE session_id = ?", (session_id,))
                conn.execute("DELETE FROM research_requests WHERE session_id = ?", (session_id,))
                rows = conn.execute(
                    "SELECT id, metadata_json FROM youtube_live_summaries WHERE session_id = ?",
                    (session_id,),
                ).fetchall()
                for row in rows:
                    metadata = self._json_load(row["metadata_json"], {})
                    if isinstance(metadata, dict):
                        metadata.update({
                            "runtime_session_deleted": True,
                            "runtime_session_deleted_at": now,
                        })
                        conn.execute(
                            "UPDATE youtube_live_summaries SET metadata_json = ?, updated_at = ? WHERE id = ?",
                            (self._json_dump(metadata), now, int(row["id"])),
                        )
            conn.commit()
            return cursor.rowcount > 0

