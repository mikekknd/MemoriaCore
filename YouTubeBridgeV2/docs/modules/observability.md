# Observability Module Design

## Purpose

Observability 負責 V2 的 phase transition log、adapter request summary、error classification、trace lookup 與 correlation id。它讓人類與 agent 能診斷流程，而不暴露 hidden prompt、raw Topic Pack 或 raw external payload。

## Ownership

- 擁有 V2 transition/event/error trace 的 public/private 邊界。
- 擁有 correlation id 的傳遞規則。
- 擁有 adapter summary 與 redaction policy。
- 擁有 operator-visible diagnostics 的最小資料形狀。
- 不擁有 runtime decision 或 adapter retry。

## Inputs

- `PhaseTransition` metadata。
- LiveEpisodePlan turn summary。
- Aftertalk cue summary。
- MemoriaCore/YouTube adapter summary。
- API error summary 與 security event。

## Outputs

- `TransitionLogEntry`
- `AdapterTraceSummary`
- `RuntimeErrorEvent`
- `CorrelationMetadata`
- operator-visible diagnostic summary。

## Dependencies

- Runtime Phase 提供 transition metadata。
- Adapters 提供 redacted request/response summary。
- Storage 保存 trace/log entries。
- Server/API Surface 與 UI 消費 diagnostic events。
- Security module 定義 secret/raw payload redaction。

## Out Of Scope

- LLM prompt construction。
- storage schema 實作。
- adapter HTTP retry。
- UI layout。
- phase transition 判斷。

## Public Entrypoints

本階段只描述 planned public contracts，不宣稱 source symbol 已存在。

- `TransitionLogEntry`
- `AdapterTraceSummary`
- `RuntimeErrorEvent`
- `CorrelationMetadata`
- `DiagnosticEvent`

## Redaction Rules

| Input | Public Diagnostic Output |
| --- | --- |
| phase transition | phase, reason, timestamp, correlation id, compact metadata |
| MemoriaCore request | adapter name, request type, duration, status, redacted ids |
| YouTube event | normalized event type, event id, author display summary, no raw payload |
| error | stable class, retryable flag, public message, correlation id |
| security failure | permission group, route group, sanitized reason |
| hidden prompt/raw Topic Pack/raw FactCard | excluded |

Observability may retain private references only when Storage defines an explicit private trace field.

## Failure Modes

- logging failure 不得阻斷 runtime phase decision。
- correlation id 缺失時建立 diagnostic warning，不偽造外部 trace。
- raw prompt、raw Topic Pack、raw MemoriaCore payload 不得出現在 public diagnostics。
- adapter error 必須分類為 timeout、transport、auth、invalid response 或 unexpected。
- security error 不得包含 secret 或 raw header。

## Test Strategy

- transition log shape tests。
- adapter summary redaction tests。
- hidden prompt exclusion tests。
- correlation id propagation tests。
- error classification tests。
- logging failure isolation tests。
- UI diagnostic event contract tests。

## Open Questions

- trace 是否寫入檔案、DB 或雙寫，需與 Storage implementation plan 對齊。
- correlation id 由 Server/API Surface 或 Observability 產生，需在 runtime service 設計決定。
- 與既有 `runtime/llm_trace.jsonl` 的關係需在實作階段重新確認。
