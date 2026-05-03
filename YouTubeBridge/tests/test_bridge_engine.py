import shutil
import sys
import uuid
from pathlib import Path


BRIDGE_ROOT = Path(__file__).resolve().parents[1]
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))

from bridge_engine import YouTubeBridgeManager
from storage import BridgeStorage


def _tmp_dir() -> Path:
    path = Path(".pyTestTemp") / "youtube-bridge" / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=False)
    return path


def test_build_external_context_uses_compact_llm_lines():
    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "api_key": "key",
            "enabled": True,
        })
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "video_id": "video-a",
            "live_chat_id": "chat-a",
        })
        storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt-main",
            "youtube_message_id": "msg-a",
            "message_text": "被看到大型debug現場",
            "author_display_name": "@yodawnla",
            "author_channel_id": "UCFakeChannelId",
            "message_type": "textMessageEvent",
            "published_at": "2026-05-02T15:53:17.8658+00:00",
        })

        payload, summary = YouTubeBridgeManager(storage).build_external_context("live-a")

        assert summary["event_count"] == 1
        assert payload["context_text"] == "- @yodawnla: 被看到大型debug現場"
        assert "2026-05-02T15:53:17.8658+00:00" not in payload["context_text"]
        assert "textMessageEvent" not in payload["context_text"]
        assert "UCFakeChannelId" not in payload["context_text"]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
