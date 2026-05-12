# YouTube Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task by task. Steps use checkbox syntax for tracking.

**Goal:** Implement YouTube event normalization and polling boundary contracts for V2.

**Architecture:** The adapter owns YouTube API shapes and converts them to V2 normalized events. It does not decide phase, write storage directly, or render UI.

**Tech Stack:** Python 3.12, pytest, YouTube client selected during adapter implementation.

---

## Scope

Planned source: `YouTubeBridgeV2/adapters/youtube.py`

Planned tests: `tests/youtubebridge_v2/test_youtube_adapter.py`

## Planned Symbols

- `NormalizedYouTubeEvent`
- `YouTubePollingCursor`
- `SuperChatMetadata`
- `YouTubeStreamStatus`
- `YouTubeAdapterError`
- `normalize_youtube_event(raw_event)`
- `extract_super_chat_metadata(raw_event)`
- `classify_youtube_error(error)`

## Red Cases

- `test_normalize_text_message_event`
- `test_normalize_super_chat_event_with_metadata`
- `test_pagination_cursor_is_preserved`
- `test_duplicate_event_id_is_detected`
- `test_live_ended_state_returns_stream_status`
- `test_transient_api_error_is_retryable`
- `test_auth_error_is_terminal`
- `test_normalized_event_excludes_raw_youtube_payload`
- `test_adapter_does_not_emit_phase_transition`

Expected red command:

```powershell
python -m pytest tests/youtubebridge_v2/test_youtube_adapter.py -q
```

Expected red result before implementation: missing `YouTubeBridgeV2.adapters.youtube` or planned symbols.

## Green Scope

- Implement raw event normalization.
- Implement Super Chat metadata extraction.
- Implement cursor and duplicate helpers.
- Implement stream status and error classification.

## Refactor Boundary

Allowed: private helpers for event type mapping and currency metadata.

Forbidden: Runtime Phase calls, MemoriaCore calls, storage writes, UI rendering, or closing script generation.

## Adapter Strategy

Unit tests use raw event fixtures. Real YouTube API calls belong to separately marked integration tests.

## Docs Sync

After implementation exists, update API Source values for YouTube adapter contracts.

## Execution Steps

- [ ] Create failing adapter tests.
- [ ] Run the red command and confirm expected failure.
- [ ] Create YouTube adapter module with planned symbols.
- [ ] Implement event normalization, Super Chat extraction, cursor handling, and error classification.
- [ ] Run the green command and confirm all tests pass.
- [ ] Refactor event helpers and rerun tests.
- [ ] Sync API reference Source values after symbols exist.

## Acceptance Criteria

- YouTube raw events become V2 normalized events.
- Super Chat metadata is preserved in display-safe form.
- Adapter classifies retryable and terminal failures.
- Adapter has no phase transition side effects.
