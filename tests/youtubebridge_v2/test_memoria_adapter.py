import inspect

from YouTubeBridgeV2.adapters.memoria import (
    MemoriaAdapterError,
    MemoriaCorrelationMetadata,
    MemoriaRequestPayload,
    NormalizedMemoriaResponse,
    build_memoria_request,
    classify_memoria_error,
    normalize_memoria_response,
)
from YouTubeBridgeV2.live_episode_plan.runner import PlannedTurnIntent
from YouTubeBridgeV2.runtime.aftertalk import AftertalkCue, AftertalkTurnRequest


def _planned_turn_intent():
    return PlannedTurnIntent(
        plan_id="plan-1",
        turn_id="opening",
        turn_index=0,
        purpose="Open the planned show.",
        speaker_policy="fixed",
        speaker_ids=("host",),
        topic_cue="Why V2 needs explicit runtime boundaries.",
        audience_summary={
            "type": "message",
            "display_text": "Question from chat",
            "raw_payload": {"secret": "must not leak"},
        },
        audience_handling_hint="audience_summary_allowed",
        metadata={"hidden_prompt": "must not leak"},
    )


def _aftertalk_request():
    cue = AftertalkCue(
        session_id="session-1",
        public_show_summary={
            "title": "Runtime V2",
            "public_recap": {
                "safe": "visible",
                "hidden_prompt": "must not leak",
            },
        },
        speaker_rotation_hint=("host", "cohost"),
        metadata={"raw_memoriacore_payload": {"token": "secret"}},
    )
    return AftertalkTurnRequest(
        session_id="session-1",
        should_dispatch=True,
        group_chat_mode="aftertalk",
        adapter_intent="memoriacore_group_chat",
        cue=cue,
        stop_reason=None,
        metadata={"correlation_id": "corr-1"},
    )


def _context(**overrides):
    base = {
        "v2_session_id": "v2-session-1",
        "memoria_session_id": "memoria-session-1",
        "user_id": "__youtube_live__",
        "character_id": "host",
        "correlation_id": "corr-1",
        "request_id": "request-1",
        "auth": {"access_token": "secret-token"},
        "hidden_prompt": "must not leak",
        "raw_topic_pack": "must not leak",
    }
    base.update(overrides)
    return base


def _correlation():
    return MemoriaCorrelationMetadata(
        correlation_id="corr-1",
        request_id="request-1",
        v2_session_id="v2-session-1",
        memoria_session_id="memoria-session-1",
        trace_id="trace-1",
    )


def _assert_no_private_payload(value):
    text = repr(value).lower()
    for forbidden in (
        "hidden_prompt",
        "raw_payload",
        "raw_memoriacore_payload",
        "raw_topic_pack",
        "secret-token",
        "access_token",
        "memoriacore_raw",
    ):
        assert forbidden not in text


def test_planned_turn_intent_maps_to_memoria_chat_request():
    payload = build_memoria_request(_planned_turn_intent(), _context())

    assert isinstance(payload, MemoriaRequestPayload)
    assert payload.mode == "chat"
    assert payload.endpoint == "/api/v1/chat/sync"
    assert payload.body["session_id"] == "memoria-session-1"
    assert payload.body["user_id"] == "__youtube_live__"
    assert payload.body["channel"] == "youtube_live"
    assert payload.body["channel_uid"] == "v2-session-1"
    assert payload.body["channel_class"] == "public"
    assert payload.body["persona_face"] == "public"
    assert payload.body["character_ids"] == ["host"]
    assert payload.body["content"] == "Open the planned show."
    assert payload.body["include_speech"] is False
    assert payload.body["memory_write_policy"] == "transient"
    assert payload.body["external_context"]["source"] == "youtube_live_director"
    assert payload.body["external_context"]["summary"]["episode_plan_turn_id"] == "opening"
    assert payload.body["external_context"]["live_episode_plan"]["speaker_policy"] == "fixed"
    assert (
        payload.body["external_context"]["live_episode_plan"]["topic_cue"]
        == "Why V2 needs explicit runtime boundaries."
    )
    assert payload.public_summary == {
        "mode": "chat",
        "v2_session_id": "v2-session-1",
        "turn_id": "opening",
        "speaker_ids": ["host"],
        "correlation_id": "corr-1",
        "request_id": "request-1",
    }
    _assert_no_private_payload(payload)


def test_aftertalk_request_maps_to_group_chat_request():
    payload = build_memoria_request(_aftertalk_request(), _context())

    assert payload.mode == "group_chat"
    assert payload.endpoint == "/api/v1/chat/sync"
    assert payload.body["session_id"] == "memoria-session-1"
    assert payload.body["character_ids"] == ["host", "cohost"]
    assert payload.body["group_name"] == "aftertalk"
    assert payload.body["external_context"]["group_turn_limit"] == 2
    assert payload.body["external_context"]["aftertalk"]["group_chat_mode"] == "aftertalk"
    assert payload.body["external_context"]["aftertalk"]["cue"]["public_show_summary"] == {
        "title": "Runtime V2",
        "public_recap": {"safe": "visible"},
    }
    assert payload.public_summary["mode"] == "group_chat"
    assert payload.public_summary["speaker_count"] == 2
    assert payload.public_summary["request_id"] == "request-1"
    _assert_no_private_payload(payload)


def test_memoria_response_is_normalized_with_session_id():
    response = normalize_memoria_response(
        {
            "session_id": "memoria-session-2",
            "message_id": 1,
            "character_id": "host",
            "reply": "Hello from host",
            "trace_id": "trace-response",
            "raw_payload": {"secret": "must not leak"},
        },
        _correlation(),
    )

    assert isinstance(response, NormalizedMemoriaResponse)
    assert response.mode == "chat"
    assert response.memoria_session_id == "memoria-session-2"
    assert response.messages == (
        {
            "message_id": "1",
            "speaker_id": "host",
            "content": "Hello from host",
        },
    )
    assert response.correlation.trace_id == "trace-response"
    assert response.public_summary["correlation_id"] == "corr-1"
    assert response.public_summary["request_id"] == "request-1"
    assert response.public_summary["v2_session_id"] == "v2-session-1"
    _assert_no_private_payload(response.public_summary)


def test_group_chat_response_with_joined_reply_preserves_turns():
    response = normalize_memoria_response(
        {
            "session_id": "memoria-session-2",
            "message_id": 2,
            "character_id": "cohost",
            "reply": "Host: First reply\n\nCohost: Follow-up reply",
            "turns": [
                {
                    "message_id": 1,
                    "character_id": "host",
                    "reply": "First reply",
                    "turn_index": 0,
                    "is_final": False,
                },
                {
                    "message_id": 2,
                    "character_id": "cohost",
                    "reply": "Follow-up reply",
                    "turn_index": 1,
                    "is_final": True,
                },
            ],
            "trace_id": "trace-response",
        },
        _correlation(),
    )

    assert isinstance(response, NormalizedMemoriaResponse)
    assert response.mode == "group_chat"
    assert response.messages == (
        {
            "message_id": "1",
            "speaker_id": "host",
            "content": "First reply",
        },
        {
            "message_id": "2",
            "speaker_id": "cohost",
            "content": "Follow-up reply",
        },
    )
    assert response.public_summary["message_count"] == 2
    assert response.public_summary["correlation_id"] == "corr-1"


def test_group_chat_response_requires_speaker_metadata():
    error = normalize_memoria_response(
        {
            "session_id": "memoria-session-1",
            "turns": [{"reply": "Missing speaker metadata"}],
        },
        _correlation(),
    )

    assert isinstance(error, MemoriaAdapterError)
    assert error.error_type == "invalid_response"
    assert error.retryable is False
    assert "speaker" in error.public_summary["message"]


def test_aftertalk_request_requires_non_empty_speaker_rotation_hint():
    cue = AftertalkCue(
        session_id="session-1",
        public_show_summary={"title": "Runtime V2"},
        speaker_rotation_hint=(),
        metadata={},
    )
    request = AftertalkTurnRequest(
        session_id="session-1",
        should_dispatch=True,
        group_chat_mode="aftertalk",
        adapter_intent="memoriacore_group_chat",
        cue=cue,
        stop_reason=None,
        metadata={},
    )

    try:
        build_memoria_request(request, _context())
    except ValueError as exc:
        assert "speaker" in str(exc)
    else:
        raise AssertionError("expected empty aftertalk speaker list to fail")


def test_timeout_is_classified_as_retryable_adapter_error():
    error = classify_memoria_error(TimeoutError("request timed out"))

    assert isinstance(error, MemoriaAdapterError)
    assert error.error_type == "timeout"
    assert error.retryable is True
    assert error.public_summary == {"error_type": "timeout", "retryable": True}


class FakeTransportError(Exception):
    retryable = True
    status_code = 503


def test_transport_failure_is_classified_without_phase_change():
    error = classify_memoria_error(FakeTransportError("service unavailable"))

    assert error.error_type == "transport_failure"
    assert error.retryable is True
    assert error.public_summary == {
        "error_type": "transport_failure",
        "retryable": True,
        "status_code": 503,
    }
    assert not hasattr(error, "next_phase")


class FakeAuthError(Exception):
    status_code = 401


def test_auth_failure_is_classified_as_terminal():
    error = classify_memoria_error(FakeAuthError("unauthorized"))

    assert error.error_type == "auth_failure"
    assert error.retryable is False
    assert error.public_summary == {
        "error_type": "auth_failure",
        "retryable": False,
        "status_code": 401,
    }


def test_public_summary_excludes_hidden_prompt_and_raw_payload():
    payload = build_memoria_request(
        _planned_turn_intent(),
        _context(
            public_metadata={
                "safe": "visible",
                "public_recap": {"raw_payload": {"secret": "must not leak"}},
            }
        ),
    )
    response = normalize_memoria_response(
        {
            "session_id": "memoria-session-1",
            "turns": [
                {
                    "id": "m1",
                    "character_id": "host",
                    "reply": "safe",
                    "raw_memoriacore_payload": {"token": "secret"},
                }
            ],
            "summary": {
                "safe": "visible",
                "hidden_prompt": "must not leak",
            },
        },
        _correlation(),
    )

    _assert_no_private_payload(payload.public_summary)
    _assert_no_private_payload(response.public_summary)
    assert response.public_summary["summary"] == {"safe": "visible"}


def test_memoria_adapter_functions_have_no_transport_or_storage_dependency():
    assert list(inspect.signature(build_memoria_request).parameters) == [
        "intent",
        "context",
    ]
    assert list(inspect.signature(normalize_memoria_response).parameters) == [
        "response_payload",
        "correlation_metadata",
    ]
    assert list(inspect.signature(classify_memoria_error).parameters) == ["error"]
