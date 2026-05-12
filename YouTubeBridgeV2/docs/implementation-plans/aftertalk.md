# Aftertalk Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task by task. Steps use checkbox syntax for tracking.

**Goal:** Implement the V2 aftertalk planner that creates group chat cue requests after the planned show completes.

**Architecture:** Aftertalk sits behind Runtime Phase and before MemoriaCore Adapter. It evaluates aftertalk continuation context, emits cue/request contracts, and leaves transport and role response generation to other modules.

**Tech Stack:** Python 3.12, pytest, dataclasses.

---

## Scope

Planned source: `YouTubeBridgeV2/runtime/aftertalk.py`

Planned tests: `tests/youtubebridge_v2/test_aftertalk.py`

## Planned Symbols

- `AftertalkCue`
- `AftertalkTurnRequest`
- `AftertalkStopReason`
- `AftertalkSessionSummary`
- `build_aftertalk_turn_request(aftertalk_context)`
- `summarize_aftertalk_result(aftertalk_result)`

## Red Cases

- `test_aftertalk_auto_policy_builds_group_chat_request_when_duration_allows`
- `test_aftertalk_disabled_policy_returns_disabled_stop_reason`
- `test_aftertalk_duration_reached_returns_duration_stop_reason`
- `test_aftertalk_manual_close_returns_manual_stop_reason`
- `test_aftertalk_cue_uses_public_show_summary_only`
- `test_aftertalk_request_contains_group_chat_mode`
- `test_aftertalk_does_not_use_legacy_director`
- `test_aftertalk_has_no_memoria_transport_side_effect`

Expected red command:

```powershell
python -m pytest tests/youtubebridge_v2/test_aftertalk.py -q
```

Expected red result before implementation: missing `YouTubeBridgeV2.runtime.aftertalk` or missing planned symbols.

## Green Scope

- Implement cue/request/summary dataclasses.
- Implement policy and duration checks using already prepared context.
- Produce group chat intent for MemoriaCore Adapter.
- Return stop reason instead of calling adapters when disabled, closed, or duration exhausted.

## Refactor Boundary

Allowed: private helpers for cue minimization and stop reason selection.

Forbidden: LLM call, MemoriaCore HTTP call, storage write, phase decision, YouTube event processing, or Legacy director import.

## Adapter Strategy

No direct adapter call. Tests assert only an intent object is returned.

## Docs Sync

After implementation exists, update `docs/api-reference-index.md` with Source values for aftertalk contracts. Keep module design aligned if cue fields change.

## Execution Steps

- [ ] Create failing tests in `tests/youtubebridge_v2/test_aftertalk.py`.
- [ ] Run the red command and confirm expected failure.
- [ ] Create `YouTubeBridgeV2/runtime/aftertalk.py` with planned symbols.
- [ ] Implement minimal cue/request/stop behavior.
- [ ] Run the green command and confirm all tests pass.
- [ ] Refactor cue construction and rerun tests.
- [ ] Sync API reference Source values after symbols exist.

## Acceptance Criteria

- Aftertalk emits group chat intent only when policy and duration allow it.
- Stop reasons are deterministic.
- Cue metadata excludes hidden prompt, raw Topic Pack, and raw MemoriaCore payload.
- No Legacy director path is used.
