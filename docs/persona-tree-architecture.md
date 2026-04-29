# Persona Evolution 系統完整架構（Path D — Trait Evolution）

> 本文件為當前最新版本，基於 Path D（增量 Trait 樹）架構。
> 舊版 6 維度萃取邏輯仍保留於 `PersonaProbe/probe_engine.py`，僅供互動式採集使用，
> 主專案人格同步已全面改為 Path D。

---

## 一、系統定位

**Path D** 是主專案 `core/persona_sync.py` 使用的增量 trait 演化流程，與
`PersonaProbe` 子專案的互動式採集（6 維度 DIMENSION_SPECS）**並行存在**，兩者共享：
- `core/persona_evolution/` 核心模組
- `persona_snapshots.db` SQLite 資料庫
- `PersonaProbe/probe_engine.py` 中的 prompt builder（`build_trait_v1_prompt` 等）
- `PersonaProbe/prompts_trait.py` System Prompt 常數

---

## 二、資料庫結構（`persona_snapshots.db`，Schema v2）

三張表，由 `StorageManager._init_persona_snapshot_db()` 管理（PRAGMA user_version=2）：

**`persona_snapshots`**
| 欄位 | 說明 |
|------|------|
| `id` | 主鍵 |
| `character_id` | 角色識別 |
| `version` | 版本號（遞增） |
| `timestamp` | ISO 8601 時間戳 |
| `summary` | 演化摘要（probe-report 首段） |
| `evolved_prompt` | 完整 persona.md 內容 |

**`persona_traits`**（跨版本真實血統表）
| 欄位 | 說明 |
|------|------|
| `trait_key` | UUID hex（32 字元），跨版本穩定識別碼 |
| `character_id` | 角色識別 |
| `name` | Trait 名稱（2~8 字短詞） |
| `created_version` | 首次出現版本 |
| `last_active_version` | 最近一次被更新（updates/new）版本 |
| `parent_key` | 指向另一 `trait_key`（可為 NULL 表示 root） |
| `is_active` | 是否活躍（休眠後為 0，B' sweep 規則） |
| `created_at` | 時間戳 |

**`persona_dimensions`**（版本快照明細）
| 欄位 | 說明 |
|------|------|
| `dimension_key` | 等於 `trait_key`（UUID） |
| `name` | 同 `persona_traits.name`（denormalised） |
| `confidence` | 浮點信心度（0.0~10.0） |
| `confidence_label` | 字串標籤：`high`/`medium`/`low`/`none` |
| `description` | 描述（由 LLM 提供） |
| `parent_name` | denormalised cache（display 用） |
| `is_active` | 永遠寫 1（真實狀態在 `persona_traits`） |

---

## 三、Trait 生成判斷流程（Path D）

### 第一層：V1 vs Vn 分支判斷

```python
active_traits = store.list_active_traits(character_id)
is_v1 = len(active_traits) == 0  # 無活躍 trait → 首版
```

- **V1（首版）**：從對話片段萃取 3~5 個 root trait，全部 `parent_key = None`
- **Vn（增量）**：同時輸出 `updates`（既有 trait 強度調整）與 `new_traits`（新 trait）

### 第二層：LLM 萃取（3 次呼叫）

**V1 Prompt：`build_trait_v1_prompt`**
```
system: TRAIT_V1_SYSTEM
user: [fragments_text] → 輸出 {"new_traits": [{"name","description","confidence"}, ...]}
```

**Vn Prompt：`build_trait_vn_prompt`**
```
system: TRAIT_VN_SYSTEM
user: [active_traits 清單] + [fragments_text]
→ 輸出 {"updates": [{"trait_key","confidence"}, ...],
       "new_traits": [{"name","description","parent_key","confidence"}, ...]}
```

**共同後段：**
1. `build_trait_report_prompt` → Markdown 敘事報告（probe-report.md）
2. `build_persona_md_prompt` → 更新 persona.md

### 第三層：Confidence 過濾

| confidence | 寫入 `persona_dimensions`？ | 說明 |
|---|---|---|
| `high` | ✅ 是（8.0） | 多次獨立證據 |
| `medium` | ✅ 是（5.0） | 1~2 次明確片段 |
| `low` | ✅ 是（2.5） | 單一片段，推斷性強 |
| `none` | ❌ 不寫（但 bump `last_active_version`） | LLM 仍注意到 trait，只是本版無強證據 |

### 第四層：Parent Key 推斷（`infer_single_parent`）

LLM 可能填錯或未填 `parent_key`，此時 fallback 邏輯：
1. 若 `parent_key` 所指 trait 在歷史中存在（已休眠也可）→ 直接採用
2. 若無效 → 以 BGE-M3 ONNX 計算 `new_trait.description` vs `active_traits[*].last_description` cosine similarity，≥ 0.82 → 該 trait_key 為 parent
3. 都無匹配 → `parent_key = None`（root trait）

---

## 四、B' 休眠規則

**觸發條件（於 `save_trait_snapshot` 交易尾端 sweep）**：
```
(current_version - last_active_version) >= 3 AND
最近一次 confidence <= 5.0（medium門檻）
→ is_active = 0
```

效果：
- 連續 3 版未被更新且 confidence 偏低的 trait 進入休眠
- 被引用為 `parent_key` 的休眠 trait 會自動 reactivate 並 bump `last_active_version`
- 休眠 trait 仍存在於 `persona_traits` 表（可被後續 `parent_key` 引用）

---

## 五、節點生成判斷總結

```
輸入：conversation.db 對話片段
  ↓
有活躍 trait？→ 否 → V1（3~5 個 root trait，全部 parent_key=None）
             → 是 → Vn
  ↓
V1: build_trait_v1_prompt → LLM → parse_trait_v1 → list[NewTrait]
Vn: build_trait_vn_prompt → LLM → parse_trait_vn → TraitDiff(updates, new_traits)
  ↓
new_traits 每筆生成 uuid4().hex → 作為 trait_key
parent_key fallback → infer_single_parent (BGE-M3 cosine ≥ 0.82)
  ↓
save_trait_snapshot → 原子寫入 persona_traits + persona_dimensions + B' sweep
  ↓
前端以 trait_key 為節點 id，parent_key 為 Edges 渲染 Force-Directed Graph
```

---

## 六、API 端點（唯讀，寫入由 PersonaSyncManager 觸發）

Base prefix: `/api/v1/system/personality`

| Method | Route | 說明 |
|--------|-------|------|
| GET | `/snapshots?character_id=` | 版本清單摘要（遞增排序） |
| GET | `/snapshots/latest?character_id=` | 最新完整 snapshot |
| GET | `/snapshots/latest/tree?character_id=` | 最新版 Force-Directed Graph 結構 |
| GET | `/snapshots/{version}?character_id=` | 指定版本 snapshot |
| GET | `/snapshots/{version}/tree?character_id=` | 指定版本 Force-Directed Graph 結構 |
| GET | `/traits?character_id=&active_only=` | Trait 清單（debug 用，預設只回活躍） |
| GET | `/traits/timeline?character_id=&trait_key=` | 單一 trait 的 confidence 折線 |

---

## 七、`persona_tree.html` 視覺化

- **輪詢**：`GET /snapshots/latest/tree`，每 5 分鐘一次
- **資料來源**：`PersonaTreeDTO`（nodes + links）
- **節點識別**：`id = dimension_key = trait_key`（UUID hex）
- **Links 推導**：`parent_key` 存在於同一 snapshot 的 nodes 中才加 link（避免孤立邊）
- **節點顏色**：`high`→綠、`medium`→藍、`low`→黃、`none`→灰（由 `confidence_label` 決定）
- **節點半徑**：`confidence * 4`（RADIUS_SCALE = 4）
- **增量動畫**：`applyTree()` 保留既有節點座標，只更新 confidence；新節點從 parent 座標初始化

---

## 八、核心模組職責

| 模組 | 職責 |
|------|------|
| `core/persona_evolution/__init__.py` | Package facade，re-export 所有公開介面 |
| `core/persona_evolution/constants.py` | B' 休眠參數（3版/5.0門檻）與 MAX_ACTIVE_TRAITS_IN_PROMPT（20） |
| `core/persona_evolution/trait_diff.py` | `TraitDiff` / `TraitUpdate` / `NewTrait` Pydantic 結構 |
| `core/persona_evolution/extractor.py` | `TRAIT_V1_SCHEMA` / `TRAIT_VN_SCHEMA` + `parse_trait_v1` / `parse_trait_vn` 容錯解析 |
| `core/persona_evolution/lineage.py` | `infer_single_parent` — BGE-M3 cosine fallback，threshold=0.82 |
| `core/persona_evolution/snapshot_store.py` | `PersonaSnapshotStore` 高層 orchestrator，組合 lineage + storage |
| `core/storage_manager.py`（SECTION: 人格演化 Snapshots）| Schema v2 建構、`save_trait_snapshot` 原子寫入 + B' sweep |
| `core/persona_sync.py`（`PersonaSyncManager`）| 觸發條件判斷、3 次 LLM 呼叫流程、evolved_prompt 寫入 |
| `api/routers/persona_evolution.py` | 7 個 GET 唯讀端點 |

---

## 九、Path D LLM 流程（`core/persona_sync.py::_run_probe_sync`）

```
1. load_fragments_from_db(db_path, limit=400)
   ↓
2. list_active_traits → is_v1?

   【V1 分支】
   build_trait_v1_prompt → LLM → parse_trait_v1 → list[NewTrait]
   (new_traits 只帶 name/description/confidence，parent_key=None)

   【Vn 分支】
   build_trait_vn_prompt → LLM → parse_trait_vn → TraitDiff(updates, new_traits)
   (updates 只改 confidence，new_traits 可帶 parent_key)

   ↓（共同後段）
3. build_trait_report_prompt → LLM → full_report (Markdown)
4. build_persona_md_prompt → LLM → new_persona_content
   ↓
5. 寫入 result/fragment-{timestamp}/（留存備份）
6. 返回 {persona, trait_diff, active_traits, summary, output_dir}
```

---

## 十、TraitDiff 資料結構

```python
class TraitUpdate(BaseModel):
    trait_key: str           # UUID，指向既有 trait
    confidence: ConfidenceLabel  # high/medium/low/none

class NewTrait(BaseModel):
    name: str
    description: str
    confidence: ConfidenceLabel
    parent_key: str | None   # 可指向任何歷史 trait_key，None=root

class TraitDiff(BaseModel):
    updates: list[TraitUpdate]
    new_traits: list[NewTrait]
```

---

## 十一、DIMENSION_SPECS 維度定義（舊版，僅供 PersonaProbe 互動式採集使用）

| ID | 名稱 | 核心問題 |
|----|------|----------|
| 1 | 決策邏輯 | 在資源、控制權、價值觀三者衝突時的優先順序 |
| 2 | 思考方式 | 被挑戰時的反應模式——防禦、吸收、偽裝讓步？ |
| 3 | 表達 DNA | 解釋事物的慣用結構與語言風格 |
| 4 | 核心動機 | 穿透表層動機，挖掘底層真實驅動力 |
| 5 | 轉折與信念 | 核心信念的形成與動搖經歷 |
| 6 | 行動模式 | 困境中的行為策略與能量來源 |

---

## 十二、架構圖

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Unity / Frontend                                  │
│                 (Force-Directed Graph + Timeline Chart)                   │
└──────────────────────────────────┬────────────────────────────────────────┘
                                   │ JSON REST (GET)
                                   ▼
┌────────────────────────────────────────────────────────────────────────────┐
│  FastAPI Main Server (port 8088)                                           │
│  ┌──────────────────────────────────────────────────────────────────────┐ │
│  │  api/main.py — registers persona_evolution.router at /api/v1/system  │ │
│  └──────────────────────────────────────────────────────────────────────┘ │
│  ┌──────────────────────────────────────────────────────────────────────┐ │
│  │  api/routers/persona_evolution.py  (7 GET endpoints)                  │ │
│  │  GET /snapshots, /snapshots/latest/tree, /traits, /traits/timeline... │ │
│  └──────────────────────────────────────────────────────────────────────┘ │
│  ┌──────────────────────────────────────────────────────────────────────┐ │
│  │  api/models/persona_evolution.py  (Pydantic DTOs)                     │ │
│  └──────────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────┬────────────────────────────────────────┘
                                   │ DI singleton
                                   ▼
┌────────────────────────────────────────────────────────────────────────────┐
│  core/persona_evolution/snapshot_store.py                                 │
│  PersonaSnapshotStore  (high-level orchestrator)                         │
│  ├── save_snapshot(trait_diff)  — parse + lineage + atomic write + B' sweep│
│  ├── list_active_traits()       — 供 Vn 分支判斷                          │
│  ├── get_tree() / get_latest_tree()                                      │
│  └── get_trait_timeline()                                               │
└──────────────────────────────────┬────────────────────────────────────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              │                    │                    │
              ▼                    ▼                    ▼
┌─────────────────────┐  ┌─────────────────┐  ┌──────────────────────────────┐
│ extractor.py        │  │ lineage.py      │  │ StorageManager               │
│                     │  │                 │  │                              │
│ TRAIT_V1_SCHEMA     │  │ infer_single_   │  │ save_trait_snapshot()        │
│ TRAIT_VN_SCHEMA     │  │ parent()        │  │ get_active_traits()          │
│ parse_trait_v1()    │  │ BGE-M3 cosine   │  │ get_trait_timeline()         │
│ parse_trait_vn()    │  │ threshold=0.82  │  │ _init_persona_snapshot_db()  │
└─────────────────────┘  └─────────────────┘  │ (Schema v2: persona_traits)  │
                                               └──────────────────────────────┘
              │                    ▲
              │ raw LLM output     │ embedding vector
              ▼                    │
┌─────────────────────┐            │
│ core/persona_sync.py │◄──────────┘
│ (PersonaSyncManager)│
│  - 每 20 分鐘檢查一次觸發條件
│  - 滿足後呼叫 _run_probe_sync
└──────────┬──────────┘
           │ 觸發後 3 次 LLM 呼叫
           ▼
┌────────────────────────────────────────────────────────────────────────────┐
│  PersonaProbe Sub-project (port 8089)  ←── Streamlit UI (port 8502)       │
│  ┌──────────────────────────────────────────────────────────────────────┐ │
│  │  probe_engine.py  — build_trait_v1_prompt / build_trait_vn_prompt   │ │
│  │  prompts_trait.py  — TRAIT_V1_SYSTEM / TRAIT_VN_SYSTEM /            │ │
│  │                      TRAIT_REPORT_SYSTEM                            │ │
│  │  llm_client.py     — LLMClient (Ollama / OpenRouter)               │ │
│  └──────────────────────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────────────────────┘
```

---

## 十三、Path D 與舊 6 維度的差異

| 項目 | 舊 6 維度（Path prototype） | Path D（當前） |
|------|------|------|
| 節點數 | 固定 6 個維度 | 動態 3~5 個 root，隨版本增減 |
| 識別碼 | `dimension_key`（如 `fragment_1`） | `trait_key`（UUID hex，跨版本穩定） |
| 父子關係 | `parent_name` 字串比對 | `parent_key` 直接指向 `trait_key` |
| 血統推斷 | 整批 cosine（`infer_parents`） | 單筆 fallback（`infer_single_parent`） |
| LLM 呼叫次數 | 8 次（6 維度萃取 + 聚合 + persona） | 3 次（trait diff + report + persona） |
| 休眠規則 | 無 | B' sweep（3 版閒置 + confidence ≤ 5.0） |
| Schema | 6 維度固定 schema | V1 / Vn 動態 schema |
| 使用場景 | PersonaProbe 互動式採集 | 主專案人格同步（`PersonaSyncManager`） |

---

## 十四、檔案對照表

```
G:\ClaudeProject\MemoriaCore\
├── core/
│   ├── persona_sync.py           # PersonaSyncManager，20 分鐘檢查 + 觸發
│   └── persona_evolution/
│       ├── __init__.py           # Package facade
│       ├── constants.py          # DORMANCY_IDLE_VERSIONS=3, DORMANCY_CONFIDENCE_THRESHOLD=5.0
│       ├── trait_diff.py         # TraitDiff / TraitUpdate / NewTrait Pydantic
│       ├── extractor.py          # TRAIT_V1/VN_SCHEMA + parse_trait_v1/vn
│       ├── lineage.py            # infer_single_parent (BGE-M3 cosine, 0.82)
│       └── snapshot_store.py     # PersonaSnapshotStore 高層 orchestrator
├── api/
│   ├── main.py                   # 註冊 persona_evolution.router
│   ├── models/
│   │   └── persona_evolution.py  # Pydantic DTOs（trait_key, parent_key, is_active 等）
│   └── routers/
│       └── persona_evolution.py  # 7 個 GET 端點
├── static/
│   └── persona_tree.html         # D3.js Force-Directed Graph 前端
├── PersonaProbe/
│   ├── probe_engine.py           # build_trait_v1/vn/report_prompt, build_persona_md_prompt
│   ├── prompts_trait.py          # TRAIT_V1_SYSTEM / TRAIT_VN_SYSTEM / TRAIT_REPORT_SYSTEM
│   ├── server.py                 # FastAPI (port 8089)
│   ├── app.py                   # Streamlit (port 8502)
│   └── llm_client.py            # LLMClient (Ollama / OpenRouter)
└── docs/
    └── persona-evolution-architecture.md  # 系統設計文件
```

---

## 十五、Confidence 映射表（`extractor.py`）

```python
CONFIDENCE_MAP = {
    "high": 8.0,
    "medium": 5.0,
    "low": 2.5,
    "none": 0.0,
}
```

---

## 十六、觸發條件（`PersonaSyncManager.should_run`）

自動 PersonaSync 每輪先由 conversation DB 推導候選角色：所有曾有 assistant 發言的
`character_id` 都會被檢查，且不以 `active_character_id` / default character 補位。
這等同 derived dirty set，避免空角色或全域預設角色污染同步目標。

全部滿足才執行：
1. `persona_sync_enabled == True`
2. 今日執行次數 < `persona_sync_max_per_day`（預設 2）
3. 最後一筆訊息距今 > `persona_sync_idle_minutes`（預設 10 分鐘）
4. 上次反思後新訊息數 >= `persona_sync_min_messages`（預設 50）

`insufficient_messages(...)` 代表角色尚未累積足夠素材，是正常等待狀態，不寫入
`persona_sync_skip` 系統 log。

---

## 十七、Write Path 完整流程

```
PersonaSyncManager.run_sync() 觸發
  ↓
await should_run() → 確認條件滿足
  ↓
await asyncio.to_thread(_run_probe_sync)
  ├── load_fragments_from_db() → fragments_text
  ├── list_active_traits() → is_v1 判斷
  ├── LLM 第 1 次：trait extraction
  │     V1: build_trait_v1_prompt → parse_trait_v1 → list[NewTrait]
  │     Vn: build_trait_vn_prompt → parse_trait_vn → TraitDiff
  ├── LLM 第 2 次：build_trait_report_prompt → full_report
  ├── LLM 第 3 次：build_persona_md_prompt → new_persona
  └── 回傳 {persona, trait_diff, summary, output_dir}
  ↓
char_mgr.set_evolved_prompt() → 寫入 ai_persona.md
  ↓
snapshot_id = store.save_snapshot(
  character_id, trait_diff, summary, evolved_prompt
)
  └── StorageManager.save_trait_snapshot()
       ├── 原子寫入 persona_snapshots + persona_traits + persona_dimensions
       └── 同交易尾端 B' sweep（休眠候選 → is_active=0）
  ↓
狀態更新（last_reflection_at, today_run_count）
```
