# CLAUDE.md

## Language
Always respond in Traditional Chinese (zh-TW). Code comments and documents also use zh-TW unless asked otherwise.

## Project Overview
MemoriaCore — AI contextual memory engine。BGE-M3 ONNX embeddings + personality evolution system。
Backend: FastAPI (port 8088)；Frontend: Streamlit (port 8501)、Telegram bot、Unity WebSocket client。

子專案 **PersonaProbe**（`PersonaProbe/`）— 人格採集與分析工具，Streamlit UI (port 8502) + FastAPI API server (port 8089)。

## Architecture
- `core/` — 記憶、LLM 路由、人格、儲存引擎
- `api/` — FastAPI routers，singleton DI 由 `api/dependencies.py` 管理
- `tools/` — LLM tool 實作
- `ui/` — Streamlit 頁面；透過 API 與後端溝通，不直接 import core
- `tests/` — Pytest 測試套件
- `PersonaProbe/` — 獨立子專案，詳見 `PersonaProbe/CLAUDE.md`
  - `probe_engine.py` — 純 Python 核心（禁止 import streamlit）
  - `app.py` — Streamlit UI；`server.py` — FastAPI (port 8089)
  - `llm_client.py` — 自有 LLM 抽象層（`LLMClient(config).chat()`，與主專案 `llm_gateway.py` 各自獨立）

LLM routing (`core/llm_gateway.py`) 分派 9 種 task type 到可設定 provider（Ollama/OpenAI/OpenRouter/llama.cpp），設定存於 `user_prefs.json`。

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
每個 tool 需提供 `*_SCHEMA`（`{"type": "function", ...}`）與執行函式（回傳 `str`）；新增後在 `chat_orchestrator.py` 的 tool 清單登記。

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
涉及檔案：`api/routers/chat_ws.py`、`core/chat_orchestrator.py`（兩處都要確認）。

**Streamlit UI 與 dashboard.html 必須同步修改**
對話介面、debug 資訊、session 管理、路由設定有兩個並行前端：
- `ui/chat.py`（Streamlit，port 8501）
- `static/dashboard.html`（純 HTML，嵌入同一 FastAPI）

凡是涉及以下項目的改動，兩個前端都必須同步更新：
- 對話送出 / SSE 事件處理（`result`、`tool_status`、`thinking_speech` 等）
- Session 建立 / 還原 / 切換邏輯
- Debug panel 顯示的欄位（`retrieval_context`、`context_messages_count` 等）
- Routing config 的任務清單（`TASK_INFOS` 在 dashboard.html 是硬編碼）
- `RetrievalContextDTO` 新增欄位後，兩邊的渲染邏輯都要更新

**Tests**
使用 `tmp_path` 隔離 SQLite DB，禁止讀寫根目錄 `.db` 檔；不 mock `StorageManager` async lock。