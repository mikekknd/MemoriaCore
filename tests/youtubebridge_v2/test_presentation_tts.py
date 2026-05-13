from dataclasses import asdict
from datetime import datetime, timezone
import ast
from pathlib import Path

import YouTubeBridgeV2.presentation.tts as presentation_tts
import YouTubeBridgeV2.presentation as presentation
from YouTubeBridgeV2.presentation.tts import (
    DeliveryAck,
    DeliveryTimeoutResult,
    PresentationDisplayMetadata,
    PresentationEvent,
    TTSRequest,
    build_presentation_event,
    enqueue_tts_request,
    record_delivery_ack,
    record_delivery_timeout,
)


COMPLETED_AT = datetime(2026, 5, 12, 9, 0, tzinfo=timezone.utc)
ACKED_AT = datetime(2026, 5, 12, 9, 1, tzinfo=timezone.utc)


def _interaction(**overrides):
    base = {
        "interaction_id": "interaction-1",
        "session_id": "session-1",
        "event_id": "response-1",
        "status": "completed",
        "character_id": "host",
        "character_name": "Luna",
        "role_label": "Host",
        "response_text": "Welcome back to the planned show.",
        "completed_at": COMPLETED_AT,
        "phase": "planned_show",
        "voice_id": "voice-luna",
        "presentation": {
            "voice_state": "ready",
            "visual_state": "focus",
            "subtitle": "Welcome back",
            "hidden_prompt": "must not leak",
        },
        "metadata": {
            "correlation_id": "corr-1",
            "raw_payload": {"authorization": "Bearer secret"},
        },
        "raw_adapter_payload": {"token": "must not leak"},
    }
    base.update(overrides)
    return base


def _assert_no_private_payload(value):
    text = repr(value).lower()
    for forbidden in (
        "hidden_prompt",
        "raw_prompt",
        "raw_payload",
        "raw_adapter_payload",
        "raw_memoriacore_payload",
        "authorization",
        "bearer secret",
        "access_token",
        "api_key",
        "client_secret",
        "secret",
        "refresh_token",
        "operator_only_metadata",
        "manual_close",
        "youtube_raw",
    ):
        assert forbidden not in text


def test_completed_character_response_builds_presentation_event():
    event = build_presentation_event(_interaction())

    assert isinstance(event, PresentationEvent)
    assert event.event_id == "response-1"
    assert event.interaction_id == "interaction-1"
    assert event.session_id == "session-1"
    assert event.character_id == "host"
    assert event.character_name == "Luna"
    assert event.role_label == "Host"
    assert event.response_text == "Welcome back to the planned show."
    assert event.completed_at == COMPLETED_AT
    assert event.should_present is True
    assert event.skip_reason is None
    assert event.display_metadata == PresentationDisplayMetadata(
        voice_state="ready",
        visual_state="focus",
        phase="planned_show",
        role_label="Host",
        subtitle="Welcome back",
        public_payload={"correlation_id": "corr-1"},
    )
    assert event.display_event == {
        "event_type": "character_response",
        "event_id": "response-1",
        "session_id": "session-1",
        "character_name": "Luna",
        "role_label": "Host",
        "response_text": "Welcome back to the planned show.",
        "phase": "planned_show",
        "presentation": {
            "voice_state": "ready",
            "visual_state": "focus",
            "phase": "planned_show",
            "role_label": "Host",
            "subtitle": "Welcome back",
            "public_payload": {"correlation_id": "corr-1"},
        },
    }
    _assert_no_private_payload(event)


def test_tts_enabled_enqueues_tts_request():
    event = build_presentation_event(_interaction())
    queue: list[TTSRequest] = []

    request = enqueue_tts_request(
        event,
        {"enabled": True, "provider": "local", "default_voice_id": "fallback-voice"},
        queue=queue,
    )

    assert isinstance(request, TTSRequest)
    assert request.delivery_id == "tts-response-1"
    assert request.event_id == "response-1"
    assert request.session_id == "session-1"
    assert request.character_id == "host"
    assert request.text == "Welcome back to the planned show."
    assert request.voice_id == "voice-luna"
    assert request.provider == "local"
    assert request.queue_position == 1
    assert request.status == "pending"
    assert queue == [request]
    _assert_no_private_payload(request)


def test_tts_disabled_keeps_display_metadata_without_request():
    event = build_presentation_event(_interaction())
    queue: list[TTSRequest] = []

    request = enqueue_tts_request(event, {"enabled": False}, queue=queue)

    assert request is None
    assert queue == []
    assert event.display_metadata.voice_state == "ready"
    assert event.display_event["presentation"]["visual_state"] == "focus"


def test_queue_preserves_event_order():
    queue: list[TTSRequest] = []
    first = build_presentation_event(_interaction(event_id="response-1", interaction_id="i-1"))
    second = build_presentation_event(
        _interaction(event_id="response-2", interaction_id="i-2", response_text="Second line")
    )

    first_request = enqueue_tts_request(first, {"enabled": True}, queue=queue)
    second_request = enqueue_tts_request(second, {"enabled": True}, queue=queue)

    assert [item.event_id for item in queue] == ["response-1", "response-2"]
    assert first_request.queue_position == 1
    assert second_request.queue_position == 2
    assert [item.delivery_id for item in queue] == ["tts-response-1", "tts-response-2"]


def test_delivery_ack_marks_success():
    state: dict[str, dict[str, object]] = {}

    ack = record_delivery_ack("tts-response-1", delivery_state=state, acknowledged_at=ACKED_AT)
    duplicate = record_delivery_ack("tts-response-1", delivery_state=state, acknowledged_at=ACKED_AT)

    assert isinstance(ack, DeliveryAck)
    assert ack.delivery_id == "tts-response-1"
    assert ack.status == "delivered"
    assert ack.acknowledged_at == ACKED_AT
    assert ack.duplicate is False
    assert ack.public_summary == {"delivery_id": "tts-response-1", "status": "delivered"}
    assert duplicate.duplicate is True
    assert state["tts-response-1"]["status"] == "delivered"
    _assert_no_private_payload(ack)


def test_delivery_timeout_marks_timeout_without_phase_change():
    state: dict[str, dict[str, object]] = {}

    result = record_delivery_timeout(
        "tts-response-1",
        timeout_seconds=30,
        delivery_state=state,
        metadata={
            "correlation_id": "corr-1",
            "raw_payload": {"token": "must not leak"},
        },
    )

    assert isinstance(result, DeliveryTimeoutResult)
    assert result.delivery_id == "tts-response-1"
    assert result.status == "timeout"
    assert result.timeout_seconds == 30
    assert result.phase_transition_requested is False
    assert not hasattr(result, "next_phase")
    assert state["tts-response-1"]["status"] == "timeout"
    _assert_no_private_payload(result)


def test_malformed_event_is_skipped_safely():
    event = build_presentation_event(
        {
            "interaction_id": "interaction-bad",
            "session_id": "session-1",
            "event_id": "response-bad",
            "status": "completed",
            "character_name": "Luna",
            "metadata": {"raw_payload": {"authorization": "Bearer secret"}},
        }
    )

    assert isinstance(event, PresentationEvent)
    assert event.should_present is False
    assert event.skip_reason == "missing_response_text"
    assert event.response_text == ""
    assert event.display_event["event_type"] == "presentation_skipped"
    assert event.display_event["reason"] == "missing_response_text"
    assert enqueue_tts_request(event, {"enabled": True}, queue=[]) is None
    _assert_no_private_payload(event)


def test_non_completed_interaction_is_skipped_safely():
    event = build_presentation_event(_interaction(status="pending"))

    assert event.should_present is False
    assert event.skip_reason == "interaction_not_completed"
    assert event.response_text == ""
    assert event.display_event["event_type"] == "presentation_skipped"
    assert event.display_event["reason"] == "interaction_not_completed"
    assert enqueue_tts_request(event, {"enabled": True}, queue=[]) is None


def test_missing_identity_event_is_skipped_safely():
    event = build_presentation_event(
        _interaction(event_id="", interaction_id="", session_id="")
    )

    assert event.should_present is False
    assert event.skip_reason == "missing_identity"
    assert event.response_text == ""
    assert event.display_event["event_type"] == "presentation_skipped"
    assert event.display_event["reason"] == "missing_identity"
    assert enqueue_tts_request(event, {"enabled": True}, queue=[]) is None


def test_timeout_after_ack_does_not_overwrite_delivered_state():
    state: dict[str, dict[str, object]] = {}
    record_delivery_ack("tts-response-1", delivery_state=state, acknowledged_at=ACKED_AT)

    result = record_delivery_timeout(
        "tts-response-1",
        timeout_seconds=30,
        delivery_state=state,
        metadata={"correlation_id": "corr-1"},
    )

    assert result.status == "delivered"
    assert result.public_summary == {
        "delivery_id": "tts-response-1",
        "status": "delivered",
        "timeout_seconds": 30,
        "timeout_ignored": True,
        "reason": "already_delivered",
    }
    assert state["tts-response-1"]["status"] == "delivered"


def test_public_metadata_redacts_secret_key_patterns():
    result = record_delivery_timeout(
        "tts-response-1",
        timeout_seconds=30,
        metadata={
            "safe": "visible",
            "refresh_token": "must not leak",
            "client_secret": "must not leak",
            "nested": {
                "api_key": "must not leak",
                "authorization_header": "Bearer secret",
            },
        },
    )

    assert result.metadata == {"safe": "visible", "nested": {}}
    _assert_no_private_payload(result)


def test_display_metadata_excludes_hidden_prompt_and_raw_payload():
    event = build_presentation_event(
        _interaction(
            presentation={
                "voice_state": "speaking",
                "visual_state": "focus",
                "raw_memoriacore_payload": {"secret": "must not leak"},
            },
            metadata={
                "correlation_id": "corr-1",
                "operator_only_metadata": {"manual_close": True},
                "raw_payload": {"access_token": "secret"},
            },
        )
    )

    payload = asdict(event)

    assert payload["display_metadata"]["public_payload"] == {"correlation_id": "corr-1"}
    _assert_no_private_payload(payload)


def test_presentation_tts_does_not_cross_runtime_or_external_boundaries():
    source = Path(presentation_tts.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules: set[str] = set()
    referenced_names: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)
        elif isinstance(node, ast.Name):
            referenced_names.add(node.id)
        elif isinstance(node, ast.Attribute):
            referenced_names.add(node.attr)

    for forbidden_module in (
        "sqlite3",
        "aiosqlite",
        "YouTubeBridgeV2.adapters.youtube",
    ):
        assert all(
            module != forbidden_module and not module.startswith(f"{forbidden_module}.")
            for module in imported_modules
        )

    for forbidden in (
        "advance_phase",
        "RuntimePhase",
        "StorageManager",
        "operator_console",
        "operator_control",
        "manual_close",
    ):
        assert forbidden not in referenced_names


def test_presentation_package_reexports_public_tts_contracts():
    for symbol in presentation_tts.__all__:
        assert getattr(presentation, symbol) is getattr(presentation_tts, symbol)
