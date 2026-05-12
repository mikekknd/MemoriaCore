# Runtime Application Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task by task. Steps use checkbox syntax for tracking.

**Goal:** Implement the V2 orchestration service that coordinates session commands across Runtime Phase, storage, runners, adapters, closing, and observability.

**Architecture:** The service is the side-effect boundary for runtime workflow. It reads snapshots, calls pure decision modules, dispatches next actions, persists results, and publishes redacted events without letting routes or adapters own orchestration.

**Tech Stack:** Python 3.12, pytest, dataclasses or Pydantic models if the runtime package standardizes on Pydantic.

---

## Scope

Planned source: `YouTubeBridgeV2/runtime/application_service.py`

Planned tests: `tests/youtubebridge_v2/test_runtime_application_service.py`

This module may call repositories and adapters through interfaces. It must not import old `YouTubeBridge/` runtime modules.

## Planned Symbols

- `RuntimeApplicationService`
- `RuntimeCommand`
- `RuntimeCommandType`
- `RuntimeServiceResult`
- `RuntimeServiceEvent`
- `PersistedTransitionRef`
- `AdapterDispatchResult`
- `RecoveryDecision`

## Red Cases

- `test_create_session_command_delegates_to_storage`
- `test_tick_reads_snapshot_before_advancing_phase`
- `test_phase_next_action_dispatches_planned_show_runner`
- `test_phase_next_action_dispatches_aftertalk`
- `test_phase_next_action_dispatches_closing`
- `test_manual_close_wins_over_planned_turn_continuation`
- `test_duplicate_command_id_does_not_repeat_adapter_call`
- `test_retryable_adapter_error_is_persisted_with_redacted_summary`
- `test_storage_write_failure_stops_later_side_effects`
- `test_crash_recovery_resumes_incomplete_closing`
- `test_runtime_service_event_excludes_hidden_prompt_and_raw_payload`

Expected red command:

```powershell
python -m pytest tests/youtubebridge_v2/test_runtime_application_service.py -q
```

Expected red result before implementation: missing `YouTubeBridgeV2.runtime.application_service` or missing planned symbols.

## Green Scope

- Implement command envelope/result dataclasses.
- Implement orchestration methods for session create, plan bind, tick, YouTube event handling, aftertalk policy update, manual close, and closing finalization.
- Use injected fake repositories/adapters/services in tests.
- Preserve idempotency by `command_id`.
- Redact all service events before returning them.

## Refactor Boundary

Allowed: split command handlers into private methods or package-local handler classes.

Forbidden: phase policy changes, direct HTTP route parsing, direct `StorageManager` bypass, concrete SQLite code, real adapter transport, UI rendering, or Legacy runtime imports.

## Adapter Strategy

Unit tests use fake Storage, Runtime Phase, LiveEpisodePlan, Aftertalk, Closing, MemoriaCore Adapter, YouTube Adapter, and Observability dependencies. Real adapter integration tests belong to adapter modules.

## Docs Sync

After implementation exists, update `docs/api-reference-index.md` Source values for Runtime Application Service contracts. Update module docs if orchestration order or public result shape changes.

## Execution Steps

- [ ] Create failing service tests in `tests/youtubebridge_v2/test_runtime_application_service.py`.
- [ ] Run the red command and confirm expected missing module or symbol failure.
- [ ] Create `YouTubeBridgeV2/runtime/application_service.py` with planned symbols.
- [ ] Implement command dispatch and dependency injection.
- [ ] Implement idempotency and manual-close priority.
- [ ] Implement redacted event/result output.
- [ ] Run the green command and confirm all tests pass.
- [ ] Refactor private handlers and rerun tests.
- [ ] Sync API reference Source values after symbols exist.

## Acceptance Criteria

- API routes can delegate all runtime actions to the service.
- Service is the only orchestration layer for storage/phase/adapter side effects.
- Duplicate commands do not repeat side effects.
- Crash/restart recovery has a deterministic entry point.
- Public service events do not expose hidden prompt or raw payloads.
