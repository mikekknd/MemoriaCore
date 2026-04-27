# 【環境假設】：Python 3.12, numpy 庫可用。使用內建 sqlite3。支援 Schema Evolution。
import json
import os
import re
import sqlite3
import numpy as np
from datetime import datetime, timedelta

class StorageManager:
    def __init__(
        self,
        prefs_file="user_prefs.json",
        history_file="chat_history.json",
        persona_snapshot_db_path="persona_snapshots.db",
    ):
        self.prefs_file = prefs_file
        self.history_file = history_file
        self.persona_snapshot_db_path = persona_snapshot_db_path

    # ════════════════════════════════════════════════════════════
    # SECTION: 檔案 I/O — 偏好 / 對話歷史 / System Prompt
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

    def load_system_prompt(self, prompt_file="system_prompt.txt"):
        if os.path.exists(prompt_file):
            with open(prompt_file, "r", encoding="utf-8") as f:
                return f.read()
        return "你是一個具備情境記憶與核心認知的 AI 助理。"

    def save_system_prompt(self, prompt_text, prompt_file="system_prompt.txt"):
        with open(prompt_file, "w", encoding="utf-8") as f:
            f.write(prompt_text)

    # ════════════════════════════════════════════════════════════
    # SECTION: 模型 DB — 路徑解析 / Schema 初始化（含 Schema Evolution）
    # ════════════════════════════════════════════════════════════

    def get_db_path(self, model_name):
        safe_model_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', model_name)
        return f"memory_db_{safe_model_name}.db"

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

        # ── ai_personality_observations（含三維度隔離欄位）──
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ai_personality_observations (
                obs_id TEXT PRIMARY KEY,
                timestamp TEXT,
                category TEXT,
                raw_statement TEXT,
                extracted_trait TEXT,
                trait_vector BLOB,
                source_context TEXT,
                is_reflected INTEGER DEFAULT 0,
                encounter_count REAL DEFAULT 1.0,
                user_id TEXT NOT NULL DEFAULT 'default',
                character_id TEXT NOT NULL DEFAULT 'default',
                visibility TEXT NOT NULL DEFAULT 'public'
            )
        ''')
        cursor.execute("PRAGMA table_info(ai_personality_observations)")
        ao_cols = [info[1] for info in cursor.fetchall()]
        for col, typedef in [
            ('user_id', "TEXT NOT NULL DEFAULT 'default'"),
            ('character_id', "TEXT NOT NULL DEFAULT 'default'"),
            ('visibility', "TEXT NOT NULL DEFAULT 'public'"),
        ]:
            if col not in ao_cols:
                cursor.execute(
                    f"ALTER TABLE ai_personality_observations ADD COLUMN {col} {typedef}"
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
    # SECTION: 情境記憶 Memory Blocks — 載入 / 儲存
    # ════════════════════════════════════════════════════════════

    def load_db(
        self,
        db_path,
        user_id: str = "default",
        character_id: str = "default",
        visibility_filter=None,
    ):
        """載入記憶區塊。

        visibility_filter: list[str] | None
            None → 全部（給 private face 的 keyed cache 使用）
            ['public'] → 只 public
            ['private', 'public'] → private face 讀全量
        """
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
            "SELECT block_id, timestamp, overview, overview_vector, sparse_vector, "
            "raw_dialogues, is_consolidated, encounter_count, potential_preferences "
            f"FROM memory_blocks{where_clause}",
            params
        )
        rows = cursor.fetchall()

        memory_blocks = []
        for row in rows:
            (block_id, timestamp, overview, overview_vector_blob,
             sparse_vector_json, raw_dialogues_json,
             is_consolidated, encounter_count, potential_preferences_json) = row
            overview_vector = np.frombuffer(overview_vector_blob, dtype=np.float32).tolist()
            memory_blocks.append({
                "block_id": block_id,
                "timestamp": timestamp,
                "overview": overview,
                "overview_vector": overview_vector,
                "sparse_vector": json.loads(sparse_vector_json) if sparse_vector_json else {},
                "raw_dialogues": json.loads(raw_dialogues_json) if raw_dialogues_json else [],
                "is_consolidated": bool(is_consolidated),
                "encounter_count": float(encounter_count) if encounter_count is not None else 1.0,
                "potential_preferences": json.loads(potential_preferences_json) if potential_preferences_json else [],
            })

        conn.close()
        return memory_blocks

    def save_db(
        self,
        db_path,
        memory_blocks,
        user_id: str = "default",
        character_id: str = "default",
        visibility: str = "public",
    ):
        """寫回記憶區塊（scoped DELETE + INSERT）。

        ⚠️ 高風險操作：DELETE 限定 (user_id, character_id, visibility) 範圍，
        絕對不能移除 WHERE 條件，否則會清掉其他用戶的資料。
        """
        conn = self._init_db(db_path)
        cursor = conn.cursor()
        try:
            cursor.execute("BEGIN")
            cursor.execute(
                "DELETE FROM memory_blocks "
                "WHERE user_id = ? AND character_id = ? AND visibility = ?",
                (user_id, character_id, visibility)
            )

            for block in memory_blocks:
                vector_blob = np.array(block["overview_vector"], dtype=np.float32).tobytes()
                sparse_json = json.dumps(block.get("sparse_vector", {}), ensure_ascii=False)
                dialogues_json = json.dumps(block.get("raw_dialogues", []), ensure_ascii=False)
                is_cons = 1 if block.get("is_consolidated", False) else 0
                enc_count = float(block.get("encounter_count", 1.0))
                prefs_json = json.dumps(block.get("potential_preferences", []), ensure_ascii=False)
                cursor.execute(
                    "INSERT INTO memory_blocks "
                    "(block_id, timestamp, overview, overview_vector, sparse_vector, "
                    " raw_dialogues, is_consolidated, encounter_count, potential_preferences, "
                    " user_id, character_id, visibility) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (block["block_id"], block["timestamp"], block["overview"],
                     vector_blob, sparse_json, dialogues_json,
                     is_cons, enc_count, prefs_json,
                     user_id, character_id, visibility)
                )

            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ════════════════════════════════════════════════════════════
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
    ):
        """刪除指定 core memory（含 user_id / character_id 範圍驗證）。"""
        conn = self._init_db(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM core_memories "
            "WHERE core_id = ? AND user_id = ? AND character_id = ?",
            (core_id, user_id, character_id)
        )
        conn.commit()
        conn.close()

    # ════════════════════════════════════════════════════════════
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
    ):
        """刪除使用者事實（同時清除向量）。

        ⚠️ 高風險操作：user_id 必須傳入，防止誤刪其他用戶同 fact_key 的資料。
        若指定 fact_value 則精準刪除單筆，否則刪除該 user_id + key 下所有值。
        """
        conn = self._init_db(db_path)
        cursor = conn.cursor()
        if fact_value is not None:
            cursor.execute(
                "DELETE FROM user_profile_vectors "
                "WHERE user_id = ? AND fact_key = ? AND fact_value = ?",
                (user_id, fact_key, fact_value)
            )
            cursor.execute(
                "DELETE FROM user_profile "
                "WHERE user_id = ? AND fact_key = ? AND fact_value = ?",
                (user_id, fact_key, fact_value)
            )
        else:
            cursor.execute(
                "DELETE FROM user_profile_vectors WHERE user_id = ? AND fact_key = ?",
                (user_id, fact_key)
            )
            cursor.execute(
                "DELETE FROM user_profile WHERE user_id = ? AND fact_key = ?",
                (user_id, fact_key)
            )
        conn.commit()
        conn.close()

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
    ):
        if not os.path.exists(db_path):
            return []
        conn = self._init_db(db_path)
        cursor = conn.cursor()
        where_parts = ["is_mentioned_to_user = 0", "user_id = ?", "character_id = ?"]
        params: list = [user_id, character_id]
        if visibility_filter is not None:
            placeholders = ','.join('?' * len(visibility_filter))
            where_parts.append(f"visibility IN ({placeholders})")
            params.extend(visibility_filter)
        params.append(limit)
        where_clause = " WHERE " + " AND ".join(where_parts)
        cursor.execute(
            "SELECT topic_id, interest_keyword, summary_content, created_at "
            f"FROM topic_cache{where_clause} ORDER BY created_at DESC LIMIT ?",
            params
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
    # SECTION: 對話 conversation.db — Schema / Sessions / Messages / Bridge Point
    # ════════════════════════════════════════════════════════════

    _CONV_DB = "conversation.db"

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
                user_id TEXT NOT NULL DEFAULT 'default',
                channel_class TEXT NOT NULL DEFAULT 'public'
            )
        ''')
        # Schema evolution for older DBs
        cursor.execute("PRAGMA table_info(conversation_sessions)")
        cs_cols = [info[1] for info in cursor.fetchall()]
        for col, typedef in [
            ('bridge_after_msg_id', 'INTEGER DEFAULT 0'),
            ('user_id', "TEXT NOT NULL DEFAULT 'default'"),
            ('channel_class', "TEXT NOT NULL DEFAULT 'public'"),
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
                timestamp TEXT,
                FOREIGN KEY (session_id) REFERENCES conversation_sessions(session_id)
            )
        ''')
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_conv_msg_session ON conversation_messages(session_id)'
        )
        conn.commit()
        return conn

    def create_conversation_session(
        self,
        session_id,
        channel="rest",
        channel_uid="",
        user_id: str = "default",
        channel_class: str = "public",
    ):
        conn = self._init_conversation_db()
        cursor = conn.cursor()
        now = datetime.now().isoformat()
        cursor.execute(
            "INSERT OR IGNORE INTO conversation_sessions "
            "(session_id, channel, channel_uid, created_at, last_active, user_id, channel_class) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (session_id, channel, channel_uid, now, now, user_id, channel_class)
        )
        conn.commit()
        conn.close()

    def save_conversation_message(self, session_id, role, content, debug_info=None):
        conn = self._init_conversation_db()
        cursor = conn.cursor()
        now = datetime.now().isoformat()
        debug_json = json.dumps(debug_info, ensure_ascii=False) if debug_info else None
        cursor.execute(
            "INSERT INTO conversation_messages (session_id, role, content, debug_info, timestamp) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, role, content, debug_json, now)
        )
        cursor.execute(
            'UPDATE conversation_sessions SET last_active = ? WHERE session_id = ?', (now, session_id)
        )
        conn.commit()
        conn.close()

    def load_conversation_messages(self, session_id, since_msg_id: int = 0):
        """載入對話訊息。since_msg_id > 0 時只載入該 msg_id 之後的訊息（用於 bridge 還原）。"""
        conn = self._init_conversation_db()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT role, content, debug_info, timestamp "
            "FROM conversation_messages WHERE session_id = ? AND msg_id > ? "
            "ORDER BY msg_id ASC",
            (session_id, since_msg_id)
        )
        rows = cursor.fetchall()
        conn.close()
        results = []
        for r in rows:
            msg = {"role": r[0], "content": r[1], "timestamp": r[3]}
            if r[2]:
                try:
                    msg["debug_info"] = json.loads(r[2])
                except Exception:
                    pass
            results.append(msg)
        return results

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

    def load_conversation_sessions(self, channel=None, limit=50):
        conn = self._init_conversation_db()
        cursor = conn.cursor()
        if channel:
            cursor.execute(
                "SELECT s.session_id, s.channel, s.channel_uid, s.created_at, s.last_active, "
                "       s.is_active, s.user_id, s.channel_class, "
                "       (SELECT COUNT(*) FROM conversation_messages m WHERE m.session_id = s.session_id) "
                "FROM conversation_sessions s WHERE s.channel = ? "
                "ORDER BY s.last_active DESC LIMIT ?",
                (channel, limit)
            )
        else:
            cursor.execute(
                "SELECT s.session_id, s.channel, s.channel_uid, s.created_at, s.last_active, "
                "       s.is_active, s.user_id, s.channel_class, "
                "       (SELECT COUNT(*) FROM conversation_messages m WHERE m.session_id = s.session_id) "
                "FROM conversation_sessions s "
                "ORDER BY s.last_active DESC LIMIT ?",
                (limit,)
            )
        rows = cursor.fetchall()
        conn.close()
        return [
            {"session_id": r[0], "channel": r[1], "channel_uid": r[2],
             "created_at": r[3], "last_active": r[4], "is_active": bool(r[5]),
             "user_id": r[6], "channel_class": r[7], "message_count": r[8]}
            for r in rows
        ]

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
        cursor.execute('DELETE FROM conversation_sessions WHERE session_id = ?', (session_id,))
        conn.commit()
        conn.close()

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
            "       s.is_active, s.user_id, s.channel_class, "
            "       (SELECT COUNT(*) FROM conversation_messages m WHERE m.session_id = s.session_id) "
            "FROM conversation_sessions s WHERE s.session_id = ?",
            (session_id,)
        )
        r = cursor.fetchone()
        conn.close()
        if not r:
            return None
        return {
            "session_id": r[0], "channel": r[1], "channel_uid": r[2],
            "created_at": r[3], "last_active": r[4], "is_active": bool(r[5]),
            "user_id": r[6], "channel_class": r[7], "message_count": r[8],
        }

    # ════════════════════════════════════════════════════════════
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

    # ════════════════════════════════════════════════════════════
    # SECTION: 人格演化 Snapshots — 版本儲存 / 血統查詢 / 時間序列
    # ════════════════════════════════════════════════════════════

    def _init_persona_snapshot_db(self):
        """初始化人格演化 snapshot 資料表（PRAGMA user_version 驅動 schema migration）。

        版本歷史：
        - user_version == 0 — 空 DB 或舊 6 維 prototype → 重建為 v3。
        - user_version == 2 — Path D trait tree（無 persona_face）→ 遷移至 v3。
        - user_version == 3 — 正式雙 face schema（有 persona_face）。
        - 其他值 — 拒絕啟動。
        """
        conn = sqlite3.connect(self.persona_snapshot_db_path, timeout=15.0)
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA user_version")
        user_version = cur.fetchone()[0]

        if user_version == 0:
            try:
                cur.execute("BEGIN IMMEDIATE")
                cur.execute("PRAGMA user_version")
                if cur.fetchone()[0] == 0:
                    cur.execute("DROP TABLE IF EXISTS persona_dimensions")
                    cur.execute("DROP TABLE IF EXISTS persona_traits")
                    cur.execute("DROP TABLE IF EXISTS persona_snapshots")
                    self._create_persona_v3_schema(cur)
                    cur.execute("PRAGMA user_version = 3")
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        elif user_version == 2:
            try:
                # FK 必須在 transaction 外關閉，否則 SQLite 忽略此 PRAGMA
                cur.execute("PRAGMA foreign_keys=OFF")
                cur.execute("BEGIN IMMEDIATE")
                cur.execute("PRAGMA user_version")
                if cur.fetchone()[0] == 2:
                    self._migrate_persona_v2_to_v3(cur)
                    cur.execute("PRAGMA user_version = 3")
                conn.commit()
                cur.execute("PRAGMA foreign_keys=ON")
            except Exception:
                conn.rollback()
                cur.execute("PRAGMA foreign_keys=ON")
                raise
        elif user_version == 3:
            self._create_persona_v3_schema(cur)
            conn.commit()
        else:
            raise RuntimeError(
                f"persona_snapshots.db 發現無法識別的 user_version={user_version}"
                f"（預期 0、2 或 3）— 拒絕啟動以防半毀 DB"
            )
        return conn

    def _migrate_persona_v2_to_v3(self, cur):
        """v2 → v3：為 persona_snapshots 和 persona_traits 加入 persona_face 欄位。

        persona_snapshots：rename + rebuild（UNIQUE 從 (character_id, version)
        改為 (character_id, persona_face, version)）。
        persona_traits：ALTER ADD COLUMN + drop/create unique index。
        """
        # persona_snapshots rebuild（persona_dimensions FK 指向 id，id 值不變，FK 依然有效）
        cur.execute("ALTER TABLE persona_snapshots RENAME TO _persona_snapshots_v2")
        cur.execute('''
            CREATE TABLE persona_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                character_id TEXT NOT NULL,
                persona_face TEXT NOT NULL DEFAULT 'public',
                version INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                summary TEXT,
                evolved_prompt TEXT,
                UNIQUE(character_id, persona_face, version)
            )
        ''')
        cur.execute(
            "INSERT INTO persona_snapshots "
            "(id, character_id, persona_face, version, timestamp, summary, evolved_prompt) "
            "SELECT id, character_id, 'public', version, timestamp, summary, evolved_prompt "
            "FROM _persona_snapshots_v2"
        )
        cur.execute("DROP TABLE _persona_snapshots_v2")

        # persona_traits：加欄位 + 更換 unique index
        cur.execute(
            "ALTER TABLE persona_traits ADD COLUMN persona_face TEXT NOT NULL DEFAULT 'public'"
        )
        cur.execute("DROP INDEX IF EXISTS idx_trait_char_key")
        cur.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_trait_char_face_key "
            "ON persona_traits(character_id, persona_face, trait_key)"
        )

        # 更新 snapshot 相關 index
        cur.execute("DROP INDEX IF EXISTS idx_persona_snap_char_ver")
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_persona_snap_char_ver "
            "ON persona_snapshots(character_id, persona_face, version DESC)"
        )

        # 重建 idx_trait_char_active 加入 persona_face 欄位
        cur.execute("DROP INDEX IF EXISTS idx_trait_char_active")
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_trait_char_active "
            "ON persona_traits(character_id, persona_face, is_active, last_active_version DESC)"
        )

    def _create_persona_v3_schema(self, cur):
        """Path D v3 schema：含 persona_face 的雙 face 架構。

        persona_dimensions.is_active 欄位永久寫 1（歷史 artefact，
        實際活躍狀態由 persona_traits.is_active 持有，讀取時 JOIN 取真值）。
        """
        cur.execute('''
            CREATE TABLE IF NOT EXISTS persona_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                character_id TEXT NOT NULL,
                persona_face TEXT NOT NULL DEFAULT 'public',
                version INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                summary TEXT,
                evolved_prompt TEXT,
                UNIQUE(character_id, persona_face, version)
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS persona_traits (
                trait_key TEXT PRIMARY KEY,
                character_id TEXT NOT NULL,
                persona_face TEXT NOT NULL DEFAULT 'public',
                name TEXT NOT NULL,
                created_version INTEGER NOT NULL,
                last_active_version INTEGER NOT NULL,
                parent_key TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                FOREIGN KEY (parent_key) REFERENCES persona_traits(trait_key) ON DELETE SET NULL
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS persona_dimensions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id INTEGER NOT NULL,
                dimension_key TEXT NOT NULL,
                name TEXT NOT NULL,
                confidence REAL NOT NULL,
                confidence_label TEXT,
                description TEXT NOT NULL,
                parent_name TEXT,
                is_active INTEGER DEFAULT 1,
                FOREIGN KEY (snapshot_id) REFERENCES persona_snapshots(id) ON DELETE CASCADE
            )
        ''')
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_persona_dim_snapshot "
            "ON persona_dimensions(snapshot_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_persona_snap_char_ver "
            "ON persona_snapshots(character_id, persona_face, version DESC)"
        )
        cur.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_trait_char_face_key "
            "ON persona_traits(character_id, persona_face, trait_key)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_trait_char_active "
            "ON persona_traits(character_id, persona_face, is_active, last_active_version DESC)"
        )

    def get_next_persona_version(
        self,
        character_id: str,
        persona_face: str = "public",
    ) -> int:
        """回傳該角色 + face 下一個應使用的 version 號；無紀錄則為 1。"""
        conn = self._init_persona_snapshot_db()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT COALESCE(MAX(version), 0) FROM persona_snapshots "
                "WHERE character_id = ? AND persona_face = ?",
                (character_id, persona_face),
            )
            row = cur.fetchone()
            return int(row[0]) + 1 if row else 1
        finally:
            conn.close()

    def _row_to_snapshot(self, row, dimensions):
        return {
            "id": row[0],
            "character_id": row[1],
            "persona_face": row[2],
            "version": row[3],
            "timestamp": row[4],
            "summary": row[5],
            "evolved_prompt": row[6],
            "dimensions": dimensions,
        }

    def _load_dimensions_for(
        self,
        cursor,
        snapshot_id: int,
        character_id: str | None = None,
        version: int | None = None,
        persona_face: str | None = None,
    ) -> list:
        """讀指定 snapshot 的所有維度明細；is_active / parent_key 來自 persona_traits。

        當傳入 character_id + version 時，額外補入「存在於 persona_traits
        但此版 snapshot 沒有 dimension 記錄」的 trait，以最近已知 confidence 顯示。
        persona_face 用於過濾補充查詢；None 表示不限（向後相容）。
        """
        cursor.execute(
            "SELECT d.dimension_key, d.name, d.confidence, d.confidence_label, "
            "       d.description, d.parent_name, "
            "       COALESCE(t.is_active, 1) AS is_active, "
            "       t.parent_key "
            "FROM persona_dimensions d "
            "LEFT JOIN persona_traits t ON t.trait_key = d.dimension_key "
            "WHERE d.snapshot_id = ? ORDER BY d.id",
            (snapshot_id,),
        )
        result = [
            {
                "dimension_key": r[0],
                "name": r[1],
                "confidence": float(r[2]),
                "confidence_label": r[3],
                "description": r[4],
                "parent_name": r[5],
                "is_active": bool(r[6]),
                "parent_key": r[7],
            }
            for r in cursor.fetchall()
        ]

        if character_id is None or version is None:
            return result

        # 補入此版 snapshot 沒有 dimension 記錄的歷史 trait
        have_keys = {item["dimension_key"] for item in result}

        # 找出這版有 bump last_active_version 但沒寫 dim row 的 trait
        # （即 confidence="none" 的 update），這些不應被補入歷史值
        if persona_face is not None:
            cursor.execute(
                "SELECT trait_key FROM persona_traits "
                "WHERE character_id = ? AND persona_face = ? AND last_active_version = ?",
                (character_id, persona_face, version),
            )
        else:
            cursor.execute(
                "SELECT trait_key FROM persona_traits "
                "WHERE character_id = ? AND last_active_version = ?",
                (character_id, version),
            )
        visited_none_keys = {r[0] for r in cursor.fetchall()} - have_keys

        if persona_face is not None:
            cursor.execute(
                "SELECT t.trait_key, t.name, t.is_active, t.parent_key "
                "FROM persona_traits t "
                "WHERE t.character_id = ? AND t.persona_face = ? AND t.created_version <= ?",
                (character_id, persona_face, version),
            )
        else:
            cursor.execute(
                "SELECT t.trait_key, t.name, t.is_active, t.parent_key "
                "FROM persona_traits t "
                "WHERE t.character_id = ? AND t.created_version <= ?",
                (character_id, version),
            )
        missing = [
            r for r in cursor.fetchall()
            if r[0] not in have_keys and r[0] not in visited_none_keys
        ]
        for r in missing:
            trait_key = r[0]
            if persona_face is not None:
                cursor.execute(
                    "SELECT pd.confidence, pd.confidence_label, pd.description, pd.parent_name "
                    "FROM persona_dimensions pd "
                    "JOIN persona_snapshots ps ON ps.id = pd.snapshot_id "
                    "WHERE pd.dimension_key = ? AND ps.character_id = ? "
                    "  AND ps.persona_face = ? AND ps.version <= ? "
                    "ORDER BY ps.version DESC LIMIT 1",
                    (trait_key, character_id, persona_face, version),
                )
            else:
                cursor.execute(
                    "SELECT pd.confidence, pd.confidence_label, pd.description, pd.parent_name "
                    "FROM persona_dimensions pd "
                    "JOIN persona_snapshots ps ON ps.id = pd.snapshot_id "
                    "WHERE pd.dimension_key = ? AND ps.character_id = ? AND ps.version <= ? "
                    "ORDER BY ps.version DESC LIMIT 1",
                    (trait_key, character_id, version),
                )
            last = cursor.fetchone()
            result.append({
                "dimension_key": trait_key,
                "name": r[1],
                "confidence": float(last[0]) if last else 0.0,
                "confidence_label": last[1] if last else "none",
                "description": last[2] if last else "",
                "parent_name": last[3] if last else None,
                "is_active": bool(r[2]),
                "parent_key": r[3],
            })

        return result

    def get_latest_persona_snapshot(
        self,
        character_id: str,
        persona_face: str = "public",
    ) -> dict | None:
        """回傳該角色 + face 最新一筆 snapshot（含 dimensions）；無紀錄回 None。"""
        conn = self._init_persona_snapshot_db()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, character_id, persona_face, version, timestamp, summary, evolved_prompt "
                "FROM persona_snapshots WHERE character_id = ? AND persona_face = ? "
                "ORDER BY version DESC LIMIT 1",
                (character_id, persona_face),
            )
            row = cur.fetchone()
            if not row:
                return None
            dims = self._load_dimensions_for(cur, row[0], row[1], row[3], row[2])
            return self._row_to_snapshot(row, dims)
        finally:
            conn.close()

    def get_persona_snapshot(
        self,
        character_id: str,
        version: int,
        persona_face: str = "public",
    ) -> dict | None:
        """回傳指定版本的 snapshot（含 dimensions）；找不到回 None。"""
        conn = self._init_persona_snapshot_db()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, character_id, persona_face, version, timestamp, summary, evolved_prompt "
                "FROM persona_snapshots "
                "WHERE character_id = ? AND persona_face = ? AND version = ?",
                (character_id, persona_face, version),
            )
            row = cur.fetchone()
            if not row:
                return None
            dims = self._load_dimensions_for(cur, row[0], row[1], row[3], row[2])
            return self._row_to_snapshot(row, dims)
        finally:
            conn.close()

    def list_persona_snapshots(
        self,
        character_id: str,
        persona_face: str = "public",
    ) -> list:
        """回傳該角色 + face 所有 snapshot 的摘要（不含 dimensions 內容），版本遞增。"""
        conn = self._init_persona_snapshot_db()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT s.id, s.version, s.timestamp, s.summary, "
                "       (SELECT COUNT(*) FROM persona_dimensions d WHERE d.snapshot_id = s.id) "
                "FROM persona_snapshots s "
                "WHERE s.character_id = ? AND s.persona_face = ? "
                "ORDER BY s.version ASC",
                (character_id, persona_face),
            )
            return [
                {
                    "id": r[0],
                    "version": r[1],
                    "timestamp": r[2],
                    "summary": r[3],
                    "dimensions_count": int(r[4]),
                }
                for r in cur.fetchall()
            ]
        finally:
            conn.close()

    def delete_persona_snapshots_by_character(
        self,
        character_id: str,
        persona_face: str | None = None,
    ) -> int:
        """清空指定角色的 snapshot（含 dimensions，靠 CASCADE）。

        persona_face=None → 刪除所有 face；指定 face → 只刪該 face。
        回傳刪除列數。
        """
        conn = self._init_persona_snapshot_db()
        try:
            cur = conn.cursor()
            if persona_face is None:
                cur.execute(
                    "DELETE FROM persona_snapshots WHERE character_id = ?",
                    (character_id,),
                )
            else:
                cur.execute(
                    "DELETE FROM persona_snapshots WHERE character_id = ? AND persona_face = ?",
                    (character_id, persona_face),
                )
            deleted = cur.rowcount
            conn.commit()
            return int(deleted or 0)
        finally:
            conn.close()

    def save_trait_snapshot(
        self,
        character_id: str,
        timestamp: str,
        summary: str,
        evolved_prompt: str,
        updates: list,
        new_traits: list,
        persona_face: str = "public",
        dormancy_idle_versions: int = 3,
        dormancy_confidence_threshold: float = 5.0,
    ) -> int:
        """Path D 原子寫入：一筆 snapshot + updates/new_traits 同交易 + 尾端 B' sweep。

        updates 每筆格式（對既有 trait）::
            {
                "trait_key": str,
                "name": str,
                "description": str,
                "confidence": float,        # 0.0~10.0
                "confidence_label": str,    # high/medium/low/none
                "parent_name": str | None,
            }
          confidence_label=="none" 時不寫 persona_dimensions 列，但仍 bump last_active_version。

        new_traits 每筆格式（新建 trait）::
            {
                "trait_key": str,           # uuid4().hex，由呼叫端生成
                "name": str,
                "description": str,
                "confidence": float,
                "confidence_label": str,
                "parent_key": str | None,
                "parent_name": str | None,
            }

        B' 休眠規則（同交易尾端 sweep）：
          (current_version - last_active_version) >= dormancy_idle_versions
          AND 最近一次 confidence <= dormancy_confidence_threshold → is_active=0。
          本輪觸及的 trait 不受影響（last_active_version==current_version，差值 0）。

        回傳：snapshot_id。
        """
        conn = self._init_persona_snapshot_db()
        try:
            cur = conn.cursor()
            cur.execute("BEGIN IMMEDIATE")

            # 版本號在寫鎖內計算，避免併發 sync 搶同一版本
            cur.execute(
                "SELECT COALESCE(MAX(version), 0) FROM persona_snapshots "
                "WHERE character_id = ? AND persona_face = ?",
                (character_id, persona_face),
            )
            current_version = int(cur.fetchone()[0]) + 1

            cur.execute(
                "INSERT INTO persona_snapshots "
                "(character_id, persona_face, version, timestamp, summary, evolved_prompt) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (character_id, persona_face, current_version, timestamp, summary, evolved_prompt),
            )
            sid = cur.lastrowid

            # ── updates：既有 trait 的 confidence 變動 ──
            for u in updates:
                trait_key = str(u["trait_key"])
                cur.execute(
                    "UPDATE persona_traits "
                    "SET last_active_version = ?, is_active = 1 "
                    "WHERE trait_key = ? AND character_id = ? AND persona_face = ?",
                    (current_version, trait_key, character_id, persona_face),
                )
                if u.get("confidence_label") != "none":
                    cur.execute(
                        "INSERT INTO persona_dimensions "
                        "(snapshot_id, dimension_key, name, confidence, "
                        " confidence_label, description, parent_name, is_active) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
                        (
                            sid,
                            trait_key,
                            str(u["name"]),
                            float(u["confidence"]),
                            u.get("confidence_label"),
                            str(u.get("description", "")),
                            u.get("parent_name"),
                        ),
                    )

            # ── new_traits：本版新建 trait（INSERT 血統表 + 明細表） ──
            for n in new_traits:
                trait_key = str(n["trait_key"])
                parent_key = n.get("parent_key")
                cur.execute(
                    "INSERT INTO persona_traits "
                    "(trait_key, character_id, persona_face, name, "
                    " created_version, last_active_version, parent_key, is_active, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)",
                    (
                        trait_key,
                        character_id,
                        persona_face,
                        str(n["name"]),
                        current_version,
                        current_version,
                        parent_key,
                        timestamp,
                    ),
                )
                cur.execute(
                    "INSERT INTO persona_dimensions "
                    "(snapshot_id, dimension_key, name, confidence, "
                    " confidence_label, description, parent_name, is_active) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
                    (
                        sid,
                        trait_key,
                        str(n["name"]),
                        float(n["confidence"]),
                        n.get("confidence_label"),
                        str(n.get("description", "")),
                        n.get("parent_name"),
                    ),
                )
                # 被引用為 parent 的 trait 自動 reactivate + bump
                if parent_key:
                    cur.execute(
                        "UPDATE persona_traits "
                        "SET last_active_version = ?, is_active = 1 "
                        "WHERE trait_key = ? AND character_id = ? AND persona_face = ?",
                        (current_version, parent_key, character_id, persona_face),
                    )

            # ── B' sweep：同交易尾端掃描休眠候選（限定 persona_face 範圍）──
            cur.execute(
                "SELECT t.trait_key FROM persona_traits t "
                "WHERE t.character_id = ? AND t.persona_face = ? AND t.is_active = 1 "
                "  AND (? - t.last_active_version) >= ? "
                "  AND COALESCE(("
                "    SELECT d.confidence FROM persona_dimensions d "
                "    JOIN persona_snapshots s ON s.id = d.snapshot_id "
                "    WHERE s.character_id = ? AND s.persona_face = ? "
                "          AND d.dimension_key = t.trait_key "
                "    ORDER BY s.version DESC LIMIT 1"
                "  ), 0.0) <= ?",
                (
                    character_id, persona_face,
                    current_version,
                    dormancy_idle_versions,
                    character_id, persona_face,
                    dormancy_confidence_threshold,
                ),
            )
            dormant_keys = [r[0] for r in cur.fetchall()]
            for tk in dormant_keys:
                cur.execute(
                    "UPDATE persona_traits SET is_active = 0 "
                    "WHERE trait_key = ? AND character_id = ? AND persona_face = ?",
                    (tk, character_id, persona_face),
                )

            conn.commit()
            return sid
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get_active_traits(
        self,
        character_id: str,
        persona_face: str = "public",
        limit: int | None = None,
    ) -> list:
        """回傳該角色 + face 當前活躍 trait（is_active=1）清單，按 last_active_version DESC。"""
        return self._get_traits(character_id, persona_face=persona_face, active_only=True, limit=limit)

    def get_all_traits(
        self,
        character_id: str,
        persona_face: str = "public",
        limit: int | None = None,
    ) -> list:
        """回傳該角色 + face 所有 trait（含已休眠）清單。"""
        return self._get_traits(character_id, persona_face=persona_face, active_only=False, limit=limit)

    def _get_traits(
        self,
        character_id: str,
        persona_face: str = "public",
        active_only: bool = True,
        limit: int | None = None,
    ) -> list:
        conn = self._init_persona_snapshot_db()
        try:
            cur = conn.cursor()
            sql = (
                "SELECT "
                "  t.trait_key, t.name, t.created_version, t.last_active_version, "
                "  t.parent_key, t.is_active, "
                "  COALESCE(("
                "    SELECT d.description FROM persona_dimensions d "
                "    JOIN persona_snapshots s ON s.id = d.snapshot_id "
                "    WHERE s.character_id = t.character_id AND s.persona_face = t.persona_face "
                "          AND d.dimension_key = t.trait_key "
                "    ORDER BY s.version DESC LIMIT 1"
                "  ), '') AS last_description "
                "FROM persona_traits t "
                "WHERE t.character_id = ? AND t.persona_face = ?"
            )
            if active_only:
                sql += " AND t.is_active = 1"
            sql += " ORDER BY t.last_active_version DESC"

            params: tuple = (character_id, persona_face)
            if limit is not None:
                sql += " LIMIT ?"
                params = (character_id, persona_face, int(limit))
            cur.execute(sql, params)
            return [
                {
                    "trait_key": r[0],
                    "name": r[1],
                    "created_version": int(r[2]),
                    "last_active_version": int(r[3]),
                    "parent_key": r[4],
                    "is_active": bool(r[5]),
                    "last_description": r[6],
                }
                for r in cur.fetchall()
            ]
        finally:
            conn.close()

    def get_trait_timeline(
        self,
        character_id: str,
        trait_key: str,
        persona_face: str = "public",
    ) -> list:
        """回傳指定 trait 在所有版本的 confidence 變化序列（折線圖用）。

        confidence 為 none 的版本因不寫 persona_dimensions 列，在此序列中會缺席。
        """
        conn = self._init_persona_snapshot_db()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT s.version, s.timestamp, d.confidence, d.confidence_label "
                "FROM persona_snapshots s "
                "JOIN persona_dimensions d ON d.snapshot_id = s.id "
                "WHERE s.character_id = ? AND s.persona_face = ? AND d.dimension_key = ? "
                "ORDER BY s.version ASC",
                (character_id, persona_face, trait_key),
            )
            return [
                {
                    "version": int(r[0]),
                    "timestamp": r[1],
                    "confidence": float(r[2]),
                    "confidence_label": r[3],
                }
                for r in cur.fetchall()
            ]
        finally:
            conn.close()
