# Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task by task. Steps use checkbox syntax for tracking.

**Goal:** Implement V2 diagnostic event contracts for phase transitions, adapter summaries, runtime errors, and correlation metadata.

**Architecture:** Observability consumes summaries from runtime and adapters. It redacts sensitive fields and emits diagnostic records without changing runtime behavior.

**Tech Stack:** Python 3.12, pytest, dataclasses.

---

## Scope

Planned source: `YouTubeBridgeV2/runtime/observability.py`

Planned tests: `tests/youtubebridge_v2/test_observability.py`

## Planned Symbols

- `TransitionLogEntry`
- `AdapterTraceSummary`
- `RuntimeErrorEvent`
- `CorrelationMetadata`
- `DiagnosticEvent`
- `build_transition_log_entry(transition)`
- `redact_adapter_summary(summary)`
- `classify_runtime_error(error)`

## Red Cases

- `test_transition_log_entry_contains_phase_reason_and_correlation_id`
- `test_adapter_summary_redacts_hidden_prompt`
- `test_adapter_summary_redacts_raw_memoria_payload`
- `test_error_event_classifies_timeout_transport_auth_and_invalid_response`
- `test_missing_correlation_id_creates_diagnostic_warning`
- `test_logging_failure_does_not_change_phase_transition`
- `test_public_diagnostic_excludes_raw_topic_pack`

Expected red command:

```powershell
python -m pytest tests/youtubebridge_v2/test_observability.py -q
```

Expected red result before implementation: missing `YouTubeBridgeV2.runtime.observability` or planned symbols.

## Green Scope

- Implement diagnostic dataclasses.
- Implement redaction and classification helpers.
- Keep output lightweight and public-safe.

## Refactor Boundary

Allowed: private redaction key lists if they are derived from documented public/private boundaries.

Forbidden: phase decision, adapter retry, storage writes, or UI rendering.

## Adapter Strategy

No external adapter dependency. Tests use sample summaries and error objects.

## Docs Sync

Add Source values for observability contracts after implementation exists.

## Execution Steps

- [x] Create failing observability tests.
- [x] Run the red command and confirm expected failure.
- [x] Create observability module with planned symbols.
- [x] Implement transition log, redaction, and error classification.
- [x] Run the green command and confirm all tests pass.
- [x] Refactor redaction helpers and rerun tests.
- [x] Sync API reference Source values after symbols exist.

## Acceptance Criteria

- Diagnostic events are useful and compact.
- Hidden prompt/raw Topic Pack/raw adapter payload do not appear.
- Observability failures do not affect phase decisions.
- Missing correlation id creates warning diagnostics for transition, adapter, and runtime error records.
