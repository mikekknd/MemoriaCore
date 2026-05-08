import sys
from pathlib import Path


BRIDGE_ROOT = Path(__file__).resolve().parents[1]
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))

from bridge_engine import YouTubeBridgeManager
from memoria_client import MemoriaClient
from storage import BridgeStorage


class _FakeResponse:
    status_code = 200
    text = "{}"

    def json(self):
        return {"session_id": "mem-a", "reply": "ok"}


class _FakeSession:
    def __init__(self):
        self.payload = None
        self.cookies = {}

    def post(self, _url, *, json=None, headers=None, timeout=None, stream=False):
        self.payload = json
        return _FakeResponse()


def test_live_persona_overlay_storage_roundtrip(tmp_path):
    storage = BridgeStorage(tmp_path / "bridge.db")

    saved = storage.upsert_live_persona_overlay(
        "coco",
        {
            "enabled": True,
            "mode": "replace",
            "system_prompt": "直播可可 prompt",
            "self_address": "本小姐",
            "opening_intro": "本小姐是今天的主持。",
            "reply_rules": "自然接話。",
            "addressing": {"bailian": "白蓮大人"},
        },
    )

    assert saved["character_id"] == "coco"
    assert saved["enabled"] is True
    assert saved["addressing"] == {"bailian": "白蓮大人"}
    assert storage.get_live_persona_overlay("coco")["system_prompt"] == "直播可可 prompt"
    assert storage.list_live_persona_overlays()[0]["self_address"] == "本小姐"


def test_manager_attaches_enabled_live_persona_overrides(tmp_path):
    storage = BridgeStorage(tmp_path / "bridge.db")
    storage.ensure_single_connector()
    storage.upsert_session({
        "session_id": "live-a",
        "connector_id": "youtube-main",
        "character_ids": ["coco", "bailian"],
    })
    storage.upsert_live_persona_overlay(
        "coco",
        {
            "enabled": True,
            "mode": "replace",
            "system_prompt": "直播可可 prompt",
            "self_address": "本小姐",
            "opening_intro": "可可開場。",
            "reply_rules": "自然接話。",
            "addressing": {"bailian": "白蓮大人"},
        },
    )
    storage.upsert_live_persona_overlay(
        "bailian",
        {"enabled": False, "mode": "replace", "system_prompt": "不應送出"},
    )

    external_context = {"source": "youtube_live_director", "context_text": "直播流程"}
    enriched = YouTubeBridgeManager(storage)._attach_live_persona_overrides(
        storage.get_session("live-a"),
        external_context,
    )

    overrides = enriched["character_prompt_overrides"]
    assert list(overrides) == ["coco"]
    assert overrides["coco"]["system_prompt"] == "直播可可 prompt"
    assert overrides["coco"]["addressing"] == {"bailian": "白蓮大人"}
    assert external_context.get("character_prompt_overrides") is None


def test_memoria_client_sends_live_persona_overrides_with_live_scope():
    client = MemoriaClient(base_url="http://memoria.test/api/v1", admin_bypass=True)
    fake_session = _FakeSession()
    client.session = fake_session
    client.ensure_auth = lambda: None

    client.chat_sync(
        content="請延續直播",
        session_id="mem-a",
        character_ids=["coco"],
        external_context={
            "source": "youtube_live_director",
            "source_session_id": "live-a",
            "context_text": "直播流程",
            "character_prompt_overrides": {
                "coco": {
                    "enabled": True,
                    "mode": "replace",
                    "system_prompt": "直播可可 prompt",
                    "self_address": "本小姐",
                    "opening_intro": "可可開場。",
                    "addressing": {},
                }
            },
        },
    )

    assert fake_session.payload["channel"] == "youtube_live"
    assert fake_session.payload["user_id"] == "__youtube_live__"
    assert fake_session.payload["external_context"]["character_prompt_overrides"]["coco"]["system_prompt"] == "直播可可 prompt"
