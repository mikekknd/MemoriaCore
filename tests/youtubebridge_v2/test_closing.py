from datetime import datetime, timezone
import inspect

from YouTubeBridgeV2.runtime.closing import (
    ClosingCompletionStatus,
    ClosingDisplayEvent,
    ClosingFinalizationResult,
    ClosingPolicy,
    ClosingReason,
    ClosingRequest,
    ClosingStartContext,
    ClosingSuperChatAction,
    build_closing_request,
    finalize_closing,
)
from YouTubeBridgeV2.runtime.phase import (
    AftertalkPolicy,
    DurationPolicy,
    LiveSessionPhase,
    LiveSessionSnapshot,
    PhaseTransitionReason,
    advance_phase,
)


PHASE_ENTERED_AT = datetime(2026, 5, 12, 8, 30, tzinfo=timezone.utc)
COMPLETED_AT = datetime(2026, 5, 12, 8, 35, tzinfo=timezone.utc)


def _context(**overrides):
    base = {
        "session_id": "session-1",
        "closing_reason": ClosingReason.MANUAL_CLOSE,
        "phase_entered_at": PHASE_ENTERED_AT,
        "duration_summary": {
            "duration_reached": False,
            "remaining_time_seconds": 300,
        },
        "manual_close_requested": True,
        "correlation_id": "corr-1",
        "completed_at": COMPLETED_AT,
    }
    base.update(overrides)
    return ClosingStartContext(**base)


def _summary(**overrides):
    base = {
        "title": "V2 runtime episode",
        "public_recap": {
            "safe": "visible",
            "hidden_prompt": "must not leak",
            "raw_memoriacore_payload": {"token": "secret"},
        },
        "completed_turn_count": 4,
        "raw_topic_pack": "must not leak",
    }
    base.update(overrides)
    return base


def _policy(**overrides):
    base = {
        "final_message_enabled": True,
        "acknowledge_super_chats": True,
        "terminal_error_allows_system_summary": True,
        "visibility": "public",
    }
    base.update(overrides)
    return ClosingPolicy(**base)


def _super_chats():
    return [
        {
            "id": "sc-1",
            "author_display_name": "Alice",
            "amount_display_string": "NT$150",
            "message_text": "Great stream",
            "raw_payload": {"youtube_raw": "must not leak"},
        },
        {
            "event_id": "sc-2",
            "author": "Bob",
            "amount": "$5.00",
            "text": "Thanks",
            "hidden_prompt": "must not leak",
        },
    ]


def _assert_no_private_payload(value):
    text = repr(value).lower()
    for forbidden in (
        "hidden_prompt",
        "raw_prompt",
        "raw_topic_pack",
        "raw_payload",
        "raw_memoriacore_payload",
        "raw_super_chat",
        "raw_super_chat_payload",
        "youtube_raw",
        "access_token",
        "secret",
        "legacy director",
    ):
        assert forbidden not in text


def test_manual_close_context_builds_closing_request():
    request = build_closing_request(_context(), _summary(), [], _policy())

    assert isinstance(request, ClosingRequest)
    assert request.session_id == "session-1"
    assert request.closing_reason == ClosingReason.MANUAL_CLOSE
    assert request.should_dispatch is True
    assert request.adapter_intent == "memoriacore_closing_message"
    assert request.visibility == "public"
    assert request.summary == {
        "title": "V2 runtime episode",
        "public_recap": {"safe": "visible"},
        "completed_turn_count": 4,
    }
    _assert_no_private_payload(request)


def test_duration_reached_context_builds_closing_request():
    request = build_closing_request(
        _context(
            closing_reason=ClosingReason.DURATION_REACHED,
            duration_summary={"duration_reached": True, "remaining_time_seconds": 0},
            manual_close_requested=False,
        ),
        _summary(),
        [],
        _policy(),
    )

    assert request.closing_reason == ClosingReason.DURATION_REACHED
    assert request.metadata["duration_summary"] == {
        "duration_reached": True,
        "remaining_time_seconds": 0,
    }


def test_stream_ended_context_builds_closing_request():
    request = build_closing_request(
        _context(
            closing_reason=ClosingReason.STREAM_ENDED,
            manual_close_requested=False,
        ),
        _summary(),
        [],
        _policy(),
    )

    assert request.closing_reason == ClosingReason.STREAM_ENDED
    assert request.metadata["manual_close_requested"] is False


def test_pending_super_chats_create_acknowledgement_actions():
    request = build_closing_request(_context(), _summary(), _super_chats(), _policy())

    assert request.super_chat_actions == (
        ClosingSuperChatAction(
            super_chat_id="sc-1",
            action_type="acknowledge",
            status="pending",
            author_display_name="Alice",
            amount_display_string="NT$150",
            public_message="Great stream",
            error_summary={},
        ),
        ClosingSuperChatAction(
            super_chat_id="sc-2",
            action_type="acknowledge",
            status="pending",
            author_display_name="Bob",
            amount_display_string="$5.00",
            public_message="Thanks",
            error_summary={},
        ),
    )
    _assert_no_private_payload(request.super_chat_actions)


def test_duplicate_pending_super_chat_id_is_acknowledged_once():
    request = build_closing_request(
        _context(),
        _summary(),
        [
            {
                "id": "sc-1",
                "author_display_name": "Alice",
                "amount_display_string": "NT$150",
                "message_text": "First copy",
            },
            {
                "super_chat_id": "sc-1",
                "author_display_name": "Alice",
                "amount_display_string": "NT$150",
                "message_text": "Duplicate copy",
            },
        ],
        _policy(),
    )

    assert [action.super_chat_id for action in request.super_chat_actions] == ["sc-1"]
    assert request.super_chat_actions[0].public_message == "First copy"


def test_malformed_super_chat_is_skipped_with_redacted_error():
    request = build_closing_request(
        _context(),
        _summary(),
        [
            {"raw_payload": {"token": "must not leak"}},
            {"id": "sc-ok", "author_display_name": "Alice", "message_text": "safe"},
        ],
        _policy(),
    )

    assert request.super_chat_actions[0].action_type == "skipped"
    assert request.super_chat_actions[0].status == "invalid"
    assert request.super_chat_actions[0].error_summary == {
        "error_type": "invalid_super_chat",
        "reason": "missing_id_or_author",
    }
    assert request.super_chat_actions[1].action_type == "acknowledge"
    _assert_no_private_payload(request)


def test_final_message_disabled_allows_system_only_finalization():
    policy = _policy(final_message_enabled=False)
    request = build_closing_request(_context(), _summary(), [], policy)
    result = finalize_closing(_context(), None, policy)

    assert request.should_dispatch is False
    assert request.adapter_intent is None
    assert result.closing_completion_status == ClosingCompletionStatus.COMPLETE
    assert result.status == "complete"
    assert result.error_summary == {}
    assert result.display_event.status == "complete"


def test_memoria_timeout_returns_retryable_completion_status():
    result = finalize_closing(
        _context(),
        {
            "error_type": "timeout",
            "retryable": True,
            "raw_memoriacore_payload": {"token": "must not leak"},
        },
        _policy(),
    )

    assert result.closing_completion_status == ClosingCompletionStatus.FAILED_RETRYABLE
    assert result.status == "failed_retryable"
    assert result.error_summary == {"error_type": "timeout", "retryable": True}
    _assert_no_private_payload(result)


def test_terminal_memoria_error_can_finalize_with_system_summary():
    result = finalize_closing(
        _context(),
        {"error_type": "auth_failure", "retryable": False},
        _policy(terminal_error_allows_system_summary=True),
    )

    assert result.closing_completion_status == ClosingCompletionStatus.COMPLETE
    assert result.status == "complete"
    assert result.error_summary == {
        "error_type": "auth_failure",
        "retryable": False,
        "fallback": "system_summary",
    }


def test_duplicate_closing_command_is_idempotent():
    context = _context(
        acknowledged_super_chat_ids=("sc-1",),
        final_message_sent=True,
    )
    request = build_closing_request(context, _summary(), _super_chats(), _policy())

    assert request.should_dispatch is False
    assert request.adapter_intent is None
    assert [action.super_chat_id for action in request.super_chat_actions] == ["sc-2"]
    assert request.metadata["idempotent_skip"] == "final_message_already_sent"


def test_complete_finalization_status_moves_runtime_phase_to_ended():
    result = finalize_closing(
        _context(),
        {
            "status": "ok",
            "message_count": 1,
            "public_summary": {"message_count": 1},
        },
        _policy(),
    )
    transition = advance_phase(
        LiveSessionSnapshot(
            current_phase=LiveSessionPhase.CLOSING,
            session_started_at=PHASE_ENTERED_AT,
            plan_completed=True,
            aftertalk_policy=AftertalkPolicy.DISABLED,
            duration_policy=DurationPolicy(
                planned_duration_seconds=3600,
                auto_finalize_on_duration=True,
            ),
            closing_completed=result.closing_completion_status
            == ClosingCompletionStatus.COMPLETE,
        ),
        COMPLETED_AT,
    )

    assert isinstance(result, ClosingFinalizationResult)
    assert result.closing_completion_status == ClosingCompletionStatus.COMPLETE
    assert transition.next_phase == LiveSessionPhase.ENDED
    assert transition.reason == PhaseTransitionReason.CLOSING_COMPLETED


def test_closing_display_event_excludes_hidden_prompt_raw_super_chat_and_raw_memoria_payload():
    request = build_closing_request(_context(), _summary(), _super_chats(), _policy())
    result = finalize_closing(
        _context(),
        {
            "status": "ok",
            "public_summary": {
                "message_count": 1,
                "raw_memoriacore_payload": {"token": "must not leak"},
            },
            "hidden_prompt": "must not leak",
        },
        _policy(),
    )

    assert isinstance(result.display_event, ClosingDisplayEvent)
    assert result.display_event.event_type == "closing_status"
    assert result.display_event.session_id == "session-1"
    _assert_no_private_payload(request)
    _assert_no_private_payload(result.display_event)


def test_closing_public_surfaces_redact_raw_prompt_and_super_chat_payload_keys():
    request = build_closing_request(
        _context(),
        _summary(
            raw_prompt="must not leak",
            public_recap={
                "safe": "visible",
                "raw_super_chat_payload": {"token": "must not leak"},
            },
        ),
        [],
        _policy(),
    )
    result = finalize_closing(
        _context(),
        {
            "status": "ok",
            "public_summary": {
                "message_count": 1,
                "raw_prompt": "must not leak",
                "raw_super_chat": {"token": "must not leak"},
            },
        },
        _policy(),
    )

    assert "raw_prompt" not in request.summary
    assert "raw_super_chat_payload" not in request.summary["public_recap"]
    assert "raw_prompt" not in result.display_event.public_summary
    assert "raw_super_chat" not in result.display_event.public_summary
    _assert_no_private_payload(request)
    _assert_no_private_payload(result.display_event)


def test_closing_functions_have_no_transport_storage_ui_or_tts_dependency():
    assert list(inspect.signature(build_closing_request).parameters) == [
        "context",
        "summary",
        "pending_super_chats",
        "policy",
    ]
    assert list(inspect.signature(finalize_closing).parameters) == [
        "context",
        "adapter_result",
        "policy",
    ]
