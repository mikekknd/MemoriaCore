# Presentation/TTS Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task by task. Steps use checkbox syntax for tracking.

**Goal:** Implement optional presentation and TTS event consumption for completed V2 interactions.

**Architecture:** Presentation/TTS is an event consumer. It queues display/voice output requests and records ack or timeout results without influencing runtime phase.

**Tech Stack:** Python 3.12, pytest。第一版只建立 provider-neutral queue/request contract，不選定或呼叫真實 TTS provider。

---

## Scope

Source: `YouTubeBridgeV2/presentation/tts.py`

Tests: `tests/youtubebridge_v2/test_presentation_tts.py`

## Public Symbols

- `PresentationEvent`
- `TTSRequest`
- `DeliveryAck`
- `DeliveryTimeoutResult`
- `PresentationDisplayMetadata`
- `build_presentation_event(interaction)`
- `enqueue_tts_request(event, policy)`
- `record_delivery_ack(delivery_id)`
- `record_delivery_timeout(delivery_id)`

## Red Cases

- `test_completed_character_response_builds_presentation_event`
- `test_tts_enabled_enqueues_tts_request`
- `test_tts_disabled_keeps_display_metadata_without_request`
- `test_queue_preserves_event_order`
- `test_delivery_ack_marks_success`
- `test_delivery_timeout_marks_timeout_without_phase_change`
- `test_malformed_event_is_skipped_safely`
- `test_display_metadata_excludes_hidden_prompt_and_raw_payload`
- `test_presentation_tts_does_not_cross_runtime_or_external_boundaries`

Expected red command:

```powershell
python -m pytest tests/youtubebridge_v2/test_presentation_tts.py -q
```

Expected red result before implementation: missing `YouTubeBridgeV2.presentation.tts` or planned symbols.

## Green Scope

- Implement event and request dataclasses.
- Implement queue ordering in memory or through an abstract queue contract.
- Implement ack and timeout result helpers.
- Implement disabled TTS behavior.

## Refactor Boundary

Allowed: split provider-neutral queue helpers from provider-specific adapters.

Forbidden: phase decision, LLM generation, YouTube polling, operator controls, or direct storage ownership.

## Adapter Strategy

Unit tests use fake provider/queue objects. Real TTS provider tests are separate integration tests.

## Docs Sync

After implementation exists, update API Source values for Presentation/TTS contracts.

## Execution Steps

- [x] Create failing presentation/TTS tests.
- [x] Run the red command and confirm expected failure.
- [x] Create presentation package and planned symbols.
- [x] Implement event construction, queueing, ack, timeout, and disabled behavior.
- [x] Run the green command and confirm all tests pass.
- [x] Refactor queue/provider boundaries and rerun tests.
- [x] Sync API reference Source values after symbols exist.

## Acceptance Criteria

- Completed interactions can become presentation events.
- TTS request behavior respects enabled/disabled policy.
- Ack and timeout are recorded without changing runtime phase.
- Display metadata is public-safe.
