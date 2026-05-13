# YouTube Adapter Module Design

## Purpose

YouTube Adapter 負責 YouTube live chat polling、event normalization、Super Chat metadata、stream status、pagination、rate limit、retry 與 adapter error classification。它把 YouTube 外部狀態轉成 V2 normalized events，但不決定節目流程。

## Ownership

- 擁有 YouTube API request/response mapping。
- 擁有 live chat event normalization。
- 擁有 Super Chat metadata extraction。
- 擁有 polling cursor、pagination 與 duplicate handling。
- 擁有 stream ended/status summary。
- 不擁有 phase transition、MemoriaCore calls、UI rendering 或 storage transaction。

## Inputs

- connector config 與 credential reference。
- live chat id、video id、polling cursor。
- rate limit/retry policy。
- current session id 與 correlation metadata。

## Outputs

- `NormalizedYouTubeEvent`
- `YouTubePollingCursor`
- `SuperChatMetadata`
- `YouTubeStreamStatus`
- `YouTubeAdapterError`

## Dependencies

- Access Control / Security 提供 credential boundary。
- Storage 保存 normalized events 與 cursor。
- Server/API Surface 或 runtime service 觸發 polling。
- Observability 消費 adapter summary。
- Chat Display UI 消費 display-safe event metadata。

## Out Of Scope

- Runtime Phase decision。
- LiveEpisodePlan advancement。
- MemoriaCore request。
- Closing script generation。
- UI rendering。
- storage implementation。

## Public Entrypoints

本階段的 pure adapter contract 已由 `YouTubeBridgeV2/adapters/youtube.py` 提供。真實 YouTube transport/client 仍不在此模組內。

- `NormalizedYouTubeEvent`
- `YouTubePollingCursor`
- `SuperChatMetadata`
- `YouTubeStreamStatus`
- `YouTubeAdapterError`
- `normalize_youtube_event(raw_event, *, cursor=None)`
- `extract_super_chat_metadata(raw_event)`
- `classify_youtube_error(error)`

## Runtime Handoff

Wave 3A runtime handoff:
- Runtime 只呼叫 `normalize_youtube_event(...)` 取得 display-safe payload，不直接接收 raw YouTube payload 到 public event。
- `HANDLE_YOUTUBE_EVENT` 只處理單一 live chat event normalization + runtime input handoff；polling cursor / duplicate event recovery / scheduler ingestion 保留給 3B/3D。

Wave 3B cursor handoff:
- Runtime 可把 storage/payload cursor 傳入 `normalize_youtube_event(..., cursor=cursor)`，讓 duplicate event id 變成 `duplicate=True`、`should_dispatch=False`。
- Adapter 仍不保存 cursor；cursor persistence 屬於 Storage/Runtime boundary。

## Polling Rules

| Situation | Required Behavior |
| --- | --- |
| first poll | Use configured live chat id/video id and empty cursor. |
| next page token present | Persist cursor for next poll. |
| duplicate event id | Skip duplicate and emit idempotent summary. |
| Super Chat event | Extract display-safe metadata; private reference persistence is deferred to Storage/Runtime integration. |
| live ended | Emit `YouTubeStreamStatus.ended`; Runtime Application Service decides next command. |
| transient failure | Return retryable adapter error and backoff hint. |
| auth failure | Return terminal adapter error. |

## Failure Modes

- transient API failure 回傳 retryable adapter error。
- auth failure 回傳 terminal adapter error。
- duplicate event 由 event id/cursor 去重。
- pagination cursor 缺失時回傳 recoverable polling state。
- live ended 狀態輸出 stream status，不直接改 phase。
- raw YouTube payload 不得進入 display event。

## Test Strategy

- event normalization tests。
- pagination cursor tests。
- duplicate event tests。
- Super Chat metadata tests。
- stream ended status tests。
- retry classification tests。
- no phase side effect tests。
- redaction tests：display/public metadata 不含 raw payload。

## Deferred Questions

- YouTube API client 版本與 authentication source 需在 adapter implementation plan 鎖定。
- polling interval 與 backoff policy 需與 runtime service 設計對齊。
- Super Chat 全量欄位是否完整保存需與 Storage 設計對齊。
