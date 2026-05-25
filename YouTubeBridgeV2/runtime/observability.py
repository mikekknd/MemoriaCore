"""Public-safe diagnostic contracts for YouTubeBridgeV2 runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from YouTubeBridgeV2.runtime.phase import PhaseTransition


_FORBIDDEN_KEYS = {
    "access_token",
    "api_key",
    "authorization",
    "cookie",
    "headers",
    "hidden_prompt",
    "memoriacore_raw",
    "password",
    "raw_adapter_payload",
    "raw_fact_card",
    "raw_fact_cards",
    "raw_factcard",
    "raw_headers",
    "raw_memoriacore_payload",
    "raw_payload",
    "raw_prompt",
    "raw_topic_pack",
    "request_headers",
    "response_headers",
    "secret",
    "secret_value",
    "set_cookie",
    "token",
    "topic_pack",
    "topic_pack_fact_cards",
}
_FORBIDDEN_TEXT = tuple(
    sorted(
        _FORBIDDEN_KEYS
        - {
            "cookie",
            "headers",
            "request_headers",
            "response_headers",
            "set_cookie",
        }
    )
)
_FORBIDDEN_TEXT_PATTERNS = (
    "authorization:",
    "basic ",
    "bearer ",
    "ghp_",
    "sk-",
    "x-api-key",
)


@dataclass(frozen=True)
class CorrelationMetadata:
    """Stable ids used to connect diagnostics across runtime and adapters."""

    correlation_id: str | None = None
    request_id: str | None = None
    session_id: str | None = None
    trace_id: str | None = None
    source: str = "runtime"

    def __post_init__(self) -> None:
        object.__setattr__(self, "correlation_id", _optional_string(self.correlation_id))
        object.__setattr__(self, "request_id", _optional_string(self.request_id))
        object.__setattr__(self, "session_id", _optional_string(self.session_id))
        object.__setattr__(self, "trace_id", _optional_string(self.trace_id))


@dataclass(frozen=True)
class DiagnosticEvent:
    """Operator-visible diagnostic event with recursively redacted metadata."""

    event_type: str
    severity: str
    message: str
    metadata: dict[str, object] = field(default_factory=dict)
    correlation: CorrelationMetadata | Mapping[str, object] | None = None
    recorded_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "message", _sanitize_public_text(self.message))
        object.__setattr__(self, "metadata", _redact_public_value(self.metadata))
        object.__setattr__(self, "correlation", _coerce_correlation(self.correlation))
        object.__setattr__(self, "recorded_at", self.recorded_at or _utc_now())

    def emit_to(self, sink: Callable[["DiagnosticEvent"], object]) -> "DiagnosticEvent | None":
        """Emit this event to a diagnostic sink without surfacing sink failures."""

        try:
            sink(self)
        except Exception as exc:
            return _observability_failure_event(exc, self.correlation)
        return None


@dataclass(frozen=True)
class TransitionLogEntry:
    """Public-safe phase transition diagnostic entry."""

    previous_phase: str
    next_phase: str
    changed: bool
    reason: str
    next_action: str
    metadata: dict[str, object]
    correlation: CorrelationMetadata | Mapping[str, object] | None = None
    recorded_at: datetime | None = None
    diagnostics: tuple[DiagnosticEvent, ...] = ()

    def __post_init__(self) -> None:
        correlation_metadata = _coerce_correlation(self.correlation)
        recorded_at = self.recorded_at or _utc_now()
        object.__setattr__(self, "metadata", _redact_public_value(self.metadata))
        object.__setattr__(self, "correlation", correlation_metadata)
        object.__setattr__(self, "recorded_at", recorded_at)
        object.__setattr__(
            self,
            "diagnostics",
            _diagnostics_with_missing_correlation(
                self.diagnostics,
                correlation_metadata,
                recorded_at,
                "transition_log",
            ),
        )

    def emit_to(self, sink: Callable[["TransitionLogEntry"], object]) -> DiagnosticEvent | None:
        """Emit this entry without letting observability failures affect runtime."""

        try:
            sink(self)
        except Exception as exc:
            return _observability_failure_event(exc, self.correlation)
        return None


@dataclass(frozen=True)
class AdapterTraceSummary:
    """Public-safe adapter trace summary."""

    adapter_name: str
    request_type: str
    status: str
    duration_ms: float | None = None
    metadata: dict[str, object] = field(default_factory=dict)
    correlation: CorrelationMetadata | Mapping[str, object] | None = None
    recorded_at: datetime | None = None
    diagnostics: tuple[DiagnosticEvent, ...] = ()

    def __post_init__(self) -> None:
        correlation_metadata = _coerce_correlation(self.correlation)
        recorded_at = self.recorded_at or _utc_now()
        object.__setattr__(self, "metadata", _redact_public_value(self.metadata))
        object.__setattr__(self, "correlation", correlation_metadata)
        object.__setattr__(self, "recorded_at", recorded_at)
        object.__setattr__(
            self,
            "diagnostics",
            _diagnostics_with_missing_correlation(
                self.diagnostics,
                correlation_metadata,
                recorded_at,
                "adapter_trace",
            ),
        )


@dataclass(frozen=True)
class RuntimeErrorEvent:
    """Classified runtime error suitable for public diagnostics."""

    error_class: str
    retryable: bool
    public_message: str
    status_code: int | None = None
    metadata: dict[str, object] = field(default_factory=dict)
    correlation: CorrelationMetadata | Mapping[str, object] | None = None
    recorded_at: datetime | None = None
    diagnostics: tuple[DiagnosticEvent, ...] = ()

    def __post_init__(self) -> None:
        correlation_metadata = _coerce_correlation(self.correlation)
        recorded_at = self.recorded_at or _utc_now()
        object.__setattr__(self, "public_message", _sanitize_public_text(self.public_message))
        object.__setattr__(self, "metadata", _redact_public_value(self.metadata))
        object.__setattr__(self, "correlation", correlation_metadata)
        object.__setattr__(self, "recorded_at", recorded_at)
        object.__setattr__(
            self,
            "diagnostics",
            _diagnostics_with_missing_correlation(
                self.diagnostics,
                correlation_metadata,
                recorded_at,
                "runtime_error",
            ),
        )


def build_transition_log_entry(
    transition: PhaseTransition,
    *,
    correlation: CorrelationMetadata | Mapping[str, object] | None = None,
    recorded_at: datetime | None = None,
) -> TransitionLogEntry:
    """Build a public transition log entry from a pure phase transition."""

    correlation_metadata = _coerce_correlation(correlation)
    return TransitionLogEntry(
        previous_phase=_phase_value(transition.current_phase),
        next_phase=_phase_value(transition.next_phase),
        changed=transition.changed,
        reason=_reason_value(transition.reason),
        next_action=transition.next_action,
        metadata=transition.metadata,
        correlation=correlation_metadata,
        recorded_at=recorded_at,
    )


def redact_adapter_summary(
    summary: AdapterTraceSummary | Mapping[str, object],
    *,
    correlation: CorrelationMetadata | Mapping[str, object] | None = None,
    recorded_at: datetime | None = None,
) -> AdapterTraceSummary:
    """Normalize an adapter summary and remove private/raw payload fields."""

    if isinstance(summary, AdapterTraceSummary):
        return AdapterTraceSummary(
            adapter_name=summary.adapter_name,
            request_type=summary.request_type,
            status=summary.status,
            duration_ms=summary.duration_ms,
            metadata=summary.metadata,
            correlation=correlation or summary.correlation,
            recorded_at=recorded_at or summary.recorded_at,
        )

    duration_ms = _optional_float(
        summary.get("duration_ms", summary.get("duration"))
    )
    return AdapterTraceSummary(
        adapter_name=str(summary.get("adapter_name", summary.get("adapter", "unknown"))),
        request_type=str(
            summary.get("request_type", summary.get("operation", "unknown"))
        ),
        status=str(summary.get("status", "unknown")),
        duration_ms=duration_ms,
        metadata=_adapter_metadata(summary),
        correlation=correlation or summary.get("correlation"),
        recorded_at=recorded_at,
    )


def classify_runtime_error(
    error: object,
    *,
    correlation: CorrelationMetadata | Mapping[str, object] | None = None,
    recorded_at: datetime | None = None,
) -> RuntimeErrorEvent:
    """Classify runtime/adapter errors without exposing raw exception content."""

    status_code = _optional_int(_field_value(error, "status_code"))
    retryable_value = _field_value(error, "retryable")
    error_type = str(
        _field_value(error, "error_type") or _field_value(error, "error_class") or ""
    ).lower()
    class_name = type(error).__name__.lower()

    if isinstance(error, TimeoutError) or error_type == "timeout" or "timeout" in class_name:
        return _runtime_error_event(
            "timeout",
            retryable=_optional_bool(retryable_value, default=True),
            public_message="runtime operation timed out",
            status_code=status_code,
            error=error,
            correlation=correlation,
            recorded_at=recorded_at,
        )

    if status_code in {401, 403} or "auth" in error_type or "auth" in class_name:
        return _runtime_error_event(
            "auth",
            retryable=_optional_bool(retryable_value, default=False),
            public_message="authentication failed",
            status_code=status_code,
            error=error,
            correlation=correlation,
            recorded_at=recorded_at,
        )

    if error_type == "invalid_response" or "invalidresponse" in class_name:
        return _runtime_error_event(
            "invalid_response",
            retryable=_optional_bool(retryable_value, default=False),
            public_message="adapter returned an invalid response",
            status_code=status_code,
            error=error,
            correlation=correlation,
            recorded_at=recorded_at,
        )

    if error_type in {"transport", "transport_failure"} or status_code is not None or retryable_value is not None:
        retryable = _optional_bool(
            retryable_value,
            default=_status_is_retryable(status_code),
        )
        return _runtime_error_event(
            "transport",
            retryable=retryable,
            public_message="transport failed",
            status_code=status_code,
            error=error,
            correlation=correlation,
            recorded_at=recorded_at,
        )

    return _runtime_error_event(
        "unexpected",
        retryable=False,
        public_message="unexpected runtime error",
        status_code=status_code,
        error=error,
        correlation=correlation,
        recorded_at=recorded_at,
    )


def _runtime_error_event(
    error_class: str,
    *,
    retryable: bool,
    public_message: str,
    status_code: int | None,
    error: object,
    correlation: CorrelationMetadata | Mapping[str, object] | None,
    recorded_at: datetime | None,
) -> RuntimeErrorEvent:
    metadata: dict[str, object] = {
        "exception_type": type(error).__name__
        if isinstance(error, BaseException)
        else None,
    }
    if metadata["exception_type"] is None:
        metadata = {"source_type": type(error).__name__}
    if status_code is not None:
        metadata["status_code"] = status_code
    public_summary = _field_value(error, "public_summary")
    if isinstance(public_summary, Mapping):
        metadata["public_summary"] = _redact_public_value(dict(public_summary))
    return RuntimeErrorEvent(
        error_class=error_class,
        retryable=retryable,
        public_message=public_message,
        status_code=status_code,
        metadata=metadata,
        correlation=correlation,
        recorded_at=recorded_at,
    )


def _observability_failure_event(
    error: BaseException,
    correlation: CorrelationMetadata | Mapping[str, object] | None,
) -> DiagnosticEvent:
    return DiagnosticEvent(
        event_type="observability_failure",
        severity="warning",
        message="diagnostic sink failed",
        metadata={"exception_type": type(error).__name__},
        correlation=correlation,
    )


def _adapter_metadata(summary: Mapping[str, object]) -> dict[str, object]:
    known_fields = {
        "adapter",
        "adapter_name",
        "correlation",
        "duration",
        "duration_ms",
        "operation",
        "request_type",
        "status",
    }
    metadata = summary.get("metadata")
    if isinstance(metadata, Mapping):
        return _redact_public_value(dict(metadata))
    return _redact_public_value(
        {
            key: value
            for key, value in summary.items()
            if str(key) not in known_fields
        }
    )


def _redact_public_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _redact_public_value(inner)
            for key, inner in value.items()
            if not _is_forbidden_key(str(key))
        }
    if isinstance(value, list):
        return [_redact_public_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_public_value(item) for item in value)
    if isinstance(value, str):
        return _sanitize_public_text(value)
    return value


def _sanitize_public_text(value: str) -> str:
    lowered = value.lower()
    if any(forbidden in lowered for forbidden in _FORBIDDEN_TEXT):
        return "[redacted]"
    if any(pattern in lowered for pattern in _FORBIDDEN_TEXT_PATTERNS):
        return "[redacted]"
    return value


def _is_forbidden_key(key: str) -> bool:
    normalized = _normalize_key(key)
    return any(forbidden == normalized or forbidden in normalized for forbidden in _FORBIDDEN_KEYS)


def _normalize_key(key: str) -> str:
    return key.lower().replace("-", "_").replace(" ", "_")


def _diagnostics_with_missing_correlation(
    diagnostics: tuple[DiagnosticEvent, ...],
    correlation: CorrelationMetadata,
    recorded_at: datetime,
    source: str,
) -> tuple[DiagnosticEvent, ...]:
    result = tuple(diagnostics)
    if correlation.correlation_id:
        return result
    return result + (
        DiagnosticEvent(
            event_type="missing_correlation_id",
            severity="warning",
            message="correlation id missing",
            metadata={"source": source},
            correlation=correlation,
            recorded_at=recorded_at,
        ),
    )


def _field_value(value: object, key: str) -> object:
    if isinstance(value, Mapping):
        return value.get(key)
    return getattr(value, key, None)


def _coerce_correlation(
    value: CorrelationMetadata | Mapping[str, object] | None,
) -> CorrelationMetadata:
    if isinstance(value, CorrelationMetadata):
        return value
    if isinstance(value, Mapping):
        return CorrelationMetadata(
            correlation_id=_optional_string(value.get("correlation_id")),
            request_id=_optional_string(value.get("request_id")),
            session_id=_optional_string(value.get("session_id")),
            trace_id=_optional_string(value.get("trace_id")),
            source=str(value.get("source", "runtime")),
        )
    return CorrelationMetadata()


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_bool(value: object, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y"}:
            return True
        if normalized in {"0", "false", "no", "n"}:
            return False
    return bool(value)


def _status_is_retryable(status_code: int | None) -> bool:
    return status_code is not None and status_code >= 500


def _phase_value(value: object) -> str:
    return getattr(value, "value", str(value))


def _reason_value(value: object) -> str:
    return getattr(value, "value", str(value))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "AdapterTraceSummary",
    "CorrelationMetadata",
    "DiagnosticEvent",
    "RuntimeErrorEvent",
    "TransitionLogEntry",
    "build_transition_log_entry",
    "classify_runtime_error",
    "redact_adapter_summary",
]
