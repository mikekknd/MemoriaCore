# Runtime Automation Pause Resume Controls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成 roadmap `4D`：提供 operator-only pause/resume/safety controls，讓 runtime automation 可 durable 暫停或停用，並確保 scheduler/recovery cycles 不在 paused/disabled session 上產生 side effects。

**Architecture:** 新增 `RuntimeCommandType.UPDATE_AUTOMATION_CONTROL` 與 `POST /v2/sessions/{session_id}/automation-control` route。Route 只做 request/security mapping；RuntimeApplicationService 經 RuntimeStoragePort 將 sanitized `automation_control` 存到 session metadata；query/read model 回傳 control status；automation refs 從 top-level 或 metadata control flags 解析 `automation_enabled` / `automation_paused` 以 skip tick/recovery dispatch。

**Tech Stack:** Python 3.13、FastAPI/Pydantic、pytest、existing StorageManager durable backend、existing automation cycle helpers。

---

## Scope

Roadmap item：`4D：operator pause/resume/safety controls`

完成條件：

- 新增 operator-only API：`POST /v2/sessions/{session_id}/automation-control`。
- Request 支援：
  - `command_id`
  - `enabled` optional bool
  - `paused` optional bool
  - `reason` optional text
- 至少要提供 `enabled` 或 `paused` 其中之一。
- Runtime service 不直接改 phase；只保存 sanitized automation control metadata 並發出 operator control event。
- Query service `get_session(...)` / `get_phase(...)` 回傳 public `automation_control`。
- Automation tick cycle 與 recovery cycle 會解析 session record metadata 的 `automation_control.enabled/paused`，paused/disabled 時不 dispatch。
- Main app security 只允許 operator 寫 control；observer/display 被拒。

不包含：

- 不做完整 operator console UI；Wave 5 再做。
- 不啟動 background process 或 thread。
- 不變更 roadmap checkbox。

## File Structure

- Modify: `YouTubeBridgeV2/runtime/application_service.py`
  - 新增 command type 與 service method。
- Modify: `YouTubeBridgeV2/storage/runtime_store.py`
  - 新增 durable metadata update method。
- Modify: `YouTubeBridgeV2/query_service.py`
  - expose `automation_control` in status/phase.
- Modify: `YouTubeBridgeV2/runtime/automation.py`
  - parse nested metadata `automation_control` for tick/recovery refs。
- Modify: `YouTubeBridgeV2/server/routes.py`
  - add request model and route。
- Modify: `YouTubeBridgeV2/server/main_security.py`
  - add route permission id。
- Modify tests:
  - `tests/youtubebridge_v2/test_runtime_application_service.py`
  - `tests/youtubebridge_v2/test_runtime_automation.py`
  - `tests/youtubebridge_v2/test_server_api_surface.py`
  - `tests/youtubebridge_v2/test_main_app_security.py`
  - `tests/youtubebridge_v2/test_real_storage_integration.py`
- Modify docs:
  - `YouTubeBridgeV2/docs/modules/runtime-application-service.md`
  - `YouTubeBridgeV2/docs/modules/server-api-surface.md`
  - `YouTubeBridgeV2/docs/api-reference-index.md`
  - `YouTubeBridgeV2/docs/architecture-index.md`

---

### Task 1: Red Runtime/Automation Control Tests

**Files:**
- Modify: `tests/youtubebridge_v2/test_runtime_application_service.py`
- Modify: `tests/youtubebridge_v2/test_runtime_automation.py`

- [ ] **Step 1: Add runtime service control test**

Append to runtime application service tests:

```python
def test_update_automation_control_persists_sanitized_operator_control():
    storage = FakeStorage()
    service = _service(storage=storage)

    result = service.update_automation_control(
        _command(
            RuntimeCommandType.UPDATE_AUTOMATION_CONTROL,
            command_id="cmd-control",
            payload={
                "paused": True,
                "enabled": False,
                "reason": "operator pause",
                "raw_payload": {"token": "must not leak"},
            },
        ),
        BASE_NOW,
    )

    assert result.status == "ok"
    assert storage.snapshot_metadata["automation_control"] == {
        "enabled": False,
        "paused": True,
        "reason": "operator pause",
        "updated_at": BASE_NOW.isoformat(),
    }
    assert result.events[0].event_type == "automation_control_updated"
    assert result.events[0].payload == {
        "operator_controls": {
            "automation_control": {
                "enabled": False,
                "paused": True,
                "reason": "operator pause",
            }
        }
    }
    _assert_no_forbidden_payload(result)
```

Update `FakeStorage` with:

```python
self.snapshot_metadata = {}

def update_automation_control(self, command, now):
    control = {
        "enabled": command.payload.get("enabled", True),
        "paused": command.payload.get("paused", False),
        "reason": command.payload.get("reason", ""),
        "updated_at": now.isoformat(),
    }
    self.snapshot_metadata["automation_control"] = control
    return self.snapshot
```

- [ ] **Step 2: Add automation metadata control skip test**

Append to automation tests:

```python
def test_scheduler_cycles_respect_nested_automation_control_metadata():
    service = FakeRuntimeService()
    session = {
        "session_id": "session-paused",
        "current_phase": "planned_show",
        "metadata": {
            "automation_control": {
                "enabled": True,
                "paused": True,
            }
        },
    }

    tick = dispatch_scheduler_cycle(service, [session], NOW)
    recovery = dispatch_scheduler_recovery_cycle(service, [session], NOW)

    assert tick.skipped[0].skip_reason == "automation_paused"
    assert recovery.skipped[0].skip_reason == "automation_paused"
    assert service.calls == []
```

- [ ] **Step 3: Run red tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_runtime_application_service.py::test_update_automation_control_persists_sanitized_operator_control tests\youtubebridge_v2\test_runtime_automation.py::test_scheduler_cycles_respect_nested_automation_control_metadata -q
```

Expected:

- Fails because `UPDATE_AUTOMATION_CONTROL` / `update_automation_control(...)` and nested metadata parsing are missing.

### Task 2: Implement Runtime Control Command

**Files:**
- Modify: `YouTubeBridgeV2/runtime/application_service.py`
- Modify: `YouTubeBridgeV2/storage/runtime_store.py`
- Modify: `YouTubeBridgeV2/query_service.py`

- [ ] **Step 1: Add command type and service method**

In `RuntimeCommandType` add:

```python
UPDATE_AUTOMATION_CONTROL = "update_automation_control"
```

Add method to `RuntimeApplicationService`:

```python
def update_automation_control(
    self,
    command: RuntimeCommand,
    now: datetime,
) -> RuntimeServiceResult:
    existing = self._existing_result(command)
    if existing is not None:
        return existing

    snapshot = self._storage.update_automation_control(command, now)
    control = _automation_control_summary(command.payload)
    event = self._event(
        event_type="automation_control_updated",
        command=command,
        phase=snapshot.current_phase,
        payload={"operator_controls": {"automation_control": control}},
    )
    self._persist_event(event)
    result = RuntimeServiceResult(
        status="ok",
        session_id=command.session_id,
        phase=snapshot.current_phase,
        events=[event],
        errors=[],
        correlation_id=_correlation_id(command),
    )
    self._save_result(command, result)
    return result
```

Add helper:

```python
def _automation_control_summary(payload: dict[str, object]) -> dict[str, object]:
    summary: dict[str, object] = {}
    if "enabled" in payload:
        summary["enabled"] = bool(payload["enabled"])
    if "paused" in payload:
        summary["paused"] = bool(payload["paused"])
    if "reason" in payload:
        summary["reason"] = str(payload.get("reason") or "")
    return _sanitize_public_payload(summary)
```

- [ ] **Step 2: Add RuntimeStoragePort control update**

Add:

```python
def update_automation_control(self, command: RuntimeCommand, now: datetime):
    payload = _object_to_dict(command.payload)
    current = {}
    if hasattr(self._storage_manager, "get_v2_session"):
        record = self._storage_manager.get_v2_session(command.session_id) or {}
        current = _object_to_dict(_object_to_dict(record.get("metadata", {})).get("automation_control", {}))
    control = {
        "enabled": bool(payload.get("enabled", current.get("enabled", True))),
        "paused": bool(payload.get("paused", current.get("paused", False))),
        "reason": str(payload.get("reason", current.get("reason", "")) or ""),
        "updated_at": now.isoformat(),
    }
    return self._update_session(command.session_id, {"automation_control": control})
```

- [ ] **Step 3: Add query projection**

Add to `get_session(...)` and `get_phase(...)` response:

```python
"automation_control": _automation_control(record),
```

Add helper:

```python
def _automation_control(record: dict[str, object]) -> dict[str, object]:
    metadata = record.get("metadata", {})
    if isinstance(metadata, dict):
        raw = metadata.get("automation_control", {})
        if isinstance(raw, dict):
            return _sanitize_public_payload(
                {
                    "enabled": bool(raw.get("enabled", True)),
                    "paused": bool(raw.get("paused", False)),
                    "reason": str(raw.get("reason", "") or ""),
                }
            )
    return {"enabled": True, "paused": False, "reason": ""}
```

- [ ] **Step 4: Update automation ref coercion**

In `automation.py`, add helper:

```python
def _automation_flags(data: object) -> tuple[bool, bool]:
    mapping = data if isinstance(data, dict) else vars(data)
    metadata = mapping.get("metadata", {})
    control = metadata.get("automation_control", {}) if isinstance(metadata, dict) else {}
    enabled = mapping.get("automation_enabled", control.get("enabled", True))
    paused = mapping.get("automation_paused", control.get("paused", False))
    return bool(enabled), bool(paused)
```

Use it in `_session_ref(...)` and `_recovery_ref(...)`.

- [ ] **Step 5: Run green tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_runtime_application_service.py::test_update_automation_control_persists_sanitized_operator_control tests\youtubebridge_v2\test_runtime_automation.py::test_scheduler_cycles_respect_nested_automation_control_metadata -q
```

Expected:

- Tests pass.

### Task 3: Red/Green API and Security Tests

**Files:**
- Modify: `tests/youtubebridge_v2/test_server_api_surface.py`
- Modify: `tests/youtubebridge_v2/test_main_app_security.py`

- [ ] **Step 1: Add server route delegation test**

Add FakeRuntimeService method:

```python
def update_automation_control(self, command, now):
    self.calls.append(("update_automation_control", command, now))
    return _result(command, phase=LiveSessionPhase.PLANNED_SHOW)
```

Append test:

```python
def test_automation_control_delegates_to_runtime_service():
    service = FakeRuntimeService()
    client = TestClient(_app(runtime_service=service))

    response = client.post(
        "/v2/sessions/session-1/automation-control",
        json={
            "command_id": "cmd-control",
            "paused": True,
            "enabled": False,
            "reason": "operator pause",
        },
    )

    assert response.status_code == 200
    assert service.calls[0][0] == "update_automation_control"
    command = service.calls[0][1]
    assert command.command_type == RuntimeCommandType.UPDATE_AUTOMATION_CONTROL
    assert command.payload == {
        "enabled": False,
        "paused": True,
        "reason": "operator pause",
    }
```

Append validation test:

```python
def test_automation_control_requires_enabled_or_paused():
    service = FakeRuntimeService()
    client = TestClient(_app(runtime_service=service))

    response = client.post(
        "/v2/sessions/session-1/automation-control",
        json={"command_id": "cmd-control", "reason": "missing flags"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert service.calls == []
```

- [ ] **Step 2: Add main app security assertions**

Extend observer/display security tests with `/automation-control` POST and expect 403.

- [ ] **Step 3: Run red tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_server_api_surface.py::test_automation_control_delegates_to_runtime_service tests\youtubebridge_v2\test_server_api_surface.py::test_automation_control_requires_enabled_or_paused tests\youtubebridge_v2\test_main_app_security.py -q
```

Expected:

- Route tests fail until endpoint/security mapping exists.

- [ ] **Step 4: Implement route and security**

In routes:

```python
class AutomationControlRequest(BaseModel):
    command_id: str = Field(..., min_length=1)
    enabled: bool | None = None
    paused: bool | None = None
    reason: str | None = None
```

Add endpoint:

```python
@router.post("/sessions/{session_id}/automation-control", response_model=None)
def update_automation_control_endpoint(...):
    body = _validate_body(AutomationControlRequest, raw_body)
    if isinstance(body, JSONResponse):
        return body
    if body.enabled is None and body.paused is None:
        return _validation_error_response(raw_body)
    command = _command(
        command_id=body.command_id,
        session_id=session_id,
        command_type=RuntimeCommandType.UPDATE_AUTOMATION_CONTROL,
        now=now,
        permission_context=_request_permission_context(request),
        payload={
            "enabled": body.enabled,
            "paused": body.paused,
            "reason": body.reason,
        },
    )
    return _call_runtime(runtime_service, "update_automation_control", command, now)
```

In `main_security.py`:

```python
if child == "automation-control" and method == "POST":
    return PermissionGroup.OPERATOR, "automation_control"
```

- [ ] **Step 5: Run API/security tests green**

Run same command as Step 3; expected pass.

### Task 4: Real Storage Integration Test

**Files:**
- Modify: `tests/youtubebridge_v2/test_real_storage_integration.py`

- [ ] **Step 1: Add integration test**

Append:

```python
def test_automation_control_pause_survives_restart_and_blocks_cycles(tmp_path):
    storage = _storage_manager(tmp_path)
    composition, _planned_show, _aftertalk, _closing = _composition(storage)
    client = TestClient(create_v2_app(composition, now_provider=lambda: STARTED_AT))
    _create_session(client, "session-control")

    result = composition.runtime_service.update_automation_control(
        RuntimeCommand(
            command_id="cmd-control",
            session_id="session-control",
            command_type=RuntimeCommandType.UPDATE_AUTOMATION_CONTROL,
            issued_at=STARTED_AT,
            payload={"paused": True, "reason": "operator pause"},
        ),
        STARTED_AT,
    )

    restarted_storage = _storage_manager(tmp_path)
    restarted_composition, _planned2, _aftertalk2, _closing2 = _composition(
        restarted_storage,
    )
    tick = dispatch_scheduler_cycle(
        restarted_composition.runtime_service,
        restarted_composition.storage.list_recoverable_sessions(),
        STARTED_AT + timedelta(seconds=10),
    )
    recovery = dispatch_scheduler_recovery_cycle(
        restarted_composition.runtime_service,
        restarted_composition.storage.list_recoverable_sessions(),
        STARTED_AT + timedelta(seconds=20),
    )
    session = V2QueryService(restarted_storage).get_session("session-control")

    assert result.events[0].event_type == "automation_control_updated"
    assert tick.skipped[0].skip_reason == "automation_paused"
    assert recovery.skipped[0].skip_reason == "automation_paused"
    assert session["automation_control"]["paused"] is True
```

Add missing imports as needed.

- [ ] **Step 2: Run integration test**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_real_storage_integration.py::test_automation_control_pause_survives_restart_and_blocks_cycles -q
```

Expected:

- Pass.

### Task 5: Documentation

**Files:**
- Modify: `YouTubeBridgeV2/docs/modules/runtime-application-service.md`
- Modify: `YouTubeBridgeV2/docs/modules/server-api-surface.md`
- Modify: `YouTubeBridgeV2/docs/api-reference-index.md`
- Modify: `YouTubeBridgeV2/docs/architecture-index.md`

- [ ] **Step 1: Update runtime docs**

Add Wave 4D status:

```markdown
Wave 4D operator controls:
- `RuntimeCommandType.UPDATE_AUTOMATION_CONTROL` 與 `RuntimeApplicationService.update_automation_control(...)` 保存 durable `automation_control` metadata，不直接改 phase。
- Automation tick/recovery refs 會讀取 top-level 或 metadata `automation_control.enabled/paused`，disabled/paused sessions 不 dispatch side effects。
- `POST /v2/sessions/{session_id}/automation-control` 是 operator-only safety control；完整 UI 留給 Wave 5。
```

- [ ] **Step 2: Update API docs**

Add route and sources for:

- `POST /v2/sessions/{session_id}/automation-control`
- `AutomationControlRequest`
- `RuntimeCommandType.UPDATE_AUTOMATION_CONTROL`
- `RuntimeApplicationService.update_automation_control`

- [ ] **Step 3: Update architecture status**

Add:

```markdown
## Integration Wave 4D 狀態

- [x] Operator safety control：`POST /v2/sessions/{session_id}/automation-control` 可 durable 設定 enabled/paused/reason。
- [x] Automation gating：tick/recovery cycles 會尊重 `automation_control.enabled/paused`，paused/disabled 不 dispatch runner side effects。
- [x] Security boundary：automation control route 是 operator-only；observer/display 無法寫入。
- [x] Scope boundary：完整 operator console UI 留給 Wave 5。
```

### Task 6: Verification and Commit

**Files:**
- Code, tests, docs, plan above.

- [ ] **Step 1: Run focused verification**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_runtime_automation.py tests\youtubebridge_v2\test_runtime_application_service.py tests\youtubebridge_v2\test_server_api_surface.py tests\youtubebridge_v2\test_main_app_security.py tests\youtubebridge_v2\test_real_storage_integration.py -q
```

Expected:

- Focused control/security suites pass.

- [ ] **Step 2: Run full roadmap verification**

Run:

```powershell
python -m pytest tests\youtubebridge_v2 -q
git diff --check
```

Expected:

- Full V2 suite passes.
- `git diff --check` exits 0. CRLF warnings are acceptable if there are no whitespace errors.

- [ ] **Step 3: Commit exact files**

Run:

```powershell
git add YouTubeBridgeV2\runtime\application_service.py YouTubeBridgeV2\storage\runtime_store.py YouTubeBridgeV2\query_service.py YouTubeBridgeV2\runtime\automation.py YouTubeBridgeV2\server\routes.py YouTubeBridgeV2\server\main_security.py tests\youtubebridge_v2\test_runtime_application_service.py tests\youtubebridge_v2\test_runtime_automation.py tests\youtubebridge_v2\test_server_api_surface.py tests\youtubebridge_v2\test_main_app_security.py tests\youtubebridge_v2\test_real_storage_integration.py YouTubeBridgeV2\docs\modules\runtime-application-service.md YouTubeBridgeV2\docs\modules\server-api-surface.md YouTubeBridgeV2\docs\api-reference-index.md YouTubeBridgeV2\docs\architecture-index.md YouTubeBridgeV2\docs\implementation-plans\runtime-automation-pause-resume-controls.md
git diff --cached --check
git commit -m "feat: add runtime automation safety controls"
```

Expected:

- Commit succeeds.

## Self-Review

- Spec coverage: 4D pause/resume/safety controls are covered by operator-only API, durable metadata, query projection, and automation cycle skip behavior.
- Placeholder scan: no `TBD`, `TODO`, or vague implementation placeholders remain.
- Type consistency: route uses `RuntimeCommandType.UPDATE_AUTOMATION_CONTROL`; service delegates to `RuntimeStoragePort.update_automation_control`; automation reads `automation_control` metadata.
