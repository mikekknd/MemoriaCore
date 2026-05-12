import inspect
from dataclasses import asdict

import YouTubeBridgeV2.adapters.youtube as youtube_adapter
from YouTubeBridgeV2.adapters.youtube import (
    NormalizedYouTubeEvent,
    SuperChatMetadata,
    YouTubeAdapterError,
    YouTubePollingCursor,
    YouTubeStreamStatus,
    classify_youtube_error,
    extract_super_chat_metadata,
    normalize_youtube_event,
)


def _text_event(**overrides):
    event = {
        "id": "evt-1",
        "snippet": {
            "type": "textMessageEvent",
            "publishedAt": "2026-05-12T08:00:00Z",
            "displayMessage": "Hello host",
            "textMessageDetails": {"messageText": "Hello host"},
            "authorChannelId": "channel-1",
            "rawTopicPack": {"hidden_prompt": "must not leak"},
        },
        "authorDetails": {
            "displayName": "Mika",
            "channelId": "channel-1",
            "profileImageUrl": "https://example.invalid/avatar.png",
            "isChatOwner": False,
            "isChatModerator": True,
            "isChatSponsor": False,
        },
        "raw_payload": {"access_token": "secret-value"},
    }
    event.update(overrides)
    return event


def _super_chat_event(**overrides):
    event = {
        "id": "sc-1",
        "snippet": {
            "type": "superChatEvent",
            "publishedAt": "2026-05-12T08:05:00Z",
            "displayMessage": "Great stream",
            "authorChannelId": "channel-2",
            "superChatDetails": {
                "amountMicros": 150000000,
                "currency": "TWD",
                "amountDisplayString": "NT$150",
                "userComment": "Great stream",
                "tier": 3,
                "raw_payload": {"secret": "must not leak"},
            },
        },
        "authorDetails": {
            "displayName": "Rin",
            "channelId": "channel-2",
            "isChatOwner": False,
            "isChatModerator": False,
            "isChatSponsor": True,
        },
    }
    event.update(overrides)
    return event


def _assert_no_private_payload(value):
    text = repr(value).lower()
    for forbidden in (
        "hidden_prompt",
        "rawtopicpack",
        "raw_topic_pack",
        "raw_payload",
        "access_token",
        "authorization",
        "secret-value",
        "must not leak",
        "closing_script",
    ):
        assert forbidden not in text


def test_normalize_text_message_event():
    event = normalize_youtube_event(_text_event())

    assert isinstance(event, NormalizedYouTubeEvent)
    assert event.event_id == "evt-1"
    assert event.event_type == "text_message"
    assert event.author_channel_id == "channel-1"
    assert event.author_display_name == "Mika"
    assert event.message_text == "Hello host"
    assert event.published_at == "2026-05-12T08:00:00Z"
    assert event.super_chat is None
    assert event.duplicate is False
    assert event.should_dispatch is True
    assert event.public_payload["message_text"] == "Hello host"
    assert event.public_payload["author_badges"] == ["moderator"]
    assert event.display_event == {
        "event_id": "evt-1",
        "event_type": "audience_message",
        "author_display_name": "Mika",
        "message_text": "Hello host",
        "published_at": "2026-05-12T08:00:00Z",
        "author_badges": ["moderator"],
        "duplicate": False,
        "should_dispatch": True,
    }
    _assert_no_private_payload(event)


def test_normalize_super_chat_event_with_metadata():
    event = normalize_youtube_event(_super_chat_event())

    assert event.event_type == "super_chat"
    assert event.message_text == "Great stream"
    assert event.super_chat == SuperChatMetadata(
        super_chat_id="sc-1",
        amount_micros=150000000,
        currency="TWD",
        amount_display_string="NT$150",
        tier=3,
        public_message="Great stream",
        acknowledgement_status="pending",
    )
    assert event.display_event["event_type"] == "super_chat"
    assert event.display_event["super_chat"]["amount_display_string"] == "NT$150"
    assert event.display_event["super_chat"]["acknowledgement_status"] == "pending"
    _assert_no_private_payload(event)


def test_extract_super_chat_metadata_returns_none_for_regular_chat():
    assert extract_super_chat_metadata(_text_event()) is None


def test_pagination_cursor_is_preserved_and_advanced_immutably():
    cursor = YouTubePollingCursor(
        live_chat_id="live-chat-1",
        next_page_token="page-1",
        polling_interval_millis=1500,
        seen_event_ids=("evt-0",),
    )

    updated = cursor.advance(
        next_page_token="page-2",
        polling_interval_millis=2500,
        seen_event_ids=("evt-0", "evt-1", "evt-1"),
    )

    assert cursor.next_page_token == "page-1"
    assert cursor.seen_event_ids == ("evt-0",)
    assert updated.live_chat_id == "live-chat-1"
    assert updated.next_page_token == "page-2"
    assert updated.polling_interval_millis == 2500
    assert updated.seen_event_ids == ("evt-0", "evt-1")
    assert updated.has_seen("evt-1") is True
    assert updated.has_seen("missing") is False


def test_pagination_cursor_advance_preserves_metadata_when_only_seen_ids_change():
    cursor = YouTubePollingCursor(
        live_chat_id="live-chat-1",
        next_page_token="page-1",
        polling_interval_millis=1500,
        seen_event_ids=("evt-0",),
    )

    updated = cursor.advance(seen_event_ids=("evt-1",))

    assert updated.next_page_token == "page-1"
    assert updated.polling_interval_millis == 1500
    assert updated.seen_event_ids == ("evt-0", "evt-1")


def test_duplicate_event_id_is_detected_and_not_dispatchable():
    cursor = YouTubePollingCursor(
        live_chat_id="live-chat-1",
        next_page_token="page-1",
        seen_event_ids=("evt-1",),
    )

    event = normalize_youtube_event(_text_event(), cursor=cursor)

    assert event.duplicate is True
    assert event.should_dispatch is False
    assert event.display_event["duplicate"] is True
    assert event.display_event["should_dispatch"] is False


def test_live_ended_state_returns_stream_status():
    status = YouTubeStreamStatus.from_raw(
        {
            "id": "video-1",
            "liveStreamingDetails": {
                "activeLiveChatId": None,
                "actualEndTime": "2026-05-12T09:00:00Z",
            },
            "status": {"lifeCycleStatus": "complete"},
        }
    )

    assert status.status == "ended"
    assert status.is_live is False
    assert status.live_chat_id is None
    assert status.video_id == "video-1"
    assert status.ended_at == "2026-05-12T09:00:00Z"
    assert status.public_summary == {
        "status": "ended",
        "is_live": False,
        "video_id": "video-1",
    }
    _assert_no_private_payload(status.public_summary)


class FakeYouTubeError(Exception):
    def __init__(self, message, *, status_code=None, retry_after=None, retryable=None):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after
        if retryable is not None:
            self.retryable = retryable


def test_transient_api_error_is_retryable():
    error = classify_youtube_error(
        FakeYouTubeError("backend unavailable with bearer secret", status_code=503, retry_after=10)
    )

    assert isinstance(error, YouTubeAdapterError)
    assert error.error_type == "transient_api_error"
    assert error.retryable is True
    assert error.status_code == 503
    assert error.backoff_hint_seconds == 10
    assert error.public_summary == {
        "error_type": "transient_api_error",
        "retryable": True,
        "status_code": 503,
        "backoff_hint_seconds": 10,
    }
    _assert_no_private_payload(error.public_summary)


def test_rate_limit_error_is_retryable_with_backoff():
    error = classify_youtube_error(
        FakeYouTubeError("quota exceeded", status_code=429, retry_after=30)
    )

    assert error.error_type == "rate_limited"
    assert error.retryable is True
    assert error.status_code == 429
    assert error.backoff_hint_seconds == 30
    assert error.public_summary == {
        "error_type": "rate_limited",
        "retryable": True,
        "status_code": 429,
        "backoff_hint_seconds": 30,
    }
    _assert_no_private_payload(error.public_summary)


def test_auth_error_is_terminal():
    error = classify_youtube_error(FakeYouTubeError("invalid api key secret", status_code=401))

    assert error.error_type == "auth_failure"
    assert error.retryable is False
    assert error.status_code == 401
    assert error.public_summary == {
        "error_type": "auth_failure",
        "retryable": False,
        "status_code": 401,
    }
    _assert_no_private_payload(error.public_summary)


def test_normalized_event_excludes_raw_youtube_payload():
    event = normalize_youtube_event(_text_event())

    payload = asdict(event)

    assert "raw_event" not in payload
    assert "raw_payload" not in payload
    _assert_no_private_payload(payload)


def test_adapter_does_not_emit_phase_transition_or_cross_boundary_side_effects():
    source = inspect.getsource(youtube_adapter)

    for forbidden in (
        "advance_phase",
        "RuntimePhase",
        "StorageManager",
        "sqlite3",
        "aiosqlite",
        "MemoriaClient",
        "closing_script",
        "next_phase",
    ):
        assert forbidden not in source

    event = normalize_youtube_event(_text_event())
    assert not hasattr(event, "next_phase")
    assert "closing_script" not in event.display_event
