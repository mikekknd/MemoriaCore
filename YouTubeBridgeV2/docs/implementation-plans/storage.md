# Storage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task by task. Steps use checkbox syntax for tracking.

**Goal:** Implement V2 storage repositories for sessions, phase transitions, events, interactions, adapter metadata, and finalization records.

**Architecture:** Storage is accessed through V2 repository interfaces backed by the main project `StorageManager` boundary. Runtime core consumes snapshots and writes decisions through service code, while SQLite details remain inside `core/storage/` and `core/storage_manager.py`.

**Tech Stack:** Python 3.12, pytest, `StorageManager`-backed repositories.

---

## Scope

Planned source: `YouTubeBridgeV2/storage/repositories.py`

Allowed persistence integration points if new durable storage is required: `core/storage/` and `core/storage_manager.py`.

Planned tests: `tests/youtubebridge_v2/test_storage.py`

## Planned Symbols

- `SessionRepository`
- `PhaseTransitionRepository`
- `EventRepository`
- `InteractionRepository`
- `FinalizationRepository`
- `read_live_session_snapshot(session_id)`
- `append_phase_transition(session_id, transition)`
- `append_live_event(session_id, event)`
- `append_interaction(session_id, interaction)`

## Red Cases

- `test_create_session_and_read_snapshot`
- `test_read_missing_session_returns_not_found`
- `test_append_phase_transition_persists_record`
- `test_duplicate_transition_id_is_idempotent`
- `test_append_live_event_persists_normalized_event`
- `test_append_interaction_persists_response_summary`
- `test_finalization_record_moves_session_to_ended_metadata`
- `test_public_metadata_redacts_raw_prompt_and_adapter_payload`
- `test_v2_storage_uses_storage_manager_boundary`
- `test_v2_modules_do_not_import_sqlite_or_aiosqlite`

Expected red command:

```powershell
python -m pytest tests/youtubebridge_v2/test_storage.py -q
```

Expected red result before implementation: missing `YouTubeBridgeV2.storage.repositories` or missing planned symbols.

## Green Scope

- Implement repository interfaces as adapters over `StorageManager`.
- Use fake `StorageManager` objects for unit tests; use pytest temp paths only through `StorageManager` integration tests.
- Preserve session snapshot fields needed by Runtime Phase.
- Implement transition idempotency.
- Keep raw hidden payloads out of public metadata.

## Refactor Boundary

Allowed: split V2 repository adapters under `YouTubeBridgeV2/storage/` if a file becomes too large. Add concrete SQLite repository/migration code only under `core/storage/` and expose it through `core/storage_manager.py`.

Forbidden: importing `sqlite3` or `aiosqlite` from `YouTubeBridgeV2/`, direct SQLite access outside `core/storage/` and `core/storage_manager.py`, phase decisions, adapter calls, UI rendering, or migration tooling beyond the approved storage boundary.

## Adapter Strategy

No external adapter dependency. Unit tests use fake `StorageManager`; integration tests that need durable storage use pytest temp paths through the real `StorageManager` lock.

## Docs Sync

After implementation exists, add Source values for storage contracts. Update module design if repository names or snapshot shape changes.

## Execution Steps

- [ ] Create failing tests in `tests/youtubebridge_v2/test_storage.py`.
- [ ] Run the red command and confirm expected failure.
- [ ] Create V2 storage package files and planned repository adapters.
- [ ] Implement session snapshot, transition, event, interaction, and finalization persistence.
- [ ] Add any required concrete SQLite persistence under `core/storage/` and expose it through `core/storage_manager.py`.
- [ ] Run the green command and confirm all tests pass.
- [ ] Refactor repository internals and rerun tests.
- [ ] Sync API reference Source values after symbols exist.

## Acceptance Criteria

- Runtime Phase can read a complete snapshot through storage contracts.
- Phase transitions are append-only and idempotent by transition id.
- Public metadata is redacted.
- V2 modules do not import SQLite internals.
- All durable SQLite access goes through `StorageManager` and its allowed internal storage package.
