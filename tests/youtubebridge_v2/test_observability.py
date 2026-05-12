from datetime import datetime, timezone

from YouTubeBridgeV2.adapters.memoria import MemoriaAdapterError
from YouTubeBridgeV2.runtime.observability import (
    AdapterTraceSummary,
    CorrelationMetadata,
    DiagnosticEvent,
    RuntimeErrorEvent,
    TransitionLogEntry,
    build_transition_log_entry,
    classify_runtime_error,
    redact_adapter_summary,
)
from YouTubeBridgeV2.runtime.phase import (
    LiveSessionPhase,
    PhaseTransition,
    PhaseTransitionReason,
)


RECORDED_AT = datetime(2026, 5, 12, 9, 0, tzinfo=timezone.utc)


def _transition(metadata=None):
    return PhaseTransition(
        current_phase=LiveSessionPhase.PLANNED_SHOW,
        next_phase=LiveSessionPhase.AFTERTALK,
        changed=True,
        reason=PhaseTransitionReason.AFTERTALK_ENABLED,
        metadata=metadata
        or {
            "previous_phase": "planned_show",
            "next_phase": "aftertalk",
            "reason": "aftertalk_enabled",
        },
        next_action="start_aftertalk",
    )


def _correlation():
    return CorrelationMetadata(
        correlation_id="corr-1",
        request_id="req-1",
        session_id="session-1",
        trace_id="trace-1",
    )


def _assert_public_safe(value):
    text = repr(value).lower()
    for forbidden in (
        "hidden_prompt",
        "raw_topic_pack",
        "topic_pack_fact_cards",
        "raw_factcard",
        "raw_payload",
        "raw_memoriacore_payload",
        "memoriacore_raw",
        "authorization",
        "access_token",
        "secret",
        "token",
    ):
        assert forbidden not in text


def test_transition_log_entry_contains_phase_reason_and_correlation_id():
    entry = build_transition_log_entry(
        _transition(),
        correlation=_correlation(),
        recorded_at=RECORDED_AT,
    )

    assert isinstance(entry, TransitionLogEntry)
    assert entry.previous_phase == "planned_show"
    assert entry.next_phase == "aftertalk"
    assert entry.reason == "aftertalk_enabled"
    assert entry.next_action == "start_aftertalk"
    assert entry.correlation.correlation_id == "corr-1"
    assert entry.recorded_at == RECORDED_AT


def test_adapter_summary_redacts_hidden_prompt():
    summary = redact_adapter_summary(
        {
            "adapter_name": "memoria",
            "request_type": "group_chat",
            "status": "ok",
            "duration_ms": 42,
            "metadata": {
                "speaker_count": 2,
                "hidden_prompt": "do not expose",
                "nested": {"hidden_prompt": "still secret", "visible": "ok"},
            },
        },
        correlation=_correlation(),
    )

    assert isinstance(summary, AdapterTraceSummary)
    assert summary.metadata["speaker_count"] == 2
    assert summary.metadata["nested"] == {"visible": "ok"}
    _assert_public_safe(summary)


def test_adapter_summary_redacts_raw_memoria_payload():
    summary = redact_adapter_summary(
        {
            "adapter_name": "memoria",
            "request_type": "chat",
            "status": "error",
            "metadata": {
                "safe": "timeout",
                "raw_memoriacore_payload": {
                    "authorization": "Bearer super-private-token",
                    "reply": "raw",
                },
                "items": [{"memoriacore_raw": "secret", "id": "visible"}],
            },
        }
    )

    assert summary.metadata == {"safe": "timeout", "items": [{"id": "visible"}]}
    _assert_public_safe(summary)


def test_adapter_summary_redacts_header_containers_and_bearer_text():
    summary = redact_adapter_summary(
        {
            "adapter_name": "youtube",
            "request_type": "poll_live_chat",
            "status": "ok",
            "metadata": {
                "safe": "visible",
                "headers": {
                    "X-Api-Key": "AIza-private",
                    "Authorization": "Bearer provider-secret",
                },
                "nested": {
                    "response_headers": {"Set-Cookie": "session=secret"},
                    "public": "ok",
                },
                "note": "Bearer provider-secret",
            },
        },
        correlation=_correlation(),
    )

    assert summary.metadata == {
        "safe": "visible",
        "nested": {"public": "ok"},
        "note": "[redacted]",
    }
    _assert_public_safe(summary)
    assert "bearer" not in repr(summary).lower()
    assert "AIza" not in repr(summary)


class TransportError(Exception):
    status_code = 503
    retryable = True


class AuthError(Exception):
    status_code = 401


class InvalidResponseError(Exception):
    error_type = "invalid_response"


def test_error_event_classifies_timeout_transport_auth_and_invalid_response():
    timeout = classify_runtime_error(TimeoutError("adapter timed out"))
    transport = classify_runtime_error(TransportError("upstream unavailable"))
    auth = classify_runtime_error(AuthError("Bearer super-private-token rejected"))
    invalid = classify_runtime_error(
        InvalidResponseError("invalid response with raw_payload")
    )

    assert isinstance(timeout, RuntimeErrorEvent)
    assert timeout.error_class == "timeout"
    assert timeout.retryable is True
    assert transport.error_class == "transport"
    assert transport.retryable is True
    assert auth.error_class == "auth"
    assert auth.retryable is False
    assert invalid.error_class == "invalid_response"
    assert invalid.retryable is False
    _assert_public_safe((timeout, transport, auth, invalid))


def test_structured_adapter_error_is_classified_and_redacted():
    error = classify_runtime_error(
        MemoriaAdapterError(
            error_type="auth_failure",
            retryable=False,
            status_code=403,
            public_summary={
                "error_type": "auth_failure",
                "retryable": False,
                "status_code": 403,
                "raw_payload": {"authorization": "Bearer provider-secret"},
                "safe": "visible",
            },
        ),
        correlation=_correlation(),
        recorded_at=RECORDED_AT,
    )

    assert error.error_class == "auth"
    assert error.retryable is False
    assert error.status_code == 403
    assert error.metadata["public_summary"] == {
        "error_type": "auth_failure",
        "retryable": False,
        "status_code": 403,
        "safe": "visible",
    }
    _assert_public_safe(error)


def test_missing_correlation_id_creates_diagnostic_warning():
    entry = build_transition_log_entry(
        _transition(),
        correlation={"session_id": "session-1"},
        recorded_at=RECORDED_AT,
    )

    assert entry.correlation.correlation_id is None
    assert entry.diagnostics
    assert entry.diagnostics[0].event_type == "missing_correlation_id"
    assert entry.diagnostics[0].severity == "warning"


def test_adapter_and_error_events_warn_when_correlation_id_is_missing():
    summary = redact_adapter_summary(
        {
            "adapter_name": "tts",
            "request_type": "synthesize",
            "status": "timeout",
            "metadata": {"safe": "visible"},
        },
        correlation={"session_id": "session-1"},
        recorded_at=RECORDED_AT,
    )
    error = classify_runtime_error(
        TimeoutError("provider timed out"),
        correlation={"session_id": "session-1"},
        recorded_at=RECORDED_AT,
    )

    assert summary.diagnostics[0].event_type == "missing_correlation_id"
    assert summary.diagnostics[0].metadata == {"source": "adapter_trace"}
    assert error.diagnostics[0].event_type == "missing_correlation_id"
    assert error.diagnostics[0].metadata == {"source": "runtime_error"}


def test_logging_failure_does_not_change_phase_transition():
    transition = _transition()
    entry = build_transition_log_entry(
        transition,
        correlation=_correlation(),
        recorded_at=RECORDED_AT,
    )

    def broken_sink(_event):
        raise RuntimeError("disk failure with raw_payload and secret")

    failure = entry.emit_to(broken_sink)

    assert transition.next_phase == LiveSessionPhase.AFTERTALK
    assert transition.reason == PhaseTransitionReason.AFTERTALK_ENABLED
    assert isinstance(failure, DiagnosticEvent)
    assert failure.event_type == "observability_failure"
    assert failure.severity == "warning"
    _assert_public_safe(failure)


def test_public_diagnostic_excludes_raw_topic_pack():
    event = DiagnosticEvent(
        event_type="operator_diagnostic",
        severity="info",
        message="planned turn summary",
        metadata={
            "safe": "visible",
            "raw_topic_pack": {"hidden_prompt": "do not expose"},
            "turns": [{"raw_factcard": "secret", "title": "visible"}],
            "topic_pack_fact_cards": ["raw card"],
        },
        correlation=_correlation(),
    )

    assert event.metadata == {"safe": "visible", "turns": [{"title": "visible"}]}
    _assert_public_safe(event)
