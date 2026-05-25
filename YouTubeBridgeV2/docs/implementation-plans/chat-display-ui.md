# Chat Display UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task by task. Steps use checkbox syntax for tracking.

**Goal:** Implement the livestream-facing chat display that renders display-safe events.

**Architecture:** Chat Display UI consumes display stream events and renders audience, character, Super Chat, system, and presentation metadata. It has read-only access and no runtime control path.

**Tech Stack:** Browser UI, JavaScript or framework chosen by V2 frontend setup, pytest or browser smoke tests.

---

## Scope

Planned source: `YouTubeBridgeV2/static/chat-display/`

Planned tests: `tests/youtubebridge_v2/test_chat_display_ui.py`

## Planned Symbols

- `DisplayMessageEvent`
- `DisplaySystemStateEvent`
- `DisplaySuperChatEvent`
- `DisplayCharacterResponseEvent`
- `DisplayPresentationMetadata`

## Red Cases

- `test_chat_display_renders_audience_message`
- `test_chat_display_renders_character_response_with_role_label`
- `test_chat_display_renders_super_chat_metadata`
- `test_chat_display_renders_aftertalk_status_banner`
- `test_chat_display_renders_closing_status_banner`
- `test_chat_display_renders_existing_closing_status_event_shape`
- `test_chat_display_renders_display_flags_for_audience_message`
- `test_chat_display_orders_events_by_sequence_when_rendering_replay`
- `test_chat_display_missing_session_id_renders_system_banner`
- `test_malformed_display_event_uses_safe_fallback`
- `test_render_display_events_keeps_safe_fallback_for_unsupported_items`
- `test_display_permission_does_not_call_control_api`
- `test_hidden_prompt_and_operator_metadata_are_not_rendered`

Expected red command:

```powershell
python -m pytest tests/youtubebridge_v2/test_chat_display_ui.py -q
```

Expected red result before implementation: missing UI test support or missing planned UI module.

## Green Scope

- Implement display event rendering and safe fallback.
- Keep controls absent from display UI.
- Render display-safe phase and presentation metadata.
- Render existing closing status event shape and public display flags.
- Keep replay rendering stable by `sequence` when present.

## Refactor Boundary

Allowed: split rendering helpers by event type.

Forbidden: operator controls, runtime direct import, adapter call, storage write, or secret display.

## Adapter Strategy

Tests use fake display events. Browser smoke can be added after UI stack exists.

## Docs Sync

Update API reference if display event names or payload fields change. Add Source values after UI files exist.

## Execution Steps

- [x] Create failing display UI tests.
- [x] Run the red command and confirm expected failure.
- [x] Create chat display UI files.
- [x] Implement event rendering and safe fallback.
- [x] Run the green command. Browser smoke is deferred until the UI stack is stable.
- [x] Refactor rendering helpers and rerun tests.
- [x] Sync API reference after files exist.

## Acceptance Criteria

- Display UI renders audience, character, Super Chat, system, and presentation events.
- Display UI never calls control endpoints.
- Hidden prompt, raw payload, and operator-only metadata are not rendered.
- Existing `closing_status` events render as closing status banners.
- Display-safe audience flags render as allowlisted badges.
- Replay/initial events sort by `sequence` when present.
