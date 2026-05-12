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

### Runtime Application Service

Purpose:
協調 V2 session command workflow，負責 snapshot read、phase decision dispatch、storage write、adapter call、event publish、idempotency 與 recovery。

Concepts:
- `RuntimeApplicationService`
- `RuntimeCommand`
- `RuntimeCommandType`
- `RuntimeServiceResult`
- `RuntimeServiceEvent`
- `PersistedTransitionRef`
- `AdapterDispatchResult`
- `RecoveryDecision`

Stability:
- `provisional`

Source:
- `YouTubeBridgeV2/runtime/application_service.py::RuntimeApplicationService`
- `YouTubeBridgeV2/runtime/application_service.py::RuntimeCommand`
- `YouTubeBridgeV2/runtime/application_service.py::RuntimeCommandType`
- `YouTubeBridgeV2/runtime/application_service.py::RuntimeServiceResult`
- `YouTubeBridgeV2/runtime/application_service.py::RuntimeServiceEvent`
- `YouTubeBridgeV2/runtime/application_service.py::PersistedTransitionRef`
- `YouTubeBridgeV2/runtime/application_service.py::AdapterDispatchResult`
- `YouTubeBridgeV2/runtime/application_service.py::RecoveryDecision`

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

### MemoriaCore Adapter

Purpose:
將 V2 planned show / aftertalk intent 轉成 MemoriaCore `/api/v1/chat/sync` request envelope，並正規化回覆與錯誤。

Concepts:
- `MemoriaRequestPayload`
- `NormalizedMemoriaResponse`
- `MemoriaAdapterError`
- `MemoriaCorrelationMetadata`
- `build_memoria_request`
- `normalize_memoria_response`
- `classify_memoria_error`

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

### Closing

Purpose:
定義 `closing` phase 內 final message、Super Chat acknowledgement、finalization result 與 `closing_completion_status` contract。

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
提供 V2 session、phase transition、event、interaction 與 finalization 的 repository contract。

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

Stability:
- `provisional`

Implementation status:
- Adapter skeleton. The repository contracts require an explicitly injected
  `StorageManager`-like V2 backend. The default helper path intentionally raises
  `StorageBackendNotConfigured` until the durable backend is wired through
  `core/storage/` and `core/storage_manager.py`.
- `StorageManagerBackedRepository` is an aggregate repository facade, not the
  `RuntimeApplicationService` storage adapter contract.

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

### Server/API Surface

Purpose:
提供 operator、display、observer 與外部工具使用的 HTTP/SSE contract。

Concepts:
- `POST /v2/sessions`
- `GET /v2/sessions/{session_id}`
- `POST /v2/sessions/{session_id}/plan`
- `GET /v2/sessions/{session_id}/phase`
- `POST /v2/sessions/{session_id}/aftertalk-policy`
- `POST /v2/sessions/{session_id}/manual-close`
- `GET /v2/sessions/{session_id}/events`
- `GET /v2/sessions/{session_id}/operator-stream`
- `GET /v2/sessions/{session_id}/display-stream`
- `create_session_endpoint`
- `get_session_endpoint`
- `bind_plan_endpoint`
- `get_phase_endpoint`
- `update_aftertalk_policy_endpoint`
- `manual_close_endpoint`
- `get_session_events_endpoint`
- `operator_stream_endpoint`
- `display_stream_endpoint`

Stability:
- `provisional`

Source:
- `YouTubeBridgeV2/server/routes.py::router`
- `YouTubeBridgeV2/server/routes.py::create_session_endpoint`
- `YouTubeBridgeV2/server/routes.py::get_session_endpoint`
- `YouTubeBridgeV2/server/routes.py::bind_plan_endpoint`
- `YouTubeBridgeV2/server/routes.py::get_phase_endpoint`
- `YouTubeBridgeV2/server/routes.py::update_aftertalk_policy_endpoint`
- `YouTubeBridgeV2/server/routes.py::manual_close_endpoint`
- `YouTubeBridgeV2/server/routes.py::get_session_events_endpoint`
- `YouTubeBridgeV2/server/routes.py::operator_stream_endpoint`
- `YouTubeBridgeV2/server/routes.py::display_stream_endpoint`

### Access Control / Security

Purpose:
定義 V2 API 權限、secret boundary、MemoriaCore auth delegation 與 sanitized error contract。

Concepts:
- `AuthRequirement`
- `PermissionGroup`
- `PermissionContext`
- `SecurityErrorResponse`
- `SecretBoundary`
- `resolve_permission_context`
- `sanitize_security_error`

Stability:
- `provisional`

Source:
- `YouTubeBridgeV2/server/security.py::AuthRequirement`
- `YouTubeBridgeV2/server/security.py::PermissionGroup`
- `YouTubeBridgeV2/server/security.py::PermissionContext`
- `YouTubeBridgeV2/server/security.py::SecurityErrorResponse`
- `YouTubeBridgeV2/server/security.py::SecretBoundary`
- `YouTubeBridgeV2/server/security.py::resolve_permission_context`
- `YouTubeBridgeV2/server/security.py::sanitize_security_error`

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

Concepts:
- `OperatorSessionStatusView`
- `OperatorControlAction`
- `AftertalkPolicyControl`
- `ManualCloseCommand`
- `OperatorDiagnosticBanner`

Stability:
- `provisional`

### Chat Display UI

Purpose:
定義直播畫面 chat display 可呈現的 display-safe event contract。

Concepts:
- `DisplayMessageEvent`
- `DisplaySystemStateEvent`
- `DisplaySuperChatEvent`
- `DisplayCharacterResponseEvent`
- `DisplayPresentationMetadata`

Stability:
- `provisional`

### YouTube Adapter

Purpose:
定義 YouTube live chat polling、event normalization、Super Chat metadata 與 stream status contract。

Concepts:
- `NormalizedYouTubeEvent`
- `YouTubePollingCursor`
- `SuperChatMetadata`
- `YouTubeStreamStatus`
- `YouTubeAdapterError`

Stability:
- `provisional`

### Presentation/TTS

Purpose:
定義 presentation 與 TTS event consumer 的 queue、ack、timeout 與 display metadata contract。

Concepts:
- `PresentationEvent`
- `TTSRequest`
- `DeliveryAck`
- `DeliveryTimeoutResult`
- `PresentationDisplayMetadata`

Stability:
- `provisional`
