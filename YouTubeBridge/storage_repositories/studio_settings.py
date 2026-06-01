from __future__ import annotations

from datetime import datetime
from typing import Any


class StudioSettingsRepositoryMixin:
    def get_studio_settings(self, section: str) -> dict[str, Any]:
        clean_section = str(section or "").strip()
        if not clean_section:
            return {}
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM studio_settings WHERE section = ?",
                (clean_section,),
            ).fetchone()
        if not row:
            return {}
        payload = self._json_load(row["payload_json"], {})
        return payload if isinstance(payload, dict) else {}

    def get_all_studio_settings(self) -> dict[str, dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT section, payload_json FROM studio_settings ORDER BY section ASC"
            ).fetchall()
        output: dict[str, dict[str, Any]] = {}
        for row in rows:
            payload = self._json_load(row["payload_json"], {})
            output[str(row["section"])] = payload if isinstance(payload, dict) else {}
        return output

    def upsert_studio_settings(self, section: str, payload: dict[str, Any]) -> dict[str, Any]:
        clean_section = str(section or "").strip()
        if not clean_section:
            raise ValueError("studio settings section 不可為空")
        clean_payload = payload if isinstance(payload, dict) else {}
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            existing = conn.execute(
                "SELECT created_at FROM studio_settings WHERE section = ?",
                (clean_section,),
            ).fetchone()
            created_at = existing["created_at"] if existing else now
            conn.execute(
                """
                INSERT INTO studio_settings (section, payload_json, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(section) DO UPDATE SET
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at
                """,
                (clean_section, self._json_dump(clean_payload), created_at, now),
            )
            conn.commit()
        return self.get_studio_settings(clean_section)
