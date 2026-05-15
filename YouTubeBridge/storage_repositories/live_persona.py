from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any


class LivePersonaRepositoryMixin:
    @classmethod
    def _row_to_live_persona_overlay(cls, row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        return {
            "character_id": row["character_id"],
            "enabled": bool(row["enabled"]),
            "mode": row["mode"] or "replace",
            "system_prompt": row["system_prompt"] or "",
            "self_address": row["self_address"] or "",
            "addressing": cls._json_load(row["addressing_json"], {}),
            "opening_intro": row["opening_intro"] or "",
            "reply_rules": row["reply_rules"] or "",
            "avatar_url": row["avatar_url"] or "",
            "chat_background_color": row["chat_background_color"] or "",
            "chat_accent_color": row["chat_accent_color"] or "",
            "created_at": row["created_at"] or "",
            "updated_at": row["updated_at"] or "",
        }

    @staticmethod
    def _clean_chat_color(value: Any) -> str:
        color = str(value or "").strip()
        if not color:
            return ""
        if len(color) != 7 or not color.startswith("#"):
            raise ValueError("角色色盤必須是 #RRGGBB 格式")
        try:
            int(color[1:], 16)
        except ValueError as exc:
            raise ValueError("角色色盤必須是 #RRGGBB 格式") from exc
        return color.lower()

    @staticmethod
    def _clean_overlay_payload(character_id: str, data: dict[str, Any]) -> dict[str, Any]:
        clean_id = str(character_id or "").strip()
        if not clean_id:
            raise ValueError("character_id 不可為空")
        raw_mode = str(data.get("mode") or "replace").strip()
        mode = raw_mode if raw_mode in {"replace", "append"} else "replace"
        addressing_raw = data.get("addressing") if isinstance(data.get("addressing"), dict) else {}
        addressing = {
            str(key).strip()[:120]: str(value).strip()[:120]
            for key, value in addressing_raw.items()
            if str(key).strip() and str(value).strip()
        }
        return {
            "character_id": clean_id[:120],
            "enabled": bool(data.get("enabled", False)),
            "mode": mode,
            "system_prompt": str(data.get("system_prompt") or "").replace("\r", "\n").strip()[:8000],
            "self_address": str(data.get("self_address") or "").strip()[:120],
            "addressing": addressing,
            "opening_intro": str(data.get("opening_intro") or "").replace("\r", "\n").strip()[:1200],
            "reply_rules": str(data.get("reply_rules") or "").replace("\r", "\n").strip()[:2000],
            "avatar_url": str(data.get("avatar_url") or "").strip()[:1000],
            "chat_background_color": LivePersonaRepositoryMixin._clean_chat_color(data.get("chat_background_color")),
            "chat_accent_color": LivePersonaRepositoryMixin._clean_chat_color(data.get("chat_accent_color")),
        }

    def upsert_live_persona_overlay(self, character_id: str, data: dict[str, Any]) -> dict:
        payload = self._clean_overlay_payload(character_id, data)
        now = datetime.now().isoformat()
        existing = self.get_live_persona_overlay(payload["character_id"])
        created_at = existing["created_at"] if existing else now
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO live_persona_overlays (
                    character_id, enabled, mode, system_prompt, self_address,
                    addressing_json, opening_intro, reply_rules, avatar_url,
                    chat_background_color, chat_accent_color, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(character_id) DO UPDATE SET
                    enabled=excluded.enabled,
                    mode=excluded.mode,
                    system_prompt=excluded.system_prompt,
                    self_address=excluded.self_address,
                    addressing_json=excluded.addressing_json,
                    opening_intro=excluded.opening_intro,
                    reply_rules=excluded.reply_rules,
                    avatar_url=excluded.avatar_url,
                    chat_background_color=excluded.chat_background_color,
                    chat_accent_color=excluded.chat_accent_color,
                    updated_at=excluded.updated_at
                """,
                (
                    payload["character_id"],
                    1 if payload["enabled"] else 0,
                    payload["mode"],
                    payload["system_prompt"],
                    payload["self_address"],
                    self._json_dump(payload["addressing"]),
                    payload["opening_intro"],
                    payload["reply_rules"],
                    payload["avatar_url"],
                    payload["chat_background_color"],
                    payload["chat_accent_color"],
                    created_at,
                    now,
                ),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM live_persona_overlays WHERE character_id = ?",
                (payload["character_id"],),
            ).fetchone()
        overlay = self._row_to_live_persona_overlay(row)
        if not overlay:
            raise RuntimeError("live persona overlay 儲存失敗")
        return overlay

    def get_live_persona_overlay(self, character_id: str) -> dict | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM live_persona_overlays WHERE character_id = ?",
                (str(character_id or "").strip(),),
            ).fetchone()
        return self._row_to_live_persona_overlay(row)

    def list_live_persona_overlays(self) -> list[dict]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM live_persona_overlays ORDER BY updated_at DESC, character_id ASC"
            ).fetchall()
        return [overlay for row in rows if (overlay := self._row_to_live_persona_overlay(row))]

    def live_persona_prompt_overrides_for(self, character_ids: list[str]) -> dict[str, dict]:
        ids = [str(character_id or "").strip() for character_id in character_ids if str(character_id or "").strip()]
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM live_persona_overlays
                WHERE enabled = 1 AND character_id IN ({placeholders})
                ORDER BY character_id ASC
                """,
                ids,
            ).fetchall()
        overrides: dict[str, dict] = {}
        for row in rows:
            overlay = self._row_to_live_persona_overlay(row)
            if not overlay or not overlay.get("system_prompt"):
                continue
            overrides[overlay["character_id"]] = {
                "enabled": True,
                "mode": overlay.get("mode") or "replace",
                "system_prompt": overlay.get("system_prompt") or "",
                "self_address": overlay.get("self_address") or "",
                "addressing": overlay.get("addressing") or {},
                "opening_intro": overlay.get("opening_intro") or "",
                "reply_rules": overlay.get("reply_rules") or "",
            }
        return overrides
