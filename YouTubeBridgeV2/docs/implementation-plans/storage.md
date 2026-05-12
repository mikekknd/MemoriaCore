# Storage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task by task. Steps use checkbox syntax for tracking.

**Goal:** Implement the V2 storage repository adapter skeleton for sessions, phase transitions, events, interactions, adapter metadata, and finalization records.

**Architecture:** Storage is accessed through V2 repository interfaces backed by an explicitly injected `StorageManager`-like boundary. Runtime core consumes snapshots and writes decisions through service code, while SQLite details remain inside `core/storage/` and `core/storage_manager.py`. This stage does not add the durable V2 backend or the Runtime Application Service storage adapter.

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
- `StorageBackendNotConfigured`

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
- `test_default_repository_without_configured_backend_fails_clearly`
- `test_facade_does_not_claim_runtime_application_service_storage_contract`
- `test_phase_transition_requires_explicit_transition_id`
- `test_v2_modules_do_not_import_sqlite_or_aiosqlite`

Expected red command:

```powershell
python -m pytest tests/youtubebridge_v2/test_storage.py -q
```

Expected red result before implementation: missing `YouTubeBridgeV2.storage.repositories` or missing planned symbols.

## Green Scope

- Implement repository interfaces as adapters over an injected `StorageManager`-like backend.
- Use fake `StorageManager` objects for unit tests; use pytest temp paths only through `StorageManager` integration tests.
- Preserve session snapshot fields needed by Runtime Phase.
- Implement transition idempotency.
- Keep raw hidden payloads out of public metadata.
- Fail clearly with `StorageBackendNotConfigured` when the default backend has not been wired.
- Do not expose the aggregate repository facade as the Runtime Application Service storage adapter.

## Refactor Boundary

Allowed: split V2 repository adapters under `YouTubeBridgeV2/storage/` if a file becomes too large. Add concrete SQLite repository/migration code only under `core/storage/` and expose it through `core/storage_manager.py`.

Forbidden: importing `sqlite3` or `aiosqlite` from `YouTubeBridgeV2/`, direct SQLite access outside `core/storage/` and `core/storage_manager.py`, phase decisions, adapter calls, UI rendering, or migration tooling beyond the approved storage boundary.

## Adapter Strategy

No external adapter dependency. Unit tests use fake `StorageManager`-like objects. Integration tests that need durable storage belong to the later backend wiring stage and must use pytest temp paths through the real `StorageManager` lock.

## Docs Sync

After implementation exists, add Source values for storage contracts. Update module design if repository names or snapshot shape changes.

## Execution Steps

- [x] Create failing tests in `tests/youtubebridge_v2/test_storage.py`.
- [x] Run the red command and confirm expected failure.
- [x] Create V2 storage package files and planned repository adapters.
- [x] Implement session snapshot, transition, event, interaction, and finalization repository adapter skeleton.
- [x] Confirm no concrete SQLite persistence is required for this repository-adapter stage; durable backend remains deferred to `StorageManager` / `core/storage/` / `core/storage_manager.py`.
- [x] Run the green command and confirm all tests pass.
- [x] Refactor repository internals and rerun tests.
- [x] Sync API reference Source values after symbols exist.

## Acceptance Criteria

- Runtime Phase can read a complete snapshot through injected storage repository contracts.
- Phase transitions are append-only and idempotent by transition id.
- Public metadata is redacted.
- V2 modules do not import SQLite internals.
- The default helper path fails clearly until a durable V2 backend is configured.
- The aggregate repository facade is distinct from the Runtime Application Service storage adapter contract.
- Any future durable SQLite access must go through `StorageManager` and its allowed internal storage package.
