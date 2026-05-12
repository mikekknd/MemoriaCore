import inspect

from YouTubeBridgeV2.runtime.aftertalk import (
    AftertalkCue,
    AftertalkSessionSummary,
    AftertalkStopReason,
    AftertalkTurnRequest,
    build_aftertalk_turn_request,
    summarize_aftertalk_result,
)
from YouTubeBridgeV2.runtime.phase import AftertalkPolicy


def _context(**overrides):
    base = {
        "session_id": "session-1",
        "aftertalk_policy": AftertalkPolicy.AUTO,
        "duration_reached": False,
        "remaining_time_seconds": 900,
        "manual_close_requested": False,
        "public_show_summary": {
            "title": "V2 runtime opening",
            "completed_turn_count": 3,
            "public_topics": ["phase flow", "planned show"],
            "hidden_prompt": "must not leak",
            "raw_topic_pack": "must not leak",
            "raw_memoriacore_payload": {"token": "secret"},
        },
        "speaker_rotation_hint": ["host", "cohost-a", "cohost-b"],
        "correlation_id": "corr-1",
        "legacy_director": "must not be used",
    }
    base.update(overrides)
    return base


def _assert_no_private_payload(value):
    text = repr(value).lower()
    for forbidden in (
        "hidden_prompt",
        "raw_topic_pack",
        "raw_factcard",
        "raw_payload",
        "raw_memoriacore_payload",
        "old director",
        "program_segment_plan",
    ):
        assert forbidden not in text


def test_aftertalk_auto_policy_builds_group_chat_request_when_duration_allows():
    request = build_aftertalk_turn_request(_context())

    assert isinstance(request, AftertalkTurnRequest)
    assert request.should_dispatch is True
    assert request.stop_reason is None
    assert isinstance(request.cue, AftertalkCue)
    assert request.cue.session_id == "session-1"
    assert request.cue.public_show_summary == {
        "title": "V2 runtime opening",
        "completed_turn_count": 3,
        "public_topics": ["phase flow", "planned show"],
    }


def test_aftertalk_disabled_policy_returns_disabled_stop_reason():
    request = build_aftertalk_turn_request(
        _context(aftertalk_policy=AftertalkPolicy.DISABLED)
    )

    assert request.should_dispatch is False
    assert request.cue is None
    assert request.stop_reason == AftertalkStopReason.DISABLED


def test_aftertalk_duration_reached_returns_duration_stop_reason():
    request = build_aftertalk_turn_request(
        _context(duration_reached=True, remaining_time_seconds=0)
    )

    assert request.should_dispatch is False
    assert request.cue is None
    assert request.stop_reason == AftertalkStopReason.DURATION_REACHED


def test_aftertalk_manual_close_returns_manual_stop_reason():
    request = build_aftertalk_turn_request(_context(manual_close_requested=True))

    assert request.should_dispatch is False
    assert request.cue is None
    assert request.stop_reason == AftertalkStopReason.MANUAL_CLOSE


def test_aftertalk_cue_uses_public_show_summary_only():
    request = build_aftertalk_turn_request(_context())

    _assert_no_private_payload(request)
    assert request.cue.public_show_summary == {
        "title": "V2 runtime opening",
        "completed_turn_count": 3,
        "public_topics": ["phase flow", "planned show"],
    }


def test_aftertalk_cue_recursively_redacts_public_show_summary_values():
    request = build_aftertalk_turn_request(
        _context(
            public_show_summary={
                "title": "Nested public summary",
                "public_recap": {
                    "safe": "visible",
                    "hidden_prompt": "must not leak",
                    "raw_memoriacore_payload": {"token": "secret"},
                },
                "public_topics": [
                    {
                        "label": "topic one",
                        "raw_payload": {"youtube_raw": "secret"},
                    }
                ],
            }
        )
    )

    assert request.cue.public_show_summary == {
        "title": "Nested public summary",
        "public_recap": {"safe": "visible"},
        "public_topics": [{"label": "topic one"}],
    }
    _assert_no_private_payload(request)


def test_aftertalk_request_contains_group_chat_mode():
    request = build_aftertalk_turn_request(_context())

    assert request.group_chat_mode == "aftertalk"
    assert request.adapter_intent == "memoriacore_group_chat"
    assert request.cue.speaker_rotation_hint == ("host", "cohost-a", "cohost-b")


def test_aftertalk_does_not_use_legacy_director():
    request = build_aftertalk_turn_request(
        _context(
            legacy_director={"enabled": True, "prompt": "old director"},
            program_segment_plan={"raw": "old plan"},
        )
    )

    assert request.should_dispatch is True
    assert request.metadata["legacy_director_used"] is False
    _assert_no_private_payload(request)


def test_aftertalk_has_no_memoria_transport_side_effect():
    signature = inspect.signature(build_aftertalk_turn_request)

    assert list(signature.parameters) == ["aftertalk_context"]

    request = build_aftertalk_turn_request(_context())

    assert request.should_dispatch is True
    assert request.adapter_intent == "memoriacore_group_chat"


def test_aftertalk_invalid_policy_returns_invalid_policy_stop_reason():
    invalid = build_aftertalk_turn_request(_context(aftertalk_policy="unsupported"))
    missing = build_aftertalk_turn_request(_context(aftertalk_policy=None))

    for request in (invalid, missing):
        assert request.should_dispatch is False
        assert request.cue is None
        assert request.stop_reason == AftertalkStopReason.INVALID_POLICY


def test_aftertalk_result_summary_redacts_raw_payload():
    summary = summarize_aftertalk_result(
        {
            "session_id": "session-1",
            "status": "adapter_error",
            "stop_reason": "adapter_error",
            "message_count": 2,
            "raw_memoriacore_payload": {"token": "secret"},
            "hidden_prompt": "must not leak",
        }
    )

    assert isinstance(summary, AftertalkSessionSummary)
    assert summary.session_id == "session-1"
    assert summary.status == "adapter_error"
    assert summary.stop_reason == AftertalkStopReason.ADAPTER_ERROR
    assert summary.public_summary == {"message_count": 2}
    _assert_no_private_payload(summary)


def test_aftertalk_result_summary_recursively_redacts_public_recap():
    summary = summarize_aftertalk_result(
        {
            "session_id": "session-1",
            "status": "ok",
            "public_recap": {
                "safe": "visible",
                "hidden_prompt": "must not leak",
                "raw_payload": {"token": "secret"},
            },
        }
    )

    assert summary.public_summary == {"public_recap": {"safe": "visible"}}
    _assert_no_private_payload(summary)
