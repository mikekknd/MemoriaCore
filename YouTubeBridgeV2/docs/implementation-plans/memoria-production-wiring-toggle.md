# Memoria Production Wiring Toggle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成 roadmap `2E-D`：production `/v2` 只有在 prefs 明確啟用 Memoria transport 且設定有效時才外呼，否則維持 no-op runner。

**Architecture:** `YouTubeBridgeV2/production.py` 擁有 production-only toggle 解析與 transport 建立；adapter config parser 仍只負責驗證 HTTP config。`create_production_v2_composition(...)` 保留 explicit `memoria_transport` injection 優先權，未注入時才從 `StorageManager.load_prefs()` 讀取 `youtubebridge_v2_memoria_transport.enabled`。設定缺失、未啟用、或 config invalid 都回 no-op，不讓 `/v2` 意外外呼。

**Tech Stack:** Python 3.12+、pytest、StorageManager prefs、既有 `MemoriaSyncHttpTransport`、FastAPI TestClient fake transport。

---

## Scope

Roadmap item：`2E-D：production wiring toggle，未設定時維持 no-op`

完成條件：

- Production composition 未設定 prefs 時仍使用 no-op runners。
- Production prefs 只有 `base_url` 但沒有 `enabled=true` 時仍使用 no-op runners。
- Production prefs 設定 `enabled=true` 但 config invalid 時仍使用 no-op runners。
- Production prefs 設定 `enabled=true` 且 config valid 時，`create_production_v2_composition(storage)` 會建立 `MemoriaSyncHttpTransport` 並注入 planned show / aftertalk / closing runners。
- Explicit `memoria_transport=` injection 仍優先於 prefs，保持測試與手動 composition 可控。

不包含：

- UI prefs 管理。
- 自動啟動 8088。
- 真 MemoriaCore live smoke。
- YouTube / TTS / scheduler wiring。

## File Structure

- Modify: `YouTubeBridgeV2/production.py`
  - 新增 `load_production_memoria_transport(storage_manager)`。
  - 新增 production-only enabled toggle helper。
  - `create_production_v2_composition(...)` 未明確注入 transport 時改走 loader。
- Modify: `tests/youtubebridge_v2/test_runtime_tick_vertical_slice.py`
  - 新增 prefs-driven production toggle tests，使用 monkeypatched fake transport class 避免外呼。
- Modify: `YouTubeBridgeV2/docs/modules/memoria-adapter.md`
  - 補 production toggle 行為與 no-op fallback。
- Modify: `YouTubeBridgeV2/docs/architecture-index.md`
  - 補 Wave 2E-D 狀態。
- Modify: `YouTubeBridgeV2/docs/api-reference-index.md`
  - 補 `load_production_memoria_transport` production composition source。

## Prefs Contract

`StorageManager.load_prefs()` 的 `youtubebridge_v2_memoria_transport` 支援：

```json
{
  "enabled": true,
  "base_url": "http://127.0.0.1:8088",
  "api_key": "<optional-token>",
  "timeout_seconds": 10,
  "max_attempts": 2
}
```

Truthiness for `enabled`：

- enabled: `true`, `1`, `"1"`, `"true"`, `"yes"`, `"on"`
- disabled: missing, `false`, `0`, `"0"`, empty string, any other value

---

### Task 1: Production Toggle Red Tests

**Files:**
- Modify: `tests/youtubebridge_v2/test_runtime_tick_vertical_slice.py`

- [ ] **Step 1: Extend imports**

Change the Memoria HTTP import block to include `MEMORIA_TRANSPORT_PREFS_KEY`:

```python
from YouTubeBridgeV2.adapters.memoria_http import (
    MEMORIA_TRANSPORT_PREFS_KEY,
    MemoriaHttpTransportConfig,
    MemoriaSyncHttpTransport,
)
```

Change production import to:

```python
from YouTubeBridgeV2.production import (
    create_production_v2_composition,
    load_production_memoria_transport,
)
```

- [ ] **Step 2: Add production toggle loader red tests**

Append after `test_production_composition_without_memoria_transport_keeps_noop_runner`:

```python
def test_production_memoria_transport_requires_enabled_pref(tmp_path):
    storage = _storage_manager(tmp_path)
    storage.save_prefs(
        {
            MEMORIA_TRANSPORT_PREFS_KEY: {
                "base_url": "http://127.0.0.1:8088",
            }
        }
    )

    assert load_production_memoria_transport(storage) is None

    storage.save_prefs(
        {
            MEMORIA_TRANSPORT_PREFS_KEY: {
                "enabled": False,
                "base_url": "http://127.0.0.1:8088",
            }
        }
    )

    assert load_production_memoria_transport(storage) is None


def test_production_memoria_transport_invalid_enabled_config_falls_back_noop(tmp_path):
    storage = _storage_manager(tmp_path)
    storage.save_prefs(
        {
            MEMORIA_TRANSPORT_PREFS_KEY: {
                "enabled": True,
                "base_url": "file:///tmp/memoria",
            }
        }
    )

    assert load_production_memoria_transport(storage) is None
    composition = create_production_v2_composition(storage)
    client = TestClient(create_v2_app(composition, now_provider=lambda: NOW))
    _create_and_bind(client, "session-invalid-transport")

    response = client.post(
        "/v2/sessions/session-invalid-transport/tick",
        json={"command_id": "cmd-invalid-transport"},
    )

    assert response.status_code == 200
    assert response.json()["events"][0]["payload"]["adapter_summary"] == {
        "mode": "noop",
        "runner": "planned_show",
        "external_adapter": "not_configured",
        "next_action": "run_planned_show",
    }
```

- [ ] **Step 3: Add enabled prefs composition red test**

Append:

```python
def test_production_composition_loads_enabled_memoria_transport_from_prefs(
    tmp_path,
    monkeypatch,
):
    import YouTubeBridgeV2.production as production

    storage = _storage_manager(tmp_path)
    storage.save_prefs(
        {
            MEMORIA_TRANSPORT_PREFS_KEY: {
                "enabled": True,
                "base_url": "http://127.0.0.1:8088",
                "api_key": "secret-token",
                "timeout_seconds": 4,
                "max_attempts": 1,
            }
        }
    )
    created = {}

    class CapturingTransport:
        def __init__(self, config):
            self.config = config
            self.requests = []
            created["transport"] = self
            created["summary"] = config.public_summary()

        def send(self, request):
            self.requests.append(request)
            return {
                "session_id": "memoria-enabled-production",
                "message_id": "enabled-1",
                "character_id": "host",
                "reply": "enabled production response",
            }

    monkeypatch.setattr(production, "MemoriaSyncHttpTransport", CapturingTransport)
    composition = create_production_v2_composition(storage)
    client = TestClient(create_v2_app(composition, now_provider=lambda: NOW))
    _create_and_bind(client, "session-enabled-transport")

    response = client.post(
        "/v2/sessions/session-enabled-transport/tick",
        json={"command_id": "cmd-enabled-transport"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert created["summary"] == {
        "base_url": "http://127.0.0.1:8088",
        "timeout_seconds": 4.0,
        "max_attempts": 1,
        "has_api_key": True,
    }
    assert len(created["transport"].requests) == 1
    assert (
        storage.get_v2_session("session-enabled-transport")["metadata"][
            "live_episode_plan_state"
        ]["last_memoria_session_id"]
        == "memoria-enabled-production"
    )
    assert "secret-token" not in repr(response.json())
```

- [ ] **Step 4: Run red tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_runtime_tick_vertical_slice.py::test_production_memoria_transport_requires_enabled_pref tests\youtubebridge_v2\test_runtime_tick_vertical_slice.py::test_production_memoria_transport_invalid_enabled_config_falls_back_noop tests\youtubebridge_v2\test_runtime_tick_vertical_slice.py::test_production_composition_loads_enabled_memoria_transport_from_prefs -q
```

Expected: FAIL because `load_production_memoria_transport` is not exported yet, and `production.MemoriaSyncHttpTransport` is not monkeypatchable at module scope.

---

### Task 2: Production Toggle Green Implementation

**Files:**
- Modify: `YouTubeBridgeV2/production.py`
- Test: `tests/youtubebridge_v2/test_runtime_tick_vertical_slice.py`

- [ ] **Step 1: Add imports**

Update `production.py` imports:

```python
from collections.abc import Mapping

from YouTubeBridgeV2.adapters.memoria_http import (
    MEMORIA_TRANSPORT_PREFS_KEY,
    MemoriaHttpConfigError,
    MemoriaSyncHttpTransport,
    parse_memoria_http_transport_config,
)
from YouTubeBridgeV2.composition import V2RuntimeComposition, create_v2_composition
```

- [ ] **Step 2: Update production composition**

Replace `create_production_v2_composition(...)` with:

```python
def create_production_v2_composition(
    storage_manager: object,
    *,
    memoria_transport: object | None = None,
) -> V2RuntimeComposition:
    """以主專案 StorageManager 建立 production V2 composition。"""

    resolved_memoria_transport = memoria_transport
    if resolved_memoria_transport is None:
        resolved_memoria_transport = load_production_memoria_transport(storage_manager)

    if resolved_memoria_transport is not None:
        from YouTubeBridgeV2.runtime.memoria_runners import (
            MemoriaAftertalkRunner,
            MemoriaClosingRunner,
            MemoriaPlannedShowRunner,
        )

        return create_v2_composition(
            storage_manager=storage_manager,
            planned_show_runner=MemoriaPlannedShowRunner(
                storage_manager,
                resolved_memoria_transport,
            ),
            aftertalk_runner=MemoriaAftertalkRunner(
                storage_manager,
                resolved_memoria_transport,
            ),
            closing_runner=MemoriaClosingRunner(
                storage_manager,
                resolved_memoria_transport,
            ),
        )

    return create_v2_composition(
        storage_manager=storage_manager,
        planned_show_runner=NoopPlannedShowRunner(),
        aftertalk_runner=NoopAftertalkRunner(),
        closing_runner=NoopClosingRunner(),
    )
```

- [ ] **Step 3: Add loader and helper functions**

Add below `create_production_v2_composition(...)`:

```python
def load_production_memoria_transport(storage_manager: object) -> object | None:
    """Load the opt-in MemoriaCore HTTP transport for production V2."""

    raw_config = _raw_memoria_transport_config(storage_manager)
    if not _production_memoria_transport_enabled(raw_config):
        return None
    try:
        config = parse_memoria_http_transport_config(raw_config)
    except MemoriaHttpConfigError:
        return None
    if config is None:
        return None
    return MemoriaSyncHttpTransport(config)


def _raw_memoria_transport_config(storage_manager: object) -> Mapping[str, object] | None:
    if not hasattr(storage_manager, "load_prefs"):
        return None
    try:
        prefs = storage_manager.load_prefs()
    except Exception:
        return None
    if not isinstance(prefs, Mapping):
        return None
    raw_config = prefs.get(MEMORIA_TRANSPORT_PREFS_KEY)
    if isinstance(raw_config, Mapping):
        return raw_config
    return None


def _production_memoria_transport_enabled(
    raw_config: Mapping[str, object] | None,
) -> bool:
    if raw_config is None:
        return False
    return _truthy(raw_config.get("enabled"))


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value == 1
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}
```

- [ ] **Step 4: Update exports**

Change `__all__`:

```python
__all__ = ["create_production_v2_composition", "load_production_memoria_transport"]
```

- [ ] **Step 5: Run green tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_runtime_tick_vertical_slice.py::test_production_memoria_transport_requires_enabled_pref tests\youtubebridge_v2\test_runtime_tick_vertical_slice.py::test_production_memoria_transport_invalid_enabled_config_falls_back_noop tests\youtubebridge_v2\test_runtime_tick_vertical_slice.py::test_production_composition_loads_enabled_memoria_transport_from_prefs -q
```

Expected: `3 passed`.

---

### Task 3: Preserve Explicit Injection And Main-App Safety

**Files:**
- Modify: `tests/youtubebridge_v2/test_runtime_tick_vertical_slice.py`
- Modify: `tests/youtubebridge_v2/test_main_app_wiring.py`

- [ ] **Step 1: Add explicit injection guard test**

Append to `test_runtime_tick_vertical_slice.py`:

```python
def test_production_composition_explicit_transport_overrides_disabled_prefs(tmp_path):
    storage = _storage_manager(tmp_path)
    storage.save_prefs(
        {
            MEMORIA_TRANSPORT_PREFS_KEY: {
                "enabled": False,
                "base_url": "http://127.0.0.1:8088",
            }
        }
    )
    transport = _transport()
    composition = create_production_v2_composition(storage, memoria_transport=transport)
    client = TestClient(create_v2_app(composition, now_provider=lambda: NOW))
    _create_and_bind(client, "session-explicit-transport")

    response = client.post(
        "/v2/sessions/session-explicit-transport/tick",
        json={"command_id": "cmd-explicit-transport"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert len(transport.requests) == 1
```

- [ ] **Step 2: Add main app invalid prefs safety test**

In `test_main_app_wiring.py`, extend imports:

```python
from YouTubeBridgeV2.adapters.memoria_http import MEMORIA_TRANSPORT_PREFS_KEY
```

Add helper near existing tests:

```python
def _bind_minimal_plan(client, session_id: str) -> None:
    client.post(
        "/v2/sessions",
        json={
            "command_id": f"{session_id}-create",
            "session_id": session_id,
            "aftertalk_policy": "auto",
        },
    )
    client.post(
        f"/v2/sessions/{session_id}/plan",
        json={
            "command_id": f"{session_id}-bind",
            "plan": {
                "plan_id": f"{session_id}-plan",
                "title": "Main app invalid transport",
                "turns": [
                    {
                        "id": "turn-1",
                        "purpose": "Confirm no-op when transport prefs are invalid.",
                        "topic_cue": "No accidental MemoriaCore external call.",
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
```

Append:

```python
def test_main_app_v2_invalid_memoria_transport_prefs_keep_tick_noop(
    tmp_path,
    monkeypatch,
):
    storage = _storage_manager(tmp_path)
    storage.save_prefs(
        {
            MEMORIA_TRANSPORT_PREFS_KEY: {
                "enabled": True,
                "base_url": "file:///tmp/memoria",
            }
        }
    )
    api_main = _install_test_storage(monkeypatch, storage)
    client = _loopback_client(api_main.app)
    _bind_minimal_plan(client, "session-main-invalid-transport")

    response = client.post(
        "/v2/sessions/session-main-invalid-transport/tick",
        json={"command_id": "cmd-main-invalid-transport"},
    )

    assert response.status_code == 200
    assert response.json()["events"][0]["payload"]["adapter_summary"] == {
        "mode": "noop",
        "runner": "planned_show",
        "external_adapter": "not_configured",
        "next_action": "run_planned_show",
    }
```

- [ ] **Step 3: Run tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_runtime_tick_vertical_slice.py::test_production_composition_explicit_transport_overrides_disabled_prefs tests\youtubebridge_v2\test_main_app_wiring.py::test_main_app_v2_invalid_memoria_transport_prefs_keep_tick_noop -q
```

Expected: `2 passed`.

---

### Task 4: Docs Sync

**Files:**
- Modify: `YouTubeBridgeV2/docs/modules/memoria-adapter.md`
- Modify: `YouTubeBridgeV2/docs/api-reference-index.md`
- Modify: `YouTubeBridgeV2/docs/architecture-index.md`

- [ ] **Step 1: Update module design**

In `docs/modules/memoria-adapter.md`, under `Failure Modes`, add:

```markdown
- production wiring 只有在 `youtubebridge_v2_memoria_transport.enabled` 明確啟用且 config valid 時建立真 Memoria HTTP transport；未設定、未啟用或設定錯誤時維持 no-op，不嘗試外呼。
```

Under `Public Entrypoints`, add:

```markdown
- `load_production_memoria_transport(storage_manager)`：production-only opt-in loader；回傳真 transport 或 `None` 讓 composition 維持 no-op。
```

- [ ] **Step 2: Update API reference**

In `docs/api-reference-index.md`, under production/composition concepts or MemoriaCore Adapter sources, add:

```markdown
- `load_production_memoria_transport`
```

Source:

```markdown
- `YouTubeBridgeV2/production.py::load_production_memoria_transport`
```

- [ ] **Step 3: Update architecture index**

In `docs/architecture-index.md`, update the Wave 2E-C/2E-D area:

```markdown
## Integration Wave 2E-D 狀態

- [x] Production wiring toggle：`create_production_v2_composition(...)` 未明確注入 transport 時，只在 prefs `youtubebridge_v2_memoria_transport.enabled` 明確啟用且 config valid 時建立 `MemoriaSyncHttpTransport`。
- [x] No-op fallback：未設定、未啟用或 invalid config 都維持 no-op runner，`/v2` tick 不意外外呼。
- [x] Explicit injection precedence：測試或手動 composition 傳入 `memoria_transport=` 時仍優先使用注入物件。
```

- [ ] **Step 4: Docs sanity check**

Run:

```powershell
rg -n "load_production_memoria_transport|Integration Wave 2E-D|youtubebridge_v2_memoria_transport.enabled|Production wiring toggle" YouTubeBridgeV2\docs YouTubeBridgeV2\production.py
```

Expected: finds production source and docs.

---

### Task 5: Final Verification For 2E-D

**Files:**
- Verify: `YouTubeBridgeV2/production.py`
- Verify: `tests/youtubebridge_v2/test_runtime_tick_vertical_slice.py`
- Verify: `tests/youtubebridge_v2/test_main_app_wiring.py`
- Verify: V2 docs touched in Task 4

- [ ] **Step 1: Run focused tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_runtime_tick_vertical_slice.py -q
python -m pytest tests\youtubebridge_v2\test_main_app_wiring.py -q
```

Expected: both pass.

- [ ] **Step 2: Run Wave 2E verification commands**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_memoria_adapter.py -q
python -m pytest tests\youtubebridge_v2\test_runtime_memoria_runners.py -q
python -m pytest tests\youtubebridge_v2\test_runtime_tick_vertical_slice.py -q
python -m pytest tests\youtubebridge_v2 -q
git diff --check
```

Expected:

- Focused suites pass.
- Full V2 suite passes; real Memoria integration remains skipped by default.
- `git diff --check` prints no whitespace errors.

- [ ] **Step 3: Check scope and forbidden imports**

Run:

```powershell
git status -sb
git diff --stat
rg -n "sqlite3|aiosqlite|\bfrom YouTubeBridge(\.|\s|$)|\bimport YouTubeBridge(\.|\s|$)" YouTubeBridgeV2\production.py tests\youtubebridge_v2\test_runtime_tick_vertical_slice.py tests\youtubebridge_v2\test_main_app_wiring.py
```

Expected:

- Changed files are limited to 2E-D source/tests/docs/plan.
- No direct SQLite imports in V2 production code.
- No Legacy `YouTubeBridge` runtime imports.

- [ ] **Step 4: Request code review**

Use `superpowers:requesting-code-review` with scope limited to roadmap `2E-D`. Review must check no-op fallback, explicit enable requirement, invalid config fallback, secret redaction, and no production external call without enabled prefs.

- [ ] **Step 5: Commit**

After review findings are fixed and verification is fresh:

```powershell
git add YouTubeBridgeV2\production.py tests\youtubebridge_v2\test_runtime_tick_vertical_slice.py tests\youtubebridge_v2\test_main_app_wiring.py YouTubeBridgeV2\docs\modules\memoria-adapter.md YouTubeBridgeV2\docs\api-reference-index.md YouTubeBridgeV2\docs\architecture-index.md YouTubeBridgeV2\docs\implementation-plans\memoria-production-wiring-toggle.md
git diff --cached --check
git commit -m "feat: gate production Memoria transport wiring"
```

---

## Self-Review

Spec coverage:

- `只有明確啟用 transport 時才會呼叫 MemoriaCore` covered by requiring `enabled=true-ish` before building `MemoriaSyncHttpTransport`.
- `未設定或設定錯誤時 /v2 不會意外外呼` covered by no-op tests for missing enabled and invalid config.
- `不要跨 wave` covered by no UI, no YouTube, no TTS, no scheduler, and no automatic MemoriaCore service startup.

Placeholder scan:

- No red-flag placeholder steps remain.
- Every code-changing task includes concrete code and commands.

Type consistency:

- `load_production_memoria_transport(storage_manager)` returns a transport object or `None`, matching `create_production_v2_composition(...)`.
- Explicit `memoria_transport` injection remains accepted by `create_production_v2_composition(...)`.
- Tests monkeypatch `YouTubeBridgeV2.production.MemoriaSyncHttpTransport`, so production module imports the class at module scope.

## Execution Handoff

Plan complete and saved to `YouTubeBridgeV2/docs/implementation-plans/memoria-production-wiring-toggle.md`. Two execution options:

**1. Subagent-Driven (recommended)** - dispatch a fresh worker for the production toggle and review.

**2. Inline Execution** - execute tasks in this session using `superpowers:executing-plans`, with checkpoints for review.
