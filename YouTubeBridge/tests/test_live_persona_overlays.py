import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

BRIDGE_ROOT = Path(__file__).resolve().parents[1]
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))

from bridge_engine import YouTubeBridgeManager
from memoria_client import MemoriaClient
from models import LiveTTSProfileRequest
from server_routes import persona_overlays
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
            "avatar_url": "https://example.invalid/coco.png",
            "chat_background_color": "#f2fbff",
            "chat_accent_color": "#0d9488",
        },
    )

    assert saved["character_id"] == "coco"
    assert saved["enabled"] is True
    assert saved["addressing"] == {"bailian": "白蓮大人"}
    assert saved["avatar_url"] == "https://example.invalid/coco.png"
    assert saved["chat_background_color"] == "#f2fbff"
    assert saved["chat_accent_color"] == "#0d9488"
    assert storage.get_live_persona_overlay("coco")["system_prompt"] == "直播可可 prompt"
    assert storage.get_live_persona_overlay("coco")["avatar_url"] == "https://example.invalid/coco.png"
    assert storage.list_live_persona_overlays()[0]["self_address"] == "本小姐"


@pytest.mark.asyncio
async def test_live_persona_routes_manage_per_character_tts_profiles(tmp_path):
    storage = BridgeStorage(tmp_path / "bridge.db")
    persona_overlays.configure(SimpleNamespace(storage=storage))

    saved = await persona_overlays.update_tts_profile(
        "coco",
        LiveTTSProfileRequest(
            enabled=True,
            ref_audio_path="G:/Voices/coco.wav",
            prompt_text="本小姐是今天的主持可可。",
            text_lang="zh",
            prompt_lang="zh",
            speed_factor=1.15,
            media_type="wav",
        ),
    )

    assert saved["character_id"] == "coco"
    assert saved["enabled"] is True
    assert saved["ref_audio_path"] == "G:/Voices/coco.wav"
    assert saved["prompt_text"] == "本小姐是今天的主持可可。"
    assert saved["speed_factor"] == 1.15

    listed = await persona_overlays.list_persona_overlays()
    assert listed["tts_profiles"][0]["character_id"] == "coco"
    assert listed["tts_profiles"][0]["prompt_text"] == "本小姐是今天的主持可可。"

    default = await persona_overlays.get_tts_profile("bailian")
    assert default["character_id"] == "bailian"
    assert default["enabled"] is False
    assert default["ref_audio_path"] == ""
    assert default["prompt_text"] == ""


@pytest.mark.asyncio
async def test_live_tts_sources_list_matching_audio_and_transcript(monkeypatch, tmp_path):
    source_root = tmp_path / "TTSSource"
    source_root.mkdir()
    (source_root / "coco.wav").write_bytes(b"wav")
    (source_root / "coco.txt").write_text("本小姐是今天的主持可可。", encoding="utf-8")
    (source_root / "bailian.mp3").write_bytes(b"mp3")
    (source_root / "missing-transcript.wav").write_bytes(b"wav")
    (source_root / "missing-audio.txt").write_text("沒有音檔。", encoding="utf-8")
    nested = source_root / "team"
    nested.mkdir()
    (nested / "analyst.flac").write_bytes(b"flac")
    (nested / "analyst.txt").write_text("分析角色參考音。", encoding="utf-8")
    monkeypatch.setattr(persona_overlays, "TTS_SOURCE_ROOT", source_root)

    sources = await persona_overlays.list_tts_sources()

    assert sources["root"] == str(source_root)
    assert sources["sources"] == [
        {
            "name": "coco",
            "audio_path": str(source_root / "coco.wav"),
            "transcript_path": str(source_root / "coco.txt"),
            "prompt_text": "本小姐是今天的主持可可。",
        },
        {
            "name": "team/analyst",
            "audio_path": str(nested / "analyst.flac"),
            "transcript_path": str(nested / "analyst.txt"),
            "prompt_text": "分析角色參考音。",
        },
    ]


def test_live_tts_profile_requires_reference_audio_and_transcript_when_enabled():
    with pytest.raises(ValueError):
        LiveTTSProfileRequest(enabled=True, ref_audio_path="", prompt_text="參考文字")
    with pytest.raises(ValueError):
        LiveTTSProfileRequest(enabled=True, ref_audio_path="voice.wav", prompt_text="")


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


def test_live_persona_overlay_rejects_invalid_chat_colors(tmp_path):
    storage = BridgeStorage(tmp_path / "bridge.db")

    with pytest.raises(ValueError):
        storage.upsert_live_persona_overlay(
            "coco",
            {
                "enabled": True,
                "mode": "replace",
                "chat_background_color": "not-a-color",
            },
        )


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
