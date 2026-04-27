# 記憶隔離遷移計畫（已完成）：三維度正交隔離（user_id × visibility × persona_face）

## 0. 背景與目標

### 0.1 現況問題

目前所有 AI 角色與使用者的記憶資料共用同一份 DB，沒有任何隔離機制。主要風險：

- **記憶污染**：向量相似度合併（threshold 0.85）在全量資料上執行，不同用戶或角色的記憶區塊會互相汙染、合併
- **人格污染**：`core/persona_sync.py` 的 `_run_probe_sync()` 透過 `load_fragments_from_db()` 讀取**所有 session 的對話**餵給 LLM 反思，產出 trait 直接寫入共用 `persona_traits` — 多用戶共用同一 character 時會發生平均化、兩極化、誤休眠
- **隱私洩漏**：SU 的私下事實（如健康狀況、私人偏好）會在 public 場合被 AI 引用

### 0.2 部署場景

複數用戶來源：
- **Telegram bot**：1 對 1、長期關係，每個用戶是穩定個體
- **直播互動**：海量、短暫的觀眾留言，期待 AI 維持一致「主播人設」
- **Discord 支援**：依用途，介於兩者之間

### 0.3 設計目標

達成以下三件事，且彼此正交（互不糾纏）：

1. **資料歸屬隔離**：誰的記憶就是誰的，不會洩漏到別人那
2. **公私可見性不對稱**：private face 可讀 public 記憶（SU 能跟 AI 聊「直播上的事」），public face 讀不到 private 記憶（觀眾問不出 SU 私事）
3. **人格雙面獨立演化**：private 人格（對 SU）與 public 人格（對其他人）各自演化，互不污染；且支援雙 face 並行運作

---

## 1. 三維度隔離設計

### 1.1 三個正交維度

| 維度 | 用途 | 取值 | 套用範圍 |
|---|---|---|---|
| `user_id` | 資料歸屬 — 誰的記憶 | 真實用戶 ID（Telegram user id、`'default'` 等） | 記憶系統所有表 |
| `visibility` | 可見性 — 誰看得到 | `'private'` \| `'public'` | 記憶 / 事實 / 對話 |
| `persona_face` | 人格面向 — 演哪一面 | `'private'` \| `'public'` | 人格演化系統 |

**正交性原則**：三個維度各自獨立決策。例如：SU 在直播台前發言 → `user_id=SU_ID`、`visibility='public'`、`persona_face='public'` — 同一筆訊息，三個維度給出三個獨立的答案。

### 1.2 Context 解析規則

定義一個 helper 放在 `core/deployment_config.py`：

```python
PUBLIC_CHANNELS = {'livestream', 'discord_public'}
SU_ID = os.getenv('SU_USER_ID', '')

def resolve_context(user_id: str, channel: str) -> tuple[str, str]:
    """
    Returns (persona_face, write_visibility).
    """
    if channel in PUBLIC_CHANNELS:
        return ('public', 'public')        # 直播即使 SU 自己留言也是 public
    if user_id == SU_ID:
        return ('private', 'private')      # SU 私訊
    return ('public', 'public')            # 其他人私訊（共用 public face）
```

### 1.3 三類用戶處理矩陣

| 用戶類型 | channel 範例 | persona_face | write_visibility | profile 抽取 |
|---|---|---|---|---|
| **SU 私訊** | telegram (user_id=SU) / rest | `private` | `private` | ✓（visibility=private）|
| **SU 公開發言** | livestream (user_id=SU) | `public` | `public` | ✓（visibility=public）|
| **Telegram 非 SU** | telegram (user_id≠SU) | `public` | `public` | ✓（visibility=public）|
| **直播觀眾** | livestream (user_id≠SU) | `public` | `public` | ✗（跳過）|
| **DC 公開** | discord_public | `public` | `public` | ✗ 或 ✓（依 config）|

### 1.4 不對稱記憶共享

**讀取規則**（核心設計）：

```
persona_face='private'  →  WHERE visibility IN ('private', 'public')
persona_face='public'   →  WHERE visibility = 'public'
```

具體效果：
- SU 私訊問「今天直播聊了什麼」→ private face 能讀到 public 的直播記憶 ✓
- 觀眾在直播問「你和 SU 私下都聊什麼」→ public face 讀不到 private 記憶 ✓

### 1.5 雙 Face 並行運作

設計天然支援。原因：

1. **每個請求獨立 resolve 自己的 context** — Telegram 與直播訊息分別走自己的 channel/user_id 解析
2. **System prompt 是 per-request 重新組合** — 不存在「全域當前人格狀態」，每次回應都從 DB 撈當下需要的 face 的 evolved_prompt + 對應 visibility 的記憶
3. **StorageManager 的 async lock 序列化寫入** — 兩條對話同時寫 memory_block 會被排隊，但 `(user_id, visibility)` 不同所以無 PK 衝突
4. **Persona sync 是背景閒置觸發** — 不會插入活躍對話中

副作用（**by design**，不是 bug）：
- private 對話無法即時影響 public face 回應 — 例如 SU 私訊「等下有人問就回中立」，直播觀眾真的問時 AI 不知道。要破壞這條保證才能做到，**不予實作**。

---

## 2. Schema 變更

### 2.1 通則

所有 schema migration 必須：
1. 用 `BEGIN IMMEDIATE` 取得寫鎖避免併發遷移
2. 用 `PRAGMA table_info()` 檢查欄位/PK 存在性，確保冪等
3. 新增欄位以 `DEFAULT` 確保現有資料平滑升級
4. PK 重建類型的遷移用 rename + rebuild 模式

### 2.2 記憶相關表

```sql
-- memory_blocks
ALTER TABLE memory_blocks ADD COLUMN user_id TEXT NOT NULL DEFAULT 'default';
ALTER TABLE memory_blocks ADD COLUMN character_id TEXT NOT NULL DEFAULT 'default';
ALTER TABLE memory_blocks ADD COLUMN visibility TEXT NOT NULL DEFAULT 'public';
CREATE INDEX IF NOT EXISTS idx_mb_scope ON memory_blocks(user_id, character_id, visibility);

-- core_memories
ALTER TABLE core_memories ADD COLUMN user_id TEXT NOT NULL DEFAULT 'default';
ALTER TABLE core_memories ADD COLUMN character_id TEXT NOT NULL DEFAULT 'default';
ALTER TABLE core_memories ADD COLUMN visibility TEXT NOT NULL DEFAULT 'public';
CREATE INDEX IF NOT EXISTS idx_cm_scope ON core_memories(user_id, character_id, visibility);

-- ai_personality_observations
ALTER TABLE ai_personality_observations ADD COLUMN user_id TEXT NOT NULL DEFAULT 'default';
ALTER TABLE ai_personality_observations ADD COLUMN character_id TEXT NOT NULL DEFAULT 'default';
ALTER TABLE ai_personality_observations ADD COLUMN visibility TEXT NOT NULL DEFAULT 'public';

-- topic_cache
ALTER TABLE topic_cache ADD COLUMN user_id TEXT NOT NULL DEFAULT 'default';
ALTER TABLE topic_cache ADD COLUMN character_id TEXT NOT NULL DEFAULT 'default';
ALTER TABLE topic_cache ADD COLUMN visibility TEXT NOT NULL DEFAULT 'public';
CREATE INDEX IF NOT EXISTS idx_tc_scope ON topic_cache(user_id, character_id, visibility);
```

### 2.3 user_profile / user_profile_vectors（PK 重建）

原 PK `(fact_key, fact_value)` 改為 `(user_id, fact_key, fact_value)`，並加 `visibility`：

```sql
BEGIN IMMEDIATE;

-- user_profile
ALTER TABLE user_profile RENAME TO _user_profile_old;
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
);
INSERT INTO user_profile
SELECT 'default', fact_key, fact_value, category, confidence, timestamp, source_context, 'public'
FROM _user_profile_old;
DROP TABLE _user_profile_old;

-- user_profile_vectors
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

COMMIT;
```

### 2.4 conversation_sessions

```sql
ALTER TABLE conversation_sessions ADD COLUMN user_id TEXT NOT NULL DEFAULT 'default';
ALTER TABLE conversation_sessions ADD COLUMN channel_class TEXT NOT NULL DEFAULT 'public';
CREATE INDEX IF NOT EXISTS idx_cs_user ON conversation_sessions(user_id);
```

`channel_class` 紀錄當時是 `'private'` 或 `'public'`，方便事後 reproduce 與 PersonaSync 過濾片段。

### 2.5 人格相關表（**修正原計畫盲點**）

原計畫聲稱「persona_snapshots / persona_traits 已有 character_id 不需改動」是錯的。實際上需要加 `persona_face` 才能避免雙 face 共用同一筆 trait。

```sql
BEGIN IMMEDIATE;

-- persona_traits（PRIMARY KEY 不變，但 UNIQUE 改）
ALTER TABLE persona_traits ADD COLUMN persona_face TEXT NOT NULL DEFAULT 'public';
-- 原 UNIQUE(character_id, trait_key) → UNIQUE(character_id, persona_face, trait_key)
-- 透過 rename + rebuild 重建，並把現有資料標 'public'
ALTER TABLE persona_traits RENAME TO _persona_traits_old;
CREATE TABLE persona_traits (
    trait_key TEXT PRIMARY KEY,
    character_id TEXT NOT NULL,
    persona_face TEXT NOT NULL DEFAULT 'public',
    name TEXT NOT NULL,
    created_version INTEGER NOT NULL,
    last_active_version INTEGER NOT NULL,
    parent_key TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    UNIQUE(character_id, persona_face, trait_key),
    FOREIGN KEY (parent_key) REFERENCES persona_traits(trait_key) ON DELETE SET NULL
);
INSERT INTO persona_traits
SELECT trait_key, character_id, 'public', name, created_version,
       last_active_version, parent_key, is_active, created_at
FROM _persona_traits_old;
DROP TABLE _persona_traits_old;

-- persona_snapshots
ALTER TABLE persona_snapshots ADD COLUMN persona_face TEXT NOT NULL DEFAULT 'public';
-- 原 UNIQUE(character_id, version) → UNIQUE(character_id, persona_face, version)
-- 同樣 rename + rebuild
ALTER TABLE persona_snapshots RENAME TO _persona_snapshots_old;
CREATE TABLE persona_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    character_id TEXT NOT NULL,
    persona_face TEXT NOT NULL DEFAULT 'public',
    version INTEGER NOT NULL,
    timestamp TEXT NOT NULL,
    summary TEXT,
    evolved_prompt TEXT,
    UNIQUE(character_id, persona_face, version)
);
INSERT INTO persona_snapshots
SELECT id, character_id, 'public', version, timestamp, summary, evolved_prompt
FROM _persona_snapshots_old;
DROP TABLE _persona_snapshots_old;

COMMIT;
```

`persona_dimensions` 透過 `snapshot_id` 外鍵指向 `persona_snapshots`，間接繼承 `persona_face`，**無需新欄位**。

---

## 3. StorageManager 改動

**檔案：`core/storage_manager.py`**

### 3.1 高風險點（最優先處理）

| 方法 | 現況 | 改後 |
|---|---|---|
| `save_db()` | `DELETE FROM memory_blocks`（全表刪除）| `DELETE FROM memory_blocks WHERE user_id=? AND character_id=? AND visibility=?` |
| `delete_profile()` | `DELETE FROM user_profile WHERE fact_key=?` | `DELETE FROM user_profile WHERE user_id=? AND fact_key=? [AND fact_value=?]` |
| `memory.py:delete_core` | 直接 `sqlite3.connect(ms.db_path)` 跳過 StorageManager | 新增 `StorageManager.delete_core_memory(...)` 方法走 async lock |

> ⚠️ **`save_db()` 是最危險的修改**。若 scoped DELETE 寫錯，會清掉其他用戶/角色/可見性的資料。Phase 1 完成後**必須**寫單元測試驗證隔離。

### 3.2 方法簽名變更總表

預設值都用 `'default'` / `'public'` 確保向後相容。

**記憶與核心**：
| 方法 | 新增參數 |
|---|---|
| `load_db(db_path, user_id, character_id, visibility_filter)` | `visibility_filter: list[str]` |
| `save_db(db_path, memory_blocks, user_id, character_id, visibility)` | scoped DELETE |
| `load_core_db(db_path, user_id, character_id, visibility_filter)` | 同上 |
| `save_core_memory(..., user_id, character_id, visibility)` | INSERT 帶入三欄 |
| `delete_core_memory(db_path, user_id, character_id, core_id)` | **新增方法** |

**Profile**：
| 方法 | 新增參數 |
|---|---|
| `upsert_profile(db_path, user_id, fact_key, fact_value, category, ..., visibility)` | 加 user_id + visibility |
| `upsert_profile_vector(db_path, user_id, ...)` | 加 user_id |
| `delete_profile(db_path, user_id, fact_key, fact_value=None)` | 加 user_id（**高風險點**）|
| `load_all_profiles(db_path, user_id, visibility_filter, include_tombstones)` | 加 user_id + visibility filter |
| `load_profiles_by_category(db_path, user_id, category, visibility_filter)` | 同上 |
| `load_profile_vectors(db_path, user_id)` | 加 user_id |
| `get_profile_by_key(db_path, user_id, fact_key, fact_value)` | 加 user_id |

**話題與對話**：
| 方法 | 新增參數 |
|---|---|
| `insert_topic_cache(db_path, user_id, character_id, ..., visibility)` | 加三欄 |
| `get_unmentioned_topics(db_path, user_id, character_id, visibility_filter, ...)` | 加三欄 |
| `mark_topic_mentioned(...)` | 必要時加 user_id, character_id |
| `create_conversation_session(session_id, channel, channel_uid, user_id, channel_class)` | 加兩欄 |

**人格演化**：
| 方法 | 新增參數 |
|---|---|
| `get_next_persona_version(character_id, persona_face)` | 加 face |
| `get_latest_persona_snapshot(character_id, persona_face)` | 加 face |
| `get_persona_snapshot(character_id, persona_face, version)` | 加 face |
| `list_persona_snapshots(character_id, persona_face)` | 加 face |
| `delete_persona_snapshots_by_character(character_id, persona_face=None)` | face 可選（None=全刪）|
| `save_trait_snapshot(..., persona_face)` | 加 face |
| `get_active_traits(character_id, persona_face)` | 加 face |
| `get_all_traits(character_id, persona_face)` | 加 face |
| `get_trait_timeline(character_id, persona_face, trait_key)` | 加 face |

### 3.3 _init_db / _init_conversation_db / _init_persona_snapshot_db

統一以下模式：
1. `BEGIN IMMEDIATE` 加寫鎖
2. `PRAGMA table_info()` 檢查欄位 — 不存在才 ALTER
3. 對 PK 重建類型，檢查 `sqlite_master` 看 schema 是否已是新版
4. `COMMIT`（失敗時 ROLLBACK）

---

## 4. CoreMemory（MemorySystem）

**檔案：`core/core_memory.py`**

### 4.1 廢除 flat instance cache，改為 keyed cache

```python
# 移除
self.memory_blocks: list = []
self.core_memories: list = []
self.user_profiles: list = []

# 改為（key 包含三維度）
self._memory_blocks_cache: dict[tuple[str, str], list] = {}     # (user_id, character_id)
self._core_memories_cache: dict[tuple[str, str], list] = {}      # (user_id, character_id)
self._user_profiles_cache: dict[str, list] = {}                  # user_id
```

> visibility 不進 cache key — cache 載入「全部 visibility」的資料，由查詢時的 filter 過濾。理由：避免 private/public 重複載入相同 user_id 下的記憶，記憶體效率較好。

Lazy loaders：

```python
def _get_memory_blocks(self, user_id: str, character_id: str) -> list:
    key = (user_id, character_id)
    if key not in self._memory_blocks_cache:
        self._memory_blocks_cache[key] = self.storage.load_db(
            self.db_path, user_id, character_id, visibility_filter=None
        )
    return self._memory_blocks_cache[key]
```

### 4.2 `switch_embedding_model()` 改為 lazy 清空

```python
def switch_embedding_model(self, provider, model_name):
    self.embed_provider = provider
    self.embed_model = model_name
    self.db_path = self.storage.get_db_path(model_name)
    self._memory_blocks_cache.clear()
    self._core_memories_cache.clear()
    self._user_profiles_cache.clear()
```

### 4.3 對外方法簽名（含 visibility filter）

```python
def add_memory_block(self, overview, raw_dialogues,
                     user_id: str = "default",
                     character_id: str = "default",
                     visibility: str = "public",
                     duplicate_threshold=0.85, ...): ...

def search_blocks(self, original_query, combined_keywords,
                  user_id: str = "default",
                  character_id: str = "default",
                  visibility_filter: list[str] = ("public",),
                  top_k=2, ...): ...

def search_core_memories(self, query,
                         user_id: str = "default",
                         character_id: str = "default",
                         visibility_filter: list[str] = ("public",),
                         top_k=1, ...): ...

def find_pending_clusters(self, user_id: str = "default",
                          character_id: str = "default",
                          visibility: str = "public",
                          cluster_threshold=0.75, ...): ...
# 注意：合併操作必須限定單一 visibility，不可跨 private/public 合併

def consolidate_and_fuse(self, related_blocks,
                         user_id: str = "default",
                         character_id: str = "default",
                         visibility: str = "public",
                         router=None, ...): ...

def apply_profile_facts(self, facts, embed_model,
                        user_id: str = "default",
                        visibility: str = "public"): ...

def search_profile_by_query(self, query,
                            user_id: str = "default",
                            visibility_filter: list[str] = ("public",),
                            top_k=3, ...): ...

def get_static_profile_prompt(self, user_id: str = "default",
                              visibility_filter: list[str] = ("public",)): ...

def get_proactive_topics_prompt(self, user_id: str = "default",
                                character_id: str = "default",
                                visibility_filter: list[str] = ("public",), ...): ...

def load_user_profile(self, user_id: str = "default",
                      visibility_filter: list[str] = None): ...
# visibility_filter=None 表示全收（給 SU private face 用）
```

### 4.4 跨用戶/可見性合併防護

`find_pending_clusters()` / `consolidate_and_fuse()` 必須**只在同一 (user_id, character_id, visibility) 內部運作**。這是修正原 0.85 cosine threshold 跨用戶污染漏洞的關鍵。

---

## 5. PersonaSync 雙 Face 改造（**全新章節**）

### 5.1 涉及檔案

- `core/persona_sync.py` — `PersonaSyncManager` 與 `_run_probe_sync`
- `core/persona_evolution/snapshot_store.py` — `PersonaSnapshotStore.save_snapshot`
- `PersonaProbe/probe_engine.py` — `load_fragments_from_db`
- `core/persona_evolution/extractor.py` — prompt 建構（active_traits 注入）

### 5.2 `load_fragments_from_db` 加 face filter

```python
def load_fragments_from_db(
    db_path: str,
    session_id: Optional[str] = None,
    visibility: Optional[str] = None,        # 新增：'private' | 'public' | None(全收)
    user_ids: Optional[list[str]] = None,    # 新增：限定 user 範圍
    limit: Optional[int] = None,
) -> str:
    # SQL 對應加 WHERE conversation_sessions.channel_class = ?
    # （透過 JOIN session 取得 channel_class）
    # 或直接讓 conversation_messages 也帶 visibility 欄位（建議走 JOIN，避免再改一張表）
```

### 5.3 `_run_probe_sync` 簽名變更

```python
def _run_probe_sync(
    character_id: str,
    persona_face: str,         # 'private' | 'public'
    fragments_text: str,
    active_traits: list[dict],
    ...
) -> SnapshotResult:
    # build_trait_v1_prompt / build_trait_vn_prompt 注入該 face 的 active_traits
    # parse 結果 → store.save_snapshot(character_id, persona_face, ...)
```

### 5.4 `PersonaSyncManager.run_sync` 改為逐 face 觸發

```python
async def run_sync(self):
    if not self._should_run():
        return

    active_char_id = self.prefs.get("active_character_id")
    for face in ('private', 'public'):
        if not self._has_enough_new_messages(active_char_id, face):
            continue
        fragments = load_fragments_from_db(
            self.conv_db_path,
            visibility=face,      # private face 只看 private 對話；public face 只看 public
            limit=400,
        )
        active_traits = self.store.list_active_traits(
            character_id=active_char_id, persona_face=face
        )
        result = _run_probe_sync(
            character_id=active_char_id,
            persona_face=face,
            fragments_text=fragments,
            active_traits=active_traits,
        )
        self.store.save_snapshot(
            character_id=active_char_id,
            persona_face=face,
            trait_diff=result.trait_diff,
            summary=result.summary,
            evolved_prompt=result.evolved_prompt,
        )
```

### 5.5 觸發條件按 face 計算

`persona_sync_min_messages`（預設 50）改為**每個 face 各自計算**：
- private face：`COUNT(*) FROM conversation_messages cm JOIN conversation_sessions cs ON cm.session_id=cs.session_id WHERE cs.channel_class='private' AND cm.timestamp > last_private_sync_time`
- public face：同上但 `channel_class='public'`

### 5.6 PersonaSnapshotStore 改動

```python
def save_snapshot(
    self,
    character_id: str,
    persona_face: str,           # 新增
    trait_diff: TraitDiff,
    summary: str,
    evolved_prompt: str,
    timestamp=None,
): ...

def list_active_traits(
    self,
    character_id: str,
    persona_face: str,           # 新增
) -> list[Trait]: ...
```

`save_trait_snapshot()` 內的原子交易、B' sweep、parent_key fallback 邏輯**全部限縮在 `(character_id, persona_face)` 範圍內**。

---

## 6. Profile 三類用戶策略

### 6.1 抽取策略

| 用戶類型 | 是否抽取 | visibility 寫入 | 說明 |
|---|---|---|---|
| **直播觀眾** | ❌ 跳過 | — | 互動太短雜訊太多，profile 不準也用不到。透過 `extract_profile=False` config 控制 |
| **Telegram 非 SU** | ✅ 抽取 | `'public'` | 長期關係，profile 對「AI 認得這位朋友」有用 |
| **SU 私訊** | ✅ 抽取 | `'private'` | 私下事實 |
| **SU 公開發言**（直播）| ✅ 抽取 | `'public'` | 公開事實（如「我的頻道叫 X」）|

### 6.2 抽取邏輯接點

```python
# core/deployment_config.py
EXTRACT_PROFILE_FROM_CHANNELS = {'telegram', 'rest', 'discord_private'}

def should_extract_profile(channel: str) -> bool:
    return channel in EXTRACT_PROFILE_FROM_CHANNELS
```

`pipeline.py._run_memory_pipeline_sync` 在呼叫 `apply_profile_facts` 前先檢查：

```python
if should_extract_profile(channel):
    ms.apply_profile_facts(facts, embed_model, user_id=user_id, visibility=write_visibility)
```

### 6.3 載入時 filter

`load_user_profile` / `get_static_profile_prompt` 接收 `visibility_filter`：

```python
# 由 orchestration.py 呼叫
if persona_face == 'private':
    profile = ms.load_user_profile(user_id, visibility_filter=None)  # 全收
else:  # public face
    profile = ms.load_user_profile(user_id, visibility_filter=['public'])
```

> **這條規則保護 SU 隱私**：SU 在直播留言時即使被 AI 用 public face 回應，也只會載入 visibility='public' 的 fact，不會洩漏 private 事實。

---

## 7. API 與 Session 層

### 7.1 SessionState 擴充

**`api/session_manager.py`**

```python
@dataclass
class SessionState:
    session_id: str
    messages: list[dict]
    last_entities: list[str]
    created_at: datetime
    last_active: datetime
    channel: str = "rest"
    channel_uid: str = ""
    user_id: str = "default"           # 新增
    character_id: str = "default"      # 新增
    persona_face: str = "public"       # 新增
    channel_class: str = "public"      # 新增

async def create(self, channel="rest", channel_uid="",
                 user_id="default", character_id="default",
                 persona_face="public", channel_class="public") -> SessionState: ...
```

`restore_from_db()` 從 `conversation_sessions` 還原 `user_id`、`channel_class`，並透過 `resolve_context()` 重新計算 `persona_face`。

### 7.2 Pydantic Models

**`api/models/requests.py`**

```python
class ChatSyncRequest(BaseModel):
    content: str
    session_id: Optional[str] = None
    user_id: str = "default"           # 新增
    channel_class: Optional[str] = None  # 新增；None 由後端依 channel 推斷
```

### 7.3 user_id 來源規則

| Channel | user_id 來源 | channel_class |
|---|---|---|
| Telegram | `str(message.from_user.id)` | `'private'` |
| REST `/chat/sync` | `body.user_id`（預設 `'default'`）| `'private'` 或 `body.channel_class` |
| WebSocket | frame 中的 `user_id` | 同上 |
| Livestream（未來）| 觀眾平台 user id | `'public'` |
| Discord（未來）| Discord user id | 依 server config |

### 7.4 Pipeline Data 擴充

`pipeline_data` 從 2-tuple 升級為 dataclass：

```python
@dataclass
class PipelineContext:
    msgs_to_extract: list
    last_block: Optional[dict]
    user_id: str
    character_id: str
    persona_face: str
    write_visibility: str
    channel: str
```

涉及檔案：
- `api/routers/chat/pipeline.py` — `_run_memory_pipeline_sync(ctx: PipelineContext)`
- `api/routers/chat/pipeline.py` — `_run_memory_pipeline_bg(session_id, ctx)`
- `api/routers/chat_rest.py` — 兩個端點呼叫 `_run_memory_pipeline_bg(session_id, ctx)`
- `api/routers/chat_ws.py` — 同上

### 7.5 Orchestration 改造

**`api/routers/chat/orchestration.py`**

```python
def _run_chat_orchestration(
    session_messages, last_entities, user_prompt, user_prefs,
    on_event=None,
    user_id: str = "default",
    character_id: str = "default",
    persona_face: str = "public",
    visibility_filter: tuple[str, ...] = ("public",),
):
    # 所有 ms.search_blocks / ms.search_core_memories / ms.search_profile_by_query
    # 帶入 user_id, character_id, visibility_filter
    # persona prompt 載入時依 persona_face 取對應 face 的 evolved_prompt
```

**`core/chat_orchestrator/coordinator.py`** 雙層編排同步改造，`_memory_branch` 與 `_tool_branch` 都需收 face 參數。

⚠️ **務必確認 `api_messages.extend(clean_history)` 在 sys_prompt 賦值之後仍然存在**（CLAUDE.md 高頻踩坑點）。

### 7.6 WebSocket / Telegram

**`api/routers/chat_ws.py`**：
- frame 解析時取 `user_id` / `channel_class`
- `clear_context` 重建 session 時保留原 `user_id`、`character_id`、`channel_class`、重新計算 `persona_face`

**`api/telegram_bot.py`**：

```python
session = await session_manager.create(
    channel="telegram",
    channel_uid=str(user_id),
    user_id=str(user_id),
    character_id=active_character_id,
    channel_class="private",
    persona_face=resolve_context(str(user_id), "telegram")[0],
)
```

### 7.7 memory.py 路由

**`api/routers/memory.py`**：
- `delete_core` 改為呼叫 `storage.delete_core_memory(...)`，不再直接 `sqlite3.connect`
- 端點接收 `user_id` / `character_id` query params

---

## 8. 周邊元件

### 8.1 PreferenceAggregator

**`core/preference_aggregator.py`**

```python
class PreferenceAggregator:
    def __init__(self, memory_sys):
        self.memory_sys = memory_sys

    def aggregate(self,
                  user_id: str = "default",
                  character_id: str = "default",
                  visibility_filter: tuple[str, ...] = ("public",),
                  decay_lambda=0.02,
                  similarity_threshold=0.85,
                  score_threshold=3.0):
        all_blocks = self.memory_sys._get_memory_blocks(user_id, character_id)
        all_blocks = [b for b in all_blocks if b.get("visibility", "public") in visibility_filter]
        # ... 餘下邏輯
```

> 設計差異：原計畫把 user/char 放進 `__init__`，正式版改放 `aggregate()`，避免單一 instance 被多 user 重用時殘留狀態。

### 8.2 background_gatherer

**`core/background_gatherer.py`**

```python
def run_background_topic_gather(
    db_path, router, storage,
    user_id: str = "default",
    character_id: str = "default",
    visibility: str = "public",
):
    # 從 user_profile 隨機選興趣時帶 user_id + visibility filter
    # 寫入 topic_cache 時帶三欄
```

### 8.3 scripts / 測試輔助

- `scripts/seed_persona_traits_prototype.py` — 呼叫 `save_trait_snapshot(...)` 補上 `persona_face='public'`
- 其他啟動腳本若直接呼叫 StorageManager 方法的，全部補預設值

### 8.4 UI 層

**短期**（本計畫範圍內）：
- `ui/chat.py`（Streamlit）— 暫時硬填 `user_id='default'`、不顯示 user 切換 UI
- `static/dashboard.html` — 同樣硬填 `'default'`
- 兩端的 SSE / WS frame 加 `user_id` 欄位（值為 `'default'`），確保協議 forward-compatible

**未來**（不在本計畫）：
- Streamlit 加 user 選擇 dropdown / 登入機制
- dashboard.html 加 user 切換
- 直播平台串接：webhook 端點接收觀眾 user_id

---

## 9. 資料遷移策略（**全新章節**）

### 9.1 既有資料處理

所有現有資料於 schema migration 時統一標：
- `user_id = 'default'`
- `visibility = 'public'`
- `persona_face = 'public'`
- `channel_class = 'public'`

理由：保守選擇 — 既有資料無從判斷歸屬，全部視為共用 public 資料，避免誤判私密。

### 9.2 Telegram 既有資料的可選反推

提供獨立 script `scripts/backfill_user_id_from_telegram.py`：

```python
# 從 conversation_sessions 取 channel='telegram' 的紀錄
# channel_uid 即為 telegram user_id
# 對應 session 下的 conversation_messages, memory_blocks, ...
# 將 user_id 從 'default' 改為 channel_uid
# 不動 visibility（保持 'public'）
```

**手動執行**，不在自動 migration 內。執行前必須備份。

### 9.3 SU_ID 設定

```bash
# .env
SU_USER_ID=123456789  # 你的 Telegram user id
```

或在 `user_prefs.json` 加 `su_user_id` 欄位。

### 9.4 升級不可逆性

- Schema 變更為**累加式**（只加欄位、改 PK），不刪資料
- `'default'` user 與 `'public'` visibility / face 永久保留作為相容層
- 若反推 script 跑錯，可手動 SQL 還原

---

## 10. 陷阱清單

| 項目 | 說明 |
|---|---|
| `save_db()` 全表刪除 | **最高風險**：必須改為 `(user_id, character_id, visibility)` scoped DELETE |
| `delete_profile()` 缺 user_id | 第二高風險：誤刪其他用戶的同 fact_key 資料 |
| `memory.py` 的 `sqlite3` 直連 | 必須改走 `StorageManager.delete_core_memory` |
| `find_pending_clusters` 跨用戶合併 | 必須限定 `(user_id, character_id, visibility)` 同範圍 |
| `_run_probe_sync` 跨 face 污染 | 必須逐 face 跑、`load_fragments_from_db(visibility=...)` 限定 |
| PersonaProbe `load_fragments_from_db` 預設讀全部 | 必須加 visibility / user_ids filter |
| `apply_profile_facts` 漏標 visibility | SU 隱私洩漏：私訊事實在直播 prompt 出現 |
| Profile 載入時 filter 漏 | public face 載入到 visibility='private' fact |
| `pipeline_data` tuple 擴充 | 改為 `PipelineContext` dataclass，所有 unpack 點同步 |
| WebSocket `clear_context` | 重建 session 必須保留 `user_id` / `character_id` / `channel_class` |
| `api_messages.extend(clean_history)` | 修改 sys_prompt 組裝後必須確認此行仍存在 |
| Schema migration 併發 | 必須 `BEGIN IMMEDIATE` + `PRAGMA table_info` 雙重檢查 |
| `user_profile` PK 重建順序 | 必須先 vectors 後 profile（FK 依賴），或先 disable FK |
| Cache key 缺 visibility | cache 載入全 visibility 由查詢端 filter，避免 key 爆炸 |

---

## 11. 驗證方式

### 11.1 單元測試（補預設值）

現有 `tests/` 所有測試呼叫 StorageManager / MemorySystem 的點補上 `user_id='default'`、`character_id='default'`、`visibility='public'`、`persona_face='public'`，確保向後相容。

### 11.2 隔離測試（新增）

```python
def test_memory_isolation_by_user():
    # 兩組 (user_id, character_id) 各寫入記憶
    # search_blocks 只能取回自己 user_id 的
    # 跨用戶不能召回

def test_visibility_asymmetric_read():
    # SU 寫 private 與 public 各一筆
    # face='private' 讀取：兩筆都返回
    # face='public' 讀取：只 public 那筆
```

### 11.3 污染防護測試

```python
def test_no_cross_user_cluster_merge():
    # user_A 與 user_B 寫入相似內容（cosine > 0.85）
    # find_pending_clusters 不返回跨用戶 group
    # add_memory_block 不會誤合併

def test_persona_face_independent_evolution():
    # 對 private 與 public 各餵不同調性的對話
    # 兩個 face 的 active_traits 不重疊
    # save_snapshot 寫入時 face 正確
```

### 11.4 雙 Face 並行測試

```python
async def test_concurrent_faces():
    # 同時送 SU 私訊 + 直播留言
    # 各自 system prompt 含對應 face 的 evolved_prompt
    # 各自 retrieval 帶對應 visibility filter
    # DB 寫入無 PK 衝突
```

### 11.5 SU Profile Visibility 測試

```python
def test_su_private_fact_not_in_public_prompt():
    # SU 私訊抽取 fact (visibility=private)
    # SU 直播留言 → public face 回應
    # get_static_profile_prompt 不含該 private fact
```

### 11.6 向後相容測試

```python
def test_legacy_default_call_works():
    # 不帶 user_id / visibility / persona_face 的呼叫
    # 落到 'default' / 'public' / 'public'
    # 能讀到既有資料
```

---

## 12. 改動優先順序

```
Phase 1: Schema migration + StorageManager（含 face 與 visibility）
Phase 2: CoreMemory + visibility filter
Phase 3: SessionState + resolve_context helper
Phase 4: 路由層 + PipelineContext dataclass
Phase 5: PersonaSync 雙 face 改造（含 PersonaProbe 的 load_fragments_from_db）
Phase 6: Profile 三類策略 + 周邊元件（PreferenceAggregator / background_gatherer / memory.py）
Phase 7: 資料遷移 script + 完整測試套件
```

每個 Phase 完成後可獨立測試、不需一次完成。Phase 1 與 Phase 5 是兩個關鍵風險點，分別對應「記憶污染」與「人格污染」的根治。

### 各 Phase 完成標準

- **Phase 1**：所有舊測試帶預設值通過；`save_db` / `delete_profile` 隔離測試通過
- **Phase 2**：visibility filter 測試通過；cluster merge 不跨用戶
- **Phase 3-4**：REST / WS / Telegram 端到端跑通，user_id 從 channel 正確流到 pipeline
- **Phase 5**：雙 face 各自能跑出獨立 snapshot；private 對話不影響 public face
- **Phase 6**：SU private fact 在 public face response 中不出現
- **Phase 7**：Telegram 反推 script 在備份 DB 上驗證；隔離 + 並行 + 污染防護測試全部通過
