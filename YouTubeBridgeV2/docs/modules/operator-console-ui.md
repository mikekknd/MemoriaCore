# Operator Console UI Module Design

## Purpose

Operator Console UI 負責後台操作者介面，讓操作者檢視 V2 session 狀態、LiveEpisodePlan 進度、Aftertalk 開關、剩餘時間、closing 狀態、錯誤與手動控制入口。

## Ownership

- 擁有後台資訊架構與操作入口。
- 擁有 phase/status/error 的 operator-facing 呈現。
- 擁有 Aftertalk policy toggle 與 manual close 控制 UI。
- 擁有 API/SSE 消費方式與 UI state mapping。
- 不直接推進 phase、不呼叫 adapter、不寫 storage。

## Inputs

- phase status API/SSE event。
- durable session public status (`GET /v2/sessions/{session_id}`)，包含 `public_summary`、`automation_control` 與 request `permission_group`。
- LiveEpisodePlan progress summary。
- Aftertalk policy/status。
- duration summary 與 closing status。
- runtime error/diagnostic event。
- operator action result。

## Outputs

- operator action request：create session、plan bind/import、tick、aftertalk policy update、manual close。
- visible status model。
- disabled/enabled control state。
- operator error banner。

## Dependencies

- Server/API Surface 提供 endpoints 與 SSE。
- Access Control / Security 定義 operator permission。
- Observability 提供 diagnostic event。
- Runtime modules 提供 public status summary，但 UI 不直接依賴 runtime internals。

## Out Of Scope

- Chat Display UI rendering。
- runtime phase decision。
- MemoriaCore/YouTube transport。
- storage transaction。
- TTS/presentation output。

## Public Entrypoints

本階段 UI-facing contracts 已由 `YouTubeBridgeV2/static/operator-console/` 實作。

- Served entrypoint: `/v2/static/operator-console/index.html`
- Initial status source: `GET /v2/sessions/{session_id}` durable session status。
- Main app wiring: `api/main.py` includes the V2 router and serves `/v2/static`.
- `OperatorSessionStatusView`
- `OperatorControlAction`
- `AftertalkPolicyControl`
- `ManualCloseCommand`
- `OperatorDiagnosticBanner`
- `renderOperatorConsole(view)`
- `loadOperatorStatus({sessionId, fetchImpl})`
- `connectOperatorStream({sessionId, eventSourceFactory, onStatus, onStale})`
- `initOperatorConsoleI18n(i18n)`
- `mountOperatorConsole({root, sessionId, fetchImpl, initialStatus})`

## UI State Rules

| State | Required UI Behavior |
| --- | --- |
| `planned_show` | Show plan progress, current turn summary, aftertalk policy, manual close. |
| `aftertalk` | Show aftertalk active state, remaining time, manual close. |
| `closing` | Disable destructive controls, show closing progress and finalization status. |
| `ended` | Show final summary and read-only diagnostics. |
| API action in flight | Disable the triggering control and keep status visible. |
| SSE stale/disconnected | Show stale indicator and retry status. |
| display-only permission | Hide operator controls entirely. |
| Aftertalk policy update | 成功後重新讀 `GET /v2/sessions/{session_id}`，以 durable status 作為畫面真相來源。 |

## Failure Modes

- API failure 時保持既有狀態並顯示 error banner。
- Aftertalk policy update 成功後必須重新讀 `GET /v2/sessions/{session_id}`，不靠 optimistic local patch 作為最終狀態。
- manual close in-flight 時控制項 disabled，避免重複提交。
- display-only permission 不顯示 operator controls。
- unknown phase 顯示 recoverable diagnostic，不自行改 phase。
- SSE disconnect 時顯示 stale indicator。
- hidden prompt/raw payload 不得顯示。
- UI action 只送出 `/v2/sessions`、`/v2/sessions/{session_id}/plan`、`/v2/sessions/{session_id}/tick`、`/v2/sessions/{session_id}/aftertalk-policy` 與 `/v2/sessions/{session_id}/manual-close` request envelope，不直接 import runtime、呼叫 adapter 或寫 storage。

## Test Strategy

- phase rendering tests。
- Aftertalk toggle tests。
- remaining time display tests。
- manual close action tests。
- disabled state tests。
- error banner tests。
- permission boundary tests。
- no direct adapter/storage call tests。

## Open Questions

- 前端技術是否沿用純 HTML/JS 或新建框架，需在 UI implementation plan 決定。
- operator console 是否與 chat display 同頁或分離，需與實際直播操作流程對齊。
- SSE reconnect 策略需與 Server/API Surface 設計對齊。
