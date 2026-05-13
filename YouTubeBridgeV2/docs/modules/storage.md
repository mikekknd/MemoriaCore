# Storage Module Design

## Purpose

Storage 負責定義 V2 session、phase state、events、interactions、adapter metadata、TTS delivery state 與 finalization result 的保存 contract。Wave 2A 已落地 `StorageManager` durable backend：V2 package 仍只透過 repository/interface 與 `RuntimeStoragePort` 委派資料操作，實際 SQLite schema 與讀寫集中在 `core/storage/youtube_bridge_v2.py` 與 `core/storage_manager.py`。

## Ownership

- 擁有 V2 session record、phase state、plan cursor、events、interaction summary、adapter metadata、TTS delivery queue/ack/timeout state。
- 擁有 Runtime Phase 所需 session snapshot 的讀取介面。
- 擁有 phase transition write 與 idempotency boundary。
- 擁有資料保存與讀取的 repository contract。
- 擁有 V2 repository contract 到主專案 `StorageManager` 方法的 mapping。
- 不擁有 phase decision、adapter transport 或 UI rendering。

## Inputs

- session create/update command。
- phase transition decision。
- planned turn result、aftertalk result、adapter response summary。
- YouTube normalized event。
- presentation/TTS delivery request、ack 與 timeout result。
- finalization result。

## Outputs

- `LiveSessionSnapshot`：Runtime Phase 的 pure input。
- `StoredPhaseTransition`：transition record。
- `StoredLiveEvent`：normalized event record。
- `StoredInteraction`：角色/觀眾互動摘要。
- `StoredTTSDelivery`：provider-neutral delivery queue/ack/timeout public state。
- `SessionFinalizationRecord`：closing/ended 結果。

## Dependencies

- Runtime Phase 讀取 snapshot 並輸出 transition decision。
- 主專案 `StorageManager` 提供實際資料讀寫邊界與 async lock。
- LiveEpisodePlan Runner 讀寫 plan cursor 與 turn result。
- Aftertalk 讀寫 aftertalk request/response summary。
- MemoriaCore Adapter 與 YouTube Adapter 提供 normalized metadata。
- Server/API Surface 透過 `RuntimeStoragePort` 與 `V2QueryService` 使用 repository/backend，不直接碰 storage internals。
- Observability 讀取 transition/event summaries。

## Out Of Scope

- 另行建立 migration CLI 或 schema migration framework。
- 在 `YouTubeBridgeV2/` 內直接連線 SQLite 或選擇 SQLite implementation。
- 在 `YouTubeBridgeV2/` 內新增繞過 `StorageManager` 的 persistence backend。
- FastAPI route。
- YouTube/MemoriaCore transport。
- LLM prompt 或 role response generation。
- UI rendering。

## Public Entrypoints

本模組的 repository adapter 已由 `YouTubeBridgeV2/storage/repositories.py` 實作。V2 repository 只委派到明確注入的 `StorageManager`-like 邊界，不在 `YouTubeBridgeV2/` 內直接存取 SQLite；未設定預設 backend 時，module-level helper 會丟 `StorageBackendNotConfigured`。

- `SessionRepository`：session lifecycle 與 snapshot 讀取。
- `PhaseTransitionRepository`：transition append 與 idempotency。
- `EventRepository`：YouTube/system event append。
- `InteractionRepository`：planned show、aftertalk、chat display interaction record。
- `TTSDeliveryRepository`：provider-neutral TTS delivery queue、ack 與 timeout record。
- `FinalizationRepository`：closing 與 ended metadata。
- `StorageManagerBackedRepository`：聚合 repository facade，持有注入的 `StorageManager`-like 物件；不是 `RuntimeApplicationService` 的 storage adapter contract。
- `read_live_session_snapshot(session_id)`：透過預設 repository 讀取 Runtime Phase snapshot；預設 backend 未設定時會丟 `StorageBackendNotConfigured`。
- `append_phase_transition(session_id, transition)`：append transition record，依明確 transition id idempotent；缺 transition id 時回傳 contract error。
- `append_live_event(session_id, event)`：append display-safe normalized event。
- `append_interaction(session_id, interaction)`：append planned show / aftertalk response summary。
- `StorageManagerBackedRepository.tts_deliveries`：委派到注入的 StorageManager-like backend。
- `StorageBackendNotConfigured`：預設 V2 backend 尚未 wiring 時的 explicit error。
- `StorageRecordNotFound`：找不到 V2 session/record 時的 not found error。
- `StorageContractError`：StorageManager 回傳資料不符合 V2 contract 時的 contract error。
- `RuntimeStoragePort`：`RuntimeApplicationService` 使用的 service-facing storage adapter，負責 command-to-storage mapping、event persistence 與 command idempotency round-trip。
- `live_episode_plan_state`：session metadata 內的 sanitized plan execution state，包含 `contract`、`cursor`、`completed_turn_ids`、`last_memoria_session_id`。
- `youtube_polling_cursor`：session metadata 內的 sanitized YouTube polling cursor，包含 `live_chat_id`、`next_page_token`、`polling_interval_millis`、`seen_event_ids` 與 `updated_at`。
- `RuntimeStoragePort.save_youtube_polling_cursor(session_id, cursor, now)` / `load_youtube_polling_cursor(session_id)`：runtime-facing cursor persistence/recovery boundary。
- `RuntimeStoragePort.list_recoverable_sessions(limit=100)`：restart bootstrap 使用的 service-facing non-ended session listing，不讓 automation module 直接掃 storage。
- `YouTubeBridgeV2RepositoryMixin`：主專案 `StorageManager` 的 durable V2 repository mixin，負責 `yb2_*` schema 初始化、CRUD/append methods 與 public redaction。
- `StorageManager(..., youtube_bridge_v2_db_path=None)`：提供 V2 durable DB path 注入；預設使用 `runtime/youtubebridge_v2.db`。
- `create_v2_session(record)` / `get_v2_session(session_id)` / `update_v2_session(session_id, patch)`：session durable contract。
- `list_v2_sessions_for_recovery(limit=100)`：durable non-ended session listing，供 restart recovery bootstrap 轉成 scheduler recovery refs。
- `get_v2_phase_transition(transition_id)` / `append_v2_phase_transition(session_id, record)`：phase transition append-only/idempotent contract。
- `append_v2_live_event(session_id, record)` / `list_v2_live_events(session_id, limit=100)`：public event history contract。
- `append_v2_interaction(session_id, record)` / `append_v2_finalization(session_id, record)`：interaction/finalization persistence contract。
- `append_v2_tts_request(session_id, record)` / `list_v2_tts_deliveries(session_id, limit=100, status=None)`：provider-neutral TTS queue persistence contract。
- `ack_v2_tts_delivery(session_id, delivery_id, record)` / `timeout_v2_tts_delivery(session_id, delivery_id, record)`：delivery state mutation contract；回傳值必須包含 `phase_transition_requested: false`。
- `get_v2_command_result(command_id)` / `save_v2_command_result(command_id, result)`：runtime command idempotency contract。

## Persistence Rules

| Data | Required Rule |
| --- | --- |
| storage backend | V2 repository must call an explicitly injected `StorageManager`-like backend or a facade exposed by `core/storage_manager.py`; it must not import `sqlite3` or `aiosqlite`. |
| durable DB path | Production default is `runtime/youtubebridge_v2.db`; tests inject `youtube_bridge_v2_db_path` via `StorageManager`. |
| session snapshot | Must contain all Runtime Phase required fields or return contract error. |
| phase transition | Append-only and idempotent by explicit transition id. |
| normalized event | Store display-safe summary separately from private adapter metadata. |
| interaction | Store speaker, phase, public content summary, and correlation id. |
| TTS delivery | Store display-safe text, speaker/voice/provider summary, queue position, status, ack time and timeout summary; never store raw provider payload or hidden prompt. |
| LiveEpisodePlan state | Store sanitized contract/cursor/completed turn ids in session metadata; raw Topic Pack, raw FactCards, hidden prompt and raw Memoria payload stay out of public storage. |
| YouTube polling cursor | Store sanitized cursor in session metadata so restart can continue duplicate detection and pagination state. |
| adapter metadata | Store redacted summary by default; raw payload requires explicit private storage policy. |
| finalization result | Must include closing completion status and ended metadata. |
| crash/restart | Snapshot must be sufficient for Runtime Application Service recovery. |

## Failure Modes

- session 不存在時回傳 not found，不建立隱式 session。
- 預設 backend 尚未設定時回傳 `StorageBackendNotConfigured`，不隱式建立真實 `StorageManager`。
- duplicate transition id 應 idempotent。
- transition id 缺漏時回傳 contract error，不使用固定 fallback id。
- snapshot 缺少 Runtime Phase 必要欄位時回傳 contract error。
- adapter metadata 過大時保存 redacted summary，不保存 raw hidden payload。
- duplicate TTS delivery id 應 idempotent；ack after delivered 回傳 duplicate summary；timeout after delivered 必須 ignored 且不覆寫 delivered state。
- TTS timeout 只更新 delivery state，不得轉成 phase decision。
- repository error 不應被轉成 phase decision。
- SQLite 細節不得外洩到 runtime core，且不得在 `YouTubeBridgeV2/` 內直接存取。

## Test Strategy

- durable schema tests：`StorageManager` 初始化 `yb2_*` tables 且可跨 instance idempotent 初始化。
- create/read tests：session 建立與 snapshot 讀取。
- transition tests：write、read、duplicate idempotency。
- event tests：append normalized YouTube/system event。
- interaction tests：planned show 與 aftertalk response summary。
- TTS delivery tests：queue ordering、metadata redaction、ack idempotency、timeout no phase side effect。
- finalization tests：closing result 與 ended metadata。
- command result tests：`RuntimeServiceResult` 經 durable JSON round-trip 後仍回到 service contract，不變成裸 dict。
- real-storage E2E tests：真 `StorageManager` + fake runners 跑 create session、planned show、aftertalk、manual close、closing、ended 與 restart/recovery。
- boundary tests：V2 模組不可直接依賴 SQLite implementation；V2 storage repository 只可依賴 `StorageManager` 邊界。
- redaction tests：不保存 raw prompt 或 raw adapter payload 到 public metadata。
- repository contract tests：預設 backend 未設定時明確失敗，aggregate facade 不宣稱自己是 Runtime Application Service storage adapter。

## Open Questions

- 後續若需要 schema migration CLI 或 versioned migration，仍必須放在 `core/storage/` 或 `core/storage_manager.py` 邊界內，不可放進 `YouTubeBridgeV2/`。
- Wave 2B 已將 `api/main.py` wiring 到真 V2 durable composition；Wave 2D 已讓 tick + Memoria fake transport vertical slice 可使用 durable storage。
- Wave 6D 已將 provider-neutral TTS queue/ack/timeout state 放入 durable storage；真實 provider delivery 與 browser playback callback 仍待後續 integration。
- 更細的 private adapter metadata 是否需要獨立 private table，需等真 YouTube/MemoriaCore adapter wave 決定；目前 public tables 一律保存 redacted summary。
