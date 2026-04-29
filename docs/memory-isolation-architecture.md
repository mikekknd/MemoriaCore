# 記憶隔離架構：三維度正交隔離（user_id × visibility × persona_face）

## 1. 系統定位與目標

MemoriaCore 支援多來源用戶（ Telegram、直播、Discord 等），記憶隔離架構確保：

1. **資料歸屬隔離** — 誰的記憶就是誰的，不會洩漏到別人
2. **公私可見性不對稱** — private face 可讀 public 記憶，public face 讀不到 private 記憶
3. **人格雙面獨立演化** — private 與 public 人格各自演化，互不污染

---

## 2. 三個正交維度

| 維度 | 用途 | 取值 | 套用範圍 |
|---|---|---|---|
| `user_id` | 資料歸屬 — 誰的記憶 | 真實用戶 ID（Telegram user id、`'default'` 等） | 記憶系統所有表 |
| `visibility` | 可見性 — 誰看得到 | `'private'` \| `'public'` | 記憶 / 事實 / 對話 |
| `persona_face` | 人格面向 — 演哪一面 | `'private'` \| `'public'` | 人格演化系統 |

**正交性原則**：三個維度各自獨立決策。

---

## 3. Context 解析

**檔案：`core/deployment_config.py`**

```python
PUBLIC_CHANNELS: frozenset[str] = frozenset({'livestream', 'discord_public'})
EXTRACT_PROFILE_FROM_CHANNELS: frozenset[str] = frozenset({'telegram', 'rest', 'discord_private'})
SU_USER_ID: str = os.getenv('SU_USER_ID', '')

def resolve_context(user_id: str, channel: str) -> tuple[str, str]:
    """
    回傳 (persona_face, write_visibility)
    - 公開頻道（livestream / discord_public）→ public / public
    - SU 私訊（telegram 或 rest，且 user_id == SU_USER_ID）→ private / private
    - 其他所有情況 → public / public
    """
```

### 用戶類型矩陣

| 用戶類型 | channel 範例 | persona_face | write_visibility | profile 抽取 |
|---|---|---|---|---|
| **SU 私訊** | telegram (user_id=SU) / rest | `private` | `private` | ✓ |
| **SU 公開發言** | livestream (user_id=SU) | `public` | `public` | ✓ |
| **Telegram 非 SU** | telegram (user_id≠SU) | `public` | `public` | ✓ |
| **直播觀眾** | livestream (user_id≠SU) | `public` | `public` | ✗ |
| **DC 公開** | discord_public | `public` | `public` | ✗ |

---

## 4. 公私可見性不對稱

### 讀取規則

```
persona_face='private'  →  WHERE visibility IN ('private', 'public')   # 可讀全部
persona_face='public'   →  WHERE visibility = 'public'                # 只讀 public
```

### 效果

- SU 私訊問「今天直播聊了什麼」→ private face 能讀到 public 的直播記憶 ✓
- 觀眾在直播問「你和 SU 私下都聊什麼」→ public face 讀不到 private 記憶 ✓

---

## 5. 資料庫 Schema

### 5.1 記憶相關表

所有表皆有三個共同欄位：

| 欄位 | 類型 | 預設值 |
|---|---|---|
| `user_id` | TEXT NOT NULL | `'default'` |
| `character_id` | TEXT NOT NULL | `'default'` |
| `visibility` | TEXT NOT NULL | `'public'` |

**Tables**：
- `memory_blocks` — 記憶區塊（帶 BGE-M3 embedding）
- `core_memories` — 核心記憶
- `ai_personality_observations` — AI 人格觀察
- `topic_cache` — 主題快取；背景蒐集產生的 user-level topic 使用 `character_id='__global__'`，詳見 `docs/proactive-topic-architecture.md`

### 5.2 user_profile 表

```sql
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

CREATE TABLE user_profile_vectors (
    user_id TEXT NOT NULL DEFAULT 'default',
    fact_key TEXT NOT NULL,
    fact_value TEXT NOT NULL,
    fact_vector BLOB,
    PRIMARY KEY (user_id, fact_key, fact_value),
    FOREIGN KEY (user_id, fact_key, fact_value)
        REFERENCES user_profile(user_id, fact_key, fact_value) ON DELETE CASCADE
);
```

### 5.3 人格演化表（雙 face）

| 表 | 主鍵 | face 關聯 |
|---|---|---|
| `persona_snapshots` | `(character_id, persona_face, version)` | 直接欄位 `persona_face` |
| `persona_traits` | `(character_id, persona_face, trait_key)` | 直接欄位 `persona_face` |
| `persona_dimensions` | 透過 `snapshot_id` 外鍵繼承 | 間接透過 snapshot |

---

## 6. 雙 Face 並行運作

系統天然支援雙 face 並行，原因：

1. **每個請求獨立 resolve** — 每個請求各自解析 context，不共享全域狀態
2. **System prompt per-request 重組** — 每次回應都從 DB 撈當下需要的 face 的 evolved_prompt
3. **StorageManager async lock** — 序列化寫入，無 PK 衝突
4. **Persona sync 背景閒置觸發** — 不插入活躍對話

**副作用（by design）**：private 對話無法即時影響 public face 回應。

---

## 7. 核心模組職責

| 模組 | 職責 |
|---|---|
| `core/deployment_config.py` | `resolve_context()`、`should_extract_profile()` — 三維度入口 |
| `core/storage_manager.py` | 所有 DB 操作，嚴格 scope 到 `(user_id, character_id, visibility)` |
| `core/core_memory.py` | 三軌檢索、Memory Block 寫入、Profile 管理 |
| `core/persona_sync.py` | `PersonaSyncManager` — per-character/per-face 獨立觸發與演化 |
| `core/persona_evolution/` | 人格快照、trait lineage、BGE-M3 parent inference |
| `core/character_engine.py` | `get_effective_prompt(persona_face)` — face-aware system prompt 組合 |

---

## 8. Persona Face 人格演化流程

```
resolve_context(user_id, channel)
    ↓
(新增對話片段)
    ↓
自動 PersonaSync 從 conversation DB 掃描曾有 assistant 發言的 character_id
    ↓
PersonaSyncManager.should_run(character_id, persona_face)
    ↓ (觸發)
Run _run_probe_sync(persona_face)
    ├── load_fragments_from_db(face=persona_face)  — 只取對應 visibility 的對話
    ├── list_active_traits(character_id, persona_face)  — 讀取對應 face 的 trait 樹
    ├── V1 / Vn 分支
    └── save_snapshot(persona_face)  — 寫入對應 face 的 snapshot + traits
```

---

## 9. 檔案對照表

```
G:\ClaudeProject\MemoriaCore\
├── core/
│   ├── deployment_config.py       # resolve_context()、should_extract_profile()
│   ├── storage_manager.py        # SQLite async lock，scope DELETE/SELECT
│   ├── core_memory.py             # 三軌檢索、memory_block CRUD
│   ├── persona_sync.py           # PersonaSyncManager，per-face 觸發
│   └── persona_evolution/
│       ├── snapshot_store.py      # PersonaSnapshotStore 高層 orchestrator
│       ├── lineage.py             # BGE-M3 cosine similarity parent inference
│       ├── extractor.py           # V1 / Vn trait parse
│       └── trait_diff.py          # TraitDiff / TraitUpdate / NewTrait
├── api/
│   ├── routers/
│   │   ├── memory.py             # 記憶 CRUD，scope to (user_id, character_id, visibility)
│   │   └── persona_evolution.py  # 7 個 GET 端點（唯讀）
│   └── models/
│       └── persona_evolution.py   # Pydantic DTOs
└── docs/
    └── memory-isolation-architecture.md  # 本文件
```
