import sys
import threading
from pathlib import Path

import pytest

BRIDGE_ROOT = Path(__file__).resolve().parents[1]
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))

from memoria_client import GenerationInterrupted, MemoriaClient


class _FakeResponse:
    status_code = 200
    text = "{}"

    def json(self):
        return {"session_id": "live-public-session", "reply": "ok"}


class _FakeStreamResponse:
    status_code = 200
    text = ""

    def __init__(self):
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        return False

    def close(self):
        self.closed = True

    def iter_lines(self, decode_unicode=False):
        lines = [
            'data: {"type": "result", "session_id": "mem-a", "message_id": 1, "reply": "可可", "character_id": "char-a"}',
            'data: {"type": "result", "session_id": "mem-a", "message_id": 2, "reply": "白蓮", "character_id": "char-b"}',
        ]
        return lines if decode_unicode else [line.encode("utf-8") for line in lines]


class _FakeSession:
    def __init__(self):
        self.payload = None
        self.url = ""
        self.cookies = {}

    def post(self, _url, *, json=None, headers=None, timeout=None, stream=False):
        self.url = _url
        self.payload = json
        return _FakeResponse()


def test_youtube_external_context_payload_uses_public_live_scope():
    client = MemoriaClient(base_url="http://memoria.test/api/v1", admin_bypass=True)
    fake_session = _FakeSession()
    client.session = fake_session
    client.ensure_auth = lambda: None

    result = client.chat_sync(
        content="請回應直播留言",
        display_content="觀眾: hi",
        session_id="private-session-from-ui",
        character_ids=["char-a", "char-b"],
        external_context={
            "source": "youtube_live",
            "source_session_id": "yt-live-a",
            "context_text": "觀眾: hi",
            "summary": {"source_session_id": "yt-live-a"},
        },
    )

    assert result["session_id"] == "live-public-session"
    assert fake_session.payload["channel"] == "youtube_live"
    assert fake_session.payload["channel_uid"] == "yt-live-a"
    assert fake_session.payload["user_id"] == "__youtube_live__"
    assert fake_session.payload["channel_class"] == "public"
    assert fake_session.payload["persona_face"] == "public"
    assert fake_session.payload["memory_write_policy"] == "transient"


def test_add_system_event_posts_to_session_endpoint():
    client = MemoriaClient(base_url="http://memoria.test/api/v1", admin_bypass=True)
    fake_session = _FakeSession()
    client.session = fake_session
    client.ensure_auth = lambda: None

    client.add_system_event(
        session_id="mem-a",
        content="感謝本場 Super Chat 支持。",
        debug_info={"event_type": "youtube_live_closing_super_chat_fallback"},
    )

    assert fake_session.url == "http://memoria.test/api/v1/session/mem-a/system-event"
    assert fake_session.payload == {
        "content": "感謝本場 Super Chat 支持。",
        "debug_info": {"event_type": "youtube_live_closing_super_chat_fallback"},
    }


def test_chat_stream_sync_calls_on_result_for_each_stream_result():
    client = MemoriaClient(base_url="http://memoria.test/api/v1", admin_bypass=True)
    fake_session = _FakeSession()
    fake_session.post = lambda *_args, **_kwargs: _FakeStreamResponse()
    client.session = fake_session
    client.ensure_auth = lambda: None
    streamed = []

    result = client.chat_stream_sync(
        content="直播提示",
        session_id="mem-a",
        character_ids=["char-a", "char-b"],
        external_context={"source": "youtube_live_director", "source_session_id": "yt-a"},
        on_result=streamed.append,
    )

    assert result["message_id"] == 2
    assert [event["character_id"] for event in streamed] == ["char-a", "char-b"]


class _CancelBeforeResultResponse(_FakeStreamResponse):
    def __init__(self, cancel_event):
        super().__init__()
        self.cancel_event = cancel_event
        self.status_code = 200
        self.text = ""

    def iter_lines(self, decode_unicode=False):
        line = _CancelOnPayloadStrip(
            'data: {"type": "result", "session_id": "mem-a", "message_id": 3, "reply": "過期", "character_id": "char-b"}',
            self.cancel_event,
        )
        return [line] if decode_unicode else [line.encode("utf-8")]


class _CancelOnPayloadStrip(str):
    def __new__(cls, value, cancel_event):
        instance = str.__new__(cls, value)
        instance.cancel_event = cancel_event
        return instance

    def __getitem__(self, item):
        value = super().__getitem__(item)
        if isinstance(item, slice) and item.start == 5:
            return _CancelOnStripResult(value, self.cancel_event)
        return value


class _CancelOnStripResult(str):
    def __new__(cls, value, cancel_event):
        instance = str.__new__(cls, value)
        instance.cancel_event = cancel_event
        return instance

    def strip(self, chars=None):
        self.cancel_event.set()
        return super().strip(chars)


def test_chat_stream_sync_does_not_dispatch_result_after_cancel():
    cancel_event = threading.Event()
    client = MemoriaClient(base_url="http://memoria.test/api/v1", admin_bypass=True)
    fake_session = _FakeSession()
    fake_session.post = lambda *_args, **_kwargs: _CancelBeforeResultResponse(cancel_event)
    client.session = fake_session
    client.ensure_auth = lambda: None
    streamed = []

    with pytest.raises(GenerationInterrupted):
        client.chat_stream_sync(
            content="直播提示",
            session_id="mem-a",
            character_ids=["char-a", "char-b"],
            external_context={"source": "youtube_live_director", "source_session_id": "yt-a"},
            cancel_event=cancel_event,
            on_result=streamed.append,
        )

    assert streamed == []
