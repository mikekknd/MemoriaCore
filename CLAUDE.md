# CLAUDE.md

## Language
Always respond in Traditional Chinese (zh-TW). Code comments and documents also use zh-TW unless asked otherwise.

## Project Overview
MemoriaCore — AI contextual memory engine。BGE-M3 ONNX embeddings + personality evolution system。
Backend: FastAPI (port 8088)；Frontend: Streamlit (port 8501)、Telegram bot、Unity WebSocket client。

子專案 **PersonaProbe**（`PersonaProbe/`）— 人格採集與分析工具，Streamlit UI (port 8502) + FastAPI API server (port 8089)。

## Architecture
頂層目錄（內部 package 切分、SECTION 分區、檔案職責請查 `docs/codebase-structure.md`；高層架構與請求流程查 `docs/架構說明.md`）：

- `core/` — 記憶、LLM 路由（`llm_gateway.py`）、人格、儲存引擎；singleton DI 由 `api/dependencies.py` 管理
- `api/` — FastAPI routers；Pydantic models 統一放 `api/models/`
- `tools/` — LLM tool 實作
- `ui/` — Streamlit 頁面；透過 API 與後端溝通，不直接 import core
- `static/` — Dashboard HTML / 前端 JS / i18n locales
- `tests/` — Pytest 測試套件
- `PersonaProbe/` — 獨立子專案（人格採集與分析工具），詳見 `PersonaProbe/CLAUDE.md`

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
高頻修改的大檔拆成 package，穩定大檔用 `# SECTION: …` 分區。詳細策略與各檔 SECTION 對照表查 `docs/codebase-structure.md`。

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
- Routing config 的任務清單（`static/shared/routing_config.js` 的 `TASK_KEYS` 與 `static/locales/*.json` 的 `routing.tasks.*` 必須同步）
- `RetrievalContextDTO` 新增欄位

**i18n / UI 文字維護**
凡是任務會修改任何 UI 可見文字、文字來源、placeholder、title、toast、confirm、badge、table column、API metadata label/description、Streamlit 文案或 dashboard iframe 文案，先查 `docs/i18n-maintenance-guide.md`。
若要接續多語系 / i18n 工程進度與待辦，再查 `docs/i18n-ready-backlog.md`。`CLAUDE.md` 不重複維護詳細清單，避免內容漂移。

**Tests**
使用 `tmp_path` 隔離 SQLite DB，禁止讀寫根目錄 `.db` 檔；不 mock `StorageManager` async lock。
