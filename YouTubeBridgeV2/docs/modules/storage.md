# Storage Module Design

## Purpose

Storage 負責定義 V2 session、phase state、events、interactions、adapter metadata 與 finalization result 的保存 contract。它提供 repository/interface 給 runtime application service 使用，並透過主專案既有 `StorageManager` 邊界存取資料，避免 V2 runtime core 或 V2 storage package 直接依賴 SQLite。

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
- Server/API Surface 透過 service 使用 repository。
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

本階段只描述 planned public contracts，不宣稱 source symbol 已存在。

- `SessionRepository`：session lifecycle 與 snapshot 讀取。
- `PhaseTransitionRepository`：transition append 與 idempotency。
- `EventRepository`：YouTube/system event append。
- `InteractionRepository`：planned show、aftertalk、chat display interaction record。
- `FinalizationRepository`：closing 與 ended metadata。

## Persistence Rules

| Data | Required Rule |
| --- | --- |
| storage backend | V2 repository must call `StorageManager` or a facade exposed by `core/storage_manager.py`; it must not import `sqlite3` or `aiosqlite`. |
| session snapshot | Must contain all Runtime Phase required fields or return contract error. |
| phase transition | Append-only and idempotent by transition id. |
| normalized event | Store display-safe summary separately from private adapter metadata. |
| interaction | Store speaker, phase, public content summary, and correlation id. |
| adapter metadata | Store redacted summary by default; raw payload requires explicit private storage policy. |
| finalization result | Must include closing completion status and ended metadata. |
| crash/restart | Snapshot must be sufficient for Runtime Application Service recovery. |

## Failure Modes

- session 不存在時回傳 not found，不建立隱式 session。
- duplicate transition id 應 idempotent。
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

## Open Questions

- 若 V2 需要新 SQLite schema 或 migration，實作位置必須在 `core/storage/` 與 `core/storage_manager.py` facade 內鎖定；`YouTubeBridgeV2/` 只保留 repository contract/adapter。
- runtime DB 檔案位置需沿用 repo `runtime/` 原則，並由 `StorageManager` 設定決定。
- transition id 的生成責任需與 Observability 或 runtime service 對齊。
