# 專案目錄結構詳解

> 給 Agent（Claude / Codex）查找模組職責用。CLAUDE.md / AGENTS.md 只保留頂層目錄；
> 任何需要深入到 package 內部、SECTION 切分、檔案職責的問題都來這裡查。
>
> 高層架構與請求流程請改看 `docs/架構說明.md`。

---

## 頂層目錄

- `core/` — 記憶、LLM 路由、人格、儲存引擎
- `api/` — FastAPI routers，singleton DI 由 `api/dependencies.py` 管理
- `tools/` — LLM tool 實作
- `ui/` — Streamlit 頁面；透過 API 與後端溝通，不直接 import core
- `static/` — Dashboard HTML / 前端 JS / i18n locales
- `tests/` — Pytest 測試套件
- `docs/` — 架構文件與專案文件
- `PersonaProbe/` — 獨立子專案（人格採集與分析工具）

---

## `core/` 內部結構

- `core/persona_evolution/` — 人格演化系統（Path D 增量 trait 架構）；詳見 `docs/persona-tree-architecture.md`
- `core/chat_orchestrator/` — 雙層 Agent 對話編排（package）
  - `dataclasses.py` — `RouterResult` / `ToolContext` / `PersonaResult`
  - `router_agent.py` — Module A：意圖路由（含 `DIRECT_CHAT_SCHEMA` dummy tool）
  - `middleware.py`   — Module B：工具並行執行 + 過渡語音推播
  - `persona_agent.py`— Module C：角色渲染（結構化 JSON 回覆）
  - `coordinator.py`  — `run_dual_layer_orchestration` 頂層協調（兩條分支平行）
  - `__init__.py`     — package 識別檔，docstring 內有直接 import 範例
- `core/deployment_config.py` — 三維度隔離入口（`resolve_context`）；新增 channel 需在此登記
- `core/storage_manager.py` — 單檔但有 SECTION 標記分區
  - 分區：檔案 I/O / 模型 DB / Memory Blocks / Core Memory / Profile / Topic Cache / Conversation / 訊息統計
- `core/core_memory.py` — 同上
  - 分區：Embedding 工具 / 查詢擴展 / Memory Block 寫入 / 叢集融合 / 三軌檢索 / Profile
- `core/llm_gateway.py` — LLM routing；分派 9 種 task type 到可設定 provider（Ollama/OpenAI/OpenRouter/llama.cpp），設定存於 `user_prefs.json`
  - 若任務帶 `response_format` 但模型回傳純文字（無 `{`），`LLMRouter.generate()` 會自動以警告 prompt + 降溫重試一次（針對 cloud-proxied 模型忽略 schema 的問題）

---

## `api/` 內部結構

- `api/dependencies.py` — 所有 core singleton 的 DI 入口
- `api/models/` — Pydantic request/response models（**禁止**在 router 檔內定義新 model）
- `api/routers/chat/` — WebSocket / REST 共用實作（package）
  - `timer.py`         — `StepTimer` 計時工具
  - `ws_manager.py`    — `ConnectionManager` WebSocket 連線池 + `ws_manager` singleton
  - `pipeline.py`      — 記憶管線同步/背景執行
  - `orchestration.py` — `_run_chat_orchestration` 單層編排與雙層編排選擇器
- `api/routers/chat_ws.py`  — WebSocket 端點（slim，re-export 內部相容）
- `api/routers/chat_rest.py`— REST `/chat/sync` 與 SSE `/chat/stream-sync` 端點

---

## `PersonaProbe/` 子專案

獨立子專案，詳見 `PersonaProbe/CLAUDE.md` / `PersonaProbe/AGENTS.md`。

- `probe_engine.py` — 純 Python 核心（禁止 import streamlit）
- `app.py` — Streamlit UI (port 8502)
- `server.py` — FastAPI API server (port 8089)
- `llm_client.py` — 自有 LLM 抽象層（`LLMClient(config).chat()`，與主專案 `llm_gateway.py` 各自獨立）

PersonaProbe 不經由主專案的 `StorageManager`，讀取 `conversation.db` 時使用 Python 內建 `sqlite3`。

---

## 模組拆分 + SECTION 標記原則

為了降低修檔時的 context 消耗：

- **高頻修改的大檔** → 拆成 package（如 `chat/`、`chat_orchestrator/`），並在 `__init__.py` re-export 維持向後相容。
- **穩定但仍大的 class 介面檔**（如 `storage_manager.py`、`core_memory.py`）→ 不拆檔，但用 `# ════…` 加 `# SECTION: …` 分區，方便 Grep 定位。

新增方法時請放在語意對應的 SECTION 內；新增 SECTION 請維持與現有相同的視覺樣式。
