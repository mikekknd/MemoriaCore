# 【環境假設】：Python 3.12, numpy 庫可用。使用內建 sqlite3。支援 Schema Evolution。
import json
import os
import re
import sqlite3
import numpy as np
from datetime import datetime, timedelta

class StorageManager:
    def __init__(self, prefs_file="user_prefs.json", history_file="chat_history.json"):
        self.prefs_file = prefs_file
        self.history_file = history_file

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
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS memory_blocks (
                block_id TEXT PRIMARY KEY,
                timestamp TEXT,
                overview TEXT,
                overview_vector BLOB,
                sparse_vector TEXT,
                raw_dialogues TEXT
            )
        ''')
        
        # 【Schema Evolution】：動態檢查並新增缺失欄位
        cursor.execute("PRAGMA table_info(memory_blocks)")
        columns = [info[1] for info in cursor.fetchall()]
        if 'is_consolidated' not in columns:
            cursor.execute("ALTER TABLE memory_blocks ADD COLUMN is_consolidated INTEGER DEFAULT 0")
        if 'encounter_count' not in columns:
            cursor.execute("ALTER TABLE memory_blocks ADD COLUMN encounter_count REAL DEFAULT 1.0")
        if 'potential_preferences' not in columns:
            cursor.execute("ALTER TABLE memory_blocks ADD COLUMN potential_preferences TEXT DEFAULT '[]'")
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS core_memories (
                core_id TEXT PRIMARY KEY,
                timestamp TEXT,
                insight TEXT,
                insight_vector BLOB,
                encounter_count REAL DEFAULT 1.0
            )
        ''')
        # 【Schema Evolution】：core_memories 追加 encounter_count
        cursor.execute("PRAGMA table_info(core_memories)")
        core_columns = [info[1] for info in cursor.fetchall()]
        if 'encounter_count' not in core_columns:
            cursor.execute("ALTER TABLE core_memories ADD COLUMN encounter_count REAL DEFAULT 1.0")

        # 【Schema Evolution】：使用者畫像 (User Profile) 資料表
        # 複合主鍵 (fact_key, fact_value)，允許同一 key 儲存多個不同 value
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_profile (
                fact_key TEXT NOT NULL,
                fact_value TEXT NOT NULL,
                category TEXT,
                confidence REAL DEFAULT 1.0,
                timestamp TEXT,
                source_context TEXT,
                PRIMARY KEY (fact_key, fact_value)
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_profile_vectors (
                fact_key TEXT NOT NULL,
                fact_value TEXT NOT NULL,
                fact_vector BLOB,
                PRIMARY KEY (fact_key, fact_value),
                FOREIGN KEY (fact_key, fact_value) REFERENCES user_profile(fact_key, fact_value) ON DELETE CASCADE
            )
        ''')

        # 【Schema Migration】：user_profile 從單一 PK 遷移至複合 PK
        cursor.execute("PRAGMA table_info(user_profile)")
        up_cols = {info[1]: info for info in cursor.fetchall()}
        # 舊 schema: fact_key pk=1, fact_value pk=0 → 需要遷移
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

            # 同步遷移 vectors 表：補上 fact_value 欄位
            cursor.execute("ALTER TABLE user_profile_vectors RENAME TO _user_profile_vectors_old")
            cursor.execute('''
                CREATE TABLE user_profile_vectors (
                    fact_key TEXT NOT NULL,
                    fact_value TEXT NOT NULL,
                    fact_vector BLOB,
                    PRIMARY KEY (fact_key, fact_value),
                    FOREIGN KEY (fact_key, fact_value) REFERENCES user_profile(fact_key, fact_value) ON DELETE CASCADE
                )
            ''')
            cursor.execute('''
                INSERT INTO user_profile_vectors
                SELECT v.fact_key, p.fact_value, v.fact_vector
                FROM _user_profile_vectors_old v
                JOIN user_profile p ON v.fact_key = p.fact_key
            ''')
            cursor.execute("DROP TABLE _user_profile_vectors_old")

        # 【Schema Evolution】：AI 個性觀察資料表
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
                encounter_count REAL DEFAULT 1.0
            )
        ''')

        # 【Schema Evolution】：主動話題快取資料表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS topic_cache (
                topic_id TEXT PRIMARY KEY,
                interest_keyword TEXT,
                summary_content TEXT,
                created_at TEXT,
                is_mentioned_to_user INTEGER DEFAULT 0
            )
        ''')

        conn.commit()
        return conn

    # ════════════════════════════════════════════════════════════
    # SECTION: 情境記憶 Memory Blocks — 載入 / 儲存
    # ════════════════════════════════════════════════════════════

    def load_db(self, db_path):
        if not os.path.exists(db_path):
            return []
        
        conn = self._init_db(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT block_id, timestamp, overview, overview_vector, sparse_vector, raw_dialogues, is_consolidated, encounter_count, potential_preferences FROM memory_blocks")
        rows = cursor.fetchall()

        memory_blocks = []
        for row in rows:
            block_id, timestamp, overview, overview_vector_blob, sparse_vector_json, raw_dialogues_json, is_consolidated, encounter_count, potential_preferences_json = row
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
                "potential_preferences": json.loads(potential_preferences_json) if potential_preferences_json else []
            })
            
        conn.close()
        return memory_blocks

    def save_db(self, db_path, memory_blocks):
        conn = self._init_db(db_path)
        cursor = conn.cursor()
        try:
            cursor.execute("BEGIN")
            cursor.execute("DELETE FROM memory_blocks")

            for block in memory_blocks:
                vector_blob = np.array(block["overview_vector"], dtype=np.float32).tobytes()
                sparse_json = json.dumps(block.get("sparse_vector", {}), ensure_ascii=False)
                dialogues_json = json.dumps(block.get("raw_dialogues", []), ensure_ascii=False)
                is_cons = 1 if block.get("is_consolidated", False) else 0
                enc_count = float(block.get("encounter_count", 1.0))
                prefs_json = json.dumps(block.get("potential_preferences", []), ensure_ascii=False)

                cursor.execute('''
                    INSERT INTO memory_blocks (block_id, timestamp, overview, overview_vector, sparse_vector, raw_dialogues, is_consolidated, encounter_count, potential_preferences)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (block["block_id"], block["timestamp"], block["overview"], vector_blob, sparse_json, dialogues_json, is_cons, enc_count, prefs_json))

            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ════════════════════════════════════════════════════════════
    # SECTION: 核心認知 Core Memory — 載入 / Upsert
    # ════════════════════════════════════════════════════════════

    def load_core_db(self, db_path):
        if not os.path.exists(db_path):
            return []
        conn = self._init_db(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT core_id, timestamp, insight, insight_vector, encounter_count FROM core_memories")
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
                "encounter_count": float(encounter_count) if encounter_count is not None else 1.0
            })
        conn.close()
        return core_memories

    def save_core_memory(self, db_path, core_id, timestamp, insight, insight_vector, encounter_count=1.0):
        conn = self._init_db(db_path)
        cursor = conn.cursor()
        vector_blob = np.array(insight_vector, dtype=np.float32).tobytes()
        cursor.execute('''
            INSERT OR REPLACE INTO core_memories (core_id, timestamp, insight, insight_vector, encounter_count)
            VALUES (?, ?, ?, ?, ?)
        ''', (core_id, timestamp, insight, vector_blob, encounter_count))
        conn.commit()
        conn.close()

    # ==========================================
    # 使用者畫像 (User Profile) CRUD
    # ==========================================
    # ════════════════════════════════════════════════════════════
    # SECTION: 使用者偏好 Profile Facts — Upsert / 刪除 / 查詢 / 向量
    # ════════════════════════════════════════════════════════════

    def upsert_profile(self, db_path, fact_key, fact_value, category, source_context="", confidence=1.0):
        """新增或更新一筆使用者事實"""
        conn = self._init_db(db_path)
        cursor = conn.cursor()
        timestamp = datetime.now().isoformat()
        cursor.execute('''
            INSERT OR REPLACE INTO user_profile (fact_key, fact_value, category, confidence, timestamp, source_context)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (fact_key, fact_value, category, confidence, timestamp, source_context))
        conn.commit()
        conn.close()

    def upsert_profile_vector(self, db_path, fact_key, fact_value, fact_vector):
        """新增或更新一筆使用者事實的向量（複合鍵：fact_key + fact_value）"""
        conn = self._init_db(db_path)
        cursor = conn.cursor()
        vector_blob = np.array(fact_vector, dtype=np.float32).tobytes()
        cursor.execute('''
            INSERT OR REPLACE INTO user_profile_vectors (fact_key, fact_value, fact_vector)
            VALUES (?, ?, ?)
        ''', (fact_key, fact_value, vector_blob))
        conn.commit()
        conn.close()

    def delete_profile(self, db_path, fact_key, fact_value=None):
        """刪除使用者事實（同時清除向量）。若指定 fact_value 則精準刪除，否則刪除該 key 下所有值。"""
        conn = self._init_db(db_path)
        cursor = conn.cursor()
        if fact_value is not None:
            cursor.execute("DELETE FROM user_profile_vectors WHERE fact_key = ? AND fact_value = ?", (fact_key, fact_value))
            cursor.execute("DELETE FROM user_profile WHERE fact_key = ? AND fact_value = ?", (fact_key, fact_value))
        else:
            cursor.execute("DELETE FROM user_profile_vectors WHERE fact_key = ?", (fact_key,))
            cursor.execute("DELETE FROM user_profile WHERE fact_key = ?", (fact_key,))
        conn.commit()
        conn.close()

    def load_all_profiles(self, db_path, include_tombstones=False):
        """載入所有使用者事實（預設排除墓碑記錄 confidence < 0）"""
        if not os.path.exists(db_path):
            return []
        conn = self._init_db(db_path)
        cursor = conn.cursor()
        where_clause = "" if include_tombstones else " WHERE confidence >= 0"
        cursor.execute(f"SELECT fact_key, fact_value, category, confidence, timestamp, source_context FROM user_profile{where_clause} ORDER BY category, fact_key")
        rows = cursor.fetchall()
        conn.close()
        return [{"fact_key": r[0], "fact_value": r[1], "category": r[2], "confidence": r[3], "timestamp": r[4], "source_context": r[5]} for r in rows]

    def load_profiles_by_category(self, db_path, category):
        """按分類篩選使用者事實（自動排除墓碑記錄）"""
        if not os.path.exists(db_path):
            return []
        conn = self._init_db(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT fact_key, fact_value, category, confidence, timestamp, source_context FROM user_profile WHERE category = ? AND confidence >= 0", (category,))
        rows = cursor.fetchall()
        conn.close()
        return [{"fact_key": r[0], "fact_value": r[1], "category": r[2], "confidence": r[3], "timestamp": r[4], "source_context": r[5]} for r in rows]

    def load_profile_vectors(self, db_path):
        """載入所有使用者事實及其向量（自動排除墓碑記錄，供語意搜尋用）"""
        if not os.path.exists(db_path):
            return []
        conn = self._init_db(db_path)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT p.fact_key, p.fact_value, p.category, p.confidence, v.fact_vector
            FROM user_profile p
            LEFT JOIN user_profile_vectors v ON p.fact_key = v.fact_key AND p.fact_value = v.fact_value
            WHERE p.confidence >= 0
        ''')
        rows = cursor.fetchall()
        conn.close()
        results = []
        for r in rows:
            vec = np.frombuffer(r[4], dtype=np.float32).tolist() if r[4] else []
            results.append({"fact_key": r[0], "fact_value": r[1], "category": r[2], "confidence": r[3], "fact_vector": vec})
        return results

    def get_profile_by_key(self, db_path, fact_key, fact_value=None):
        """查詢 profile（含墓碑記錄）。若指定 fact_value 則精準查詢單筆，否則回傳該 key 下所有值的 list。"""
        if not os.path.exists(db_path):
            return [] if fact_value is None else None
        conn = self._init_db(db_path)
        cursor = conn.cursor()
        if fact_value is not None:
            cursor.execute("SELECT fact_key, fact_value, category, confidence FROM user_profile WHERE fact_key = ? AND fact_value = ?", (fact_key, fact_value))
            row = cursor.fetchone()
            conn.close()
            return {"fact_key": row[0], "fact_value": row[1], "category": row[2], "confidence": row[3]} if row else None
        else:
            cursor.execute("SELECT fact_key, fact_value, category, confidence FROM user_profile WHERE fact_key = ?", (fact_key,))
            rows = cursor.fetchall()
            conn.close()
            return [{"fact_key": r[0], "fact_value": r[1], "category": r[2], "confidence": r[3]} for r in rows]

    # ==========================================
    # 話題快取 (Topic Cache) CRUD
    # ==========================================
    # ════════════════════════════════════════════════════════════
    # SECTION: 主動話題 Topic Cache — 插入 / 查未提及 / 標記已提
    # ════════════════════════════════════════════════════════════

    def insert_topic_cache(self, db_path, topic_id, interest_keyword, summary_content):
        conn = self._init_db(db_path)
        cursor = conn.cursor()
        now = datetime.now().isoformat()
        cursor.execute('''
            INSERT OR REPLACE INTO topic_cache (topic_id, interest_keyword, summary_content, created_at, is_mentioned_to_user)
            VALUES (?, ?, ?, ?, 0)
        ''', (topic_id, interest_keyword, summary_content, now))
        conn.commit()
        conn.close()

    def get_unmentioned_topics(self, db_path, limit=3):
        if not os.path.exists(db_path):
            return []
        conn = self._init_db(db_path)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT topic_id, interest_keyword, summary_content, created_at
            FROM topic_cache
            WHERE is_mentioned_to_user = 0
            ORDER BY created_at DESC
            LIMIT ?
        ''', (limit,))
        rows = cursor.fetchall()
        conn.close()
        return [{"topic_id": r[0], "interest_keyword": r[1], "summary_content": r[2], "created_at": r[3]} for r in rows]

    def mark_topic_mentioned(self, db_path, topic_id):
        conn = self._init_db(db_path)
        cursor = conn.cursor()
        cursor.execute('UPDATE topic_cache SET is_mentioned_to_user = 1 WHERE topic_id = ?', (topic_id,))
        conn.commit()
        conn.close()

    # ==========================================
    # 對話紀錄持久化 (conversation.db)
    # ==========================================
    _CONV_DB = "conversation.db"

    # ════════════════════════════════════════════════════════════
    # SECTION: 對話 conversation.db — Schema / Sessions / Messages / Bridge Point
    # ════════════════════════════════════════════════════════════

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
                bridge_after_msg_id INTEGER DEFAULT 0
            )
        ''')
        # 漸進式 schema 升級：舊 DB 可能缺少 bridge_after_msg_id 欄位
        try:
            cursor.execute("SELECT bridge_after_msg_id FROM conversation_sessions LIMIT 1")
        except sqlite3.OperationalError:
            cursor.execute("ALTER TABLE conversation_sessions ADD COLUMN bridge_after_msg_id INTEGER DEFAULT 0")
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
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_conv_msg_session ON conversation_messages(session_id)')
        conn.commit()
        return conn

    def create_conversation_session(self, session_id, channel="rest", channel_uid=""):
        conn = self._init_conversation_db()
        cursor = conn.cursor()
        now = datetime.now().isoformat()
        cursor.execute('''
            INSERT OR IGNORE INTO conversation_sessions (session_id, channel, channel_uid, created_at, last_active)
            VALUES (?, ?, ?, ?, ?)
        ''', (session_id, channel, channel_uid, now, now))
        conn.commit()
        conn.close()

    def save_conversation_message(self, session_id, role, content, debug_info=None):
        conn = self._init_conversation_db()
        cursor = conn.cursor()
        now = datetime.now().isoformat()
        debug_json = json.dumps(debug_info, ensure_ascii=False) if debug_info else None
        cursor.execute('''
            INSERT INTO conversation_messages (session_id, role, content, debug_info, timestamp)
            VALUES (?, ?, ?, ?, ?)
        ''', (session_id, role, content, debug_json, now))
        # 同步更新 session last_active
        cursor.execute('UPDATE conversation_sessions SET last_active = ? WHERE session_id = ?', (now, session_id))
        conn.commit()
        conn.close()

    def load_conversation_messages(self, session_id, since_msg_id: int = 0):
        """載入對話訊息。since_msg_id > 0 時只載入該 msg_id 之後的訊息（用於 bridge 還原）。"""
        conn = self._init_conversation_db()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT role, content, debug_info, timestamp
            FROM conversation_messages WHERE session_id = ? AND msg_id > ?
            ORDER BY msg_id ASC
        ''', (session_id, since_msg_id))
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
        # 取得倒數第 N+1 筆的 msg_id 作為截斷點
        cursor.execute('''
            SELECT msg_id FROM conversation_messages
            WHERE session_id = ?
            ORDER BY msg_id DESC LIMIT 1 OFFSET ?
        ''', (session_id, keep_last_n))
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
            cursor.execute('''
                SELECT s.session_id, s.channel, s.channel_uid, s.created_at, s.last_active, s.is_active,
                       (SELECT COUNT(*) FROM conversation_messages m WHERE m.session_id = s.session_id)
                FROM conversation_sessions s WHERE s.channel = ?
                ORDER BY s.last_active DESC LIMIT ?
            ''', (channel, limit))
        else:
            cursor.execute('''
                SELECT s.session_id, s.channel, s.channel_uid, s.created_at, s.last_active, s.is_active,
                       (SELECT COUNT(*) FROM conversation_messages m WHERE m.session_id = s.session_id)
                FROM conversation_sessions s
                ORDER BY s.last_active DESC LIMIT ?
            ''', (limit,))
        rows = cursor.fetchall()
        conn.close()
        return [{"session_id": r[0], "channel": r[1], "channel_uid": r[2],
                 "created_at": r[3], "last_active": r[4], "is_active": bool(r[5]),
                 "message_count": r[6]} for r in rows]

    def deactivate_session(self, session_id):
        conn = self._init_conversation_db()
        cursor = conn.cursor()
        cursor.execute('UPDATE conversation_sessions SET is_active = 0 WHERE session_id = ?', (session_id,))
        conn.commit()
        conn.close()

    def reactivate_session(self, session_id):
        """重新標記 session 為活躍"""
        conn = self._init_conversation_db()
        cursor = conn.cursor()
        now = datetime.now().isoformat()
        cursor.execute('UPDATE conversation_sessions SET is_active = 1, last_active = ? WHERE session_id = ?', (now, session_id))
        conn.commit()
        conn.close()

    def hard_delete_session(self, session_id):
        """永久刪除指定 session 及其所有訊息"""
        conn = self._init_conversation_db()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM conversation_messages WHERE session_id = ?', (session_id,))
        cursor.execute('DELETE FROM conversation_sessions WHERE session_id = ?', (session_id,))
        conn.commit()
        conn.close()

    def hard_delete_sessions_older_than(self, days: int) -> int:
        """永久刪除 N 天前的 session 及其訊息，回傳刪除數量"""
        conn = self._init_conversation_db()
        cursor = conn.cursor()
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        # 找出符合條件的 session_ids
        cursor.execute(
            'SELECT session_id FROM conversation_sessions WHERE last_active < ?', (cutoff,)
        )
        old_ids = [r[0] for r in cursor.fetchall()]
        if old_ids:
            placeholders = ','.join('?' * len(old_ids))
            cursor.execute(f'DELETE FROM conversation_messages WHERE session_id IN ({placeholders})', old_ids)
            cursor.execute(f'DELETE FROM conversation_sessions WHERE session_id IN ({placeholders})', old_ids)
            conn.commit()
        conn.close()
        return len(old_ids)

    def get_session_info(self, session_id):
        """取得單一 session 的元資料"""
        conn = self._init_conversation_db()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT s.session_id, s.channel, s.channel_uid, s.created_at, s.last_active, s.is_active,
                   (SELECT COUNT(*) FROM conversation_messages m WHERE m.session_id = s.session_id)
            FROM conversation_sessions s WHERE s.session_id = ?
        ''', (session_id,))
        r = cursor.fetchone()
        conn.close()
        if not r:
            return None
        return {"session_id": r[0], "channel": r[1], "channel_uid": r[2],
                "created_at": r[3], "last_active": r[4], "is_active": bool(r[5]),
                "message_count": r[6]}

    # ════════════════════════════════════════════════════════════
    # SECTION: 訊息統計 — 給 PersonaSync 等背景任務查閱閒置 / 訊息量
    # ════════════════════════════════════════════════════════════

    def get_last_message_time(self) -> "datetime | None":
        """回傳 conversation_messages 表中最後一筆訊息的 timestamp（datetime 物件）。
        無任何訊息時回傳 None。涵蓋所有 channel（WS + REST）。"""
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

    def count_messages_since(self, since_iso: str) -> int:
        """計算 since_iso 時間點之後的訊息數（含 user 與 assistant）。
        since_iso 格式：ISO 8601 字串，例如 '2026-04-15T10:00:00'。
        查詢失敗或格式錯誤時回傳 0。"""
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