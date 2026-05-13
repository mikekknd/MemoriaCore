# YouTubeBridgeV2 API Reference Index

本文件定義 V2 的 API 入口索引格式。它的目的不是取代 `rg` 或閱讀原始碼，而是讓人類與 agent 能先找到正確 public entrypoint，再依需求進入實作細節。

## 文件原則

- 只記錄 public/stable entrypoint。
- 不記錄 private helper、臨時 migration helper、測試 fixture helper。
- 程式碼 docstring 是真相來源；本文件是快速索引。
- 每個 entry 都要能回答：它做什麼、怎麼呼叫、回傳什麼、會改變什麼狀態。
- 若 entrypoint 尚未實作，module design 可以先描述預期 entry，但本索引不應假裝已有 source symbol。

## Entry 格式

每個 API entry 使用下列欄位：

```markdown
### `<symbol or endpoint>`

Purpose:
簡述此 entrypoint 的用途。

Params:
- `name: Type` — 參數用途。

Returns:
- `Type` — 回傳內容。

Raises:
- `ErrorType` — 何時發生。

Side Effects:
- 寫入 storage、呼叫外部 API、發送 SSE、觸發 TTS 等。

Since:
- `YouTubeBridgeV2 v0.1`

Stability:
- `stable | provisional | internal`

Source:
- `path/to/file.py::symbol`
```

## Python Docstring 格式

public function/class 應使用同等內容的 docstring。範例：

```python
def advance_phase(session: LiveSessionState, now: datetime) -> PhaseTransition:
    """推進 YouTubeBridgeV2 live session 的 phase 狀態。

    用於判斷 `planned_show` 完成後是否進入 `aftertalk`、`closing`
    或維持目前狀態。此函式只做狀態判斷，不直接寫入 storage。

    Args:
        session: 目前 live session 狀態。
        now: 判斷時間上限與剩餘時間時使用的目前時間。

    Returns:
        PhaseTransition: 下一個 phase、轉換原因與可寫回 metadata 的摘要。

    Raises:
        PhaseContractError: session phase 或必要欄位不符合 V2 contract。

    Side Effects:
        無。

    Since:
        YouTubeBridgeV2 v0.1
    """
```

## Entry 類型

### Runtime Contract

記錄 phase state、transition function、session policy 與 completion result 的 public model 或 function。

### Adapter Contract

記錄 MemoriaCore、YouTube、storage、presentation/TTS 的 public interface。Adapter entry 必須列出外部 side effects。

### HTTP Endpoint

記錄 V2 FastAPI endpoint。Endpoint entry 必須列出 request body、response body、auth requirement 與會觸發的 runtime 行為。

### Security / Auth Contract

記錄 V2 API 存取控制、loopback/API key、MemoriaCore auth delegation、secret/config 與不可信 payload handling 的 public contract。Security entry 必須列出套用範圍、失敗回應與 side effects。

### Event / SSE Payload

記錄後台控制 UI、直播 Chat 顯示介面或外部 observer 會收到的 event payload。Event entry 必須列出 event type、必要欄位、來源 module，以及是否可用於操作者控制或直播畫面呈現。

## Runtime Phase 實作接口

以下 Runtime Phase public contracts 已由 `YouTubeBridgeV2/runtime/phase.py` 實作。

### `LiveSessionPhase`

Purpose:
表示 V2 live session 所在 phase。

Values:
- `planned_show`
- `aftertalk`
- `closing`
- `ended`

Stability:
- `provisional`

Source:
- `YouTubeBridgeV2/runtime/phase.py::LiveSessionPhase`

### `AftertalkPolicy`

Purpose:
控制 LiveEpisodePlan 完成後是否自動進入雜談 phase。

Values:
- `disabled` — 節目完成後直接進入 closing。
- `auto` — 節目完成且仍有剩餘時間時進入 aftertalk。

Stability:
- `provisional`

Source:
- `YouTubeBridgeV2/runtime/phase.py::AftertalkPolicy`

### `DurationPolicy`

Purpose:
定義 Runtime Phase 判斷直播時間上限、剩餘時間與自動收尾時使用的概念 contract。

Fields:
- `planned_duration_seconds` — 直播計畫時間上限；有限正數代表可計算剩餘時間，未設定或非正值代表不以時間自動結束。
- `auto_finalize_on_duration` — 到達時間上限時是否自動進入 closing。
- `aftertalk_requires_remaining_time` — `AftertalkPolicy.auto` 是否需要有限且大於零的剩餘時間才可進入 aftertalk。

Stability:
- `provisional`

Source:
- `YouTubeBridgeV2/runtime/phase.py::DurationPolicy`

### `DurationSummary`

Purpose:
表示 `evaluate_duration(...)` 產生的 duration boundary summary。

Fields:
- `duration_reached: bool` — 剩餘時間小於等於零時為 true；無上限 duration 為 false。
- `remaining_time_seconds: int | None` — 有限 duration 的剩餘秒數；無上限時為 `None`。
- `aftertalk_allowed: bool` — 依 `aftertalk_requires_remaining_time` 與剩餘時間判斷是否可自動進入 aftertalk。

Stability:
- `provisional`

Source:
- `YouTubeBridgeV2/runtime/phase.py::DurationSummary`

### `LiveSessionSnapshot`

Purpose:
提供 `advance_phase(...)` 所需的已整理 session state，不直接讀取 storage 或外部 API。

Fields:
- `current_phase: LiveSessionPhase | str` — 目前 phase。
- `session_started_at: datetime` — session 起始時間。
- `plan_completed: bool` — LiveEpisodePlan 是否已完成。
- `aftertalk_policy: AftertalkPolicy | str` — aftertalk policy。
- `duration_policy: DurationPolicy` — duration policy。
- `manual_close_requested: bool` — 是否要求手動進入 closing。
- `closing_completed: bool` — closing finalization 是否已完成。

Stability:
- `provisional`

Source:
- `YouTubeBridgeV2/runtime/phase.py::LiveSessionSnapshot`

### `PhaseTransition`

Purpose:
表示 Runtime Phase 對目前 session snapshot 做出的 phase decision。它描述下一步應該做什麼，但不直接執行 storage write、adapter call 或 UI event。

Fields:
- `current_phase: LiveSessionPhase` — 決策前的 phase。
- `next_phase: LiveSessionPhase` — 決策後的 phase。
- `changed: bool` — phase 是否改變。
- `reason: PhaseTransitionReason` — transition reason。
- `metadata: Mapping[str, Any]` — 可寫入 transition log 的精簡摘要，不包含 raw prompt、raw Topic Pack 或 hidden context。
- `next_action: str` — 呼叫端下一步高層動作，例如 `run_planned_show`、`start_aftertalk`、`continue_aftertalk`、`start_closing`、`mark_ended`、`wait`。

Stability:
- `provisional`

Source:
- `YouTubeBridgeV2/runtime/phase.py::PhaseTransition`

### `PhaseTransitionReason`

Purpose:
表示 Runtime Phase 為何維持或改變 phase。

Values:
- `plan_completed` — LiveEpisodePlan 已完成，且依 policy 應離開 `planned_show`。
- `aftertalk_enabled` — LiveEpisodePlan 已完成，`AftertalkPolicy.auto` 可進入 aftertalk。
- `duration_reached` — duration 已到達時間上限，且 policy 要求自動收尾。
- `manual_close` — 操作者或外部控制 API 要求進入 closing。
- `closing_completed` — closing finalization 已完成，可進入 ended。
- `invalid_state_recovery` — session phase 或必要欄位不符合 contract，採保守收尾。
- `no_change` — 目前 snapshot 不需要 transition。

Stability:
- `provisional`

Source:
- `YouTubeBridgeV2/runtime/phase.py::PhaseTransitionReason`

### `evaluate_duration(session_started_at, now, duration_policy)`

Purpose:
用 `DurationPolicy` 判斷 session 是否到達時間上限、剩餘秒數，以及 aftertalk 是否可自動開始。

Params:
- `session_started_at: datetime` — session 起始時間。
- `now: datetime` — 判斷 duration 時使用的目前時間。
- `duration_policy: DurationPolicy` — 時間上限、自動收尾與 aftertalk 剩餘時間策略。

Returns:
- `DurationSummary` — duration boundary summary。

Raises:
- 無。

Side Effects:
- 無。

Since:
- `YouTubeBridgeV2 v0.1`

Stability:
- `provisional`

Source:
- `YouTubeBridgeV2/runtime/phase.py::evaluate_duration`

### `advance_phase(session_snapshot, now)`

Purpose:
根據 session snapshot 與目前時間，回傳下一個 phase decision 與呼叫端應執行的高層 action。

Params:
- `session_snapshot: LiveSessionSnapshot` — 已整理好的 V2 session phase/policy/completion state。
- `now: datetime` — 判斷時間上限與 transition 時使用的目前時間。

Returns:
- `PhaseTransition` — 下一個 phase、轉換原因、metadata summary 與 next action。

Raises:
- 無；未知 phase 會回傳 `invalid_state_recovery` 並保守導向 `closing`。

Side Effects:
- 無。

Since:
- `YouTubeBridgeV2 v0.1`

Stability:
- `provisional`

Source:
- `YouTubeBridgeV2/runtime/phase.py::advance_phase`

## Module Concept Contracts

以下列出 V2 模組層 contract。已實作的條目會附 `Source`；尚未實作的條目保留 Concepts，等 runtime code、API route、UI file 或 adapter implementation 實際存在後再補 `Source`。

### App Factory / Composition

Purpose:
提供 V2 root wiring，將 runtime service、query service、storage port 與 FastAPI dependency overrides 組合起來。

Concepts:
- `create_v2_app`
- `V2AppConfigurationError`
- `create_v2_composition`
- `V2RuntimeComposition`
- `V2CompositionConfigurationError`
- `create_production_v2_composition`
- `load_production_memoria_transport`

Stability:
- `provisional`

Source:
- `YouTubeBridgeV2/app.py::create_v2_app`
- `YouTubeBridgeV2/app.py::V2AppConfigurationError`
- `YouTubeBridgeV2/composition.py::create_v2_composition`
- `YouTubeBridgeV2/composition.py::V2RuntimeComposition`
- `YouTubeBridgeV2/composition.py::V2CompositionConfigurationError`
- `YouTubeBridgeV2/production.py::create_production_v2_composition`
- `YouTubeBridgeV2/production.py::load_production_memoria_transport`

### Runtime Application Service

Purpose:
協調 V2 session command workflow，負責 snapshot read、phase decision dispatch、storage write、adapter call、event publish、idempotency 與 recovery。
Wave 3A：`HANDLE_YOUTUBE_EVENT` command payload 會先經 YouTube adapter normalization，保存 normalized public/display event 後再交回 tick dispatch。
Wave 4A：scheduler tick contract 可產生 deterministic `RuntimeCommandType.TICK` command，並以單次 dispatch helper 委派 runtime service。
Wave 4B：scheduler cycle contract 可對 explicit session refs 自動 dispatch planned_show、aftertalk、closing tick，phase decision 仍由 runtime service 決定。
Wave 4C：scheduler recovery cycle 使用 `RuntimeCommandType.RECOVER` 與 state-marker command id，讓 restart recovery 可 idempotent replay。
Wave 4D：operator automation control 可 durable 設定 enabled/paused，automation tick/recovery cycles 會尊重控制狀態。

Concepts:
- `RuntimeApplicationService`
- `RuntimeCommand`
- `RuntimeCommandType`
- `RuntimeCommandType.HANDLE_YOUTUBE_EVENT`
- `RuntimeCommandType.UPDATE_AUTOMATION_CONTROL`
- `RuntimeApplicationService.update_automation_control`
- `AutomationTickPolicy`
- `SchedulerRecoverySessionRef`
- `SchedulerRecoveryIntent`
- `SchedulerRecoveryCycleResult`
- `SchedulerSessionRef`
- `SchedulerCycleResult`
- `SchedulerTickIntent`
- `build_scheduler_recovery_intents`
- `build_scheduler_cycle_intents`
- `build_scheduler_tick_intent`
- `dispatch_scheduler_recovery_cycle`
- `dispatch_scheduler_recovery`
- `dispatch_scheduler_cycle`
- `dispatch_scheduler_tick`
- `RuntimeServiceResult`
- `RuntimeServiceEvent`
- `PersistedTransitionRef`
- `AdapterDispatchResult`
- `RecoveryDecision`
- `NoopPlannedShowRunner`
- `NoopAftertalkRunner`
- `NoopClosingRunner`
- `MemoriaTransportProtocol`
- `MemoriaPlannedShowRunner`
- `MemoriaAftertalkRunner`
- `MemoriaClosingRunner`

Stability:
- `provisional`

Source:
- `YouTubeBridgeV2/runtime/application_service.py::RuntimeApplicationService`
- `YouTubeBridgeV2/runtime/application_service.py::RuntimeCommand`
- `YouTubeBridgeV2/runtime/application_service.py::RuntimeCommandType`
- `YouTubeBridgeV2/runtime/application_service.py::RuntimeApplicationService.update_automation_control`
- `YouTubeBridgeV2/runtime/automation.py::AutomationTickPolicy`
- `YouTubeBridgeV2/runtime/automation.py::SchedulerRecoverySessionRef`
- `YouTubeBridgeV2/runtime/automation.py::SchedulerRecoveryIntent`
- `YouTubeBridgeV2/runtime/automation.py::SchedulerRecoveryCycleResult`
- `YouTubeBridgeV2/runtime/automation.py::SchedulerSessionRef`
- `YouTubeBridgeV2/runtime/automation.py::SchedulerCycleResult`
- `YouTubeBridgeV2/runtime/automation.py::SchedulerTickIntent`
- `YouTubeBridgeV2/runtime/automation.py::build_scheduler_recovery_intents`
- `YouTubeBridgeV2/runtime/automation.py::build_scheduler_cycle_intents`
- `YouTubeBridgeV2/runtime/automation.py::build_scheduler_tick_intent`
- `YouTubeBridgeV2/runtime/automation.py::dispatch_scheduler_recovery_cycle`
- `YouTubeBridgeV2/runtime/automation.py::dispatch_scheduler_recovery`
- `YouTubeBridgeV2/runtime/automation.py::dispatch_scheduler_cycle`
- `YouTubeBridgeV2/runtime/automation.py::dispatch_scheduler_tick`
- `YouTubeBridgeV2/runtime/application_service.py::RuntimeServiceResult`
- `YouTubeBridgeV2/runtime/application_service.py::RuntimeServiceEvent`
- `YouTubeBridgeV2/runtime/application_service.py::PersistedTransitionRef`
- `YouTubeBridgeV2/runtime/application_service.py::AdapterDispatchResult`
- `YouTubeBridgeV2/runtime/application_service.py::RecoveryDecision`
- `YouTubeBridgeV2/runtime/noop_runners.py::NoopPlannedShowRunner`
- `YouTubeBridgeV2/runtime/noop_runners.py::NoopAftertalkRunner`
- `YouTubeBridgeV2/runtime/noop_runners.py::NoopClosingRunner`
- `YouTubeBridgeV2/runtime/memoria_runners.py::MemoriaTransportProtocol`
- `YouTubeBridgeV2/runtime/memoria_runners.py::MemoriaPlannedShowRunner`
- `YouTubeBridgeV2/runtime/memoria_runners.py::MemoriaAftertalkRunner`
- `YouTubeBridgeV2/runtime/memoria_runners.py::MemoriaClosingRunner`

### LiveEpisodePlan Runner

Purpose:
將匯入的節目企劃推進為 planned show turn intent 與 completion signal。

Concepts:
- `LiveEpisodePlanContract`
- `LiveEpisodePlanState`
- `PlannedTurnIntent`
- `PlanExecutionStatus`
- `PlannedTurnResult`
- `PlanCompletionSignal`
- `validate_episode_plan_contract`
- `next_planned_turn`
- `record_planned_turn_result`

Stability:
- `provisional`

Source:
- `YouTubeBridgeV2/live_episode_plan/runner.py::LiveEpisodePlanContract`
- `YouTubeBridgeV2/live_episode_plan/runner.py::LiveEpisodePlanState`
- `YouTubeBridgeV2/live_episode_plan/runner.py::PlannedTurnIntent`
- `YouTubeBridgeV2/live_episode_plan/runner.py::PlanExecutionStatus`
- `YouTubeBridgeV2/live_episode_plan/runner.py::PlannedTurnResult`
- `YouTubeBridgeV2/live_episode_plan/runner.py::PlanCompletionSignal`
- `YouTubeBridgeV2/live_episode_plan/runner.py::validate_episode_plan_contract`
- `YouTubeBridgeV2/live_episode_plan/runner.py::next_planned_turn`
- `YouTubeBridgeV2/live_episode_plan/runner.py::record_planned_turn_result`

### Aftertalk

Purpose:
在正式節目完成後建立雜談 cue 與 group chat handoff intent。

Concepts:
- `AftertalkCue`
- `AftertalkTurnRequest`
- `AftertalkStopReason`
- `AftertalkSessionSummary`
- `build_aftertalk_turn_request`
- `summarize_aftertalk_result`

Stability:
- `provisional`

Source:
- `YouTubeBridgeV2/runtime/aftertalk.py::AftertalkCue`
- `YouTubeBridgeV2/runtime/aftertalk.py::AftertalkTurnRequest`
- `YouTubeBridgeV2/runtime/aftertalk.py::AftertalkStopReason`
- `YouTubeBridgeV2/runtime/aftertalk.py::AftertalkSessionSummary`
- `YouTubeBridgeV2/runtime/aftertalk.py::build_aftertalk_turn_request`
- `YouTubeBridgeV2/runtime/aftertalk.py::summarize_aftertalk_result`

### LiveEpisodePlan State Metadata

Purpose:
保存 runtime tick 可重啟的 LiveEpisodePlan cursor/state。此 contract 由 `RuntimeStoragePort.bind_plan(...)` 寫入 session metadata，runner 只讀取 sanitized contract，不讀 raw Topic Pack 或 hidden prompt。

Fields:
- `contract` — `validate_episode_plan_contract(...)` 產生的 sanitized contract summary。
- `cursor` — 下一個 planned turn index。
- `completed_turn_ids` — 已完成 turn id list。
- `last_memoria_session_id` — 上次 MemoriaCore 回傳的 session id，未建立時為 `null`。

Stability:
- `provisional`

Source:
- `YouTubeBridgeV2/storage/runtime_store.py::RuntimeStoragePort.bind_plan`
- `YouTubeBridgeV2/runtime/memoria_runners.py::MemoriaPlannedShowRunner`

### MemoriaCore Adapter

Purpose:
將 V2 planned show / aftertalk / closing intent 轉成 MemoriaCore `/api/v1/chat/sync` request envelope，並正規化回覆與錯誤。
HTTP transport config/client 條目定義真同步 transport 邊界，但 production
`/v2` 只有在 prefs 明確啟用且 config valid 時才會建立真 transport；未設定、
未啟用或設定錯誤時維持 no-op，不意外外呼。

Concepts:
- `MemoriaRequestPayload`
- `NormalizedMemoriaResponse`
- `MemoriaAdapterError`
- `MemoriaCorrelationMetadata`
- `build_memoria_request`
- `normalize_memoria_response`
- `classify_memoria_error`
- `MEMORIA_TRANSPORT_PREFS_KEY`
- `MemoriaHttpConfigError`
- `MemoriaHttpTransportConfig`
- `MemoriaHttpTransportError`
- `SyncJsonHttpClientProtocol`
- `UrllibSyncJsonHttpClient`
- `MemoriaSyncHttpTransport`
- `parse_memoria_http_transport_config`
- `load_memoria_http_transport_config`

Stability:
- `provisional`

Source:
- `YouTubeBridgeV2/adapters/memoria.py::MemoriaRequestPayload`
- `YouTubeBridgeV2/adapters/memoria.py::NormalizedMemoriaResponse`
- `YouTubeBridgeV2/adapters/memoria.py::MemoriaAdapterError`
- `YouTubeBridgeV2/adapters/memoria.py::MemoriaCorrelationMetadata`
- `YouTubeBridgeV2/adapters/memoria.py::build_memoria_request`
- `YouTubeBridgeV2/adapters/memoria.py::normalize_memoria_response`
- `YouTubeBridgeV2/adapters/memoria.py::classify_memoria_error`
- `YouTubeBridgeV2/adapters/memoria_http.py::MEMORIA_TRANSPORT_PREFS_KEY`
- `YouTubeBridgeV2/adapters/memoria_http.py::MemoriaHttpConfigError`
- `YouTubeBridgeV2/adapters/memoria_http.py::MemoriaHttpTransportConfig`
- `YouTubeBridgeV2/adapters/memoria_http.py::MemoriaHttpTransportError`
- `YouTubeBridgeV2/adapters/memoria_http.py::SyncJsonHttpClientProtocol`
- `YouTubeBridgeV2/adapters/memoria_http.py::UrllibSyncJsonHttpClient`
- `YouTubeBridgeV2/adapters/memoria_http.py::MemoriaSyncHttpTransport`
- `YouTubeBridgeV2/adapters/memoria_http.py::parse_memoria_http_transport_config`
- `YouTubeBridgeV2/adapters/memoria_http.py::load_memoria_http_transport_config`

### Closing

Purpose:
定義 `closing` phase 內 final message、Super Chat acknowledgement、finalization result 與 `closing_completion_status` contract。
Wave 3C：`youtube_super_chat` live event 的 sanitized public metadata 可由 closing runner 轉成 `ClosingSuperChatAction`，並交給 Memoria closing external context。

Concepts:
- `ClosingStartContext`
- `ClosingReason`
- `ClosingPolicy`
- `ClosingRequest`
- `ClosingSuperChatAction`
- `ClosingFinalizationResult`
- `ClosingCompletionStatus`
- `ClosingDisplayEvent`
- `build_closing_request`
- `finalize_closing`

Stability:
- `provisional`

Source:
- `YouTubeBridgeV2/runtime/closing.py::ClosingStartContext`
- `YouTubeBridgeV2/runtime/closing.py::ClosingReason`
- `YouTubeBridgeV2/runtime/closing.py::ClosingPolicy`
- `YouTubeBridgeV2/runtime/closing.py::ClosingRequest`
- `YouTubeBridgeV2/runtime/closing.py::ClosingSuperChatAction`
- `YouTubeBridgeV2/runtime/closing.py::ClosingFinalizationResult`
- `YouTubeBridgeV2/runtime/closing.py::ClosingCompletionStatus`
- `YouTubeBridgeV2/runtime/closing.py::ClosingDisplayEvent`
- `YouTubeBridgeV2/runtime/closing.py::build_closing_request`
- `YouTubeBridgeV2/runtime/closing.py::finalize_closing`

### Storage

Purpose:
提供 V2 session、phase transition、event、interaction、finalization 與 runtime command idempotency 的 repository/durable storage contract。

Concepts:
- `SessionRepository`
- `PhaseTransitionRepository`
- `EventRepository`
- `InteractionRepository`
- `FinalizationRepository`
- `StorageManagerBackedRepository`
- `read_live_session_snapshot`
- `append_phase_transition`
- `append_live_event`
- `append_interaction`
- `StorageBackendNotConfigured`
- `StorageRecordNotFound`
- `StorageContractError`
- `RuntimeStoragePort`
- `live_episode_plan_state`
- `youtube_polling_cursor`
- `RuntimeStoragePort.save_youtube_polling_cursor(session_id, cursor, now)`
- `RuntimeStoragePort.load_youtube_polling_cursor(session_id)`
- `RuntimeStoragePort.list_recoverable_sessions(limit=100)`
- `RuntimeStorageContractError`
- `YouTubeBridgeV2RepositoryMixin`
- `StorageManager(..., youtube_bridge_v2_db_path=None)`
- `create_v2_session(record)`
- `get_v2_session(session_id)`
- `update_v2_session(session_id, patch)`
- `list_v2_sessions_for_recovery(limit=100)`
- `get_v2_phase_transition(transition_id)`
- `append_v2_phase_transition(session_id, record)`
- `append_v2_live_event(session_id, record)`
- `list_v2_live_events(session_id, limit=100)`
- `append_v2_interaction(session_id, record)`
- `append_v2_finalization(session_id, record)`
- `get_v2_command_result(command_id)`
- `save_v2_command_result(command_id, result)`

Stability:
- `provisional`

Implementation status:
- Durable StorageManager backend exists in `core/storage/youtube_bridge_v2.py`.
  The repository contracts still require an explicitly injected
  `StorageManager`-like V2 backend; the default helper path intentionally raises
  `StorageBackendNotConfigured` when no backend is configured.
- `StorageManagerBackedRepository` is an aggregate repository facade, not the
  `RuntimeApplicationService` storage adapter contract.
- `RuntimeStoragePort` is the application-service storage port. It maps runtime
  commands to the injected StorageManager-like backend, handles command result
  JSON-safe persistence/rehydration, and still does not import or own SQLite.
- Wave 3B：YouTube polling cursor 以 `youtube_polling_cursor` 存在 session
  metadata，讓 restart 後可恢復 `next_page_token`、polling interval 與
  seen event ids。
- Wave 4C：`RuntimeStoragePort.list_recoverable_sessions(...)` 委派 durable
  `list_v2_sessions_for_recovery(...)`，供 restart recovery bootstrap 取得
  non-ended session records。
- SQLite access for V2 durable storage is allowed only through
  `core/storage/youtube_bridge_v2.py` and `core/storage_manager.py`.

Source:
- `YouTubeBridgeV2/storage/repositories.py::SessionRepository`
- `YouTubeBridgeV2/storage/repositories.py::PhaseTransitionRepository`
- `YouTubeBridgeV2/storage/repositories.py::EventRepository`
- `YouTubeBridgeV2/storage/repositories.py::InteractionRepository`
- `YouTubeBridgeV2/storage/repositories.py::FinalizationRepository`
- `YouTubeBridgeV2/storage/repositories.py::StorageManagerBackedRepository`
- `YouTubeBridgeV2/storage/repositories.py::read_live_session_snapshot`
- `YouTubeBridgeV2/storage/repositories.py::append_phase_transition`
- `YouTubeBridgeV2/storage/repositories.py::append_live_event`
- `YouTubeBridgeV2/storage/repositories.py::append_interaction`
- `YouTubeBridgeV2/storage/repositories.py::StorageBackendNotConfigured`
- `YouTubeBridgeV2/storage/repositories.py::StorageRecordNotFound`
- `YouTubeBridgeV2/storage/repositories.py::StorageContractError`
- `YouTubeBridgeV2/storage/runtime_store.py::RuntimeStoragePort`
- `YouTubeBridgeV2/storage/runtime_store.py::RuntimeStoragePort.save_youtube_polling_cursor`
- `YouTubeBridgeV2/storage/runtime_store.py::RuntimeStoragePort.load_youtube_polling_cursor`
- `YouTubeBridgeV2/storage/runtime_store.py::RuntimeStoragePort.list_recoverable_sessions`
- `YouTubeBridgeV2/storage/runtime_store.py::RuntimeStorageContractError`
- `core/storage/youtube_bridge_v2.py::YouTubeBridgeV2RepositoryMixin`
- `core/storage/youtube_bridge_v2.py::YouTubeBridgeV2RepositoryMixin.create_v2_session`
- `core/storage/youtube_bridge_v2.py::YouTubeBridgeV2RepositoryMixin.get_v2_session`
- `core/storage/youtube_bridge_v2.py::YouTubeBridgeV2RepositoryMixin.update_v2_session`
- `core/storage/youtube_bridge_v2.py::YouTubeBridgeV2RepositoryMixin.list_v2_sessions_for_recovery`
- `core/storage/youtube_bridge_v2.py::YouTubeBridgeV2RepositoryMixin.get_v2_phase_transition`
- `core/storage/youtube_bridge_v2.py::YouTubeBridgeV2RepositoryMixin.append_v2_phase_transition`
- `core/storage/youtube_bridge_v2.py::YouTubeBridgeV2RepositoryMixin.append_v2_live_event`
- `core/storage/youtube_bridge_v2.py::YouTubeBridgeV2RepositoryMixin.list_v2_live_events`
- `core/storage/youtube_bridge_v2.py::YouTubeBridgeV2RepositoryMixin.append_v2_interaction`
- `core/storage/youtube_bridge_v2.py::YouTubeBridgeV2RepositoryMixin.append_v2_finalization`
- `core/storage/youtube_bridge_v2.py::YouTubeBridgeV2RepositoryMixin.get_v2_command_result`
- `core/storage/youtube_bridge_v2.py::YouTubeBridgeV2RepositoryMixin.save_v2_command_result`
- `core/storage_manager.py::StorageManager`

### Query Service

Purpose:
提供 Server/API Surface 使用的 public read model 與 SSE event source，讓 route 不直接讀 storage internals。

Concepts:
- `V2QueryService`
- `V2QueryServiceError`
- `get_session`
- `get_phase`
- `get_session_events`
- `iter_operator_events`
- `iter_display_events`

Stability:
- `provisional`

Source:
- `YouTubeBridgeV2/query_service.py::V2QueryService`
- `YouTubeBridgeV2/query_service.py::V2QueryServiceError`

### Server/API Surface

Purpose:
提供 operator、display、observer 與外部工具使用的 HTTP/SSE contract。

Concepts:
- `POST /v2/sessions`
- `GET /v2/sessions/{session_id}`
- `POST /v2/sessions/{session_id}/plan`
- `GET /v2/sessions/{session_id}/phase`
- `POST /v2/sessions/{session_id}/aftertalk-policy`
- `POST /v2/sessions/{session_id}/automation-control`
- `POST /v2/sessions/{session_id}/manual-close`
- `POST /v2/sessions/{session_id}/tick`
- `POST /v2/sessions/{session_id}/youtube-events`
- `GET /v2/sessions/{session_id}/events`
- `GET /v2/sessions/{session_id}/operator-stream`
- `GET /v2/sessions/{session_id}/display-stream`
- `create_session_endpoint`
- `get_session_endpoint`
- `bind_plan_endpoint`
- `get_phase_endpoint`
- `update_aftertalk_policy_endpoint`
- `AutomationControlRequest`
- `update_automation_control_endpoint`
- `manual_close_endpoint`
- `TickRequest`
- `tick_session_endpoint`
- `YouTubeEventIngestRequest`
- `ingest_youtube_event_endpoint`
- `get_session_events_endpoint`
- `operator_stream_endpoint`
- `display_stream_endpoint`
- `session_not_found` error response

Stability:
- `provisional`

Source:
- `YouTubeBridgeV2/server/routes.py::router`
- `YouTubeBridgeV2/server/routes.py::create_session_endpoint`
- `YouTubeBridgeV2/server/routes.py::get_session_endpoint`
- `YouTubeBridgeV2/server/routes.py::bind_plan_endpoint`
- `YouTubeBridgeV2/server/routes.py::get_phase_endpoint`
- `YouTubeBridgeV2/server/routes.py::update_aftertalk_policy_endpoint`
- `YouTubeBridgeV2/server/routes.py::AutomationControlRequest`
- `YouTubeBridgeV2/server/routes.py::update_automation_control_endpoint`
- `YouTubeBridgeV2/server/routes.py::manual_close_endpoint`
- `YouTubeBridgeV2/server/routes.py::TickRequest`
- `YouTubeBridgeV2/server/routes.py::tick_session_endpoint`
- `YouTubeBridgeV2/server/routes.py::YouTubeEventIngestRequest`
- `YouTubeBridgeV2/server/routes.py::ingest_youtube_event_endpoint`
- `YouTubeBridgeV2/server/routes.py::get_session_events_endpoint`
- `YouTubeBridgeV2/server/routes.py::operator_stream_endpoint`
- `YouTubeBridgeV2/server/routes.py::display_stream_endpoint`

### Ingest YouTube Event Endpoint

Purpose:
接受一筆 operator-supplied YouTube event payload，並委派 `RuntimeApplicationService.handle_youtube_event(...)`。

Route:
- `POST /v2/sessions/{session_id}/youtube-events`

Request:
- `command_id`
- `youtube_event`
- optional `polling_cursor`
- optional `page_info`

Returns:
- sanitized runtime service result body。

Source:
- `YouTubeBridgeV2/server/routes.py::ingest_youtube_event_endpoint`

Wave 3E Boundary Coverage:
- `tests/youtubebridge_v2/test_youtube_ingestion_boundaries.py`

### Access Control / Security

Purpose:
定義 V2 API 權限、secret boundary、MemoriaCore auth delegation 與 sanitized error contract。

Concepts:
- `AuthRequirement`
- `PermissionGroup`
- `PermissionContext`
- `SecurityErrorResponse`
- `SecretBoundary`
- `V2_API_KEYS_PREFS_KEY`
- `V2ApiKeyConfig`
- `load_v2_api_key_config`
- `resolve_permission_context`
- `sanitize_security_error`
- `V2MainSecurityMiddleware`
- `V2LoopbackOnlyMiddleware`

Stability:
- `provisional`

Source:
- `YouTubeBridgeV2/server/security.py::AuthRequirement`
- `YouTubeBridgeV2/server/security.py::PermissionGroup`
- `YouTubeBridgeV2/server/security.py::PermissionContext`
- `YouTubeBridgeV2/server/security.py::SecurityErrorResponse`
- `YouTubeBridgeV2/server/security.py::SecretBoundary`
- `YouTubeBridgeV2/server/auth_config.py::V2_API_KEYS_PREFS_KEY`
- `YouTubeBridgeV2/server/auth_config.py::V2ApiKeyConfig`
- `YouTubeBridgeV2/server/auth_config.py::load_v2_api_key_config`
- `YouTubeBridgeV2/server/security.py::resolve_permission_context`
- `YouTubeBridgeV2/server/security.py::sanitize_security_error`
- `YouTubeBridgeV2/server/main_security.py::V2MainSecurityMiddleware`
- `YouTubeBridgeV2/server/main_security.py::V2LoopbackOnlyMiddleware`

### Observability

Purpose:
定義 phase transition、adapter summary、runtime error 與 correlation metadata 的診斷 contract。

Concepts:
- `TransitionLogEntry`
- `AdapterTraceSummary`
- `RuntimeErrorEvent`
- `CorrelationMetadata`
- `DiagnosticEvent`
- `build_transition_log_entry`
- `redact_adapter_summary`
- `classify_runtime_error`

Stability:
- `provisional`

Source:
- `YouTubeBridgeV2/runtime/observability.py::TransitionLogEntry`
- `YouTubeBridgeV2/runtime/observability.py::AdapterTraceSummary`
- `YouTubeBridgeV2/runtime/observability.py::RuntimeErrorEvent`
- `YouTubeBridgeV2/runtime/observability.py::CorrelationMetadata`
- `YouTubeBridgeV2/runtime/observability.py::DiagnosticEvent`
- `YouTubeBridgeV2/runtime/observability.py::build_transition_log_entry`
- `YouTubeBridgeV2/runtime/observability.py::redact_adapter_summary`
- `YouTubeBridgeV2/runtime/observability.py::classify_runtime_error`

### Operator Console UI

Purpose:
定義後台控制 UI 消費的 session status、control action 與 diagnostic contract。
Wave 5A：初始 status dashboard 以 `GET /v2/sessions/{session_id}` 作為 durable source，phase-only endpoint 不再是初始載入來源。
Wave 5B：main-app status/phase response 會包含 request `permission_group`，operator controls 只在 operator context 顯示。
Wave 5C：Aftertalk policy control 更新成功後重新讀 `GET /v2/sessions/{session_id}`，不靠 optimistic local patch 作為最終狀態。

Concepts:
- `GET /v2/static/operator-console/index.html`
- `GET /v2/sessions/{session_id}`
- `OperatorSessionStatusView`
- `OperatorControlAction`
- `AftertalkPolicyControl`
- `CreateSessionCommand`
- `BindPlanCommand`
- `TickSessionCommand`
- `ManualCloseCommand`
- `OperatorDiagnosticBanner`
- `renderOperatorConsole`
- `loadOperatorStatus`
- `connectOperatorStream`
- `initOperatorConsoleI18n`
- `mountOperatorConsole`

Stability:
- `provisional`

Source:
- `api/main.py::/v2/static`
- `api/main.py::YouTubeBridgeV2.server.routes.router`
- `YouTubeBridgeV2/static/operator-console/index.html`
- `YouTubeBridgeV2/static/operator-console/operator-console.css`
- `YouTubeBridgeV2/static/operator-console/operator-console.js::OperatorSessionStatusView`
- `YouTubeBridgeV2/static/operator-console/operator-console.js::OperatorControlAction`
- `YouTubeBridgeV2/static/operator-console/operator-console.js::AftertalkPolicyControl`
- `YouTubeBridgeV2/static/operator-console/operator-console.js::CreateSessionCommand`
- `YouTubeBridgeV2/static/operator-console/operator-console.js::BindPlanCommand`
- `YouTubeBridgeV2/static/operator-console/operator-console.js::TickSessionCommand`
- `YouTubeBridgeV2/static/operator-console/operator-console.js::ManualCloseCommand`
- `YouTubeBridgeV2/static/operator-console/operator-console.js::OperatorDiagnosticBanner`
- `YouTubeBridgeV2/static/operator-console/operator-console.js::renderOperatorConsole`
- `YouTubeBridgeV2/static/operator-console/operator-console.js::loadOperatorStatus`
- `YouTubeBridgeV2/static/operator-console/operator-console.js::connectOperatorStream`
- `YouTubeBridgeV2/static/operator-console/operator-console.js::initOperatorConsoleI18n`
- `YouTubeBridgeV2/static/operator-console/operator-console.js::mountOperatorConsole`

### Chat Display UI

Purpose:
定義直播畫面 chat display 可呈現的 display-safe event contract。

Concepts:
- `GET /v2/static/chat-display/index.html`
- `DisplayMessageEvent`
- `DisplaySystemStateEvent`
- `DisplaySuperChatEvent`
- `DisplayCharacterResponseEvent`
- `DisplayPresentationMetadata`
- `renderDisplayEvent`
- `renderDisplayEvents`
- `connectDisplayStream`
- `initChatDisplayI18n`
- `mountChatDisplay`

Stability:
- `provisional`

Source:
- `api/main.py::/v2/static`
- `YouTubeBridgeV2/static/chat-display/index.html`
- `YouTubeBridgeV2/static/chat-display/chat-display.css`
- `YouTubeBridgeV2/static/chat-display/chat-display.js::DisplayMessageEvent`
- `YouTubeBridgeV2/static/chat-display/chat-display.js::DisplaySystemStateEvent`
- `YouTubeBridgeV2/static/chat-display/chat-display.js::DisplaySuperChatEvent`
- `YouTubeBridgeV2/static/chat-display/chat-display.js::DisplayCharacterResponseEvent`
- `YouTubeBridgeV2/static/chat-display/chat-display.js::DisplayPresentationMetadata`
- `YouTubeBridgeV2/static/chat-display/chat-display.js::renderDisplayEvent`
- `YouTubeBridgeV2/static/chat-display/chat-display.js::renderDisplayEvents`
- `YouTubeBridgeV2/static/chat-display/chat-display.js::connectDisplayStream`
- `YouTubeBridgeV2/static/chat-display/chat-display.js::initChatDisplayI18n`
- `YouTubeBridgeV2/static/chat-display/chat-display.js::mountChatDisplay`

### YouTube Adapter

Purpose:
定義 YouTube live chat polling、event normalization、Super Chat metadata 與 stream status contract。

Concepts:
- `NormalizedYouTubeEvent`
- `YouTubePollingCursor`
- `SuperChatMetadata`
- `YouTubeStreamStatus`
- `YouTubeAdapterError`
- `normalize_youtube_event`
- `extract_super_chat_metadata`
- `classify_youtube_error`

Stability:
- `provisional`

Source:
- `YouTubeBridgeV2/adapters/youtube.py::NormalizedYouTubeEvent`
- `YouTubeBridgeV2/adapters/youtube.py::YouTubePollingCursor`
- `YouTubeBridgeV2/adapters/youtube.py::SuperChatMetadata`
- `YouTubeBridgeV2/adapters/youtube.py::YouTubeStreamStatus`
- `YouTubeBridgeV2/adapters/youtube.py::YouTubeAdapterError`
- `YouTubeBridgeV2/adapters/youtube.py::normalize_youtube_event`
- `YouTubeBridgeV2/adapters/youtube.py::extract_super_chat_metadata`
- `YouTubeBridgeV2/adapters/youtube.py::classify_youtube_error`

### Presentation/TTS

Purpose:
定義 presentation 與 TTS event consumer 的 queue、ack、timeout 與 display metadata contract。

Concepts:
- `PresentationEvent`
- `TTSRequest`
- `DeliveryAck`
- `DeliveryTimeoutResult`
- `PresentationDisplayMetadata`
- `build_presentation_event`
- `enqueue_tts_request`
- `record_delivery_ack`
- `record_delivery_timeout`

Stability:
- `provisional`

Source:
- `YouTubeBridgeV2/presentation/tts.py::PresentationEvent`
- `YouTubeBridgeV2/presentation/tts.py::TTSRequest`
- `YouTubeBridgeV2/presentation/tts.py::DeliveryAck`
- `YouTubeBridgeV2/presentation/tts.py::DeliveryTimeoutResult`
- `YouTubeBridgeV2/presentation/tts.py::PresentationDisplayMetadata`
- `YouTubeBridgeV2/presentation/tts.py::build_presentation_event`
- `YouTubeBridgeV2/presentation/tts.py::enqueue_tts_request`
- `YouTubeBridgeV2/presentation/tts.py::record_delivery_ack`
- `YouTubeBridgeV2/presentation/tts.py::record_delivery_timeout`
