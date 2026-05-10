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


class TopicCacheRepositoryMixin:
    # SECTION: 主動話題 Topic Cache — 插入 / 查未提及 / 標記已提
    # ════════════════════════════════════════════════════════════

    def insert_topic_cache(
        self,
        db_path,
        topic_id,
        interest_keyword,
        summary_content,
        user_id: str = "default",
        character_id: str = "default",
        visibility: str = "public",
    ):
        conn = self._init_db(db_path)
        cursor = conn.cursor()
        now = datetime.now().isoformat()
        cursor.execute(
            "INSERT OR REPLACE INTO topic_cache "
            "(topic_id, interest_keyword, summary_content, created_at, is_mentioned_to_user, "
            " user_id, character_id, visibility) "
            "VALUES (?, ?, ?, ?, 0, ?, ?, ?)",
            (topic_id, interest_keyword, summary_content, now, user_id, character_id, visibility)
        )
        conn.commit()
        conn.close()

    def get_unmentioned_topics(
        self,
        db_path,
        limit=3,
        user_id: str = "default",
        character_id: str = "default",
        visibility_filter=None,
        include_global: bool = False,
    ):
        if not os.path.exists(db_path):
            return []
        conn = self._init_db(db_path)
        cursor = conn.cursor()
        where_parts = ["is_mentioned_to_user = 0", "user_id = ?"]
        params: list = [user_id]
        if include_global:
            where_parts.append("character_id IN (?, ?)")
            params.extend([character_id, GLOBAL_TOPIC_CHARACTER_ID])
        else:
            where_parts.append("character_id = ?")
            params.append(character_id)
        if visibility_filter is not None:
            placeholders = ','.join('?' * len(visibility_filter))
            where_parts.append(f"visibility IN ({placeholders})")
            params.extend(visibility_filter)
        where_clause = " WHERE " + " AND ".join(where_parts)
        query_params = params + [character_id, limit]
        cursor.execute(
            "SELECT topic_id, interest_keyword, summary_content, created_at "
            f"FROM topic_cache{where_clause} "
            "ORDER BY CASE WHEN character_id = ? THEN 0 ELSE 1 END, created_at DESC LIMIT ?",
            query_params,
        )
        rows = cursor.fetchall()
        conn.close()
        return [
            {"topic_id": r[0], "interest_keyword": r[1],
             "summary_content": r[2], "created_at": r[3]}
            for r in rows
        ]

    def mark_topic_mentioned(self, db_path, topic_id):
        conn = self._init_db(db_path)
        cursor = conn.cursor()
        cursor.execute(
            'UPDATE topic_cache SET is_mentioned_to_user = 1 WHERE topic_id = ?', (topic_id,)
        )
        conn.commit()
        conn.close()

    # ════════════════════════════════════════════════════════════


__all__ = ["TopicCacheRepositoryMixin"]
