from __future__ import annotations

from datetime import datetime
from typing import Any

from live_episode_plan_contract import validate_live_episode_plan


class EpisodePlanRepositoryMixin:
    def upsert_live_episode_plan(
        self,
        plan_json: dict[str, Any],
        *,
        source_path: str = "",
    ) -> dict:
        plan = validate_live_episode_plan(plan_json)
        now = datetime.now().isoformat()
        existing = self.get_live_episode_plan(str(plan["plan_id"]))
        created_at = existing["created_at"] if existing else now
        show_format = plan.get("show_format") if isinstance(plan.get("show_format"), dict) else {}
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO live_episode_plans (
                    plan_id, schema_version, title, language, show_format_json,
                    plan_json, source_path, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(plan_id) DO UPDATE SET
                    schema_version=excluded.schema_version,
                    title=excluded.title,
                    language=excluded.language,
                    show_format_json=excluded.show_format_json,
                    plan_json=excluded.plan_json,
                    source_path=excluded.source_path,
                    updated_at=excluded.updated_at
                """,
                (
                    str(plan["plan_id"]),
                    str(plan["schema_version"]),
                    str(plan["title"]),
                    str(plan.get("language") or "zh-TW"),
                    self._json_dump(show_format),
                    self._json_dump(plan),
                    str(source_path or ""),
                    created_at,
                    now,
                ),
            )
            conn.commit()
        saved = self.get_live_episode_plan(str(plan["plan_id"]))
        if not saved:
            raise RuntimeError("episode plan 儲存失敗")
        return saved

    def get_live_episode_plan(self, plan_id: str) -> dict | None:
        plan_id = str(plan_id or "").strip()
        if not plan_id:
            return None
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM live_episode_plans WHERE plan_id = ?",
                (plan_id,),
            ).fetchone()
        return self._row_to_episode_plan(row)

    def list_live_episode_plans(self, *, limit: int = 100) -> list[dict]:
        limit = max(1, min(int(limit or 100), 500))
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM live_episode_plans ORDER BY updated_at DESC, plan_id LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            item
            for row in rows
            if (item := self._row_to_episode_plan(row)) is not None
        ]

    def delete_live_episode_plan(self, plan_id: str) -> bool:
        plan_id = str(plan_id or "").strip()
        if not plan_id:
            return False
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE live_sessions SET episode_plan_id = '' WHERE episode_plan_id = ?",
                (plan_id,),
            )
            cursor = conn.execute(
                "DELETE FROM live_episode_plans WHERE plan_id = ?",
                (plan_id,),
            )
            conn.commit()
        return cursor.rowcount > 0

    def bind_episode_plan_to_session(self, session_id: str, plan_id: str) -> dict:
        if not self.get_session(session_id):
            raise ValueError("live session 不存在")
        plan_id = str(plan_id or "").strip()
        if not self.get_live_episode_plan(plan_id):
            raise ValueError("episode plan 不存在")
        updated = self.update_session_fields(session_id, episode_plan_id=plan_id)
        if not updated:
            raise RuntimeError("episode plan 綁定失敗")
        return updated

    def unbind_episode_plan_from_session(self, session_id: str) -> dict:
        if not self.get_session(session_id):
            raise ValueError("live session 不存在")
        updated = self.update_session_fields(session_id, episode_plan_id="")
        if not updated:
            raise RuntimeError("episode plan 解除綁定失敗")
        return updated
