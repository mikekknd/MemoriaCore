# Server/API Surface Module Design

## Purpose

Server/API Surface 負責提供 V2 的 HTTP/SSE 入口，供後台控制 UI、直播 Chat 顯示、observer 與外部工具使用。它接收 request、做基本 request/response mapping，並把行為委派給 runtime application service。

## Ownership

- 擁有 V2 HTTP endpoint 與 SSE event stream 的 public contract。
- 擁有 request/response envelope、狀態碼與錯誤回應形狀。
- 擁有 operator console 與 chat display 的 API 需求整理。
- 不擁有 phase decision、storage transaction、adapter transport 或 auth policy 細節。

## Inputs

- operator API request：session create、plan bind/import、manual close、aftertalk policy update。
- read API request：phase status、plan status、event history。
- SSE subscription request：operator stream、display stream、observer stream。
- access metadata：由 Security module 判斷後附加的 permission context。

## Outputs

- HTTP response body：session/status/control result。
- SSE events：phase update、interaction event、display event、error event。
- service command：交給 runtime application service 的 typed command。
- API error response：sanitized error shape。

## Dependencies

- Access Control / Security 定義 permission 與 auth requirement。
- Runtime Phase、LiveEpisodePlan Runner、Aftertalk、Storage 透過 application service 被呼叫。
- Observability 提供 correlation id 與 error summary。
- Operator Console UI 與 Chat Display UI 消費 API/SSE contract。

## Out Of Scope

- 實際 phase transition 判斷。
- MemoriaCore 或 YouTube adapter direct call。
- storage implementation。
- auth secret validation 細節。
- UI rendering。

## Public Entrypoints

本模組的 route contracts 已由 `YouTubeBridgeV2/server/routes.py` 實作。Routes 只做 request/response mapping 與 SSE envelope，所有 runtime 行為委派 runtime service 或 query service。

Wave 2B 已將主 FastAPI app 的 `/v2` routes 接到真 `StorageManager` durable composition；standalone `create_v2_app(...)` 仍可在測試中使用注入式 composition。主 app 的 production `/v2` API/SSE 受 loopback-only boundary 保護，`/v2/static` 不受此限制。

- `POST /v2/sessions`
- `GET /v2/sessions/{session_id}`
- `POST /v2/sessions/{session_id}/plan`
- `GET /v2/sessions/{session_id}/phase`
- `POST /v2/sessions/{session_id}/aftertalk-policy`
- `POST /v2/sessions/{session_id}/manual-close`
- `GET /v2/sessions/{session_id}/events`
- `GET /v2/sessions/{session_id}/operator-stream`
- `GET /v2/sessions/{session_id}/display-stream`
- `create_session_endpoint`
- `get_session_endpoint`
- `bind_plan_endpoint`
- `get_phase_endpoint`
- `update_aftertalk_policy_endpoint`
- `manual_close_endpoint`
- `get_session_events_endpoint`
- `operator_stream_endpoint`
- `display_stream_endpoint`

## Endpoint Boundary Rules

| Endpoint Group | Required Behavior |
| --- | --- |
| session commands | Validate request and delegate typed command to Runtime Application Service. |
| phase/status reads | Read via runtime service or query service, not direct storage internals. |
| manual close | Create command; do not mutate phase in route handler. |
| aftertalk policy update | Validate policy and delegate command. |
| operator stream | May expose operator-safe diagnostics and controls state. |
| display stream | Must be display-safe and read-only. |
| errors | Return sanitized stable error code and correlation id. |
| missing session | Return `404` with `session_not_found` and `query-<session_id>` correlation id. |

## Failure Modes

- invalid request body 回傳 sanitized validation error。
- unauthorized 或 forbidden 由 Security module 決定。
- service command failure 回傳 stable error code，不暴露 hidden payload。
- SSE client disconnect 不影響 runtime state。
- route 不得直接呼叫 adapter 或改 phase。
- display stream 不得暴露 operator-only control metadata。

## Test Strategy

- endpoint contract tests：request/response shape。
- delegation tests：routes 只呼叫 runtime service。
- SSE shape tests：operator/display event payload。
- auth boundary tests：permission context 由 Security module 提供。
- error response tests：sanitized error body。
- side effect tests：routes 不直接呼叫 MemoriaCore、YouTube 或 storage internals。

## Open Questions

- application service 的檔案與 class 名稱需在 runtime implementation plan 鎖定。
- SSE 是否使用單一路由加 query scope 或分開路由，需與 UI 設計對齊。
- API key / operator-display-observer header permission matrix 仍屬後續 security wave。
