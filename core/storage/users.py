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


class UserRepositoryMixin:
    # SECTION: Users DB — 登入帳號 / 權限 / Auth Rate Limit
    # ════════════════════════════════════════════════════════════

    _USERS_DB = runtime_file("users.db")

    def _init_users_db(self):
        conn = sqlite3.connect(self._USERS_DB, timeout=15.0)
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                nickname TEXT DEFAULT '',
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                token_version INTEGER NOT NULL DEFAULT 0,
                telegram_uid TEXT DEFAULT NULL,
                discord_uid TEXT DEFAULT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute("PRAGMA table_info(users)")
        user_cols = [info[1] for info in cursor.fetchall()]
        for col, typedef in [
            ("nickname", "TEXT DEFAULT ''"),
            ("role", "TEXT NOT NULL DEFAULT 'user'"),
            ("token_version", "INTEGER NOT NULL DEFAULT 0"),
            ("telegram_uid", "TEXT DEFAULT NULL"),
            ("discord_uid", "TEXT DEFAULT NULL"),
            ("updated_at", "TEXT DEFAULT CURRENT_TIMESTAMP"),
        ]:
            if col not in user_cols:
                cursor.execute(f"ALTER TABLE users ADD COLUMN {col} {typedef}")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_role ON users(role)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_telegram_uid ON users(telegram_uid)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_discord_uid ON users(discord_uid)")
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS auth_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                ip_address TEXT NOT NULL,
                failed_count INTEGER NOT NULL DEFAULT 0,
                locked_until TEXT DEFAULT NULL,
                last_failed_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(username, ip_address)
            )
        ''')
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_auth_attempts_lookup "
            "ON auth_attempts(username, ip_address)"
        )
        conn.commit()
        return conn

    def count_users(self) -> int:
        conn = self._init_users_db()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        count = int(cursor.fetchone()[0])
        conn.close()
        return count

    def create_user(
        self,
        username: str,
        password_hash: str,
        nickname: str = "",
        telegram_uid: str | None = None,
        discord_uid: str | None = None,
    ) -> dict:
        conn = self._init_users_db()
        cursor = conn.cursor()
        now = datetime.now().isoformat()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute("SELECT COUNT(*) FROM users")
            role = "admin" if int(cursor.fetchone()[0]) == 0 else "user"
            cursor.execute(
                "INSERT INTO users "
                "(username, nickname, password_hash, role, telegram_uid, discord_uid, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (username, nickname, password_hash, role, telegram_uid, discord_uid, now, now),
            )
            user_id = cursor.lastrowid
            conn.commit()
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            raise ValueError("username already exists") from exc
        finally:
            conn.close()
        user = self.get_user_by_id(str(user_id))
        if not user:
            raise ValueError("user created but not found")
        return user

    def _row_to_user(self, row) -> dict | None:
        if not row:
            return None
        return {
            "id": row[0],
            "username": row[1],
            "nickname": row[2] or "",
            "password_hash": row[3],
            "role": row[4] or "user",
            "token_version": int(row[5] or 0),
            "telegram_uid": row[6],
            "discord_uid": row[7],
            "created_at": row[8],
            "updated_at": row[9],
        }

    def get_user_by_username(self, username: str) -> dict | None:
        conn = self._init_users_db()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, username, nickname, password_hash, role, token_version, "
            "telegram_uid, discord_uid, created_at, updated_at "
            "FROM users WHERE username = ?",
            (username,),
        )
        user = self._row_to_user(cursor.fetchone())
        conn.close()
        return user

    def get_user_by_id(self, user_id) -> dict | None:
        conn = self._init_users_db()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, username, nickname, password_hash, role, token_version, "
            "telegram_uid, discord_uid, created_at, updated_at "
            "FROM users WHERE id = ?",
            (str(user_id),),
        )
        user = self._row_to_user(cursor.fetchone())
        conn.close()
        return user

    def get_user_by_telegram_uid(self, telegram_uid: str | int) -> dict | None:
        """以 Telegram UID 對應登入帳號；若重複設定，取最早建立的帳號。"""
        uid = str(telegram_uid).strip()
        if not uid:
            return None
        conn = self._init_users_db()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, username, nickname, password_hash, role, token_version, "
            "telegram_uid, discord_uid, created_at, updated_at "
            "FROM users WHERE telegram_uid = ? ORDER BY id ASC LIMIT 1",
            (uid,),
        )
        user = self._row_to_user(cursor.fetchone())
        conn.close()
        return user

    def get_user_by_discord_uid(self, discord_uid: str | int) -> dict | None:
        """以 Discord UID 對應登入帳號；若重複設定，取最早建立的帳號。"""
        uid = str(discord_uid).strip()
        if not uid:
            return None
        conn = self._init_users_db()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, username, nickname, password_hash, role, token_version, "
            "telegram_uid, discord_uid, created_at, updated_at "
            "FROM users WHERE discord_uid = ? ORDER BY id ASC LIMIT 1",
            (uid,),
        )
        user = self._row_to_user(cursor.fetchone())
        conn.close()
        return user

    def update_user_profile(
        self,
        user_id,
        nickname: str | None = None,
        telegram_uid: str | None = None,
        discord_uid: str | None = None,
    ) -> dict | None:
        updates = []
        params = []
        if nickname is not None:
            updates.append("nickname = ?")
            params.append(nickname)
        if telegram_uid is not None:
            updates.append("telegram_uid = ?")
            params.append(telegram_uid or None)
        if discord_uid is not None:
            updates.append("discord_uid = ?")
            params.append(discord_uid or None)
        if updates:
            updates.append("updated_at = ?")
            params.append(datetime.now().isoformat())
            params.append(str(user_id))
            conn = self._init_users_db()
            cursor = conn.cursor()
            cursor.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", params)
            conn.commit()
            conn.close()
        return self.get_user_by_id(user_id)

    def update_user_password_hash(self, user_id, password_hash: str) -> dict | None:
        conn = self._init_users_db()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET password_hash = ?, token_version = token_version + 1, updated_at = ? "
            "WHERE id = ?",
            (password_hash, datetime.now().isoformat(), str(user_id)),
        )
        conn.commit()
        conn.close()
        return self.get_user_by_id(user_id)

    def increment_user_token_version(self, user_id) -> dict | None:
        conn = self._init_users_db()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET token_version = token_version + 1, updated_at = ? WHERE id = ?",
            (datetime.now().isoformat(), str(user_id)),
        )
        conn.commit()
        conn.close()
        return self.get_user_by_id(user_id)

    def count_admin_users(self) -> int:
        conn = self._init_users_db()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'")
        count = int(cursor.fetchone()[0])
        conn.close()
        return count

    def get_first_admin_user(self) -> dict | None:
        conn = self._init_users_db()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, username, nickname, password_hash, role, token_version, "
            "telegram_uid, discord_uid, created_at, updated_at "
            "FROM users WHERE role = 'admin' ORDER BY id ASC LIMIT 1"
        )
        user = self._row_to_user(cursor.fetchone())
        conn.close()
        return user

    def list_users_with_stats(self) -> list[dict]:
        conn = self._init_users_db()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, username, nickname, password_hash, role, token_version, "
            "telegram_uid, discord_uid, created_at, updated_at "
            "FROM users ORDER BY id ASC"
        )
        users = [self._row_to_user(row) for row in cursor.fetchall()]
        conn.close()
        return [
            {**user, "stats": self._user_owned_data_counts(str(user["id"]))}
            for user in users
            if user
        ]

    def list_users_basic(self) -> list[dict]:
        """列出登入帳號基本資料；不掃描 memory DB。"""
        conn = self._init_users_db()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, username, nickname, password_hash, role, token_version, "
            "telegram_uid, discord_uid, created_at, updated_at "
            "FROM users ORDER BY id ASC"
        )
        users = [self._row_to_user(row) for row in cursor.fetchall()]
        conn.close()
        return [user for user in users if user]

    def _memory_db_paths(self) -> list[str]:
        roots = {os.getcwd()}
        prefs_root = os.path.dirname(os.path.abspath(self.prefs_file))
        if prefs_root:
            roots.add(prefs_root)
        paths: list[str] = []
        for root in roots:
            if not os.path.isdir(root):
                continue
            for name in os.listdir(root):
                if name.startswith("memory_db_") and name.endswith(".db"):
                    path = os.path.join(root, name)
                    if path not in paths:
                        paths.append(path)
        return paths

    def _user_owned_data_counts(self, user_id: str) -> dict:
        counts = {
            "sessions": 0,
            "messages": 0,
            "memory_blocks": 0,
            "core_memories": 0,
            "profiles": 0,
            "topics": 0,
        }

        conv = self._init_conversation_db()
        cur = conv.cursor()
        cur.execute("SELECT COUNT(*) FROM conversation_sessions WHERE user_id = ?", (str(user_id),))
        counts["sessions"] = int(cur.fetchone()[0])
        cur.execute(
            "SELECT COUNT(*) FROM conversation_messages "
            "WHERE session_id IN (SELECT session_id FROM conversation_sessions WHERE user_id = ?)",
            (str(user_id),),
        )
        counts["messages"] = int(cur.fetchone()[0])
        conv.close()

        for db_path in self._memory_db_paths():
            conn = sqlite3.connect(db_path, timeout=15.0)
            cur = conn.cursor()
            for table, key in [
                ("memory_blocks", "memory_blocks"),
                ("core_memories", "core_memories"),
                ("user_profile", "profiles"),
                ("topic_cache", "topics"),
            ]:
                try:
                    cur.execute(f"SELECT COUNT(*) FROM {table} WHERE user_id = ?", (str(user_id),))
                    counts[key] += int(cur.fetchone()[0])
                except sqlite3.Error:
                    pass
            conn.close()
        return counts

    def delete_user_and_owned_data(self, user_id, confirm_username: str) -> dict:
        user = self.get_user_by_id(user_id)
        if not user:
            raise ValueError("user not found")
        if confirm_username != user["username"]:
            raise ValueError("confirm_username does not match")

        counts = self._user_owned_data_counts(str(user_id))

        conv = self._init_conversation_db()
        cur = conv.cursor()
        cur.execute("SELECT session_id FROM conversation_sessions WHERE user_id = ?", (str(user_id),))
        session_ids = [row[0] for row in cur.fetchall()]
        if session_ids:
            placeholders = ",".join("?" * len(session_ids))
            cur.execute(f"DELETE FROM conversation_messages WHERE session_id IN ({placeholders})", session_ids)
            cur.execute(f"DELETE FROM conversation_session_participants WHERE session_id IN ({placeholders})", session_ids)
        cur.execute("DELETE FROM conversation_sessions WHERE user_id = ?", (str(user_id),))
        conv.commit()
        conv.close()

        for db_path in self._memory_db_paths():
            conn = sqlite3.connect(db_path, timeout=15.0)
            cur = conn.cursor()
            for table in [
                "user_profile_vectors",
                "user_profile",
                "memory_blocks",
                "core_memories",
                "ai_personality_observations",
                "topic_cache",
            ]:
                try:
                    cur.execute(f"DELETE FROM {table} WHERE user_id = ?", (str(user_id),))
                except sqlite3.Error:
                    pass
            conn.commit()
            conn.close()

        conn = self._init_users_db()
        cur = conn.cursor()
        cur.execute("DELETE FROM auth_attempts WHERE username = ?", (user["username"],))
        cur.execute("DELETE FROM users WHERE id = ?", (str(user_id),))
        conn.commit()
        conn.close()

        return {
            "deleted_user_id": int(user["id"]),
            "deleted_username": user["username"],
            "deleted_counts": counts,
        }

    def get_auth_attempt(self, username: str, ip_address: str) -> dict | None:
        conn = self._init_users_db()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT username, ip_address, failed_count, locked_until, last_failed_at "
            "FROM auth_attempts WHERE username = ? AND ip_address = ?",
            (username, ip_address),
        )
        row = cursor.fetchone()
        conn.close()
        if not row:
            return None
        return {
            "username": row[0],
            "ip_address": row[1],
            "failed_count": int(row[2] or 0),
            "locked_until": row[3],
            "last_failed_at": row[4],
        }

    def is_auth_locked(self, username: str, ip_address: str) -> bool:
        attempt = self.get_auth_attempt(username, ip_address)
        if not attempt or not attempt.get("locked_until"):
            return False
        try:
            return datetime.fromisoformat(attempt["locked_until"]) > datetime.now()
        except Exception:
            return False

    def record_auth_attempt(
        self,
        username: str,
        ip_address: str,
        limit: int = 5,
        lock_minutes: int = 15,
        window_minutes: int = 15,
    ) -> dict:
        conn = self._init_users_db()
        cursor = conn.cursor()
        now = datetime.now()
        now_iso = now.isoformat()
        cursor.execute(
            "SELECT failed_count, last_failed_at FROM auth_attempts "
            "WHERE username = ? AND ip_address = ?",
            (username, ip_address),
        )
        row = cursor.fetchone()
        failed_count = 0
        if row:
            failed_count = int(row[0] or 0)
            try:
                last_failed = datetime.fromisoformat(row[1]) if row[1] else None
            except Exception:
                last_failed = None
            if not last_failed or now - last_failed > timedelta(minutes=window_minutes):
                failed_count = 0
        failed_count += 1
        locked_until = None
        if failed_count >= limit:
            locked_until = (now + timedelta(minutes=lock_minutes)).isoformat()
        cursor.execute(
            "INSERT INTO auth_attempts (username, ip_address, failed_count, locked_until, last_failed_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(username, ip_address) DO UPDATE SET "
            "failed_count = excluded.failed_count, "
            "locked_until = excluded.locked_until, "
            "last_failed_at = excluded.last_failed_at",
            (username, ip_address, failed_count, locked_until, now_iso),
        )
        conn.commit()
        conn.close()
        return self.get_auth_attempt(username, ip_address) or {}

    def reset_auth_attempts(self, username: str, ip_address: str) -> None:
        conn = self._init_users_db()
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM auth_attempts WHERE username = ? AND ip_address = ?",
            (username, ip_address),
        )
        conn.commit()
        conn.close()

    # ════════════════════════════════════════════════════════════


__all__ = ["UserRepositoryMixin"]
