# 【環境假設】：Python 3.12, numpy 庫可用。使用內建 sqlite3。支援 Schema Evolution。
from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timedelta

import numpy as np

from core.runtime_paths import runtime_file
from core.storage.constants import (
    DEFAULT_SYSTEM_PROMPT,
    GLOBAL_TOPIC_CHARACTER_ID,
    MAINTENANCE_DROP_TABLE_ALLOWLIST,
    SHARED_MEMORY_CHARACTER_ID,
    SHARED_MEMORY_USER_ID,
)


class MemoryInspectRepositoryMixin:
    # SECTION: 管理檢視 Read-only Inspect — Scope-aware 查詢
    # ════════════════════════════════════════════════════════════

    def _connect_existing_memory_db(self, db_path):
        if not db_path or not os.path.exists(db_path):
            return None
        return sqlite3.connect(db_path, timeout=15.0)

    @staticmethod
    def _table_columns(cursor, table_name: str) -> set[str]:
        try:
            cursor.execute(f"PRAGMA table_info({table_name})")
            return {row[1] for row in cursor.fetchall()}
        except sqlite3.Error:
            return set()

    @staticmethod
    def _scope_expr(columns: set[str], column: str, default: str) -> str:
        if column in columns:
            return f"COALESCE({column}, '{default}')"
        return f"'{default}'"

    @staticmethod
    def _json_loads(value, fallback):
        if not value:
            return fallback
        try:
            return json.loads(value)
        except Exception:
            return fallback

    @staticmethod
    def _limit_value(limit: int | None, default: int = 200, maximum: int = 1000) -> int:
        try:
            value = int(limit if limit is not None else default)
        except (TypeError, ValueError):
            value = default
        return max(1, min(value, maximum))

    def _visibility_condition(self, vis_expr: str, visibility_filter, params: list) -> str | None:
        if visibility_filter is None:
            return None
        values = [v for v in visibility_filter if v]
        if not values:
            return None
        placeholders = ",".join("?" * len(values))
        params.extend(values)
        return f"{vis_expr} IN ({placeholders})"

    def inspect_memory_scopes(self, db_path) -> dict:
        """回傳 runtime memory DB 內實際存在的 user / character / visibility 與分表統計。"""
        conn = self._connect_existing_memory_db(db_path)
        if conn is None:
            return {
                "user_ids": [],
                "character_ids": [],
                "visibilities": [],
                "counts": {
                    "memory_blocks": [],
                    "core_memories": [],
                    "user_profile": [],
                    "topic_cache": [],
                },
            }

        cursor = conn.cursor()
        user_ids: set[str] = set()
        character_ids: set[str] = set()
        visibilities: set[str] = set()
        counts: dict[str, list[dict]] = {
            "memory_blocks": [],
            "core_memories": [],
            "user_profile": [],
            "topic_cache": [],
        }

        def group_scoped_table(table_name: str, has_character: bool) -> None:
            columns = self._table_columns(cursor, table_name)
            if not columns:
                return
            user_expr = self._scope_expr(columns, "user_id", "default")
            vis_expr = self._scope_expr(columns, "visibility", "public")
            if has_character:
                char_expr = self._scope_expr(columns, "character_id", "default")
                select_sql = (
                    f"SELECT {user_expr}, {char_expr}, {vis_expr}, COUNT(*) "
                    f"FROM {table_name} GROUP BY 1, 2, 3 ORDER BY 1, 2, 3"
                )
            else:
                select_sql = (
                    f"SELECT {user_expr}, {vis_expr}, COUNT(*) "
                    f"FROM {table_name} GROUP BY 1, 2 ORDER BY 1, 2"
                )
            try:
                cursor.execute(select_sql)
            except sqlite3.Error:
                return
            for row in cursor.fetchall():
                if has_character:
                    user_id, character_id, visibility, count = row
                    character_id = str(character_id or "default")
                    character_ids.add(character_id)
                    counts[table_name].append({
                        "user_id": str(user_id or "default"),
                        "character_id": character_id,
                        "visibility": str(visibility or "public"),
                        "count": int(count),
                    })
                else:
                    user_id, visibility, count = row
                    counts[table_name].append({
                        "user_id": str(user_id or "default"),
                        "visibility": str(visibility or "public"),
                        "count": int(count),
                    })
                user_ids.add(str(user_id or "default"))
                visibilities.add(str(visibility or "public"))

        group_scoped_table("memory_blocks", has_character=True)
        group_scoped_table("core_memories", has_character=True)
        group_scoped_table("user_profile", has_character=False)
        group_scoped_table("topic_cache", has_character=True)

        conn.close()
        return {
            "user_ids": sorted(user_ids),
            "character_ids": sorted(character_ids),
            "visibilities": sorted(visibilities),
            "counts": counts,
        }

    def inspect_memory_blocks(
        self, db_path, user_id: str, character_id: str,
        visibility_filter=None, limit: int = 200, include_dialogues: bool = False,
    ) -> list[dict]:
        conn = self._connect_existing_memory_db(db_path)
        if conn is None:
            return []
        cursor = conn.cursor()
        columns = self._table_columns(cursor, "memory_blocks")
        if not columns:
            conn.close()
            return []

        user_expr = self._scope_expr(columns, "user_id", "default")
        char_expr = self._scope_expr(columns, "character_id", "default")
        vis_expr = self._scope_expr(columns, "visibility", "public")
        params: list = [user_id, character_id]
        where_parts = [f"{user_expr} = ?", f"{char_expr} = ?"]
        vis_condition = self._visibility_condition(vis_expr, visibility_filter, params)
        if vis_condition:
            where_parts.append(vis_condition)
        params.append(self._limit_value(limit))

        try:
            cursor.execute(
                "SELECT block_id, timestamp, overview, is_consolidated, encounter_count, "
                f"potential_preferences, raw_dialogues, {user_expr}, {char_expr}, {vis_expr} "
                "FROM memory_blocks "
                f"WHERE {' AND '.join(where_parts)} "
                "ORDER BY timestamp DESC LIMIT ?",
                params,
            )
            rows = cursor.fetchall()
        except sqlite3.Error:
            rows = []
        conn.close()

        results = []
        for row in rows:
            item = {
                "block_id": row[0],
                "timestamp": row[1],
                "overview": row[2],
                "is_consolidated": bool(row[3]),
                "encounter_count": float(row[4] if row[4] is not None else 1.0),
                "potential_preferences": self._json_loads(row[5], []),
                "user_id": str(row[7] or "default"),
                "character_id": str(row[8] or "default"),
                "visibility": str(row[9] or "public"),
            }
            if include_dialogues:
                item["raw_dialogues"] = self._json_loads(row[6], [])
            results.append(item)
        return results

    def inspect_core_memories(
        self, db_path, user_id: str, character_id: str,
        visibility_filter=None, limit: int = 200,
    ) -> list[dict]:
        conn = self._connect_existing_memory_db(db_path)
        if conn is None:
            return []
        cursor = conn.cursor()
        columns = self._table_columns(cursor, "core_memories")
        if not columns:
            conn.close()
            return []

        user_expr = self._scope_expr(columns, "user_id", "default")
        char_expr = self._scope_expr(columns, "character_id", "default")
        vis_expr = self._scope_expr(columns, "visibility", "public")
        params: list = [user_id, character_id]
        where_parts = [f"{user_expr} = ?", f"{char_expr} = ?"]
        vis_condition = self._visibility_condition(vis_expr, visibility_filter, params)
        if vis_condition:
            where_parts.append(vis_condition)
        params.append(self._limit_value(limit))

        try:
            cursor.execute(
                f"SELECT core_id, timestamp, insight, encounter_count, {user_expr}, {char_expr}, {vis_expr} "
                "FROM core_memories "
                f"WHERE {' AND '.join(where_parts)} "
                "ORDER BY timestamp DESC LIMIT ?",
                params,
            )
            rows = cursor.fetchall()
        except sqlite3.Error:
            rows = []
        conn.close()
        return [
            {
                "core_id": r[0],
                "timestamp": r[1],
                "insight": r[2],
                "encounter_count": float(r[3] if r[3] is not None else 1.0),
                "user_id": str(r[4] or "default"),
                "character_id": str(r[5] or "default"),
                "visibility": str(r[6] or "public"),
            }
            for r in rows
        ]

    def inspect_profiles(
        self, db_path, user_id: str, visibility_filter=None,
        include_tombstones: bool = False, limit: int = 200,
    ) -> list[dict]:
        conn = self._connect_existing_memory_db(db_path)
        if conn is None:
            return []
        cursor = conn.cursor()
        columns = self._table_columns(cursor, "user_profile")
        if not columns:
            conn.close()
            return []

        user_expr = self._scope_expr(columns, "user_id", "default")
        vis_expr = self._scope_expr(columns, "visibility", "public")
        params: list = [user_id]
        where_parts = [f"{user_expr} = ?"]
        if not include_tombstones:
            where_parts.append("confidence >= 0")
        vis_condition = self._visibility_condition(vis_expr, visibility_filter, params)
        if vis_condition:
            where_parts.append(vis_condition)
        params.append(self._limit_value(limit))

        try:
            cursor.execute(
                "SELECT fact_key, fact_value, category, confidence, timestamp, source_context, "
                f"{user_expr}, {vis_expr} "
                "FROM user_profile "
                f"WHERE {' AND '.join(where_parts)} "
                "ORDER BY timestamp DESC, category, fact_key LIMIT ?",
                params,
            )
            rows = cursor.fetchall()
        except sqlite3.Error:
            rows = []
        conn.close()
        return [
            {
                "fact_key": r[0],
                "fact_value": r[1],
                "category": r[2],
                "confidence": float(r[3] if r[3] is not None else 1.0),
                "timestamp": r[4],
                "source_context": r[5],
                "user_id": str(r[6] or "default"),
                "visibility": str(r[7] or "public"),
            }
            for r in rows
        ]

    def inspect_topics(
        self, db_path, user_id: str, character_id: str,
        visibility_filter=None, include_global: bool = False,
        only_unmentioned: bool = False, limit: int = 200,
    ) -> list[dict]:
        conn = self._connect_existing_memory_db(db_path)
        if conn is None:
            return []
        cursor = conn.cursor()
        columns = self._table_columns(cursor, "topic_cache")
        if not columns:
            conn.close()
            return []

        user_expr = self._scope_expr(columns, "user_id", "default")
        char_expr = self._scope_expr(columns, "character_id", "default")
        vis_expr = self._scope_expr(columns, "visibility", "public")
        params: list = [user_id]
        where_parts = [f"{user_expr} = ?"]
        if include_global:
            where_parts.append(f"{char_expr} IN (?, ?)")
            params.extend([character_id, GLOBAL_TOPIC_CHARACTER_ID])
        else:
            where_parts.append(f"{char_expr} = ?")
            params.append(character_id)
        if only_unmentioned:
            where_parts.append("is_mentioned_to_user = 0")
        vis_condition = self._visibility_condition(vis_expr, visibility_filter, params)
        if vis_condition:
            where_parts.append(vis_condition)
        params.append(self._limit_value(limit))

        try:
            cursor.execute(
                "SELECT topic_id, interest_keyword, summary_content, created_at, "
                f"is_mentioned_to_user, {user_expr}, {char_expr}, {vis_expr} "
                "FROM topic_cache "
                f"WHERE {' AND '.join(where_parts)} "
                "ORDER BY created_at DESC LIMIT ?",
                params,
            )
            rows = cursor.fetchall()
        except sqlite3.Error:
            rows = []
        conn.close()
        return [
            {
                "topic_id": r[0],
                "interest_keyword": r[1],
                "summary_content": r[2],
                "created_at": r[3],
                "is_mentioned_to_user": bool(r[4]),
                "user_id": str(r[5] or "default"),
                "character_id": str(r[6] or "default"),
                "visibility": str(r[7] or "public"),
            }
            for r in rows
        ]

    def delete_topic_cache(
        self,
        db_path,
        user_id: str,
        character_id: str,
        visibility: str,
        topic_id: str,
    ) -> int:
        """精準刪除單一主動話題快取，限定完整隔離 scope。"""
        conn = self._init_db(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM topic_cache "
            "WHERE topic_id = ? AND user_id = ? AND character_id = ? AND visibility = ?",
            (topic_id, user_id, character_id, visibility),
        )
        deleted = cursor.rowcount
        conn.commit()
        conn.close()
        return int(deleted if deleted is not None else 0)

    def inspect_maintenance_tables(self, db_path) -> list[dict]:
        """列出允許由維護模式 drop 的舊表與目前筆數。"""
        conn = self._connect_existing_memory_db(db_path)
        if conn is None:
            return []
        cursor = conn.cursor()
        rows: list[dict] = []
        for table_name in sorted(MAINTENANCE_DROP_TABLE_ALLOWLIST):
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
                (table_name,),
            )
            exists = cursor.fetchone() is not None
            count = 0
            if exists:
                try:
                    cursor.execute(f'SELECT COUNT(*) FROM "{table_name}"')
                    count = int(cursor.fetchone()[0])
                except sqlite3.Error:
                    count = 0
            rows.append({"table_name": table_name, "exists": exists, "count": count})
        conn.close()
        return rows

    def drop_maintenance_table(self, db_path, table_name: str) -> bool:
        """Drop allowlist 中的舊表。不可用於任意 SQL 或核心資料表。"""
        if table_name not in MAINTENANCE_DROP_TABLE_ALLOWLIST:
            raise ValueError(f"table not allowed: {table_name}")
        conn = self._connect_existing_memory_db(db_path)
        if conn is None:
            return False
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
            (table_name,),
        )
        exists = cursor.fetchone() is not None
        if exists:
            cursor.execute(f'DROP TABLE "{table_name}"')
            conn.commit()
        conn.close()
        return exists

    # ════════════════════════════════════════════════════════════


__all__ = ["MemoryInspectRepositoryMixin"]
