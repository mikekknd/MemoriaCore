# 記憶隔離遷移計畫：user_id + character_id

## 背景與目的

目前所有 AI 角色與使用者的記憶資料共用同一份 DB 表，沒有任何隔離機制。
主要風險：向量相似度合併（threshold 0.85）在全量資料上執行，不同用戶或角色的記憶區塊會互相汙染、合併。

本計畫以**行級隔離**（加欄位）方式解決，不拆 DB 檔案。原因：
- `StorageManager` 是 singleton，拆檔需大幅重構 DI 架構
- 現有 Schema Evolution 機制（`ALTER TABLE`）可平滑升級
- `user_profile` 需跨角色共享（同用戶不同角色看到同一份使用者畫像），行級隔離保有此彈性

**隔離維度定義：**
- `memory_blocks` / `core_memories` / `topic_cache` — 按 `(user_id, character_id)` 隔離
- `user_profile` / `user_profile_vectors` — 只按 `user_id` 隔離（跨角色共享）
- `conversation_sessions` — 加 `user_id` 欄位
- `persona_snapshots` / `persona_traits` — 已有 `character_id`，不需改動

---

## Phase 1：DB Schema + StorageManager

**檔案：`core/storage_manager.py`**

### 1.1 `_init_db()` 加 Schema Evolution

```sql
-- memory_blocks
ALTER TABLE memory_blocks ADD COLUMN user_id TEXT NOT NULL DEFAULT 'default';
ALTER TABLE memory_blocks ADD COLUMN character_id TEXT NOT NULL DEFAULT 'default';
CREATE INDEX IF NOT EXISTS idx_mb_user_char ON memory_blocks(user_id, character_id);

-- core_memories
ALTER TABLE core_memories ADD COLUMN user_id TEXT NOT NULL DEFAULT 'default';
ALTER TABLE core_memories ADD COLUMN character_id TEXT NOT NULL DEFAULT 'default';
CREATE INDEX IF NOT EXISTS idx_cm_user_char ON core_memories(user_id, character_id);

-- ai_personality_observations（目前無讀寫程式碼，但一併補齊）
ALTER TABLE ai_personality_observations ADD COLUMN user_id TEXT NOT NULL DEFAULT 'default';
ALTER TABLE ai_personality_observations ADD COLUMN character_id TEXT NOT NULL DEFAULT 'default';

-- topic_cache
ALTER TABLE topic_cache ADD COLUMN user_id TEXT NOT NULL DEFAULT 'default';
ALTER TABLE topic_cache ADD COLUMN character_id TEXT NOT NULL DEFAULT 'default';
CREATE INDEX IF NOT EXISTS idx_tc_user_char ON topic_cache(user_id, character_id);
```

`user_profile` 需重建 PRIMARY KEY（原 PK 是 `(fact_key, fact_value)`，加 `user_id` 後改為三欄複合 PK），
沿用現有的 rename + rebuild 模式，並以 `BEGIN IMMEDIATE` 加寫鎖防止重複執行：

```sql
ALTER TABLE user_profile RENAME TO _user_profile_old;
CREATE TABLE user_profile (
    user_id TEXT NOT NULL DEFAULT 'default',
    fact_key TEXT NOT NULL,
    fact_value TEXT NOT NULL,
    category TEXT,
    confidence REAL DEFAULT 1.0,
    timestamp TEXT,
    source_context TEXT,
    PRIMARY KEY (user_id, fact_key, fact_value)
);
INSERT INTO user_profile SELECT 'default', fact_key, fact_value, category, confidence, timestamp, source_context FROM _user_profile_old;
DROP TABLE _user_profile_old;

-- 同步重建 user_profile_vectors
ALTER TABLE user_profile_vectors RENAME TO _user_profile_vectors_old;
CREATE TABLE user_profile_vectors (
    user_id TEXT NOT NULL DEFAULT 'default',
    fact_key TEXT NOT NULL,
    fact_value TEXT NOT NULL,
    fact_vector BLOB,
    PRIMARY KEY (user_id, fact_key, fact_value),
    FOREIGN KEY (user_id, fact_key, fact_value)
        REFERENCES user_profile(user_id, fact_key, fact_value) ON DELETE CASCADE
);
INSERT INTO user_profile_vectors
SELECT 'default', fact_key, fact_value, fact_vector FROM _user_profile_vectors_old;
DROP TABLE _user_profile_vectors_old;
```

### 1.2 `_init_conversation_db()` 加 Schema Evolution

```sql
ALTER TABLE conversation_sessions ADD COLUMN user_id TEXT NOT NULL DEFAULT 'default';
CREATE INDEX IF NOT EXISTS idx_cs_user ON conversation_sessions(user_id);
```

### 1.3 StorageManager 方法簽名變更（所有預設值 `'default'` 確保向後相容）

| 方法 | 新增參數 |
|---|---|
| `load_db(db_path, user_id, character_id)` | 加 WHERE 過濾 |
| `save_db(db_path, memory_blocks, user_id, character_id)` | **最關鍵**：DELETE 改為 `WHERE user_id=? AND character_id=?` |
| `load_core_db(db_path, user_id, character_id)` | 加 WHERE 過濾 |
| `save_core_memory(..., user_id, character_id)` | INSERT 帶入兩欄 |
| `upsert_profile(db_path, user_id, fact_key, ...)` | 加 user_id |
| `upsert_profile_vector(db_path, user_id, ...)` | 加 user_id |
| `delete_profile(db_path, user_id, fact_key, ...)` | 加 user_id |
| `load_all_profiles(db_path, user_id, ...)` | 加 WHERE user_id=? |
| `load_profiles_by_category(db_path, user_id, category)` | 加 user_id |
| `load_profile_vectors(db_path, user_id)` | 加 user_id |
| `get_profile_by_key(db_path, user_id, fact_key, ...)` | 加 user_id |
| `insert_topic_cache(db_path, user_id, character_id, ...)` | 加兩欄 |
| `get_unmentioned_topics(db_path, user_id, character_id, ...)` | 加兩欄 |
| `create_conversation_session(session_id, channel, channel_uid, user_id)` | 加 user_id |

> ⚠️ **最危險的修改**：`save_db()` 目前是 `DELETE FROM memory_blocks`（全表刪除）。
> 改後必須是 `DELETE FROM memory_blocks WHERE user_id=? AND character_id=?`，否則會清掉其他用戶資料。

---

## Phase 2：CoreMemory（MemorySystem）

**檔案：`core/core_memory.py`**

### 2.1 廢除 flat instance cache，改為 per-key lazy cache

```python
# 移除
self.memory_blocks: list = []
self.core_memories: list = []
self.user_profiles: list = []

# 改為
self._memory_blocks_cache: dict[tuple[str, str], list] = {}
self._core_memories_cache: dict[tuple[str, str], list] = {}
self._user_profiles_cache: dict[str, list] = {}
```

新增三個 lazy loader（cache miss 才觸發 DB 讀取）：

```python
def _get_memory_blocks(self, user_id: str, character_id: str) -> list:
    key = (user_id, character_id)
    if key not in self._memory_blocks_cache:
        self._memory_blocks_cache[key] = self.storage.load_db(self.db_path, user_id, character_id)
    return self._memory_blocks_cache[key]
```

### 2.2 `switch_embedding_model()` 改為 lazy 清空

```python
def switch_embedding_model(self, provider, model_name):
    self.embed_provider = provider
    self.embed_model = model_name
    self.db_path = self.storage.get_db_path(model_name)
    self._memory_blocks_cache.clear()   # lazy，不預載
    self._core_memories_cache.clear()
    self._user_profiles_cache.clear()
```

### 2.3 所有對外方法加 `user_id` + `character_id` 參數（有預設值）

```python
def add_memory_block(self, overview, raw_dialogues,
                     user_id: str = "default", character_id: str = "default", ...)
def search_blocks(self, original_query, combined_keywords,
                  user_id: str = "default", character_id: str = "default", ...)
def search_core_memories(self, query,
                          user_id: str = "default", character_id: str = "default", ...)
def find_pending_clusters(self, user_id: str = "default", character_id: str = "default", ...)
def consolidate_and_fuse(self, related_blocks,
                          user_id: str = "default", character_id: str = "default", ...)
def apply_profile_facts(self, facts, embed_model, user_id: str = "default")
def search_profile_by_query(self, query, user_id: str = "default", ...)
def get_static_profile_prompt(self, user_id: str = "default")
def get_proactive_topics_prompt(self, user_id: str = "default", character_id: str = "default", ...)
def load_user_profile(self, user_id: str = "default")
```

---

## Phase 3：Pydantic Models + SessionState

**`api/models/requests.py`**
```python
class ChatSyncRequest(BaseModel):
    content: str
    session_id: Optional[str] = None
    user_id: str = "default"   # 新增
```

**`api/session_manager.py`**
```python
@dataclass
class SessionState:
    session_id: str
    ...
    user_id: str = "default"        # 新增
    character_id: str = "default"   # 新增（session 建立時從 active_character_id 固化）

async def create(self, channel="rest", channel_uid="", user_id="default", character_id="default"):
    ...
```

`restore_from_db()` 也需從 DB 還原 `user_id`。

---

## Phase 4：API 路由層

### user_id 來源規則

| channel | user_id 來源 |
|---|---|
| Telegram | `str(message.from_user.id)` |
| REST / SSE | `body.user_id`（預設 `'default'`） |
| WebSocket | frame 中的 `user_id`（預設 `'default'`） |

### 傳遞鏈（需修改的檔案）

**`api/routers/chat_rest.py`**
- `_resolve_session()` 帶入 `user_id`
- `chat_sync()` / `chat_stream_sync()` 從 request 或 session 取 `user_id`, `character_id`
- `_run_memory_pipeline_bg()` 呼叫帶入兩個參數

**`api/routers/chat_ws.py`**
- 從 frame 或 session 取 `user_id`
- `clear_context` 重建 session 時保留舊 session 的 `user_id`

**`api/routers/chat/orchestration.py`**
```python
def _run_chat_orchestration(session_messages, last_entities, user_prompt, user_prefs,
                             on_event=None,
                             user_id: str = "default",
                             character_id: str = "default"):
    # 所有 ms.* 呼叫帶入 user_id, character_id
```

**`api/routers/chat/pipeline.py`**
```python
def _run_memory_pipeline_sync(msgs_to_extract, last_block,
                               user_id: str = "default",
                               character_id: str = "default"):
    ms.add_memory_block(..., user_id, character_id)
    ms.apply_profile_facts(..., user_id)
    pref_agg = PreferenceAggregator(ms, user_id, character_id)
```

`pipeline_data` tuple 從 2-tuple 擴充為 4-tuple：
`pipeline_data = (msgs_to_extract, last_block, user_id, character_id)`

**`api/telegram_bot.py`**
```python
session = await session_manager.create(
    channel="telegram",
    channel_uid=str(user_id),
    user_id=str(user_id),
)
```

---

## Phase 5：相關元件

**`core/preference_aggregator.py`**
```python
class PreferenceAggregator:
    def __init__(self, memory_sys, user_id: str = "default", character_id: str = "default"):
        ...
    def aggregate(self, ...):
        all_blocks = self.memory_sys._get_memory_blocks(self.user_id, self.character_id)
```

**`core/background_gatherer.py`**
```python
def run_background_topic_gather(db_path, router, storage,
                                 user_id="default", character_id="default"):
```

**`api/routers/memory.py`**
- `delete_core` 端點直接用 `sqlite3` 繞過 StorageManager，需改為透過 StorageManager 的新方法，並帶入 `user_id`/`character_id`

---

## 陷阱清單

| 項目 | 說明 |
|---|---|
| `save_db()` 全表刪除 | **最高風險**：必須改為 scoped DELETE，否則清掉所有用戶資料 |
| `user_profile` PK migration | 需 `BEGIN IMMEDIATE` 加寫鎖，並以欄位存在性 check 避免重複執行 |
| `pipeline_data` tuple 擴充 | 從 2-tuple 改為 4-tuple，所有 unpack 點都要同步修改 |
| WebSocket `clear_context` | 重建 session 時需保留原 `user_id` |
| `memory.py` 的 `sqlite3` 直連 | 需改為透過 StorageManager |
| `api_messages.extend(clean_history)` | 修改 `orchestration.py` 時確認此行仍在 sys_prompt 賦值之後 |

---

## 驗證方式

1. **單元測試**：現有 `tests/` 的所有測試呼叫補上 `user_id="default", character_id="default"`，確保向後相容
2. **隔離測試**：建立兩組 `(user_id, character_id)` 各寫入不同記憶，確認 `search_blocks()` 不會跨空間返回結果
3. **汙染測試**：兩個用戶分別有相似內容的記憶區塊（cosine > 0.85），確認 `add_memory_block()` 不會合併跨用戶的區塊
4. **向後相容**：未帶 `user_id` 的現有呼叫（預設 `'default'`）能正確讀到遷移後的舊資料

---

## 改動優先順序

```
Phase 1 → Phase 2 → Phase 3 → Phase 4 → Phase 5
storage_manager  core_memory  models/session  routers  其他元件
```

每個 Phase 完成後可獨立測試，不需要一次全部完成。
