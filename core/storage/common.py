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


class StorageCommonMixin:
    # SECTION: 檔案 I/O — 偏好 / 對話歷史
    # ════════════════════════════════════════════════════════════

    def load_prefs(self):
        if os.path.exists(self.prefs_file):
            try:
                with open(self.prefs_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def save_prefs(self, prefs_dict):
        with open(self.prefs_file, "w", encoding="utf-8") as f:
            json.dump(prefs_dict, f, ensure_ascii=False, indent=2)

    def load_history(self):
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return []
        return []

    def save_history(self, messages):
        with open(self.history_file, "w", encoding="utf-8") as f:
            json.dump(messages, f, ensure_ascii=False, indent=2)

    def load_system_prompt(self, prompt_file=None):
        """Legacy fallback：system_prompt.txt 已棄用，不再讀取磁碟。"""
        return DEFAULT_SYSTEM_PROMPT

    def save_system_prompt(self, prompt_text, prompt_file=None):
        """Legacy no-op：system_prompt.txt 已棄用，不再寫入磁碟。"""
        return None

    # ════════════════════════════════════════════════════════════
    # SECTION: 模型 DB — 路徑解析 / Schema 初始化（含 Schema Evolution）
    # ════════════════════════════════════════════════════════════

    def get_db_path(self, model_name):
        safe_model_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', model_name)
        return runtime_file(f"memory_db_{safe_model_name}.db")

    def _init_db(self, db_path):
        conn = sqlite3.connect(db_path, timeout=15.0)
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")

        # ── memory_blocks（含三維度隔離欄位）──
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS memory_blocks (
                block_id TEXT PRIMARY KEY,
                timestamp TEXT,
                overview TEXT,
                overview_vector BLOB,
                sparse_vector TEXT,
                raw_dialogues TEXT,
                is_consolidated INTEGER DEFAULT 0,
                encounter_count REAL DEFAULT 1.0,
                potential_preferences TEXT DEFAULT '[]',
                user_id TEXT NOT NULL DEFAULT 'default',
                character_id TEXT NOT NULL DEFAULT 'default',
                visibility TEXT NOT NULL DEFAULT 'public'
            )
        ''')
        cursor.execute("PRAGMA table_info(memory_blocks)")
        mb_cols = [info[1] for info in cursor.fetchall()]
        for col, typedef in [
            ('is_consolidated', 'INTEGER DEFAULT 0'),
            ('encounter_count', 'REAL DEFAULT 1.0'),
            ('potential_preferences', "TEXT DEFAULT '[]'"),
            ('user_id', "TEXT NOT NULL DEFAULT 'default'"),
            ('character_id', "TEXT NOT NULL DEFAULT 'default'"),
            ('visibility', "TEXT NOT NULL DEFAULT 'public'"),
        ]:
            if col not in mb_cols:
                cursor.execute(f"ALTER TABLE memory_blocks ADD COLUMN {col} {typedef}")
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_mb_scope "
            "ON memory_blocks(user_id, character_id, visibility)"
        )
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS memory_block_audience (
                block_id TEXT NOT NULL,
                character_id TEXT NOT NULL,
                source TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                PRIMARY KEY (block_id, character_id)
            )
        ''')
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_mb_audience_character "
            "ON memory_block_audience(character_id, source)"
        )
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS memory_block_metadata (
                block_id TEXT PRIMARY KEY,
                metadata_json TEXT DEFAULT '{}',
                updated_at TEXT NOT NULL
            )
        ''')

        # ── core_memories（含三維度隔離欄位）──
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS core_memories (
                core_id TEXT PRIMARY KEY,
                timestamp TEXT,
                insight TEXT,
                insight_vector BLOB,
                encounter_count REAL DEFAULT 1.0,
                user_id TEXT NOT NULL DEFAULT 'default',
                character_id TEXT NOT NULL DEFAULT 'default',
                visibility TEXT NOT NULL DEFAULT 'public'
            )
        ''')
        cursor.execute("PRAGMA table_info(core_memories)")
        cm_cols = [info[1] for info in cursor.fetchall()]
        for col, typedef in [
            ('encounter_count', 'REAL DEFAULT 1.0'),
            ('user_id', "TEXT NOT NULL DEFAULT 'default'"),
            ('character_id', "TEXT NOT NULL DEFAULT 'default'"),
            ('visibility', "TEXT NOT NULL DEFAULT 'public'"),
        ]:
            if col not in cm_cols:
                cursor.execute(f"ALTER TABLE core_memories ADD COLUMN {col} {typedef}")
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_cm_scope "
            "ON core_memories(user_id, character_id, visibility)"
        )

        # ── user_profile / user_profile_vectors
        #    PK 遷移鏈：舊 (fact_key) → (fact_key, fact_value) → (user_id, fact_key, fact_value)
        #    同時加入 visibility 欄位
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_profile (
                user_id TEXT NOT NULL DEFAULT 'default',
                fact_key TEXT NOT NULL,
                fact_value TEXT NOT NULL,
                category TEXT,
                confidence REAL DEFAULT 1.0,
                timestamp TEXT,
                source_context TEXT,
                visibility TEXT NOT NULL DEFAULT 'public',
                PRIMARY KEY (user_id, fact_key, fact_value)
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_profile_vectors (
                user_id TEXT NOT NULL DEFAULT 'default',
                fact_key TEXT NOT NULL,
                fact_value TEXT NOT NULL,
                fact_vector BLOB,
                PRIMARY KEY (user_id, fact_key, fact_value),
                FOREIGN KEY (user_id, fact_key, fact_value)
                    REFERENCES user_profile(user_id, fact_key, fact_value) ON DELETE CASCADE
            )
        ''')
        cursor.execute("PRAGMA table_info(user_profile)")
        up_cols = {info[1]: info for info in cursor.fetchall()}

        # Stage 1：舊單 PK(fact_key) → (fact_key, fact_value)
        if up_cols.get('fact_key', (None,)*6)[5] == 1 and up_cols.get('fact_value', (None,)*6)[5] == 0:
            cursor.execute("ALTER TABLE user_profile RENAME TO _user_profile_old")
            cursor.execute('''
                CREATE TABLE user_profile (
                    fact_key TEXT NOT NULL,
                    fact_value TEXT NOT NULL,
                    category TEXT,
                    confidence REAL DEFAULT 1.0,
                    timestamp TEXT,
                    source_context TEXT,
                    PRIMARY KEY (fact_key, fact_value)
                )
            ''')
            cursor.execute("INSERT INTO user_profile SELECT * FROM _user_profile_old")
            cursor.execute("DROP TABLE _user_profile_old")
            cursor.execute("ALTER TABLE user_profile_vectors RENAME TO _user_profile_vectors_old")
            cursor.execute('''
                CREATE TABLE user_profile_vectors (
                    fact_key TEXT NOT NULL,
                    fact_value TEXT NOT NULL,
                    fact_vector BLOB,
                    PRIMARY KEY (fact_key, fact_value),
                    FOREIGN KEY (fact_key, fact_value)
                        REFERENCES user_profile(fact_key, fact_value) ON DELETE CASCADE
                )
            ''')
            cursor.execute('''
                INSERT INTO user_profile_vectors
                SELECT v.fact_key, p.fact_value, v.fact_vector
                FROM _user_profile_vectors_old v
                JOIN user_profile p ON v.fact_key = p.fact_key
            ''')
            cursor.execute("DROP TABLE _user_profile_vectors_old")
            cursor.execute("PRAGMA table_info(user_profile)")
            up_cols = {info[1]: info for info in cursor.fetchall()}

        # Stage 2：(fact_key, fact_value) → (user_id, fact_key, fact_value) + visibility
        if 'user_id' not in up_cols:
            cursor.execute("ALTER TABLE user_profile RENAME TO _user_profile_pre_isolation")
            cursor.execute('''
                CREATE TABLE user_profile (
                    user_id TEXT NOT NULL DEFAULT 'default',
                    fact_key TEXT NOT NULL,
                    fact_value TEXT NOT NULL,
                    category TEXT,
                    confidence REAL DEFAULT 1.0,
                    timestamp TEXT,
                    source_context TEXT,
                    visibility TEXT NOT NULL DEFAULT 'public',
                    PRIMARY KEY (user_id, fact_key, fact_value)
                )
            ''')
            cursor.execute("""
                INSERT INTO user_profile
                SELECT 'default', fact_key, fact_value, category, confidence,
                       timestamp, source_context, 'public'
                FROM _user_profile_pre_isolation
            """)
            cursor.execute("DROP TABLE _user_profile_pre_isolation")
            cursor.execute("ALTER TABLE user_profile_vectors RENAME TO _user_profile_vectors_pre_isolation")
            cursor.execute('''
                CREATE TABLE user_profile_vectors (
                    user_id TEXT NOT NULL DEFAULT 'default',
                    fact_key TEXT NOT NULL,
                    fact_value TEXT NOT NULL,
                    fact_vector BLOB,
                    PRIMARY KEY (user_id, fact_key, fact_value),
                    FOREIGN KEY (user_id, fact_key, fact_value)
                        REFERENCES user_profile(user_id, fact_key, fact_value) ON DELETE CASCADE
                )
            ''')
            cursor.execute("""
                INSERT INTO user_profile_vectors
                SELECT 'default', fact_key, fact_value, fact_vector
                FROM _user_profile_vectors_pre_isolation
            """)
            cursor.execute("DROP TABLE _user_profile_vectors_pre_isolation")
        elif 'visibility' not in up_cols:
            cursor.execute(
                "ALTER TABLE user_profile ADD COLUMN visibility TEXT NOT NULL DEFAULT 'public'"
            )

        # ── topic_cache（含三維度隔離欄位）──
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS topic_cache (
                topic_id TEXT PRIMARY KEY,
                interest_keyword TEXT,
                summary_content TEXT,
                created_at TEXT,
                is_mentioned_to_user INTEGER DEFAULT 0,
                user_id TEXT NOT NULL DEFAULT 'default',
                character_id TEXT NOT NULL DEFAULT 'default',
                visibility TEXT NOT NULL DEFAULT 'public'
            )
        ''')
        cursor.execute("PRAGMA table_info(topic_cache)")
        tc_cols = [info[1] for info in cursor.fetchall()]
        for col, typedef in [
            ('user_id', "TEXT NOT NULL DEFAULT 'default'"),
            ('character_id', "TEXT NOT NULL DEFAULT 'default'"),
            ('visibility', "TEXT NOT NULL DEFAULT 'public'"),
        ]:
            if col not in tc_cols:
                cursor.execute(f"ALTER TABLE topic_cache ADD COLUMN {col} {typedef}")
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_tc_scope "
            "ON topic_cache(user_id, character_id, visibility)"
        )

        conn.commit()
        return conn

    # ════════════════════════════════════════════════════════════


__all__ = ["StorageCommonMixin"]
