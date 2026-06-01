from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any


class PresentationRepositoryMixin:
    def upsert_tts_profile(self, data: dict[str, Any]) -> dict:
        now = datetime.now().isoformat()
        character_id = str(data.get("character_id") or "").strip()
        if not character_id:
            raise ValueError("character_id 不可為空")
        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        with self._lock, self._connect() as conn:
            existing = conn.execute(
                "SELECT created_at FROM live_tts_profiles WHERE character_id = ?",
                (character_id,),
            ).fetchone()
            created_at = existing["created_at"] if existing else now
            conn.execute(
                """
                INSERT INTO live_tts_profiles (
                    character_id, ref_audio_path, prompt_text, text_lang, prompt_lang,
                    speed_factor, media_type, enabled, created_at, updated_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(character_id) DO UPDATE SET
                    ref_audio_path=excluded.ref_audio_path,
                    prompt_text=excluded.prompt_text,
                    text_lang=excluded.text_lang,
                    prompt_lang=excluded.prompt_lang,
                    speed_factor=excluded.speed_factor,
                    media_type=excluded.media_type,
                    enabled=excluded.enabled,
                    updated_at=excluded.updated_at,
                    metadata_json=excluded.metadata_json
                """,
                (
                    character_id,
                    str(data.get("ref_audio_path") or ""),
                    str(data.get("prompt_text") or ""),
                    str(data.get("text_lang") or "zh").lower(),
                    str(data.get("prompt_lang") or "zh").lower(),
                    float(data.get("speed_factor", 1.0) or 1.0),
                    str(data.get("media_type") or "wav").lower(),
                    1 if data.get("enabled", True) else 0,
                    created_at,
                    now,
                    self._json_dump(metadata),
                ),
            )
            conn.commit()
        profile = self.get_tts_profile(character_id)
        if not profile:
            raise RuntimeError("TTS profile 儲存失敗")
        return profile

    def get_tts_profile(self, character_id: str) -> dict | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM live_tts_profiles WHERE character_id = ?",
                (str(character_id or "").strip(),),
            ).fetchone()
        return self._row_to_tts_profile(row)

    def list_tts_profiles(self) -> list[dict]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM live_tts_profiles ORDER BY updated_at DESC, character_id ASC"
            ).fetchall()
        return [profile for row in rows if (profile := self._row_to_tts_profile(row))]

    def create_presentation_item(self, data: dict[str, Any]) -> dict:
        now = datetime.now().isoformat()
        session_id = str(data.get("session_id") or "").strip()
        if not session_id:
            raise ValueError("session_id 不可為空")
        item_id = str(data.get("item_id") or uuid.uuid4())
        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO live_presentation_items (
                    item_id, session_id, interaction_job_id, message_id,
                    character_id, character_name, sequence_index, status,
                    text, audio_path, audio_format, error, created_at, updated_at,
                    presented_at, acked_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item_id,
                    session_id,
                    str(data.get("interaction_job_id") or ""),
                    str(data.get("message_id") or ""),
                    str(data.get("character_id") or ""),
                    str(data.get("character_name") or ""),
                    int(data.get("sequence_index", 0) or 0),
                    str(data.get("status") or "queued"),
                    str(data.get("text") or ""),
                    str(data.get("audio_path") or ""),
                    str(data.get("audio_format") or "wav"),
                    str(data.get("error") or ""),
                    now,
                    now,
                    str(data.get("presented_at") or ""),
                    str(data.get("acked_at") or ""),
                    self._json_dump(metadata),
                ),
            )
            conn.commit()
        item = self.get_presentation_item(item_id)
        if not item:
            raise RuntimeError("presentation item 建立失敗")
        return item

    def get_presentation_item(self, item_id: str) -> dict | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM live_presentation_items WHERE item_id = ?",
                (str(item_id or "").strip(),),
            ).fetchone()
        return self._row_to_presentation_item(row)

    def update_presentation_item(self, item_id: str, **fields) -> dict | None:
        current = self.get_presentation_item(item_id)
        if not current:
            return None
        allowed = {
            "status", "text", "audio_path", "audio_format", "error",
            "presented_at", "acked_at", "metadata",
        }
        updates: dict[str, Any] = {}
        for key, value in fields.items():
            if key in allowed:
                updates[key] = value
        if "metadata" in updates and not isinstance(updates["metadata"], dict):
            updates.pop("metadata", None)
        if not updates:
            return current
        updates["updated_at"] = datetime.now().isoformat()
        columns: list[str] = []
        params: list[Any] = []
        for key, value in updates.items():
            column = "metadata_json" if key == "metadata" else key
            if key == "metadata":
                merged = dict(current.get("metadata") or {})
                merged.update(value)
                value = self._json_dump(merged)
            columns.append(f"{column} = ?")
            params.append(value)
        params.append(str(item_id))
        with self._lock, self._connect() as conn:
            conn.execute(
                f"UPDATE live_presentation_items SET {', '.join(columns)} WHERE item_id = ?",
                params,
            )
            conn.commit()
        return self.get_presentation_item(item_id)

    def list_presentation_items(
        self,
        session_id: str,
        *,
        statuses: set[str] | list[str] | tuple[str, ...] | None = None,
        limit: int = 100,
    ) -> list[dict]:
        limit = max(1, min(int(limit or 100), 500))
        params: list[Any] = [session_id]
        status_filter = ""
        if statuses:
            clean = [str(status) for status in statuses if str(status).strip()]
            if clean:
                status_filter = f" AND status IN ({','.join('?' for _ in clean)})"
                params.extend(clean)
        params.append(limit)
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM live_presentation_items
                WHERE session_id = ?
                {status_filter}
                ORDER BY id ASC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [item for row in rows if (item := self._row_to_presentation_item(row))]

    def list_unacked_failed_presentation_items(
        self,
        session_id: str,
        *,
        limit: int = 500,
    ) -> list[dict]:
        limit = max(1, min(int(limit or 500), 500))
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM live_presentation_items
                WHERE session_id = ?
                  AND status = 'failed'
                  AND COALESCE(acked_at, '') = ''
                ORDER BY id ASC
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()
        return [item for row in rows if (item := self._row_to_presentation_item(row))]

    def list_presented_messages(self, session_id: str, *, limit: int = 120) -> list[dict]:
        items = self.list_presentation_items(
            session_id,
            statuses={"presenting", "played", "failed"},
            limit=500,
        )
        items.sort(key=lambda item: (
            item.get("presented_at") or item.get("created_at") or "",
            int(item.get("id") or 0),
        ))
        messages: list[dict] = []
        for item in items:
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            messages.append({
                "message_id": item.get("message_id") or item.get("item_id"),
                "role": "assistant",
                "content": text,
                "created_at": item.get("presented_at") or item.get("created_at") or "",
                "timestamp": item.get("presented_at") or item.get("created_at") or "",
                "character_id": item.get("character_id"),
                "character_name": item.get("character_name"),
                "turn_index": item.get("sequence_index"),
                "source": "presentation",
            })
        return messages[-limit:]
