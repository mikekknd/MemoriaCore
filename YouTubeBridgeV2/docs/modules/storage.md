# Storage Module Design

## Purpose

Storage 負責定義 V2 session、phase state、events、interactions、adapter metadata 與 finalization result 的保存 contract。本階段落地的是 repository adapter skeleton：它提供 repository/interface 與主專案 `StorageManager`-like 邊界的映射，但尚未新增 durable V2 backend 或 Runtime Application Service storage adapter，避免 V2 runtime core 或 V2 storage package 直接依賴 SQLite。

## Ownership

- 擁有 V2 session record、phase state、plan cursor、events、interaction summary、adapter metadata。
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
- finalization result。

## Outputs

- `LiveSessionSnapshot`：Runtime Phase 的 pure input。
- `StoredPhaseTransition`：transition record。
- `StoredLiveEvent`：normalized event record。
- `StoredInteraction`：角色/觀眾互動摘要。
- `SessionFinalizationRecord`：closing/ended 結果。

## Dependencies

- Runtime Phase 讀取 snapshot 並輸出 transition decision。
- 主專案 `StorageManager` 提供實際資料讀寫邊界與 async lock。
- LiveEpisodePlan Runner 讀寫 plan cursor 與 turn result。
- Aftertalk 讀寫 aftertalk request/response summary。
- MemoriaCore Adapter 與 YouTube Adapter 提供 normalized metadata。
- Server/API Surface 透過 service-facing storage adapter 使用 repository；該 adapter 屬於後續 integration，不由本 skeleton 直接提供。
- Observability 讀取 transition/event summaries。

## Out Of Scope

- 具體 migration script。
- 直接連線 SQLite 或選擇 SQLite implementation。
- 在 `YouTubeBridgeV2/` 內新增繞過 `StorageManager` 的 persistence backend。
- FastAPI route。
- YouTube/MemoriaCore transport。
- LLM prompt 或 role response generation。
- UI rendering。
- TTS delivery。

## Public Entrypoints

本模組的 repository adapter skeleton 已由 `YouTubeBridgeV2/storage/repositories.py` 實作。V2 repository 只委派到明確注入的 `StorageManager`-like 邊界，不在 `YouTubeBridgeV2/` 內直接存取 SQLite；未設定預設 backend 時，module-level helper 會丟 `StorageBackendNotConfigured`。

- `SessionRepository`：session lifecycle 與 snapshot 讀取。
- `PhaseTransitionRepository`：transition append 與 idempotency。
- `EventRepository`：YouTube/system event append。
- `InteractionRepository`：planned show、aftertalk、chat display interaction record。
- `FinalizationRepository`：closing 與 ended metadata。
- `StorageManagerBackedRepository`：聚合 repository facade，持有注入的 `StorageManager`-like 物件；不是 `RuntimeApplicationService` 的 storage adapter contract。
- `read_live_session_snapshot(session_id)`：透過預設 repository 讀取 Runtime Phase snapshot；預設 backend 未設定時會丟 `StorageBackendNotConfigured`。
- `append_phase_transition(session_id, transition)`：append transition record，依明確 transition id idempotent；缺 transition id 時回傳 contract error。
- `append_live_event(session_id, event)`：append display-safe normalized event。
- `append_interaction(session_id, interaction)`：append planned show / aftertalk response summary。
- `StorageBackendNotConfigured`：預設 V2 backend 尚未 wiring 時的 explicit error。
- `StorageRecordNotFound`：找不到 V2 session/record 時的 not found error。
- `StorageContractError`：StorageManager 回傳資料不符合 V2 contract 時的 contract error。

## Persistence Rules

| Data | Required Rule |
| --- | --- |
| storage backend | V2 repository must call an explicitly injected `StorageManager`-like backend or a facade exposed by `core/storage_manager.py`; it must not import `sqlite3` or `aiosqlite`. |
| session snapshot | Must contain all Runtime Phase required fields or return contract error. |
| phase transition | Append-only and idempotent by explicit transition id. |
| normalized event | Store display-safe summary separately from private adapter metadata. |
| interaction | Store speaker, phase, public content summary, and correlation id. |
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
- repository error 不應被轉成 phase decision。
- SQLite 細節不得外洩到 runtime core，且不得在 `YouTubeBridgeV2/` 內直接存取。

## Test Strategy

- create/read tests：session 建立與 snapshot 讀取。
- transition tests：write、read、duplicate idempotency。
- event tests：append normalized YouTube/system event。
- interaction tests：planned show 與 aftertalk response summary。
- finalization tests：closing result 與 ended metadata。
- boundary tests：V2 模組不可直接依賴 SQLite implementation；V2 storage repository 只可依賴 `StorageManager` 邊界。
- redaction tests：不保存 raw prompt 或 raw adapter payload 到 public metadata。
- skeleton contract tests：預設 backend 未設定時明確失敗，aggregate facade 不宣稱自己是 Runtime Application Service storage adapter。

## Open Questions

- 若 V2 需要新 SQLite schema 或 migration，實作位置必須在 `core/storage/` 與 `core/storage_manager.py` facade 內鎖定；`YouTubeBridgeV2/` 只保留 repository contract/adapter。
- Runtime Application Service 需要一層 service-facing storage adapter，將 `create_session(command, now)`、`persist_transition(...)`、`request_manual_close(...)` 等 service contract 映射到 repository/backend；本 skeleton 不直接提供該 adapter。
- runtime DB 檔案位置需沿用 repo `runtime/` 原則，並由 `StorageManager` 設定決定。
- transition id 的生成責任需與 Observability 或 runtime service 對齊。
