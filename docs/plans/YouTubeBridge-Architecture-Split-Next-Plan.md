# YouTubeBridge 架構拆分下一階段計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在第一階段 facade 拆分已通過測試後，繼續降低 `server.py`、`storage.py`、`bridge_engine.py` 的單檔負擔，並維持既有 API、DB、測試相容。

**Architecture:** 先拆 HTTP router，因為它最容易用 API 測試鎖住且與 async runtime 耦合較低；再把 `BridgeStorage` 拆成 repository mixin；最後才拆 `YouTubeBridgeManager` 的 async runtime 與 live query resolver。每一階段都保留原入口與 public method facade。

**Tech Stack:** Python 3.12+/3.13、FastAPI、SQLite、pytest、YouTubeBridge 既有純 Python 模組。

---

## 執行前檢查

- [ ] 執行 `git status -sb`，確認既有未提交變更清單。
- [ ] 不 stage 或改動與本階段無關的既有變更，例如 `YouTubeBridge/static/index.html`、`YouTubeBridge/static/ui/`、`prompts_default.json`。
- [ ] 執行 `python -m pytest YouTubeBridge/tests -q`，取得拆分前 baseline。
- [ ] 若 `.pyTestTemp` 發生 Windows ACL/PermissionError，先跑 `scripts/cleanup_pytest_temp.bat`。

## Task 1：拆 `server.py` 的 app state 與 router 註冊

**目的:** 讓 `server.py` 保留 `app` 入口，但不再承載所有 route function。

**Files:**
- Create: `YouTubeBridge/server_state.py`
- Create: `YouTubeBridge/server_routes/__init__.py`
- Modify: `YouTubeBridge/server.py`
- Test: `YouTubeBridge/tests/test_server_route_split.py`

**設計:**
- `server_state.py` 建立 `BridgeAppState` dataclass，包住 `storage`、`manager`、`summary_manager`、`chat_preview_cache`。
- `server.py` 仍建立 singleton state 與 `app`。
- `server_routes/__init__.py` 暴露 `register_routes(app, state)`，初期只搬 UI/static/health/config route。

**驗證:**
- `test_server_route_split.py` 檢查 `/health`、`/ui-config`、`/ui-assets/{asset}` 仍存在。
- `python -m pytest YouTubeBridge/tests/test_server_auth.py YouTubeBridge/tests/test_server_route_split.py -q`

## Task 2：拆 UI / connector / session routes

**目的:** 把最穩定、最接近 CRUD 的 routes 從 `server.py` 搬走。

**Files:**
- Create: `YouTubeBridge/server_routes/ui.py`
- Create: `YouTubeBridge/server_routes/connectors.py`
- Create: `YouTubeBridge/server_routes/sessions.py`
- Modify: `YouTubeBridge/server_routes/__init__.py`
- Modify: `YouTubeBridge/server.py`
- Test: `YouTubeBridge/tests/test_server_route_split.py`

**範圍:**
- UI/static: `/health`、`/ui-config`、`/ui-assets/*`、`/ui`、`/live`、`/live-chat`
- Connector: `/connectors`
- Session 基本 CRUD/lifecycle: `/sessions`、`/sessions/{session_id}`、start/stop/delete/recent/events/interactions/chat-preview

**不搬:**
- topic pack、fact card、research、summary、testing、memoria auth。這些留到 Task 3。

**驗證:**
- 既有 `test_server_auth.py` 必須全通過。
- 新增 route module smoke tests，確認 `server_module.app.routes` 包含原本 path。

## Task 3：拆 topic pack / fact card / summary / memoria routes

**目的:** 完成 server router 層拆分，讓 `server.py` 降到 app factory + state wiring。

**Files:**
- Create: `YouTubeBridge/server_routes/topic_packs.py`
- Create: `YouTubeBridge/server_routes/fact_cards.py`
- Create: `YouTubeBridge/server_routes/summaries.py`
- Create: `YouTubeBridge/server_routes/memoria.py`
- Create: `YouTubeBridge/server_routes/testing.py`
- Modify: `YouTubeBridge/server_routes/__init__.py`
- Modify: `YouTubeBridge/server.py`
- Test: `YouTubeBridge/tests/test_server_route_split.py`

**驗證:**
- `python -m pytest YouTubeBridge/tests/test_server_auth.py YouTubeBridge/tests/test_server_split_modules.py YouTubeBridge/tests/test_server_route_split.py -q`
- `python -m pytest YouTubeBridge/tests -q`

## Task 4：把 `BridgeStorage` 拆成 repository mixin

**目的:** 讓 `storage.py` 從 facade 變成組合入口，把操作分到同領域檔案。

**Files:**
- Create: `YouTubeBridge/storage_repositories/__init__.py`
- Create: `YouTubeBridge/storage_repositories/connectors.py`
- Create: `YouTubeBridge/storage_repositories/sessions.py`
- Create: `YouTubeBridge/storage_repositories/events.py`
- Create: `YouTubeBridge/storage_repositories/topic_packs.py`
- Create: `YouTubeBridge/storage_repositories/interactions.py`
- Create: `YouTubeBridge/storage_repositories/director_state.py`
- Create: `YouTubeBridge/storage_repositories/summaries.py`
- Modify: `YouTubeBridge/storage.py`
- Test: `YouTubeBridge/tests/test_storage_repository_split.py`

**設計:**
- `BridgeStorage` 繼承多個 mixin，例如 `class BridgeStorage(ConnectorRepositoryMixin, SessionRepositoryMixin, ...)`。
- 每個 mixin 假設 `self._lock`、`self._connect()`、`self._json_dump()` 等 facade helper 存在。
- 不改任何 public method 名稱。

**驗證:**
- 每拆一個 mixin 就跑對應 `test_storage.py` 區段。
- 完成後跑 `python -m pytest YouTubeBridge/tests/test_storage.py YouTubeBridge/tests/test_storage_split_modules.py -q`。

## Task 5：拆 `bridge_engine.py` 的 pure helper 與 test event generator

**目的:** 在不碰主要 async runtime 的前提下，先移出純函式/低狀態邏輯。

**Files:**
- Create: `YouTubeBridge/engine_test_events.py`
- Create: `YouTubeBridge/engine_public_events.py`
- Modify: `YouTubeBridge/bridge_engine.py`
- Test: `YouTubeBridge/tests/test_bridge_engine_split_modules.py`

**候選搬移:**
- `_format_test_amount`
- `_variant_test_comment_text`
- `_variant_test_super_chat_text`
- `_generate_test_super_chats`
- `_generate_test_comments`
- `_clean_test_comments`
- `_fallback_test_comments`
- `_public_event`
- `_public_live_event`
- `_visible_event`
- `_event_safe_text`

**驗證:**
- 先加測試確認 `YouTubeBridgeManager._generate_test_super_chats()` facade 與新模組函式結果一致。
- `python -m pytest YouTubeBridge/tests/test_bridge_engine.py YouTubeBridge/tests/test_bridge_engine_split_modules.py -q`

## Task 6：拆 `bridge_engine.py` 的 topic pack service

**目的:** 將 topic pack embedding/search/usage 與 fact card replenishment 從 manager 主檔移出。

**Files:**
- Create: `YouTubeBridge/engine_topic_packs.py`
- Modify: `YouTubeBridge/bridge_engine.py`
- Test: `YouTubeBridge/tests/test_bridge_engine_split_modules.py`

**候選搬移:**
- `_embed_text`
- `_topic_entry_embedding_text`
- `index_topic_pack_entry`
- `rebuild_topic_pack_embeddings`
- `_ensure_session_topic_pack_embeddings`
- `_topic_pack_context_for_query`
- `_topic_pack_entries_for_query`
- `_record_topic_pack_usage`
- `get_topic_pack_usage_status`
- `maybe_replenish_fact_cards`
- `auto_build_topic_pack`
- fact card import/generate helpers

**驗證:**
- `test_fact_cards.py`、`test_bridge_engine.py` 必須全通過。
- 若 live query resolver 邏輯仍在變動，先只搬不被該分支頻繁改動的方法。

## Task 7：拆 director / injection / research runtime

**目的:** 最後處理高風險 async flow，這階段要小批次執行。

**Files:**
- Create: `YouTubeBridge/engine_director.py`
- Create: `YouTubeBridge/engine_injection.py`
- Create: `YouTubeBridge/engine_research.py`
- Modify: `YouTubeBridge/bridge_engine.py`
- Test: `YouTubeBridge/tests/test_bridge_engine.py`

**順序:**
- 先拆 `_director_*` pure decision functions。
- 再拆 `build_external_context()` 與 external context display helpers。
- 最後拆 `_director_loop()`、`_auto_inject_loop()`、audience research worker。

**驗證:**
- 每拆一組跑 `python -m pytest YouTubeBridge/tests/test_bridge_engine.py -q`。
- 最後跑 `python -m pytest YouTubeBridge/tests -q`。

## 完成條件

- [ ] `server.py` 降到 app/state/route registration，路由實作搬到 `server_routes/`。
- [ ] `storage.py` 降到 facade + shared helpers，repository operation 搬到 `storage_repositories/`。
- [ ] `bridge_engine.py` 至少拆出 pure helper、test event、topic pack service，async runtime 留到最後批次。
- [ ] `python -m pytest YouTubeBridge/tests -q` 通過。
- [ ] final 回報列出有無未納入本次工作的既有本地變更。
