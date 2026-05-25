# YouTube Event API Ingestion Path Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成 roadmap `3D`：提供 operator-only YouTube event HTTP ingestion path，將外部或測試工具送入的 YouTube event 交給既有 runtime normalization、cursor persistence 與 storage path。

**Architecture:** 本階段只做 API ingestion path，不建立 scheduler/tick loop，避免跨到 Wave 4。`YouTubeBridgeV2/server/routes.py` 只驗證 request shape、建立 `RuntimeCommandType.HANDLE_YOUTUBE_EVENT`，再委派 `RuntimeApplicationService.handle_youtube_event(...)`；route 不直接 import YouTube adapter、不碰 storage、不改 phase。

**Tech Stack:** Python 3.13、FastAPI、Pydantic request model、pytest、existing `RuntimeApplicationService.handle_youtube_event(...)`、existing V2 security middleware。

---

## Scope

Roadmap item：`3D：YouTube event API 或 scheduler ingestion path`

本 plan 選擇 API path，完成條件：

- 新增 `POST /v2/sessions/{session_id}/youtube-events`。
- Request body 必須包含 `command_id` 與 `youtube_event`。
- Request 可選帶入 `polling_cursor` 與 `page_info`，沿用 3B runtime cursor contract。
- Route 建立 `RuntimeCommandType.HANDLE_YOUTUBE_EVENT`，呼叫 `runtime_service.handle_youtube_event(command, now)`。
- Response 使用既有 `_service_result_body(...)` sanitize 流程，不外洩 raw payload、token、authorization、access token。
- Main app security 將 endpoint 設為 `operator` only；observer/display key 不可 ingest。
- Standalone route tests 與 main-app security tests 都覆蓋此 endpoint。

不包含：

- YouTube polling transport 或 Google API client。
- background scheduler / tick loop。
- 實際 YouTube API reply、acknowledgement write-back 或 stream status polling。
- Operator Console UI button。

## File Structure

- Modify: `YouTubeBridgeV2/server/routes.py`
  - 新增 `YouTubeEventIngestRequest` model。
  - 新增 `ingest_youtube_event_endpoint(...)`。
  - 新增 `__all__` export。
- Modify: `YouTubeBridgeV2/server/main_security.py`
  - 將 `/v2/sessions/{session_id}/youtube-events` `POST` 對應到 `operator` route id。
- Modify: `YouTubeBridgeV2/server/security.py`
  - 將 `ingest_youtube_event` 加入 operator allowed actions 與 route action mapping。
- Modify: `tests/youtubebridge_v2/test_server_api_surface.py`
  - 新增 route delegation、validation、sanitization tests。
- Modify: `tests/youtubebridge_v2/test_main_app_security.py`
  - 新增 operator/observer/display permission boundary 與 permission context test。
- Modify docs:
  - `YouTubeBridgeV2/docs/modules/server-api-surface.md`
  - `YouTubeBridgeV2/docs/modules/youtube-adapter.md`
  - `YouTubeBridgeV2/docs/api-reference-index.md`
  - `YouTubeBridgeV2/docs/architecture-index.md`

## API Contract

Request:

```json
{
  "command_id": "cmd-youtube-event-1",
  "youtube_event": {
    "id": "yt-evt-1",
    "snippet": {
      "type": "textMessageEvent",
      "publishedAt": "2026-05-12T08:10:00Z",
      "displayMessage": "Hello runtime",
      "textMessageDetails": {"messageText": "Hello runtime"}
    },
    "authorDetails": {"displayName": "Mika", "channelId": "channel-1"}
  },
  "polling_cursor": {
    "live_chat_id": "live-chat-1",
    "next_page_token": "page-1",
    "polling_interval_millis": 1500,
    "seen_event_ids": []
  },
  "page_info": {
    "next_page_token": "page-2",
    "polling_interval_millis": 2500
  }
}
```

Runtime command payload:

```python
{
    "youtube_event": body.youtube_event,
    "polling_cursor": body.polling_cursor,
    "page_info": body.page_info,
}
```

Optional keys with `None` are dropped by existing `_command(...)`.

---

### Task 1: Route Contract Red Tests

**Files:**
- Modify: `tests/youtubebridge_v2/test_server_api_surface.py`

- [ ] **Step 1: Extend fake runtime service**

Add this method to `FakeRuntimeService`:

```python
    def handle_youtube_event(self, command, now):
        self.calls.append(("handle_youtube_event", command, now))
        return _result(command, phase=LiveSessionPhase.PLANNED_SHOW)
```

- [ ] **Step 2: Add delegation test**

Add after `test_tick_session_delegates_to_runtime_service`:

```python
def test_ingest_youtube_event_delegates_to_runtime_service():
    service = FakeRuntimeService()
    client = TestClient(_app(runtime_service=service))

    response = client.post(
        "/v2/sessions/session-1/youtube-events",
        json={
            "command_id": "cmd-youtube-api",
            "youtube_event": {
                "id": "yt-evt-1",
                "snippet": {
                    "type": "textMessageEvent",
                    "displayMessage": "Hello runtime",
                    "textMessageDetails": {"messageText": "Hello runtime"},
                },
                "authorDetails": {"displayName": "Mika", "channelId": "channel-1"},
                "raw_payload": {"access_token": "must not leak"},
            },
            "polling_cursor": {
                "live_chat_id": "live-chat-1",
                "next_page_token": "page-1",
                "polling_interval_millis": 1500,
                "seen_event_ids": [],
            },
            "page_info": {
                "next_page_token": "page-2",
                "polling_interval_millis": 2500,
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert service.calls[0][0] == "handle_youtube_event"
    command = service.calls[0][1]
    assert command.command_type == RuntimeCommandType.HANDLE_YOUTUBE_EVENT
    assert command.command_id == "cmd-youtube-api"
    assert command.session_id == "session-1"
    assert command.payload["youtube_event"]["id"] == "yt-evt-1"
    assert command.payload["polling_cursor"]["live_chat_id"] == "live-chat-1"
    assert command.payload["page_info"]["next_page_token"] == "page-2"
    _assert_no_private_payload(response.json())
```

- [ ] **Step 3: Add validation test**

Add:

```python
def test_ingest_youtube_event_requires_event_payload():
    service = FakeRuntimeService()
    client = TestClient(_app(runtime_service=service))

    response = client.post(
        "/v2/sessions/session-1/youtube-events",
        json={"command_id": "cmd-youtube-api"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert service.calls == []
    _assert_no_private_payload(response.json())
```

- [ ] **Step 4: Run red route tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_server_api_surface.py::test_ingest_youtube_event_delegates_to_runtime_service tests\youtubebridge_v2\test_server_api_surface.py::test_ingest_youtube_event_requires_event_payload -q
```

Expected before implementation:

- Delegation test fails with `404 Not Found` or missing route.
- Validation test fails because route does not exist.

### Task 2: Route Implementation

**Files:**
- Modify: `YouTubeBridgeV2/server/routes.py`

- [ ] **Step 1: Add request model**

Add below `TickRequest`:

```python
class YouTubeEventIngestRequest(BaseModel):
    command_id: str = Field(..., min_length=1)
    youtube_event: dict[str, object]
    polling_cursor: dict[str, object] | None = None
    page_info: dict[str, object] | None = None
```

- [ ] **Step 2: Add endpoint**

Add after `tick_session_endpoint(...)`:

```python
@router.post("/sessions/{session_id}/youtube-events", response_model=None)
def ingest_youtube_event_endpoint(
    session_id: str,
    request: Request,
    raw_body: object = Body(...),
    runtime_service: object = Depends(get_runtime_service),
    now: datetime = Depends(get_now),
) -> dict[str, object] | JSONResponse:
    """Ingest one YouTube event by delegating to runtime service."""

    body = _validate_body(YouTubeEventIngestRequest, raw_body)
    if isinstance(body, JSONResponse):
        return body
    command = _command(
        command_id=body.command_id,
        session_id=session_id,
        command_type=RuntimeCommandType.HANDLE_YOUTUBE_EVENT,
        now=now,
        permission_context=_request_permission_context(request),
        payload={
            "youtube_event": body.youtube_event,
            "polling_cursor": body.polling_cursor,
            "page_info": body.page_info,
        },
    )
    return _call_runtime(runtime_service, "handle_youtube_event", command, now)
```

- [ ] **Step 3: Export endpoint**

Add to `__all__`:

```python
    "ingest_youtube_event_endpoint",
```

- [ ] **Step 4: Run route tests green**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_server_api_surface.py -q
```

Expected after implementation:

- All server API surface tests pass.

### Task 3: Main App Security Boundary

**Files:**
- Modify: `YouTubeBridgeV2/server/main_security.py`
- Modify: `YouTubeBridgeV2/server/security.py`
- Modify: `tests/youtubebridge_v2/test_main_app_security.py`

- [ ] **Step 1: Extend security fake service**

Add to `CapturingRuntimeService`:

```python
    def handle_youtube_event(self, command, now):
        self.commands.append(command)
        return {
            "status": "ok",
            "session_id": command.session_id,
            "phase": LiveSessionPhase.PLANNED_SHOW,
            "events": [],
            "errors": [],
            "correlation_id": f"runtime-{command.command_id}",
        }
```

- [ ] **Step 2: Add permission context test**

Add after `test_main_app_v2_tick_command_receives_api_key_permission_context`:

```python
def test_main_app_v2_youtube_event_command_receives_operator_permission_context(
    tmp_path,
    monkeypatch,
):
    storage = _storage_manager(tmp_path)
    _save_api_keys(storage)
    api_main = _install_test_storage(monkeypatch, storage)
    service = CapturingRuntimeService()
    monkeypatch.setitem(
        api_main.app.dependency_overrides,
        api_main.youtubebridge_v2_routes.get_runtime_service,
        lambda: service,
    )
    client = _remote_client(api_main.app)

    response = client.post(
        "/v2/sessions/session-sec/youtube-events",
        headers={"x-youtubebridgev2-api-key": OPERATOR_KEY},
        json={
            "command_id": "cmd-capture-youtube",
            "youtube_event": {"id": "yt-evt-1", "snippet": {}, "authorDetails": {}},
        },
    )

    assert response.status_code == 200
    assert len(service.commands) == 1
    permission = service.commands[0].permission_context
    assert permission is not None
    assert permission.auth_method == "api_key"
    assert permission.permission_group == PermissionGroup.OPERATOR
    assert permission.is_loopback is False
    assert "ingest_youtube_event" in permission.allowed_actions
```

- [ ] **Step 3: Extend observer/display denial tests**

In `test_main_app_v2_observer_key_can_read_status_events_and_operator_stream_only`, add:

```python
    ingest_response = client.post(
        "/v2/sessions/observer-session/youtube-events",
        headers={"x-youtubebridgev2-api-key": OBSERVER_KEY},
        json={
            "command_id": "cmd-observer-youtube",
            "youtube_event": {"id": "yt-evt-1", "snippet": {}, "authorDetails": {}},
        },
    )
```

and assert:

```python
    _assert_security_error(ingest_response, status_code=403, code="forbidden")
```

In `test_main_app_v2_display_key_can_read_display_stream_only`, add the same request with `DISPLAY_KEY`, then assert forbidden.

- [ ] **Step 4: Run red security tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_main_app_security.py::test_main_app_v2_youtube_event_command_receives_operator_permission_context tests\youtubebridge_v2\test_main_app_security.py::test_main_app_v2_observer_key_can_read_status_events_and_operator_stream_only tests\youtubebridge_v2\test_main_app_security.py::test_main_app_v2_display_key_can_read_display_stream_only -q
```

Expected before security implementation:

- Operator request may be `403` because `ingest_youtube_event` is not an allowed action.
- Observer/display ingestion must be denied after route/security is explicit.

- [ ] **Step 5: Implement route requirement**

In `YouTubeBridgeV2/server/main_security.py`, add to `_session_child_requirement(...)`:

```python
    if child == "youtube-events" and method == "POST":
        return PermissionGroup.OPERATOR, "youtube_event_ingest"
```

- [ ] **Step 6: Implement action mapping**

In `YouTubeBridgeV2/server/security.py`, add `"ingest_youtube_event"` to operator allowed actions:

```python
            "ingest_youtube_event",
```

Add route mappings:

```python
    "youtube_event_ingest": "ingest_youtube_event",
    "youtube_events": "ingest_youtube_event",
```

- [ ] **Step 7: Run security tests green**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_main_app_security.py -q
```

Expected after implementation:

- All main app security tests pass.

### Task 4: Vertical Slice Regression

**Files:**
- Modify: `tests/youtubebridge_v2/test_integration_vertical_slice.py`

- [ ] **Step 1: Add API-ingestion vertical slice test**

Add a test that creates a standalone V2 app with in-memory storage, creates a session, binds a minimal plan if the existing helpers require it, then posts one `youtube-events` request and verifies the event appears in `GET /events` as a normalized public YouTube event.

Use existing helpers in the file; if no helper exists, add this compact event body:

```python
raw_event = {
    "id": "yt-evt-api-1",
    "snippet": {
        "type": "textMessageEvent",
        "publishedAt": "2026-05-12T08:10:00Z",
        "displayMessage": "Hello from API",
        "textMessageDetails": {"messageText": "Hello from API"},
    },
    "authorDetails": {
        "displayName": "Mika",
        "channelId": "channel-1",
        "isChatModerator": True,
    },
    "raw_payload": {"access_token": "must not leak"},
}
```

Expected assertions:

```python
assert response.status_code == 200
assert response.json()["status"] == "ok"
events = events_response.json()["events"]
assert any(
    event["event_id"] == "yt-evt-api-1"
    and event["event_type"] == "youtube_text_message"
    for event in events
)
_assert_no_private_payload(response.json())
_assert_no_private_payload(events_response.json())
```

- [ ] **Step 2: Run vertical slice red/green**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_integration_vertical_slice.py -q
```

Expected after route/security implementation:

- Vertical slice passes and demonstrates API route to runtime/storage/read API path.

### Task 5: Documentation

**Files:**
- Modify: `YouTubeBridgeV2/docs/modules/server-api-surface.md`
- Modify: `YouTubeBridgeV2/docs/modules/youtube-adapter.md`
- Modify: `YouTubeBridgeV2/docs/api-reference-index.md`
- Modify: `YouTubeBridgeV2/docs/architecture-index.md`

- [ ] **Step 1: Update server API module**

In `Public Entrypoints`, add:

```markdown
- `POST /v2/sessions/{session_id}/youtube-events`
- `ingest_youtube_event_endpoint`
```

In `Endpoint Boundary Rules`, add:

```markdown
| YouTube event ingestion | Validate request and delegate `RuntimeCommandType.HANDLE_YOUTUBE_EVENT`; route does not call YouTube adapter directly. |
```

In `Main App Auth Requirements`, add:

```markdown
| `POST /v2/sessions/{session_id}/youtube-events` | `operator` |
```

- [ ] **Step 2: Update YouTube adapter module**

Add under Wave 3 status:

```markdown
Wave 3D API ingestion:
- `POST /v2/sessions/{session_id}/youtube-events` is the first ingestion path for externally supplied YouTube events.
- The route delegates to runtime service; adapter normalization still lives in `YouTubeBridgeV2/adapters/youtube.py`.
- Scheduler/polling transport remains out of scope until Wave 4/runtime automation or a later YouTube transport item.
```

- [ ] **Step 3: Update API reference**

Add a Server/API Surface entry:

```markdown
### Ingest YouTube Event Endpoint

Purpose:
Accept one operator-supplied YouTube event payload and delegate it to `RuntimeApplicationService.handle_youtube_event(...)`.

Route:
- `POST /v2/sessions/{session_id}/youtube-events`

Request:
- `command_id`
- `youtube_event`
- optional `polling_cursor`
- optional `page_info`

Returns:
- Runtime service result body with sanitized public events/errors.

Source:
- `YouTubeBridgeV2/server/routes.py::ingest_youtube_event_endpoint`
```

- [ ] **Step 4: Update architecture status**

Add:

```markdown
## Integration Wave 3D 狀態

- [x] API ingestion path：`POST /v2/sessions/{session_id}/youtube-events` 可將 operator-supplied YouTube event 送入 runtime。
- [x] Runtime handoff：route 建立 `RuntimeCommandType.HANDLE_YOUTUBE_EVENT`，沿用 3A/3B normalization、cursor 與 storage path。
- [x] Scope boundary：本階段不建立 scheduler/polling transport、不直接呼叫 YouTube API。
```

- [ ] **Step 5: Run docs sanity search**

Run:

```powershell
rg -n "youtube-events|ingest_youtube_event_endpoint|Integration Wave 3D|youtube_event_ingest" YouTubeBridgeV2\docs YouTubeBridgeV2\server
```

Expected:

- Matches include route implementation, security route id, API reference, server API module, YouTube adapter module, and architecture status.

### Task 6: Final Verification and Commit

**Files:**
- All files modified above.

- [ ] **Step 1: Run focused verification**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_server_api_surface.py -q
python -m pytest tests\youtubebridge_v2\test_main_app_security.py -q
python -m pytest tests\youtubebridge_v2\test_runtime_application_service.py -q
python -m pytest tests\youtubebridge_v2\test_storage.py -q
python -m pytest tests\youtubebridge_v2\test_youtube_adapter.py -q
```

Expected:

- All focused suites pass.

- [ ] **Step 2: Run full roadmap verification**

Run:

```powershell
python -m pytest tests\youtubebridge_v2 -q
git diff --check
```

Expected:

- Full V2 suite passes.
- `git diff --check` has exit code 0. CRLF warnings are acceptable if there are no whitespace errors.

- [ ] **Step 3: Run boundary scan**

Run:

```powershell
rg -n "^\s*(from|import)\s+(sqlite3|aiosqlite|YouTubeBridge(\.|\s|$)|googleapiclient|requests)" YouTubeBridgeV2\server YouTubeBridgeV2\runtime YouTubeBridgeV2\adapters tests\youtubebridge_v2\test_server_api_surface.py tests\youtubebridge_v2\test_main_app_security.py
```

Expected:

- No matches for new route/security changes. Existing allowed stdlib/FastAPI imports are not relevant.

- [ ] **Step 4: Commit exact files**

Run:

```powershell
git add YouTubeBridgeV2\server\routes.py YouTubeBridgeV2\server\main_security.py YouTubeBridgeV2\server\security.py tests\youtubebridge_v2\test_server_api_surface.py tests\youtubebridge_v2\test_main_app_security.py tests\youtubebridge_v2\test_integration_vertical_slice.py YouTubeBridgeV2\docs\modules\server-api-surface.md YouTubeBridgeV2\docs\modules\youtube-adapter.md YouTubeBridgeV2\docs\api-reference-index.md YouTubeBridgeV2\docs\architecture-index.md YouTubeBridgeV2\docs\implementation-plans\youtube-event-api-ingestion-path.md
git diff --cached --check
git commit -m "feat: add YouTube event ingestion API"
```

Expected:

- Commit succeeds.

## Self-Review

- Spec coverage: 3D asks for API or scheduler ingestion path; this plan implements API ingestion only and explicitly defers scheduler/polling transport to avoid crossing Wave 4.
- Placeholder scan: no `TBD`, `TODO`, or vague "handle edge cases" steps remain.
- Type consistency: route model uses `youtube_event`, `polling_cursor`, and `page_info`, matching `RuntimeApplicationService.handle_youtube_event(...)` payload handling from 3A/3B.
