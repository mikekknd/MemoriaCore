# PR Merge Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the Final Hardening `PR/merge readiness` item by running fresh final verification, recording branch readiness, and presenting integration options without merging or pushing by default.

**Architecture:** This is a release-readiness gate. It does not change runtime behavior; it records final verification evidence, branch/worktree state, residual opt-in test risks, and the exact next choices for merge or PR. It must use `superpowers:verification-before-completion` and `superpowers:finishing-a-development-branch`; the latter presents options after tests pass.

**Tech Stack:** pytest, git, Markdown readiness report.

---

## Scope Boundary

- Implement only `Final Hardening / PR/merge readiness`.
- Do not edit `YouTubeBridgeV2/docs/roadmap.md` checkbox state until merge/user confirmation.
- Do not push, create PR, merge, delete branch, or clean up worktree unless the user chooses that option after readiness is presented.
- Do not start hidden/background 8088 or 8091 services.
- Use the repo-required verification commands fresh in this item:
  - `python -m pytest tests\youtubebridge_v2 -q`
  - `python -m pytest -q`
  - `git diff --check`

## File Structure

- Create `YouTubeBridgeV2/docs/reviews/pr-merge-readiness.md`
  - Records final verification, branch state, and integration options.
- Modify `YouTubeBridgeV2/docs/architecture-index.md`
  - Add Final Hardening PR/merge readiness status.
- Modify `core/memory_analyzer.py` only if fresh full-suite verification exposes a repository blocker that prevents readiness completion.
- Modify `tests/test_chat_orchestrator_unit/test_memory_pipeline_format.py` only to add fail-first regressions for those full-suite blockers.
- Create this implementation plan file.

---

### Task 1: Fresh Final Verification

**Files:**
- Verify: entire repository

- [ ] **Step 1: Run V2 suite**

```powershell
python -m pytest tests\youtubebridge_v2 -q
```

Expected: all non-opt-in V2 tests pass; opt-in browser/external tests skip unless their env vars are explicitly set.

- [ ] **Step 2: Run full repository suite**

```powershell
python -m pytest -q
```

Expected: all tests pass or only documented skips. If this fails, stop and fix or document the blocker; do not mark readiness complete.

- [ ] **Step 3: Run diff whitespace check**

```powershell
git diff --check
```

Expected: exit 0. CRLF warnings are acceptable only when exit code remains 0 and no whitespace error is reported.

- [ ] **Step 4: Confirm roadmap remains unedited**

```powershell
git diff -- YouTubeBridgeV2\docs\roadmap.md
```

Expected: empty diff.

---

### Task 2: Branch and Worktree Readiness Detection

**Files:**
- Verify: git metadata only

- [ ] **Step 1: Capture git state**

```powershell
git status --short --branch
git rev-parse --abbrev-ref HEAD
git merge-base origin/main HEAD
git rev-parse HEAD
git log --oneline origin/main..HEAD
git rev-parse --git-dir
git rev-parse --git-common-dir
```

Expected: branch is named, worktree has only this item files dirty before commit, and no unrelated files are staged.

- [ ] **Step 2: Confirm no legacy files changed in branch diff**

```powershell
git diff --name-only origin/main...HEAD -- YouTubeBridge
```

Expected: empty output.

---

### Task 3: Record Readiness Report

**Files:**
- Create: `YouTubeBridgeV2/docs/reviews/pr-merge-readiness.md`
- Modify: `YouTubeBridgeV2/docs/architecture-index.md`

- [ ] **Step 1: Create readiness report**

Create `YouTubeBridgeV2/docs/reviews/pr-merge-readiness.md`:

```markdown
# YouTubeBridgeV2 PR/Merge Readiness

## Scope

- Branch: `codex/youtubebridge-v2-aftertalk`
- Base: `<merge-base-origin-main>`
- Head: `<head-sha>`
- Roadmap checkbox policy: `YouTubeBridgeV2/docs/roadmap.md` checkboxes were not edited before merge/user confirmation.

## Verification

- `python -m pytest tests\youtubebridge_v2 -q`: `<exact V2 result>`
- `python -m pytest -q`: `<exact full repository result>`
- `git diff --check`: exit 0; CRLF warnings only if present.
- `git diff -- YouTubeBridgeV2\docs\roadmap.md`: empty.

## Branch State

- Worktree type: `<normal repo or worktree details>`
- Current branch: `codex/youtubebridge-v2-aftertalk`
- Commits ahead of `origin/main`: `<count or log summary>`
- Legacy `YouTubeBridge/` branch diff: empty.

## Residual Risk

- Opt-in browser smoke tests remain skipped unless `YOUTUBEBRIDGE_V2_BROWSER_SMOKE=1` and local Chrome/live server env are provided.
- Opt-in full external E2E remains skipped unless `YB2_FULL_EXTERNAL_E2E=1` and live MemoriaCore env are provided.
- No merge, push, PR creation, or branch cleanup has been executed in this item.

## Integration Options

1. Merge back to `main` locally.
2. Push and create a Pull Request.
3. Keep the branch as-is.
4. Discard this work after explicit confirmation.
```

Replace all `<...>` placeholders with actual command output.

- [ ] **Step 2: Update architecture Final Hardening status**

Add this bullet under `## Final Hardening 狀態`:

```markdown
- [x] PR/merge readiness：`YouTubeBridgeV2/docs/reviews/pr-merge-readiness.md` 記錄完整 V2 suite、完整 repo suite、diff check、branch/worktree 狀態與 integration options；尚未 push、merge 或建立 PR。
```

- [ ] **Step 3: Verify report references**

```powershell
rg -n "PR/merge readiness|pr-merge-readiness|python -m pytest -q|Integration Options" YouTubeBridgeV2\docs
```

Expected: hits in architecture index, readiness report, and this implementation plan.

---

### Task 4: Commit Readiness Artifact

**Files:**
- Verify: `YouTubeBridgeV2/docs/reviews/pr-merge-readiness.md`
- Verify: `YouTubeBridgeV2/docs/architecture-index.md`
- Verify: `YouTubeBridgeV2/docs/implementation-plans/pr-merge-readiness.md`

- [ ] **Step 1: Re-run post-report diff checks**

```powershell
git diff --check
git diff -- YouTubeBridgeV2\docs\roadmap.md
```

Expected: `git diff --check` exits 0. Roadmap diff is empty.

- [ ] **Step 2: Commit this item only**

```powershell
git add YouTubeBridgeV2\docs\reviews\pr-merge-readiness.md YouTubeBridgeV2\docs\architecture-index.md YouTubeBridgeV2\docs\implementation-plans\pr-merge-readiness.md
git commit -m "docs: record V2 PR merge readiness"
```

Expected: one commit containing PR/merge readiness docs plus any minimal regression-backed blocker fixes required to make fresh full-repo verification pass.

---

### Task 5: Finishing Branch Options

**Files:**
- Verify: git metadata only

- [ ] **Step 1: Use finishing-a-development-branch**

After the readiness commit, use `superpowers:finishing-a-development-branch`:

```powershell
python -m pytest tests\youtubebridge_v2 -q
git status --short --branch
git rev-parse --git-dir
git rev-parse --git-common-dir
git merge-base HEAD main
```

Expected: V2 suite passes, branch is clean, and environment state is known.

- [ ] **Step 2: Present exactly the standard integration options**

Report:

```text
Implementation complete. What would you like to do?

1. Merge back to main locally
2. Push and create a Pull Request
3. Keep the branch as-is
4. Discard this work

Which option?
```

Do not execute any option until the user chooses.

---

## Self-Review

- Spec coverage: Covers only PR/merge readiness. It intentionally does not push, create a PR, merge, delete branches, or edit roadmap checkboxes without user confirmation.
- Placeholder scan: Report template placeholders must be replaced with real command outputs before commit.
- Type consistency: Readiness report and architecture status both reference `YouTubeBridgeV2/docs/reviews/pr-merge-readiness.md`.

Plan complete and saved to `YouTubeBridgeV2/docs/implementation-plans/pr-merge-readiness.md`. Execute inline with `superpowers:executing-plans`, then apply `superpowers:verification-before-completion` and `superpowers:finishing-a-development-branch` before the final response.
