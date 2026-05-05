"""YouTubeBridge SQLite storage。"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

import storage_mappers as mappers
from storage_constants import DEFAULT_CONNECTOR_ID, DEFAULT_CONNECTOR_NAME
from storage_event_utils import classify_live_event_safety, infer_super_chat_tier
from storage_repositories import (
    ConnectorRepositoryMixin,
    DirectorStateRepositoryMixin,
    EventRepositoryMixin,
    InteractionRepositoryMixin,
    SessionRepositoryMixin,
    SummaryRepositoryMixin,
    TopicPackRepositoryMixin,
)
from storage_schema import (
    ensure_live_event_columns,
    ensure_live_session_columns,
    init_bridge_db,
)


RUNTIME_ROOT = Path(__file__).resolve().parent.parent / "runtime" / "YouTubeBridge"
DEFAULT_DB_PATH = RUNTIME_ROOT / "youtube_live.db"


class BridgeStorage(
    ConnectorRepositoryMixin,
    SessionRepositoryMixin,
    EventRepositoryMixin,
    TopicPackRepositoryMixin,
    InteractionRepositoryMixin,
    DirectorStateRepositoryMixin,
    SummaryRepositoryMixin,
):
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
            init_bridge_db(conn)

    @staticmethod
    def _ensure_live_session_columns(conn: sqlite3.Connection) -> None:
        ensure_live_session_columns(conn)

    @staticmethod
    def _ensure_live_event_columns(conn: sqlite3.Connection) -> None:
        ensure_live_event_columns(conn)

    @staticmethod
    def _json_dump(value: Any) -> str:
        return mappers.json_dump(value)

    @staticmethod
    def _json_load(value: str, fallback: Any) -> Any:
        return mappers.json_load(value, fallback)

    @staticmethod
    def topic_entry_content_hash(entry: dict[str, Any]) -> str:
        return mappers.topic_entry_content_hash(entry)

    @staticmethod
    def _vector_to_blob(vector: list[float]) -> bytes:
        return mappers.vector_to_blob(vector)

    @staticmethod
    def _blob_to_vector(blob: bytes | memoryview | None, dim: int) -> list[float]:
        return mappers.blob_to_vector(blob, dim)

    @staticmethod
    def _cosine_similarity(left: list[float], right: list[float]) -> float:
        return mappers.cosine_similarity(left, right)

    @staticmethod
    def _row_value(row: sqlite3.Row, key: str, fallback: Any = None) -> Any:
        return mappers.row_value(row, key, fallback)

    @staticmethod
    def _int_or_default(value: Any, fallback: int) -> int:
        return mappers.int_or_default(value, fallback)

    @classmethod
    def _row_to_connector(cls, row: sqlite3.Row | None) -> dict | None:
        return mappers.row_to_connector(row)

    @classmethod
    def _row_to_session(cls, row: sqlite3.Row | None) -> dict | None:
        return mappers.row_to_session(row)

    @classmethod
    def _row_to_event(cls, row: sqlite3.Row | None) -> dict | None:
        return mappers.row_to_event(row)

    @classmethod
    def _row_to_summary(cls, row: sqlite3.Row | None) -> dict | None:
        return mappers.row_to_summary(row)

    @classmethod
    def _row_to_interaction(cls, row: sqlite3.Row | None) -> dict | None:
        return mappers.row_to_interaction(row)

    @classmethod
    def _row_to_director_state(cls, row: sqlite3.Row | None, session_id: str) -> dict:
        return mappers.row_to_director_state(row, session_id)
