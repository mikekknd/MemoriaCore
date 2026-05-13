# Presentation/TTS Module Design

## Purpose

Presentation/TTS 負責消費已完成的 interaction 或 response event，產生可選的視覺展示與語音輸出。它是 event consumer，不參與 phase decision。

## Ownership

- 擁有 presentation/TTS queue 與 delivery request。
- 擁有 ack、timeout、disabled behavior 與 output metadata。
- 擁有 display-safe metadata 的產生或補充。
- 不擁有 LLM generation、phase transition、storage ownership 或 YouTube polling。
- 第一版不擁有真實 TTS provider error 分類；provider adapter / integration layer 需先轉成 provider-neutral delivery status。

## Inputs

- character response event。
- system/display event。
- presentation metadata。
- TTS enabled/disabled policy。
- delivery ack 或 timeout event。

## Outputs

- `PresentationEvent`
- `TTSRequest`
- `DeliveryAck`
- `DeliveryTimeoutResult`
- `PresentationDisplayMetadata`

## Dependencies

- Chat Display UI 消費 presentation metadata。
- Storage 可保存 delivery queue、ack 與 timeout result。
- Observability 記錄 timeout/error summary。
- Server/API Surface 可提供 presentation status。
- Access Control / Security 定義可見 metadata。

## Out Of Scope

- phase transition。
- MemoriaCore or YouTube adapter calls。
- LLM content generation。
- operator controls。
- storage implementation。
- display UI layout。

## Public Entrypoints

本階段的 provider-neutral contract 已由 `YouTubeBridgeV2/presentation/tts.py` 提供。真實 TTS provider、browser playback callback 與 provider retry 仍由後續 integration layer 負責。

Wave 6C 將 Memoria runner 完成的 interaction 接成 display-safe presentation live
event。Runtime runner 會在 interaction persistence 後呼叫
`build_presentation_event(...)`，並 append 含有 `public_metadata.display_event` 的
`presentation_character_response` event；presentation 仍是 event consumer，不要求
phase transition。

Wave 6D 將 provider-neutral TTS delivery queue 接到 runtime/storage/API：
session metadata 內 `tts_policy.enabled == true` 時，Memoria runner 會在
presentation event append 後建立 pending `TTSRequest` delivery record；policy
缺漏或 disabled 時不建立 delivery。Delivery ack 與 timeout 是 public delivery
state，永遠不要求 runtime phase transition，也不代表真實 provider 已完成整合。

- `PresentationEvent`
- `TTSRequest`
- `DeliveryAck`
- `DeliveryTimeoutResult`
- `PresentationDisplayMetadata`
- `build_presentation_event(interaction)`
- `enqueue_tts_request(event, policy, *, queue=None)`
- `record_delivery_ack(delivery_id, *, delivery_state=None, acknowledged_at=None)`
- `record_delivery_timeout(delivery_id, *, timeout_seconds, delivery_state=None, metadata=None)`

## Delivery Rules

| Situation | Required Behavior |
| --- | --- |
| TTS enabled | Build `TTSRequest` from display-safe response event. |
| TTS disabled | Skip voice request and keep presentation display metadata. |
| ack received | Mark delivery success once. |
| ack duplicated | Return idempotent success summary. |
| timeout reached | Mark timeout without changing runtime phase. |
| malformed event | Produce skipped result with sanitized reason. |

## Failure Modes

- TTS disabled 時不建立 delivery request，但保留 display metadata。
- provider timeout 產生 timeout result，不改 phase。
- ack 遺失時標記 pending/timeout，不重複播放。
- ack 已成功後抵達的 timeout 視為 ignored timeout，不覆寫 delivered state。
- malformed response event 產生 safe skipped result。
- metadata 不得包含 hidden prompt、raw adapter payload 或 secret。
- runtime enqueue 只依 session `tts_policy` 與 display-safe presentation event 建立
  delivery，不讀 hidden prompt 或 raw Memoria payload。

## Test Strategy

- event consumption tests。
- queue ordering tests。
- ack success tests。
- timeout tests。
- disabled TTS tests。
- metadata redaction tests。
- no phase side effect tests。

## Deferred Questions

- Wave 6D ack/timeout 先由 Server/API Surface 的 operator route 回寫；browser 或
  TTS worker callback 仍留待 provider/E2E integration 對齊。
- display metadata 欄位需與 Chat Display UI 對齊。
- 真實 TTS provider error 分類需在 provider adapter / integration layer 設計時補上，再決定是否回寫本模組 public contract。
