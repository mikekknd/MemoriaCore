# YouTubeBridge Free Talk Roadmap

> `/goal` source of truth for the YouTubeBridge post-plan free-talk implementation. This file defines the execution boundary, stage order, acceptance gates, and stop conditions. Detailed implementation steps live in the linked stage plans.

## Goal Boundary

Build the post-plan free-talk pipeline for the current YouTubeBridge Studio flow:

`直播內容結束 -> 直播 SC/留言收尾 -> main summary -> free talk -> free talk SC/留言收尾 -> free talk summary -> final cleanup`

This roadmap is intentionally an execution index. It tells `/goal` what is in scope, what to run first, when a stage is complete, and when to stop. It should not duplicate every code-level step from the implementation plans.

## Required Read Order

Before choosing or executing work, read these files in order:

1. `AGENTS.md`
2. `CLAUDE.md`
3. `docs/superpowers/roadmaps/youtubebridge-free-talk-roadmap.md`
4. `docs/superpowers/plans/2026-05-15-youtubebridge-free-talk-goal-execution.md`
5. The first unchecked stage plan from the stage checklist below.

When implementation touches UI-visible text, also read:

6. `docs/i18n-maintenance-guide.md`

## Required Skills

Use these skills during `/goal` execution:

- `superpowers:subagent-driven-development` is required for every stage.
- `superpowers:test-driven-development` for each code change.
- `superpowers:systematic-debugging` for runtime, E2E, or test failures.
- `superpowers:verification-before-completion` before marking any stage complete.

Do not use the single-agent `superpowers:executing-plans` fallback for this roadmap unless the user explicitly changes this roadmap rule. The main orchestrator must not implement stage tasks directly. The main orchestrator owns only:

- Reading the roadmap and selecting the first unchecked stage.
- Dispatching implementation and verification subagents.
- Monitoring progress and preventing scope drift.
- Reviewing subagent outputs before integration.
- Running final verification for each stage.
- Updating this roadmap after gates pass.
- Committing, pushing, and reporting the result.

## Current Status

- [x] Stage 1: Topic Library And Manual Free Talk Runtime
- [x] Stage 2: Main Finish Pipeline And Phase Transition
- [x] Stage 3: Two Independent Summaries And Cleanup Wait
- [x] Stage 4: Low-Signal Filter And Large-Batch Free-Talk Closing
- [ ] Final Goal Gate: Full Studio lifecycle E2E and full regression pass

## Stage Checklist

### Stage 1: Topic Library And Manual Free Talk Runtime

Plan file:

`docs/superpowers/plans/2026-05-15-youtubebridge-free-talk-stage-1-topic-runtime.md`

Scope:

- Add JSON free-talk topic pack loading from `runtime/YouTubeBridge/freeTalkTopics/`.
- Support object format and array format topic JSON files.
- Add Studio topic-pack checklist UI with `全部話題庫`.
- Persist selected packs and free-talk runtime defaults.
- Add a debug/manual entry path to start free talk from Studio.
- Produce real AI dialogue from selected free-talk topics in test mode.

Completion gate:

- Unit/source tests in the Stage 1 plan pass.
- `git diff --check` passes.
- Browser E2E confirms Studio can reload topic packs, select packs, start a test session, enter free talk, and receive one free-talk AI round.
- No LiveEpisodePlan planned turn is rerun during manual free talk.

### Stage 2: Main Finish Pipeline And Phase Transition

Plan file:

`docs/superpowers/plans/2026-05-15-youtubebridge-free-talk-stage-2-main-finish-runtime.md`

Scope:

- Add phase pipeline state and transition helpers.
- Add `POST /sessions/{session_id}/phase/finish-main`.
- Add `POST /sessions/{session_id}/phase/finalize`.
- Connect LiveEpisodePlan completion to main finish.
- Main phase closing handles unhandled SC only.
- If free talk is enabled, enter free talk after main closing and main summary dispatch.
- If free talk is disabled, proceed to final cleanup path.
- Add Studio debug control for ending the planned phase and entering free talk.

Completion gate:

- Unit/source tests and route/auth regressions in the Stage 2 plan pass.
- `git diff --check` passes.
- Browser E2E confirms both free-talk enabled and free-talk disabled paths.
- Normal non-SC pending comments are not consumed by main closing.
- Free-talk ticks continue by configured interval after transition.

### Stage 3: Two Independent Summaries And Cleanup Wait

Plan file:

`docs/superpowers/plans/2026-05-15-youtubebridge-free-talk-stage-3-two-summary-cleanup.md`

Scope:

- Generate `main` and `free_talk` summaries as separate summaries.
- Write both summaries to Shared Memory.
- Mark event/session metadata by phase without sending that metadata to the LLM.
- Start main summary after main closing.
- Start free-talk summary after free-talk closing.
- Defer runtime cleanup until required summaries and memory writes finish.
- Display phase summary status in Studio.

Completion gate:

- Unit/source tests and lifecycle regressions in the Stage 3 plan pass.
- `git diff --check` passes.
- Browser E2E confirms main summary and free-talk summary are independent.
- Cleanup waits for both memory writes when free talk is enabled.
- Cleanup waits only for main summary when free talk is disabled.

### Stage 4: Low-Signal Filter And Large-Batch Free-Talk Closing

Plan file:

`docs/superpowers/plans/2026-05-15-youtubebridge-free-talk-stage-4-low-signal-closing.md`

Scope:

- Filter low-signal comments before LLM batching.
- Skip pure emoji, repeated short noise, `6666`-style messages, and large duplicate floods.
- Mark skipped comments with metadata such as `low_signal_skipped`.
- Free-talk closing handles all eligible pending comments with larger batches.
- Compute batch size from eligible count and configured target batch count.
- Keep SC handling compatible with existing SC batch/cooldown settings.
- Show handled/skipped counts in Studio.

Completion gate:

- Unit/source tests and integration regressions in the Stage 4 plan pass.
- `git diff --check` passes.
- Browser E2E confirms low-signal comments are skipped and not included in generated response text.
- Browser E2E confirms eligible comments are grouped into larger batches.
- Final summary excludes skipped low-signal noise.

Completion note (2026-05-15): Studio E2E `codex_stage4_noisy` completed with free-talk closing metadata `eligible_processed_count=3`, `low_signal_skipped_count=3`, `closing_skipped_count=0`, `batch_count=1`; `main` summary id 46 and `free_talk` summary id 47 both completed Shared Memory writes.

## Final Goal Gate

The goal is complete only when all of these are true:

- [ ] Every stage checkbox above is checked.
- [ ] The full `/studio/` lifecycle works in test mode:
  - Start session.
  - Generate planned content.
  - Finish main phase.
  - Run main SC closing.
  - Start main summary.
  - Enter free talk.
  - Handle pending audience comments with soft interrupt on the next tick.
  - Finalize free talk.
  - Run free-talk closing.
  - Generate free-talk summary.
  - Wait for both Shared Memory writes.
  - Clear runtime session only when configured.
- [ ] The latest full regression commands from `docs/superpowers/plans/2026-05-15-youtubebridge-free-talk-goal-execution.md` pass.
- [ ] Browser console has no relevant errors.
- [ ] Server output has no traceback related to session phase, summary, Studio API, or topic loading.
- [ ] PR or commit summary lists all changed files, tests run, E2E evidence, and residual risks.

## Non-Goals

These are outside this roadmap:

- OBS automatic `video_id` detection.
- Real YouTube production connectivity as a required test gate.
- GPT-SoVITS runtime output wiring.
- Presentation Queue runtime output wiring.
- Legacy `/ui/` redesign.
- Reintroducing Topic Pack controls into the new Studio page.
- Hard interrupting an active MemoriaCore generation.
- Sending phase metadata, topic queue metadata, or low-signal skip metadata into the LLM prompt.

## Runtime Behavior Decisions

- Free talk uses runtime pacing, not a LiveEpisodePlan director.
- Soft interrupt only: pending legal comments are handled on the next free-talk tick.
- If no eligible comments are available, characters continue natural group chat from the selected topic queue.
- Topic queue uses a session snapshot so later JSON file edits do not mutate an active session.
- Global topic packs are selected from Studio; sidecar plan topics are auto-included when present.
- Free-talk topic sidecar presence should be visible in Studio, but no separate sidecar picker is required.
- Manual stop/finalize during planned phase does not enter free talk; it is treated as debug/manual cleanup.
- A debug button may explicitly end the planned phase and enter free talk for testing.

## `/goal` Execution Algorithm

1. Read the required files listed above.
2. Run:

   ```powershell
   git status --short --branch
   ```

3. Identify the first unchecked stage in `Current Status`.
4. Open that stage plan and execute only that stage.
5. Dispatch a worker subagent to write or update failing tests before implementation.
6. Dispatch a worker subagent to implement the minimal stage changes after the failing tests prove the missing behavior.
7. Dispatch a verifier subagent to run the stage verification commands and report failures without fixing them.
8. The main orchestrator reviews the worker/verifier outputs and runs final verification locally.
9. The main orchestrator runs the required Browser E2E.
10. If all gates pass, update this roadmap by checking the completed stage.
11. Commit only the files from the completed stage and this roadmap update.
12. Continue to the next unchecked stage only after the commit is complete.

## Stop Conditions

Stop and report instead of continuing when:

- Subagent execution is unavailable and the user has not explicitly approved changing this roadmap to a single-agent mode.
- A stage requires changing an earlier user decision.
- A required E2E cannot be run.
- The server can only be started hidden or in a background-only process.
- A stage produces only schema/routes/UI placeholders without a working Studio E2E slice.
- A failing test is unrelated to the stage and cannot be isolated.
- A mixed worktree contains user changes in files the stage must edit.
- A runtime behavior would send hidden metadata or low-signal skip metadata into the LLM prompt.
- Summary or Shared Memory writes would run before the relevant phase is stopped.

## Maintenance Rules

- Keep this roadmap as the high-level `/goal` index.
- Keep implementation detail in `docs/superpowers/plans/2026-05-15-youtubebridge-free-talk-*.md`.
- After each stage, update only the corresponding checkbox and add a short completion note if needed.
- Do not mark a stage complete without Browser E2E evidence.
- Do not mark the final goal complete until all stage gates and the final goal gate pass.
