# MemoriaCore Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task by task. Steps use checkbox syntax for tracking.

**Goal:** Implement the V2 adapter boundary that maps planned show and aftertalk intents into MemoriaCore requests and normalizes responses.

**Architecture:** The adapter is the only V2 layer that knows MemoriaCore transport details. It returns normalized responses and redacted summaries without changing runtime phase or writing storage.

**Tech Stack:** Python 3.12, pytest, HTTP client selected by the runtime service implementation.

---

## Scope

Planned source: `YouTubeBridgeV2/adapters/memoria.py`

Planned tests: `tests/youtubebridge_v2/test_memoria_adapter.py`

## Planned Symbols

- `MemoriaRequestPayload`
- `NormalizedMemoriaResponse`
- `MemoriaAdapterError`
- `MemoriaCorrelationMetadata`
- `build_memoria_request(intent, context)`
- `normalize_memoria_response(response_payload, correlation_metadata)`
- `classify_memoria_error(error)`

## Red Cases

- `test_planned_turn_intent_maps_to_memoria_chat_request`
- `test_aftertalk_request_maps_to_group_chat_request`
- `test_memoria_response_is_normalized_with_session_id`
- `test_group_chat_response_requires_speaker_metadata`
- `test_timeout_is_classified_as_retryable_adapter_error`
- `test_transport_failure_is_classified_without_phase_change`
- `test_auth_failure_is_classified_as_terminal`
- `test_public_summary_excludes_hidden_prompt_and_raw_payload`

Expected red command:

```powershell
python -m pytest tests/youtubebridge_v2/test_memoria_adapter.py -q
```

Expected red result before implementation: missing `YouTubeBridgeV2.adapters.memoria` or missing planned symbols.

## Green Scope

- Implement request builder for planned show and aftertalk intents.
- Implement response normalizer for chat and group chat shapes.
- Implement error classification.
- Return redacted request/response summaries.

## Refactor Boundary

Allowed: private helper functions for payload shape, response extraction, and redaction.

Forbidden: phase transition, plan cursor updates, storage transactions, YouTube calls, UI events, or retry loop ownership.

## Adapter Strategy

Unit tests use fake response payloads and fake exception objects. Real MemoriaCore HTTP integration belongs to a marked integration test after endpoint shape is confirmed.

## Docs Sync

After implementation exists, update API Source values for adapter contracts. If MemoriaCore endpoint shape differs from this plan, update this plan and module design before implementation continues.

## Execution Steps

- [x] Create failing tests in `tests/youtubebridge_v2/test_memoria_adapter.py`.
- [x] Run the red command and confirm expected failure.
- [x] Create `YouTubeBridgeV2/adapters/memoria.py` with planned symbols.
- [x] Implement request mapping and response normalization.
- [x] Implement error classification and redaction.
- [x] Run the green command and confirm all tests pass.
- [x] Refactor private helpers and rerun tests.
- [x] Sync API reference Source values after symbols exist.

## Acceptance Criteria

- Planned show and aftertalk requests map to distinct MemoriaCore modes.
- Responses are normalized with speaker/session/correlation metadata.
- Public summaries exclude hidden prompt and raw payload.
- Adapter does not change phase or write storage.
