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


class ConversationRepositoryMixin:
    # SECTION: 對話 conversation.db — Schema / Sessions / Messages / Bridge Point
    # ════════════════════════════════════════════════════════════

    _CONV_DB = runtime_file("conversation.db")

    def _init_conversation_db(self):
        conn = sqlite3.connect(self._CONV_DB, timeout=15.0)
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS conversation_sessions (
                session_id TEXT PRIMARY KEY,
                channel TEXT DEFAULT 'rest',
                channel_uid TEXT DEFAULT '',
                created_at TEXT,
                last_active TEXT,
                is_active INTEGER DEFAULT 1,
                bridge_after_msg_id INTEGER DEFAULT 0,
                bot_id TEXT DEFAULT '',
                user_id TEXT NOT NULL DEFAULT 'default',
                character_id TEXT NOT NULL DEFAULT 'default',
                channel_class TEXT NOT NULL DEFAULT 'public',
                persona_face TEXT DEFAULT NULL,
                session_mode TEXT NOT NULL DEFAULT 'single',
                group_name TEXT DEFAULT ''
            )
        ''')
        # Schema evolution for older DBs
        cursor.execute("PRAGMA table_info(conversation_sessions)")
        cs_cols = [info[1] for info in cursor.fetchall()]
        for col, typedef in [
            ('bridge_after_msg_id', 'INTEGER DEFAULT 0'),
            ('bot_id', "TEXT DEFAULT ''"),
            ('user_id', "TEXT NOT NULL DEFAULT 'default'"),
            ('character_id', "TEXT NOT NULL DEFAULT 'default'"),
            ('channel_class', "TEXT NOT NULL DEFAULT 'public'"),
            ('persona_face', "TEXT DEFAULT NULL"),
            ('session_mode', "TEXT NOT NULL DEFAULT 'single'"),
            ('group_name', "TEXT DEFAULT ''"),
        ]:
            if col not in cs_cols:
                cursor.execute(f"ALTER TABLE conversation_sessions ADD COLUMN {col} {typedef}")
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_cs_user ON conversation_sessions(user_id)"
        )
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS conversation_messages (
                msg_id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                debug_info TEXT,
                character_name TEXT,
                character_id TEXT,
                timestamp TEXT,
                FOREIGN KEY (session_id) REFERENCES conversation_sessions(session_id)
            )
        ''')
        cursor.execute("PRAGMA table_info(conversation_messages)")
        cm_cols = [info[1] for info in cursor.fetchall()]
        if "character_name" not in cm_cols:
            cursor.execute("ALTER TABLE conversation_messages ADD COLUMN character_name TEXT")
        if "character_id" not in cm_cols:
            cursor.execute("ALTER TABLE conversation_messages ADD COLUMN character_id TEXT")
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_conv_msg_session ON conversation_messages(session_id)'
        )
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS conversation_session_participants (
                session_id TEXT NOT NULL,
                character_id TEXT NOT NULL,
                display_order INTEGER NOT NULL DEFAULT 0,
                is_active INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (session_id, character_id),
                FOREIGN KEY (session_id) REFERENCES conversation_sessions(session_id)
            )
        ''')
        cursor.execute("PRAGMA table_info(conversation_session_participants)")
        csp_cols = [info[1] for info in cursor.fetchall()]
        for col, typedef in [
            ('display_order', 'INTEGER NOT NULL DEFAULT 0'),
            ('is_active', 'INTEGER NOT NULL DEFAULT 1'),
        ]:
            if col not in csp_cols:
                cursor.execute(f"ALTER TABLE conversation_session_participants ADD COLUMN {col} {typedef}")
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_csp_session_order "
            "ON conversation_session_participants(session_id, display_order)"
        )
        conn.commit()
        return conn

    def _load_conversation_participants_with_conn(self, cursor, session_id: str) -> list[str]:
        cursor.execute(
            "SELECT character_id FROM conversation_session_participants "
            "WHERE session_id = ? AND is_active = 1 "
            "ORDER BY display_order ASC, character_id ASC",
            (session_id,),
        )
        return [r[0] for r in cursor.fetchall()]

    def create_conversation_session(
        self,
        session_id,
        channel="rest",
        channel_uid="",
        bot_id: str = "",
        user_id: str = "default",
        character_id: str = "default",
        channel_class: str = "public",
        persona_face: str | None = None,
        session_mode: str = "single",
        group_name: str = "",
        character_ids: list[str] | None = None,
    ):
        conn = self._init_conversation_db()
        cursor = conn.cursor()
        now = datetime.now().isoformat()
        participant_ids = list(dict.fromkeys(character_ids or [character_id]))
        if not participant_ids:
            participant_ids = [character_id]
        effective_mode = "group" if session_mode == "group" or len(participant_ids) > 1 else "single"
        cursor.execute(
            "INSERT OR IGNORE INTO conversation_sessions "
            "(session_id, channel, channel_uid, created_at, last_active, bot_id, user_id, "
            "character_id, channel_class, persona_face, session_mode, group_name) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session_id, channel, channel_uid, now, now, bot_id, user_id,
                participant_ids[0] if participant_ids else character_id,
                channel_class, persona_face, effective_mode, group_name,
            )
        )
        cursor.execute(
            "DELETE FROM conversation_session_participants WHERE session_id = ?",
            (session_id,),
        )
        for idx, cid in enumerate(participant_ids):
            cursor.execute(
                "INSERT OR REPLACE INTO conversation_session_participants "
                "(session_id, character_id, display_order, is_active) VALUES (?, ?, ?, 1)",
                (session_id, cid, idx),
            )
        conn.commit()
        conn.close()

    def update_conversation_session_roster(
        self,
        session_id: str,
        character_ids: list[str],
        *,
        session_mode: str,
        group_name: str | None = None,
    ) -> None:
        """更新 session 目前在場角色；保留曾參與資料，只切換 is_active。

        ⚠️ `conversation_sessions.character_id` 語意警告：
        此欄位為舊 schema 遺產（每 session 單一 AI 時代）。本函式會把它更新為
        `participant_ids[0]`（目前在場第一順位），意即同一個 session_id 在不同
        時間點查 `cs.character_id` 可能拿到不同答案，並非「session 創建時的主角色」。

        新代碼若需要：
        - 「目前在場名單」→ 查 `conversation_session_participants WHERE is_active=1`
        - 「歷史曾出現的角色」→ 查 `conversation_messages.character_id` distinct
        - 「session 創建時的主角色」→ 目前無此資訊，需另外擴 schema
        不要用 `cs.character_id` 推斷上述任何語意。
        """
        conn = self._init_conversation_db()
        cursor = conn.cursor()
        now = datetime.now().isoformat()
        participant_ids = list(dict.fromkeys(str(cid).strip() for cid in character_ids if str(cid).strip()))
        if not participant_ids:
            conn.close()
            return
        cursor.execute(
            "UPDATE conversation_session_participants SET is_active = 0 WHERE session_id = ?",
            (session_id,),
        )
        for idx, cid in enumerate(participant_ids):
            cursor.execute(
                "INSERT OR REPLACE INTO conversation_session_participants "
                "(session_id, character_id, display_order, is_active) VALUES (?, ?, ?, 1)",
                (session_id, cid, idx),
            )
        if group_name is None:
            cursor.execute(
                "UPDATE conversation_sessions "
                "SET character_id = ?, session_mode = ?, last_active = ? "
                "WHERE session_id = ?",
                (participant_ids[0], session_mode, now, session_id),
            )
        else:
            cursor.execute(
                "UPDATE conversation_sessions "
                "SET character_id = ?, session_mode = ?, group_name = ?, last_active = ? "
                "WHERE session_id = ?",
                (participant_ids[0], session_mode, group_name, now, session_id),
            )
        conn.commit()
        conn.close()

    def save_conversation_message(
        self,
        session_id,
        role,
        content,
        debug_info=None,
        character_name=None,
        character_id=None,
    ):
        conn = self._init_conversation_db()
        cursor = conn.cursor()
        now = datetime.now().isoformat()
        debug_json = json.dumps(debug_info, ensure_ascii=False) if debug_info else None
        cursor.execute(
            "INSERT INTO conversation_messages "
            "(session_id, role, content, debug_info, character_name, character_id, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (session_id, role, content, debug_json, character_name, character_id, now)
        )
        msg_id = cursor.lastrowid
        cursor.execute(
            'UPDATE conversation_sessions SET last_active = ? WHERE session_id = ?', (now, session_id)
        )
        conn.commit()
        conn.close()
        return msg_id

    def load_conversation_messages(self, session_id, since_msg_id: int = 0):
        """載入對話訊息。since_msg_id > 0 時只載入該 msg_id 之後的訊息（用於 bridge 還原）。"""
        conn = self._init_conversation_db()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT msg_id, role, content, debug_info, timestamp, character_name, character_id "
            "FROM conversation_messages WHERE session_id = ? AND msg_id > ? "
            "ORDER BY msg_id ASC",
            (session_id, since_msg_id)
        )
        rows = cursor.fetchall()
        conn.close()
        results = []
        for r in rows:
            msg = {"message_id": r[0], "role": r[1], "content": r[2], "timestamp": r[4]}
            if r[5]:
                msg["character_name"] = r[5]
            if r[6]:
                msg["character_id"] = r[6]
            if r[3]:
                try:
                    msg["debug_info"] = json.loads(r[3])
                except Exception:
                    pass
            results.append(msg)
        return results

    def load_conversation_participants(self, session_id: str) -> list[str]:
        conn = self._init_conversation_db()
        cursor = conn.cursor()
        participants = self._load_conversation_participants_with_conn(cursor, session_id)
        if not participants:
            cursor.execute(
                "SELECT character_id FROM conversation_sessions WHERE session_id = ?",
                (session_id,),
            )
            row = cursor.fetchone()
            if row and row[0]:
                participants = [row[0]]
        conn.close()
        return participants

    def update_bridge_point(self, session_id: str, keep_last_n: int = 2):
        """記錄 bridge 截斷點：保留最後 N 筆訊息，之前的訊息在 restore 時不載入。"""
        conn = self._init_conversation_db()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT msg_id FROM conversation_messages "
            "WHERE session_id = ? ORDER BY msg_id DESC LIMIT 1 OFFSET ?",
            (session_id, keep_last_n)
        )
        row = cursor.fetchone()
        if row:
            cursor.execute(
                'UPDATE conversation_sessions SET bridge_after_msg_id = ? WHERE session_id = ?',
                (row[0], session_id)
            )
            conn.commit()
        conn.close()

    def get_bridge_point(self, session_id: str) -> int:
        """取得 session 的 bridge 截斷點（msg_id），0 表示無截斷。"""
        conn = self._init_conversation_db()
        cursor = conn.cursor()
        cursor.execute(
            'SELECT bridge_after_msg_id FROM conversation_sessions WHERE session_id = ?',
            (session_id,)
        )
        row = cursor.fetchone()
        conn.close()
        return row[0] if row and row[0] else 0

    def load_conversation_sessions(self, channel=None, limit=50, user_id: str | None = None):
        conn = self._init_conversation_db()
        cursor = conn.cursor()
        where_parts = []
        params = []
        if channel:
            where_parts.append("s.channel = ?")
            params.append(channel)
        if user_id is not None:
            where_parts.append("s.user_id = ?")
            params.append(user_id)
        where_clause = f"WHERE {' AND '.join(where_parts)} " if where_parts else ""
        params.append(limit)
        cursor.execute(
            "SELECT s.session_id, s.channel, s.channel_uid, s.created_at, s.last_active, "
                "       s.is_active, s.bot_id, s.user_id, s.character_id, s.channel_class, s.persona_face, "
                "       s.session_mode, s.group_name, "
                "       (SELECT COUNT(*) FROM conversation_messages m WHERE m.session_id = s.session_id) "
            f"FROM conversation_sessions s {where_clause}"
            "ORDER BY s.last_active DESC LIMIT ?",
            params
        )
        rows = cursor.fetchall()
        results = []
        for r in rows:
            participants = self._load_conversation_participants_with_conn(cursor, r[0]) or [r[8]]
            results.append(
                {"session_id": r[0], "channel": r[1], "channel_uid": r[2],
                 "created_at": r[3], "last_active": r[4], "is_active": bool(r[5]),
                 "bot_id": r[6], "user_id": r[7], "character_id": r[8],
                 "channel_class": r[9], "persona_face": r[10],
                 "session_mode": r[11] or "single", "group_name": r[12] or "",
                 "character_ids": participants, "message_count": r[13]}
            )
        conn.close()
        return results

    def deactivate_session(self, session_id):
        conn = self._init_conversation_db()
        cursor = conn.cursor()
        cursor.execute(
            'UPDATE conversation_sessions SET is_active = 0 WHERE session_id = ?', (session_id,)
        )
        conn.commit()
        conn.close()

    def reactivate_session(self, session_id):
        conn = self._init_conversation_db()
        cursor = conn.cursor()
        now = datetime.now().isoformat()
        cursor.execute(
            'UPDATE conversation_sessions SET is_active = 1, last_active = ? WHERE session_id = ?',
            (now, session_id)
        )
        conn.commit()
        conn.close()

    def hard_delete_session(self, session_id):
        conn = self._init_conversation_db()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM conversation_messages WHERE session_id = ?', (session_id,))
        cursor.execute('DELETE FROM conversation_session_participants WHERE session_id = ?', (session_id,))
        cursor.execute('DELETE FROM conversation_sessions WHERE session_id = ?', (session_id,))
        conn.commit()
        conn.close()

    def hard_delete_sessions_for_user(self, user_id: str) -> int:
        """永久刪除指定使用者的所有 conversation sessions 與訊息。"""
        conn = self._init_conversation_db()
        cursor = conn.cursor()
        cursor.execute(
            'SELECT session_id FROM conversation_sessions WHERE user_id = ?',
            (str(user_id),),
        )
        session_ids = [r[0] for r in cursor.fetchall()]
        if session_ids:
            placeholders = ','.join('?' * len(session_ids))
            cursor.execute(
                f'DELETE FROM conversation_messages WHERE session_id IN ({placeholders})',
                session_ids,
            )
            cursor.execute(
                f'DELETE FROM conversation_session_participants WHERE session_id IN ({placeholders})',
                session_ids,
            )
            cursor.execute(
                f'DELETE FROM conversation_sessions WHERE session_id IN ({placeholders})',
                session_ids,
            )
            conn.commit()
        conn.close()
        return len(session_ids)

    def hard_delete_sessions_older_than(self, days: int) -> int:
        conn = self._init_conversation_db()
        cursor = conn.cursor()
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        cursor.execute(
            'SELECT session_id FROM conversation_sessions WHERE last_active < ?', (cutoff,)
        )
        old_ids = [r[0] for r in cursor.fetchall()]
        if old_ids:
            placeholders = ','.join('?' * len(old_ids))
            cursor.execute(
                f'DELETE FROM conversation_messages WHERE session_id IN ({placeholders})', old_ids
            )
            cursor.execute(
                f'DELETE FROM conversation_session_participants WHERE session_id IN ({placeholders})', old_ids
            )
            cursor.execute(
                f'DELETE FROM conversation_sessions WHERE session_id IN ({placeholders})', old_ids
            )
            conn.commit()
        conn.close()
        return len(old_ids)

    def get_session_info(self, session_id):
        conn = self._init_conversation_db()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT s.session_id, s.channel, s.channel_uid, s.created_at, s.last_active, "
            "       s.is_active, s.bot_id, s.user_id, s.character_id, s.channel_class, s.persona_face, "
            "       s.session_mode, s.group_name, "
            "       (SELECT COUNT(*) FROM conversation_messages m WHERE m.session_id = s.session_id) "
            "FROM conversation_sessions s WHERE s.session_id = ?",
            (session_id,)
        )
        r = cursor.fetchone()
        participants = self._load_conversation_participants_with_conn(cursor, session_id) if r else []
        conn.close()
        if not r:
            return None
        if not participants and r[8]:
            participants = [r[8]]
        return {
            "session_id": r[0], "channel": r[1], "channel_uid": r[2],
            "created_at": r[3], "last_active": r[4], "is_active": bool(r[5]),
            "bot_id": r[6], "user_id": r[7], "character_id": r[8],
            "channel_class": r[9], "persona_face": r[10],
            "session_mode": r[11] or "single", "group_name": r[12] or "",
            "character_ids": participants, "message_count": r[13],
        }

    # ════════════════════════════════════════════════════════════


__all__ = ["ConversationRepositoryMixin"]
