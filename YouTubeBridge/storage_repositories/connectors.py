from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta
from typing import Any

from storage_constants import DEFAULT_CONNECTOR_ID, DEFAULT_CONNECTOR_NAME
from storage_event_utils import infer_super_chat_tier


class ConnectorRepositoryMixin:
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

