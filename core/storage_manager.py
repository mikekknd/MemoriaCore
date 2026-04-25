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

    # ════════════════════════════════════════════════════════════
    # SECTION: 人格演化 Snapshots — 版本儲存 / 血統查詢 / 時間序列
    # ════════════════════════════════════════════════════════════

    def _init_persona_snapshot_db(self):
        """初始化人格演化 snapshot 資料表 — PRAGMA user_version 驅動的 schema migration。

        Schema 版本：
        - ``user_version == 0`` — 空 DB 或舊 6 維度 prototype。自動 DROP 舊表 + 建 v2 新表。
        - ``user_version == 2`` — Path D trait tree schema（``persona_traits`` 真實血統
          表 + ``persona_dimensions`` 版本快照明細）。
        - 其他值 — 拒絕啟動（防止半毀 DB）。

        Drop-rebuild 策略：舊 prototype 資料為 seed 腳本產物，確認可 drop；以
        ``BEGIN IMMEDIATE`` 取得寫鎖避免啟動期 race（多執行緒啟動時只有第一個會真的 drop）。
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
                # 雙重檢查：若另一執行緒已搶先升級過，這裡會讀到 2，直接跳過 drop
                cur.execute("PRAGMA user_version")
                if cur.fetchone()[0] == 0:
                    # 順序：child(dimensions) → self-ref(traits) → parent(snapshots)
                    cur.execute("DROP TABLE IF EXISTS persona_dimensions")
                    cur.execute("DROP TABLE IF EXISTS persona_traits")
                    cur.execute("DROP TABLE IF EXISTS persona_snapshots")
                    self._create_persona_v2_schema(cur)
                    # PRAGMA 不支援 parameter binding，版本號為常數拼接安全無 injection 風險
                    cur.execute("PRAGMA user_version = 2")
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        elif user_version == 2:
            # 已是 v2（或剛升級完），確保表存在（新 DB 或併發創建情境）
            self._create_persona_v2_schema(cur)
            conn.commit()
        else:
            raise RuntimeError(
                f"persona_snapshots.db 發現無法識別的 user_version={user_version}"
                f"（預期 0 或 2）— 拒絕啟動以防半毀 DB"
            )
        return conn

    def _create_persona_v2_schema(self, cur):
        """Path D schema：``persona_traits`` 是跨版本真實血統表；``persona_dimensions``
        是各版 snapshot 的明細（``dimension_key`` 語意為 trait UUID）。

        ``persona_dimensions.is_active`` 欄位永久寫 1（歷史 artefact，實際活躍狀態由
        ``persona_traits.is_active`` 持有）。讀取時以 JOIN 取得真正值。
        """
        cur.execute('''
            CREATE TABLE IF NOT EXISTS persona_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                character_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                summary TEXT,
                evolved_prompt TEXT,
                UNIQUE(character_id, version)
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS persona_traits (
                trait_key TEXT PRIMARY KEY,
                character_id TEXT NOT NULL,
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
            "ON persona_snapshots(character_id, version DESC)"
        )
        cur.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_trait_char_key "
            "ON persona_traits(character_id, trait_key)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_trait_char_active "
            "ON persona_traits(character_id, is_active, last_active_version DESC)"
        )

    def get_next_persona_version(self, character_id: str) -> int:
        """回傳該角色下一個應使用的 version 號；無紀錄則為 1。"""
        conn = self._init_persona_snapshot_db()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT COALESCE(MAX(version), 0) FROM persona_snapshots WHERE character_id = ?",
                (character_id,),
            )
            row = cur.fetchone()
            return int(row[0]) + 1 if row else 1
        finally:
            conn.close()

    def _row_to_snapshot(self, row, dimensions):
        return {
            "id": row[0],
            "character_id": row[1],
            "version": row[2],
            "timestamp": row[3],
            "summary": row[4],
            "evolved_prompt": row[5],
            "dimensions": dimensions,
        }

    def _load_dimensions_for(
        self,
        cursor,
        snapshot_id: int,
        character_id: str | None = None,
        version: int | None = None,
    ) -> list:
        """讀指定 snapshot 的所有維度明細；``is_active`` / ``parent_key`` 來自
        ``persona_traits`` 表（跨版本真實狀態），未建 trait 列的筆（防禦性情境）
        預設 active、無 parent_key。

        當傳入 ``character_id`` + ``version`` 時，額外補入「存在於 ``persona_traits``
        但此版 snapshot 沒有 dimension 記錄」的 trait（例如 confidence=none 未寫入的
        節點），以其最近一次已知的 confidence 值顯示、保持當前 ``is_active`` 狀態。
        這樣 deactivated trait 在樹裡會以淡化節點呈現，而非直接消失。
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
        cursor.execute(
            "SELECT t.trait_key, t.name, t.is_active, t.parent_key "
            "FROM persona_traits t "
            "WHERE t.character_id = ? AND t.created_version <= ?",
            (character_id, version),
        )
        missing = [r for r in cursor.fetchall() if r[0] not in have_keys]
        for r in missing:
            trait_key = r[0]
            # 取截至此版本最近一次有記錄的 confidence/description
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

    def get_latest_persona_snapshot(self, character_id: str) -> dict | None:
        """回傳該角色最新一筆 snapshot（含 dimensions）；無紀錄回 None。"""
        conn = self._init_persona_snapshot_db()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, character_id, version, timestamp, summary, evolved_prompt "
                "FROM persona_snapshots WHERE character_id = ? "
                "ORDER BY version DESC LIMIT 1",
                (character_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            dims = self._load_dimensions_for(cur, row[0], row[1], row[2])
            return self._row_to_snapshot(row, dims)
        finally:
            conn.close()

    def get_persona_snapshot(self, character_id: str, version: int) -> dict | None:
        """回傳指定版本的 snapshot（含 dimensions）；找不到回 None。"""
        conn = self._init_persona_snapshot_db()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, character_id, version, timestamp, summary, evolved_prompt "
                "FROM persona_snapshots WHERE character_id = ? AND version = ?",
                (character_id, version),
            )
            row = cur.fetchone()
            if not row:
                return None
            dims = self._load_dimensions_for(cur, row[0], row[1], row[2])
            return self._row_to_snapshot(row, dims)
        finally:
            conn.close()

    def list_persona_snapshots(self, character_id: str) -> list:
        """回傳該角色所有 snapshot 的摘要（不含 dimensions 內容），版本遞增。"""
        conn = self._init_persona_snapshot_db()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT s.id, s.version, s.timestamp, s.summary, "
                "       (SELECT COUNT(*) FROM persona_dimensions d WHERE d.snapshot_id = s.id) "
                "FROM persona_snapshots s WHERE s.character_id = ? "
                "ORDER BY s.version ASC",
                (character_id,),
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

    def delete_persona_snapshots_by_character(self, character_id: str) -> int:
        """清空指定角色所有 snapshot（含 dimensions，靠 CASCADE）。回傳刪除列數。

        用於管理工具 / 測試 seeding 時的覆寫場景。
        """
        conn = self._init_persona_snapshot_db()
        try:
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM persona_snapshots WHERE character_id = ?",
                (character_id,),
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
        dormancy_idle_versions: int = 3,
        dormancy_confidence_threshold: float = 5.0,
    ) -> int:
        """Path D 原子寫入：一筆 snapshot + updates/new_traits 同交易 + 尾端 B' sweep。

        ``updates`` 每筆格式（對既有 trait）::
            {
                "trait_key": str,
                "name": str,                    # denormalised 顯示用
                "description": str,
                "confidence": float,            # 0.0~10.0
                "confidence_label": str,        # high/medium/low/none
                "parent_name": str | None,      # denormalised 顯示用
            }
          - ``confidence_label == "none"`` 時不寫 persona_dimensions 列，但仍 bump
            ``last_active_version``（代表「本輪 LLM 有注意到這個 trait」）。

        ``new_traits`` 每筆格式（新建 trait）::
            {
                "trait_key": str,              # uuid4().hex，由呼叫端生成
                "name": str,
                "description": str,
                "confidence": float,
                "confidence_label": str,
                "parent_key": str | None,      # 指向 persona_traits.trait_key，NULL 表 root
                "parent_name": str | None,     # denormalised 顯示用
            }
          - ``parent_key`` 指定的 trait 會被 reactivate 並 bump last_active_version。

        B' 休眠規則（同交易內 sweep）：
          ``(current_version - last_active_version) >= dormancy_idle_versions`` AND
          最近一次 ``confidence <= dormancy_confidence_threshold`` → ``is_active = 0``。
          本輪 update / 新建 / 被引用為 parent 的 trait 不會被掃（它們的
          ``last_active_version == current_version``，差值為 0）。

        回傳：``snapshot_id``。
        """
        conn = self._init_persona_snapshot_db()
        try:
            cur = conn.cursor()
            cur.execute("BEGIN IMMEDIATE")

            # 版本號在寫鎖內計算，避免併發 sync 搶同一版本
            cur.execute(
                "SELECT COALESCE(MAX(version), 0) FROM persona_snapshots WHERE character_id = ?",
                (character_id,),
            )
            current_version = int(cur.fetchone()[0]) + 1

            cur.execute(
                "INSERT INTO persona_snapshots "
                "(character_id, version, timestamp, summary, evolved_prompt) "
                "VALUES (?, ?, ?, ?, ?)",
                (character_id, current_version, timestamp, summary, evolved_prompt),
            )
            sid = cur.lastrowid

            # ── updates：既有 trait 的 confidence 變動 ──
            for u in updates:
                trait_key = str(u["trait_key"])
                cur.execute(
                    "UPDATE persona_traits "
                    "SET last_active_version = ?, is_active = 1 "
                    "WHERE trait_key = ? AND character_id = ?",
                    (current_version, trait_key, character_id),
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
                    "(trait_key, character_id, name, created_version, last_active_version, "
                    " parent_key, is_active, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, 1, ?)",
                    (
                        trait_key,
                        character_id,
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
                        "WHERE trait_key = ? AND character_id = ?",
                        (current_version, parent_key, character_id),
                    )

            # ── B' sweep：同交易尾端掃描休眠候選 ──
            # 條件：is_active=1 AND 閒置版本 >= N AND 最近一次 confidence <= threshold
            cur.execute(
                "SELECT t.trait_key FROM persona_traits t "
                "WHERE t.character_id = ? AND t.is_active = 1 "
                "  AND (? - t.last_active_version) >= ? "
                "  AND COALESCE(("
                "    SELECT d.confidence FROM persona_dimensions d "
                "    JOIN persona_snapshots s ON s.id = d.snapshot_id "
                "    WHERE s.character_id = ? AND d.dimension_key = t.trait_key "
                "    ORDER BY s.version DESC LIMIT 1"
                "  ), 0.0) <= ?",
                (
                    character_id,
                    current_version,
                    dormancy_idle_versions,
                    character_id,
                    dormancy_confidence_threshold,
                ),
            )
            dormant_keys = [r[0] for r in cur.fetchall()]
            for tk in dormant_keys:
                cur.execute(
                    "UPDATE persona_traits SET is_active = 0 "
                    "WHERE trait_key = ? AND character_id = ?",
                    (tk, character_id),
                )

            conn.commit()
            return sid
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get_active_traits(self, character_id: str, limit: int | None = None) -> list:
        """回傳該角色當前活躍 trait（``is_active = 1``）清單，按
        ``last_active_version DESC`` 排序，附帶最近一次 description（供 prompt 注入）。

        等價於 ``_get_traits(character_id, active_only=True, limit=limit)``。
        """
        return self._get_traits(character_id, active_only=True, limit=limit)

    def get_all_traits(self, character_id: str, limit: int | None = None) -> list:
        """回傳該角色所有 trait（**含已休眠**）清單，按 ``last_active_version DESC`` 排序。

        用途：``PersonaSnapshotStore`` 驗證 LLM 回傳的 ``parent_key`` / ``trait_key``
        是否存在於歷史（已 sweep 的 trait 仍可被引用 → 自動 reactivate）。
        """
        return self._get_traits(character_id, active_only=False, limit=limit)

    def _get_traits(
        self,
        character_id: str,
        active_only: bool,
        limit: int | None,
    ) -> list:
        """內部共用查詢，透過 ``active_only`` flag 決定是否過濾 ``is_active = 1``。

        回傳格式（與 ``get_active_traits`` 一致）::
            [{
                "trait_key": str,
                "name": str,
                "last_description": str,
                "created_version": int,
                "last_active_version": int,
                "parent_key": str | None,
                "is_active": bool,
            }, ...]
        """
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
                "    WHERE s.character_id = t.character_id AND d.dimension_key = t.trait_key "
                "    ORDER BY s.version DESC LIMIT 1"
                "  ), '') AS last_description "
                "FROM persona_traits t "
                "WHERE t.character_id = ?"
            )
            if active_only:
                sql += " AND t.is_active = 1"
            sql += " ORDER BY t.last_active_version DESC"

            params: tuple = (character_id,)
            if limit is not None:
                sql += " LIMIT ?"
                params = (character_id, int(limit))
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

    def get_trait_timeline(self, character_id: str, trait_key: str) -> list:
        """回傳指定 trait 在所有版本的 confidence 變化序列（折線圖用）。

        回傳：``[{"version": int, "timestamp": str, "confidence": float,
        "confidence_label": str}, ...]``，版本遞增排序。confidence 為 none 的版本
        因不寫 ``persona_dimensions`` 列，在此序列中會缺席（代表該版 LLM 仍注意到，
        但未顯著表現）。
        """
        conn = self._init_persona_snapshot_db()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT s.version, s.timestamp, d.confidence, d.confidence_label "
                "FROM persona_snapshots s "
                "JOIN persona_dimensions d ON d.snapshot_id = s.id "
                "WHERE s.character_id = ? AND d.dimension_key = ? "
                "ORDER BY s.version ASC",
                (character_id, trait_key),
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