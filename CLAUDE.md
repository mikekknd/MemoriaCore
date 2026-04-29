# CLAUDE.md

## Language
Always respond in Traditional Chinese (zh-TW). Code comments and documents also use zh-TW unless asked otherwise.

## Project Overview
MemoriaCore — AI contextual memory engine。BGE-M3 ONNX embeddings + personality evolution system。
Backend: FastAPI (port 8088)；Frontend: Streamlit (port 8501)、Telegram bot、Unity WebSocket client。

子專案 **PersonaProbe**（`PersonaProbe/`）— 人格採集與分析工具，Streamlit UI (port 8502) + FastAPI API server (port 8089)。

## Architecture
- `core/` — 記憶、LLM 路由、人格、儲存引擎
  - `core/persona_evolution/` — 人格演化系統（Path D 增量 trait 架構）；詳見 `docs/persona-tree-architecture.md`
  - `core/chat_orchestrator/` — 雙層 Agent 對話編排（package）
    - `dataclasses.py` — `RouterResult` / `ToolContext` / `PersonaResult`
    - `router_agent.py` — Module A：意圖路由（含 `DIRECT_CHAT_SCHEMA` dummy tool）
    - `middleware.py`   — Module B：工具並行執行 + 過渡語音推播
    - `persona_agent.py`— Module C：角色渲染（結構化 JSON 回覆）
    - `coordinator.py`  — `run_dual_layer_orchestration` 頂層協調（兩條分支平行）
    - `__init__.py`     — package 識別檔，docstring 內有直接 import 範例
  - `core/deployment_config.py` — 三維度隔離入口（`resolve_context`）；新增 channel 需在此登記
  - `core/storage_manager.py` — 單檔但有 SECTION 標記分區（檔案 I/O / 模型 DB / Memory Blocks / Core Memory / Profile / Topic Cache / Conversation / 訊息統計）
  - `core/core_memory.py` — 同上（Embedding 工具 / 查詢擴展 / Memory Block 寫入 / 叢集融合 / 三軌檢索 / Profile）
- `api/` — FastAPI routers，singleton DI 由 `api/dependencies.py` 管理
  - `api/routers/chat/` — WebSocket / REST 共用實作（package）
    - `timer.py`         — `StepTimer` 計時工具
    - `ws_manager.py`    — `ConnectionManager` WebSocket 連線池 + `ws_manager` singleton
    - `pipeline.py`      — 記憶管線同步/背景執行
    - `orchestration.py` — `_run_chat_orchestration` 單層編排與雙層編排選擇器
  - `api/routers/chat_ws.py`  — WebSocket 端點（slim，re-export 內部相容）
  - `api/routers/chat_rest.py`— REST `/chat/sync` 與 SSE `/chat/stream-sync` 端點
- `tools/` — LLM tool 實作
- `ui/` — Streamlit 頁面；透過 API 與後端溝通，不直接 import core
- `tests/` — Pytest 測試套件
- `PersonaProbe/` — 獨立子專案，詳見 `PersonaProbe/CLAUDE.md`
  - `probe_engine.py` — 純 Python 核心（禁止 import streamlit）
  - `app.py` — Streamlit UI；`server.py` — FastAPI (port 8089)
  - `llm_client.py` — 自有 LLM 抽象層（`LLMClient(config).chat()`，與主專案 `llm_gateway.py` 各自獨立）

LLM routing (`core/llm_gateway.py`) 分派 9 種 task type 到可設定 provider（Ollama/OpenAI/OpenRouter/llama.cpp），設定存於 `user_prefs.json`。
若任務帶 `response_format` 但模型回傳純文字（無 `{`），`LLMRouter.generate()` 會自動以警告 prompt + 降溫重試一次（針對 cloud-proxied 模型忽略 schema 的問題）。

## Constraints
- Python 3.12，NumPy < 2.0.0
- Dev 環境：Windows batch scripts；核心 Python 跨平台
- ONNX session 只初始化一次（`get_bge_m3_onnx_instance()`），路徑固定 `StreamingAssets/Models/*.onnx`

## Critical Patterns（根目錄工作時最常需要）

**Singleton DI**
所有 core 元件為 singleton，統一由 `api/dependencies.py` 初始化；router 透過 FastAPI DI 注入，**禁止在 router 或其他地方自行實例化**。

**SQLite Locking**
永遠透過 `StorageManager` 的 async lock 讀寫，**禁止直接使用 aiosqlite / sqlite3**。
例外：`PersonaProbe/` 內讀取 `conversation.db` 時，使用 Python 內建 `sqlite3`（PersonaProbe 不經由 StorageManager）。

**LLM Provider 介面**
`generate_chat(messages, model, temperature, response_format, tools, tool_choice)` → 回傳 `(str, list)`。

**Request/Response Model**
Pydantic model 統一放 `api/models/`，不可在 router 檔內定義新 model。

**Tool 實作**
每個 tool 需提供 `*_SCHEMA`（`{"type": "function", ...}`）與執行函式（回傳 `str`）；新增後在 `core/chat_orchestrator/coordinator.py` 內 Pre-fork 區段的 `tools_list` 登記。

**Prompt Templates**
所有 LLM prompt 模板統一存放於 `prompts_default.json`，禁止在 Python 程式碼中硬寫 prompt 字串。
使用 `get_prompt_manager().get("key").format(...)` 取得並填入佔位符。

**對話紀錄必須納入 LLM 上下文（高頻踩坑）**
`api_messages` 的組裝順序固定為：
```python
api_messages = [{"role": "system", "content": sys_prompt}]
clean_history = [{"role": m["role"], "content": m["content"]} for m in session_messages[-context_window:]]
api_messages.extend(clean_history)   # ← 禁止移除此行
```
每次修改 `sys_prompt` 組裝邏輯（例如增減人格區塊、speech_rules 等）後，**必須確認 `api_messages.extend(clean_history)` 仍然存在且在 sys_prompt 賦值之後**。
此行一旦遺漏，LLM 僅收到系統提示，對話紀錄完全消失，但不會有任何報錯，只會讓模型失憶。
涉及檔案：`api/routers/chat/orchestration.py`（單層）、`core/chat_orchestrator/coordinator.py` 內 `_memory_branch()`（雙層）— 兩處都要確認。

**Router Agent 對話歷史不可重複末筆 user 訊息（高頻踩坑）**
`run_router_agent` 內部會把 `user_prompt` 自行 append 到 messages 末尾。若 `recent_history` 已包含當前 user_prompt（例如在 `add_user_message` 後直接傳 `session_messages[-context_window:]`），就會形成兩筆相同的 user 訊息，導致路由判斷被汙染。
正確寫法（見 `coordinator.py` 的 `_tool_branch`）：
```python
_recent_for_router = session_messages[-context_window:-1]   # 切掉最後一筆
```

**模組拆分 + SECTION 標記原則**
為了降低修檔時的 context 消耗，採以下策略：
- 高頻修改的大檔 → 拆成 package（如 `chat/`、`chat_orchestrator/`），並在 `__init__.py` re-export 維持向後相容。
- 穩定但仍大的 class 介面檔（如 `storage_manager.py`、`core_memory.py`）→ 不拆檔，但用 `# ════…` 加 `# SECTION: …` 分區，方便 Grep 定位。
新增方法時請放在語意對應的 SECTION 內；新增 SECTION 請維持與現有相同的視覺樣式。

**記憶隔離三維度（高頻踩坑）**
任何涉及多使用者、公私可見性、雙 face 人格演化問題，應先查 `docs/memory-isolation-architecture.md`。
涉及背景話題、主動開場、`topic_cache`、proactive topics 時，另需查 `docs/proactive-topic-architecture.md`。
涉及 PersonaProbe 同步、人格式 evolved_prompt、`/system/personality` API 時，另需查 `docs/personality-api-modernization.md`。
涉及 Weather Cache、`weather_city`、prompt 天氣注入時，另需查 `docs/weather-cache-architecture.md`。
核心原則：
- 所有 DB 讀寫**必須** scope 到 `(user_id, character_id, visibility)` 三維度
- `resolve_context(user_id, channel)` 決定 `persona_face` 與 `write_visibility`
- public face **只能**讀到 `visibility='public'` 的記憶；private face 可讀兩者
- 背景蒐集產生的 user-level topic 寫入 `character_id='__global__'`，不要綁定 `active_character_id`
- 人格管理與 PersonaProbe 同步必須明確傳入 `character_id`，不要綁定 `active_character_id`
- 自動 PersonaSync 只掃描 conversation DB 中曾有 assistant 發言的角色作為 dirty 候選；不要用 active/default character 補位
- Weather Cache prompt 注入只服務 SU private face，`weather_city` 視為 SU 的常駐城市；一般天氣查詢走 `get_weather` tool

**Streamlit UI 與 dashboard.html 必須同步修改**
對話介面已迁移至 dashboard.html。其餘頁面（設定、路由等）有兩個並行前端：
- `ui/`（Streamlit，port 8501）
- `static/dashboard.html`（純 HTML，嵌入同一 FastAPI）

⚠️ DEPRECATED: `ui/chat.py` 與 `ui/history.py` 已移除，請勿參考。

凡涉及以下項目的改動，Broader UI 都要同步更新：
- Routing config 的任務清單（`TASK_INFOS` 在 dashboard.html 是硬編碼）
- `RetrievalContextDTO` 新增欄位

**Tests**
使用 `tmp_path` 隔離 SQLite DB，禁止讀寫根目錄 `.db` 檔；不 mock `StorageManager` async lock。
