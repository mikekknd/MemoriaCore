# YouTubeBridgeV2 Goal Roadmap Checklist

本文件是 `/goal` 指令的長期進度索引。它不是單一 wave 的 implementation plan；它只負責讓後續 agent 判斷目前完成度、下一個應處理的 checkpoint、每波完成條件、必要 skill、review 節奏與驗證命令。

## /goal 使用流程

1. 先讀 `CLAUDE.md`、`YouTubeBridgeV2/README.md`、本文件、`docs/architecture-index.md`、`docs/api-reference-index.md`。
2. 執行 `git status -sb`，確認目前 branch、HEAD 與 worktree 狀態。
3. 選取本文件中第一個未完成的最小 checklist item，例如 `2E-A`，不要跨 wave 合併實作。
4. 使用 `superpowers:writing-plans` 產出該 checklist item 的 implementation plan，存到 `YouTubeBridgeV2/docs/implementation-plans/`。
5. 實作階段優先使用 worker subAgent 隔離上下文，並要求 worker 使用 `superpowers:test-driven-development` 依 plan 先寫 red tests，再做最小實作。很小的 docs-only 或單檔修正可由主調度者 inline 完成。
6. 若 checklist item 可切分，優先使用 `superpowers:subagent-driven-development`；若不可切分或範圍很小，才使用 `superpowers:executing-plans` inline 執行。主調度者保留 roadmap 狀態、plan 摘要、review findings、final verification 與 git 發布責任。
7. 完成自然 checkpoint 後，使用 `superpowers:requesting-code-review` 請 reviewer subAgent 審查；reviewer 只負責審查，不負責修改、commit、push 或 PR。
8. 修正 Critical / Important findings，必要時補 fail-first regression tests。
9. Worker 或 verifier subAgent 可先跑局部或預備驗證，但不能取代主調度者的發布前驗證。
10. 使用 `superpowers:verification-before-completion` 時，主調度者必須親自執行 final verification，至少包含 `git status -sb`、`git diff --check` 與該 roadmap item 指定的完整測試命令。
11. 主調度者確認 final verification output 後，才能 commit、push、建立或更新 PR。
12. PR 合併或使用者確認後，再勾選本文件對應 checklist。

## SubAgent 分工規則

- 主調度者：持有 roadmap 狀態、決定下一個 checklist item、審核 implementation plan、整合 review findings、執行 final verification、負責 commit/push/PR。
- Planner subAgent：只在 wave 很大時草擬 implementation plan；主調度者必須審核後才可定稿。
- Worker subAgent：負責單一小 checkpoint 的 TDD 實作與 targeted tests，不決定 roadmap，不跨 checkpoint 擴張 scope。
- Reviewer subAgent：使用 `superpowers:requesting-code-review` 審查已完成的 checkpoint，不修改檔案、不發布 PR。
- Verifier subAgent：可跑預備或耗時驗證，但主調度者發布前仍必須親自跑 final verification。

## 全域完成定義

V2 可視為完成時必須同時滿足：

- 主 app `/v2` 可以建立 session、綁定 plan、讀取狀態、推進 runtime、處理 aftertalk、closing 與 ended。
- 真 MemoriaCore transport、YouTube live event ingestion、runtime automation、operator console、chat display、presentation/TTS 都已接入可測路徑。
- 所有 public entrypoint 都在 `docs/api-reference-index.md` 有 Source。
- `YouTubeBridgeV2/` 不直接 import `sqlite3` 或 `aiosqlite`。
- 不引用 Legacy `YouTubeBridge/` no-plan director 作為 V2 runtime dependency。
- `python -m pytest tests\youtubebridge_v2 -q` 通過。
- `git diff --check` 通過。

## 已完成 Waves

- [x] Wave 1：Fake-backed V2 API/runtime vertical slice。
  - Completion criteria：standalone V2 app、composition、runtime service、query service、in-memory fake storage 與 fake runners 可跑 create/bind/planned/aftertalk/manual close/closing/ended。
  - Verification：`python -m pytest tests\youtubebridge_v2\test_integration_vertical_slice.py -q`。

- [x] Wave 2A：StorageManager durable backend。
  - Completion criteria：V2 durable schema 與 StorageManager methods 已落在 `core/storage/` boundary，runtime command result 可 JSON-safe round-trip。
  - Verification：`python -m pytest tests\youtubebridge_v2\test_storage_manager_durable_backend.py tests\youtubebridge_v2\test_real_storage_integration.py -q`。

- [x] Wave 2B：Production app wiring。
  - Completion criteria：主 app `/v2` 已接真 StorageManager durable composition；未接外部服務時使用 explicit no-op runners。
  - Verification：`python -m pytest tests\youtubebridge_v2\test_main_app_wiring.py -q`。

- [x] Wave 2C：API key permission boundary。
  - Completion criteria：main app `/v2` 已有 prefs-backed API key + operator/display/observer permission matrix；loopback 仍以 operator 通過。
  - Verification：`python -m pytest tests\youtubebridge_v2\test_main_app_security.py -q`。

- [x] Wave 2D：Runtime tick + fake Memoria runner vertical slice。
  - Completion criteria：`POST /v2/sessions/{session_id}/tick` 可推進 runtime；fake transport runner 可驗證 `planned_show -> aftertalk -> closing -> ended`；durable replay 不重複 dispatch。
  - Verification：`python -m pytest tests\youtubebridge_v2\test_runtime_memoria_runners.py tests\youtubebridge_v2\test_runtime_tick_vertical_slice.py -q`。

## 下一個 Wave：2E 真 MemoriaCore Transport

- [ ] Wave 2E：真 MemoriaCore transport。
  - Required skills：`superpowers:writing-plans`、`superpowers:test-driven-development`、`superpowers:requesting-code-review`、`superpowers:verification-before-completion`。
  - Completion criteria：明確設定真 HTTP transport boundary；未設定時 production 仍 no-op；timeout/retry/auth/invalid response 都回 sanitized adapter summary；不把 token/raw payload 寫入 public event。
  - Verification commands：
    - `python -m pytest tests\youtubebridge_v2\test_memoria_adapter.py -q`
    - `python -m pytest tests\youtubebridge_v2\test_runtime_memoria_runners.py -q`
    - `python -m pytest tests\youtubebridge_v2\test_runtime_tick_vertical_slice.py -q`
    - `python -m pytest tests\youtubebridge_v2 -q`
    - `git diff --check`

  - [ ] 2E-A：transport config 與 sync HTTP client boundary。
    - Completion criteria：新增可注入 transport implementation；設定來源不硬寫 secret；測試可替換 fake client。
  - [ ] 2E-B：timeout、retry、auth、sanitized error mapping。
    - Completion criteria：timeout 與 5xx 可 retry；401/403 terminal；錯誤 response 不外洩 URL secret、header、token 或 raw payload。
  - [ ] 2E-C：real MemoriaCore integration test harness。
    - Completion criteria：可用明確 opt-in 設定跑本機 MemoriaCore integration；預設 CI/pytest 不依賴外部服務。
  - [ ] 2E-D：production wiring toggle，未設定時維持 no-op。
    - Completion criteria：只有明確啟用 transport 時才會呼叫 MemoriaCore；未設定或設定錯誤時 `/v2` 不會意外外呼。

## Wave 3：YouTube Adapter Runtime Ingestion

- [ ] Wave 3：YouTube Adapter runtime ingestion。
  - Required skills：`superpowers:writing-plans`、`superpowers:test-driven-development`、`superpowers:requesting-code-review`、`superpowers:verification-before-completion`。
  - Completion criteria：YouTube live chat / Super Chat / stream status 可轉成 V2 normalized event 並餵進 runtime/storage；restart 後 cursor 不遺失；不讓 raw YouTube payload 進入 public API/SSE。
  - Verification commands：
    - `python -m pytest tests\youtubebridge_v2\test_youtube_adapter.py -q`
    - `python -m pytest tests\youtubebridge_v2\test_runtime_application_service.py -q`
    - `python -m pytest tests\youtubebridge_v2\test_storage.py -q`
    - `python -m pytest tests\youtubebridge_v2 -q`
    - `git diff --check`

  - [ ] 3A：live chat event normalization 對接 runtime input。
  - [ ] 3B：polling cursor/storage/restart recovery。
  - [ ] 3C：Super Chat metadata 與 closing handoff。
  - [ ] 3D：YouTube event API 或 scheduler ingestion path。
  - [ ] 3E：fake-backed + boundary tests。

## Wave 4：Runtime Automation

- [ ] Wave 4：Runtime automation。
  - Required skills：`superpowers:writing-plans`、`superpowers:test-driven-development`、`superpowers:requesting-code-review`、`superpowers:verification-before-completion`。
  - Completion criteria：runtime 不再只能靠手動 tick；scheduler/tick loop 可依 phase、plan state、aftertalk policy、duration policy 與 YouTube events 自動推進；pause/resume 能阻止自動 side effects。
  - Verification commands：
    - `python -m pytest tests\youtubebridge_v2\test_runtime_phase.py -q`
    - `python -m pytest tests\youtubebridge_v2\test_runtime_application_service.py -q`
    - `python -m pytest tests\youtubebridge_v2\test_runtime_tick_vertical_slice.py -q`
    - `python -m pytest tests\youtubebridge_v2 -q`
    - `git diff --check`

  - [ ] 4A：scheduler/tick loop contract。
  - [ ] 4B：planned_show/aftertalk/closing 自動推進。
  - [ ] 4C：restart/recovery 與 idempotency hardening。
  - [ ] 4D：operator pause/resume/safety controls。

## Wave 5：Operator Console

- [ ] Wave 5：Operator Console。
  - Required skills：`superpowers:writing-plans`、`superpowers:test-driven-development`、`build-web-apps:frontend-testing-debugging`、`superpowers:requesting-code-review`、`superpowers:verification-before-completion`。
  - Completion criteria：operator console 可操作真 `/v2` durable API，支援 status、create/bind/tick/manual close、aftertalk policy、API key management；UI 不直接改 runtime state。
  - Verification commands：
    - `python -m pytest tests\youtubebridge_v2\test_operator_console_ui.py -q`
    - `python -m pytest tests\youtubebridge_v2\test_main_app_security.py -q`
    - `python -m pytest tests\youtubebridge_v2 -q`
    - Browser verification for `/v2/static/operator-console/index.html`
    - `git diff --check`

  - [ ] 5A：status dashboard 接真 `/v2` durable API。
  - [ ] 5B：create/bind/tick/manual-close controls。
  - [ ] 5C：aftertalk policy controls。
  - [ ] 5D：API key management UI。
  - [ ] 5E：browser/UI regression verification。

## Wave 6：Chat Display / Presentation / TTS

- [ ] Wave 6：Chat Display / Presentation / TTS。
  - Required skills：`superpowers:writing-plans`、`superpowers:test-driven-development`、`build-web-apps:frontend-testing-debugging`、`superpowers:requesting-code-review`、`superpowers:verification-before-completion`。
  - Completion criteria：display stream 可呈現角色發言、系統狀態、Super Chat、closing status；presentation/TTS 消費 display-safe events，支援 queue、ack、timeout；不參與 runtime phase decision。
  - Verification commands：
    - `python -m pytest tests\youtubebridge_v2\test_chat_display_ui.py -q`
    - `python -m pytest tests\youtubebridge_v2\test_presentation_tts.py -q`
    - `python -m pytest tests\youtubebridge_v2 -q`
    - Browser verification for `/v2/static/chat-display/index.html`
    - `git diff --check`

  - [ ] 6A：display event contract hardening。
  - [ ] 6B：chat display stream UI。
  - [ ] 6C：presentation metadata integration。
  - [ ] 6D：TTS queue/ack/timeout integration。
  - [ ] 6E：display + TTS E2E verification。

## Final Hardening

- [ ] Final Hardening。
  - Required skills：`superpowers:requesting-code-review`、`superpowers:verification-before-completion`、`superpowers:finishing-a-development-branch`。
  - Completion criteria：真外部 E2E、啟動/關閉、文件、API reference、Legacy boundary、security redaction 與 PR readiness 全部通過。
  - Verification commands：
    - `python -m pytest tests\youtubebridge_v2 -q`
    - `python -m pytest -q`
    - `git diff --check`
    - 8088 foreground startup smoke test when requested by user
    - thread-aware code review before merge

  - [ ] full external E2E。
  - [ ] startup/shutdown validation。
  - [ ] Legacy boundary audit。
  - [ ] docs/API reference sync。
  - [ ] final code review。
  - [ ] PR/merge readiness。

## 維護規則

- 完成任何 checklist item 後，同步更新本文件、`docs/architecture-index.md` 與 `docs/api-reference-index.md`。
- 每個未完成子項實作前都要先產生自己的 implementation plan，不直接在 roadmap 裡塞施工細節。
- review scope 必須鎖定已實作的 roadmap item；不得把下一個未實作 item 當成 blocking finding。
- subAgent 回報不能作為發布依據；commit、push 或 PR 前，主調度者必須保有 fresh final verification output。
- 若 worktree 有混合變更，只 stage 目前 roadmap item 相關檔案。
- 若發現實際程式狀態與本文件不符，先更新文件與 architecture index，再繼續 `/goal`。
