"""YouTubeBridge SQLite storage。"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


RUNTIME_ROOT = Path(__file__).resolve().parent.parent / "runtime" / "YouTubeBridge"
DEFAULT_DB_PATH = RUNTIME_ROOT / "youtube_live.db"


class BridgeStorage:
    """YouTubeBridge 專用儲存層。

    這個 DB 只保存 YouTube connector、live session 與原始聊天室事件；
    不直接寫入 MemoriaCore 的 runtime DB。
    """

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path or DEFAULT_DB_PATH)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=15.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS connectors (
                    connector_id TEXT PRIMARY KEY,
                    display_name TEXT DEFAULT '',
                    api_key TEXT DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS live_sessions (
                    session_id TEXT PRIMARY KEY,
                    connector_id TEXT NOT NULL,
                    display_name TEXT DEFAULT '',
                    video_id TEXT DEFAULT '',
                    live_chat_id TEXT DEFAULT '',
                    target_memoria_session_id TEXT DEFAULT '',
                    character_ids_json TEXT DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'stopped',
                    auto_connect INTEGER NOT NULL DEFAULT 0,
                    auto_inject INTEGER NOT NULL DEFAULT 0,
                    inject_interval_seconds INTEGER NOT NULL DEFAULT 30,
                    min_pending_events INTEGER NOT NULL DEFAULT 1,
                    max_context_messages INTEGER NOT NULL DEFAULT 50,
                    max_context_chars INTEGER NOT NULL DEFAULT 8000,
                    retention_days INTEGER NOT NULL DEFAULT 30,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS live_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bridge_session_id TEXT NOT NULL,
                    connector_id TEXT NOT NULL,
                    video_id TEXT DEFAULT '',
                    live_chat_id TEXT DEFAULT '',
                    youtube_message_id TEXT NOT NULL,
                    message_type TEXT NOT NULL DEFAULT '',
                    author_channel_id TEXT DEFAULT '',
                    author_display_name TEXT DEFAULT '',
                    author_profile_image_url TEXT DEFAULT '',
                    message_text TEXT DEFAULT '',
                    published_at TEXT DEFAULT '',
                    received_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    amount_display_string TEXT DEFAULT '',
                    currency TEXT DEFAULT '',
                    injected_at TEXT DEFAULT '',
                    injection_count INTEGER NOT NULL DEFAULT 0,
                    metadata_json TEXT DEFAULT '{}',
                    UNIQUE(bridge_session_id, youtube_message_id)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_live_events_session_time "
                "ON live_events(bridge_session_id, id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_live_events_author "
                "ON live_events(author_channel_id)"
            )
            conn.commit()

    @staticmethod
    def _json_dump(value: Any) -> str:
        return json.dumps(value if value is not None else {}, ensure_ascii=False)

    @staticmethod
    def _json_load(value: str, fallback: Any) -> Any:
        try:
            return json.loads(value or "")
        except Exception:
            return fallback

    @classmethod
    def _row_to_connector(cls, row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        return {
            "connector_id": row["connector_id"],
            "display_name": row["display_name"] or "",
            "api_key": row["api_key"] or "",
            "enabled": bool(row["enabled"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @classmethod
    def _row_to_session(cls, row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        return {
            "session_id": row["session_id"],
            "connector_id": row["connector_id"],
            "display_name": row["display_name"] or "",
            "video_id": row["video_id"] or "",
            "live_chat_id": row["live_chat_id"] or "",
            "target_memoria_session_id": row["target_memoria_session_id"] or "",
            "character_ids": cls._json_load(row["character_ids_json"], []),
            "status": row["status"] or "stopped",
            "auto_connect": bool(row["auto_connect"]),
            "auto_inject": bool(row["auto_inject"]),
            "inject_interval_seconds": int(row["inject_interval_seconds"] or 30),
            "min_pending_events": int(row["min_pending_events"] or 1),
            "max_context_messages": int(row["max_context_messages"] or 50),
            "max_context_chars": int(row["max_context_chars"] or 8000),
            "retention_days": int(row["retention_days"] or 30),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @classmethod
    def _row_to_event(cls, row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        return {
            "id": int(row["id"]),
            "bridge_session_id": row["bridge_session_id"],
            "connector_id": row["connector_id"],
            "video_id": row["video_id"] or "",
            "live_chat_id": row["live_chat_id"] or "",
            "youtube_message_id": row["youtube_message_id"] or "",
            "message_type": row["message_type"] or "",
            "author_channel_id": row["author_channel_id"] or "",
            "author_display_name": row["author_display_name"] or "",
            "author_profile_image_url": row["author_profile_image_url"] or "",
            "message_text": row["message_text"] or "",
            "published_at": row["published_at"] or "",
            "received_at": row["received_at"] or "",
            "status": row["status"] or "active",
            "amount_display_string": row["amount_display_string"] or "",
            "currency": row["currency"] or "",
            "injected_at": row["injected_at"] or "",
            "injection_count": int(row["injection_count"] or 0),
            "metadata": cls._json_load(row["metadata_json"], {}),
        }

    def upsert_connector(self, config: dict) -> dict:
        now = datetime.now().isoformat()
        connector_id = str(config.get("connector_id", "")).strip()
        if not connector_id:
            raise ValueError("connector_id 不可為空")
        existing = self.get_connector(connector_id)
        api_key = str(config.get("api_key", "") or "")
        if existing and not api_key:
            api_key = existing.get("api_key", "")
        with self._lock, self._connect() as conn:
            created_at = existing["created_at"] if existing else now
            conn.execute(
                """
                INSERT INTO connectors (
                    connector_id, display_name, api_key, enabled, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(connector_id) DO UPDATE SET
                    display_name=excluded.display_name,
                    api_key=excluded.api_key,
                    enabled=excluded.enabled,
                    updated_at=excluded.updated_at
                """,
                (
                    connector_id,
                    str(config.get("display_name", "") or ""),
                    api_key,
                    1 if config.get("enabled", True) else 0,
                    created_at,
                    now,
                ),
            )
            conn.commit()
        saved = self.get_connector(connector_id)
        if not saved:
            raise RuntimeError("connector 儲存失敗")
        return saved

    def list_connectors(self) -> list[dict]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT * FROM connectors ORDER BY connector_id").fetchall()
        return [self._row_to_connector(row) for row in rows if row]

    def get_connector(self, connector_id: str) -> dict | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM connectors WHERE connector_id = ?", (connector_id,)).fetchone()
        return self._row_to_connector(row)

    def delete_connector(self, connector_id: str) -> bool:
        with self._lock, self._connect() as conn:
            cursor = conn.execute("DELETE FROM connectors WHERE connector_id = ?", (connector_id,))
            conn.commit()
            return cursor.rowcount > 0

    def upsert_session(self, config: dict) -> dict:
        now = datetime.now().isoformat()
        session_id = str(config.get("session_id", "")).strip()
        if not session_id:
            raise ValueError("session_id 不可為空")
        if not self.get_connector(str(config.get("connector_id", "") or "")):
            raise ValueError("connector_id 不存在")
        with self._lock, self._connect() as conn:
            existing = self.get_session(session_id)
            created_at = existing["created_at"] if existing else now
            conn.execute(
                """
                INSERT INTO live_sessions (
                    session_id, connector_id, display_name, video_id, live_chat_id,
                    target_memoria_session_id, character_ids_json, status, auto_connect,
                    auto_inject, inject_interval_seconds, min_pending_events,
                    max_context_messages, max_context_chars, retention_days,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    connector_id=excluded.connector_id,
                    display_name=excluded.display_name,
                    video_id=excluded.video_id,
                    live_chat_id=excluded.live_chat_id,
                    target_memoria_session_id=excluded.target_memoria_session_id,
                    character_ids_json=excluded.character_ids_json,
                    status=excluded.status,
                    auto_connect=excluded.auto_connect,
                    auto_inject=excluded.auto_inject,
                    inject_interval_seconds=excluded.inject_interval_seconds,
                    min_pending_events=excluded.min_pending_events,
                    max_context_messages=excluded.max_context_messages,
                    max_context_chars=excluded.max_context_chars,
                    retention_days=excluded.retention_days,
                    updated_at=excluded.updated_at
                """,
                (
                    session_id,
                    str(config.get("connector_id", "") or ""),
                    str(config.get("display_name", "") or ""),
                    str(config.get("video_id", "") or ""),
                    str(config.get("live_chat_id", "") or ""),
                    str(config.get("target_memoria_session_id", "") or ""),
                    self._json_dump(config.get("character_ids") if isinstance(config.get("character_ids"), list) else []),
                    str(config.get("status", "stopped") or "stopped"),
                    1 if config.get("auto_connect", False) else 0,
                    1 if config.get("auto_inject", False) else 0,
                    int(config.get("inject_interval_seconds", 30) or 30),
                    int(config.get("min_pending_events", 1) or 1),
                    int(config.get("max_context_messages", 50) or 50),
                    int(config.get("max_context_chars", 8000) or 8000),
                    int(config.get("retention_days", 30) or 30),
                    created_at,
                    now,
                ),
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

    def delete_session(self, session_id: str) -> bool:
        with self._lock, self._connect() as conn:
            cursor = conn.execute("DELETE FROM live_sessions WHERE session_id = ?", (session_id,))
            conn.commit()
            return cursor.rowcount > 0

    def save_event(self, event: dict) -> dict | None:
        now = datetime.now().isoformat()
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO live_events (
                    bridge_session_id, connector_id, video_id, live_chat_id, youtube_message_id,
                    message_type, author_channel_id, author_display_name, author_profile_image_url,
                    message_text, published_at, received_at, status, amount_display_string,
                    currency, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(event.get("bridge_session_id", "") or ""),
                    str(event.get("connector_id", "") or ""),
                    str(event.get("video_id", "") or ""),
                    str(event.get("live_chat_id", "") or ""),
                    str(event.get("youtube_message_id", "") or ""),
                    str(event.get("message_type", "") or ""),
                    str(event.get("author_channel_id", "") or ""),
                    str(event.get("author_display_name", "") or ""),
                    str(event.get("author_profile_image_url", "") or ""),
                    str(event.get("message_text", "") or ""),
                    str(event.get("published_at", "") or ""),
                    str(event.get("received_at", "") or now),
                    str(event.get("status", "active") or "active"),
                    str(event.get("amount_display_string", "") or ""),
                    str(event.get("currency", "") or ""),
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
