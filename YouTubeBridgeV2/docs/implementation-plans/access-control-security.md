# Access Control / Security Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task by task. Steps use checkbox syntax for tracking.

**Goal:** Implement V2 API permission metadata, credential boundaries, and sanitized security errors.

**Architecture:** Security is a shared boundary used by routes and adapters. It produces permission context and credential references without exposing secrets.

**Tech Stack:** Python 3.12, FastAPI dependency patterns, pytest.

---

## Scope

Planned source: `YouTubeBridgeV2/server/security.py`

Planned tests: `tests/youtubebridge_v2/test_access_control_security.py`

## Planned Symbols

- `AuthRequirement`
- `PermissionGroup`
- `PermissionContext`
- `SecurityErrorResponse`
- `SecretBoundary`
- `resolve_permission_context(request, requirement)`
- `sanitize_security_error(error)`

## Red Cases

- `test_missing_api_key_returns_unauthorized`
- `test_invalid_api_key_returns_unauthorized_without_secret`
- `test_loopback_access_allows_configured_dev_route`
- `test_display_scope_can_read_display_stream`
- `test_display_scope_cannot_call_manual_close`
- `test_operator_scope_can_update_aftertalk_policy`
- `test_security_error_does_not_include_raw_headers`
- `test_security_error_code_is_allowlisted`
- `test_memoria_secret_is_exposed_only_as_boundary_reference`
- `test_secret_boundary_asdict_redacts_public_metadata`
- `test_auth_requirement_serialization_does_not_expose_raw_api_keys`
- `test_loopback_display_route_uses_required_group_by_default`
- `test_route_id_requires_matching_action_even_when_group_matches`
- `test_internal_key_cannot_enter_public_operator_surface`
- `test_internal_key_can_enter_internal_surface`

Expected red command:

```powershell
python -m pytest tests/youtubebridge_v2/test_access_control_security.py -q
```

Expected red result before implementation: missing `YouTubeBridgeV2.server.security` or planned symbols.

## Green Scope

- Implement permission groups and requirement matching.
- Implement API key and loopback evaluation according to config.
- Implement sanitized error body.
- Implement secret boundary references for adapters.
- Implement fail-safe route action checks and non-public raw key storage.

## Refactor Boundary

Allowed: private helpers for header parsing and permission comparison.

Forbidden: route business logic, direct adapter calls, UI decisions, or logging raw secrets.

## Adapter Strategy

No external adapter dependency. Tests use fake request objects and in-memory config.

## Docs Sync

Add API Source values for security contracts after implementation exists.

## Execution Steps

- [x] Create failing security tests.
- [x] Run the red command and confirm expected failure.
- [x] Create security module with planned symbols.
- [x] Implement permission context resolution.
- [x] Implement sanitized errors and secret boundary references.
- [x] Run the green command and confirm all tests pass.
- [x] Refactor private helpers and rerun tests.
- [x] Sync API reference Source values after symbols exist.

## Acceptance Criteria

- Permission groups are deterministic.
- Display scope remains read-only.
- Secrets never appear in responses or public diagnostics.
- Routes can consume permission context without duplicating auth rules.
