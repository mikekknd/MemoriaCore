# LiveEpisodePlan Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task by task. Steps use checkbox syntax for tracking.

**Goal:** Implement the V2 planned show runner that turns a validated LiveEpisodePlan into planned turn intents and completion signals.

**Architecture:** The runner is a pure planning layer above Runtime Phase and below MemoriaCore Adapter. It validates plan shape, advances a cursor, emits execution intent, and never generates prompts or calls adapters.

**Tech Stack:** Python 3.12, pytest, dataclasses or Pydantic models if the runtime package standardizes on Pydantic.

---

## Scope

Planned source: `YouTubeBridgeV2/runtime/live_episode_plan.py`

Planned tests: `tests/youtubebridge_v2/test_live_episode_plan_runner.py`

This implementation must not modify old `YouTubeBridge/` files.

## Planned Symbols

- `LiveEpisodePlanContract`
- `PlannedTurnIntent`
- `PlanExecutionStatus`
- `PlannedTurnResult`
- `PlanCompletionSignal`
- `validate_episode_plan_contract(plan)`
- `next_planned_turn(plan_state, audience_event_summary=None)`
- `record_planned_turn_result(plan_state, turn_result)`

## Red Cases

- `test_valid_episode_plan_contract_is_accepted`
- `test_missing_required_episode_plan_field_is_invalid`
- `test_first_turn_produces_planned_turn_intent`
- `test_fixed_speaker_policy_is_preserved`
- `test_audience_event_is_excluded_when_turn_policy_disallows_it`
- `test_super_chat_summary_is_allowed_only_when_turn_policy_allows_it`
- `test_turn_result_advances_cursor`
- `test_last_turn_result_emits_completion_signal`
- `test_raw_topic_pack_text_is_not_emitted_in_turn_intent`
- `test_runner_does_not_call_memoria_youtube_storage_or_ui`

Expected red command:

```powershell
python -m pytest tests/youtubebridge_v2/test_live_episode_plan_runner.py -q
```

Expected red result before implementation: missing `YouTubeBridgeV2.runtime.live_episode_plan` or missing planned symbols.

## Green Scope

- Implement only plan validation, cursor state, turn intent, turn result, and completion signal.
- Keep audience event handling as summarized policy output.
- Keep raw Topic Pack / FactCard data out of public intent.
- Do not generate LLM prompts or call MemoriaCore.

## Refactor Boundary

Allowed: private helpers for validation, cursor normalization, and public summary construction.

Forbidden: storage writes, adapter calls, phase transition decisions, Legacy director fallback, or schema migration.

## Adapter Strategy

No adapter dependency. Tests use in-memory plan dictionaries and simple dataclass instances.

## Docs Sync

After symbols exist, update `docs/api-reference-index.md` with Source values for the LiveEpisodePlan runner contracts. Update `docs/modules/live-episode-plan.md` only if implementation changes its contract.

## Execution Steps

- [ ] Create failing tests in `tests/youtubebridge_v2/test_live_episode_plan_runner.py`.
- [ ] Run the red command and confirm expected missing module or symbol failure.
- [ ] Create `YouTubeBridgeV2/runtime/live_episode_plan.py` with planned symbols.
- [ ] Implement minimal validation, cursor advancement, and completion signal.
- [ ] Run the green command and confirm all tests pass.
- [ ] Refactor inside the allowed boundary and rerun tests.
- [ ] Add API Source values only after runtime symbols exist.

## Acceptance Criteria

- Planned show runner tests pass.
- Runner emits completion signal for Runtime Phase.
- Runner does not expose raw Topic Pack / FactCard text.
- Runner has no direct adapter, storage, UI, or Legacy director dependency.
