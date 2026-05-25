# Startup Shutdown Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add automated validation that the main FastAPI app can start with YouTubeBridgeV2 mounted, serve V2 routes/static assets, and shut down lifecycle tasks cleanly without starting external services by default.

**Architecture:** Keep the validation at the main app boundary because production V2 is mounted through `api/main.py`, not through the standalone `create_v2_app(...)` factory. Add a small shutdown helper so every lifespan background task is cancelled and awaited together, then cover it with focused tests. The tests use real `StorageManager` temp paths and fake bot/background dependencies, so they do not start 8088, YouTube, MemoriaCore, Telegram, Discord, or TTS.

**Tech Stack:** FastAPI `TestClient`, pytest, pytest-asyncio, real temp `StorageManager`, `api.main` monkeypatch fakes, V2 docs/API index updates.

---

## Scope Boundary

- Implement only `Final Hardening / startup/shutdown validation`.
- Do not edit `YouTubeBridgeV2/docs/roadmap.md` checkbox state.
- Do not start the real 8088 service unless the user explicitly requests a foreground smoke run.
- Do not implement Legacy boundary audit, docs sync, final code review, PR readiness, YouTube polling, real TTS provider, or new background scheduler behavior.
- Do not weaken current startup behavior; lifecycle tests must use fakes around existing dependency injection points.
- The validation must prove default startup does not call optional external background gather when prefs do not request it.

## File Structure

- Modify `api/main.py`
  - Add `_cancel_lifespan_tasks(...)`.
  - Use it in shutdown instead of awaiting cancelled tasks one by one.
- Create `tests/youtubebridge_v2/test_main_app_lifecycle.py`
  - Owns startup/shutdown validation for the production main app V2 mount.
  - Owns unit coverage for the shutdown task cancellation helper.
- Modify `YouTubeBridgeV2/docs/architecture-index.md`
  - Add Final Hardening startup/shutdown validation status.
- Modify `YouTubeBridgeV2/docs/api-reference-index.md`
  - Add the lifecycle helper as an internal hardening reference.
- Modify `YouTubeBridgeV2/docs/modules/server-api-surface.md`
  - Document production startup/shutdown validation coverage and the no-hidden-server rule.

---

### Task 1: Shutdown Task Cancellation Helper

**Files:**
- Modify: `api/main.py`
- Create: `tests/youtubebridge_v2/test_main_app_lifecycle.py`

- [ ] **Step 1: Write the failing helper test**

Create `tests/youtubebridge_v2/test_main_app_lifecycle.py` with this initial content:

```python
from __future__ import annotations

import asyncio

import pytest


@pytest.mark.asyncio
async def test_lifespan_shutdown_awaits_all_cancelled_background_tasks():
    import api.main as api_main

    finalized: list[str] = []

    async def worker(name: str) -> None:
        try:
            await asyncio.Event().wait()
        finally:
            finalized.append(name)

    cleanup_task = asyncio.create_task(worker("cleanup"))
    persona_task = asyncio.create_task(worker("persona"))
    await asyncio.sleep(0)

    await api_main._cancel_lifespan_tasks(cleanup_task, None, persona_task)

    assert cleanup_task.done()
    assert persona_task.done()
    assert set(finalized) == {"cleanup", "persona"}
```

- [ ] **Step 2: Run the helper test and verify it fails**

```powershell
python -m pytest tests\youtubebridge_v2\test_main_app_lifecycle.py::test_lifespan_shutdown_awaits_all_cancelled_background_tasks -q
```

Expected: FAIL with `AttributeError: module 'api.main' has no attribute '_cancel_lifespan_tasks'`.

- [ ] **Step 3: Add the lifecycle helper**

In `api/main.py`, add this helper below `_should_log_persona_sync_skip(...)`:

```python
async def _cancel_lifespan_tasks(*tasks: asyncio.Task | None) -> None:
    """Cancel and await lifespan background tasks during shutdown."""

    active_tasks = [task for task in tasks if task is not None]
    for task in active_tasks:
        task.cancel()
    if not active_tasks:
        return

    results = await asyncio.gather(*active_tasks, return_exceptions=True)
    for result in results:
        if result is None or isinstance(result, asyncio.CancelledError):
            continue
        if isinstance(result, BaseException):
            raise result
```

- [ ] **Step 4: Wire the helper into shutdown**

Replace the shutdown cancellation block in `api/main.py`:

```python
    cleanup_task.cancel()
    if bg_gather_task:
        bg_gather_task.cancel()
    persona_sync_task.cancel()

    try:
        await cleanup_task
        if bg_gather_task:
            await bg_gather_task
        await persona_sync_task
    except asyncio.CancelledError:
        pass
```

with:

```python
    await _cancel_lifespan_tasks(cleanup_task, bg_gather_task, persona_sync_task)
```

- [ ] **Step 5: Run the helper test and verify it passes**

```powershell
python -m pytest tests\youtubebridge_v2\test_main_app_lifecycle.py::test_lifespan_shutdown_awaits_all_cancelled_background_tasks -q
```

Expected: PASS.

---

### Task 2: Main App V2 Startup/Shutdown Smoke

**Files:**
- Modify: `tests/youtubebridge_v2/test_main_app_lifecycle.py`

- [ ] **Step 1: Add fake lifecycle dependencies and startup test**

Append this code to `tests/youtubebridge_v2/test_main_app_lifecycle.py`:

```python
from types import SimpleNamespace

from fastapi.testclient import TestClient

from core.storage_manager import StorageManager


class _FakeBotManager:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def sync_from_registry(self) -> None:
        self.calls.append("sync")

    async def stop_all(self) -> None:
        self.calls.append("stop")


class _FakePersonaSyncManager:
    async def should_run(self, *args, **kwargs):
        return False, "not_due"

    async def run_sync(self, *args, **kwargs):
        raise AssertionError("persona sync should not run during lifecycle smoke")


def _storage_manager(tmp_path) -> StorageManager:
    return StorageManager(
        prefs_file=str(tmp_path / "prefs.json"),
        history_file=str(tmp_path / "history.json"),
        persona_snapshot_db_path=str(tmp_path / "persona_snapshots.db"),
        youtube_bridge_v2_db_path=str(tmp_path / "youtubebridge_v2.db"),
    )


def _install_lifecycle_fakes(monkeypatch, tmp_path, prefs: dict[str, object] | None = None):
    import api.main as api_main

    storage = _storage_manager(tmp_path)
    storage.save_prefs(prefs or {})
    startup_calls: list[str] = []
    telegram = _FakeBotManager()
    discord = _FakeBotManager()

    async def forbidden_background_gather(*args, **kwargs):
        raise AssertionError("background gather should not start without tavily_api_key")

    monkeypatch.setattr(api_main, "init_all", lambda: startup_calls.append("init_all"))
    monkeypatch.setattr(api_main, "get_storage", lambda: storage)
    monkeypatch.setattr(api_main, "get_memory_sys", lambda: SimpleNamespace(db_path=""))
    monkeypatch.setattr(api_main, "get_router", lambda: object())
    monkeypatch.setattr(api_main, "get_persona_sync_manager", lambda: _FakePersonaSyncManager())
    monkeypatch.setattr(api_main, "get_telegram_bot_manager", lambda: telegram)
    monkeypatch.setattr(api_main, "get_discord_bot_manager", lambda: discord)
    monkeypatch.setattr(api_main, "is_db_maintenance_mode", lambda: False)
    monkeypatch.setattr(api_main, "start_background_gather_loop", forbidden_background_gather)
    monkeypatch.setattr(api_main, "_v2_composition_cache", None, raising=False)
    monkeypatch.setattr(api_main, "_v2_composition_storage_id", None, raising=False)
    return api_main, storage, startup_calls, telegram, discord


def test_main_app_lifespan_starts_v2_routes_and_shuts_down_managers(tmp_path, monkeypatch):
    api_main, storage, startup_calls, telegram, discord = _install_lifecycle_fakes(
        monkeypatch,
        tmp_path,
    )

    with TestClient(api_main.app, client=("127.0.0.1", 50000)) as client:
        static_response = client.get("/v2/static/operator-console/index.html")
        create_response = client.post(
            "/v2/sessions",
            json={
                "command_id": "lifecycle-create",
                "session_id": "lifecycle-session",
                "aftertalk_policy": "auto",
            },
        )

    assert static_response.status_code == 200
    assert 'id="operatorConsoleRoot"' in static_response.text
    assert create_response.status_code == 200
    assert create_response.json()["phase"] == "planned_show"
    assert storage.get_v2_session("lifecycle-session")["current_phase"] == "planned_show"
    assert startup_calls == ["init_all"]
    assert telegram.calls == ["sync", "stop"]
    assert discord.calls == ["sync", "stop"]
```

- [ ] **Step 2: Run the startup smoke and verify it passes**

```powershell
python -m pytest tests\youtubebridge_v2\test_main_app_lifecycle.py::test_main_app_lifespan_starts_v2_routes_and_shuts_down_managers -q
```

Expected: PASS.

- [ ] **Step 3: Run the lifecycle test file**

```powershell
python -m pytest tests\youtubebridge_v2\test_main_app_lifecycle.py -q
```

Expected: `2 passed`.

---

### Task 3: Documentation Sync for Startup/Shutdown Validation

**Files:**
- Modify: `YouTubeBridgeV2/docs/architecture-index.md`
- Modify: `YouTubeBridgeV2/docs/api-reference-index.md`
- Modify: `YouTubeBridgeV2/docs/modules/server-api-surface.md`

- [ ] **Step 1: Update architecture status**

In `YouTubeBridgeV2/docs/architecture-index.md`, update the `## Final Hardening 狀態` section to include startup/shutdown validation:

```markdown
- [x] Startup/shutdown validation：`tests/youtubebridge_v2/test_main_app_lifecycle.py` 驗證主 FastAPI app lifespan 可啟動 V2 durable routes/static、shutdown 會 stop bot managers，並確保取消的 lifespan background tasks 都被 await。
```

Keep the existing full external E2E bullets unchanged.

- [ ] **Step 2: Update API reference index**

In `YouTubeBridgeV2/docs/api-reference-index.md`, add an internal hardening reference near the existing app/composition entries:

```markdown
### `api.main._cancel_lifespan_tasks(...)`

Purpose:
取消並 await 主 FastAPI lifespan 建立的 background tasks，確保 shutdown 不會因第一個 `CancelledError` 提早結束而漏掉後續 task。

Params:
- `*tasks: asyncio.Task | None` — lifespan startup 建立的可選 background tasks。

Returns:
- `None`。

Raises:
- `BaseException` — task 以非 cancellation exception 結束時會重新拋出，避免 shutdown 靜默吞掉非預期錯誤。

Side Effects:
- 取消並 await 傳入的 background tasks；不啟動外部服務、不寫入 V2 storage。

Since:
- `YouTubeBridgeV2 v0.1`

Stability:
- `internal`

Source:
- `api/main.py::_cancel_lifespan_tasks`
```

- [ ] **Step 3: Update Server/API Surface module notes**

In `YouTubeBridgeV2/docs/modules/server-api-surface.md`, add this paragraph near the Public Entrypoints or Test Strategy section:

```markdown
Final Hardening startup/shutdown validation 使用 `tests/youtubebridge_v2/test_main_app_lifecycle.py` 驗證 production main app lifespan：startup 後 `/v2` routes 與 `/v2/static` 可用，shutdown 會 stop bot managers 並 await 已取消 background tasks。此測試不啟動真 8088；若需要 live 8088 smoke，必須依 repo 規則以前景 `start.bat` 視窗啟動。
```

- [ ] **Step 4: Verify docs references**

```powershell
rg -n "Startup/shutdown validation|_cancel_lifespan_tasks|test_main_app_lifecycle|foreground" YouTubeBridgeV2\docs
```

Expected: hits in architecture index, API reference index, server API surface, and this implementation plan.

---

### Task 4: Final Validation and Commit

**Files:**
- Verify: `api/main.py`
- Verify: `tests/youtubebridge_v2/test_main_app_lifecycle.py`
- Verify: `YouTubeBridgeV2/docs/architecture-index.md`
- Verify: `YouTubeBridgeV2/docs/api-reference-index.md`
- Verify: `YouTubeBridgeV2/docs/modules/server-api-surface.md`
- Verify: `YouTubeBridgeV2/docs/implementation-plans/startup-shutdown-validation.md`

- [ ] **Step 1: Run focused lifecycle tests**

```powershell
python -m pytest tests\youtubebridge_v2\test_main_app_lifecycle.py -q
```

Expected: `2 passed`.

- [ ] **Step 2: Run main app focused V2 tests**

```powershell
python -m pytest tests\youtubebridge_v2\test_main_app_lifecycle.py tests\youtubebridge_v2\test_main_app_wiring.py tests\youtubebridge_v2\test_main_app_security.py -q
```

Expected: all tests pass.

- [ ] **Step 3: Run full V2 suite**

```powershell
python -m pytest tests\youtubebridge_v2 -q
```

Expected: all non-opt-in tests pass; browser/external opt-in tests skip unless their env vars are explicitly set.

- [ ] **Step 4: Run diff checks**

```powershell
git diff --check
git diff -- YouTubeBridgeV2\docs\roadmap.md
```

Expected: `git diff --check` exits 0. Roadmap diff is empty because checkbox state is intentionally not edited in this branch until merge/user confirmation.

- [ ] **Step 5: Commit this item only**

```powershell
git add api\main.py tests\youtubebridge_v2\test_main_app_lifecycle.py YouTubeBridgeV2\docs\architecture-index.md YouTubeBridgeV2\docs\api-reference-index.md YouTubeBridgeV2\docs\modules\server-api-surface.md YouTubeBridgeV2\docs\implementation-plans\startup-shutdown-validation.md
git commit -m "test: validate V2 startup shutdown lifecycle"
```

Expected: one commit containing only the startup/shutdown validation item.

---

## Self-Review

- Spec coverage: Covers only Final Hardening / startup/shutdown validation. It validates main app startup, V2 route/static availability, manager shutdown, cancelled task awaiting, and default no-external background behavior. Legacy boundary audit, docs/API reference sync as a standalone final pass, final code review, and PR readiness remain separate checklist items.
- Placeholder scan: No `TBD`, no open-ended TODO, no unspecified test behavior.
- Type consistency: `_cancel_lifespan_tasks(*tasks: asyncio.Task | None) -> None` matches the test and the shutdown wiring. Fake bot managers provide the exact async methods used by `api.main.lifespan`.

Plan complete and saved to `YouTubeBridgeV2/docs/implementation-plans/startup-shutdown-validation.md`. Because the user asked to continue the roadmap goal directly, execute inline with `superpowers:executing-plans` for this single checklist item.
