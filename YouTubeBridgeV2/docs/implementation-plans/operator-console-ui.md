# Operator Console UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task by task. Steps use checkbox syntax for tracking.

**Goal:** Implement the operator console UI surface for monitoring and controlling V2 live sessions.

**Architecture:** The UI consumes Server/API Surface contracts and sends operator actions. It does not import runtime modules or call adapters.

**Tech Stack:** Browser UI, JavaScript or framework chosen by V2 frontend setup, pytest or browser smoke tests.

---

## Scope

Planned source: `YouTubeBridgeV2/static/operator-console/`

Planned tests: `tests/youtubebridge_v2/test_operator_console_ui.py`

## Planned Symbols

- `OperatorSessionStatusView`
- `OperatorControlAction`
- `AftertalkPolicyControl`
- `ManualCloseCommand`
- `OperatorDiagnosticBanner`

## Red Cases

- `test_operator_console_renders_current_phase`
- `test_operator_console_renders_live_episode_plan_progress`
- `test_aftertalk_toggle_sends_policy_update`
- `test_remaining_time_is_displayed_from_phase_status`
- `test_manual_close_button_sends_manual_close_command`
- `test_controls_disable_while_action_is_in_flight`
- `test_error_banner_renders_sanitized_error`
- `test_display_only_permission_hides_operator_controls`

Expected red command:

```powershell
python -m pytest tests/youtubebridge_v2/test_operator_console_ui.py -q
```

Expected red result before implementation: missing UI test support or missing planned UI module.

## Green Scope

- Implement status rendering and operator controls.
- Consume API/SSE contracts only.
- Surface sanitized errors and stale stream state.

## Refactor Boundary

Allowed: split UI state mapping helpers and rendering components.

Forbidden: direct runtime import, adapter call, storage write, or chat display ownership.

## Adapter Strategy

Tests use fake API responses and fake SSE events. Browser smoke can be added after actual UI stack exists.

## Docs Sync

Update API reference only if UI-facing event names or endpoint usage changes. Add Source values after UI files exist.

## Execution Steps

- [ ] Create failing UI contract tests.
- [ ] Run the red command and confirm expected failure.
- [ ] Create operator console UI files.
- [ ] Implement phase/status rendering and controls.
- [ ] Implement error and stale state display.
- [ ] Run the green command and any browser smoke command.
- [ ] Refactor UI state mapping and rerun tests.
- [ ] Sync API reference after files exist.

## Acceptance Criteria

- Operator can see phase, plan progress, aftertalk state, remaining time, closing state, and errors.
- Operator controls call API endpoints only.
- Display-only users cannot control runtime.
