# Runtime Tick + Memoria Runner Vertical Slice Implementation Record

## Summary

Wave 2D 新增 operator-only explicit tick endpoint，將 production/runtime 從 create/bind/read 推進到可由 API 觸發 `RuntimeApplicationService.tick_session(...)`。同時補上可注入的 Memoria runners，先以 fake transport 驗證 `planned_show -> aftertalk -> closing -> ended`，不接真 MemoriaCore HTTP、YouTube polling、TTS 或 background scheduler。

## Implemented Scope

- `POST /v2/sessions/{session_id}/tick`
  - request body：`command_id`
  - route 建立 `RuntimeCommandType.TICK`
  - standalone app 與 main app 共用同一 route
  - main app security matrix 固定為 operator-only

- LiveEpisodePlan durable state
  - `RuntimeStoragePort.bind_plan(...)` 先呼叫 `validate_episode_plan_contract(...)`
  - session metadata 保存 `live_episode_plan_state`
  - state 欄位：`contract`、`cursor`、`completed_turn_ids`、`last_memoria_session_id`
  - public summary 只保存 plan id/title/turn count/status

- Injectable Memoria runners
  - `MemoriaTransportProtocol.send(request) -> dict[str, object]`
  - `MemoriaPlannedShowRunner`
  - `MemoriaAftertalkRunner`
  - `MemoriaClosingRunner`
  - runner 只透過 StorageManager-like methods 寫入 interaction/finalization/session patch

- Production safety
  - `create_production_v2_composition(storage_manager, memoria_transport=None)`
  - 未提供 transport 時維持 explicit no-op runners
  - 明確注入 transport 時才使用 Memoria runners

## Red Cases

- tick endpoint 不存在時，server API surface test 會回 404。
- observer/display API key 呼叫 tick 必須被拒絕。
- runner module 不存在時，runner tests 無法 import。
- bind plan 沒有保存 `live_episode_plan_state` 時，plan-state test 失敗。
- fake-transport vertical slice 必須能透過 API tick 推進 lifecycle。
- repeated `command_id` 必須 replay stored result，不重複呼叫 transport 或 append interactions。
- real StorageManager rebuild 後，same command replay 與下一次 tick 仍成立。

## Green Scope

- 只新增 explicit API tick，不新增 background scheduler。
- 只新增 sync transport protocol，不新增真 HTTP transport。
- runner 只處理 planned show、aftertalk、closing 的 public-safe request/response summary。
- closing request mapping 只接到 Memoria adapter request contract，不停止 YouTube livestream。
- V2 package 仍不 import `sqlite3` 或 `aiosqlite`。

## Refactor Boundary

- 不改 `RuntimeApplicationService` public contract。
- 不改 phase lifecycle。
- 不引入 Legacy `YouTubeBridge/` dependency。
- 不把 fake transport/helper 列為 production public API。

## Verification

- `tests/youtubebridge_v2/test_server_api_surface.py::test_tick_session_delegates_to_runtime_service`
- `tests/youtubebridge_v2/test_main_app_security.py`
- `tests/youtubebridge_v2/test_runtime_memoria_runners.py`
- `tests/youtubebridge_v2/test_runtime_tick_vertical_slice.py`
- `tests/youtubebridge_v2/test_real_storage_integration.py::test_bind_plan_persists_sanitized_live_episode_plan_state`

## Remaining Work

- 真 MemoriaCore HTTP transport。
- YouTube polling/tick input integration。
- background scheduler 或 operator console tick trigger。
- API key 管理 UI。
- Presentation/TTS delivery integration。
