# Closing Module Design

## Purpose

Closing 負責 `closing` phase 的收尾流程。它把 planned show 或 aftertalk 的結束原因、session summary、未處理 Super Chat、必要的 final message intent 與 finalization result 整理成可保存、可觀測、可呈現的 closing contract，並向 Runtime Phase 回報 `closing_completion_status`。

Closing 不決定何時進入 `closing`；它只負責進入後如何安全收尾。

## Ownership

Closing 擁有：

- closing entry context normalization。
- final closing request / cue 的建構。
- unhandled Super Chat 收尾清單與 acknowledgement intent。
- finalization result 與 `closing_completion_status`。
- closing retry/idempotency 規則。
- `closing -> ended` 所需的完成訊號。

Closing 不擁有：

- Runtime Phase transition decision。
- YouTube polling。
- MemoriaCore HTTP transport。
- storage transaction implementation。
- operator console rendering。
- TTS delivery。
- Legacy closing fallback。

## Inputs

必要輸入：

- `ClosingStartContext`：session id、current phase、closing reason、started_at。
- `ClosingReason`：`duration_reached | manual_close | plan_completed | stream_ended | unrecoverable_error`。
- `SessionSummary`：節目、aftertalk、interaction 的 redacted summary。
- `UnhandledSuperChatSummary`：尚未收尾的 Super Chat 摘要。
- `ClosingPolicy`：是否產生 final message、是否處理 Super Chat、timeout/retry policy。
- `NormalizedMemoriaResponse`：若 final message 需要 MemoriaCore 回覆。

## Outputs

- `ClosingRequest`：交給 MemoriaCore Adapter 的 final message intent。
- `ClosingSuperChatAction`：Super Chat acknowledgement 或 skipped reason。
- `ClosingFinalizationResult`：finalization status、summary、timestamps、error summary。
- `ClosingCompletionStatus`：Runtime Phase 消費的 `complete | incomplete | failed_retryable | failed_terminal`。
- `ClosingDisplayEvent`：display-safe closing status。

## Dependencies

- Runtime Application Service 呼叫 Closing 並保存結果。
- Runtime Phase 消費 `closing_completion_status`。
- Storage 讀取 session summary / Super Chat summary 並保存 finalization result。
- MemoriaCore Adapter 執行 final message request。
- Observability 記錄 closing reason、duration、adapter summary、error。
- Chat Display UI 呈現 display-safe closing status。
- Presentation/TTS 可消費 final closing response。

## Out Of Scope

- 決定是否進入 `closing`。
- 直接停止 YouTube livestream。
- 直接呼叫 MemoriaCore HTTP。
- 直接寫 `StorageManager` 或 SQLite。
- UI layout。
- TTS provider details。

## Public Entrypoints

本階段只描述 planned public contracts，不宣稱 source symbol 已存在。

### `ClosingStartContext`

Purpose:
描述 closing 開始時的可測 input。

Required Fields:
- `session_id`
- `closing_reason`
- `phase_entered_at`
- `duration_summary`
- `manual_close_requested`

### `ClosingRequest`

Purpose:
描述 final message 或 Super Chat acknowledgement 的 intent。

Required Fields:
- `session_id`
- `closing_reason`
- `summary`
- `super_chat_actions`
- `visibility`

### `ClosingFinalizationResult`

Purpose:
描述 closing 是否完成以及可寫回 storage 的結果。

Required Fields:
- `status`
- `completed_at`
- `closing_completion_status`
- `display_summary`
- `error_summary`

## Closing Flow

| Step | Responsibility | Output |
| --- | --- | --- |
| Normalize context | Validate closing reason and redacted session summary. | `ClosingStartContext` |
| Load pending acknowledgements | Consume storage-provided unhandled Super Chat summary. | `ClosingSuperChatAction[]` |
| Build final request | Create final closing intent if policy allows it. | `ClosingRequest` |
| Handle adapter response | Normalize final message response through service/adapter result. | response summary |
| Finalize | Build finalization result and completion status. | `ClosingFinalizationResult` |
| Report completion | Return status for Runtime Phase next tick. | `ClosingCompletionStatus` |

## Failure Modes

- missing session summary：產生 terminal finalization error，不呼叫 adapter。
- malformed Super Chat summary：跳過該 item 並記錄 redacted error，不阻斷其他 item。
- MemoriaCore final message timeout：回傳 retryable closing status。
- MemoriaCore terminal error：可依 policy 以 system closing summary 完成 finalization。
- duplicate closing command：不得重複 acknowledgement 或 final message。
- crash/restart during closing：以 storage 中 finalization status 恢復。
- raw Super Chat payload、hidden prompt、raw MemoriaCore request 不得進入 display event。

## Test Strategy

- entry context tests：manual close、duration、stream ended、plan completed。
- Super Chat tests：多筆 pending acknowledgement、malformed item、duplicate handling。
- final request tests：policy enabled/disabled final message。
- adapter result tests：success、timeout retryable、terminal fallback。
- idempotency tests：duplicate closing command 不重複 finalization。
- recovery tests：incomplete closing restart 後可恢復。
- completion tests：complete status 讓 Runtime Phase 進入 `ended`。
- redaction tests：display event 不含 hidden/raw payload。

## Open Questions

- final closing message 是否第一版必須呼叫 MemoriaCore，或允許 system-only finalization，需在 implementation plan 鎖定。
- Super Chat acknowledgement 是否需要逐筆 response，需與 Chat Display UI 和 Storage 設計對齊。
- 是否需要實際停止 YouTube livestream，需由未來 stream-control module 或 YouTube Adapter 擴充決定。
