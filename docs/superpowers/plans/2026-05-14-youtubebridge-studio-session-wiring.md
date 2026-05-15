# YouTubeBridge Studio Session Wiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the new Studio page to the existing YouTubeBridge Live Session lifecycle without connecting OBS auto-detection, Summary archival, or TTS/Presentation runtime output.

**Architecture:** Studio remains a standalone frontend surface that uses its own API helpers and does not import legacy control UI modules. It reuses existing backend session, episode-plan, director, chat-preview, recent-events, and SSE routes instead of adding a second start endpoint.

**Tech Stack:** FastAPI routes already exposed by YouTubeBridge, SQLite-backed BridgeStorage, vanilla HTML/CSS/JS Studio frontend, pytest source and route contract tests, Browser QA on `http://127.0.0.1:8091/studio/`.

---

## Summary

第二階段接「直播 Session lifecycle」：`/studio/` 載入真實 LiveEpisodePlan 清單，按下開始後呼叫既有 `POST /sessions/current/start` 建立 single current session，啟動導播 kickoff，中央對話區讀後端 chat preview / SSE 事件。

本階段不接 OBS 自動抓取 `video_id`、不接 TTS/Presentation 實際輸出、不接自動留言注入、不接 Summary/Shared Memory 收尾流程。

## Key Changes

- Studio 企劃下拉改用 `POST /episode-plans/sync-local?max_files=200` + `GET /episode-plans?limit=100`，value 使用 `plan_id`，無企劃時禁用開始直播。
- 「開始直播」呼叫 `POST /sessions/current/start`，沿用既有 single current session replacement 與先驗證後 archival 的安全語意。
- Start payload 由 Studio live defaults 映射到 `LiveSessionConfig`，其中 `presentation_enabled` 與 `tts_enabled` 固定為 `false`，`character_ids` 固定為 `[]` 交由後端依 LiveEpisodePlan 綁定。
- Source handling 採手動/測試模式：手動欄位空白就是 test session；有 YouTube URL 或 `video_id` 就交後端解析並解析 `live_chat_id`。
- Start 成功後呼叫 `POST /sessions/{session_id}/director/start`，body 為 `{ idle_seconds: 60, guidance: "", kickoff: true }`；導播失敗只顯示警告，不 rollback session。
- 中央對話改讀 `GET /sessions/{session_id}/chat-preview?limit=120`，並用 `EventSource("/sessions/{session_id}/events")` 監聽 `chat_message`、`status`、`youtube_live_event`。
- 停止按鈕只呼叫 `POST /sessions/{session_id}/stop`，不呼叫 `/finalize`，不觸發 Summary 或 Shared Memory。

## Test Plan

- Source tests：
  - Studio JS 包含 `/episode-plans/sync-local`、`/episode-plans`、`/sessions/current/start`、`/director/start`、`/chat-preview`、`EventSource`。
  - Studio JS 不再包含 `simulateSourceDetection()`、`completeSourceDetection()`、`makeSourceToken()`。
  - Start payload 正確映射 live defaults，且固定 `presentation_enabled: false`、`tts_enabled: false`。
  - Stop 使用 `/sessions/${sessionId}/stop`，不出現 `/finalize`。
  - 中央對話維持 newest-first，直播事件顯示受 `showLiveEventsEnabled` 控制。
- Backend regression：
  - `start_current_session` 既有 archival safety tests 保留。
  - Route split test 確認 episode-plan sync/list 與 current start route 仍註冊。
- 回歸命令：
  - `python -m pytest YouTubeBridge/tests/test_studio_ui.py YouTubeBridge/tests/test_studio_settings_api.py YouTubeBridge/tests/test_server_route_split.py YouTubeBridge/tests/test_server_auth.py::test_start_current_session_archives_existing_session_and_writes_memory YouTubeBridge/tests/test_server_auth.py::test_start_current_session_validates_new_live_before_archiving_existing YouTubeBridge/tests/test_server_auth.py::test_start_current_session_never_reuses_client_memoria_session_id -q`
  - `python -m pytest YouTubeBridge/tests/test_bridge_engine_lifecycle.py YouTubeBridge/tests/test_bridge_engine_director.py -q`
  - `git diff --check`
- Browser QA：
  - 開啟 `http://127.0.0.1:8091/studio/`。
  - 確認企劃下拉由後端 LiveEpisodePlan 載入。
  - 手動來源留空，按開始直播，確認狀態變成 test mode / 直播中。
  - 中央對話區由後端 chat preview / SSE 更新，不再只靠 mock 訊息。
  - 按停止，確認狀態變成已停止，沒有 Summary/Shared Memory 相關動作。
  - reload 後確認仍能讀到最近 session 狀態。
  - console 無 relevant error/warn。

## Assumptions

- 第二階段採用既有 `/sessions/current/start` replacement 語意。
- OBS 自動抓 `video_id` 留到後續階段。
- 停止只做 `/stop`，正式 `/finalize` 留到 Summary/收尾階段。
- `post_plan_free_talk_enabled` 只繼續保存設定，不改 LiveEpisodePlan runtime。
- 自動留言測試、留言注入、Summary pipeline、Presentation Queue、GPT-SoVITS TTS runtime 接線都留到後續階段。
