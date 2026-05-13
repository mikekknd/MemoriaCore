# Chat Display UI Module Design

## Purpose

Chat Display UI 負責直播畫面使用的 chat 顯示介面。它呈現觀眾留言、角色發言、Super Chat、系統 phase 狀態、Aftertalk/closing 狀態，以及 presentation/TTS 可用的顯示 metadata。

## Ownership

- 擁有 livestream-facing display event rendering。
- 擁有 audience/character/system/Super Chat 的顯示分類。
- 擁有 display-only metadata 與 styling hints。
- 擁有不暴露 operator controls 的邊界。
- 不擁有 runtime control、phase decision 或 adapter calls。

## Inputs

- display SSE event stream。
- audience message event。
- character response event。
- Super Chat event。
- system phase status event。
- presentation/TTS metadata event。

## Outputs

- visible chat row model。
- display status banner。
- Super Chat visual metadata。
- role label and avatar reference。
- presentation/TTS display metadata pass-through。

## Dependencies

- Server/API Surface 提供 display stream。
- Access Control / Security 提供 display read-only permission。
- Storage/Observability 提供 event summaries。
- Presentation/TTS 消費或補充 display metadata。

## Out Of Scope

- operator controls。
- aftertalk policy toggle。
- manual close command。
- runtime phase transition。
- MemoriaCore/YouTube transport。
- storage writes。

## Public Entrypoints

本階段 display contracts 已由 `YouTubeBridgeV2/static/chat-display/` 實作。

Wave 6A 新增 server-side display event contract：display stream consumer 會收到
`display_contract_version: "v1"` 事件，且 `event_type` 會被正規化為
`audience_message`、`character_response`、`super_chat`、`system_state` 或
`closing_status`。Raw storage/operator fields 不進入 display contract。

Wave 6C 新增 `presentation_character_response` 作為 storage/source event name。
Display stream 會將它正規化為 `event_type: "character_response"`，並透過
`public_payload.presentation` 攜帶 display-safe voice/visual/subtitle metadata。
UI 繼續使用既有 character row 呈現，不呼叫 runtime control API。

Wave 6E 新增 display + TTS E2E verification：real storage/runtime tick 產生的
presentation event 會經 `/display-stream` 進入 `chat-display.js` renderer，驗證
character response、presentation metadata、private payload redaction 與同一回覆的
TTS queue/ack/timeout API 可一起通過。

- `DisplayMessageEvent`
- `DisplaySystemStateEvent`
- `DisplaySuperChatEvent`
- `DisplayCharacterResponseEvent`
- `DisplayPresentationMetadata`
- `renderDisplayEvent(event)`
- `renderDisplayEvents(events)`
- `renderChatDisplayShell({events, streamStatus})`
- `connectDisplayStream({sessionId, eventSourceFactory, onEvent, onStale})`
- `initChatDisplayI18n(i18n)`
- `mountChatDisplay({root, sessionId, eventSourceFactory, initialEvents})`

## Display Event Rules

| Event Type | Required Rendering |
| --- | --- |
| audience message | author display name, message text, timestamp, moderation/display flags |
| character response | character label, response text, phase badge, optional presentation metadata |
| Super Chat | amount/currency display metadata, message text, acknowledgement state if public |
| system phase state | compact phase/aftertalk/closing status banner |
| closing_status | compact closing status banner using existing closing module display-safe shape |
| presentation metadata | display-safe voice/visual state only |
| malformed event | safe fallback row and diagnostic marker |

Display stream 是可渲染 projection。Event history 仍是 public audit
projection，並可透過 `source_event_type` 保留 `youtube_super_chat` 這類來源
event name。

Display UI must never emit control requests or display raw hidden context.
When `sequence` is present on replay or initial display events, the UI sorts by sequence before rendering and preserves input order for events without sortable metadata.
Audience display flags are rendered only from an allowlist of public-safe flag names.

## Failure Modes

- malformed display event 顯示 safe fallback，不中斷整個畫面。
- unknown speaker 顯示 generic role label。
- SSE disconnect 顯示 reconnect/stale state。
- display permission 不得呼叫 control endpoints。
- hidden prompt/raw payload/operator-only metadata 不得顯示。
- Super Chat metadata 缺失時仍顯示基本訊息。
- Display UI 只連線 `/v2/sessions/{session_id}/display-stream`，不得呼叫 aftertalk/manual-close/operator control API。

## Test Strategy

- event rendering tests。
- role labeling tests。
- Super Chat metadata tests。
- aftertalk/closing status banner tests。
- display-only permission tests。
- malformed event fallback tests。
- no control API call tests。
- Wave 6B browser smoke：opt-in `tests/youtubebridge_v2/test_chat_display_browser_smoke.py` 會使用 live 8088 `/v2` server 驗證 desktop/mobile stream render、bounded shell、no private payload 與 no horizontal overflow。
- Wave 6E E2E tests：`tests/youtubebridge_v2/test_display_tts_e2e.py`
  會用 real V2 storage/runtime/display stream 驗證 presentation metadata 可被
  Chat Display renderer 呈現，並提供 skipped-by-default browser smoke。

## Open Questions

- 實際視覺樣式需在 UI implementation plan 或 mock 中鎖定。
- avatar/role color metadata 的來源需與 Storage 或 Presentation/TTS 對齊。
- 是否需要瀏覽器 screenshot smoke test，需依最終前端技術決定。
