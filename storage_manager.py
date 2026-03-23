# 【環境假設】：Python 3.12, numpy 庫可用。使用內建 sqlite3。支援 Schema Evolution。
import json
import os
import re
import sqlite3
import numpy as np
from datetime import datetime

class StorageManager:
    def __init__(self, prefs_file="user_prefs.json", history_file="chat_history.json"):
        self.prefs_file = prefs_file
        self.history_file = history_file

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
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_profile (
                fact_key TEXT PRIMARY KEY,
                fact_value TEXT,
                category TEXT,
                confidence REAL DEFAULT 1.0,
                timestamp TEXT,
                source_context TEXT
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_profile_vectors (
                fact_key TEXT PRIMARY KEY,
                fact_vector BLOB,
                FOREIGN KEY (fact_key) REFERENCES user_profile(fact_key) ON DELETE CASCADE
            )
        ''')

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

        conn.commit()
        return conn

    # ==========================================
    # AI 個性觀察 CRUD
    # ==========================================
    def insert_ai_observation(self, db_path, obs_id, category, raw_statement, extracted_trait, trait_vector, source_context=""):
        """新增一筆 AI 自我觀察"""
        conn = self._init_db(db_path)
        cursor = conn.cursor()
        timestamp = datetime.now().isoformat()
        vector_blob = np.array(trait_vector, dtype=np.float32).tobytes() if trait_vector is not None else None
        cursor.execute('''
            INSERT OR REPLACE INTO ai_personality_observations
            (obs_id, timestamp, category, raw_statement, extracted_trait, trait_vector, source_context, is_reflected, encounter_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, 1.0)
        ''', (obs_id, timestamp, category, raw_statement, extracted_trait, vector_blob, source_context))
        conn.commit()
        conn.close()

    def increment_observation_count(self, db_path, obs_id):
        """遞增既有觀察的 encounter_count"""
        conn = self._init_db(db_path)
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE ai_personality_observations
            SET encounter_count = encounter_count + 1.0, timestamp = ?
            WHERE obs_id = ?
        ''', (datetime.now().isoformat(), obs_id))
        conn.commit()
        conn.close()

    def load_pending_observations(self, db_path, limit=50):
        """載入所有未反思的觀察"""
        if not os.path.exists(db_path):
            return []
        conn = self._init_db(db_path)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT obs_id, timestamp, category, raw_statement, extracted_trait, trait_vector, source_context, encounter_count
            FROM ai_personality_observations
            WHERE is_reflected = 0
            ORDER BY timestamp DESC
            LIMIT ?
        ''', (limit,))
        rows = cursor.fetchall()
        conn.close()
        results = []
        for r in rows:
            vec = np.frombuffer(r[5], dtype=np.float32).tolist() if r[5] else []
            results.append({
                "obs_id": r[0], "timestamp": r[1], "category": r[2],
                "raw_statement": r[3], "extracted_trait": r[4],
                "trait_vector": vec, "source_context": r[6],
                "encounter_count": float(r[7]) if r[7] else 1.0
            })
        return results

    def load_all_observations(self, db_path, limit=100):
        """載入所有觀察（含已反思），供 UI 顯示"""
        if not os.path.exists(db_path):
            return []
        conn = self._init_db(db_path)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT obs_id, timestamp, category, raw_statement, extracted_trait, is_reflected, encounter_count
            FROM ai_personality_observations
            ORDER BY timestamp DESC
            LIMIT ?
        ''', (limit,))
        rows = cursor.fetchall()
        conn.close()
        return [{"obs_id": r[0], "timestamp": r[1], "category": r[2],
                 "raw_statement": r[3], "extracted_trait": r[4],
                 "is_reflected": bool(r[5]), "encounter_count": float(r[6]) if r[6] else 1.0} for r in rows]

    def mark_observations_reflected(self, db_path, obs_ids):
        """標記多筆觀察為已反思"""
        if not obs_ids:
            return
        conn = self._init_db(db_path)
        cursor = conn.cursor()
        placeholders = ",".join("?" * len(obs_ids))
        cursor.execute(f"UPDATE ai_personality_observations SET is_reflected = 1 WHERE obs_id IN ({placeholders})", obs_ids)
        conn.commit()
        conn.close()

    def count_pending_observations(self, db_path):
        """計算未反思觀察數"""
        if not os.path.exists(db_path):
            return 0
        conn = self._init_db(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM ai_personality_observations WHERE is_reflected = 0")
        count = cursor.fetchone()[0]
        conn.close()
        return count

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

    def upsert_profile_vector(self, db_path, fact_key, fact_vector):
        """新增或更新一筆使用者事實的向量"""
        conn = self._init_db(db_path)
        cursor = conn.cursor()
        vector_blob = np.array(fact_vector, dtype=np.float32).tobytes()
        cursor.execute('''
            INSERT OR REPLACE INTO user_profile_vectors (fact_key, fact_vector)
            VALUES (?, ?)
        ''', (fact_key, vector_blob))
        conn.commit()
        conn.close()

    def delete_profile(self, db_path, fact_key):
        """刪除一筆使用者事實（同時清除向量）"""
        conn = self._init_db(db_path)
        cursor = conn.cursor()
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
            LEFT JOIN user_profile_vectors v ON p.fact_key = v.fact_key
            WHERE p.confidence >= 0
        ''')
        rows = cursor.fetchall()
        conn.close()
        results = []
        for r in rows:
            vec = np.frombuffer(r[4], dtype=np.float32).tolist() if r[4] else []
            results.append({"fact_key": r[0], "fact_value": r[1], "category": r[2], "confidence": r[3], "fact_vector": vec})
        return results

    def get_profile_by_key(self, db_path, fact_key):
        """查詢單筆 profile（含墓碑記錄，供墓碑化寫入時查詢舊值用）"""
        if not os.path.exists(db_path):
            return None
        conn = self._init_db(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT fact_key, fact_value, category, confidence FROM user_profile WHERE fact_key = ?", (fact_key,))
        row = cursor.fetchone()
        conn.close()
        return {"fact_key": row[0], "fact_value": row[1], "category": row[2], "confidence": row[3]} if row else None

    # ==========================================
    # 對話紀錄持久化 (conversation.db)
    # ==========================================
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
                is_active INTEGER DEFAULT 1
            )
        ''')
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

    def load_conversation_messages(self, session_id):
        conn = self._init_conversation_db()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT role, content, debug_info, timestamp
            FROM conversation_messages WHERE session_id = ?
            ORDER BY msg_id ASC
        ''', (session_id,))
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