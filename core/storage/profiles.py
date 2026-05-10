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


class ProfileRepositoryMixin:
    # SECTION: 使用者偏好 Profile Facts — Upsert / 刪除 / 查詢 / 向量
    # ════════════════════════════════════════════════════════════

    def upsert_profile(
        self,
        db_path,
        fact_key,
        fact_value,
        category,
        source_context="",
        confidence=1.0,
        user_id: str = "default",
        visibility: str = "public",
    ):
        """新增或更新一筆使用者事實。user_id + visibility 決定資料歸屬。"""
        conn = self._init_db(db_path)
        cursor = conn.cursor()
        timestamp = datetime.now().isoformat()
        cursor.execute(
            "INSERT OR REPLACE INTO user_profile "
            "(user_id, fact_key, fact_value, category, confidence, timestamp, source_context, visibility) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, fact_key, fact_value, category, confidence, timestamp, source_context, visibility)
        )
        conn.commit()
        conn.close()

    def upsert_profile_vector(
        self,
        db_path,
        fact_key,
        fact_value,
        fact_vector,
        user_id: str = "default",
    ):
        """新增或更新一筆使用者事實的向量（複合鍵：user_id + fact_key + fact_value）。"""
        conn = self._init_db(db_path)
        cursor = conn.cursor()
        vector_blob = np.array(fact_vector, dtype=np.float32).tobytes()
        cursor.execute(
            "INSERT OR REPLACE INTO user_profile_vectors (user_id, fact_key, fact_value, fact_vector) "
            "VALUES (?, ?, ?, ?)",
            (user_id, fact_key, fact_value, vector_blob)
        )
        conn.commit()
        conn.close()

    def delete_profile(
        self,
        db_path,
        fact_key,
        fact_value=None,
        user_id: str = "default",
        visibility: str | None = None,
    ):
        """刪除使用者事實（同時清除向量）。

        ⚠️ 高風險操作：user_id 必須傳入，防止誤刪其他用戶同 fact_key 的資料。
        若指定 fact_value 則精準刪除單筆，否則刪除該 user_id + key 下所有值。
        """
        conn = self._init_db(db_path)
        cursor = conn.cursor()
        profile_where = "user_id = ? AND fact_key = ?"
        profile_params: list = [user_id, fact_key]
        if fact_value is not None:
            profile_where += " AND fact_value = ?"
            profile_params.append(fact_value)
        if visibility is not None:
            profile_where += " AND visibility = ?"
            profile_params.append(visibility)

        cursor.execute(
            f"SELECT fact_value FROM user_profile WHERE {profile_where}",
            profile_params,
        )
        deleted_values = [row[0] for row in cursor.fetchall()]
        cursor.execute(f"DELETE FROM user_profile WHERE {profile_where}", profile_params)
        deleted = cursor.rowcount

        for value in deleted_values:
            cursor.execute(
                "DELETE FROM user_profile_vectors "
                "WHERE user_id = ? AND fact_key = ? AND fact_value = ?",
                (user_id, fact_key, value),
            )
        conn.commit()
        conn.close()
        return int(deleted if deleted is not None else 0)

    def load_all_profiles(
        self,
        db_path,
        include_tombstones=False,
        user_id: str = "default",
        visibility_filter=None,
    ):
        """載入所有使用者事實（預設排除墓碑記錄 confidence < 0）。"""
        if not os.path.exists(db_path):
            return []
        conn = self._init_db(db_path)
        cursor = conn.cursor()
        where_parts = ["user_id = ?"]
        params: list = [user_id]
        if not include_tombstones:
            where_parts.append("confidence >= 0")
        if visibility_filter is not None:
            placeholders = ','.join('?' * len(visibility_filter))
            where_parts.append(f"visibility IN ({placeholders})")
            params.extend(visibility_filter)
        where_clause = " WHERE " + " AND ".join(where_parts)
        cursor.execute(
            "SELECT fact_key, fact_value, category, confidence, timestamp, source_context "
            f"FROM user_profile{where_clause} ORDER BY category, fact_key",
            params
        )
        rows = cursor.fetchall()
        conn.close()
        return [
            {"fact_key": r[0], "fact_value": r[1], "category": r[2],
             "confidence": r[3], "timestamp": r[4], "source_context": r[5]}
            for r in rows
        ]

    def load_profiles_by_category(
        self,
        db_path,
        category,
        user_id: str = "default",
        visibility_filter=None,
    ):
        """按分類篩選使用者事實（自動排除墓碑記錄）。"""
        if not os.path.exists(db_path):
            return []
        conn = self._init_db(db_path)
        cursor = conn.cursor()
        where_parts = ["user_id = ?", "category = ?", "confidence >= 0"]
        params: list = [user_id, category]
        if visibility_filter is not None:
            placeholders = ','.join('?' * len(visibility_filter))
            where_parts.append(f"visibility IN ({placeholders})")
            params.extend(visibility_filter)
        where_clause = " WHERE " + " AND ".join(where_parts)
        cursor.execute(
            "SELECT fact_key, fact_value, category, confidence, timestamp, source_context "
            f"FROM user_profile{where_clause}",
            params
        )
        rows = cursor.fetchall()
        conn.close()
        return [
            {"fact_key": r[0], "fact_value": r[1], "category": r[2],
             "confidence": r[3], "timestamp": r[4], "source_context": r[5]}
            for r in rows
        ]

    def load_profile_vectors(
        self,
        db_path,
        user_id: str = "default",
        visibility_filter: "list[str] | None" = None,
    ):
        """載入使用者事實及其向量（自動排除墓碑記錄，供語意搜尋用）。

        visibility_filter: None → 不限（供寫入去重使用）；
                           ['public'] / ['private', 'public'] → 限定 visibility（供讀取檢索使用）。
        """
        if not os.path.exists(db_path):
            return []
        conn = self._init_db(db_path)
        cursor = conn.cursor()
        where = "WHERE p.user_id = ? AND p.confidence >= 0"
        params: list = [user_id]
        if visibility_filter:
            placeholders = ", ".join("?" * len(visibility_filter))
            where += f" AND p.visibility IN ({placeholders})"
            params.extend(visibility_filter)
        cursor.execute(
            "SELECT p.fact_key, p.fact_value, p.category, p.confidence, v.fact_vector "
            "FROM user_profile p "
            "LEFT JOIN user_profile_vectors v "
            "  ON p.user_id = v.user_id AND p.fact_key = v.fact_key AND p.fact_value = v.fact_value "
            f"{where}",
            params,
        )
        rows = cursor.fetchall()
        conn.close()
        results = []
        for r in rows:
            vec = np.frombuffer(r[4], dtype=np.float32).tolist() if r[4] else []
            results.append({
                "fact_key": r[0], "fact_value": r[1],
                "category": r[2], "confidence": r[3], "fact_vector": vec,
            })
        return results

    def get_profile_by_key(
        self,
        db_path,
        fact_key,
        fact_value=None,
        user_id: str = "default",
    ):
        """查詢 profile（含墓碑記錄）。若指定 fact_value 則精準查詢單筆，否則回傳 list。"""
        if not os.path.exists(db_path):
            return [] if fact_value is None else None
        conn = self._init_db(db_path)
        cursor = conn.cursor()
        if fact_value is not None:
            cursor.execute(
                "SELECT fact_key, fact_value, category, confidence FROM user_profile "
                "WHERE user_id = ? AND fact_key = ? AND fact_value = ?",
                (user_id, fact_key, fact_value)
            )
            row = cursor.fetchone()
            conn.close()
            return (
                {"fact_key": row[0], "fact_value": row[1],
                 "category": row[2], "confidence": row[3]}
                if row else None
            )
        else:
            cursor.execute(
                "SELECT fact_key, fact_value, category, confidence FROM user_profile "
                "WHERE user_id = ? AND fact_key = ?",
                (user_id, fact_key)
            )
            rows = cursor.fetchall()
            conn.close()
            return [
                {"fact_key": r[0], "fact_value": r[1],
                 "category": r[2], "confidence": r[3]}
                for r in rows
            ]

    # ════════════════════════════════════════════════════════════


__all__ = ["ProfileRepositoryMixin"]
