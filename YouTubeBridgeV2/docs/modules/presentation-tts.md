# Presentation/TTS Module Design

## Purpose

Presentation/TTS 負責消費已完成的 interaction 或 response event，產生可選的視覺展示與語音輸出。它是 event consumer，不參與 phase decision。

## Ownership

- 擁有 presentation/TTS queue 與 delivery request。
- 擁有 ack、timeout、disabled behavior 與 output metadata。
- 擁有 display-safe metadata 的產生或補充。
- 擁有 TTS provider error 的分類。
- 不擁有 LLM generation、phase transition、storage ownership 或 YouTube polling。

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
- Storage 可保存 delivery result。
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

本階段只描述 planned public contracts，不宣稱 source symbol 已存在。

- `PresentationEvent`
- `TTSRequest`
- `DeliveryAck`
- `DeliveryTimeoutResult`
- `PresentationDisplayMetadata`

## Delivery Rules

| Situation | Required Behavior |
| --- | --- |
| TTS enabled | Build `TTSRequest` from display-safe response event. |
| TTS disabled | Skip voice request and keep presentation display metadata. |
| ack received | Mark delivery success once. |
| ack duplicated | Return idempotent success summary. |
| timeout reached | Mark timeout without changing runtime phase. |
| malformed event | Produce skipped result with sanitized reason. |
| provider error | Return delivery error summary and correlation id. |

## Failure Modes

- TTS disabled 時不建立 delivery request，但保留 display metadata。
- provider timeout 產生 timeout result，不改 phase。
- ack 遺失時標記 pending/timeout，不重複播放。
- malformed response event 產生 safe skipped result。
- metadata 不得包含 hidden prompt、raw adapter payload 或 secret。

## Test Strategy

- event consumption tests。
- queue ordering tests。
- ack success tests。
- timeout tests。
- disabled TTS tests。
- metadata redaction tests。
- no phase side effect tests。

## Open Questions

- 是否第一版只做 metadata queue，不接真實 TTS provider，需在 implementation plan 決定。
- ack 來源是 browser、TTS worker 或 server callback，需與 Server/API Surface 設計對齊。
- display metadata 欄位需與 Chat Display UI 對齊。
