# 【環境假設】：Python 3.12, numpy 庫可用。使用內建 sqlite3。支援 Schema Evolution。
from __future__ import annotations

import os

import numpy as np


class CoreMemoryRepositoryMixin:
    # SECTION: 核心認知 Core Memory — 載入 / Upsert / 刪除
    # ════════════════════════════════════════════════════════════

    def load_core_db(
        self,
        db_path,
        user_id: str = "default",
        character_id: str = "default",
        visibility_filter=None,
    ):
        if not os.path.exists(db_path):
            return []
        conn = self._init_db(db_path)
        cursor = conn.cursor()

        where_parts = ["user_id = ?", "character_id = ?"]
        params: list = [user_id, character_id]
        if visibility_filter is not None:
            placeholders = ','.join('?' * len(visibility_filter))
            where_parts.append(f"visibility IN ({placeholders})")
            params.extend(visibility_filter)
        where_clause = " WHERE " + " AND ".join(where_parts)

        cursor.execute(
            "SELECT core_id, timestamp, insight, insight_vector, encounter_count "
            f"FROM core_memories{where_clause}",
            params
        )
        rows = cursor.fetchall()
        core_memories = []
        for row in rows:
            core_id, timestamp, insight, insight_vector_blob, encounter_count = row
            insight_vector = np.frombuffer(insight_vector_blob, dtype=np.float32).tolist()
            core_memories.append({
                "core_id": core_id,
                "timestamp": timestamp,
                "insight": insight,
                "insight_vector": insight_vector,
                "encounter_count": float(encounter_count) if encounter_count is not None else 1.0,
            })
        conn.close()
        return core_memories

    def save_core_memory(
        self,
        db_path,
        core_id,
        timestamp,
        insight,
        insight_vector,
        encounter_count=1.0,
        user_id: str = "default",
        character_id: str = "default",
        visibility: str = "public",
    ):
        conn = self._init_db(db_path)
        cursor = conn.cursor()
        vector_blob = np.array(insight_vector, dtype=np.float32).tobytes()
        cursor.execute(
            "INSERT OR REPLACE INTO core_memories "
            "(core_id, timestamp, insight, insight_vector, encounter_count, "
            " user_id, character_id, visibility) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (core_id, timestamp, insight, vector_blob, encounter_count,
             user_id, character_id, visibility)
        )
        conn.commit()
        conn.close()

    def delete_core_memory(
        self,
        db_path,
        user_id: str,
        character_id: str,
        core_id: str,
        visibility: str | None = None,
    ):
        """刪除指定 core memory（含 user_id / character_id 範圍驗證）。"""
        conn = self._init_db(db_path)
        cursor = conn.cursor()
        params: list = [core_id, user_id, character_id]
        where = "core_id = ? AND user_id = ? AND character_id = ?"
        if visibility is not None:
            where += " AND visibility = ?"
            params.append(visibility)
        cursor.execute(f"DELETE FROM core_memories WHERE {where}", params)
        deleted = cursor.rowcount
        conn.commit()
        conn.close()
        return int(deleted if deleted is not None else 0)

    # ════════════════════════════════════════════════════════════


__all__ = ["CoreMemoryRepositoryMixin"]
