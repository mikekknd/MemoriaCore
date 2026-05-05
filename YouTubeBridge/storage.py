"""YouTubeBridge SQLite storage。"""
from __future__ import annotations

import json
import hashlib
import math
import sqlite3
import struct
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


RUNTIME_ROOT = Path(__file__).resolve().parent.parent / "runtime" / "YouTubeBridge"
DEFAULT_DB_PATH = RUNTIME_ROOT / "youtube_live.db"
DEFAULT_CONNECTOR_ID = "youtube-main"
DEFAULT_CONNECTOR_NAME = "YouTube Main"


def classify_live_event_safety(text: str) -> str:
    """舊 API 相容用：安全分類改由 SafetyLLM 負責。"""
    return "unclassified" if str(text or "").strip() else "clean"


def infer_super_chat_tier(amount_micros: int, explicit_tier: int = 0) -> int:
    if explicit_tier > 0:
        return min(explicit_tier, 10)
    amount = max(0, int(amount_micros or 0)) / 1_000_000
    if amount >= 3000:
        return 5
    if amount >= 1500:
        return 4
    if amount >= 750:
        return 3
    if amount >= 150:
        return 2
    if amount > 0:
        return 1
    return 0


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
                CREATE TABLE IF NOT EXISTS memoria_config (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    base_url TEXT DEFAULT 'http://localhost:8088/api/v1',
                    username TEXT DEFAULT '',
                    password TEXT DEFAULT '',
                    admin_bypass INTEGER NOT NULL DEFAULT 1,
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
                    auto_connect INTEGER NOT NULL DEFAULT 1,
                    auto_inject INTEGER NOT NULL DEFAULT 0,
                    inject_interval_seconds INTEGER NOT NULL DEFAULT 30,
                    min_pending_events INTEGER NOT NULL DEFAULT 1,
                    max_pending_events INTEGER NOT NULL DEFAULT 12,
                    dynamic_inject_enabled INTEGER NOT NULL DEFAULT 1,
                    max_context_messages INTEGER NOT NULL DEFAULT 50,
                    max_context_chars INTEGER NOT NULL DEFAULT 8000,
                    retention_days INTEGER NOT NULL DEFAULT 30,
                    planned_duration_minutes INTEGER NOT NULL DEFAULT 30,
                    auto_finalize_on_duration INTEGER NOT NULL DEFAULT 1,
                    auto_delete_after_processed INTEGER NOT NULL DEFAULT 1,
                    director_guidance TEXT DEFAULT '',
                    auto_test_events_enabled INTEGER NOT NULL DEFAULT 0,
                    test_event_min_seconds INTEGER NOT NULL DEFAULT 20,
                    test_event_max_seconds INTEGER NOT NULL DEFAULT 45,
                    test_event_count_per_tick INTEGER NOT NULL DEFAULT 3,
                    test_event_use_llm INTEGER NOT NULL DEFAULT 1,
                    test_super_chat_count_per_tick INTEGER NOT NULL DEFAULT 0,
                    test_malicious_sc_enabled INTEGER NOT NULL DEFAULT 0,
                    test_sc_burst_mode INTEGER NOT NULL DEFAULT 0,
                    sc_interrupt_cooldown_seconds INTEGER NOT NULL DEFAULT 30,
                    max_sc_per_batch INTEGER NOT NULL DEFAULT 5,
                    director_anchor_every_turns INTEGER NOT NULL DEFAULT 2,
                    director_group_turn_limit INTEGER NOT NULL DEFAULT 3,
                    director_max_chat_batches_before_anchor INTEGER NOT NULL DEFAULT 2,
                    director_offtopic_policy TEXT DEFAULT 'defer',
                    director_sc_burst_policy TEXT DEFAULT 'summarize_batch',
                    research_enabled INTEGER NOT NULL DEFAULT 0,
                    research_cooldown_seconds INTEGER NOT NULL DEFAULT 300,
                    research_max_per_session INTEGER NOT NULL DEFAULT 12,
                    auto_sc_thanks_on_finalize INTEGER NOT NULL DEFAULT 1,
                    started_at TEXT DEFAULT '',
                    finalized_at TEXT DEFAULT '',
                    summary_status TEXT NOT NULL DEFAULT 'pending',
                    summary_id INTEGER,
                    summary_error TEXT DEFAULT '',
                    summary_updated_at TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._ensure_live_session_columns(conn)
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
                    amount_micros INTEGER NOT NULL DEFAULT 0,
                    sc_tier INTEGER NOT NULL DEFAULT 0,
                    priority_class TEXT DEFAULT 'normal',
                    safety_label TEXT DEFAULT 'unclassified',
                    safety_status TEXT DEFAULT 'pending',
                    safe_message_text TEXT DEFAULT '',
                    safety_summary TEXT DEFAULT '',
                    safety_reason TEXT DEFAULT '',
                    safety_confidence REAL NOT NULL DEFAULT 0,
                    safety_checked_at TEXT DEFAULT '',
                    handled_in_closing_at TEXT DEFAULT '',
                    injected_at TEXT DEFAULT '',
                    injection_count INTEGER NOT NULL DEFAULT 0,
                    metadata_json TEXT DEFAULT '{}',
                    UNIQUE(bridge_session_id, youtube_message_id)
                )
                """
            )
            self._ensure_live_event_columns(conn)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS youtube_live_summaries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    connector_id TEXT NOT NULL,
                    video_id TEXT DEFAULT '',
                    live_chat_id TEXT DEFAULT '',
                    character_ids_json TEXT DEFAULT '[]',
                    title TEXT DEFAULT '',
                    summary_text TEXT NOT NULL,
                    topic_tags_json TEXT DEFAULT '[]',
                    key_points_json TEXT DEFAULT '[]',
                    qa_pairs_json TEXT DEFAULT '[]',
                    audience_mood TEXT DEFAULT '',
                    memory_text TEXT DEFAULT '',
                    event_count INTEGER NOT NULL DEFAULT 0,
                    source_started_at TEXT DEFAULT '',
                    source_ended_at TEXT DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'completed',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    metadata_json TEXT DEFAULT '{}'
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS topic_packs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS topic_pack_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pack_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    source_url TEXT DEFAULT '',
                    source_type TEXT DEFAULT 'manual',
                    tags_json TEXT DEFAULT '[]',
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS topic_pack_entry_embeddings (
                    entry_id INTEGER PRIMARY KEY,
                    pack_id INTEGER NOT NULL,
                    embedding_model TEXT NOT NULL,
                    embedding_dim INTEGER NOT NULL,
                    embedding_blob BLOB NOT NULL,
                    content_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS topic_pack_entry_usages (
                    session_id TEXT NOT NULL,
                    entry_id INTEGER NOT NULL,
                    pack_id INTEGER NOT NULL,
                    query_text TEXT DEFAULT '',
                    similarity REAL DEFAULT 0,
                    usage_source TEXT DEFAULT 'external_context',
                    interaction_id TEXT DEFAULT '',
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS live_session_topic_packs (
                    session_id TEXT NOT NULL,
                    pack_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(session_id, pack_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS research_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    query TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'completed',
                    result_entry_id INTEGER,
                    created_at TEXT NOT NULL,
                    metadata_json TEXT DEFAULT '{}'
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS live_interactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT UNIQUE NOT NULL,
                    session_id TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'youtube_injection',
                    priority INTEGER NOT NULL DEFAULT 100,
                    status TEXT NOT NULL DEFAULT 'queued',
                    reason TEXT DEFAULT '',
                    event_ids_json TEXT DEFAULT '[]',
                    memoria_session_id TEXT DEFAULT '',
                    character_ids_json TEXT DEFAULT '[]',
                    content TEXT DEFAULT '',
                    reply_text TEXT DEFAULT '',
                    closure_text TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    started_at TEXT DEFAULT '',
                    completed_at TEXT DEFAULT '',
                    interrupted_at TEXT DEFAULT '',
                    metadata_json TEXT DEFAULT '{}'
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS live_director_state (
                    session_id TEXT PRIMARY KEY,
                    director_enabled INTEGER NOT NULL DEFAULT 0,
                    idle_seconds INTEGER NOT NULL DEFAULT 60,
                    last_director_action_at TEXT DEFAULT '',
                    current_topic TEXT DEFAULT '',
                    consecutive_ai_turns INTEGER NOT NULL DEFAULT 0,
                    last_seen_event_id INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'stopped',
                    updated_at TEXT NOT NULL,
                    metadata_json TEXT DEFAULT '{}'
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
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_live_events_priority "
                "ON live_events(bridge_session_id, priority_class, sc_tier, id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_live_sessions_summary "
                "ON live_sessions(summary_status, updated_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_live_summaries_session "
                "ON youtube_live_summaries(session_id, id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_live_summaries_video "
                "ON youtube_live_summaries(connector_id, video_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_live_interactions_session "
                "ON live_interactions(session_id, id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_live_interactions_status "
                "ON live_interactions(session_id, status, priority, id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_topic_pack_entries_pack "
                "ON topic_pack_entries(pack_id, id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_topic_pack_embeddings_pack "
                "ON topic_pack_entry_embeddings(pack_id, entry_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_topic_pack_usages_session "
                "ON topic_pack_entry_usages(session_id, created_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_topic_pack_usages_entry "
                "ON topic_pack_entry_usages(session_id, entry_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_research_requests_session "
                "ON research_requests(session_id, created_at)"
            )
            conn.commit()

    @staticmethod
    def _ensure_live_session_columns(conn: sqlite3.Connection) -> None:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(live_sessions)").fetchall()}
        columns = {
            "auto_inject": "auto_inject INTEGER NOT NULL DEFAULT 0",
            "inject_interval_seconds": "inject_interval_seconds INTEGER NOT NULL DEFAULT 30",
            "min_pending_events": "min_pending_events INTEGER NOT NULL DEFAULT 1",
            "max_pending_events": "max_pending_events INTEGER NOT NULL DEFAULT 12",
            "dynamic_inject_enabled": "dynamic_inject_enabled INTEGER NOT NULL DEFAULT 1",
            "planned_duration_minutes": "planned_duration_minutes INTEGER NOT NULL DEFAULT 30",
            "auto_finalize_on_duration": "auto_finalize_on_duration INTEGER NOT NULL DEFAULT 1",
            "auto_delete_after_processed": "auto_delete_after_processed INTEGER NOT NULL DEFAULT 1",
            "director_guidance": "director_guidance TEXT DEFAULT ''",
            "auto_test_events_enabled": "auto_test_events_enabled INTEGER NOT NULL DEFAULT 0",
            "test_event_min_seconds": "test_event_min_seconds INTEGER NOT NULL DEFAULT 20",
            "test_event_max_seconds": "test_event_max_seconds INTEGER NOT NULL DEFAULT 45",
            "test_event_count_per_tick": "test_event_count_per_tick INTEGER NOT NULL DEFAULT 3",
            "test_event_use_llm": "test_event_use_llm INTEGER NOT NULL DEFAULT 1",
            "test_super_chat_count_per_tick": "test_super_chat_count_per_tick INTEGER NOT NULL DEFAULT 0",
            "test_malicious_sc_enabled": "test_malicious_sc_enabled INTEGER NOT NULL DEFAULT 0",
            "test_sc_burst_mode": "test_sc_burst_mode INTEGER NOT NULL DEFAULT 0",
            "sc_interrupt_cooldown_seconds": "sc_interrupt_cooldown_seconds INTEGER NOT NULL DEFAULT 30",
            "max_sc_per_batch": "max_sc_per_batch INTEGER NOT NULL DEFAULT 5",
            "director_anchor_every_turns": "director_anchor_every_turns INTEGER NOT NULL DEFAULT 2",
            "director_group_turn_limit": "director_group_turn_limit INTEGER NOT NULL DEFAULT 3",
            "director_max_chat_batches_before_anchor": "director_max_chat_batches_before_anchor INTEGER NOT NULL DEFAULT 2",
            "director_offtopic_policy": "director_offtopic_policy TEXT DEFAULT 'defer'",
            "director_sc_burst_policy": "director_sc_burst_policy TEXT DEFAULT 'summarize_batch'",
            "research_enabled": "research_enabled INTEGER NOT NULL DEFAULT 0",
            "research_cooldown_seconds": "research_cooldown_seconds INTEGER NOT NULL DEFAULT 300",
            "research_max_per_session": "research_max_per_session INTEGER NOT NULL DEFAULT 12",
            "auto_sc_thanks_on_finalize": "auto_sc_thanks_on_finalize INTEGER NOT NULL DEFAULT 1",
            "started_at": "started_at TEXT DEFAULT ''",
            "finalized_at": "finalized_at TEXT DEFAULT ''",
            "summary_status": "summary_status TEXT NOT NULL DEFAULT 'pending'",
            "summary_id": "summary_id INTEGER",
            "summary_error": "summary_error TEXT DEFAULT ''",
            "summary_updated_at": "summary_updated_at TEXT DEFAULT ''",
        }
        for name, ddl in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE live_sessions ADD COLUMN {ddl}")

    @staticmethod
    def _ensure_live_event_columns(conn: sqlite3.Connection) -> None:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(live_events)").fetchall()}
        columns = {
            "amount_micros": "amount_micros INTEGER NOT NULL DEFAULT 0",
            "sc_tier": "sc_tier INTEGER NOT NULL DEFAULT 0",
            "priority_class": "priority_class TEXT DEFAULT 'normal'",
            "safety_label": "safety_label TEXT DEFAULT 'unclassified'",
            "safety_status": "safety_status TEXT DEFAULT 'pending'",
            "safe_message_text": "safe_message_text TEXT DEFAULT ''",
            "safety_summary": "safety_summary TEXT DEFAULT ''",
            "safety_reason": "safety_reason TEXT DEFAULT ''",
            "safety_confidence": "safety_confidence REAL NOT NULL DEFAULT 0",
            "safety_checked_at": "safety_checked_at TEXT DEFAULT ''",
            "handled_in_closing_at": "handled_in_closing_at TEXT DEFAULT ''",
        }
        for name, ddl in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE live_events ADD COLUMN {ddl}")

    @staticmethod
    def _json_dump(value: Any) -> str:
        return json.dumps(value if value is not None else {}, ensure_ascii=False)

    @staticmethod
    def _json_load(value: str, fallback: Any) -> Any:
        try:
            return json.loads(value or "")
        except Exception:
            return fallback

    @staticmethod
    def topic_entry_content_hash(entry: dict[str, Any]) -> str:
        text = f"{entry.get('title') or ''}\n{entry.get('body') or ''}"
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _vector_to_blob(vector: list[float]) -> bytes:
        values = [float(value) for value in vector]
        if not values:
            return b""
        return struct.pack(f"<{len(values)}f", *values)

    @staticmethod
    def _blob_to_vector(blob: bytes | memoryview | None, dim: int) -> list[float]:
        if not blob or dim <= 0:
            return []
        data = bytes(blob)
        expected = dim * 4
        if len(data) != expected:
            return []
        return [float(value) for value in struct.unpack(f"<{dim}f", data)]

    @staticmethod
    def _cosine_similarity(left: list[float], right: list[float]) -> float:
        if not left or not right or len(left) != len(right):
            return 0.0
        dot = sum(a * b for a, b in zip(left, right))
        left_norm = math.sqrt(sum(a * a for a in left))
        right_norm = math.sqrt(sum(b * b for b in right))
        if left_norm <= 0 or right_norm <= 0:
            return 0.0
        return dot / (left_norm * right_norm)

    @staticmethod
    def _row_value(row: sqlite3.Row, key: str, fallback: Any = None) -> Any:
        return row[key] if key in row.keys() else fallback

    @staticmethod
    def _int_or_default(value: Any, fallback: int) -> int:
        if value is None or value == "":
            return int(fallback)
        return int(value)

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
            "max_pending_events": int(cls._row_value(row, "max_pending_events", 12) or 12),
            "dynamic_inject_enabled": bool(cls._row_value(row, "dynamic_inject_enabled", 1)),
            "max_context_messages": int(row["max_context_messages"] or 50),
            "max_context_chars": int(row["max_context_chars"] or 8000),
            "retention_days": int(row["retention_days"] or 30),
            "planned_duration_minutes": cls._int_or_default(cls._row_value(row, "planned_duration_minutes", 30), 30),
            "auto_finalize_on_duration": bool(cls._row_value(row, "auto_finalize_on_duration", 1)),
            "auto_delete_after_processed": bool(cls._row_value(row, "auto_delete_after_processed", 1)),
            "director_guidance": cls._row_value(row, "director_guidance", "") or "",
            "auto_test_events_enabled": bool(cls._row_value(row, "auto_test_events_enabled", 0)),
            "test_event_min_seconds": int(cls._row_value(row, "test_event_min_seconds", 20) or 20),
            "test_event_max_seconds": int(cls._row_value(row, "test_event_max_seconds", 45) or 45),
            "test_event_count_per_tick": int(cls._row_value(row, "test_event_count_per_tick", 3) or 3),
            "test_event_use_llm": bool(cls._row_value(row, "test_event_use_llm", 1)),
            "test_super_chat_count_per_tick": int(cls._row_value(row, "test_super_chat_count_per_tick", 0) or 0),
            "test_malicious_sc_enabled": bool(cls._row_value(row, "test_malicious_sc_enabled", 0)),
            "test_sc_burst_mode": bool(cls._row_value(row, "test_sc_burst_mode", 0)),
            "sc_interrupt_cooldown_seconds": int(cls._row_value(row, "sc_interrupt_cooldown_seconds", 30) or 30),
            "max_sc_per_batch": int(cls._row_value(row, "max_sc_per_batch", 5) or 5),
            "director_anchor_every_turns": int(cls._row_value(row, "director_anchor_every_turns", 2) or 2),
            "director_group_turn_limit": int(cls._row_value(row, "director_group_turn_limit", 3) or 3),
            "director_max_chat_batches_before_anchor": int(cls._row_value(row, "director_max_chat_batches_before_anchor", 2) or 2),
            "director_offtopic_policy": cls._row_value(row, "director_offtopic_policy", "defer") or "defer",
            "director_sc_burst_policy": cls._row_value(row, "director_sc_burst_policy", "summarize_batch") or "summarize_batch",
            "research_enabled": bool(cls._row_value(row, "research_enabled", 0)),
            "research_cooldown_seconds": int(cls._row_value(row, "research_cooldown_seconds", 300) or 300),
            "research_max_per_session": int(cls._row_value(row, "research_max_per_session", 12) or 12),
            "auto_sc_thanks_on_finalize": bool(cls._row_value(row, "auto_sc_thanks_on_finalize", 1)),
            "started_at": cls._row_value(row, "started_at", "") or "",
            "finalized_at": cls._row_value(row, "finalized_at", "") or "",
            "summary_status": cls._row_value(row, "summary_status", "pending") or "pending",
            "summary_id": cls._row_value(row, "summary_id", None),
            "summary_error": cls._row_value(row, "summary_error", "") or "",
            "summary_updated_at": cls._row_value(row, "summary_updated_at", "") or "",
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
            "amount_micros": int(cls._row_value(row, "amount_micros", 0) or 0),
            "sc_tier": int(cls._row_value(row, "sc_tier", 0) or 0),
            "priority_class": cls._row_value(row, "priority_class", "normal") or "normal",
            "safety_label": cls._row_value(row, "safety_label", "unclassified") or "unclassified",
            "safety_status": cls._row_value(row, "safety_status", "pending") or "pending",
            "safe_message_text": cls._row_value(row, "safe_message_text", "") or "",
            "safety_summary": cls._row_value(row, "safety_summary", "") or "",
            "safety_reason": cls._row_value(row, "safety_reason", "") or "",
            "safety_confidence": float(cls._row_value(row, "safety_confidence", 0) or 0),
            "safety_checked_at": cls._row_value(row, "safety_checked_at", "") or "",
            "handled_in_closing_at": cls._row_value(row, "handled_in_closing_at", "") or "",
            "injected_at": row["injected_at"] or "",
            "injection_count": int(row["injection_count"] or 0),
            "metadata": cls._json_load(row["metadata_json"], {}),
        }

    @classmethod
    def _row_to_summary(cls, row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        return {
            "id": int(row["id"]),
            "session_id": row["session_id"],
            "connector_id": row["connector_id"],
            "video_id": row["video_id"] or "",
            "live_chat_id": row["live_chat_id"] or "",
            "character_ids": cls._json_load(row["character_ids_json"], []),
            "title": row["title"] or "",
            "summary_text": row["summary_text"] or "",
            "topic_tags": cls._json_load(row["topic_tags_json"], []),
            "key_points": cls._json_load(row["key_points_json"], []),
            "qa_pairs": cls._json_load(row["qa_pairs_json"], []),
            "audience_mood": row["audience_mood"] or "",
            "memory_text": row["memory_text"] or "",
            "event_count": int(row["event_count"] or 0),
            "source_started_at": row["source_started_at"] or "",
            "source_ended_at": row["source_ended_at"] or "",
            "status": row["status"] or "completed",
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "metadata": cls._json_load(row["metadata_json"], {}),
        }

    @classmethod
    def _row_to_interaction(cls, row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        return {
            "id": int(row["id"]),
            "job_id": row["job_id"],
            "session_id": row["session_id"],
            "source": row["source"] or "youtube_injection",
            "priority": int(row["priority"] or 100),
            "status": row["status"] or "queued",
            "reason": row["reason"] or "",
            "event_ids": cls._json_load(row["event_ids_json"], []),
            "memoria_session_id": row["memoria_session_id"] or "",
            "character_ids": cls._json_load(row["character_ids_json"], []),
            "content": row["content"] or "",
            "reply_text": row["reply_text"] or "",
            "closure_text": row["closure_text"] or "",
            "created_at": row["created_at"],
            "started_at": row["started_at"] or "",
            "completed_at": row["completed_at"] or "",
            "interrupted_at": row["interrupted_at"] or "",
            "metadata": cls._json_load(row["metadata_json"], {}),
        }

    @classmethod
    def _row_to_director_state(cls, row: sqlite3.Row | None, session_id: str) -> dict:
        if row is None:
            return {
                "session_id": session_id,
                "director_enabled": False,
                "idle_seconds": 60,
                "last_director_action_at": "",
                "current_topic": "",
                "consecutive_ai_turns": 0,
                "last_seen_event_id": 0,
                "status": "stopped",
                "updated_at": "",
                "metadata": {},
            }
        return {
            "session_id": row["session_id"],
            "director_enabled": bool(row["director_enabled"]),
            "idle_seconds": int(row["idle_seconds"] or 60),
            "last_director_action_at": row["last_director_action_at"] or "",
            "current_topic": row["current_topic"] or "",
            "consecutive_ai_turns": int(row["consecutive_ai_turns"] or 0),
            "last_seen_event_id": int(row["last_seen_event_id"] or 0),
            "status": row["status"] or "stopped",
            "updated_at": row["updated_at"] or "",
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

    @staticmethod
    def _normalize_single_connector_refs(conn: sqlite3.Connection, connector_id: str) -> None:
        conn.execute(
            "UPDATE live_sessions SET connector_id = ? WHERE connector_id <> ?",
            (connector_id, connector_id),
        )
        conn.execute(
            "UPDATE live_events SET connector_id = ? WHERE connector_id <> ?",
            (connector_id, connector_id),
        )
        conn.execute(
            "UPDATE youtube_live_summaries SET connector_id = ? WHERE connector_id <> ?",
            (connector_id, connector_id),
        )
        conn.execute("DELETE FROM connectors WHERE connector_id <> ?", (connector_id,))

    def ensure_single_connector(self) -> dict:
        """把測試階段累積的多筆 connector 收斂成唯一一筆。"""
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT * FROM connectors ORDER BY connector_id").fetchall()
            canonical = next((row for row in rows if row["connector_id"] == DEFAULT_CONNECTOR_ID), None)
            keyed = next((row for row in rows if row["api_key"]), None)
            enabled = next((row for row in rows if row["enabled"]), None)
            source = canonical or keyed or enabled or (rows[0] if rows else None)

            if not canonical:
                conn.execute(
                    """
                    INSERT INTO connectors (
                        connector_id, display_name, api_key, enabled, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        DEFAULT_CONNECTOR_ID,
                        (source["display_name"] if source else "") or DEFAULT_CONNECTOR_NAME,
                        (source["api_key"] if source else "") or "",
                        int(source["enabled"]) if source else 1,
                        (source["created_at"] if source else "") or now,
                        now,
                    ),
                )
            else:
                next_display_name = canonical["display_name"] or DEFAULT_CONNECTOR_NAME
                next_api_key = canonical["api_key"] or ((keyed["api_key"] if keyed else "") or "")
                if next_display_name != canonical["display_name"] or next_api_key != canonical["api_key"]:
                    conn.execute(
                        """
                        UPDATE connectors
                        SET display_name = ?, api_key = ?, updated_at = ?
                        WHERE connector_id = ?
                        """,
                        (next_display_name, next_api_key, now, DEFAULT_CONNECTOR_ID),
                    )

            self._normalize_single_connector_refs(conn, DEFAULT_CONNECTOR_ID)
            conn.commit()
            row = conn.execute(
                "SELECT * FROM connectors WHERE connector_id = ?",
                (DEFAULT_CONNECTOR_ID,),
            ).fetchone()
        connector = self._row_to_connector(row)
        if not connector:
            raise RuntimeError("single connector 初始化失敗")
        return connector

    def upsert_single_connector(self, config: dict) -> dict:
        existing = self.ensure_single_connector()
        api_key = str(config.get("api_key", "") or "")
        payload = {
            "connector_id": DEFAULT_CONNECTOR_ID,
            "display_name": str(config.get("display_name", "") or existing.get("display_name") or DEFAULT_CONNECTOR_NAME),
            "api_key": api_key or existing.get("api_key", ""),
            "enabled": bool(config.get("enabled")) if "enabled" in config else bool(existing.get("enabled", True)),
        }
        saved = self.upsert_connector(payload)
        with self._lock, self._connect() as conn:
            self._normalize_single_connector_refs(conn, DEFAULT_CONNECTOR_ID)
            conn.commit()
        refreshed = self.get_connector(DEFAULT_CONNECTOR_ID)
        if not refreshed:
            raise RuntimeError("single connector 儲存失敗")
        return refreshed

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

    def get_memoria_config(self) -> dict:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM memoria_config WHERE id = 1").fetchone()
        if not row:
            return {
                "base_url": "http://localhost:8088/api/v1",
                "username": "",
                "password": "",
                "admin_bypass": True,
                "created_at": "",
                "updated_at": "",
            }
        return {
            "base_url": row["base_url"] or "http://localhost:8088/api/v1",
            "username": row["username"] or "",
            "password": row["password"] or "",
            "admin_bypass": bool(row["admin_bypass"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def get_public_memoria_config(self) -> dict:
        config = self.get_memoria_config()
        return {
            "base_url": config.get("base_url") or "http://localhost:8088/api/v1",
            "username": config.get("username") or "",
            "admin_bypass": bool(config.get("admin_bypass", True)),
            "password_configured": bool(config.get("password")),
            "updated_at": config.get("updated_at", ""),
        }

    def upsert_memoria_config(self, config: dict[str, Any]) -> dict:
        now = datetime.now().isoformat()
        existing = self.get_memoria_config()
        password = str(config.get("password", "") or "")
        if not password:
            password = str(existing.get("password", "") or "")
        base_url = str(config.get("base_url", "") or existing.get("base_url") or "http://localhost:8088/api/v1").rstrip("/")
        username = str(config.get("username", "") or "")
        created_at = existing.get("created_at") or now
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO memoria_config (
                    id, base_url, username, password, admin_bypass, created_at, updated_at
                ) VALUES (1, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    base_url=excluded.base_url,
                    username=excluded.username,
                    password=excluded.password,
                    admin_bypass=excluded.admin_bypass,
                    updated_at=excluded.updated_at
                """,
                (
                    base_url,
                    username,
                    password,
                    1 if config.get("admin_bypass", True) else 0,
                    created_at,
                    now,
                ),
            )
            conn.commit()
        return self.get_memoria_config()

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
    ) -> list[dict]:
        limit = max(1, min(int(limit or 100), 500))
        where = "bridge_session_id = ? AND priority_class = 'super_chat'"
        if unhandled_only:
            where += " AND (handled_in_closing_at IS NULL OR handled_in_closing_at = '')"
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM live_events WHERE {where} ORDER BY sc_tier DESC, id ASC LIMIT ?",
                (session_id, limit),
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

    @classmethod
    def _row_to_topic_pack(cls, row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        return {
            "id": int(row["id"]),
            "title": row["title"] or "",
            "description": row["description"] or "",
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @classmethod
    def _row_to_topic_pack_entry(cls, row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        return {
            "id": int(row["id"]),
            "pack_id": int(row["pack_id"]),
            "pack_title": cls._row_value(row, "pack_title", "") or "",
            "title": row["title"] or "",
            "body": row["body"] or "",
            "source_url": row["source_url"] or "",
            "source_type": row["source_type"] or "manual",
            "tags": cls._json_load(row["tags_json"], []),
            "created_at": row["created_at"],
        }

    @classmethod
    def _row_to_topic_pack_entry_embedding(cls, row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        dim = int(row["embedding_dim"] or 0)
        return {
            "entry_id": int(row["entry_id"]),
            "pack_id": int(row["pack_id"]),
            "embedding_model": row["embedding_model"] or "",
            "embedding_dim": dim,
            "embedding": cls._blob_to_vector(row["embedding_blob"], dim),
            "content_hash": row["content_hash"] or "",
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def create_topic_pack(self, data: dict[str, Any]) -> dict:
        now = datetime.now().isoformat()
        title = str(data.get("title", "") or "").strip()
        if not title:
            raise ValueError("topic pack title 不可為空")
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO topic_packs (title, description, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (title[:200], str(data.get("description", "") or "")[:1000], now, now),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM topic_packs WHERE id = ?", (cursor.lastrowid,)).fetchone()
        pack = self._row_to_topic_pack(row)
        if not pack:
            raise RuntimeError("topic pack 建立失敗")
        return pack

    def update_topic_pack(self, pack_id: int, data: dict[str, Any]) -> dict:
        now = datetime.now().isoformat()
        title = str(data.get("title", "") or "").strip()
        if not title:
            raise ValueError("topic pack title 不可為空")
        description = str(data.get("description", "") or "").strip()
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE topic_packs
                SET title = ?, description = ?, updated_at = ?
                WHERE id = ?
                """,
                (title[:200], description[:1000], now, int(pack_id)),
            )
            if int(cursor.rowcount or 0) <= 0:
                raise ValueError("topic pack 不存在")
            conn.commit()
            row = conn.execute("SELECT * FROM topic_packs WHERE id = ?", (int(pack_id),)).fetchone()
        pack = self._row_to_topic_pack(row)
        if not pack:
            raise RuntimeError("topic pack 更新失敗")
        return pack

    def list_topic_packs(self, *, limit: int = 100) -> list[dict]:
        limit = max(1, min(int(limit or 100), 500))
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT * FROM topic_packs ORDER BY updated_at DESC, id DESC LIMIT ?", (limit,)).fetchall()
        return [pack for row in rows if (pack := self._row_to_topic_pack(row))]

    def get_topic_pack(self, pack_id: int) -> dict | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM topic_packs WHERE id = ?", (int(pack_id),)).fetchone()
        return self._row_to_topic_pack(row)

    def create_topic_pack_entry(self, pack_id: int, data: dict[str, Any]) -> dict:
        now = datetime.now().isoformat()
        if not self.get_topic_pack(int(pack_id)):
            raise ValueError("topic pack 不存在")
        title = str(data.get("title", "") or "").strip()
        body = str(data.get("body", "") or "").strip()
        if not title or not body:
            raise ValueError("topic pack entry 需要 title 與 body")
        tags = data.get("tags") if isinstance(data.get("tags"), list) else []
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO topic_pack_entries (
                    pack_id, title, body, source_url, source_type, tags_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(pack_id),
                    title[:200],
                    body[:4000],
                    str(data.get("source_url", "") or "")[:1000],
                    str(data.get("source_type", "manual") or "manual")[:80],
                    self._json_dump([str(tag).strip() for tag in tags if str(tag).strip()]),
                    now,
                ),
            )
            conn.execute("UPDATE topic_packs SET updated_at = ? WHERE id = ?", (now, int(pack_id)))
            conn.commit()
            row = conn.execute("SELECT * FROM topic_pack_entries WHERE id = ?", (cursor.lastrowid,)).fetchone()
        entry = self._row_to_topic_pack_entry(row)
        if not entry:
            raise RuntimeError("topic pack entry 建立失敗")
        return entry

    def update_topic_pack_entry(self, entry_id: int, data: dict[str, Any]) -> dict:
        existing = self.get_topic_pack_entry(int(entry_id))
        if not existing:
            raise ValueError("topic pack entry 不存在")
        title = str(data.get("title", "") or "").strip()
        body = str(data.get("body", "") or "").strip()
        if not title or not body:
            raise ValueError("topic pack entry 需要 title 與 body")
        tags = data.get("tags") if isinstance(data.get("tags"), list) else []
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE topic_pack_entries
                SET title = ?, body = ?, source_url = ?, source_type = ?, tags_json = ?
                WHERE id = ?
                """,
                (
                    title[:200],
                    body[:4000],
                    str(data.get("source_url", "") or "")[:1000],
                    str(data.get("source_type", "manual") or "manual")[:80],
                    self._json_dump([str(tag).strip() for tag in tags if str(tag).strip()]),
                    int(entry_id),
                ),
            )
            if int(cursor.rowcount or 0) <= 0:
                raise ValueError("topic pack entry 不存在")
            conn.execute("DELETE FROM topic_pack_entry_embeddings WHERE entry_id = ?", (int(entry_id),))
            conn.execute("UPDATE topic_packs SET updated_at = ? WHERE id = ?", (now, int(existing["pack_id"])))
            conn.commit()
            row = conn.execute("SELECT * FROM topic_pack_entries WHERE id = ?", (int(entry_id),)).fetchone()
        entry = self._row_to_topic_pack_entry(row)
        if not entry:
            raise RuntimeError("topic pack entry 更新失敗")
        return entry

    def delete_topic_pack_entry(self, entry_id: int) -> bool:
        existing = self.get_topic_pack_entry(int(entry_id))
        if not existing:
            return False
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM topic_pack_entry_embeddings WHERE entry_id = ?", (int(entry_id),))
            conn.execute("DELETE FROM topic_pack_entry_usages WHERE entry_id = ?", (int(entry_id),))
            conn.execute("UPDATE research_requests SET result_entry_id = NULL WHERE result_entry_id = ?", (int(entry_id),))
            cursor = conn.execute("DELETE FROM topic_pack_entries WHERE id = ?", (int(entry_id),))
            conn.execute("UPDATE topic_packs SET updated_at = ? WHERE id = ?", (now, int(existing["pack_id"])))
            conn.commit()
        return int(cursor.rowcount or 0) > 0

    def delete_topic_pack(self, pack_id: int) -> dict[str, Any]:
        pack_id = int(pack_id)
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT id FROM topic_packs WHERE id = ?", (pack_id,)).fetchone()
            if not row:
                return {"deleted": False, "pack_id": pack_id, "entry_count": 0}
            entry_rows = conn.execute(
                "SELECT id FROM topic_pack_entries WHERE pack_id = ?",
                (pack_id,),
            ).fetchall()
            entry_ids = [int(item["id"]) for item in entry_rows]
            entry_count = len(entry_ids)
            if entry_ids:
                placeholders = ",".join("?" for _ in entry_ids)
                conn.execute(
                    f"UPDATE research_requests SET result_entry_id = NULL WHERE result_entry_id IN ({placeholders})",
                    entry_ids,
                )
                conn.execute(
                    f"DELETE FROM topic_pack_entry_embeddings WHERE entry_id IN ({placeholders})",
                    entry_ids,
                )
                conn.execute(
                    f"DELETE FROM topic_pack_entry_usages WHERE entry_id IN ({placeholders})",
                    entry_ids,
                )
            conn.execute("DELETE FROM topic_pack_entry_embeddings WHERE pack_id = ?", (pack_id,))
            conn.execute("DELETE FROM topic_pack_entry_usages WHERE pack_id = ?", (pack_id,))
            conn.execute("DELETE FROM live_session_topic_packs WHERE pack_id = ?", (pack_id,))
            conn.execute("DELETE FROM topic_pack_entries WHERE pack_id = ?", (pack_id,))
            cursor = conn.execute("DELETE FROM topic_packs WHERE id = ?", (pack_id,))
            conn.commit()
        return {
            "deleted": int(cursor.rowcount or 0) > 0,
            "pack_id": pack_id,
            "entry_count": entry_count,
        }

    def delete_all_topic_packs(self) -> dict[str, Any]:
        with self._lock, self._connect() as conn:
            pack_row = conn.execute("SELECT COUNT(*) AS count FROM topic_packs").fetchone()
            entry_row = conn.execute("SELECT COUNT(*) AS count FROM topic_pack_entries").fetchone()
            pack_count = int(pack_row["count"] or 0) if pack_row else 0
            entry_count = int(entry_row["count"] or 0) if entry_row else 0
            conn.execute(
                """
                UPDATE research_requests
                SET result_entry_id = NULL
                WHERE result_entry_id IN (SELECT id FROM topic_pack_entries)
                """
            )
            conn.execute("DELETE FROM topic_pack_entry_embeddings")
            conn.execute("DELETE FROM topic_pack_entry_usages")
            conn.execute("DELETE FROM live_session_topic_packs")
            conn.execute("DELETE FROM topic_pack_entries")
            conn.execute("DELETE FROM topic_packs")
            conn.commit()
        return {
            "deleted": pack_count > 0,
            "pack_count": pack_count,
            "entry_count": entry_count,
        }

    def list_topic_pack_entries(self, pack_id: int, *, limit: int = 100) -> list[dict]:
        limit = max(1, min(int(limit or 100), 500))
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM topic_pack_entries WHERE pack_id = ? ORDER BY id DESC LIMIT ?",
                (int(pack_id), limit),
            ).fetchall()
        entries = [entry for row in rows if (entry := self._row_to_topic_pack_entry(row))]
        entries.reverse()
        return entries

    def get_topic_pack_entry(self, entry_id: int) -> dict | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM topic_pack_entries WHERE id = ?", (int(entry_id),)).fetchone()
        return self._row_to_topic_pack_entry(row)

    def upsert_topic_pack_entry_embedding(
        self,
        entry_id: int,
        embedding: list[float],
        *,
        model: str,
        content_hash: str = "",
    ) -> dict:
        entry = self.get_topic_pack_entry(int(entry_id))
        if not entry:
            raise ValueError("topic pack entry 不存在")
        vector = [float(value) for value in embedding if isinstance(value, int | float)]
        if not vector:
            raise ValueError("embedding 不可為空")
        now = datetime.now().isoformat()
        existing = self.get_topic_pack_entry_embedding(int(entry_id))
        created_at = existing["created_at"] if existing else now
        content_hash = content_hash or self.topic_entry_content_hash(entry)
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO topic_pack_entry_embeddings (
                    entry_id, pack_id, embedding_model, embedding_dim, embedding_blob,
                    content_hash, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(entry_id) DO UPDATE SET
                    pack_id=excluded.pack_id,
                    embedding_model=excluded.embedding_model,
                    embedding_dim=excluded.embedding_dim,
                    embedding_blob=excluded.embedding_blob,
                    content_hash=excluded.content_hash,
                    updated_at=excluded.updated_at
                """,
                (
                    int(entry_id),
                    int(entry["pack_id"]),
                    str(model or "unknown")[:120],
                    len(vector),
                    self._vector_to_blob(vector),
                    content_hash,
                    created_at,
                    now,
                ),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM topic_pack_entry_embeddings WHERE entry_id = ?",
                (int(entry_id),),
            ).fetchone()
        saved = self._row_to_topic_pack_entry_embedding(row)
        if not saved:
            raise RuntimeError("topic pack entry embedding 儲存失敗")
        return saved

    def get_topic_pack_entry_embedding(self, entry_id: int) -> dict | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM topic_pack_entry_embeddings WHERE entry_id = ?",
                (int(entry_id),),
            ).fetchone()
        return self._row_to_topic_pack_entry_embedding(row)

    def list_topic_pack_entries_missing_embeddings(self, pack_id: int, *, limit: int = 100) -> list[dict]:
        limit = max(1, min(int(limit or 100), 500))
        return [
            entry for entry in self.list_topic_pack_entries(pack_id, limit=limit)
            if not (embedding := self.get_topic_pack_entry_embedding(entry["id"]))
            or embedding.get("content_hash") != self.topic_entry_content_hash(entry)
        ]

    def search_session_topic_pack_entries(
        self,
        session_id: str,
        query_embedding: list[float],
        *,
        limit: int = 6,
        min_score: float = 0.0,
    ) -> list[dict]:
        limit = max(1, min(int(limit or 6), 50))
        query_vector = [float(value) for value in query_embedding if isinstance(value, int | float)]
        if not query_vector:
            return []
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT e.*, p.title AS pack_title, emb.embedding_model, emb.embedding_dim,
                       emb.embedding_blob, emb.content_hash AS embedding_content_hash,
                       emb.updated_at AS embedding_updated_at
                FROM live_session_topic_packs sp
                JOIN topic_packs p ON p.id = sp.pack_id
                JOIN topic_pack_entries e ON e.pack_id = p.id
                JOIN topic_pack_entry_embeddings emb ON emb.entry_id = e.id
                WHERE sp.session_id = ?
                """,
                (session_id,),
            ).fetchall()
        scored: list[dict] = []
        for row in rows:
            entry = self._row_to_topic_pack_entry(row)
            if not entry:
                continue
            vector = self._blob_to_vector(row["embedding_blob"], int(row["embedding_dim"] or 0))
            score = self._cosine_similarity(query_vector, vector)
            if score < float(min_score or 0.0):
                continue
            entry.update({
                "similarity": score,
                "embedding_model": row["embedding_model"] or "",
                "embedding_content_hash": row["embedding_content_hash"] or "",
                "embedding_updated_at": row["embedding_updated_at"] or "",
            })
            scored.append(entry)
        scored.sort(key=lambda item: item.get("similarity", 0.0), reverse=True)
        return scored[:limit]

    def search_topic_pack_entries(
        self,
        pack_id: int,
        query_embedding: list[float],
        *,
        limit: int = 6,
        min_score: float = 0.0,
    ) -> list[dict]:
        limit = max(1, min(int(limit or 6), 50))
        query_vector = [float(value) for value in query_embedding if isinstance(value, int | float)]
        if not query_vector:
            return []
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT e.*, p.title AS pack_title, emb.embedding_model, emb.embedding_dim,
                       emb.embedding_blob, emb.content_hash AS embedding_content_hash,
                       emb.updated_at AS embedding_updated_at
                FROM topic_packs p
                JOIN topic_pack_entries e ON e.pack_id = p.id
                JOIN topic_pack_entry_embeddings emb ON emb.entry_id = e.id
                WHERE p.id = ?
                """,
                (int(pack_id),),
            ).fetchall()
        scored: list[dict] = []
        for row in rows:
            entry = self._row_to_topic_pack_entry(row)
            if not entry:
                continue
            vector = self._blob_to_vector(row["embedding_blob"], int(row["embedding_dim"] or 0))
            score = self._cosine_similarity(query_vector, vector)
            if score < float(min_score or 0.0):
                continue
            entry.update({
                "similarity": score,
                "embedding_model": row["embedding_model"] or "",
                "embedding_content_hash": row["embedding_content_hash"] or "",
                "embedding_updated_at": row["embedding_updated_at"] or "",
            })
            scored.append(entry)
        scored.sort(key=lambda item: item.get("similarity", 0.0), reverse=True)
        return scored[:limit]

    def record_topic_pack_entry_usages(
        self,
        session_id: str,
        entries: list[dict[str, Any]],
        *,
        query_text: str = "",
        usage_source: str = "external_context",
        interaction_id: str | int | None = None,
    ) -> list[dict[str, Any]]:
        now = datetime.now().isoformat()
        clean_session_id = str(session_id or "").strip()
        if not clean_session_id or not entries:
            return []
        clean_source = str(usage_source or "external_context").strip()[:80] or "external_context"
        clean_query = str(query_text or "").replace("\r", " ").replace("\n", " ").strip()[:1000]
        clean_interaction_id = str(interaction_id or "").strip()[:120]
        rows: list[tuple[str, int, int, str, float, str, str, str]] = []
        for item in entries:
            if not isinstance(item, dict):
                continue
            try:
                entry_id = int(item.get("entry_id") or item.get("id") or 0)
                pack_id = int(item.get("pack_id") or 0)
            except (TypeError, ValueError):
                continue
            if entry_id <= 0:
                continue
            if pack_id <= 0:
                entry = self.get_topic_pack_entry(entry_id)
                if not entry:
                    continue
                pack_id = int(entry["pack_id"])
            try:
                similarity = float(item.get("similarity") or 0.0)
            except (TypeError, ValueError):
                similarity = 0.0
            rows.append((
                clean_session_id,
                entry_id,
                pack_id,
                clean_query,
                similarity,
                clean_source,
                clean_interaction_id,
                now,
            ))
        if not rows:
            return []
        with self._lock, self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO topic_pack_entry_usages (
                    session_id, entry_id, pack_id, query_text, similarity,
                    usage_source, interaction_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.commit()
        return [
            {
                "session_id": row[0],
                "entry_id": row[1],
                "pack_id": row[2],
                "query_text": row[3],
                "similarity": row[4],
                "usage_source": row[5],
                "interaction_id": row[6],
                "created_at": row[7],
            }
            for row in rows
        ]

    def get_topic_pack_usage_stats(
        self,
        session_id: str,
        *,
        recent_limit: int = 8,
        low_unused_threshold: int = 3,
        repeat_threshold: int = 3,
    ) -> dict[str, Any]:
        recent_limit = max(1, min(int(recent_limit or 8), 100))
        low_unused_threshold = max(0, int(low_unused_threshold or 0))
        repeat_threshold = max(1, int(repeat_threshold or 1))
        entries = self.list_session_topic_pack_entries(session_id, limit=500)
        usage_by_entry: dict[int, dict[str, Any]] = {}
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT entry_id,
                       COUNT(*) AS usage_count,
                       AVG(similarity) AS avg_similarity,
                       MAX(created_at) AS last_used_at
                FROM topic_pack_entry_usages
                WHERE session_id = ?
                GROUP BY entry_id
                """,
                (session_id,),
            ).fetchall()
            source_rows = conn.execute(
                """
                SELECT entry_id, usage_source
                FROM topic_pack_entry_usages
                WHERE session_id = ?
                ORDER BY created_at ASC
                """,
                (session_id,),
            ).fetchall()
            recent_rows = conn.execute(
                """
                SELECT session_id, entry_id, pack_id, query_text, similarity,
                       usage_source, interaction_id, created_at
                FROM topic_pack_entry_usages
                WHERE session_id = ?
                ORDER BY created_at DESC, rowid DESC
                LIMIT ?
                """,
                (session_id, recent_limit),
            ).fetchall()
        for row in rows:
            usage_by_entry[int(row["entry_id"])] = {
                "usage_count": int(row["usage_count"] or 0),
                "avg_similarity": float(row["avg_similarity"] or 0.0),
                "last_used_at": row["last_used_at"] or "",
                "usage_sources": [],
            }
        for row in source_rows:
            entry_id = int(row["entry_id"])
            usage = usage_by_entry.setdefault(
                entry_id,
                {"usage_count": 0, "avg_similarity": 0.0, "last_used_at": "", "usage_sources": []},
            )
            source = str(row["usage_source"] or "").strip()
            if source and source not in usage["usage_sources"]:
                usage["usage_sources"].append(source)

        stats_entries: list[dict[str, Any]] = []
        for entry in entries:
            entry_id = int(entry["id"])
            usage = usage_by_entry.get(
                entry_id,
                {"usage_count": 0, "avg_similarity": 0.0, "last_used_at": "", "usage_sources": []},
            )
            stats_entries.append({
                "entry_id": entry_id,
                "pack_id": int(entry["pack_id"]),
                "title": entry.get("title", ""),
                "source_type": entry.get("source_type", ""),
                "usage_count": int(usage.get("usage_count") or 0),
                "avg_similarity": float(usage.get("avg_similarity") or 0.0),
                "last_used_at": str(usage.get("last_used_at") or ""),
                "usage_sources": list(usage.get("usage_sources") or []),
            })

        recent_usage = [
            {
                "session_id": row["session_id"],
                "entry_id": int(row["entry_id"]),
                "pack_id": int(row["pack_id"]),
                "query_text": row["query_text"] or "",
                "similarity": float(row["similarity"] or 0.0),
                "usage_source": row["usage_source"] or "",
                "interaction_id": row["interaction_id"] or "",
                "created_at": row["created_at"],
            }
            for row in recent_rows
        ]
        recent_counts: dict[int, int] = {}
        for item in recent_usage:
            entry_id = int(item["entry_id"])
            recent_counts[entry_id] = recent_counts.get(entry_id, 0) + 1
        repeated_entry = None
        for entry_id, count in sorted(recent_counts.items(), key=lambda pair: pair[1], reverse=True):
            if count < repeat_threshold:
                continue
            entry = next((item for item in stats_entries if item["entry_id"] == entry_id), None)
            repeated_entry = {
                "entry_id": entry_id,
                "recent_count": count,
                "title": entry.get("title", "") if entry else "",
            }
            break
        used_entry_count = sum(1 for item in stats_entries if int(item["usage_count"] or 0) > 0)
        unused_entry_count = max(0, len(stats_entries) - used_entry_count)
        return {
            "session_id": session_id,
            "total_entries": len(stats_entries),
            "used_entry_count": used_entry_count,
            "unused_entry_count": unused_entry_count,
            "low_unused": unused_entry_count < low_unused_threshold,
            "repeated_entry": repeated_entry,
            "entries": stats_entries,
            "recent_usage": recent_usage,
        }

    def link_topic_pack_to_session(self, session_id: str, pack_id: int) -> dict:
        if not self.get_session(session_id):
            raise ValueError("live session 不存在")
        if not self.get_topic_pack(int(pack_id)):
            raise ValueError("topic pack 不存在")
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO live_session_topic_packs (session_id, pack_id, created_at)
                VALUES (?, ?, ?)
                """,
                (session_id, int(pack_id), now),
            )
            conn.commit()
        return {"session_id": session_id, "pack_id": int(pack_id), "created_at": now}

    def list_session_topic_packs(self, session_id: str) -> list[dict]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT p.*
                FROM live_session_topic_packs sp
                JOIN topic_packs p ON p.id = sp.pack_id
                WHERE sp.session_id = ?
                ORDER BY sp.created_at ASC, p.id ASC
                """,
                (session_id,),
            ).fetchall()
        return [pack for row in rows if (pack := self._row_to_topic_pack(row))]

    def list_session_topic_pack_entries(self, session_id: str, *, limit: int = 20) -> list[dict]:
        limit = max(1, min(int(limit or 20), 200))
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT e.*, p.title AS pack_title
                FROM live_session_topic_packs sp
                JOIN topic_packs p ON p.id = sp.pack_id
                JOIN topic_pack_entries e ON e.pack_id = p.id
                WHERE sp.session_id = ?
                ORDER BY e.id DESC
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()
        entries = [entry for row in rows if (entry := self._row_to_topic_pack_entry(row))]
        entries.reverse()
        return entries

    def create_research_request(
        self,
        session_id: str,
        query: str,
        *,
        status: str = "completed",
        result_entry_id: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict:
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO research_requests (
                    session_id, query, status, result_entry_id, created_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (session_id, query[:500], status, result_entry_id, now, self._json_dump(metadata or {})),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM research_requests WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return {
            "id": int(row["id"]),
            "session_id": row["session_id"],
            "query": row["query"],
            "status": row["status"],
            "result_entry_id": row["result_entry_id"],
            "created_at": row["created_at"],
            "metadata": self._json_load(row["metadata_json"], {}),
        }

    def count_research_requests(self, session_id: str, *, since_iso: str = "") -> int:
        where = "session_id = ?"
        params: list[Any] = [session_id]
        if since_iso:
            where += " AND created_at >= ?"
            params.append(since_iso)
        with self._lock, self._connect() as conn:
            row = conn.execute(f"SELECT COUNT(*) AS count FROM research_requests WHERE {where}", params).fetchone()
        return int(row["count"] or 0) if row else 0

    def list_research_requests(self, session_id: str, *, limit: int = 50) -> list[dict]:
        limit = max(1, min(int(limit or 50), 200))
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM research_requests
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "session_id": row["session_id"],
                "query": row["query"],
                "status": row["status"],
                "result_entry_id": row["result_entry_id"],
                "created_at": row["created_at"],
                "metadata": self._json_load(row["metadata_json"], {}),
            }
            for row in rows
        ]

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
            if target["status"] == "running":
                return self._row_to_interaction(target)
            if target["status"] != "queued":
                return None
            running_rows = conn.execute(
                """
                SELECT * FROM live_interactions
                WHERE session_id = ?
                  AND status = 'running'
                ORDER BY priority DESC, id ASC
                """,
                (target["session_id"],),
            ).fetchall()
            self._finalize_duplicate_running_rows(conn, running_rows)
            running_count = conn.execute(
                """
                SELECT COUNT(*) AS count FROM live_interactions
                WHERE session_id = ?
                  AND status = 'running'
                """,
                (target["session_id"],),
            ).fetchone()
            if running_count and int(running_count["count"] or 0) > 0:
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
                  AND status = 'running'
                ORDER BY priority DESC, id ASC
                """,
                (session_id,),
            ).fetchall()
            kept = self._finalize_duplicate_running_rows(conn, running_rows)
            if kept:
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
                  AND status = 'running'
                ORDER BY priority DESC, id ASC
                """,
                (session_id,),
            ).fetchall()
            self._finalize_duplicate_running_rows(conn, running_rows)
            rows = conn.execute(
                """
                SELECT * FROM live_interactions
                WHERE session_id = ?
                  AND status IN ('queued', 'running', 'interrupt_requested')
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
                  AND status IN ('queued', 'running', 'interrupt_requested')
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
