# Operator Console Status Dashboard Durable API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成 roadmap `5A`：讓 Operator Console status dashboard 讀取真 `/v2/sessions/{session_id}` durable session status，而不是只讀 phase-only endpoint。

**Architecture:** UI 初始載入改用 `GET /v2/sessions/{session_id}`，保留 SSE operator stream 作為後續更新來源。`OperatorSessionStatusView` 將 durable session status 的 `public_summary`、`automation_control`、`session_id` 與現有 phase/plan/aftertalk/closing 欄位整理成 display-safe view model；status dashboard 只呈現狀態，不新增 create/bind/tick/API-key 管理。

**Tech Stack:** Plain HTML/CSS/ES module JavaScript、Node-based UI tests、FastAPI TestClient、pytest、existing V2 durable StorageManager composition。

---

## Scope

Roadmap item：`5A：status dashboard 接真 /v2 durable API`

完成條件：

- `loadOperatorStatus({sessionId})` fetches `/v2/sessions/{session_id}`。
- Status dashboard 顯示 durable session identity：
  - `session_id`
  - `public_summary.title` 或 fallback title。
- Status dashboard 顯示 durable automation state：
  - enabled + not paused => running
  - enabled + paused => paused
  - disabled => disabled
  - reason 顯示為 muted secondary text，且保持 sanitized。
- Existing phase、remaining time、aftertalk policy、closing state、plan progress rendering 繼續可用。
- Main app API dependency smoke test 要驗證 `/v2/sessions/{session_id}` 由 real durable query service served。
- 不直接 import runtime/adapters/storage，不直接寫 storage，不新增 5B/5C/5D control surface。

不包含：

- 不新增 create/bind/tick controls；那是 5B。
- 不擴充 aftertalk policy controls；那是 5C。現有 toggle 保持既有行為。
- 不新增 API key management UI；那是 5D。
- 不勾選 roadmap checkbox。

## File Structure

- Modify: `tests/youtubebridge_v2/test_operator_console_ui.py`
  - Add red tests for durable session load endpoint, durable identity rendering, automation state rendering, and main app dependency through `/v2/sessions/{id}`.
- Modify: `YouTubeBridgeV2/static/operator-console/operator-console.js`
  - Normalize `public_summary` and `automation_control`.
  - Render session title/id and automation status panel.
  - Change `loadOperatorStatus` endpoint to `/v2/sessions/{session_id}`.
- Modify: `YouTubeBridgeV2/static/operator-console/operator-console.css`
  - Add compact header metadata/title styles and automation status color rules.
- Modify: `static/locales/zh-TW.json`
  - Add operator console keys for session, automation, running/paused/disabled labels.
- Modify: `static/locales/en-US.json`
  - Add matching English keys.
- Modify: `YouTubeBridgeV2/docs/modules/operator-console-ui.md`
  - Document durable session status as primary initial load source.
- Modify: `YouTubeBridgeV2/docs/architecture-index.md`
  - Add Wave 5A status.
- Modify: `YouTubeBridgeV2/docs/api-reference-index.md`
  - Update Operator Console UI contract to mention durable status source.

---

### Task 1: Red UI Tests For Durable Session Dashboard

**Files:**
- Modify: `tests/youtubebridge_v2/test_operator_console_ui.py`

- [ ] **Step 1: Add durable status rendering test**

Add this test after `test_operator_console_renders_current_phase`:

```python
def test_operator_console_renders_durable_session_identity_and_automation_state():
    result = _run_node_json(
        """
const view = ui.OperatorSessionStatusView.fromStatus({
  session_id: "session-1",
  phase: "planned_show",
  public_summary: {
    title: "May Showcase",
    plan_id: "plan-1",
    raw_payload: {token: "must not leak"}
  },
  automation_control: {
    enabled: true,
    paused: true,
    reason: "operator pause",
    raw_payload: {authorization: "Bearer secret"}
  }
});
const html = ui.renderOperatorConsole(view);
console.log(JSON.stringify({view, html}));
"""
    )

    assert result["view"]["statusTitle"] == "May Showcase"
    assert result["view"]["sessionId"] == "session-1"
    assert result["view"]["automationControl"] == {
        "enabled": True,
        "paused": True,
        "reason": "operator pause",
    }
    assert result["view"]["automationStateLabel"] == "paused"
    assert 'data-testid="status-title"' in result["html"]
    assert 'data-testid="session-id"' in result["html"]
    assert 'data-testid="automation-state"' in result["html"]
    assert "May Showcase" in result["html"]
    assert "session-1" in result["html"]
    assert "operator pause" in result["html"]
    _assert_no_private_payload(result)
```

- [ ] **Step 2: Add durable status endpoint test**

Add this test near the existing control action tests:

```python
def test_load_operator_status_fetches_durable_session_status_api():
    result = _run_node_json(
        """
const calls = [];
const fetchImpl = async (url) => {
  calls.push(url);
  return {
    ok: true,
    json: async () => ({
      session_id: "session-1",
      phase: "planned_show",
      public_summary: {title: "Durable Status"},
      automation_control: {enabled: false, paused: false, reason: "maintenance"}
    })
  };
};
const view = await ui.loadOperatorStatus({sessionId: "session 1", fetchImpl});
console.log(JSON.stringify({calls, view}));
"""
    )

    assert result["calls"] == ["/v2/sessions/session%201"]
    assert result["view"]["statusTitle"] == "Durable Status"
    assert result["view"]["automationStateLabel"] == "disabled"
```

- [ ] **Step 3: Add i18n key expectations**

Extend `test_operator_console_i18n_keys_are_registered` key list with:

```python
"youtubebridge_v2.operator_console.session",
"youtubebridge_v2.operator_console.automation",
"youtubebridge_v2.operator_console.automation_running",
"youtubebridge_v2.operator_console.automation_paused",
"youtubebridge_v2.operator_console.automation_disabled",
```

- [ ] **Step 4: Update main app dependency smoke to expect durable status API**

Change the status request in `test_operator_console_api_dependencies_are_served_by_main_app`:

```python
status_response = client.get("/v2/sessions/session-1")
assert status_response.status_code == 200
status = status_response.json()
assert status["session_id"] == "session-1"
assert status["phase"] == "planned_show"
assert "public_summary" in status
assert status["automation_control"] == {
    "enabled": True,
    "paused": False,
    "reason": "",
}
assert "v2_runtime_not_configured" not in repr(status)
```

Remove the old `phase_response` assertions from that test.

- [ ] **Step 5: Run red tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_operator_console_ui.py::test_operator_console_renders_durable_session_identity_and_automation_state tests\youtubebridge_v2\test_operator_console_ui.py::test_load_operator_status_fetches_durable_session_status_api tests\youtubebridge_v2\test_operator_console_ui.py::test_operator_console_i18n_keys_are_registered tests\youtubebridge_v2\test_operator_console_ui.py::test_operator_console_api_dependencies_are_served_by_main_app -q
```

Expected: FAIL because `statusTitle`, `automationControl`, new i18n keys, and `/v2/sessions/{id}` fetch are not implemented yet.

---

### Task 2: Green JavaScript View Model And Durable Fetch

**Files:**
- Modify: `YouTubeBridgeV2/static/operator-console/operator-console.js`

- [ ] **Step 1: Normalize durable summary and automation control**

Add these helper functions near `normalizePlanProgress`:

```javascript
function normalizePublicSummary(summary = {}, sessionId = "") {
  const safe = sanitizePublicValue(summary || {});
  const title = String(
    safe.title
    || safe.plan_title
    || safe.plan_id
    || sessionId
    || translate("untitled_session", "Untitled session"),
  );
  return {
    title,
    planId: String(safe.plan_id || ""),
  };
}

function normalizeAutomationControl(control = {}) {
  const safe = sanitizePublicValue(control || {});
  return {
    enabled: safe.enabled === undefined ? true : Boolean(safe.enabled),
    paused: Boolean(safe.paused),
    reason: String(safe.reason || ""),
  };
}

function localizedAutomationState(control) {
  if (!control.enabled) {
    return translate("automation_disabled", "disabled");
  }
  if (control.paused) {
    return translate("automation_paused", "paused");
  }
  return translate("automation_running", "running");
}
```

- [ ] **Step 2: Extend `OperatorSessionStatusView.fromStatus`**

Inside `fromStatus`, after `diagnostics`, add:

```javascript
const sessionId = String(options.sessionId || status.session_id || "");
const publicSummary = normalizePublicSummary(status.public_summary || {}, sessionId);
const automationControl = normalizeAutomationControl(status.automation_control || {});
```

Then in the returned object, replace the current `sessionId` line and add new fields:

```javascript
sessionId,
statusTitle: publicSummary.title,
publicSummary,
automationControl,
automationStateLabel: localizedAutomationState(automationControl),
```

- [ ] **Step 3: Render durable identity in header**

In `renderOperatorConsole`, replace the header title block with:

```javascript
<div>
  <span class="eyeline">YouTubeBridgeV2</span>
  <h1>${escapeHtml(translate("title", "Operator Console"))}</h1>
  <div class="header-meta">
    <strong data-testid="status-title">${escapeHtml(view.statusTitle)}</strong>
    <span data-testid="session-id">${escapeHtml(translate("session", "Session"))}: ${escapeHtml(view.sessionId)}</span>
  </div>
</div>
```

- [ ] **Step 4: Render automation state panel**

Add this panel in the `console-grid`, after remaining time and before aftertalk:

```javascript
<section class="panel" data-automation-state="${escapeHtml(automationStateName(view.automationControl))}">
  <span class="label">${escapeHtml(translate("automation", "Automation"))}</span>
  <strong data-testid="automation-state">${escapeHtml(view.automationStateLabel)}</strong>
  <span class="muted">${escapeHtml(view.automationControl.reason || "")}</span>
</section>
```

Add helper:

```javascript
function automationStateName(control) {
  if (!control.enabled) return "disabled";
  if (control.paused) return "paused";
  return "running";
}
```

- [ ] **Step 5: Change initial load endpoint**

Replace `loadOperatorStatus` fetch line:

```javascript
const response = await fetchImpl(`/v2/sessions/${encodeURIComponent(sessionId)}`);
```

- [ ] **Step 6: Run focused UI tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_operator_console_ui.py::test_operator_console_renders_durable_session_identity_and_automation_state tests\youtubebridge_v2\test_operator_console_ui.py::test_load_operator_status_fetches_durable_session_status_api -q
```

Expected: PASS after the JavaScript change; i18n key test still fails until locale files are updated.

---

### Task 3: Green Locale And CSS Polish

**Files:**
- Modify: `static/locales/zh-TW.json`
- Modify: `static/locales/en-US.json`
- Modify: `YouTubeBridgeV2/static/operator-console/operator-console.css`

- [ ] **Step 1: Add zh-TW keys**

Add these entries near existing `youtubebridge_v2.operator_console.*` keys:

```json
"youtubebridge_v2.operator_console.session": "Session",
"youtubebridge_v2.operator_console.automation": "自動推進",
"youtubebridge_v2.operator_console.automation_running": "running",
"youtubebridge_v2.operator_console.automation_paused": "paused",
"youtubebridge_v2.operator_console.automation_disabled": "disabled",
"youtubebridge_v2.operator_console.untitled_session": "未命名 session",
```

- [ ] **Step 2: Add en-US keys**

Add these entries near existing `youtubebridge_v2.operator_console.*` keys:

```json
"youtubebridge_v2.operator_console.session": "Session",
"youtubebridge_v2.operator_console.automation": "Automation",
"youtubebridge_v2.operator_console.automation_running": "running",
"youtubebridge_v2.operator_console.automation_paused": "paused",
"youtubebridge_v2.operator_console.automation_disabled": "disabled",
"youtubebridge_v2.operator_console.untitled_session": "Untitled session",
```

- [ ] **Step 3: Add compact durable status styles**

Append these CSS rules after `.console-header h1`:

```css
.header-meta {
  align-items: baseline;
  display: flex;
  flex-wrap: wrap;
  gap: 8px 12px;
  margin-top: 8px;
}

.header-meta strong {
  font-size: 14px;
  font-weight: 650;
  line-height: 1.25;
}

.header-meta span {
  color: var(--text2);
  font-size: 12px;
  overflow-wrap: anywhere;
}
```

Add automation state styles:

```css
.panel[data-automation-state="running"] strong {
  color: var(--accent2);
}

.panel[data-automation-state="paused"] strong {
  color: var(--warn);
}

.panel[data-automation-state="disabled"] strong {
  color: var(--text2);
}
```

- [ ] **Step 4: Run focused UI file tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_operator_console_ui.py::test_operator_console_i18n_keys_are_registered tests\youtubebridge_v2\test_operator_console_ui.py -q
```

Expected: PASS.

---

### Task 4: Docs Sync

**Files:**
- Modify: `YouTubeBridgeV2/docs/modules/operator-console-ui.md`
- Modify: `YouTubeBridgeV2/docs/architecture-index.md`
- Modify: `YouTubeBridgeV2/docs/api-reference-index.md`

- [ ] **Step 1: Update operator module design**

In `Public Entrypoints`, add this bullet after the served entrypoint:

```markdown
- Initial status source: `GET /v2/sessions/{session_id}` durable session status。
```

In `Inputs`, add:

```markdown
- durable session public status (`GET /v2/sessions/{session_id}`)，包含 `public_summary` 與 `automation_control`。
```

- [ ] **Step 2: Add Wave 5A architecture status**

In `YouTubeBridgeV2/docs/architecture-index.md`, after Wave 4D status, add:

```markdown
## Integration Wave 5A 狀態

- [x] Durable status source：Operator Console 初始載入讀取 `GET /v2/sessions/{session_id}`，而不是 phase-only endpoint。
- [x] Session identity：status dashboard 顯示 sanitized `public_summary` title 與 `session_id`。
- [x] Automation status：status dashboard 顯示 durable `automation_control` running/paused/disabled 狀態與 public reason。
- [x] Scope boundary：本階段不新增 create/bind/tick/API key management UI；那些保留給 Wave 5B/5D。
```

- [ ] **Step 3: Update API reference Operator Console UI section**

In `Operator Console UI` Purpose, add:

```markdown
Wave 5A：初始 status dashboard 以 `GET /v2/sessions/{session_id}` 作為 durable source，phase-only endpoint 不再是初始載入來源。
```

Add `GET /v2/sessions/{session_id}` to Concepts if not already present in that section:

```markdown
- `GET /v2/sessions/{session_id}`
```

- [ ] **Step 4: Run docs-related tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_operator_console_ui.py::test_operator_console_static_entrypoint_links_assets tests\youtubebridge_v2\test_operator_console_ui.py::test_operator_console_does_not_import_runtime_adapter_or_storage -q
```

Expected: PASS.

---

### Task 5: Verification And Commit

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

- pytest passes, with the existing skip count if external integration is not enabled.
- `git diff --check` has no whitespace errors.
- `git status` shows only intended Wave 5A files changed.

- [ ] **Step 3: Browser/UI verification decision**

For Wave 5A, run browser verification if a local served `/v2/static/operator-console/index.html?session_id=...` target is available without starting hidden services. If 8088 must be started, use a visible foreground window per repo rule:

```powershell
Start-Process -FilePath "cmd.exe" -ArgumentList "/k", "G:\ClaudeProject\MemoriaCore\start.bat" -WorkingDirectory "G:\ClaudeProject\MemoriaCore" -WindowStyle Normal
```

Verify:

- Page loads without console errors.
- Header shows session title/id.
- Automation panel does not overlap at desktop/mobile width.
- Missing session id still renders sanitized diagnostic.

- [ ] **Step 4: Stage and commit only Wave 5A files**

Run:

```powershell
git add tests\youtubebridge_v2\test_operator_console_ui.py YouTubeBridgeV2\static\operator-console\operator-console.js YouTubeBridgeV2\static\operator-console\operator-console.css static\locales\zh-TW.json static\locales\en-US.json YouTubeBridgeV2\docs\modules\operator-console-ui.md YouTubeBridgeV2\docs\architecture-index.md YouTubeBridgeV2\docs\api-reference-index.md YouTubeBridgeV2\docs\implementation-plans\operator-console-status-dashboard-durable-api.md
git commit -m "feat: connect operator status dashboard to durable API"
```

Expected: commit succeeds. Do not modify roadmap checkboxes.

---

## Self-Review

- Spec coverage: 5A requires status dashboard to use real durable `/v2`; Task 1 and Task 2 switch initial load to `GET /v2/sessions/{session_id}` and verify main app durable API serving. Task 2 and Task 3 render durable `public_summary` and `automation_control`. Task 4 syncs docs.
- Placeholder scan: no TBD/TODO/fill-later instructions remain; each step includes concrete test code, implementation snippets, and commands.
- Type consistency: JavaScript fields are `statusTitle`, `publicSummary`, `automationControl`, `automationStateLabel`; tests assert the same names. Endpoint is consistently `/v2/sessions/${encodeURIComponent(sessionId)}`.
