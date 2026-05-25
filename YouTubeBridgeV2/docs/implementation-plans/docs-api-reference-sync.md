# Docs API Reference Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an automated docs/API sync audit and fix the current Server/API Surface public entrypoint drift for YouTubeBridgeV2 routes.

**Architecture:** Treat FastAPI route declarations in `YouTubeBridgeV2/server/routes.py` as the source of truth for V2 HTTP endpoints and endpoint function names. The new docs sync test compares that route set against `docs/api-reference-index.md` and `docs/modules/server-api-surface.md`, while allowing documented static assets to remain extra docs-only entries. Runtime code is not changed in this item.

**Tech Stack:** pytest, FastAPI `APIRoute`, regex-based Markdown extraction, docs index updates.

---

## Scope Boundary

- Implement only `Final Hardening / docs/API reference sync`.
- Do not edit `YouTubeBridgeV2/docs/roadmap.md` checkbox state.
- Do not change route behavior, permissions, request bodies, UI, or runtime services.
- Do not perform final code review or PR readiness in this item.
- Treat `YouTubeBridgeV2/server/routes.py` as source of truth for `/v2` HTTP routes; mounted static assets may be documented but are not generated from the APIRouter.

## File Structure

- Create `tests/youtubebridge_v2/test_docs_api_reference_sync.py`
  - Owns docs/API route and endpoint-name synchronization checks.
- Modify `YouTubeBridgeV2/docs/modules/server-api-surface.md`
  - Add missing `automation-control` public entrypoint and endpoint name.
- Modify `YouTubeBridgeV2/docs/architecture-index.md`
  - Add Final Hardening docs/API reference sync status.
- Modify `YouTubeBridgeV2/docs/api-reference-index.md`
  - Add an internal docs sync audit reference.

---

### Task 1: Docs/API Sync Tests

**Files:**
- Create: `tests/youtubebridge_v2/test_docs_api_reference_sync.py`

- [ ] **Step 1: Add sync tests**

Create `tests/youtubebridge_v2/test_docs_api_reference_sync.py`:

```python
from __future__ import annotations

import re
from pathlib import Path

from fastapi.routing import APIRoute

from YouTubeBridgeV2.server.routes import router


ROOT = Path(__file__).resolve().parents[2]
DOCS_ROOT = ROOT / "YouTubeBridgeV2" / "docs"
HTTP_ENDPOINT_RE = re.compile(r"`((?:GET|POST|DELETE|PUT|PATCH) /v2[^`]+)`")
ENDPOINT_NAME_RE = re.compile(r"`([a-z_]+_endpoint)`")


def _route_endpoints() -> set[str]:
    endpoints: set[str] = set()
    for route in router.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            endpoints.add(f"{method} {route.path_format}")
    return endpoints


def _route_endpoint_names() -> set[str]:
    return {
        route.name
        for route in router.routes
        if isinstance(route, APIRoute)
    }


def _section(text: str, start_marker: str, end_marker: str) -> str:
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    return text[start:end]


def _documented_endpoints(section: str) -> set[str]:
    return set(HTTP_ENDPOINT_RE.findall(section))


def _documented_endpoint_names(section: str) -> set[str]:
    return set(ENDPOINT_NAME_RE.findall(section))


def test_api_reference_server_surface_lists_all_v2_routes_and_endpoint_names():
    api_reference = (DOCS_ROOT / "api-reference-index.md").read_text(encoding="utf-8")
    section = _section(api_reference, "### Server/API Surface", "### API Key Management Endpoints")

    assert _route_endpoints() <= _documented_endpoints(section)
    assert _route_endpoint_names() <= _documented_endpoint_names(section)


def test_server_api_surface_module_lists_all_v2_routes_and_endpoint_names():
    module_doc = (DOCS_ROOT / "modules" / "server-api-surface.md").read_text(
        encoding="utf-8"
    )
    section = _section(module_doc, "## Public Entrypoints", "## Endpoint Boundary Rules")

    assert _route_endpoints() <= _documented_endpoints(section)
    assert _route_endpoint_names() <= _documented_endpoint_names(section)


def test_docs_api_reference_sync_is_documented_in_architecture_index():
    architecture_index = (DOCS_ROOT / "architecture-index.md").read_text(
        encoding="utf-8"
    )

    assert "Docs/API reference sync" in architecture_index
    assert "test_docs_api_reference_sync.py" in architecture_index
```

- [ ] **Step 2: Run sync tests and verify they fail**

```powershell
python -m pytest tests\youtubebridge_v2\test_docs_api_reference_sync.py -q
```

Expected:
- API reference server surface test passes.
- Server/API Surface module test fails because it is missing `POST /v2/sessions/{session_id}/automation-control` and `update_automation_control_endpoint`.
- Architecture status test fails because this Final Hardening item is not documented yet.

---

### Task 2: Fix Server/API Surface Drift

**Files:**
- Modify: `YouTubeBridgeV2/docs/modules/server-api-surface.md`

- [ ] **Step 1: Add missing public route bullet**

In the Public Entrypoints endpoint list, add this route immediately after `POST /v2/sessions/{session_id}/aftertalk-policy`:

```markdown
- `POST /v2/sessions/{session_id}/automation-control`
```

- [ ] **Step 2: Add missing endpoint function name**

In the Public Entrypoints function list, add this symbol immediately after `update_aftertalk_policy_endpoint`:

```markdown
- `update_automation_control_endpoint`
```

- [ ] **Step 3: Re-run the server module sync test**

```powershell
python -m pytest tests\youtubebridge_v2\test_docs_api_reference_sync.py::test_server_api_surface_module_lists_all_v2_routes_and_endpoint_names -q
```

Expected: PASS.

---

### Task 3: Document Final Hardening Docs Sync

**Files:**
- Modify: `YouTubeBridgeV2/docs/architecture-index.md`
- Modify: `YouTubeBridgeV2/docs/api-reference-index.md`

- [ ] **Step 1: Update architecture Final Hardening status**

Add this bullet under `## Final Hardening 狀態`:

```markdown
- [x] Docs/API reference sync：`tests/youtubebridge_v2/test_docs_api_reference_sync.py` 比對 `YouTubeBridgeV2/server/routes.py` 的 `/v2` routes 與 `docs/api-reference-index.md`、`docs/modules/server-api-surface.md`，避免 endpoint 或 route handler 文件漂移。
```

- [ ] **Step 2: Add internal API reference audit entry**

Add this entry near the other internal hardening references in `YouTubeBridgeV2/docs/api-reference-index.md`:

```markdown
### `tests/youtubebridge_v2/test_docs_api_reference_sync.py`

Purpose:
Final Hardening docs/API sync audit，驗證 V2 FastAPI route declarations 與 API reference / Server API Surface module doc 保持同步。

Params:
- 無。

Returns:
- pytest pass/fail audit result。

Raises:
- AssertionError — route endpoint、endpoint function name 或 Final Hardening docs status 漏列時。

Side Effects:
- 無；只讀取 route declarations 與 Markdown docs。

Since:
- `YouTubeBridgeV2 v0.1`

Stability:
- `internal`

Source:
- `tests/youtubebridge_v2/test_docs_api_reference_sync.py`
```

- [ ] **Step 3: Verify docs references**

```powershell
rg -n "Docs/API reference sync|test_docs_api_reference_sync|automation-control" YouTubeBridgeV2\docs
```

Expected: hits in architecture index, API reference index, server API surface module, roadmap, and this implementation plan.

- [ ] **Step 4: Re-run docs sync tests**

```powershell
python -m pytest tests\youtubebridge_v2\test_docs_api_reference_sync.py -q
```

Expected: `3 passed`.

---

### Task 4: Focused and Full Verification

**Files:**
- Verify: `tests/youtubebridge_v2/test_docs_api_reference_sync.py`
- Verify: `YouTubeBridgeV2/docs/modules/server-api-surface.md`
- Verify: `YouTubeBridgeV2/docs/architecture-index.md`
- Verify: `YouTubeBridgeV2/docs/api-reference-index.md`
- Verify: `YouTubeBridgeV2/docs/implementation-plans/docs-api-reference-sync.md`

- [ ] **Step 1: Run related docs/API suites**

```powershell
python -m pytest tests\youtubebridge_v2\test_docs_api_reference_sync.py tests\youtubebridge_v2\test_server_api_surface.py tests\youtubebridge_v2\test_main_app_security.py -q
```

Expected: all tests pass.

- [ ] **Step 2: Run full V2 suite**

```powershell
python -m pytest tests\youtubebridge_v2 -q
```

Expected: all non-opt-in tests pass; browser/external opt-in tests skip unless their env vars are explicitly set.

- [ ] **Step 3: Run diff checks**

```powershell
git diff --check
git diff -- YouTubeBridgeV2\docs\roadmap.md
```

Expected: `git diff --check` exits 0. Roadmap diff is empty.

- [ ] **Step 4: Commit this item only**

```powershell
git add tests\youtubebridge_v2\test_docs_api_reference_sync.py YouTubeBridgeV2\docs\modules\server-api-surface.md YouTubeBridgeV2\docs\architecture-index.md YouTubeBridgeV2\docs\api-reference-index.md YouTubeBridgeV2\docs\implementation-plans\docs-api-reference-sync.md
git commit -m "test: sync V2 API docs with routes"
```

Expected: one commit containing only the docs/API reference sync item.

---

## Self-Review

- Spec coverage: Covers only Final Hardening / docs/API reference sync by adding route-to-doc sync tests and fixing the known `automation-control` documentation drift. Final code review and PR readiness remain separate checklist items.
- Placeholder scan: No `TBD`, no open-ended TODO, no unspecified validation.
- Type consistency: Route endpoints use `APIRoute.path_format`, matching the documented `{session_id}` placeholder syntax. The regex extracts only backticked HTTP endpoint strings and `_endpoint` symbols.

Plan complete and saved to `YouTubeBridgeV2/docs/implementation-plans/docs-api-reference-sync.md`. Because the user asked to continue the roadmap goal directly, execute inline with `superpowers:executing-plans` for this single checklist item.
