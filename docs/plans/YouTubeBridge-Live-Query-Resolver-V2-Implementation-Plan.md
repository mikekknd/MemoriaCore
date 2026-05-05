# YouTubeBridge Live Query Resolver V2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 將觀眾查詢判定改成 LLM schema contract，並讓外部搜尋 fallback 以背景 worker 執行，不阻塞直播注入主流程。

**Architecture:** SafetyLLM 仍是第一道門；Live Query Resolver 只讀安全後留言，透過 `youtube_live_audience_query_classifier_prompt` 產生結構化查詢意圖。本地 FactCards 不足時，不同步等待 Research Gate，而是排入背景 worker，保留原留言未注入，待搜尋整理成 Fact Card 後由下一輪注入把留言與資料一起交給角色。

**Tech Stack:** Python 3.12/3.13, FastAPI background manager, SQLite via `BridgeStorage`, pytest under `.pyTestTemp/`.

---

## Files

- Modify: `prompts_default.json`
  - 新增 `youtube_live_audience_query_classifier_prompt`，要求輸出 JSON schema 欄位：`is_factual_question`, `needs_external_search`, `safe_search_allowed`, `sanitized_query`, `topic_scope`, `risk_label`, `reason`。
- Modify: `YouTubeBridge/bridge_engine.py`
  - 新增 `AUDIENCE_QUERY_CLASSIFIER_SCHEMA`。
  - 將 `_audience_query_text_from_events()` 改成呼叫 `_classify_audience_query()`，不再用關鍵字 heuristic。
  - 新增 `_ensure_audience_research_worker()` 和 `_run_audience_research_worker()`。
  - 本地資料不足且 Research Gate 可用時，排 worker 並 raise `ValueError("觀眾查詢資料搜尋中")`，避免該批留言被標記 injected。
  - worker 成功後建立 Research Fact Card；下一輪 `build_external_context()` 會用本地 Topic Pack 召回該卡。
- Modify: `YouTubeBridge/tests/test_bridge_engine.py`
  - 新增 schema 判定測試。
  - 更新 Research fallback 測試為「排 worker、不同步呼叫搜尋、不產生角色 context」。
  - 新增 worker 完成後下一輪可帶 Research Fact Card 的測試。

## Tasks

### Task 1: Prompt/schema contract

- [ ] Write failing test: fake Memoria client returns factual-query JSON for a phrase that old keyword heuristic would miss.
- [ ] Run the targeted test and confirm it fails because `_audience_query_text_from_events()` still uses deterministic markers.
- [ ] Add `AUDIENCE_QUERY_CLASSIFIER_SCHEMA` and `youtube_live_audience_query_classifier_prompt`.
- [ ] Implement `_classify_audience_query()` and route `_audience_query_text_from_events()` through it.
- [ ] Run targeted test and confirm pass.

### Task 2: Background Research worker

- [ ] Write failing test: local FactCards cannot answer, Research Gate is enabled, `_research_request_sync()` must not be called inline.
- [ ] Run the targeted test and confirm it fails because current code calls `_research_request_sync()` inside `build_external_context()`.
- [ ] Add worker scheduling and in-progress metadata.
- [ ] Make `build_external_context()` raise `ValueError("觀眾查詢資料搜尋中")` after scheduling worker so events remain uninjected.
- [ ] Suppress this expected ValueError in auto inject loop logging.
- [ ] Run targeted test and confirm pass.

### Task 3: Completed worker reuse

- [ ] Write failing test: after a Research Fact Card exists and has embedding, the next `build_external_context()` includes it with the original viewer question.
- [ ] Run targeted test and confirm it fails before worker completion path is wired.
- [ ] Ensure worker stores the card through `_research_request_sync()` and the next local retrieval sees it.
- [ ] Run targeted test and confirm pass.

### Task 4: Regression

- [ ] Run:

```powershell
pytest YouTubeBridge\tests\test_bridge_engine.py YouTubeBridge\tests\test_storage.py YouTubeBridge\tests\test_server_auth.py --basetemp=.pyTestTemp\basetemp-live-query-v2 -q
```

- [ ] Run:

```powershell
pytest YouTubeBridge\tests\test_fact_cards.py --basetemp=.pyTestTemp\basetemp-live-query-v2-factcards -q
```

- [ ] Run `git diff --check`.
