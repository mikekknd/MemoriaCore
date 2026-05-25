# MemoriaCore Adapter Module Design

## Purpose

MemoriaCore Adapter 負責把 V2 的 planned show intent、aftertalk request 與 closing final message request 轉成 MemoriaCore `/api/v1/chat/sync` 可接受的 chat 或 group chat payload，並把回覆、session id、trace metadata 與錯誤轉成 V2 normalized response。

## Ownership

- 擁有 V2 -> MemoriaCore request mapping。
- 擁有 MemoriaCore response normalization。
- 擁有 session id 回收、correlation id、timeout 與 transport error classification。
- 擁有 hidden prompt 與 raw payload 的 public/private 邊界。
- 不決定 phase、不推進 plan、不保存 storage。

## Inputs

- `PlannedTurnIntent`、`AftertalkTurnRequest` 或 `ClosingRequest`。
- V2 session metadata 與公開 context summary。
- MemoriaCore auth/context delegation metadata。
- timeout、retry policy 與 correlation id。

## Outputs

- `MemoriaRequestPayload`：實際傳給 MemoriaCore 的 payload envelope。
- `NormalizedMemoriaResponse`：角色回覆、assistant/session ids、trace/correlation metadata。
- `MemoriaAdapterError`：timeout、transport failure、invalid response、auth failure。
- request summary：供 Observability 使用的 redacted metadata。

## Dependencies

- LiveEpisodePlan Runner 提供 planned turn intent。
- Aftertalk 提供 group chat request intent。
- Closing 提供 final message request intent。
- Access Control / Security 提供 auth delegation 與 secret 邊界。
- Observability 消費 request/response summary。
- Storage 保存 normalized response，但本模組不直接寫入。

## Out Of Scope

- Runtime Phase transition。
- LiveEpisodePlan cursor 推進。
- Aftertalk cue 生成。
- YouTube event handling。
- UI rendering。
- storage transaction。

## Public Entrypoints

本模組的 public contracts 已由 `YouTubeBridgeV2/adapters/memoria.py` 與
`YouTubeBridgeV2/adapters/memoria_http.py` 實作。

- `MemoriaRequestPayload`：MemoriaCore request envelope。
- `NormalizedMemoriaResponse`：V2 內部使用的 response shape。
- `MemoriaAdapterError`：adapter error classification。
- `MemoriaCorrelationMetadata`：trace id、request id、session id 連結資訊。
- `build_memoria_request(intent, context)`：將 planned show / aftertalk / closing intent 映射成 MemoriaCore request envelope。
- `normalize_memoria_response(response_payload, correlation_metadata)`：將 MemoriaCore response payload 正規化成 V2 response 或 adapter error。
- `classify_memoria_error(error)`：將 timeout、transport、auth 與 unknown error 分類成 adapter error，不改變 phase。
- `MemoriaHttpTransportConfig`：真 MemoriaCore HTTP transport 的 public-safe 設定 contract。
- `MemoriaHttpTransportError`：sync HTTP transport 產生的 sanitized error，提供 `error_type`、`retryable`、`status_code` 與 public-safe summary。
- `parse_memoria_http_transport_config(raw_config)`：從明確 mapping 解析 transport config；空設定代表未啟用。
- `load_memoria_http_transport_config(storage_manager)`：從 `StorageManager.load_prefs()` 讀取 `youtubebridge_v2_memoria_transport`，不硬寫 secret。
- `SyncJsonHttpClientProtocol`：可替換的同步 JSON client protocol，供單元測試注入 fake client。
- `UrllibSyncJsonHttpClient`：stdlib `urllib.request` backed sync JSON client。
- `MemoriaSyncHttpTransport`：符合 `MemoriaTransportProtocol.send(...)` 的真 HTTP transport implementation。
- `load_production_memoria_transport(storage_manager)`：production-only opt-in loader；回傳真 transport 或 `None` 讓 composition 維持 no-op。

## Mapping Rules

| Input Intent | Required MemoriaCore Mode | Required Output |
| --- | --- | --- |
| `PlannedTurnIntent` | `/api/v1/chat/sync` public live scope with one planned speaker set | `NormalizedMemoriaResponse` with speaker/session metadata |
| `AftertalkTurnRequest` | `/api/v1/chat/sync` public live scope with group `character_ids` and `group_turn_limit` | normalized multi-speaker response summary |
| `ClosingRequest` | `/api/v1/chat/sync` public live scope with final message context | final response summary or adapter error |
| timeout | no response body assumption | retryable `MemoriaAdapterError` |
| auth failure | terminal error | sanitized auth failure summary |
| invalid response | terminal error | redacted invalid response summary |

Public summaries must include correlation metadata but not hidden prompts, raw request payloads, raw response payloads, or raw Topic Pack content.

## Failure Modes

- MemoriaCore timeout 回傳 timeout error，不自行改 phase。
- transport failure 回傳 retryable/non-retryable classification。
- invalid response 回傳 normalized adapter error。
- auth delegation 缺失時回傳 auth failure。
- hidden prompt 與 raw request 不得出現在 public summary。
- group chat response 缺少 speaker metadata 時回傳 invalid response。
- HTTP transport config 缺少 `base_url` 時視為未啟用；`base_url` 非 http/https、帶 credentials/query/fragment，或 timeout 非正數時回設定錯誤，不啟動真外呼。
- HTTP timeout 與 5xx transport failure 可依 `max_attempts` retry；重試耗盡後回 retryable sanitized error。
- HTTP 401/403 auth failure 為 terminal，不重試，public summary 只保留 `error_type`、`retryable` 與 `status_code`。
- HTTP invalid JSON 或 non-object JSON response 為 terminal `invalid_response`，不得把 raw body 寫進 public summary。
- HTTP transport public summary 只能顯示 `base_url`、`timeout_seconds`、`max_attempts` 與 `has_api_key`，不得顯示 token/header/raw payload。
- real integration harness 缺少 opt-in env 或必要 base URL / character id 時必須 skip，不得嘗試外呼。
- production wiring 只有在 `youtubebridge_v2_memoria_transport.enabled` 明確啟用且 config valid 時建立真 Memoria HTTP transport；未設定、未啟用或設定錯誤時維持 no-op，不嘗試外呼。

## Test Strategy

- request mapping tests：planned show 與 aftertalk request 轉換。
- response normalization tests：單人 chat 與 group chat response。
- timeout tests：timeout 被分類且不吞錯。
- transport failure tests：retryable 與 terminal error。
- auth tests：缺少 delegation 時失敗。
- privacy tests：public summary 不含 hidden prompt/raw payload。
- side effect tests：adapter 不寫 storage、不改 phase、不更新 UI。
- real integration harness：`tests/youtubebridge_v2/test_memoria_real_integration.py` 預設 skip；只有設定 `YB2_MEMORIA_INTEGRATION=1`、`YB2_MEMORIA_BASE_URL` 與 `YB2_MEMORIA_CHARACTER_ID` 時才會呼叫本機 MemoriaCore。
- full external V2 E2E harness：`tests/youtubebridge_v2/test_full_external_e2e.py` 預設 skip；只有設定 `YB2_FULL_EXTERNAL_E2E=1`、MemoriaCore base URL 與 character id 時，才會把真 Memoria HTTP transport 接進 V2 runtime/display/TTS round trip。

## Open Questions

- 目前 adapter request target 已確認為 MemoriaCore `/api/v1/chat/sync`；streaming transport 是否使用 `/api/v1/chat/stream-sync` 由後續 runtime service 決定。
- correlation id 是否由 Server/API Surface 或 Observability 產生需後續決定。
- backoff policy 需與 runtime service 設計對齊；目前 sync transport 只定義 `max_attempts` 重試次數，不 sleep。
