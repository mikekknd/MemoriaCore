# Operator Episode Plan Picker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let Operator Console load existing `runtime/YouTubeBridge/EpisodePlans/**/episode-plan.json` packages through an operator-only dropdown, then bind through the existing plan JSON workflow.

**Architecture:** Add a read-only `/v2/episode-plans` route that scans local EpisodePlans child packages, skips invalid JSON, sanitizes private fields, and returns bindable plan payloads without absolute paths. Extend Operator Console with a select/load control that fills the existing textarea; the existing `POST /v2/sessions/{session_id}/plan` remains the only bind action.

**Tech Stack:** FastAPI router, existing V2 security middleware, plain JavaScript Operator Console, pytest, Node ESM UI tests, existing docs sync tests.

---

### Task 1: Read-Only Episode Plan API

**Files:**
- Modify: `tests/youtubebridge_v2/test_server_api_surface.py`
- Modify: `tests/youtubebridge_v2/test_main_app_security.py`
- Modify: `YouTubeBridgeV2/server/routes.py`
- Modify: `YouTubeBridgeV2/server/main_security.py`
- Modify: `YouTubeBridgeV2/server/security.py`

- [x] **Step 1: Write failing API route tests**

Add a server API test that monkeypatches `routes.runtime_path(...)` to a temp EpisodePlans root containing two valid child `episode-plan.json` files and one invalid file. Assert `GET /v2/episode-plans` returns only valid packages, includes relative folder metadata, includes a sanitized `plan`, and does not expose private keys.

- [x] **Step 2: Verify the API test fails**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_server_api_surface.py::test_list_episode_plans_reads_child_episode_plan_json_packages -q
```

Expected: `404` or missing route failure.

- [x] **Step 3: Implement the read-only route**

Add route helpers in `YouTubeBridgeV2/server/routes.py`:
- Resolve root with `runtime_path("YouTubeBridge", "EpisodePlans")`.
- Scan `root.rglob("episode-plan.json")`.
- Load UTF-8 JSON only when the root exists and the decoded value is an object.
- Skip invalid JSON and non-object payloads.
- Sanitize with the route module's existing public payload sanitizer.
- Project planner-format `segments[].planned_turn_contracts` into top-level `turns`
  when the file does not already contain V2 bindable turns.
- Return `{"episode_plans": [...]}` sorted by `title`, `plan_id`, and relative folder.

- [x] **Step 4: Add operator-only security mapping**

Add `/v2/episode-plans` to the main-app route requirement as operator-only, map route id `episode_plans` to a new `read_episode_plans` action, and include that action in operator permissions.

- [x] **Step 5: Verify API/security tests pass**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_server_api_surface.py::test_list_episode_plans_reads_child_episode_plan_json_packages tests\youtubebridge_v2\test_main_app_security.py::test_main_app_v2_episode_plans_requires_operator_permission -q
```

Expected: both tests pass.

### Task 2: Operator Console Dropdown

**Files:**
- Modify: `tests/youtubebridge_v2/test_operator_console_ui.py`
- Modify: `YouTubeBridgeV2/static/operator-console/operator-console.js`
- Modify: `YouTubeBridgeV2/static/operator-console/operator-console.css`
- Modify: `static/locales/zh-TW.json`
- Modify: `static/locales/en-US.json`

- [x] **Step 1: Write failing UI tests**

Add Node UI tests for:
- `EpisodePlanListCommand.send(...)` calling `GET /v2/episode-plans` and returning sanitized plan packages.
- Operator controls rendering a plan select, refresh button, and load button for operators.
- Clicking load copies the selected plan into the existing `plan-json-input` textarea without calling the bind endpoint.

- [x] **Step 2: Verify UI tests fail**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_operator_console_ui.py::test_episode_plan_list_command_fetches_plan_packages_without_private_payload tests\youtubebridge_v2\test_operator_console_ui.py::test_operator_console_renders_episode_plan_picker_for_operator tests\youtubebridge_v2\test_operator_console_ui.py::test_episode_plan_picker_loads_selected_plan_into_textarea -q
```

Expected: missing command or missing controls failure.

- [x] **Step 3: Implement dropdown and load behavior**

Add `EpisodePlanListCommand`, normalize `episode_plans`, render select/options above the existing textarea, and bind refresh/load handlers. Loading a selected plan must only set textarea content; binding remains explicit through `Bind Plan`.

- [x] **Step 4: Add translations and responsive CSS**

Add i18n keys for the picker labels and empty state, and keep the picker controls in a stable responsive grid.

- [x] **Step 5: Verify UI tests pass**

Run the same three UI tests plus `test_operator_console_i18n_keys_are_registered`.

### Task 3: Docs Sync and Regression

**Files:**
- Modify: `YouTubeBridgeV2/docs/api-reference-index.md`
- Modify: `YouTubeBridgeV2/docs/modules/server-api-surface.md`

- [x] **Step 1: Update endpoint docs**

Document `GET /v2/episode-plans`, `list_episode_plans_endpoint`, and the operator-only permission boundary in both API reference and Server/API Surface module docs.

- [x] **Step 2: Run docs sync test**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_docs_api_reference_sync.py -q
```

Expected: route and docs endpoint sets match exactly.

- [x] **Step 3: Run targeted regression suite**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_server_api_surface.py tests\youtubebridge_v2\test_main_app_security.py tests\youtubebridge_v2\test_operator_console_ui.py tests\youtubebridge_v2\test_docs_api_reference_sync.py -q
```

Expected: all targeted tests pass.

- [x] **Step 4: Check final diff hygiene**

Run:

```powershell
git diff --check
git status --short
```

Expected: no whitespace errors; changed files are limited to this feature's API, UI, tests, docs, and plan.
