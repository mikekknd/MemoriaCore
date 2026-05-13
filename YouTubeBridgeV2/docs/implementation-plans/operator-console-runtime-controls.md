# Operator Console Runtime Controls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成 roadmap `5B`：Operator Console 提供 create、bind plan、tick、manual close controls，所有操作都只送 `/v2` API request，不直接改 runtime state。

**Architecture:** 延續 5A 的 durable status dashboard。新增 small command classes 包裝 `POST /v2/sessions`、`POST /v2/sessions/{session_id}/plan`、`POST /v2/sessions/{session_id}/tick`，並把現有 manual close 放進同一個 operator controls panel。UI event handler 只負責讀取表單、送 API、再讀 durable status 或合併 command result；沒有 runtime/storage/adapters import。

**Tech Stack:** Plain HTML/CSS/ES module JavaScript、Node-based UI tests、FastAPI TestClient、pytest、existing V2 HTTP API。

---

## Scope

Roadmap item：`5B：create/bind/tick/manual-close controls`

完成條件：

- No-session Operator Console 會呈現 create session form，不只顯示 missing session error。
- Create form 呼叫 `POST /v2/sessions`，request body 包含：
  - `command_id`
  - `session_id`
  - `aftertalk_policy`
- Existing session operator controls 會呈現：
  - Bind plan JSON textarea + button -> `POST /v2/sessions/{session_id}/plan`
  - Tick button -> `POST /v2/sessions/{session_id}/tick`
  - Manual close button -> existing `POST /v2/sessions/{session_id}/manual-close`
- In-flight 狀態會 disable tick、bind plan、manual close、aftertalk toggle，避免重複提交。
- Bind plan JSON parse error 顯示 sanitized banner，不送 request。
- Main app smoke test 覆蓋 create -> bind -> tick -> manual close API dependency route 可由 UI 所需 endpoints served。
- Main app loopback/operator status response 會帶 `permission_group: "operator"`，讓 browser UI 能顯示 operator controls。
- 不新增或擴張 aftertalk policy behavior；現有 toggle 保持。
- 不新增 API key management UI；那是 5D。
- 不勾選 roadmap checkbox。

不包含：

- 不做 plan file upload/import picker；本波只提供 JSON textarea。
- 不做 API key CRUD。
- 不新增 background scheduler 或 direct runtime state mutation。

## File Structure

- Modify: `tests/youtubebridge_v2/test_operator_console_ui.py`
  - Add red tests for new command classes, rendered controls, no-session create form, in-flight disable, invalid JSON handling, and main app API dependency smoke.
- Modify: `YouTubeBridgeV2/static/operator-console/operator-console.js`
  - Add `CreateSessionCommand`, `BindPlanCommand`, `TickSessionCommand`.
  - Add no-session setup render path and create binding.
  - Expand existing operator controls with plan bind + tick controls.
  - Bind new controls to API calls and durable status reload.
- Modify: `YouTubeBridgeV2/static/operator-console/operator-console.css`
  - Add compact form/input/textarea/control group styles.
- Modify: `YouTubeBridgeV2/server/routes.py`
  - Add request permission projection to status/phase read models so the UI can decide whether to show operator controls.
- Modify: `static/locales/zh-TW.json`
  - Add labels for create, bind plan, tick, plan JSON, invalid JSON.
- Modify: `static/locales/en-US.json`
  - Add matching English labels.
- Modify: `YouTubeBridgeV2/docs/modules/operator-console-ui.md`
  - Document create/bind/tick/manual close controls.
- Modify: `YouTubeBridgeV2/docs/architecture-index.md`
  - Add Wave 5B status.
- Modify: `YouTubeBridgeV2/docs/api-reference-index.md`
  - Add new UI command classes to Operator Console UI concepts/source.

---

### Task 1: Red Tests For API Command Classes And Rendered Controls

**Files:**
- Modify: `tests/youtubebridge_v2/test_operator_console_ui.py`

- [ ] **Step 1: Add create session action test**

Add near existing action tests:

```python
def test_create_session_command_sends_create_request():
    result = _run_node_json(
        """
const calls = [];
const fetchImpl = async (url, options) => {
  calls.push({url, method: options.method, body: JSON.parse(options.body)});
  return {ok: true, json: async () => ({status: "ok", session_id: "session-1"})};
};
const response = await ui.CreateSessionCommand.send({
  sessionId: "session-1",
  aftertalkPolicy: "auto",
  fetchImpl,
  commandIdFactory: () => "cmd-create"
});
console.log(JSON.stringify({calls, response}));
"""
    )

    assert result["calls"] == [
        {
            "url": "/v2/sessions",
            "method": "POST",
            "body": {
                "command_id": "cmd-create",
                "session_id": "session-1",
                "aftertalk_policy": "auto",
            },
        }
    ]
```

- [ ] **Step 2: Add bind plan action test**

```python
def test_bind_plan_command_sends_plan_request_without_private_payload():
    result = _run_node_json(
        """
const calls = [];
const fetchImpl = async (url, options) => {
  calls.push({url, method: options.method, body: JSON.parse(options.body)});
  return {ok: true, json: async () => ({status: "ok"})};
};
const response = await ui.BindPlanCommand.send({
  sessionId: "session-1",
  plan: {
    plan_id: "plan-1",
    title: "Operator Plan",
    raw_payload: {token: "must not leak"},
    turns: []
  },
  fetchImpl,
  commandIdFactory: () => "cmd-bind"
});
console.log(JSON.stringify({calls, response}));
"""
    )

    assert result["calls"] == [
        {
            "url": "/v2/sessions/session-1/plan",
            "method": "POST",
            "body": {
                "command_id": "cmd-bind",
                "plan": {
                    "plan_id": "plan-1",
                    "title": "Operator Plan",
                    "turns": [],
                },
            },
        }
    ]
    _assert_no_private_payload(result)
```

- [ ] **Step 3: Add tick action test**

```python
def test_tick_session_command_sends_tick_request():
    result = _run_node_json(
        """
const calls = [];
const fetchImpl = async (url, options) => {
  calls.push({url, method: options.method, body: JSON.parse(options.body)});
  return {ok: true, json: async () => ({status: "ok", phase: "aftertalk"})};
};
const response = await ui.TickSessionCommand.send({
  sessionId: "session-1",
  fetchImpl,
  commandIdFactory: () => "cmd-tick"
});
console.log(JSON.stringify({calls, response}));
"""
    )

    assert result["calls"] == [
        {
            "url": "/v2/sessions/session-1/tick",
            "method": "POST",
            "body": {"command_id": "cmd-tick"},
        }
    ]
```

- [ ] **Step 4: Add rendered controls test**

```python
def test_operator_console_renders_runtime_control_inputs_for_operator():
    result = _run_node_json(
        """
const view = ui.OperatorSessionStatusView.fromStatus({
  session_id: "session-1",
  phase: "planned_show",
  permission_group: "operator"
});
const html = ui.renderOperatorConsole(view);
console.log(JSON.stringify({view, html}));
"""
    )

    assert 'data-testid="plan-json-input"' in result["html"]
    assert 'data-testid="bind-plan-button"' in result["html"]
    assert 'data-testid="tick-button"' in result["html"]
    assert 'data-testid="manual-close-button"' in result["html"]
```

- [ ] **Step 5: Add in-flight disabled assertions**

Extend `test_controls_disable_while_action_is_in_flight`:

```python
assert result["view"]["controls"]["tickDisabled"] is True
assert result["view"]["controls"]["bindPlanDisabled"] is True
assert 'data-testid="tick-button" disabled' in result["html"]
assert 'data-testid="bind-plan-button" disabled' in result["html"]
```

- [ ] **Step 6: Add no-session create form test**

Replace `test_missing_session_id_renders_diagnostic` body with:

```python
def test_missing_session_id_renders_create_session_controls():
    result = _run_node_json(
        """
const root = {innerHTML: "", querySelector: () => null};
ui.mountOperatorConsole({root});
console.log(JSON.stringify({html: root.innerHTML}));
"""
    )

    assert 'data-testid="create-session-form"' in result["html"]
    assert 'data-testid="create-session-id-input"' in result["html"]
    assert 'data-testid="create-session-button"' in result["html"]
    assert "session" in result["html"].lower()
```

- [ ] **Step 7: Add invalid plan JSON helper test**

Add this Node test if `parsePlanJsonForOperator` is exported:

```python
def test_parse_plan_json_for_operator_rejects_invalid_json_safely():
    result = _run_node_json(
        """
try {
  ui.parsePlanJsonForOperator("{bad json");
} catch (error) {
  console.log(JSON.stringify({message: error.message, error}));
}
"""
    )

    assert result["message"] == "invalid plan JSON"
    _assert_no_private_payload(result)
```

- [ ] **Step 8: Run red tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_operator_console_ui.py::test_create_session_command_sends_create_request tests\youtubebridge_v2\test_operator_console_ui.py::test_bind_plan_command_sends_plan_request_without_private_payload tests\youtubebridge_v2\test_operator_console_ui.py::test_tick_session_command_sends_tick_request tests\youtubebridge_v2\test_operator_console_ui.py::test_operator_console_renders_runtime_control_inputs_for_operator tests\youtubebridge_v2\test_operator_console_ui.py::test_missing_session_id_renders_create_session_controls -q
```

Expected: FAIL because the command classes and controls do not exist yet.

---

### Task 2: Green Command Classes And View Disabled State

**Files:**
- Modify: `YouTubeBridgeV2/static/operator-console/operator-console.js`

- [ ] **Step 1: Add create/bind/tick command classes**

Add after `AftertalkPolicyControl`:

```javascript
export class CreateSessionCommand {
  static action({
    sessionId,
    aftertalkPolicy = "auto",
    commandIdFactory = defaultCommandId,
  }) {
    return new OperatorControlAction({
      sessionId,
      endpoint: "/v2/sessions",
      body: {
        command_id: commandIdFactory("create-session"),
        session_id: sessionId,
        aftertalk_policy: aftertalkPolicy,
      },
    });
  }

  static send({
    sessionId,
    aftertalkPolicy = "auto",
    fetchImpl = globalThis.fetch,
    commandIdFactory,
  }) {
    return CreateSessionCommand.action({
      sessionId,
      aftertalkPolicy,
      commandIdFactory,
    }).send(fetchImpl);
  }
}

export class BindPlanCommand {
  static action({sessionId, plan, commandIdFactory = defaultCommandId}) {
    return new OperatorControlAction({
      sessionId,
      endpoint: `/v2/sessions/${encodeURIComponent(sessionId)}/plan`,
      body: {
        command_id: commandIdFactory("bind-plan"),
        plan,
      },
    });
  }

  static send({sessionId, plan, fetchImpl = globalThis.fetch, commandIdFactory}) {
    return BindPlanCommand.action({
      sessionId,
      plan,
      commandIdFactory,
    }).send(fetchImpl);
  }
}

export class TickSessionCommand {
  static action({sessionId, commandIdFactory = defaultCommandId}) {
    return new OperatorControlAction({
      sessionId,
      endpoint: `/v2/sessions/${encodeURIComponent(sessionId)}/tick`,
      body: {
        command_id: commandIdFactory("tick"),
      },
    });
  }

  static send({sessionId, fetchImpl = globalThis.fetch, commandIdFactory}) {
    return TickSessionCommand.action({
      sessionId,
      commandIdFactory,
    }).send(fetchImpl);
  }
}
```

- [ ] **Step 2: Add disabled state fields**

In `OperatorSessionStatusView.fromStatus`, update `controls`:

```javascript
controls: {
  aftertalkDisabled: !canControl || controlsDisabled,
  bindPlanDisabled: !canControl || controlsDisabled,
  manualCloseDisabled: !canControl || controlsDisabled,
  tickDisabled: !canControl || controlsDisabled,
},
```

- [ ] **Step 3: Add safe JSON parser**

Add near helpers:

```javascript
export function parsePlanJsonForOperator(raw) {
  try {
    const parsed = JSON.parse(String(raw || ""));
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new Error("invalid");
    }
    return sanitizePublicValue(parsed);
  } catch {
    throw new OperatorDiagnosticBanner({
      message: translate("invalid_plan_json", "invalid plan JSON"),
    });
  }
}
```

- [ ] **Step 4: Run command class focused tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_operator_console_ui.py::test_create_session_command_sends_create_request tests\youtubebridge_v2\test_operator_console_ui.py::test_bind_plan_command_sends_plan_request_without_private_payload tests\youtubebridge_v2\test_operator_console_ui.py::test_tick_session_command_sends_tick_request tests\youtubebridge_v2\test_operator_console_ui.py::test_parse_plan_json_for_operator_rejects_invalid_json_safely -q
```

Expected: PASS after command classes and parser exist.

---

### Task 3: Green Rendered Controls And Event Binding

**Files:**
- Modify: `YouTubeBridgeV2/static/operator-console/operator-console.js`
- Modify: `YouTubeBridgeV2/static/operator-console/operator-console.css`

- [ ] **Step 1: Expand operator controls HTML**

Replace `renderOperatorControls(view)` with:

```javascript
function renderOperatorControls(view) {
  const aftertalkChecked = view.aftertalkPolicy === "auto" ? " checked" : "";
  const aftertalkDisabled = view.controls.aftertalkDisabled ? " disabled" : "";
  const bindDisabled = view.controls.bindPlanDisabled ? " disabled" : "";
  const manualDisabled = view.controls.manualCloseDisabled ? " disabled" : "";
  const tickDisabled = view.controls.tickDisabled ? " disabled" : "";
  return `
    <section class="operator-controls" data-testid="operator-controls">
      <div class="control-row">
        <label class="toggle">
          <input data-testid="aftertalk-toggle" type="checkbox"${aftertalkChecked}${aftertalkDisabled}>
          <span>${escapeHtml(translate("aftertalk", "Aftertalk"))}</span>
        </label>
        <button data-testid="tick-button"${tickDisabled}>${escapeHtml(translate("tick", "Tick"))}</button>
        <button data-testid="manual-close-button"${manualDisabled}>${escapeHtml(translate("manual_close", "Manual Close"))}</button>
      </div>
      <div class="plan-bind-control">
        <label for="operatorPlanJson">${escapeHtml(translate("plan_json", "Plan JSON"))}</label>
        <textarea id="operatorPlanJson" data-testid="plan-json-input" rows="6" spellcheck="false"></textarea>
        <button data-testid="bind-plan-button"${bindDisabled}>${escapeHtml(translate("bind_plan", "Bind Plan"))}</button>
      </div>
    </section>
  `;
}
```

- [ ] **Step 2: Render no-session create controls**

Add:

```javascript
function renderSessionCreatePanel() {
  return `
    <section class="operator-console setup-console">
      ${new OperatorDiagnosticBanner({
        message: translate("missing_session_id", "Missing session_id"),
      }).render()}
      <header class="console-header">
        <div>
          <span class="eyeline">YouTubeBridgeV2</span>
          <h1>${escapeHtml(translate("title", "Operator Console"))}</h1>
        </div>
      </header>
      <form class="operator-controls setup-form" data-testid="create-session-form">
        <label>
          <span>${escapeHtml(translate("session", "Session"))}</span>
          <input data-testid="create-session-id-input" name="session_id" autocomplete="off">
        </label>
        <label>
          <span>${escapeHtml(translate("aftertalk", "Aftertalk"))}</span>
          <select data-testid="create-aftertalk-policy" name="aftertalk_policy">
            <option value="auto">auto</option>
            <option value="disabled">disabled</option>
          </select>
        </label>
        <button data-testid="create-session-button" type="submit">${escapeHtml(translate("create_session", "Create Session"))}</button>
      </form>
    </section>
  `;
}
```

Then in `mountOperatorConsole`, replace the no-session block:

```javascript
if (!sessionId) {
  target.innerHTML = renderSessionCreatePanel();
  bindSessionCreateControls(target, {fetchImpl});
  return;
}
```

- [ ] **Step 3: Bind create session controls**

Add:

```javascript
function bindSessionCreateControls(root, {fetchImpl}) {
  const form = root.querySelector("[data-testid='create-session-form']");
  if (!form) return;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = root.querySelector("[data-testid='create-session-id-input']");
    const policy = root.querySelector("[data-testid='create-aftertalk-policy']");
    const nextSessionId = String(input?.value || "").trim();
    if (!nextSessionId) {
      root.innerHTML = renderSessionCreatePanel();
      return;
    }
    try {
      await CreateSessionCommand.send({
        sessionId: nextSessionId,
        aftertalkPolicy: String(policy?.value || "auto"),
        fetchImpl,
      });
      const status = await loadOperatorStatus({sessionId: nextSessionId, fetchImpl});
      if (typeof history !== "undefined" && history.replaceState) {
        const url = new URL(location.href);
        url.searchParams.set("session_id", nextSessionId);
        history.replaceState({}, "", url);
      }
      mountOperatorConsole({root, sessionId: nextSessionId, fetchImpl, initialStatus: status});
    } catch (error) {
      root.innerHTML = `${new OperatorDiagnosticBanner({
        message: error.message || translate("request_failed", "request failed"),
      }).render()}${renderSessionCreatePanel()}`;
      bindSessionCreateControls(root, {fetchImpl});
    }
  });
}
```

- [ ] **Step 4: Bind bind-plan and tick controls**

In `bindOperatorControls`, add tick and bind sections before manual close:

```javascript
const tick = root.querySelector("[data-testid='tick-button']");
if (tick) {
  tick.addEventListener("click", async () => {
    render(status, {inFlightAction: "tick"});
    try {
      const next = await TickSessionCommand.send({sessionId, fetchImpl});
      render({...status, ...next});
    } catch (error) {
      render({...status, error});
    }
  });
}

const bindPlan = root.querySelector("[data-testid='bind-plan-button']");
if (bindPlan) {
  bindPlan.addEventListener("click", async () => {
    const input = root.querySelector("[data-testid='plan-json-input']");
    let plan;
    try {
      plan = parsePlanJsonForOperator(input?.value || "");
    } catch (error) {
      render({...status, error});
      return;
    }
    render(status, {inFlightAction: "bind_plan"});
    try {
      await BindPlanCommand.send({sessionId, plan, fetchImpl});
      const nextStatus = await loadOperatorStatus({sessionId, fetchImpl});
      render(nextStatus);
    } catch (error) {
      render({...status, error});
    }
  });
}
```

- [ ] **Step 5: Add CSS for compact form controls**

Append:

```css
.control-row,
.plan-bind-control,
.setup-form {
  display: grid;
  gap: 10px;
}

.control-row {
  align-items: center;
  grid-template-columns: minmax(140px, 1fr) auto auto;
}

.plan-bind-control {
  flex: 1;
}

.plan-bind-control label,
.setup-form label span {
  color: var(--text2);
  font-size: 12px;
}

input,
select,
textarea {
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 6px;
  color: var(--text);
  min-height: 34px;
  padding: 7px 9px;
}

textarea {
  min-height: 108px;
  resize: vertical;
  width: 100%;
}

.setup-form {
  grid-template-columns: minmax(180px, 1fr) minmax(140px, auto) auto;
}
```

Extend the mobile media query:

```css
.control-row,
.setup-form {
  grid-template-columns: 1fr;
}
```

- [ ] **Step 6: Run rendered controls tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_operator_console_ui.py::test_operator_console_renders_runtime_control_inputs_for_operator tests\youtubebridge_v2\test_operator_console_ui.py::test_controls_disable_while_action_is_in_flight tests\youtubebridge_v2\test_operator_console_ui.py::test_missing_session_id_renders_create_session_controls -q
```

Expected: PASS.

---

### Task 4: API Dependency Smoke And Locale/Docs Sync

**Files:**
- Modify: `tests/youtubebridge_v2/test_operator_console_ui.py`
- Modify: `static/locales/zh-TW.json`
- Modify: `static/locales/en-US.json`
- Modify: `YouTubeBridgeV2/docs/modules/operator-console-ui.md`
- Modify: `YouTubeBridgeV2/docs/architecture-index.md`
- Modify: `YouTubeBridgeV2/docs/api-reference-index.md`

- [ ] **Step 1: Add main app controls dependency smoke**

Extend `test_operator_console_api_dependencies_are_served_by_main_app` after status assertions:

```python
bind_response = client.post(
    "/v2/sessions/session-1/plan",
    json={
        "command_id": "cmd-bind",
        "plan": {
            "plan_id": "plan-ui",
            "title": "Operator UI Plan",
            "turns": [
                {
                    "id": "turn-1",
                    "purpose": "Verify operator UI plan bind route.",
                    "topic_cue": "UI route smoke.",
                    "speaker_policy": {
                        "type": "fixed",
                        "speaker_ids": ["host"],
                    },
                    "audience_insertion": {
                        "enabled": False,
                        "allow_super_chats": False,
                    },
                }
            ],
        },
    },
)
assert bind_response.status_code == 200

tick_response = client.post(
    "/v2/sessions/session-1/tick",
    json={"command_id": "cmd-tick"},
)
assert tick_response.status_code == 200

manual_close_response = client.post(
    "/v2/sessions/session-1/manual-close",
    json={"command_id": "cmd-close", "reason": "operator"},
)
assert manual_close_response.status_code == 200
```

- [ ] **Step 2: Add i18n keys**

Extend i18n key test with:

```python
"youtubebridge_v2.operator_console.create_session",
"youtubebridge_v2.operator_console.bind_plan",
"youtubebridge_v2.operator_console.tick",
"youtubebridge_v2.operator_console.plan_json",
"youtubebridge_v2.operator_console.invalid_plan_json",
```

Add to `zh-TW.json`:

```json
"youtubebridge_v2.operator_console.create_session": "建立 session",
"youtubebridge_v2.operator_console.bind_plan": "綁定企劃",
"youtubebridge_v2.operator_console.tick": "推進 tick",
"youtubebridge_v2.operator_console.plan_json": "企劃 JSON",
"youtubebridge_v2.operator_console.invalid_plan_json": "invalid plan JSON",
```

Add to `en-US.json`:

```json
"youtubebridge_v2.operator_console.create_session": "Create Session",
"youtubebridge_v2.operator_console.bind_plan": "Bind Plan",
"youtubebridge_v2.operator_console.tick": "Tick",
"youtubebridge_v2.operator_console.plan_json": "Plan JSON",
"youtubebridge_v2.operator_console.invalid_plan_json": "invalid plan JSON",
```

- [ ] **Step 3: Docs sync**

In `YouTubeBridgeV2/docs/modules/operator-console-ui.md`, update Outputs:

```markdown
- operator action request：create session、plan bind/import、tick、aftertalk policy update、manual close。
```

In Failure Modes, update the action endpoint line:

```markdown
- UI action 只送出 `/v2/sessions`、`/v2/sessions/{session_id}/plan`、`/v2/sessions/{session_id}/tick`、`/v2/sessions/{session_id}/aftertalk-policy` 與 `/v2/sessions/{session_id}/manual-close` request envelope，不直接 import runtime、呼叫 adapter 或寫 storage。
```

In `architecture-index.md`, after Wave 5A status add:

```markdown
## Integration Wave 5B 狀態

- [x] Create session control：no-session Operator Console 可送出 `POST /v2/sessions` 建立 V2 session。
- [x] Bind plan control：operator controls 可送出 sanitized plan JSON 到 `POST /v2/sessions/{session_id}/plan`。
- [x] Tick/manual close controls：operator controls 可呼叫 `POST /v2/sessions/{session_id}/tick` 與既有 manual close route。
- [x] Scope boundary：本階段不新增 API key management UI，也不擴張 aftertalk policy contract。
```

In API reference Operator Console UI concepts/source add:

```markdown
- `CreateSessionCommand`
- `BindPlanCommand`
- `TickSessionCommand`
```

and sources:

```markdown
- `YouTubeBridgeV2/static/operator-console/operator-console.js::CreateSessionCommand`
- `YouTubeBridgeV2/static/operator-console/operator-console.js::BindPlanCommand`
- `YouTubeBridgeV2/static/operator-console/operator-console.js::TickSessionCommand`
```

- [ ] **Step 4: Expose permission context to UI status reads**

Modify `YouTubeBridgeV2/server/routes.py` so `get_session_endpoint(...)` and `get_phase_endpoint(...)` accept `request: Request`, then wrap query-service results with:

```python
def _status_with_permission_context(body: object, request: Request) -> dict[str, object]:
    data = _object_to_dict(body).copy()
    permission_context = _request_permission_context(request)
    permission_group = getattr(permission_context, "permission_group", "")
    if permission_group:
        data["permission_group"] = _enum_value(permission_group)
    return _sanitize_public_payload(data)
```

Use it in both status endpoints:

```python
return _status_with_permission_context(query_service.get_session(session_id), request)
return _status_with_permission_context(query_service.get_phase(session_id), request)
```

This keeps standalone route tests unchanged when no middleware has attached a permission context, while main-app loopback/operator reads expose the group that the UI needs.

- [ ] **Step 5: Run focused tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_operator_console_ui.py -q
```

Expected: PASS.

---

### Task 5: Verification, Browser Smoke, And Commit

**Files:**
- All files touched by Tasks 1-4.

- [ ] **Step 1: Run roadmap item verification**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_operator_console_ui.py -q
python -m pytest tests\youtubebridge_v2\test_main_app_security.py -q
```

Expected: PASS.

- [ ] **Step 2: Run full V2 verification**

Run:

```powershell
python -m pytest tests\youtubebridge_v2 -q
git diff --check
git status --short --branch
```

Expected:

- Full V2 suite passes with existing external integration skip.
- `git diff --check` exits 0.
- `git status` shows only 5B files.

- [ ] **Step 3: Browser/UI verification**

Use the Browser plugin against the current 8088 foreground service. If 8088 is not serving the current app, start it using the visible foreground rule:

```powershell
Start-Process -FilePath "cmd.exe" -ArgumentList "/k", "G:\ClaudeProject\MemoriaCore\start.bat" -WorkingDirectory "G:\ClaudeProject\MemoriaCore" -WindowStyle Normal
```

Verify:

- `GET /v2/static/operator-console/index.html` loads.
- No-session page shows create session form.
- Existing session page shows bind/tick/manual-close controls.
- Browser console has no error logs.
- Desktop and 390px mobile viewport show required controls without missing elements.

- [ ] **Step 4: Stage and commit only Wave 5B files**

Run:

```powershell
git add tests\youtubebridge_v2\test_operator_console_ui.py YouTubeBridgeV2\static\operator-console\operator-console.js YouTubeBridgeV2\static\operator-console\operator-console.css static\locales\zh-TW.json static\locales\en-US.json YouTubeBridgeV2\docs\modules\operator-console-ui.md YouTubeBridgeV2\docs\architecture-index.md YouTubeBridgeV2\docs\api-reference-index.md YouTubeBridgeV2\docs\implementation-plans\operator-console-runtime-controls.md
git commit -m "feat: add operator console runtime controls"
```

Expected: commit succeeds. Do not modify roadmap checkboxes.

---

## Self-Review

- Spec coverage: 5B requires create/bind/tick/manual-close controls. Tasks 1-3 add command wrappers, render controls, event binding, and no-session create UI. Task 4 covers main app API dependency and docs. Manual close remains existing endpoint and is included in control rendering/disable behavior.
- Placeholder scan: no TBD/TODO/fill-later instructions remain; each implementation/test step has concrete code and commands.
- Type consistency: command classes use `CreateSessionCommand`, `BindPlanCommand`, `TickSessionCommand`; tests and docs use the same names. Endpoints match server route contracts.
