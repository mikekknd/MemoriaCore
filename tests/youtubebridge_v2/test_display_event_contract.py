from __future__ import annotations

from datetime import datetime, timezone

from YouTubeBridgeV2.display.events import normalize_display_event, sanitize_display_value
from YouTubeBridgeV2.query_service import V2QueryService


NOW = datetime(2026, 5, 12, 8, 30, tzinfo=timezone.utc)


class FakeStorage:
    def __init__(self):
        self.sessions = {
            "session-1": {
                "session_id": "session-1",
                "current_phase": "planned_show",
                "aftertalk_policy": "auto",
                "plan_completed": False,
                "manual_close_requested": False,
                "closing_completed": False,
            }
        }
        self.events = []

    def get_v2_session(self, session_id):
        return self.sessions.get(session_id)

    def list_v2_live_events(self, _session_id, limit):
        return list(self.events[:limit])


def _assert_no_private_payload(value):
    text = repr(value).lower()
    for forbidden in (
        "hidden_prompt",
        "raw_prompt",
        "raw_payload",
        "raw_memoriacore_payload",
        "operator_controls",
        "operator_only",
        "manual_close",
        "access_token",
        "authorization",
        "client_secret",
        "refresh_token",
        "secret-value",
        "must not leak",
    ):
        assert forbidden not in text


def test_normalize_youtube_text_event_uses_display_event_contract():
    event = normalize_display_event(
        {
            "event_id": "yt-evt-1",
            "event_type": "youtube_text_message",
            "created_at": NOW,
            "public_metadata": {
                "public_payload": {
                    "message_text": "Hello runtime",
                    "author_display_name": "Mika",
                    "raw_payload": {"access_token": "must not leak"},
                },
                "display_event": {
                    "event_id": "yt-evt-1",
                    "event_type": "audience_message",
                    "author_display_name": "Mika",
                    "message_text": "Hello runtime",
                    "published_at": "2026-05-12T08:10:00Z",
                    "author_badges": ["moderator", "unknown_badge"],
                    "operator_controls": {"manual_close": True},
                },
            },
        }
    )

    assert event == {
        "display_contract_version": "v1",
        "event_id": "yt-evt-1",
        "event_type": "audience_message",
        "source_event_type": "youtube_text_message",
        "created_at": NOW.isoformat(),
        "public_payload": {
            "author_display_name": "Mika",
            "message_text": "Hello runtime",
            "timestamp": "2026-05-12T08:10:00Z",
            "display_flags": {"moderator": True},
        },
    }
    _assert_no_private_payload(event)


def test_normalize_youtube_super_chat_flattens_public_amount_metadata():
    event = normalize_display_event(
        {
            "event_id": "sc-1",
            "event_type": "youtube_super_chat",
            "public_metadata": {
                "display_event": {
                    "event_id": "sc-1",
                    "event_type": "super_chat",
                    "author_display_name": "Rin",
                    "message_text": "Great stream",
                    "published_at": "2026-05-12T08:20:00Z",
                    "author_badges": ["sponsor"],
                    "super_chat": {
                        "amount_display_string": "NT$150",
                        "currency": "TWD",
                        "acknowledgement_status": "pending",
                        "raw_payload": {"authorization": "Bearer secret-value"},
                    },
                }
            },
        }
    )

    assert event["event_type"] == "super_chat"
    assert event["public_payload"] == {
        "author_display_name": "Rin",
        "message_text": "Great stream",
        "timestamp": "2026-05-12T08:20:00Z",
        "display_flags": {"member": True},
        "amount_display_string": "NT$150",
        "currency": "TWD",
        "acknowledgement_status": "pending",
    }
    _assert_no_private_payload(event)


def test_normalize_runtime_event_becomes_system_state():
    event = normalize_display_event(
        {
            "event_id": "runtime-1",
            "event_type": "runtime_action_dispatched",
            "public_metadata": {
                "phase": "aftertalk",
                "payload": {"summary": {"message": "aftertalk started"}},
                "operator_controls": {"manual_close": True},
            },
        }
    )

    assert event == {
        "display_contract_version": "v1",
        "event_id": "runtime-1",
        "event_type": "system_state",
        "source_event_type": "runtime_action_dispatched",
        "public_payload": {
            "phase": "aftertalk",
            "message": "aftertalk started",
            "status": "runtime_action_dispatched",
        },
    }
    _assert_no_private_payload(event)


def test_query_service_display_stream_yields_normalized_display_events():
    storage = FakeStorage()
    storage.events.append(
        {
            "event_id": "yt-evt-1",
            "event_type": "youtube_text_message",
            "public_metadata": {
                "display_event": {
                    "event_id": "yt-evt-1",
                    "event_type": "audience_message",
                    "author_display_name": "Mika",
                    "message_text": "Hello display",
                },
            },
        }
    )

    events = list(V2QueryService(storage).iter_display_events("session-1"))

    assert events[0]["event_type"] == "audience_message"
    assert events[0]["public_payload"]["message_text"] == "Hello display"
    assert events[0]["source_event_type"] == "youtube_text_message"


def test_sanitize_display_value_removes_nested_private_key_patterns():
    sanitized = sanitize_display_value(
        {
            "safe": "visible",
            "client_secret": "must not leak",
            "nested": {
                "refresh_token": "must not leak",
                "operator_only_metadata": {"manual_close": True},
                "text": "Bearer secret-value",
            },
        }
    )

    assert sanitized == {"safe": "visible", "nested": {"text": "[redacted]"}}
    _assert_no_private_payload(sanitized)
