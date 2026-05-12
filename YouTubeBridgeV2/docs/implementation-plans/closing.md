# Closing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task by task. Steps use checkbox syntax for tracking.

**Goal:** Implement the V2 closing module that builds final closing intent, handles pending Super Chat acknowledgements, and reports finalization status to Runtime Phase.

**Architecture:** Closing runs after Runtime Phase enters `closing`. It creates redacted closing requests/results, delegates MemoriaCore transport to the adapter through Runtime Application Service, and produces `closing_completion_status`.

**Tech Stack:** Python 3.12, pytest, dataclasses.

---

## Scope

Planned source: `YouTubeBridgeV2/runtime/closing.py`

Planned tests: `tests/youtubebridge_v2/test_closing.py`

This implementation must not stop YouTube livestreams directly and must not write storage directly.

## Planned Symbols

- `ClosingStartContext`
- `ClosingReason`
- `ClosingPolicy`
- `ClosingRequest`
- `ClosingSuperChatAction`
- `ClosingFinalizationResult`
- `ClosingCompletionStatus`
- `ClosingDisplayEvent`
- `build_closing_request(context, summary, pending_super_chats, policy)`
- `finalize_closing(context, adapter_result, policy)`

## Red Cases

- `test_manual_close_context_builds_closing_request`
- `test_duration_reached_context_builds_closing_request`
- `test_stream_ended_context_builds_closing_request`
- `test_pending_super_chats_create_acknowledgement_actions`
- `test_malformed_super_chat_is_skipped_with_redacted_error`
- `test_final_message_disabled_allows_system_only_finalization`
- `test_memoria_timeout_returns_retryable_completion_status`
- `test_terminal_memoria_error_can_finalize_with_system_summary`
- `test_duplicate_closing_command_is_idempotent`
- `test_complete_finalization_status_moves_runtime_phase_to_ended`
- `test_closing_display_event_excludes_hidden_prompt_raw_super_chat_and_raw_memoria_payload`

Expected red command:

```powershell
python -m pytest tests/youtubebridge_v2/test_closing.py -q
```

Expected red result before implementation: missing `YouTubeBridgeV2.runtime.closing` or missing planned symbols.

## Green Scope

- Implement closing dataclasses/enums.
- Implement closing request construction from redacted session summary and pending Super Chat summary.
- Implement finalization result mapping for success, retryable timeout, terminal fallback, and system-only closing.
- Return `ClosingCompletionStatus` without changing Runtime Phase directly.

## Refactor Boundary

Allowed: private helpers for Super Chat normalization, redaction, and finalization status mapping.

Forbidden: direct MemoriaCore HTTP, direct YouTube livestream stop, direct `StorageManager` bypass, direct SQLite writes, UI rendering, TTS provider calls, or Legacy closing fallback.

## Adapter Strategy

Unit tests use fake adapter result objects. MemoriaCore final-message transport is tested in MemoriaCore Adapter integration tests, not here.

## Docs Sync

After implementation exists, update API Source values for closing contracts. Update Runtime Phase docs only if `ClosingCompletionStatus` semantics change.

## Execution Steps

- [ ] Create failing closing tests.
- [ ] Run the red command and confirm expected failure.
- [ ] Create `YouTubeBridgeV2/runtime/closing.py` with planned symbols.
- [ ] Implement request construction and Super Chat action mapping.
- [ ] Implement finalization status mapping.
- [ ] Run the green command and confirm all tests pass.
- [ ] Refactor redaction/status helpers and rerun tests.
- [ ] Sync API reference Source values after symbols exist.

## Acceptance Criteria

- Closing can produce final request and finalization result without owning transport.
- Pending Super Chat summaries are handled deterministically.
- Completion status can drive Runtime Phase from `closing` to `ended`.
- Display event is redacted and public-safe.
