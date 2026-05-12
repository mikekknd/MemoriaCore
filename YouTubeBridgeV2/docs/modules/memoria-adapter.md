# MemoriaCore Adapter Module Design

## Purpose

MemoriaCore Adapter 負責把 V2 的 planned show intent 與 aftertalk request 轉成 MemoriaCore `/api/v1/chat/sync` 可接受的 chat 或 group chat payload，並把回覆、session id、trace metadata 與錯誤轉成 V2 normalized response。

## Ownership

- 擁有 V2 -> MemoriaCore request mapping。
- 擁有 MemoriaCore response normalization。
- 擁有 session id 回收、correlation id、timeout 與 transport error classification。
- 擁有 hidden prompt 與 raw payload 的 public/private 邊界。
- 不決定 phase、不推進 plan、不保存 storage。

## Inputs

- `PlannedTurnIntent` 或 `AftertalkTurnRequest`。
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

本模組的 public contracts 已由 `YouTubeBridgeV2/adapters/memoria.py` 實作。

- `MemoriaRequestPayload`：MemoriaCore request envelope。
- `NormalizedMemoriaResponse`：V2 內部使用的 response shape。
- `MemoriaAdapterError`：adapter error classification。
- `MemoriaCorrelationMetadata`：trace id、request id、session id 連結資訊。
- `build_memoria_request(intent, context)`：將 planned show / aftertalk intent 映射成 MemoriaCore request envelope。
- `normalize_memoria_response(response_payload, correlation_metadata)`：將 MemoriaCore response payload 正規化成 V2 response 或 adapter error。
- `classify_memoria_error(error)`：將 timeout、transport、auth 與 unknown error 分類成 adapter error，不改變 phase。

## Mapping Rules

| Input Intent | Required MemoriaCore Mode | Required Output |
| --- | --- | --- |
| `PlannedTurnIntent` | `/api/v1/chat/sync` public live scope with one planned speaker set | `NormalizedMemoriaResponse` with speaker/session metadata |
| `AftertalkTurnRequest` | `/api/v1/chat/sync` public live scope with group `character_ids` and `group_turn_limit` | normalized multi-speaker response summary |
| `ClosingRequest` | future final message request contract; not implemented in current source | final response summary or adapter error |
| timeout | no response body assumption | retryable `MemoriaAdapterError` |
| auth failure | terminal error | sanitized auth failure summary |
| invalid response | terminal or retryable by classification | redacted invalid response summary |

Public summaries must include correlation metadata but not hidden prompts, raw request payloads, raw response payloads, or raw Topic Pack content.

## Failure Modes

- MemoriaCore timeout 回傳 timeout error，不自行改 phase。
- transport failure 回傳 retryable/non-retryable classification。
- invalid response 回傳 normalized adapter error。
- auth delegation 缺失時回傳 auth failure。
- hidden prompt 與 raw request 不得出現在 public summary。
- group chat response 缺少 speaker metadata 時回傳 invalid response。

## Test Strategy

- request mapping tests：planned show 與 aftertalk request 轉換。
- response normalization tests：單人 chat 與 group chat response。
- timeout tests：timeout 被分類且不吞錯。
- transport failure tests：retryable 與 terminal error。
- auth tests：缺少 delegation 時失敗。
- privacy tests：public summary 不含 hidden prompt/raw payload。
- side effect tests：adapter 不寫 storage、不改 phase、不更新 UI。

## Open Questions

- 目前 adapter request target 已確認為 MemoriaCore `/api/v1/chat/sync`；streaming transport 是否使用 `/api/v1/chat/stream-sync` 由後續 runtime service 決定。
- correlation id 是否由 Server/API Surface 或 Observability 產生需後續決定。
- retry 次數與 backoff policy 需與 runtime service 設計對齊。
