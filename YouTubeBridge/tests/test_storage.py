import shutil
import sys
import uuid
from pathlib import Path


BRIDGE_ROOT = Path(__file__).resolve().parents[1]
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))

from storage import BridgeStorage


def _tmp_dir() -> Path:
    path = Path(".pyTestTemp") / "youtube-bridge" / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=False)
    return path


def test_connector_and_session_roundtrip():
    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        connector = storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "api_key": "key",
            "enabled": True,
        })
        assert connector["connector_id"] == "yt-main"
        assert connector["enabled"] is True

        session = storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "display_name": "Live A",
            "video_id": "video-a",
            "live_chat_id": "",
            "target_memoria_session_id": "mem-a",
            "character_ids": ["coco", "bailian"],
            "status": "stopped",
            "auto_connect": False,
            "auto_inject": True,
            "inject_interval_seconds": 15,
            "min_pending_events": 3,
            "max_context_messages": 20,
            "max_context_chars": 4000,
            "retention_days": 7,
        })
        assert session["session_id"] == "live-a"
        assert session["character_ids"] == ["coco", "bailian"]
        assert session["auto_inject"] is True
        assert session["inject_interval_seconds"] == 15
        assert session["min_pending_events"] == 3
        assert storage.list_sessions()[0]["connector_id"] == "yt-main"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_live_event_dedupes_and_preserves_id_lookup_order():
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
        first = storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt-main",
            "youtube_message_id": "msg-a",
            "message_text": "hello",
        })
        duplicate = storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt-main",
            "youtube_message_id": "msg-a",
            "message_text": "hello again",
        })
        second = storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt-main",
            "youtube_message_id": "msg-b",
            "message_text": "world",
        })

        assert first is not None
        assert duplicate is None
        assert second is not None
        events = storage.get_events_by_ids("live-a", [second["id"], first["id"]])
        assert [event["youtube_message_id"] for event in events] == ["msg-b", "msg-a"]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_mark_events_injected_filters_pending_events():
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
        first = storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt-main",
            "youtube_message_id": "msg-a",
            "message_text": "hello",
        })
        second = storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt-main",
            "youtube_message_id": "msg-b",
            "message_text": "world",
        })

        assert first is not None
        assert second is not None
        assert storage.mark_events_injected("live-a", [first["id"]]) == 1

        pending = storage.list_events("live-a", uninjected_only=True)
        assert [event["youtube_message_id"] for event in pending] == ["msg-b"]

        injected = storage.get_events_by_ids("live-a", [first["id"]])[0]
        assert injected["injected_at"]
        assert injected["injection_count"] == 1
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
