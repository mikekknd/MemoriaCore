# Runtime Application Service Module Design

## Purpose

Runtime Application Service 是 V2 的 orchestration layer。它負責把 API command、scheduler tick、YouTube event、manual close request 與 adapter result 串成可恢復的 session workflow：讀取 storage snapshot、呼叫 Runtime Phase 做純決策、依 `next_action` 呼叫 LiveEpisodePlan、Aftertalk、Closing 或 adapter module，並把結果寫回 storage 與 observability。

它存在的目的，是避免 phase decision、storage write、adapter side effect 與 API route 混在一起。Runtime Phase 保持 pure；Application Service 才負責 side effects 的順序與一致性。

## Ownership

Runtime Application Service 擁有：

- V2 session command orchestration。
- snapshot read -> phase decision -> action execution -> persistence -> event publish 的執行順序。
- Runtime Phase `next_action` 到 module call 的 dispatch。
- command idempotency、transition id、correlation id 傳遞。
- manual close、duration tick、polling event、adapter result 同時發生時的優先順序。
- crash/restart 後從 storage snapshot 恢復下一步 action 的規則。

Runtime Application Service 不擁有：

- phase transition policy 本身。
- LiveEpisodePlan cursor 的內部規則。
- Aftertalk cue 內容。
- Closing finalization 的內容生成。
- MemoriaCore/YouTube transport 細節。
- HTTP route、auth、UI rendering、TTS delivery。

## Inputs

必要輸入：

- `RuntimeCommand`：來自 API、scheduler 或 adapter loop 的 typed command。
- `session_id`：目標 V2 session。
- `command_id`：用於 idempotency 的 command identifier。
- `now`：Runtime Phase 與 duration policy 使用的時間。
- `PermissionContext`：由 Security module 提供的權限上下文。

主要 command 類型：

- `CreateSessionCommand`
- `BindPlanCommand`
- `StartSessionCommand`
- `RuntimeTickCommand`
- `HandleYouTubeEventCommand`
- `UpdateAftertalkPolicyCommand`
- `ManualCloseCommand`
- `FinalizeClosingCommand`

## Outputs

- `RuntimeServiceResult`：command 成功、失敗、no-op、或需要 retry 的結果。
- `RuntimeServiceEvent`：可發送給 operator/display/observer stream 的事件。
- `PersistedTransitionRef`：已寫入的 phase transition reference。
- `AdapterDispatchResult`：adapter call 的 normalized result 或 error summary。
- `RecoveryDecision`：crash/restart 或 stale command 後的恢復判斷。

## Dependencies

- Storage：讀取 session snapshot、寫入 transition、event、interaction、finalization。
- Runtime Phase：產生 pure `PhaseTransition`。
- LiveEpisodePlan Runner：產生 planned turn intent 與 completion signal。
- Aftertalk：產生 group chat cue/request 或 stop reason。
- Closing：產生 closing request、finalization result、closing completion status。
- MemoriaCore Adapter：執行 planned show、aftertalk、closing 所需的 chat/group chat request。
- YouTube Adapter：提供 normalized YouTube event 與 stream status。
- Observability：建立 correlation id、transition log、adapter summary、error event。
- Access Control / Security：檢查 command 權限與 secret boundary。

## Out Of Scope

- 直接解析 HTTP request body。
- 直接渲染 operator console 或 chat display。
- 直接操作 YouTube/MemoriaCore SDK。
- 實作具體 `StorageManager`/SQLite schema。
- 修改 Legacy `YouTubeBridge/` runtime。
- 重新定義 phase lifecycle。

## Public Entrypoints

本節描述已存在的 Runtime Application Service public contracts。實作位於 `YouTubeBridgeV2/runtime/application_service.py`，具體 storage、adapter 與 route 仍由後續模組注入或實作。

### `RuntimeApplicationService`

Source:
`YouTubeBridgeV2/runtime/application_service.py::RuntimeApplicationService`

Purpose:
接收 typed command，協調 V2 runtime workflow。

Expected Methods:
- `create_session(command, now)`
- `bind_plan(command, now)`
- `start_session(command, now)`
- `tick_session(command, now)`
- `handle_youtube_event(command, now)`
- `update_aftertalk_policy(command, now)`
- `request_manual_close(command, now)`
- `finalize_closing(command, now)`
- `recover_session(command, now)`

Side Effects:
- 透過 repository 寫入 storage。
- 呼叫 MemoriaCore 或 YouTube adapter。
- 產生 observability/event stream payload。

Wave 2D status:
- `POST /v2/sessions/{session_id}/tick` 已作為 operator-only explicit tick source。
- `RuntimeCommandType.TICK` 由 route 建立並委派 `tick_session(...)`。
- 未啟用 background scheduler；production 未注入 Memoria transport 時仍使用 no-op runners。
- 注入 `MemoriaTransportProtocol` 時，planned show、aftertalk、closing 可經由 `MemoriaPlannedShowRunner`、`MemoriaAftertalkRunner`、`MemoriaClosingRunner` 推進。

Wave 3A status:
- `RuntimeCommandType.HANDLE_YOUTUBE_EVENT` 接受 raw YouTube event payload，會先經 `normalize_youtube_event(...)` 轉為 normalized public/runtime input，再保存事件並交回 tick dispatch。
- command idempotency 會在保存 YouTube event 前檢查，避免同一 `command_id` 重送時重複寫 event。
- 本階段不處理 polling cursor、YouTube API transport、scheduler 或 Super Chat closing handoff。

Wave 3B status:
- `HANDLE_YOUTUBE_EVENT` 會優先使用 command payload 的 `polling_cursor`，否則從 storage 讀取 `youtube_polling_cursor`。
- cursor 會在 event persistence 後 advance 並寫回 session metadata；duplicate event id 只保存 ignored event summary，不 dispatch runner。

Wave 4A scheduler contract:
- `AutomationTickPolicy` 與 `SchedulerTickIntent` 定義 scheduler/tick loop 的 command envelope。
- `build_scheduler_tick_intent(...)` 產生 deterministic `RuntimeCommandType.TICK` command id，讓 scheduler tick 可走既有 command idempotency。
- `dispatch_scheduler_tick(...)` 只做單次 runtime service delegation；background loop、phase automation policy、restart hardening 與 operator pause/resume controls 分別留給 4B/4C/4D。

Wave 4B automation phase advancement:
- `SchedulerSessionRef` / `SchedulerCycleResult` 定義一輪 scheduler cycle 的 explicit session input 與結果。
- `build_scheduler_cycle_intents(...)` / `dispatch_scheduler_cycle(...)` 可對已知 active refs 自動發出 planned_show、aftertalk、closing tick。
- Phase transition、duration policy、aftertalk policy 與 runner side effects 仍由 `RuntimeApplicationService` / Runtime Phase 決定；本階段不負責 durable active-session discovery、process lifecycle 或 pause/resume API。

Wave 4C restart/recovery hardening:
- `RuntimeStoragePort.list_recoverable_sessions(...)` delegates to durable `StorageManager.list_v2_sessions_for_recovery(...)` so restart bootstrap can recover non-ended sessions without importing storage into automation。
- `SchedulerRecoverySessionRef` / `SchedulerRecoveryIntent` / `SchedulerRecoveryCycleResult` define restart recovery command dispatch through `recover_session(...)`。
- Recovery command ids use phase + plan/manual-close/closing state markers instead of timestamps, so same state replays idempotently while changed state can advance to the next recovery command。

Wave 4D operator controls:
- `RuntimeCommandType.UPDATE_AUTOMATION_CONTROL` 與 `RuntimeApplicationService.update_automation_control(...)` 保存 durable `automation_control` metadata，不直接改 phase。
- Automation tick/recovery refs 會讀取 top-level 或 metadata `automation_control.enabled/paused`，disabled/paused sessions 不 dispatch side effects。
- `POST /v2/sessions/{session_id}/automation-control` 是 operator-only safety control；完整 UI 留給 Wave 5。

### `RuntimeCommand`

Source:
`YouTubeBridgeV2/runtime/application_service.py::RuntimeCommand`

Purpose:
所有外部 runtime action 的 typed command envelope。

Required Fields:
- `command_id`
- `session_id`
- `issued_at`
- `permission_context`
- command-specific payload

### `RuntimeServiceResult`

Source:
`YouTubeBridgeV2/runtime/application_service.py::RuntimeServiceResult`

Purpose:
回傳 command 的 stable result shape。

Required Fields:
- `status`
- `session_id`
- `phase`
- `events`
- `errors`
- `correlation_id`

## Orchestration Rules

| Situation | Required Behavior |
| --- | --- |
| API route receives control action | Route validates shape/security, then delegates to service command. |
| Service starts command | Read latest session snapshot before deciding anything. |
| Snapshot is stale after write conflict | Reload snapshot and re-run pure phase decision once. |
| Manual close and planned turn are both ready | Manual close wins; service dispatches closing. |
| Duration reached while adapter call is in flight | Do not cancel hidden transport mid-call; persist adapter result, then next tick enters closing. |
| YouTube live ended event arrives | Persist stream status, then request closing through Runtime Phase path. |
| Adapter returns retryable error | Persist error summary and return retryable result; retry policy lives in service, not adapter. |
| Adapter returns terminal error | Persist error summary and choose next action through module-specific policy. |
| Crash/restart during `closing` | Read storage, resume closing if `closing_completion_status` is incomplete. |
| Duplicate command id | Return previous result or no-op without duplicate side effects. |

## Failure Modes

- missing session：回傳 not found，不建立隱式 session。
- permission mismatch：回傳 forbidden result，不呼叫 runtime modules。
- stale snapshot：reload 一次後仍衝突則回傳 retryable concurrency error。
- partial adapter failure：保存 redacted error summary，不把 raw payload 寫入 public event。
- storage write failure：停止後續 side effects，回傳 terminal service error。
- event publish failure：不回滾已完成 storage transaction，但記錄 observability warning。
- crash/restart：從 storage snapshot 恢復，不依賴 process-local memory。
- duplicate command：不得重複呼叫 adapter 或重複寫 transition。

## Test Strategy

- command delegation tests：API command 進 service 後只經由 service 協調。
- phase dispatch tests：每個 Runtime Phase `next_action` 都映射到正確 module。
- idempotency tests：duplicate command 不重複 side effects。
- concurrency tests：manual close 優先於 planned/aftertalk continuation。
- recovery tests：`closing` 未完成時 restart 後可恢復。
- adapter error tests：retryable/terminal error 都會保存 redacted summary。
- storage failure tests：storage write 失敗時不繼續 adapter side effect。
- visibility tests：service event 不包含 hidden prompt、raw Topic Pack、raw adapter payload。

## Open Questions

- service 是否使用單一 class 或 command handler package，留到 implementation plan 決定。
- transition id 與 correlation id 的生成位置需與 Observability implementation plan 對齊。
- runtime tick 第一版來源已鎖定為 operator API endpoint；scheduler/background worker 屬於後續 wave。
