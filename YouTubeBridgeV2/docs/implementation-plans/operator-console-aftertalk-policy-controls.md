# Operator Console Aftertalk Policy Controls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成 roadmap `5C`：Operator Console 的 Aftertalk policy control 要接 durable `/v2` API，更新後以 durable status reload 為真相來源。

**Architecture:** 沿用既有 `AftertalkPolicyControl` 與 toggle UI，不新增新的 policy contract。改變事件處理流程：toggle change -> `POST /v2/sessions/{session_id}/aftertalk-policy` -> `GET /v2/sessions/{session_id}` reload -> render durable status。這避免 UI 只靠 optimistic local patch，並保留 permission-aware controls。

**Tech Stack:** Plain ES module JavaScript、Node-based UI tests、FastAPI TestClient、pytest、existing V2 HTTP API。

---

## Scope

Roadmap item：`5C：aftertalk policy controls`

完成條件：

- Existing aftertalk toggle 仍是唯一 5C policy control。
- Toggle sends `POST /v2/sessions/{session_id}/aftertalk-policy` with `aftertalk_policy: "auto" | "disabled"`。
- 成功後必須呼叫 `loadOperatorStatus(...)` 重新讀 durable session status，再 render。
- in-flight 時 aftertalk toggle、bind plan、tick、manual close 都 disabled。
- display-only permission 不顯示 aftertalk control。
- Main app smoke test 驗證 aftertalk policy update 後 `GET /v2/sessions/{id}` 讀到 durable policy。
- 不新增 API key UI，不改 aftertalk runtime contract，不改 roadmap checkbox。

## File Structure

- Modify: `tests/youtubebridge_v2/test_operator_console_ui.py`
  - Add regression for mounted aftertalk toggle reloading durable status after successful policy update.
  - Extend main app dependency smoke to verify durable aftertalk policy update.
- Modify: `YouTubeBridgeV2/static/operator-console/operator-console.js`
  - Change aftertalk toggle success path to reload durable status instead of only local patch.
- Modify: `YouTubeBridgeV2/docs/modules/operator-console-ui.md`
  - Document durable reload after aftertalk policy update.
- Modify: `YouTubeBridgeV2/docs/architecture-index.md`
  - Add Wave 5C status.
- Modify: `YouTubeBridgeV2/docs/api-reference-index.md`
  - Mention 5C durable reload behavior in Operator Console UI section.

---

### Task 1: Red Tests For Durable Aftertalk Policy Reload

**Files:**
- Modify: `tests/youtubebridge_v2/test_operator_console_ui.py`

- [ ] **Step 1: Add mounted toggle reload test**

Add near `test_aftertalk_toggle_sends_policy_update`:

```python
def test_aftertalk_toggle_reloads_durable_status_after_update():
    result = _run_node_json(
        """
const calls = [];
let changeHandler = null;
const elements = {
  aftertalk: {
    checked: false,
    addEventListener: (_event, handler) => { changeHandler = handler; }
  },
  tick: null,
  bindPlan: null,
  close: null
};
const root = {
  innerHTML: "",
  querySelector: (selector) => {
    if (selector === "[data-testid='aftertalk-toggle']") return elements.aftertalk;
    if (selector === "[data-testid='tick-button']") return elements.tick;
    if (selector === "[data-testid='bind-plan-button']") return elements.bindPlan;
    if (selector === "[data-testid='manual-close-button']") return elements.close;
    return null;
  }
};
const fetchImpl = async (url, options = {}) => {
  calls.push({url, method: options.method || "GET", body: options.body ? JSON.parse(options.body) : null});
  if (options.method === "POST") {
    return {ok: true, json: async () => ({status: "ok"})};
  }
  return {
    ok: true,
    json: async () => ({
      session_id: "session-1",
      phase: "planned_show",
      permission_group: "operator",
      aftertalk_policy: "disabled",
      public_summary: {title: "Reloaded Policy"}
    })
  };
};
ui.mountOperatorConsole({
  root,
  sessionId: "session-1",
  fetchImpl,
  eventSourceFactory: () => null,
  initialStatus: {
    session_id: "session-1",
    phase: "planned_show",
    permission_group: "operator",
    aftertalk_policy: "auto"
  }
});
await changeHandler();
console.log(JSON.stringify({calls, html: root.innerHTML}));
"""
    )

    assert result["calls"][0]["url"] == "/v2/sessions/session-1/aftertalk-policy"
    assert result["calls"][0]["method"] == "POST"
    assert result["calls"][0]["body"]["aftertalk_policy"] == "disabled"
    assert result["calls"][1] == {
        "url": "/v2/sessions/session-1",
        "method": "GET",
        "body": None,
    }
    assert "Reloaded Policy" in result["html"]
    assert "disabled" in result["html"]
```

- [ ] **Step 2: Extend main app dependency smoke**

In `test_operator_console_api_dependencies_are_served_by_main_app`, after the initial status assertions and before bind/tick/manual close:

```python
policy_response = client.post(
    "/v2/sessions/session-1/aftertalk-policy",
    json={"command_id": "cmd-policy", "aftertalk_policy": "disabled"},
)
assert policy_response.status_code == 200

policy_status = client.get("/v2/sessions/session-1").json()
assert policy_status["aftertalk_policy"] == "disabled"
assert policy_status["permission_group"] == "operator"
```

- [ ] **Step 3: Run red tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_operator_console_ui.py::test_aftertalk_toggle_reloads_durable_status_after_update tests\youtubebridge_v2\test_operator_console_ui.py::test_operator_console_api_dependencies_are_served_by_main_app -q
```

Expected: mounted toggle test FAILS because the current success path only renders a local `aftertalk_policy` patch and does not call `loadOperatorStatus(...)`.

---

### Task 2: Green JavaScript Durable Reload

**Files:**
- Modify: `YouTubeBridgeV2/static/operator-console/operator-console.js`

- [ ] **Step 1: Update aftertalk toggle success path**

In `bindOperatorControls`, replace:

```javascript
await AftertalkPolicyControl.send({sessionId, policy, fetchImpl});
render({...status, aftertalk_policy: policy});
```

with:

```javascript
await AftertalkPolicyControl.send({sessionId, policy, fetchImpl});
const nextStatus = await loadOperatorStatus({sessionId, fetchImpl});
render(nextStatus);
```

- [ ] **Step 2: Run focused tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_operator_console_ui.py::test_aftertalk_toggle_reloads_durable_status_after_update tests\youtubebridge_v2\test_operator_console_ui.py -q
```

Expected: PASS.

---

### Task 3: Docs Sync

**Files:**
- Modify: `YouTubeBridgeV2/docs/modules/operator-console-ui.md`
- Modify: `YouTubeBridgeV2/docs/architecture-index.md`
- Modify: `YouTubeBridgeV2/docs/api-reference-index.md`

- [ ] **Step 1: Update module design failure/state rules**

Add to UI State Rules or Failure Modes:

```markdown
- Aftertalk policy update 成功後必須重新讀 `GET /v2/sessions/{session_id}`，以 durable status 作為畫面真相來源。
```

- [ ] **Step 2: Add Wave 5C architecture status**

After Wave 5B status:

```markdown
## Integration Wave 5C 狀態

- [x] Durable aftertalk control：Aftertalk toggle 送出 `POST /v2/sessions/{session_id}/aftertalk-policy` 後會重新讀 durable session status。
- [x] Permission boundary：aftertalk control 只在 operator context 顯示，display-only 仍隱藏 controls。
- [x] Scope boundary：本階段不改 aftertalk runtime policy contract，不新增 API key management UI。
```

- [ ] **Step 3: Update API reference**

In Operator Console UI purpose:

```markdown
Wave 5C：Aftertalk policy control 更新成功後重新讀 `GET /v2/sessions/{session_id}`，不靠 optimistic local patch 作為最終狀態。
```

- [ ] **Step 4: Run docs/UI tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_operator_console_ui.py -q
```

Expected: PASS.

---

### Task 4: Verification, Browser Smoke, And Commit

**Files:**
- All files touched by Tasks 1-3.

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

Expected: full V2 suite passes with existing skip; diff check exits 0; status shows only 5C files.

- [ ] **Step 3: Browser/UI verification**

Use Browser against `/v2/static/operator-console/index.html?session_id=<test-session>`:

- Aftertalk toggle is visible in operator context.
- Toggle can be changed without console errors.
- Durable status after POST reads back the changed policy.

- [ ] **Step 4: Stage and commit**

Run:

```powershell
git add tests\youtubebridge_v2\test_operator_console_ui.py YouTubeBridgeV2\static\operator-console\operator-console.js YouTubeBridgeV2\docs\modules\operator-console-ui.md YouTubeBridgeV2\docs\architecture-index.md YouTubeBridgeV2\docs\api-reference-index.md YouTubeBridgeV2\docs\implementation-plans\operator-console-aftertalk-policy-controls.md
git commit -m "feat: harden operator aftertalk policy control"
```

Expected: commit succeeds. Do not modify roadmap checkboxes.

---

## Self-Review

- Spec coverage: 5C is aftertalk policy controls. Existing API wrapper remains; tests now prove UI updates use durable status reload and main app persists the policy.
- Placeholder scan: no TBD/TODO/fill-later text remains.
- Type consistency: route and UI use `aftertalk_policy`; UI class remains `AftertalkPolicyControl`; durable reload uses existing `loadOperatorStatus`.
