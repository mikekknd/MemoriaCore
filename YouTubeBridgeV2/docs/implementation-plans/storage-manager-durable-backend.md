# StorageManager Durable Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task by task. Steps use checkbox syntax for tracking.

**Goal:** Replace the Wave 1 in-memory storage fake with a real `StorageManager` durable backend for V2 storage E2E, while keeping YouTube, MemoriaCore, TTS, and production V2 app wiring fake/unconfigured.

**Architecture:** V2 runtime code continues to depend on `RuntimeStoragePort` and repository contracts. SQLite schema and reads/writes live only in `core/storage/youtube_bridge_v2.py`, exposed through `StorageManager` in `core/storage_manager.py`. The durable DB default is `runtime/youtubebridge_v2.db`; tests inject `youtube_bridge_v2_db_path` with `tmp_path`.

**Tech Stack:** Python, pytest, `StorageManager`, SQLite behind `core/storage/`.

---

## Scope

Source:

- `core/storage/youtube_bridge_v2.py`
- `core/storage/__init__.py`
- `core/storage_manager.py`
- `YouTubeBridgeV2/storage/runtime_store.py`

Tests:

- `tests/youtubebridge_v2/test_storage_manager_durable_backend.py`
- `tests/youtubebridge_v2/test_real_storage_integration.py`

Docs:

- `YouTubeBridgeV2/docs/architecture-index.md`
- `YouTubeBridgeV2/docs/api-reference-index.md`
- `YouTubeBridgeV2/docs/modules/storage.md`

Out of scope:

- No production `api/main.py` V2 composition wiring.
- No real YouTube polling, MemoriaCore call, or TTS delivery.
- No SQLite access from `YouTubeBridgeV2/`.
- No Legacy `YouTubeBridge/` runtime dependency.

## Planned Symbols

- `YouTubeBridgeV2RepositoryMixin`
- `StorageManager(..., youtube_bridge_v2_db_path=None)`
- `create_v2_session(record)`
- `get_v2_session(session_id)`
- `update_v2_session(session_id, patch)`
- `get_v2_phase_transition(transition_id)`
- `append_v2_phase_transition(session_id, record)`
- `append_v2_live_event(session_id, record)`
- `list_v2_live_events(session_id, limit=100)`
- `append_v2_interaction(session_id, record)`
- `append_v2_finalization(session_id, record)`
- `get_v2_command_result(command_id)`
- `save_v2_command_result(command_id, result)`

## Red Cases

- `test_v2_storage_manager_initializes_schema`
- `test_create_and_read_v2_session_snapshot_from_storage_manager`
- `test_update_v2_session_preserves_snapshot_contract`
- `test_append_v2_phase_transition_is_idempotent_by_transition_id`
- `test_append_and_list_v2_live_events_are_ordered_and_limited`
- `test_append_v2_interaction_redacts_private_public_summary`
- `test_append_v2_finalization_marks_session_closing_completed`
- `test_v2_command_result_round_trips_through_storage_manager`
- `test_v2_storage_schema_init_is_idempotent_across_manager_instances`
- `test_real_storage_vertical_slice_reaches_ended_and_persists_events`
- `test_real_storage_restart_recovery_reads_existing_snapshot`
- `test_real_storage_repeated_command_id_survives_restart_without_duplicate_dispatch`

Expected red command:

```powershell
python -m pytest tests\youtubebridge_v2\test_storage_manager_durable_backend.py -q
python -m pytest tests\youtubebridge_v2\test_real_storage_integration.py -q
```

Expected red result before implementation:

- `StorageManager.__init__` rejects `youtube_bridge_v2_db_path`.
- `StorageManager` lacks V2 durable methods.
- Durable command result replay returns a bare dict instead of `RuntimeServiceResult`.

## Green Scope

- Add `YouTubeBridgeV2RepositoryMixin` under `core/storage/`.
- Initialize `yb2_sessions`, `yb2_phase_transitions`, `yb2_live_events`, `yb2_interactions`, `yb2_finalizations`, and `yb2_command_results`.
- Add V2 mixin export and `StorageManager` inheritance.
- Add optional `youtube_bridge_v2_db_path` to `StorageManager.__init__`.
- Implement session create/read/update, transition append idempotency, event history, interaction, finalization, and command result persistence.
- Update `RuntimeStoragePort` to save command results as JSON-safe values and rehydrate durable results into `RuntimeServiceResult`.
- Keep all public persisted payloads redacted.

## Refactor Boundary

Allowed:

- Extract row mapping, JSON encode/decode, redaction, and datetime helpers inside `core/storage/youtube_bridge_v2.py`.
- Add local rehydration helpers in `YouTubeBridgeV2/storage/runtime_store.py`.
- Extend tests under `tests/youtubebridge_v2/`.

Forbidden:

- Importing `sqlite3` or `aiosqlite` from `YouTubeBridgeV2/`.
- Moving V2 durable schema into the V2 package.
- Changing `RuntimeApplicationService` public command methods.
- Wiring production `api/main.py` to real V2 runtime in this wave.
- Reusing Legacy no-plan director.

## Adapter Strategy

Use fake planned-show, aftertalk, and closing runners from the test harness. They write deterministic public summaries through the same `StorageManager` V2 methods used by production composition, so the test verifies runtime/storage wiring without external APIs.

## Docs Sync

After implementation:

- Mark Wave 2A durable backend complete in `docs/architecture-index.md`.
- Add durable `StorageManager` sources in `docs/api-reference-index.md`.
- Update `docs/modules/storage.md` so durable backend and `RuntimeStoragePort` are no longer described as future work.

## Execution Steps

- [x] Commit Wave 1 fake-backed E2E separately before durable changes.
- [x] Add fail-first durable backend tests.
- [x] Implement `core/storage/youtube_bridge_v2.py` schema and public methods.
- [x] Wire `YouTubeBridgeV2RepositoryMixin` into `StorageManager`.
- [x] Add real-storage vertical slice tests.
- [x] Rehydrate durable command results in `RuntimeStoragePort`.
- [x] Align runtime public redaction with durable persisted redaction.
- [x] Run `python -m pytest tests\youtubebridge_v2 -q`.
- [x] Sync architecture, API reference, storage module docs, and this implementation record.

## Acceptance Criteria

- V2 durable storage tests pass with a real `StorageManager` and tmp DB.
- Fake-backed integration tests still pass.
- Real-storage vertical slice reaches `ended`.
- Restart/recovery can read the same session after `StorageManager` recreation.
- Replayed command idempotency does not duplicate runner dispatch.
- V2 package still does not import SQLite directly.
- Production V2 app wiring remains deferred to Wave 2B.
