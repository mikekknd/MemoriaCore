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


class MessageStatsRepositoryMixin:
    # SECTION: 訊息統計 — 給 PersonaSync 等背景任務查閱閒置 / 訊息量
    # ════════════════════════════════════════════════════════════

    def get_last_message_time(self) -> "datetime | None":
        """回傳 conversation_messages 最後一筆訊息的 timestamp；無訊息回 None。"""
        conn = self._init_conversation_db()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT timestamp FROM conversation_messages ORDER BY msg_id DESC LIMIT 1"
        )
        row = cursor.fetchone()
        conn.close()
        if row and row[0]:
            try:
                return datetime.fromisoformat(row[0])
            except ValueError:
                return None
        return None

    def get_last_message_time_by_channel_class(self, channel_class: str) -> "datetime | None":
        """回傳指定 channel_class 的 session 中最後一筆訊息的 timestamp；無訊息回 None。

        用於 PersonaSync 逐 face 計算閒置時間（private/public 各自獨立計算）。
        """
        try:
            conn = self._init_conversation_db()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT cm.timestamp FROM conversation_messages cm "
                "JOIN conversation_sessions cs ON cm.session_id = cs.session_id "
                "WHERE cs.channel_class = ? ORDER BY cm.msg_id DESC LIMIT 1",
                (channel_class,),
            )
            row = cursor.fetchone()
            conn.close()
            if row and row[0]:
                return datetime.fromisoformat(row[0])
            return None
        except Exception:
            return None

    def get_last_message_time_by_character_and_channel_class(
        self, character_id: str, channel_class: str, exclude_channels: tuple[str, ...] = ()
    ) -> "datetime | None":
        """回傳指定角色在指定 channel_class 的最後 assistant 發言時間。"""
        try:
            conn = self._init_conversation_db()
            cursor = conn.cursor()
            query = (
                "SELECT cm.timestamp FROM conversation_messages cm "
                "JOIN conversation_sessions cs ON cm.session_id = cs.session_id "
                "WHERE cm.character_id = ? AND cm.role = 'assistant' AND cs.channel_class = ? "
            )
            params: list = [character_id, channel_class]
            if exclude_channels:
                placeholders = ",".join("?" for _ in exclude_channels)
                query += f"AND cs.channel NOT IN ({placeholders}) "
                params.extend(exclude_channels)
            query += "ORDER BY cm.msg_id DESC LIMIT 1"
            cursor.execute(query, tuple(params))
            row = cursor.fetchone()
            conn.close()
            if row and row[0]:
                return datetime.fromisoformat(row[0])
            return None
        except Exception:
            return None

    def count_messages_since(self, since_iso: str) -> int:
        """計算 since_iso 時間點之後的訊息數（含 user 與 assistant）。"""
        try:
            conn = self._init_conversation_db()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM conversation_messages WHERE timestamp > ?",
                (since_iso,)
            )
            count = cursor.fetchone()[0]
            conn.close()
            return count
        except Exception:
            return 0

    def count_messages_since_by_channel_class(
        self, since_iso: str, channel_class: str
    ) -> int:
        """計算指定 channel_class 的 session 在 since_iso 之後的訊息數。

        用於 PersonaSync 逐 face 計算觸發條件：
        - private face → channel_class='private'
        - public face  → channel_class='public'
        """
        try:
            conn = self._init_conversation_db()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM conversation_messages cm "
                "JOIN conversation_sessions cs ON cm.session_id = cs.session_id "
                "WHERE cs.channel_class = ? AND cm.timestamp > ?",
                (channel_class, since_iso)
            )
            count = cursor.fetchone()[0]
            conn.close()
            return count
        except Exception:
            return 0

    def count_messages_since_by_character_and_channel_class(
        self, since_iso: str, character_id: str, channel_class: str, exclude_channels: tuple[str, ...] = ()
    ) -> int:
        """計算指定角色在 since_iso 之後的 assistant 發言數。"""
        try:
            conn = self._init_conversation_db()
            cursor = conn.cursor()
            query = (
                "SELECT COUNT(*) FROM conversation_messages cm "
                "JOIN conversation_sessions cs ON cm.session_id = cs.session_id "
                "WHERE cm.character_id = ? AND cm.role = 'assistant' "
                "AND cs.channel_class = ? AND cm.timestamp > ? "
            )
            params: list = [character_id, channel_class, since_iso]
            if exclude_channels:
                placeholders = ",".join("?" for _ in exclude_channels)
                query += f"AND cs.channel NOT IN ({placeholders}) "
                params.extend(exclude_channels)
            cursor.execute(query, tuple(params))
            count = cursor.fetchone()[0]
            conn.close()
            return count
        except Exception:
            return 0

    def list_conversation_character_ids(
        self,
        limit: int | None = None,
        exclude_channels: tuple[str, ...] = (),
    ) -> list[str]:
        """列出實際有 assistant 發言的 character_id，供 PersonaSync 掃描候選角色。

        這是由 conversation DB 推導出的 dirty set：只有角色真的發言後才會出現在
        候選清單，不需要依賴 active/default character。
        """
        try:
            conn = self._init_conversation_db()
            cursor = conn.cursor()
            query = (
                "SELECT cm.character_id, MAX(cm.timestamp) AS last_ts "
                "FROM conversation_messages cm "
                "JOIN conversation_sessions cs ON cm.session_id = cs.session_id "
                "WHERE cm.role = 'assistant' "
                "AND cm.character_id IS NOT NULL AND cm.character_id != '' "
            )
            params: list = []
            if exclude_channels:
                placeholders = ",".join("?" for _ in exclude_channels)
                query += f"AND cs.channel NOT IN ({placeholders}) "
                params.extend(exclude_channels)
            query += "GROUP BY cm.character_id ORDER BY last_ts DESC"
            if limit is not None:
                query += " LIMIT ?"
                params.append(limit)
            cursor.execute(query, tuple(params))
            rows = cursor.fetchall()
            conn.close()
            return [r[0] for r in rows if r and r[0]]
        except Exception:
            return []

    def list_recent_conversation_character_ids(self, limit: int = 50) -> list[str]:
        """相容舊呼叫：列出近期實際有 assistant 發言的 character_id。"""
        return self.list_conversation_character_ids(limit=limit)

    # ════════════════════════════════════════════════════════════


__all__ = ["MessageStatsRepositoryMixin"]
