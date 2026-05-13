# Production App Wiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task by task. Steps use checkbox syntax for tracking.

**Goal:** Wire the main FastAPI app `/v2` routes to the real `StorageManager` durable V2 composition while external adapters remain explicit no-op runners.

**Architecture:** `api/main.py` owns only main-app dependency wiring and loopback-only boundary. `YouTubeBridgeV2/production.py` creates a production composition from the existing `StorageManager` singleton. No-op runners keep Wave 2B free of real YouTube, MemoriaCore, and TTS side effects.

**Status note:** Wave 2C supersedes the Wave 2B loopback-only boundary with `V2MainSecurityMiddleware`, adding prefs-backed API key permission checks while preserving loopback operator access.

**Tech Stack:** Python, FastAPI, pytest, `StorageManager`, YouTubeBridgeV2 runtime composition.

---

## Scope

Source:

- `YouTubeBridgeV2/production.py`
- `YouTubeBridgeV2/runtime/noop_runners.py`
- `YouTubeBridgeV2/server/main_security.py`
- `api/main.py`
- `YouTubeBridgeV2/server/routes.py`
- `YouTubeBridgeV2/query_service.py`

Tests:

- `tests/youtubebridge_v2/test_main_app_wiring.py`
- `tests/youtubebridge_v2/test_operator_console_ui.py`

Docs:

- `YouTubeBridgeV2/docs/architecture-index.md`
- `YouTubeBridgeV2/docs/api-reference-index.md`
- `YouTubeBridgeV2/docs/modules/server-api-surface.md`
- `YouTubeBridgeV2/docs/modules/access-control-security.md`

Out of scope:

- No real YouTube polling.
- No real MemoriaCore group chat or chat sync calls.
- No TTS delivery.
- No `/v2` tick endpoint.
- No environment API key or per-route permission matrix.

## Planned Symbols

- `create_production_v2_composition(storage_manager)`
- `NoopPlannedShowRunner`
- `NoopAftertalkRunner`
- `NoopClosingRunner`
- `V2LoopbackOnlyMiddleware`

Internal main-app helpers:

- `_get_v2_composition()`
- `_get_v2_runtime_service()`
- `_get_v2_query_service()`

## Red Cases

- `test_main_app_v2_routes_use_real_storage_composition`
- `test_main_app_v2_status_reads_durable_session`
- `test_main_app_v2_reuses_cached_composition_for_same_storage`
- `test_main_app_v2_missing_session_returns_sanitized_404`
- `test_main_app_v2_missing_session_events_and_streams_return_sanitized_404`
- `test_main_app_v2_rejects_non_loopback_api_request`
- `test_main_app_v2_static_assets_remain_served`
- `test_main_app_v2_wiring_does_not_import_legacy_youtubebridge`

Expected red command:

```powershell
python -m pytest tests\youtubebridge_v2\test_main_app_wiring.py -q
```

Expected red result before implementation:

- Main app `/v2` returns `unconfigured`.
- `_get_v2_composition` is missing.
- Missing session queries return fallback or raw errors.
- Non-loopback `/v2` API requests are not rejected.
- Production wiring files are missing.

## Green Scope

- Create production composition helper using the existing `StorageManager` singleton supplied by `api.main.get_storage()`.
- Add explicit no-op runners that return public-safe `AdapterDispatchResult`.
- Replace main-app unconfigured dependency overrides with lazy cached production composition overrides.
- Add loopback-only middleware for `/v2` API/SSE routes, excluding `/v2/static`.
- Map missing session query errors to stable 404 JSON responses.
- Keep static V2 UI assets served by the main app.

## Refactor Boundary

Allowed:

- Add focused helper modules under `YouTubeBridgeV2/`.
- Add internal cache helpers in `api/main.py`.
- Add route-level query error helper functions.

Forbidden:

- Importing or reusing Legacy `YouTubeBridge/`.
- Adding direct SQLite access outside `core/storage/` and `core/storage_manager.py`.
- Calling real YouTube, MemoriaCore, or TTS adapters.
- Adding API key storage or route permission matrix in this wave.

## Adapter Strategy

The production composition uses no-op runners for planned show, aftertalk, and closing. This confirms `/v2` API and durable storage wiring without external side effects. True adapters replace these runners in a later wave.

## Docs Sync

After implementation:

- Mark Wave 2B production wiring complete in `docs/architecture-index.md`.
- Add production helper, no-op runners, and loopback middleware sources in `docs/api-reference-index.md`.
- Update Server/API and Access Control module docs to reflect main-app loopback-only behavior.

## Execution Steps

- [x] Add fail-first main-app wiring tests.
- [x] Confirm red failures show unconfigured fallback, missing helper, missing files, and absent loopback boundary.
- [x] Add production composition helper.
- [x] Add explicit no-op runners.
- [x] Add main-app loopback-only middleware.
- [x] Replace main-app `/v2` dependency overrides with lazy cached durable composition.
- [x] Add missing-session 404 mapping for status, events, and streams.
- [x] Update existing operator console main-app test away from fallback behavior.
- [x] Run targeted tests.
- [x] Sync docs and API reference.

## Acceptance Criteria

- Main app `/v2` writes through true `StorageManager` durable backend.
- Main app `/v2` no longer returns `v2_runtime_not_configured` during normal loopback use.
- Non-loopback `/v2` API/SSE requests are rejected before runtime dispatch.
- `/v2/static` remains served.
- Missing session read/event/stream requests return sanitized 404.
- V2 production wiring does not import Legacy `YouTubeBridge/`.
