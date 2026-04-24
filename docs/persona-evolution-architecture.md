# Persona Evolution 系統架構

## 架構圖

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
│  │  api/routers/persona_evolution.py  (5 GET endpoints)                 │ │
│  │                                                                      │ │
│  │  GET /snapshots            → list all snapshots (summary)             │ │
│  │  GET /snapshots/latest     → latest full snapshot                    │ │
│  │  GET /snapshots/{version}  → specific version snapshot               │ │
│  │  GET /snapshots/{version}/tree → Unity force-directed graph JSON    │ │
│  │  GET /dimensions/timeline  → confidence timeline for one dimension    │ │
│  └──────────────────────────────────────────────────────────────────────┘ │
│  ┌──────────────────────────────────────────────────────────────────────┐ │
│  │  api/models/persona_evolution.py  (Pydantic DTOs)                 │ │
│  │  PersonaSnapshotDTO / PersonaTreeDTO / DimensionTimelineDTO / ...  │ │
│  └──────────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────┬────────────────────────────────────────┘
                                   │ DI singleton
                                   ▼
┌────────────────────────────────────────────────────────────────────────────┐
│  core/persona_evolution/snapshot_store.py                                 │
│  PersonaSnapshotStore  (high-level orchestrator)                         │
│  ├── save_snapshot()   — parse + lineage inference + atomic write         │
│  ├── get_tree()        → Unity graph structure                            │
│  └── get_dimension_timeline()                                            │
└──────────────────────────────────┬────────────────────────────────────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              │                    │                    │
              ▼                    ▼                    ▼
┌─────────────────────┐  ┌─────────────────┐  ┌──────────────────────────┐
│ extractor.py        │  │ lineage.py      │  │ StorageManager           │
│                     │  │                 │  │                          │
│ DimensionExtraction │  │ infer_parents() │  │ get_latest_persona_*()   │
│ parse_extraction()  │  │ bge_m3_embed()  │  │ save_persona_snapshot()  │
│ (fault-tolerant)    │  │ cosine sim 0.82 │  │ get_dimension_timeline() │
│                     │  │ BGE-M3 ONNX     │  │                          │
└─────────────────────┘  └─────────────────┘  │ SQLite via async lock   │
                                               └──────────────────────────┘
              │                    ▲
              │ raw LLM output     │ embedding vector
              ▼                    │
┌─────────────────────┐            │
│ core/persona_sync.py │◄──────────┘
│ (PersonaSyncManager)│
└──────────┬──────────┘
           │ triggers after persona evolution check
           ▼
┌────────────────────────────────────────────────────────────────────────────┐
│  PersonaProbe Sub-project (port 8089)  ←── Streamlit UI (port 8502)       │
│  ┌──────────────────────────────────────────────────────────────────────┐ │
│  │  server.py — FastAPI (8089)  +  probe_engine.py  +  llm_client.py    │ │
│  │  LLM extraction of persona dimensions from conversation messages     │ │
│  └──────────────────────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────────────────────┘
```

---

## 各端口功能

| 端口 | 服務 | 職責 |
|------|------|------|
| **8088** | FastAPI `api/main.py` | 主伺服器。MemoriaCore 所有核心 API（含 `persona_evolution` router）|
| **8501** | Streamlit `ui/chat.py` | 主 UI。對話介面 + debug panel + session 管理 |
| **8502** | Streamlit `PersonaProbe/app.py` | 人格採集工具 UI。人格題目呈現 + LLM 萃取結果展示 |
| **8089** | FastAPI `PersonaProbe/server.py` | PersonaProbe API server。提供人格題目下發 + LLM 萃取端點 |
| **任意** | WebSocket `/ws/chat` (8088) | 雙向對話即時通訊 |

---

## 資料流向

```
PersonaProbe LLM 萃取結果（dict/str）
    ↓
extractor.parse_extraction()          → DimensionExtraction Pydantic model
    ↓
PersonaSnapshotStore.save_snapshot()
    ├── lineage.infer_parents()       → BGE-M3 ONNX embedding + cosine similarity 0.82
    ├── StorageManager.get_latest()   → 讀取前版 dimension 找出 parent
    └── StorageManager.save_snapshot()→ 寫入 SQLite（persona_snapshots / persona_dimensions）
              │
              │          read path (FastAPI)
              └──────────────────→ GET /snapshots → PersonaSnapshotDTO
                               GET /snapshots/{version}/tree → PersonaTreeDTO
                               GET /dimensions/timeline → DimensionTimelineDTO
              │
              ▼
         Unity client 收到 JSON → 渲染 Force-Directed Graph + Timeline Chart
```

---

## 核心模組職責

| 模組 | 職責 |
|------|------|
| `extractor.py` | 將 LLM 回傳的原始文字/dict 做 fault-tolerant 解析，轉成 `DimensionExtraction` |
| `lineage.py` | 用 BGE-M3 embedding 計算新舊 dimension 的 cosine similarity，推斷 parent lineage |
| `snapshot_store.py` | 整合 extraction + lineage + StorageManager，提供高階 `save_snapshot / get_tree / get_dimension_timeline` |
| `persona_evolution.py` (router) | 5 個 **唯讀** REST GET 端點，統一回傳 Pydantic DTO |
| `persona_evolution.py` (models) | API 請求/回應的 DTO 定義 |
| `persona_sync.py` | 在對話後觸發同步流程，驅動整個萃取→儲存 chain |

---

## API 端點一覽

Base prefix: `/api/v1/system/personality`

| Method | Route | 說明 |
|--------|-------|------|
| GET | `/snapshots?character_id=xxx` | 列出該角色所有快照摘要（依版本排序） |
| GET | `/snapshots/latest?character_id=xxx` | 取得最新快照（含完整 dimensions） |
| GET | `/snapshots/{version}?character_id=xxx` | 取得指定版本快照 |
| GET | `/snapshots/{version}/tree?character_id=xxx` | 取得 Unity Force-Directed Graph 結構 |
| GET | `/dimensions/timeline?character_id=xxx&name=xxx` | 取得特定 dimension 跨版本信心度時序 |

---

## 檔案對照表

```
G:\ClaudeProject\MemoriaCore\
├── core/
│   └── persona_evolution/
│       ├── __init__.py          # Package facade，re-export 三層公共介面
│       ├── extractor.py         # DimensionExtraction Pydantic model + fault-tolerant parse
│       ├── lineage.py           # BGE-M3 embedding + cosine similarity parent inference
│       └── snapshot_store.py    # PersonaSnapshotStore 高層 orchestrator
├── api/
│   ├── main.py                  # 註冊 persona_evolution.router（/api/v1 prefix）
│   ├── models/
│   │   └── persona_evolution.py # Pydantic DTOs
│   └── routers/
│       └── persona_evolution.py  # 5 個 GET 唯讀端點
└── docs/
    └── persona-evolution-snapshot.md  # 系統設計文件
```
