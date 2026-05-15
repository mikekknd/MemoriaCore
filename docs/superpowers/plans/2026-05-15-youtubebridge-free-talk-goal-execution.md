# YouTubeBridge Free Talk Goal Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan with worker and verifier subagents. The main orchestrator monitors flow, reviews outputs, runs final verification, and updates roadmap status. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provide a `/goal`-friendly execution plan that runs the free-talk feature as four independently testable vertical slices. The agent must complete and verify one stage before starting the next, with browser E2E evidence at every stage.

**Architecture:** This is an orchestration plan, not a new runtime module. It points workers to the four stage implementation plans, defines hard gates between them, and requires each stage to leave the system in a usable state that can be started, exercised through `/studio/`, stopped, and inspected.

**Tech Stack:** Python, FastAPI, SQLite storage helpers, YouTubeBridge session/director runtime, Studio static HTML/CSS/JS, pytest, Codex Browser / in-app browser E2E.

---

## Stage Plan Index

Implement these plans in order. Do not merge stages or skip a gate.

1. `docs/superpowers/plans/2026-05-15-youtubebridge-free-talk-stage-1-topic-runtime.md`
2. `docs/superpowers/plans/2026-05-15-youtubebridge-free-talk-stage-2-main-finish-runtime.md`
3. `docs/superpowers/plans/2026-05-15-youtubebridge-free-talk-stage-3-two-summary-cleanup.md`
4. `docs/superpowers/plans/2026-05-15-youtubebridge-free-talk-stage-4-low-signal-closing.md`

## `/goal` Objective Text

Use this objective when starting a long-running goal:

```text
Implement YouTubeBridge post-plan free-talk in four E2E-tested vertical stages. Follow the stage plans under docs/superpowers/plans/2026-05-15-youtubebridge-free-talk-stage-*.md in order. Do not advance to the next stage until unit tests, source tests, git diff check, and browser E2E for the current stage pass or the failure is documented and explicitly accepted. Preserve legacy /ui behavior and avoid skeleton-only work.
```

## Execution Rules

- [ ] Before starting implementation, run:

  ```powershell
  git status --short --branch
  ```

- [ ] Read `AGENTS.md` and `CLAUDE.md`; keep the 8088 and 8091 foreground-window service rule.
- [ ] Use `superpowers:subagent-driven-development` for every stage. Do not use the single-agent `superpowers:executing-plans` path unless the user explicitly changes the roadmap rule.
- [ ] Keep the main orchestrator out of direct stage implementation. The main orchestrator dispatches worker/verifier subagents, reviews their outputs, runs final verification, handles commits, and reports status.
- [ ] Use `superpowers:test-driven-development` for every stage: write or update the failing tests first, verify the red failure is about the intended missing behavior, then implement.
- [ ] Use `superpowers:systematic-debugging` when an E2E or runtime behavior is inconsistent with the plan.
- [ ] Use `superpowers:verification-before-completion` before marking any stage complete.
- [ ] Treat each stage as a vertical slice. A stage is not complete when routes, schema, or UI placeholders exist but cannot be exercised in `/studio/`.
- [ ] Keep all generated runtime sample files small and deterministic. Do not depend on external YouTube connectivity for required E2E gates; use test mode unless a stage plan explicitly says otherwise.
- [ ] Do not alter legacy `/ui/` unless a stage plan explicitly requires a shared backend contract update.

## Service Handling For Browser E2E

- [ ] If 8088 or 8091 is not running, start them with visible foreground CMD windows:

  ```powershell
  Start-Process -FilePath "cmd.exe" -ArgumentList "/k", "G:\ClaudeProject\MemoriaCore\start.bat" -WorkingDirectory "G:\ClaudeProject\MemoriaCore" -WindowStyle Normal
  Start-Process -FilePath "cmd.exe" -ArgumentList "/k", "G:\ClaudeProject\MemoriaCore\YouTubeBridge\start.bat" -WorkingDirectory "G:\ClaudeProject\MemoriaCore\YouTubeBridge" -WindowStyle Normal
  ```

- [ ] Do not start either service with `-WindowStyle Hidden`, `Start-Job`, hidden shells, or detached non-interactive background processes.
- [ ] After server start or restart, confirm `/studio/` loads:

  ```text
  http://127.0.0.1:8091/studio/
  ```

## Stage 1 Gate: Topic Library And Manual Free Talk Runtime

Implementation plan:

`docs/superpowers/plans/2026-05-15-youtubebridge-free-talk-stage-1-topic-runtime.md`

- [ ] Implement topic-pack discovery, parsing, snapshot storage, Studio checklist UI, and manual free-talk test runtime exactly as the Stage 1 plan specifies.
- [ ] Required source/unit tests:

  ```powershell
  python -m pytest YouTubeBridge/tests/test_free_talk_topics.py YouTubeBridge/tests/test_studio_settings_api.py YouTubeBridge/tests/test_studio_ui.py -q
  ```

- [ ] Required lifecycle regression:

  ```powershell
  python -m pytest YouTubeBridge/tests/test_bridge_engine_lifecycle.py YouTubeBridge/tests/test_bridge_engine_director.py -q
  ```

- [ ] Required static check:

  ```powershell
  git diff --check
  ```

- [ ] Required Browser E2E:
  - Open `http://127.0.0.1:8091/studio/`.
  - Confirm the free-talk topic list shows the fixed folder path and a reload control.
  - Confirm the first checkbox is `全部話題庫`.
  - Confirm unchecking one pack clears `全部話題庫`.
  - Start a test session.
  - Click the free-talk test entry button.
  - Confirm central chat receives one free-talk round from a selected topic.
  - Confirm no LiveEpisodePlan planned turn is rerun.

- [ ] Stage 1 completion note must include:
  - Test command outputs.
  - Browser E2E result.
  - Any changed runtime fixture file path.
  - Whether fallback natural chat was exercised.

## Stage 2 Gate: Main Finish Pipeline And Phase Transition

Implementation plan:

`docs/superpowers/plans/2026-05-15-youtubebridge-free-talk-stage-2-main-finish-runtime.md`

- [ ] Implement phase state, main SC-only closing, `/phase/finish-main`, `/phase/finalize`, LiveEpisodePlan completion transition, and Studio phase controls exactly as the Stage 2 plan specifies.
- [ ] Required source/unit tests:

  ```powershell
  python -m pytest YouTubeBridge/tests/test_phase_pipeline.py YouTubeBridge/tests/test_bridge_engine_lifecycle.py YouTubeBridge/tests/test_bridge_engine_director.py YouTubeBridge/tests/test_server_route_split.py YouTubeBridge/tests/test_studio_ui.py -q
  ```

- [ ] Required auth/session regression:

  ```powershell
  python -m pytest YouTubeBridge/tests/test_server_auth.py::test_start_current_session_archives_existing_session_and_writes_memory YouTubeBridge/tests/test_server_auth.py::test_start_current_session_validates_new_live_before_archiving_existing YouTubeBridge/tests/test_server_auth.py::test_start_current_session_never_reuses_client_memoria_session_id -q
  ```

- [ ] Required static check:

  ```powershell
  git diff --check
  ```

- [ ] Required Browser E2E:
  - Open `/studio/`.
  - Start a test session with free talk enabled.
  - Use the test button to end the planned phase and enter free talk.
  - Confirm main phase SC closing runs first.
  - Confirm normal non-SC pending comments remain available for free talk.
  - Confirm free-talk ticks continue by interval and topic queue.
  - Start another test session with free talk disabled.
  - Confirm finish-main skips free talk and proceeds to final cleanup path.

- [ ] Stage 2 completion note must include:
  - Test command outputs.
  - Browser E2E result for free-talk enabled and disabled.
  - API responses for `/phase/finish-main` and `/phase/finalize`.
  - Any known phase-transition limitation that remains.

## Stage 3 Gate: Two Independent Summaries And Cleanup Wait

Implementation plan:

`docs/superpowers/plans/2026-05-15-youtubebridge-free-talk-stage-3-two-summary-cleanup.md`

- [ ] Implement phase-scoped summaries, two Shared Memory writes, background main summary, free-talk summary, and cleanup gating exactly as the Stage 3 plan specifies.
- [ ] Required source/unit tests:

  ```powershell
  python -m pytest YouTubeBridge/tests/test_phase_summary.py YouTubeBridge/tests/test_phase_pipeline.py YouTubeBridge/tests/test_summary_engine.py YouTubeBridge/tests/test_studio_ui.py -q
  ```

- [ ] Required lifecycle regression:

  ```powershell
  python -m pytest YouTubeBridge/tests/test_bridge_engine_lifecycle.py YouTubeBridge/tests/test_bridge_engine_director.py -q
  ```

- [ ] Required static check:

  ```powershell
  git diff --check
  ```

- [ ] Required Browser E2E:
  - Open `/studio/`.
  - Start a test session with free talk enabled.
  - Generate at least one planned/main AI line and one free-talk AI line.
  - Trigger planned phase finish and then final free-talk finalize.
  - Confirm the UI shows main summary and free-talk summary as separate statuses.
  - Confirm cleanup waits until both summaries finish.
  - Confirm runtime session clearing follows the existing option only after both memory writes are done.

- [ ] Stage 3 completion note must include:
  - Test command outputs.
  - Browser E2E result.
  - Evidence that main and free-talk summaries are separate.
  - Evidence that both memory writes completed or the exact failure state shown in UI.

## Stage 4 Gate: Low-Signal Filter And Large-Batch Free-Talk Closing

Implementation plan:

`docs/superpowers/plans/2026-05-15-youtubebridge-free-talk-stage-4-low-signal-closing.md`

- [ ] Implement low-signal filtering, skip metadata, large-batch free-talk closing, batch sizing, and Studio status display exactly as the Stage 4 plan specifies.
- [ ] Required source/unit tests:

  ```powershell
  python -m pytest YouTubeBridge/tests/test_free_talk_low_signal.py YouTubeBridge/tests/test_phase_pipeline.py YouTubeBridge/tests/test_studio_settings_api.py YouTubeBridge/tests/test_studio_ui.py -q
  ```

- [ ] Required integration regression:

  ```powershell
  python -m pytest YouTubeBridge/tests/test_live_persona_overlays.py YouTubeBridge/tests/test_bridge_engine_lifecycle.py YouTubeBridge/tests/test_summary_engine.py -q
  ```

- [ ] Required static check:

  ```powershell
  git diff --check
  ```

- [ ] Required Browser E2E:
  - Open `/studio/`.
  - Start a test session.
  - Inject a mixed pending queue containing SC, normal questions, repeated short messages, pure emoji, and `6666`-style comments.
  - Enter free talk.
  - Trigger final free-talk closing.
  - Confirm low-signal comments are skipped and not passed into generated response text.
  - Confirm normal eligible comments are grouped into larger batches.
  - Confirm all eligible pending comments are handled or explicitly marked skipped before final summary.

- [ ] Stage 4 completion note must include:
  - Test command outputs.
  - Browser E2E result.
  - Batch sizing evidence.
  - Low-signal skip counts.
  - Confirmation that the final summary excludes skipped low-signal noise.

## Cross-Stage Stop Conditions

Stop and report instead of continuing when any of these occur:

- [ ] A stage needs a schema or API contract that conflicts with a prior approved user decision.
- [ ] A test fails for a reason unrelated to the current stage and the failure cannot be isolated.
- [ ] Browser E2E cannot be run because 8091 cannot start in a visible foreground server window.
- [ ] The implementation requires hidden/background services.
- [ ] The worktree contains unexpected user changes in a file the stage must edit and the intended merge is unclear.
- [ ] A stage can only pass by adding a fake/mock path to production runtime behavior.
- [ ] Summary or Shared Memory writes would run in a live session before the planned phase is stopped.

## Final Goal Completion Gate

Only mark the `/goal` complete when all items below are true:

- [ ] Stage 1, Stage 2, Stage 3, and Stage 4 checklists are complete.
- [ ] Each stage has a recorded Browser E2E result.
- [ ] The latest full relevant regression set passes:

  ```powershell
  python -m pytest YouTubeBridge/tests/test_free_talk_topics.py YouTubeBridge/tests/test_free_talk_low_signal.py YouTubeBridge/tests/test_phase_pipeline.py YouTubeBridge/tests/test_phase_summary.py YouTubeBridge/tests/test_studio_ui.py YouTubeBridge/tests/test_studio_settings_api.py YouTubeBridge/tests/test_bridge_engine_lifecycle.py YouTubeBridge/tests/test_bridge_engine_director.py YouTubeBridge/tests/test_summary_engine.py -q
  ```

- [ ] Route/auth regressions pass:

  ```powershell
  python -m pytest YouTubeBridge/tests/test_server_route_split.py YouTubeBridge/tests/test_server_auth.py::test_start_current_session_archives_existing_session_and_writes_memory YouTubeBridge/tests/test_server_auth.py::test_start_current_session_validates_new_live_before_archiving_existing YouTubeBridge/tests/test_server_auth.py::test_start_current_session_never_reuses_client_memoria_session_id -q
  ```

- [ ] `git diff --check` passes.
- [ ] `/studio/` can run a test-mode lifecycle:
  - start session
  - planned phase generates content
  - finish main phase
  - enter free talk
  - handle audience comments with soft interrupt on next tick
  - finalize free talk
  - generate both summaries
  - clear runtime session only when configured
- [ ] No relevant browser console errors or server traceback remain.
- [ ] Final report lists files changed, tests run, E2E evidence, residual risks, and any user-facing behavior that intentionally remains out of scope.

## Final Report Template

Use this report shape when the goal finishes:

```md
## Completed

- Stage 1: <summary>
- Stage 2: <summary>
- Stage 3: <summary>
- Stage 4: <summary>

## Verification

- <pytest command>: passed
- <pytest command>: passed
- git diff --check: passed
- Browser E2E: passed at http://127.0.0.1:8091/studio/

## Notes

- <remaining operational note or "None">
```
