# Operator Console API Key Management UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成 roadmap `5D`：Operator Console 提供 API key management UI，並透過 operator-only durable `/v2` API 管理 prefs-backed API keys。

**Architecture:** 沿用 Wave 2C 的 `youtubebridge_v2_api_keys` prefs source，不建立新的 secret store 或改變 auth loading contract。新增 `GET/POST/DELETE /v2/api-keys` operator-only endpoints：route 層只讀寫 prefs、回應只包含 fingerprint/prefix/permission group，不回傳 raw key。Operator Console 新增管理 panel 與 JS command wrappers，讓 operator 可新增、刷新、撤銷 key；display/observer context 不顯示 controls。

**Tech Stack:** FastAPI routes、StorageManager prefs、plain ES module JavaScript、Node-based UI tests、FastAPI TestClient、pytest。

---

## Scope

Roadmap item：`5D：API key management UI`

完成條件：

- 新增 operator-only API key management endpoints，所有非 operator caller 均在 security middleware 前置拒絕。
- `GET /v2/api-keys` 回傳 sanitized list，不包含 raw `key`、`token`、`secret`、Authorization 或完整可用憑證。
- `POST /v2/api-keys` 可加入或更新一組 `key + permission_group`，只接受 `operator`、`display`、`observer`。
- `DELETE /v2/api-keys/{key_fingerprint}` 依 fingerprint 撤銷 key。
- 新增 key 後，該 key 立即可通過後續 `/v2` auth；撤銷後即失效。
- Operator Console 只在 operator context 顯示 API key management panel；key 輸入送出後清空，不在 HTML 中回顯 raw key。
- 更新 docs/API reference/module design；不修改 roadmap checkbox，不處理 5E browser regression checklist。

## File Structure

- Modify: `YouTubeBridgeV2/server/auth_config.py`
  - Add public helpers for sanitized API key entries and prefs mutation while preserving the existing `load_v2_api_key_config(...)` auth contract.
- Modify: `YouTubeBridgeV2/server/routes.py`
  - Add storage dependency placeholder and `GET/POST/DELETE /v2/api-keys` endpoints.
- Modify: `YouTubeBridgeV2/server/main_security.py`
  - Mark `/v2/api-keys` endpoints as operator-only route ids.
- Modify: `YouTubeBridgeV2/server/security.py`
  - Add `manage_api_keys` to operator allowed actions and route action map.
- Modify: `api/main.py`
  - Override the new routes storage dependency with `get_storage`.
- Modify: `YouTubeBridgeV2/app.py`
  - Override the new dependency for standalone V2 apps when composition exposes `storage_manager`.
- Modify: `YouTubeBridgeV2/static/operator-console/operator-console.js`
  - Add command wrappers, render panel, event binding, list normalization, and no-secret rendering.
- Modify: `YouTubeBridgeV2/static/operator-console/operator-console.css`
  - Add compact styles for API key controls/list.
- Modify: `static/locales/zh-TW.json`
  - Add Traditional Chinese UI strings.
- Modify: `static/locales/en-US.json`
  - Add English UI strings.
- Modify: `tests/youtubebridge_v2/test_main_app_security.py`
  - Add endpoint integration/security tests.
- Modify: `tests/youtubebridge_v2/test_access_control_security.py`
  - Add permission action mapping regression.
- Modify: `tests/youtubebridge_v2/test_operator_console_ui.py`
  - Add UI command/render/mount tests.
- Modify: `YouTubeBridgeV2/docs/modules/access-control-security.md`
  - Document API key management behavior and secret boundary.
- Modify: `YouTubeBridgeV2/docs/modules/operator-console-ui.md`
  - Document management panel and display-only hiding rule.
- Modify: `YouTubeBridgeV2/docs/modules/server-api-surface.md`
  - Document new HTTP endpoints.
- Modify: `YouTubeBridgeV2/docs/architecture-index.md`
  - Add Wave 5D status.
- Modify: `YouTubeBridgeV2/docs/api-reference-index.md`
  - Add endpoint and UI entry references.

---

### Task 1: Red Tests For API Key Management Endpoints

**Files:**
- Modify: `tests/youtubebridge_v2/test_main_app_security.py`
- Modify: `tests/youtubebridge_v2/test_access_control_security.py`

- [ ] **Step 1: Add endpoint tests**

Add tests near the existing API key/security tests:

```python
def test_main_app_v2_loopback_operator_can_manage_api_keys_without_secret_echo(
    tmp_path,
    monkeypatch,
):
    storage = _storage_manager(tmp_path)
    _save_api_keys(storage)
    api_main = _install_test_storage(monkeypatch, storage)
    client = _loopback_client(api_main.app)

    create_response = client.post(
        "/v2/api-keys",
        json={"key": "new-display-secret", "permission_group": "display"},
    )
    assert create_response.status_code == 200
    body = create_response.json()
    assert body["status"] == "ok"
    assert body["api_key"]["permission_group"] == "display"
    assert body["api_key"]["key_fingerprint"]
    assert body["api_key"]["key_prefix"] == body["api_key"]["key_fingerprint"][:12]
    assert "new-display-secret" not in create_response.text

    list_response = client.get("/v2/api-keys")
    assert list_response.status_code == 200
    list_body = list_response.json()
    assert any(
        entry["permission_group"] == "display"
        and entry["key_fingerprint"] == body["api_key"]["key_fingerprint"]
        for entry in list_body["api_keys"]
    )
    assert "new-display-secret" not in list_response.text

    display_client = _remote_client(api_main.app)
    display_stream = display_client.get(
        "/v2/sessions/missing/display-stream",
        headers={"x-youtubebridgev2-api-key": "new-display-secret"},
    )
    assert display_stream.status_code in {200, 404}

    delete_response = client.delete(f"/v2/api-keys/{body['api_key']['key_fingerprint']}")
    assert delete_response.status_code == 200
    assert delete_response.json()["removed"] == 1

    rejected_after_delete = display_client.get(
        "/v2/sessions/missing/display-stream",
        headers={"x-youtubebridgev2-api-key": "new-display-secret"},
    )
    assert rejected_after_delete.status_code == 401
```

Add non-operator and validation coverage:

```python
def test_main_app_v2_api_key_management_requires_operator_permission(
    tmp_path,
    monkeypatch,
):
    storage = _storage_manager(tmp_path)
    _save_api_keys(storage)
    api_main = _install_test_storage(monkeypatch, storage)
    client = _remote_client(api_main.app)

    observer_response = client.get(
        "/v2/api-keys",
        headers={"x-youtubebridgev2-api-key": OBSERVER_KEY},
    )
    display_response = client.post(
        "/v2/api-keys",
        headers={"x-youtubebridgev2-api-key": DISPLAY_KEY},
        json={"key": "not-allowed", "permission_group": "observer"},
    )
    invalid_group = client.post(
        "/v2/api-keys",
        headers={"x-youtubebridgev2-api-key": OPERATOR_KEY},
        json={"key": "bad", "permission_group": "admin"},
    )

    _assert_security_error(observer_response, status_code=403, code="forbidden")
    _assert_security_error(display_response, status_code=403, code="forbidden")
    assert invalid_group.status_code == 422
    assert "not-allowed" not in repr(storage.load_prefs())
```

- [ ] **Step 2: Add access-control route action test**

In `tests/youtubebridge_v2/test_access_control_security.py`, add:

```python
def test_operator_permission_can_manage_api_keys():
    result = resolve_permission_context(
        _request(api_key="operator-secret"),
        _requirement(permission_group=PermissionGroup.OPERATOR, route_id="manage_api_keys"),
    )

    assert result.permission_group == PermissionGroup.OPERATOR
    assert "manage_api_keys" in result.allowed_actions
```

- [ ] **Step 3: Run red tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_main_app_security.py::test_main_app_v2_loopback_operator_can_manage_api_keys_without_secret_echo tests\youtubebridge_v2\test_main_app_security.py::test_main_app_v2_api_key_management_requires_operator_permission tests\youtubebridge_v2\test_access_control_security.py::test_operator_permission_can_manage_api_keys -q
```

Expected: FAIL because `/v2/api-keys` endpoints and `manage_api_keys` action do not exist yet.

---

### Task 2: Green Server/API Implementation

**Files:**
- Modify: `YouTubeBridgeV2/server/auth_config.py`
- Modify: `YouTubeBridgeV2/server/routes.py`
- Modify: `YouTubeBridgeV2/server/main_security.py`
- Modify: `YouTubeBridgeV2/server/security.py`
- Modify: `api/main.py`
- Modify: `YouTubeBridgeV2/app.py`

- [ ] **Step 1: Add prefs mutation helpers**

In `YouTubeBridgeV2/server/auth_config.py`, add helpers that preserve existing prefs and expose only sanitized entries:

```python
from dataclasses import dataclass
from hashlib import sha256
```

```python
@dataclass(frozen=True)
class V2ApiKeyPublicEntry:
    key_fingerprint: str
    key_prefix: str
    permission_group: str
```

```python
def list_v2_api_key_entries(storage_manager: object) -> list[V2ApiKeyPublicEntry]:
    prefs = _load_prefs(storage_manager)
    return [_public_entry(key, group) for key, group in _valid_raw_entries(prefs)]


def upsert_v2_api_key_entry(
    storage_manager: object,
    *,
    key: str,
    permission_group: str,
) -> V2ApiKeyPublicEntry:
    normalized_key = str(key or "").strip()
    group = _coerce_public_group(permission_group)
    if not normalized_key or group is None:
        raise ValueError("invalid api key entry")
    prefs = _load_prefs(storage_manager)
    entries = [
        {"key": existing_key, "permission_group": existing_group.value}
        for existing_key, existing_group in _valid_raw_entries(prefs)
        if _fingerprint(existing_key) != _fingerprint(normalized_key)
    ]
    entries.append({"key": normalized_key, "permission_group": group.value})
    prefs[V2_API_KEYS_PREFS_KEY] = entries
    _save_prefs(storage_manager, prefs)
    return _public_entry(normalized_key, group)


def delete_v2_api_key_entry(storage_manager: object, *, key_fingerprint: str) -> int:
    fingerprint = str(key_fingerprint or "").strip().lower()
    prefs = _load_prefs(storage_manager)
    kept = []
    removed = 0
    for existing_key, existing_group in _valid_raw_entries(prefs):
        if _fingerprint(existing_key) == fingerprint:
            removed += 1
            continue
        kept.append({"key": existing_key, "permission_group": existing_group.value})
    prefs[V2_API_KEYS_PREFS_KEY] = kept
    _save_prefs(storage_manager, prefs)
    return removed
```

Also add `_valid_raw_entries`, `_public_entry`, `_fingerprint`, `_save_prefs`, and export the new symbols. `_save_prefs` must raise `RuntimeError` when storage lacks `save_prefs`.

- [ ] **Step 2: Add route dependency and endpoints**

In `YouTubeBridgeV2/server/routes.py`, import the helpers and add:

```python
class StorageManagerNotConfigured(RuntimeError):
    """StorageManager 尚未由 application wiring 注入."""
```

```python
class ApiKeyCreateRequest(BaseModel):
    key: str = Field(..., min_length=1)
    permission_group: Literal["operator", "display", "observer"]
```

```python
def get_storage_manager() -> object:
    """FastAPI dependency placeholder for StorageManager-backed prefs."""

    raise StorageManagerNotConfigured("storage manager dependency is not configured")
```

Add endpoints before `/sessions/{session_id}` routes:

```python
@router.get("/api-keys", response_model=None)
def list_api_keys_endpoint(
    storage_manager: object = Depends(get_storage_manager),
) -> dict[str, object] | JSONResponse:
    return {"api_keys": [_object_to_dict(entry) for entry in list_v2_api_key_entries(storage_manager)]}


@router.post("/api-keys", response_model=None)
def create_api_key_endpoint(
    raw_body: object = Body(...),
    storage_manager: object = Depends(get_storage_manager),
) -> dict[str, object] | JSONResponse:
    body = _validate_body(ApiKeyCreateRequest, raw_body)
    if isinstance(body, JSONResponse):
        return body
    try:
        entry = upsert_v2_api_key_entry(
            storage_manager,
            key=body.key,
            permission_group=body.permission_group,
        )
    except ValueError:
        return _validation_error_response(raw_body)
    return {"status": "ok", "api_key": _object_to_dict(entry)}


@router.delete("/api-keys/{key_fingerprint}", response_model=None)
def delete_api_key_endpoint(
    key_fingerprint: str,
    storage_manager: object = Depends(get_storage_manager),
) -> dict[str, object]:
    removed = delete_v2_api_key_entry(storage_manager, key_fingerprint=key_fingerprint)
    return {
        "status": "ok",
        "removed": removed,
        "api_keys": [_object_to_dict(entry) for entry in list_v2_api_key_entries(storage_manager)],
    }
```

- [ ] **Step 3: Wire security requirements**

In `YouTubeBridgeV2/server/main_security.py`, make `/v2/api-keys` operator-only:

```python
if parts[1] == "api-keys":
    if len(parts) in {2, 3} and http_method in {"GET", "POST", "DELETE"}:
        return PermissionGroup.OPERATOR, "manage_api_keys"
```

In `YouTubeBridgeV2/server/security.py`, add `"manage_api_keys"` to operator allowed actions and `_ROUTE_ACTIONS`.

- [ ] **Step 4: Wire storage dependency**

In `api/main.py`, add:

```python
app.dependency_overrides[youtubebridge_v2_routes.get_storage_manager] = get_storage
```

In `YouTubeBridgeV2/app.py`, add:

```python
storage_manager = getattr(composition, "storage_manager", None)
if storage_manager is not None:
    app.dependency_overrides[routes.get_storage_manager] = lambda: storage_manager
```

- [ ] **Step 5: Run focused server tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_main_app_security.py::test_main_app_v2_loopback_operator_can_manage_api_keys_without_secret_echo tests\youtubebridge_v2\test_main_app_security.py::test_main_app_v2_api_key_management_requires_operator_permission tests\youtubebridge_v2\test_access_control_security.py::test_operator_permission_can_manage_api_keys -q
```

Expected: PASS.

---

### Task 3: Red Tests For Operator Console API Key UI

**Files:**
- Modify: `tests/youtubebridge_v2/test_operator_console_ui.py`

- [ ] **Step 1: Add command wrapper tests**

Add tests near other command tests:

```python
def test_api_key_commands_use_management_endpoints_without_echoing_secret():
    result = _run_node_json(
        """
const calls = [];
const fetchImpl = async (url, options = {}) => {
  calls.push({url, method: options.method || "GET", body: options.body ? JSON.parse(options.body) : null});
  return {
    ok: true,
    json: async () => ({
      status: "ok",
      api_key: {key_fingerprint: "abc123def456", key_prefix: "abc123def456", permission_group: "display"},
      api_keys: [{key_fingerprint: "abc123def456", key_prefix: "abc123def456", permission_group: "display"}]
    })
  };
};
const created = await ui.ApiKeyCreateCommand.send({
  key: "new-display-secret",
  permissionGroup: "display",
  fetchImpl
});
const listed = await ui.ApiKeyListCommand.send({fetchImpl});
const deleted = await ui.ApiKeyDeleteCommand.send({
  keyFingerprint: "abc123def456",
  fetchImpl
});
console.log(JSON.stringify({calls, created, listed, deleted}));
"""
    )

    assert result["calls"][0] == {
        "url": "/v2/api-keys",
        "method": "POST",
        "body": {"key": "new-display-secret", "permission_group": "display"},
    }
    assert result["calls"][1] == {"url": "/v2/api-keys", "method": "GET", "body": None}
    assert result["calls"][2]["url"] == "/v2/api-keys/abc123def456"
    assert result["calls"][2]["method"] == "DELETE"
    _assert_no_private_payload(result)
```

- [ ] **Step 2: Add render test**

```python
def test_operator_console_renders_api_key_management_for_operator():
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

    assert 'data-testid="api-key-panel"' in result["html"]
    assert 'data-testid="api-key-input"' in result["html"]
    assert 'data-testid="api-key-permission-select"' in result["html"]
    assert 'data-testid="api-key-create-button"' in result["html"]
    assert 'data-testid="api-key-refresh-button"' in result["html"]
```

- [ ] **Step 3: Add mounted interaction test**

```python
def test_api_key_panel_creates_refreshes_and_deletes_without_rendering_secret():
    result = _run_node_json(
        """
const calls = [];
let createHandler = null;
let refreshHandler = null;
let deleteHandler = null;
const elements = {
  apiKeyInput: {value: "new-display-secret"},
  permissionSelect: {value: "display"},
  createButton: {addEventListener: (_event, handler) => { createHandler = handler; }},
  refreshButton: {addEventListener: (_event, handler) => { refreshHandler = handler; }},
  deleteButton: {dataset: {keyFingerprint: "abc123def456"}, addEventListener: (_event, handler) => { deleteHandler = handler; }},
};
const root = {
  innerHTML: "",
  querySelector: (selector) => {
    if (selector === "[data-testid='api-key-input']") return elements.apiKeyInput;
    if (selector === "[data-testid='api-key-permission-select']") return elements.permissionSelect;
    if (selector === "[data-testid='api-key-create-button']") return elements.createButton;
    if (selector === "[data-testid='api-key-refresh-button']") return elements.refreshButton;
    return null;
  },
  querySelectorAll: (selector) => selector === "[data-testid='api-key-delete-button']"
    ? [elements.deleteButton]
    : []
};
const fetchImpl = async (url, options = {}) => {
  calls.push({url, method: options.method || "GET", body: options.body ? JSON.parse(options.body) : null});
  return {
    ok: true,
    json: async () => ({
      status: "ok",
      api_keys: [{key_fingerprint: "abc123def456", key_prefix: "abc123def456", permission_group: "display"}],
      api_key: {key_fingerprint: "abc123def456", key_prefix: "abc123def456", permission_group: "display"}
    })
  };
};
ui.mountOperatorConsole({
  root,
  sessionId: "session-1",
  fetchImpl,
  eventSourceFactory: () => null,
  initialStatus: {session_id: "session-1", phase: "planned_show", permission_group: "operator"}
});
await createHandler();
await refreshHandler();
await deleteHandler();
console.log(JSON.stringify({calls, html: root.innerHTML, inputValue: elements.apiKeyInput.value}));
"""
    )

    assert result["calls"][0]["url"] == "/v2/api-keys"
    assert result["calls"][0]["method"] == "POST"
    assert result["calls"][1]["url"] == "/v2/api-keys"
    assert result["calls"][1]["method"] == "GET"
    assert result["calls"][2]["url"] == "/v2/api-keys"
    assert result["calls"][2]["method"] == "GET"
    assert result["calls"][3]["url"] == "/v2/api-keys/abc123def456"
    assert result["calls"][3]["method"] == "DELETE"
    assert result["inputValue"] == ""
    assert "abc123def456" in result["html"]
    assert "new-display-secret" not in result["html"]
    _assert_no_private_payload(result["html"])
```

- [ ] **Step 4: Run red UI tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_operator_console_ui.py::test_api_key_commands_use_management_endpoints_without_echoing_secret tests\youtubebridge_v2\test_operator_console_ui.py::test_operator_console_renders_api_key_management_for_operator tests\youtubebridge_v2\test_operator_console_ui.py::test_api_key_panel_creates_refreshes_and_deletes_without_rendering_secret -q
```

Expected: FAIL because UI commands/panel do not exist yet.

---

### Task 4: Green Operator Console UI

**Files:**
- Modify: `YouTubeBridgeV2/static/operator-console/operator-console.js`
- Modify: `YouTubeBridgeV2/static/operator-console/operator-console.css`
- Modify: `static/locales/zh-TW.json`
- Modify: `static/locales/en-US.json`

- [ ] **Step 1: Add JS command wrappers**

Add exported classes:

```javascript
export class ApiKeyListCommand {
  static async send({fetchImpl = globalThis.fetch} = {}) {
    const response = await fetchImpl("/v2/api-keys");
    const payload = await safeJson(response);
    if (!response.ok) {
      throw OperatorDiagnosticBanner.fromError(payload);
    }
    return normalizeApiKeyList(payload);
  }
}

export class ApiKeyCreateCommand {
  static async send({key, permissionGroup, fetchImpl = globalThis.fetch} = {}) {
    const response = await fetchImpl("/v2/api-keys", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        key,
        permission_group: permissionGroup,
      }),
    });
    const payload = await safeJson(response);
    if (!response.ok) {
      throw OperatorDiagnosticBanner.fromError(payload);
    }
    return sanitizePublicValue(payload);
  }
}

export class ApiKeyDeleteCommand {
  static async send({keyFingerprint, fetchImpl = globalThis.fetch} = {}) {
    const response = await fetchImpl(`/v2/api-keys/${encodeURIComponent(keyFingerprint)}`, {
      method: "DELETE",
    });
    const payload = await safeJson(response);
    if (!response.ok) {
      throw OperatorDiagnosticBanner.fromError(payload);
    }
    return sanitizePublicValue(payload);
  }
}
```

Add `normalizeApiKeyList(payload)` that returns only `{keyFingerprint, keyPrefix, permissionGroup}`.

- [ ] **Step 2: Render API key panel**

In `renderOperatorControls(view)`, after plan bind controls, add:

```javascript
      <div class="api-key-control" data-testid="api-key-panel">
        <div class="control-heading">
          <strong>${escapeHtml(translate("api_keys", "API Keys"))}</strong>
          <button data-testid="api-key-refresh-button" type="button"${bindDisabled}>${escapeHtml(translate("refresh", "Refresh"))}</button>
        </div>
        <div class="api-key-create-row">
          <input data-testid="api-key-input" type="password" autocomplete="off" placeholder="${escapeHtml(translate("api_key_placeholder", "New API key"))}">
          <select data-testid="api-key-permission-select">
            <option value="operator">operator</option>
            <option value="display">display</option>
            <option value="observer">observer</option>
          </select>
          <button data-testid="api-key-create-button" type="button"${bindDisabled}>${escapeHtml(translate("create_api_key", "Create Key"))}</button>
        </div>
        <div class="api-key-list" data-testid="api-key-list">${renderApiKeyList(view.apiKeys || [])}</div>
      </div>
```

Extend `OperatorSessionStatusView.fromStatus(...)` to carry `apiKeys: normalizeApiKeyList(status.api_keys || status.apiKeys || [])`.

- [ ] **Step 3: Bind API key controls**

In `bindOperatorControls`, add handlers:

```javascript
  const refreshApiKeys = async () => {
    try {
      const apiKeys = await ApiKeyListCommand.send({fetchImpl});
      render({...status, api_keys: apiKeys});
    } catch (error) {
      render({...status, error});
    }
  };
```

Wire create to `ApiKeyCreateCommand.send(...)`, clear the input on success, then refresh. Wire refresh button to `refreshApiKeys`. Wire all delete buttons to `ApiKeyDeleteCommand.send(...)`, then refresh.

- [ ] **Step 4: Add CSS and i18n keys**

Add compact layout classes for `.api-key-control`, `.api-key-create-row`, `.api-key-list`, `.api-key-entry`.

Add locale keys:

```json
"youtubebridge_v2.operator_console.api_keys": "API 金鑰",
"youtubebridge_v2.operator_console.refresh": "重新整理",
"youtubebridge_v2.operator_console.api_key_placeholder": "新的 API key",
"youtubebridge_v2.operator_console.create_api_key": "新增金鑰",
"youtubebridge_v2.operator_console.delete_api_key": "撤銷"
```

Use equivalent English strings in `en-US.json`.

- [ ] **Step 5: Run UI tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_operator_console_ui.py -q
```

Expected: PASS.

---

### Task 5: Docs Sync

**Files:**
- Modify: `YouTubeBridgeV2/docs/modules/access-control-security.md`
- Modify: `YouTubeBridgeV2/docs/modules/operator-console-ui.md`
- Modify: `YouTubeBridgeV2/docs/modules/server-api-surface.md`
- Modify: `YouTubeBridgeV2/docs/architecture-index.md`
- Modify: `YouTubeBridgeV2/docs/api-reference-index.md`

- [ ] **Step 1: Update access-control docs**

Document that API key management writes the same prefs source used by auth, but public responses expose only fingerprints/prefixes.

- [ ] **Step 2: Update operator console docs**

Add API key panel to outputs/public entrypoints and add rule: only operator context renders API key controls; raw key is never displayed after submission.

- [ ] **Step 3: Update server API docs**

Add endpoint entries:

```markdown
- `GET /v2/api-keys` — operator-only sanitized key list.
- `POST /v2/api-keys` — operator-only create/upsert key from `{key, permission_group}`.
- `DELETE /v2/api-keys/{key_fingerprint}` — operator-only revoke by fingerprint.
```

- [ ] **Step 4: Update architecture/API reference index**

Add Wave 5D status and include `ApiKeyListCommand`, `ApiKeyCreateCommand`, `ApiKeyDeleteCommand`, and server endpoint source references.

- [ ] **Step 5: Run docs-related tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_operator_console_ui.py::test_operator_console_i18n_keys_are_registered tests\youtubebridge_v2\test_operator_console_ui.py::test_operator_console_does_not_import_runtime_adapter_or_storage -q
```

Expected: PASS.

---

### Task 6: Verification, Browser Smoke, And Commit

**Files:**
- All files touched by Tasks 1-5.

- [ ] **Step 1: Run focused verification**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_main_app_security.py -q
python -m pytest tests\youtubebridge_v2\test_access_control_security.py -q
python -m pytest tests\youtubebridge_v2\test_operator_console_ui.py -q
```

Expected: PASS.

- [ ] **Step 2: Run full V2 verification**

Run:

```powershell
python -m pytest tests\youtubebridge_v2 -q
git diff --check
git status --short --branch
```

Expected: full V2 suite passes with the existing skip; diff check exits 0; status shows only 5D files.

- [ ] **Step 3: Browser/UI verification**

Use Browser against `/v2/static/operator-console/index.html?session_id=<test-session>`:

- API key panel is visible in operator context.
- Creating a disposable display key shows only fingerprint/prefix.
- Raw key text is not visible in the DOM after creation.
- Deleting that fingerprint removes the entry and there are no console errors.

- [ ] **Step 4: Stage and commit**

Run:

```powershell
git add YouTubeBridgeV2\server\auth_config.py YouTubeBridgeV2\server\routes.py YouTubeBridgeV2\server\main_security.py YouTubeBridgeV2\server\security.py api\main.py YouTubeBridgeV2\app.py YouTubeBridgeV2\static\operator-console\operator-console.js YouTubeBridgeV2\static\operator-console\operator-console.css static\locales\zh-TW.json static\locales\en-US.json tests\youtubebridge_v2\test_main_app_security.py tests\youtubebridge_v2\test_access_control_security.py tests\youtubebridge_v2\test_operator_console_ui.py YouTubeBridgeV2\docs\modules\access-control-security.md YouTubeBridgeV2\docs\modules\operator-console-ui.md YouTubeBridgeV2\docs\modules\server-api-surface.md YouTubeBridgeV2\docs\architecture-index.md YouTubeBridgeV2\docs\api-reference-index.md YouTubeBridgeV2\docs\implementation-plans\operator-console-api-key-management-ui.md
git commit -m "feat: add operator API key management"
```

Expected: commit succeeds. Do not modify roadmap checkboxes.

---

## Self-Review

- Spec coverage: 5D requires API key management UI. Plan includes operator-only durable endpoints, permissions, UI controls, no-secret rendering, docs, tests, and browser smoke.
- Placeholder scan: no TBD/TODO/fill-later text remains.
- Type consistency: server uses `permission_group`, UI normalizes to `permissionGroup`, public responses use `key_fingerprint` and `key_prefix`.
