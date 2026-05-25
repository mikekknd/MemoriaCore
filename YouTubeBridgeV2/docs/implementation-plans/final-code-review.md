# Final Code Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the Final Hardening `final code review` item with a documented, thread-aware review of the YouTubeBridgeV2 roadmap branch and any fixes needed before PR readiness.

**Architecture:** This item is a review gate, not a feature wave. It captures branch scope, audits high-risk boundaries added across the roadmap, fixes confirmed review findings, and saves a review report under `YouTubeBridgeV2/docs/reviews/`. Because the current user request did not explicitly authorize subagent dispatch, perform the review locally using the `superpowers:requesting-code-review` checklist and document that limitation.

**Tech Stack:** git diff/log, pytest focused suites, source inspection, Markdown review report.

---

## Scope Boundary

- Implement only `Final Hardening / final code review`.
- Do not edit `YouTubeBridgeV2/docs/roadmap.md` checkbox state.
- Do not perform PR/merge readiness, push, PR creation, or branch finishing in this item.
- Do not run hidden/background 8088 or 8091 services.
- If the review finds Critical or Important issues, fix them before marking this item complete.
- Full repository `python -m pytest -q` is reserved for PR/merge readiness unless a review finding requires it here; this item uses V2 and focused review suites.

## File Structure

- Modify `tests/youtubebridge_v2/test_docs_api_reference_sync.py`
  - Tighten docs sync assertions if review finds the existing subset check can miss stale documented endpoints.
- Create `YouTubeBridgeV2/docs/reviews/final-code-review.md`
  - Records scope, review checklist, findings, fixes, verification, and residual risks.
- Modify `YouTubeBridgeV2/docs/architecture-index.md`
  - Add Final Hardening final code review status.

---

### Task 1: Capture Review Scope

**Files:**
- Verify: git metadata only

- [ ] **Step 1: Refresh remote main metadata**

```powershell
git fetch origin main --prune
```

Expected: command exits 0. If network is unavailable, continue with local `origin/main` and record that the remote refresh failed.

- [ ] **Step 2: Capture base/head and changed files**

```powershell
git merge-base origin/main HEAD
git rev-parse HEAD
git diff --stat origin/main...HEAD -- YouTubeBridgeV2 tests/youtubebridge_v2 api/main.py core/storage/youtube_bridge_v2.py core/storage_manager.py
git diff --name-only origin/main...HEAD -- YouTubeBridgeV2 tests/youtubebridge_v2 api/main.py core/storage/youtube_bridge_v2.py core/storage_manager.py
```

Expected: output identifies the V2 roadmap branch scope and no unexpected legacy `YouTubeBridge/` file changes.

---

### Task 2: Review High-Risk Boundaries

**Files:**
- Verify: `api/main.py`
- Verify: `YouTubeBridgeV2/server/routes.py`
- Verify: `YouTubeBridgeV2/runtime/*`
- Verify: `YouTubeBridgeV2/adapters/*`
- Verify: `YouTubeBridgeV2/presentation/*`
- Verify: `YouTubeBridgeV2/docs/*`
- Verify: `tests/youtubebridge_v2/*`

- [ ] **Step 1: Run focused final-hardening suites**

```powershell
python -m pytest tests\youtubebridge_v2\test_full_external_e2e.py tests\youtubebridge_v2\test_main_app_lifecycle.py tests\youtubebridge_v2\test_legacy_boundary_audit.py tests\youtubebridge_v2\test_docs_api_reference_sync.py -q
```

Expected: all always-on tests pass and opt-in external/browser tests skip unless explicitly enabled.

- [ ] **Step 2: Run V2 full suite**

```powershell
python -m pytest tests\youtubebridge_v2 -q
```

Expected: all non-opt-in V2 tests pass.

- [ ] **Step 3: Inspect docs sync test strictness**

Read `tests/youtubebridge_v2/test_docs_api_reference_sync.py`. If it only checks route endpoints as a subset of documented endpoints, treat that as an Important review finding because stale documented routes could survive the sync audit.

- [ ] **Step 4: If strictness finding exists, fix it**

Replace subset assertions:

```python
assert _route_endpoints() <= _documented_endpoints(section)
assert _route_endpoint_names() <= _documented_endpoint_names(section)
```

with equality assertions:

```python
assert _documented_endpoints(section) == _route_endpoints()
assert _documented_endpoint_names(section) == _route_endpoint_names()
```

Apply the same replacement in both API reference and Server/API Surface module tests.

- [ ] **Step 5: Verify the strictness fix**

```powershell
python -m pytest tests\youtubebridge_v2\test_docs_api_reference_sync.py -q
```

Expected: `3 passed`.

---

### Task 3: Save Final Code Review Report

**Files:**
- Create: `YouTubeBridgeV2/docs/reviews/final-code-review.md`
- Modify: `YouTubeBridgeV2/docs/architecture-index.md`

- [ ] **Step 1: Create review report**

Create `YouTubeBridgeV2/docs/reviews/final-code-review.md`:

```markdown
# YouTubeBridgeV2 Final Code Review

## Scope

- Branch: `codex/youtubebridge-v2-aftertalk`
- Base: `<merge-base-origin-main>`
- Head: `<head-sha>`
- Scope reviewed: `YouTubeBridgeV2/`, `tests/youtubebridge_v2/`, `api/main.py`, `core/storage/youtube_bridge_v2.py`, and `core/storage_manager.py` changes on this branch.
- Reviewer mode: local `superpowers:requesting-code-review` checklist; no reviewer subagent was dispatched because this session does not have an explicit user request to delegate to subagents.

## Findings

- Important, fixed: `tests/youtubebridge_v2/test_docs_api_reference_sync.py` originally accepted route endpoints and endpoint names as a subset of docs. That could miss stale documented endpoints. The check now requires exact equality for APIRouter-backed endpoint lists and route handler names.
- No remaining Critical or Important findings after the fix.

## Review Checks

- Final-hardening suites pass.
- Full V2 pytest suite passes.
- Legacy boundary audit covers V2 source and `api/main.py` V2 mount against legacy runtime imports, direct SQLite, `requests`, Google transport, and direct legacy runtime path references.
- Docs/API sync audit covers APIRouter route endpoint strings and endpoint function names against API reference and Server/API Surface docs.
- Startup/shutdown validation covers main app lifespan with V2 mounted and cancelled task awaiting.
- Full external E2E remains skipped by default unless explicit external env vars are provided.

## Residual Risk

- Opt-in browser and external E2E paths are skipped by default unless the operator supplies local Chrome / live service env vars.
- Full repository pytest and PR/push readiness are reserved for the next `PR/merge readiness` roadmap item.
```

Replace `<merge-base-origin-main>` and `<head-sha>` with the actual command outputs.

- [ ] **Step 2: Update architecture Final Hardening status**

Add this bullet under `## Final Hardening 狀態`:

```markdown
- [x] Final code review：`YouTubeBridgeV2/docs/reviews/final-code-review.md` 記錄 thread-aware review scope、發現事項、修正與驗證；目前無剩餘 Critical/Important finding，PR/merge readiness 留給下一項。
```

- [ ] **Step 3: Verify review docs references**

```powershell
rg -n "Final code review|final-code-review|requesting-code-review|Important, fixed" YouTubeBridgeV2\docs
```

Expected: hits in architecture index, review report, and this implementation plan.

---

### Task 4: Final Validation and Commit

**Files:**
- Verify: `tests/youtubebridge_v2/test_docs_api_reference_sync.py`
- Verify: `YouTubeBridgeV2/docs/reviews/final-code-review.md`
- Verify: `YouTubeBridgeV2/docs/architecture-index.md`
- Verify: `YouTubeBridgeV2/docs/implementation-plans/final-code-review.md`

- [ ] **Step 1: Run focused final-hardening suites**

```powershell
python -m pytest tests\youtubebridge_v2\test_full_external_e2e.py tests\youtubebridge_v2\test_main_app_lifecycle.py tests\youtubebridge_v2\test_legacy_boundary_audit.py tests\youtubebridge_v2\test_docs_api_reference_sync.py -q
```

Expected: all always-on tests pass; external E2E skips by default.

- [ ] **Step 2: Run full V2 suite**

```powershell
python -m pytest tests\youtubebridge_v2 -q
```

Expected: all non-opt-in V2 tests pass.

- [ ] **Step 3: Run diff checks**

```powershell
git diff --check
git diff -- YouTubeBridgeV2\docs\roadmap.md
```

Expected: `git diff --check` exits 0. Roadmap diff is empty.

- [ ] **Step 4: Commit this item only**

```powershell
git add tests\youtubebridge_v2\test_docs_api_reference_sync.py YouTubeBridgeV2\docs\reviews\final-code-review.md YouTubeBridgeV2\docs\architecture-index.md YouTubeBridgeV2\docs\implementation-plans\final-code-review.md
git commit -m "docs: record V2 final code review"
```

Expected: one commit containing only the final code review item and any fix found during review.

---

## Self-Review

- Spec coverage: Covers only Final Hardening / final code review. PR/merge readiness, push, PR creation, and branch finishing remain separate.
- Placeholder scan: No `TBD`, no open-ended TODO. The report template requires actual base/head values before commit.
- Type consistency: The docs sync test equality check compares `set[str]` to `set[str]`, and the review report path is referenced consistently as `YouTubeBridgeV2/docs/reviews/final-code-review.md`.

Plan complete and saved to `YouTubeBridgeV2/docs/implementation-plans/final-code-review.md`. Because the user asked to continue the roadmap goal directly, execute inline with `superpowers:executing-plans` for this single checklist item.
