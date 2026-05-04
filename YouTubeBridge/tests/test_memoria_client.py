import sys
from pathlib import Path


BRIDGE_ROOT = Path(__file__).resolve().parents[1]
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))

from memoria_client import MemoriaClient


class _FakeResponse:
    status_code = 200
    text = "{}"

    def json(self):
        return {"session_id": "live-public-session", "reply": "ok"}


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
