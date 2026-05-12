# MemoriaCore Adapter Module Design

## Purpose

MemoriaCore Adapter 負責把 V2 的 planned show intent 與 aftertalk request 轉成 MemoriaCore API 可接受的 chat 或 group chat payload，並把回覆、session id、trace metadata 與錯誤轉成 V2 normalized response。

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

本階段只描述 planned public contracts，不宣稱 source symbol 已存在。

- `MemoriaRequestPayload`：MemoriaCore request envelope。
- `NormalizedMemoriaResponse`：V2 內部使用的 response shape。
- `MemoriaAdapterError`：adapter error classification。
- `MemoriaCorrelationMetadata`：trace id、request id、session id 連結資訊。

## Mapping Rules

| Input Intent | Required MemoriaCore Mode | Required Output |
| --- | --- | --- |
| `PlannedTurnIntent` | chat or directed role request | `NormalizedMemoriaResponse` with speaker/session metadata |
| `AftertalkTurnRequest` | group chat request | normalized multi-speaker response summary |
| `ClosingRequest` | final message request | final response summary or adapter error |
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

- MemoriaCore group chat API 的最終 endpoint 與 payload shape 需在實作前重新確認。
- correlation id 是否由 Server/API Surface 或 Observability 產生需後續決定。
- retry 次數與 backoff policy 需與 runtime service 設計對齊。
