# Server/API Surface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task by task. Steps use checkbox syntax for tracking.

**Goal:** Implement V2 HTTP and SSE entrypoints that delegate runtime work to application services.

**Architecture:** Routes own request/response mapping and event stream contracts. They do not decide phase, call adapters directly, or bypass security.

**Tech Stack:** Python 3.12, FastAPI, pytest.

---

## Scope

Planned source: `YouTubeBridgeV2/server/routes.py`

Planned tests: `tests/youtubebridge_v2/test_server_api_surface.py`

## Planned Symbols

- `create_session_endpoint`
- `get_session_endpoint`
- `bind_plan_endpoint`
- `get_phase_endpoint`
- `update_aftertalk_policy_endpoint`
- `manual_close_endpoint`
- `get_session_events_endpoint`
- `operator_stream_endpoint`
- `display_stream_endpoint`

## Red Cases

- `test_create_session_delegates_to_runtime_service`
- `test_bind_plan_validates_request_shape`
- `test_get_phase_returns_phase_status_body`
- `test_aftertalk_policy_update_delegates_to_service`
- `test_manual_close_delegates_without_direct_phase_change`
- `test_get_session_events_returns_event_history`
- `test_get_session_events_uses_query_service_without_direct_storage_access`
- `test_operator_stream_emits_operator_events`
- `test_display_stream_emits_display_safe_events`
- `test_route_error_response_is_sanitized`
- `test_routes_do_not_call_adapters_directly`

Expected red command:

```powershell
python -m pytest tests/youtubebridge_v2/test_server_api_surface.py -q
```

Expected red result before implementation: missing `YouTubeBridgeV2.server.routes` or planned endpoints.

## Green Scope

- Implement route functions and request/response models.
- Delegate to injected runtime service.
- Implement event history read endpoint through the runtime/query service boundary.
- Return sanitized errors.
- Provide operator and display SSE stream shapes.

## Refactor Boundary

Allowed: split request/response models under `YouTubeBridgeV2/server/`.

Forbidden: direct storage internals, direct MemoriaCore/YouTube calls, direct phase mutation, or auth duplication.

## Adapter Strategy

Tests use fake runtime service and fake permission context. No real adapters.

## Docs Sync

Add API Source values after routes exist. Keep endpoint list aligned with module design.

## Execution Steps

- [ ] Create failing FastAPI route tests.
- [ ] Run the red command and confirm expected failure.
- [ ] Create server route module and request/response contracts.
- [ ] Implement delegation to runtime service.
- [ ] Implement event history endpoint through a query/service dependency.
- [ ] Implement SSE event wrappers.
- [ ] Run the green command and confirm all tests pass.
- [ ] Refactor route helpers and rerun tests.
- [ ] Sync API reference Source values after routes exist.

## Acceptance Criteria

- Routes delegate behavior to services.
- Event history endpoint is covered by planned symbols and red cases.
- Operator and display streams have distinct payload boundaries.
- Error responses are sanitized.
- No route directly calls adapters or storage internals.
