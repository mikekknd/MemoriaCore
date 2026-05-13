# Server/API Surface Module Design

## Purpose

Server/API Surface 負責提供 V2 的 HTTP/SSE 入口，供後台控制 UI、直播 Chat 顯示、observer 與外部工具使用。它接收 request、做基本 request/response mapping，並把行為委派給 runtime application service。

## Ownership

- 擁有 V2 HTTP endpoint 與 SSE event stream 的 public contract。
- 擁有 request/response envelope、狀態碼與錯誤回應形狀。
- 擁有 operator console 與 chat display 的 API 需求整理。
- 不擁有 phase decision、storage transaction、adapter transport 或 auth policy 細節。

## Inputs

- operator API request：session create、plan bind/import、runtime tick、manual close、aftertalk policy update。
- read API request：phase status、plan status、event history。
- presentation/TTS delivery request：queue read、ack 與 timeout mutation。
- SSE subscription request：operator stream、display stream、observer stream。
- access metadata：由 Security module 判斷後附加的 permission context。

## Outputs

- HTTP response body：session/status/control result。
- HTTP response body：TTS delivery queue、ack 或 timeout public result。
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

Wave 2B 已將主 FastAPI app 的 `/v2` routes 接到真 `StorageManager` durable composition；standalone `create_v2_app(...)` 仍可在測試中使用注入式 composition。Wave 2C 已將主 app production `/v2` API/SSE 升級為 loopback + prefs-backed API key permission matrix，`/v2/static` 不受此限制。

Final Hardening startup/shutdown validation 使用 `tests/youtubebridge_v2/test_main_app_lifecycle.py` 驗證 production main app lifespan：startup 後 `/v2` routes 與 `/v2/static` 可用，shutdown 會 stop bot managers 並 await 已取消 background tasks。此測試不啟動真 8088；若需要 live 8088 smoke，必須依 repo 規則以前景 `start.bat` 視窗啟動。

- `POST /v2/sessions`
- `GET /v2/sessions/{session_id}`
- `POST /v2/sessions/{session_id}/plan`
- `GET /v2/sessions/{session_id}/phase`
- `POST /v2/sessions/{session_id}/aftertalk-policy`
- `POST /v2/sessions/{session_id}/manual-close`
- `POST /v2/sessions/{session_id}/tick`
- `POST /v2/sessions/{session_id}/youtube-events`
- `GET /v2/sessions/{session_id}/events`
- `GET /v2/sessions/{session_id}/operator-stream`
- `GET /v2/sessions/{session_id}/display-stream`
- `GET /v2/sessions/{session_id}/tts-queue`
- `POST /v2/sessions/{session_id}/tts-deliveries/{delivery_id}/ack`
- `POST /v2/sessions/{session_id}/tts-deliveries/{delivery_id}/timeout`
- `GET /v2/api-keys`
- `POST /v2/api-keys`
- `DELETE /v2/api-keys/{key_fingerprint}`
- `list_api_keys_endpoint`
- `create_api_key_endpoint`
- `delete_api_key_endpoint`
- `create_session_endpoint`
- `get_session_endpoint`
- `bind_plan_endpoint`
- `get_phase_endpoint`
- `update_aftertalk_policy_endpoint`
- `manual_close_endpoint`
- `tick_session_endpoint`
- `ingest_youtube_event_endpoint`
- `get_session_events_endpoint`
- `operator_stream_endpoint`
- `display_stream_endpoint`
- `get_tts_queue_endpoint`
- `ack_tts_delivery_endpoint`
- `timeout_tts_delivery_endpoint`

## Endpoint Boundary Rules

| Endpoint Group | Required Behavior |
| --- | --- |
| session commands | Validate request and delegate typed command to Runtime Application Service. |
| phase/status reads | Read via runtime service or query service, not direct storage internals. |
| manual close | Create command; do not mutate phase in route handler. |
| runtime tick | Create `RuntimeCommandType.TICK` and delegate to Runtime Application Service; route does not run adapters directly. |
| YouTube event ingestion | Validate request and delegate `RuntimeCommandType.HANDLE_YOUTUBE_EVENT`; route does not call YouTube adapter directly. |
| aftertalk policy update | Validate policy and delegate command. |
| automation control | Validate operator safety controls and delegate `RuntimeCommandType.UPDATE_AUTOMATION_CONTROL`; route does not mutate runtime state directly. |
| API key management | Read/write `StorageManager` prefs only through the API key config helpers; public response exposes fingerprint/prefix and permission group only. |
| TTS queue read | Read via `V2QueryService.get_tts_queue(...)`; route does not inspect storage internals. |
| TTS ack/timeout | Delegate to StorageManager-like delivery methods, sanitize public result, and never mutate runtime phase from route handler. |
| operator stream | May expose operator-safe diagnostics and controls state. |
| display stream | Must be display-safe and read-only. |
| errors | Return sanitized stable error code and correlation id. |
| missing session | Return `404` with `session_not_found` and `query-<session_id>` correlation id. |

Wave 6A display stream events 會先通過 display contract normalizer，再進入 SSE
encoding。Event history 保持 public audit projection；display stream 則是 Chat
Display UI 使用的可渲染 projection。

## Main App Auth Requirements

| Endpoint Group | Required Permission |
| --- | --- |
| `GET /v2/api-keys` | `operator` |
| `POST /v2/api-keys` | `operator` |
| `DELETE /v2/api-keys/{key_fingerprint}` | `operator` |
| `POST /v2/sessions` | `operator` |
| `POST /v2/sessions/{session_id}/plan` | `operator` |
| `POST /v2/sessions/{session_id}/aftertalk-policy` | `operator` |
| `POST /v2/sessions/{session_id}/automation-control` | `operator` |
| `POST /v2/sessions/{session_id}/manual-close` | `operator` |
| `POST /v2/sessions/{session_id}/tick` | `operator` |
| `POST /v2/sessions/{session_id}/youtube-events` | `operator` |
| `GET /v2/sessions/{session_id}/tts-queue` | `observer` or `operator` |
| `POST /v2/sessions/{session_id}/tts-deliveries/{delivery_id}/ack` | `operator` |
| `POST /v2/sessions/{session_id}/tts-deliveries/{delivery_id}/timeout` | `operator` |
| `GET /v2/sessions/{session_id}` | `observer` or `operator` |
| `GET /v2/sessions/{session_id}/phase` | `observer` or `operator` |
| `GET /v2/sessions/{session_id}/events` | `observer` or `operator` |
| `GET /v2/sessions/{session_id}/operator-stream` | `observer` or `operator` |
| `GET /v2/sessions/{session_id}/display-stream` | `display` or `operator` |
| `GET /v2/static/*` | 公開 static asset |

Loopback request 視為 `operator`。Non-loopback request 必須透過 `x-youtubebridgev2-api-key`、`x-api-key` 或 `Authorization: Bearer <key>` 提供有效 API key。
Mutating routes 會把 Security module 解析出的 permission context 放入 `RuntimeCommand.permission_context`，讓 runtime/audit/observability 能保留 caller metadata；standalone `create_v2_app(...)` 未掛主 app middleware 時此欄位可為 `None`。

## Failure Modes

- invalid request body 回傳 sanitized validation error。
- unauthorized 或 forbidden 由 Security module 決定。
- service command failure 回傳 stable error code，不暴露 hidden payload。
- SSE client disconnect 不影響 runtime state。
- route 不得直接呼叫 adapter 或改 phase。
- TTS ack/timeout route 不得改 phase；missing delivery 回傳 sanitized not-found。
- display stream 不得暴露 operator-only control metadata。

## Test Strategy

- endpoint contract tests：request/response shape。
- delegation tests：routes 只呼叫 runtime service。
- SSE shape tests：operator/display event payload。
- auth boundary tests：permission context 由 Security module 提供。
- error response tests：sanitized error body。
- side effect tests：routes 不直接呼叫 MemoriaCore、YouTube 或 storage internals。
- YouTube ingestion fake-backed vertical tests：API route -> runtime -> storage/query/SSE，不接真 YouTube transport。
- TTS queue/ack/timeout tests：queue 讀取、operator-only mutation、observer/display permission boundary、timeout no phase side effect。
- import boundary tests：route 不直接 import adapter/storage。

## Open Questions

- application service 的檔案與 class 名稱需在 runtime implementation plan 鎖定。
- SSE 是否使用單一路由加 query scope 或分開路由，需與 UI 設計對齊。
- API key rotation policy 目前以 create/upsert + revoke 表達；是否要加入到期日或命名 label 留待後續 hardening。
