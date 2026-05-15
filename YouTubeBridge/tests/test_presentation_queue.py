import asyncio
import shutil
import sys
import time
import uuid
from pathlib import Path

import pytest


BRIDGE_ROOT = Path(__file__).resolve().parents[1]
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))

from bridge_engine import YouTubeBridgeManager
from storage import BridgeStorage
from tts_gpt_sovits import TTSResult


def _tmp_dir() -> Path:
    path = Path(".pyTestTemp") / "youtube-bridge" / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=False)
    return path


async def _wait_for(condition, *, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition was not met before timeout")


class FakeTTSProvider:
    def __init__(self):
        self.calls = []

    def synthesize(self, text, profile):
        self.calls.append({"text": text, "profile": dict(profile)})
        return TTSResult(ok=True, audio_bytes=f"audio:{text}".encode("utf-8"), audio_format="wav")

    def call_texts(self):
        return [call["text"] for call in self.calls]


class FailingTTSProvider:
    def synthesize(self, text, profile):
        return TTSResult(ok=False, audio_format="wav", error="tts offline")


@pytest.mark.asyncio
async def test_presentation_queue_waits_for_ack_before_next_utterance():
    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "enabled": True,
        })
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "presentation_enabled": True,
            "tts_enabled": True,
            "presentation_ack_timeout_seconds": 3,
        })
        storage.upsert_tts_profile({
            "character_id": "char-a",
            "ref_audio_path": "voice.wav",
            "prompt_text": "參考文字。",
            "text_lang": "zh",
            "prompt_lang": "zh",
        })
        provider = FakeTTSProvider()
        manager = YouTubeBridgeManager(storage, tts_provider_factory=lambda: provider)
        queue = await manager.subscribe("live-a")

        task = asyncio.create_task(manager.present_stream_result(
            "live-a",
            {
                "message_id": "msg-a",
                "reply": "第一句。第二句。",
                "character_id": "char-a",
                "character_name": "可可",
            },
            source="director",
            interaction_job_id="job-a",
        ))

        first = await asyncio.wait_for(queue.get(), timeout=1)
        assert first["type"] == "presentation_item_ready"
        assert first["item"]["text"] == "第一句。"
        assert queue.empty()

        await manager.ack_presentation_item("live-a", first["item"]["item_id"])
        second = await asyncio.wait_for(queue.get(), timeout=1)
        assert second["type"] == "chat_message"
        assert second["message"]["content"] == "第一句。"
        third = await asyncio.wait_for(queue.get(), timeout=1)
        assert third["type"] == "presentation_item_ready"
        assert third["item"]["text"] == "第二句。"

        await manager.ack_presentation_item("live-a", third["item"]["item_id"])
        await asyncio.wait_for(task, timeout=1)
        assert [call["text"] for call in provider.calls] == ["第一句。", "第二句。"]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_presentation_queue_prefetches_next_utterance_audio_before_ack():
    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "enabled": True,
        })
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "presentation_enabled": True,
            "tts_enabled": True,
            "presentation_ack_timeout_seconds": 3,
        })
        storage.upsert_tts_profile({
            "character_id": "char-a",
            "ref_audio_path": "voice.wav",
            "prompt_text": "參考文字。",
        })
        provider = FakeTTSProvider()
        manager = YouTubeBridgeManager(storage, tts_provider_factory=lambda: provider)
        queue = await manager.subscribe("live-a")

        task = asyncio.create_task(manager.present_stream_result(
            "live-a",
            {
                "message_id": "msg-a",
                "reply": "第一句。第二句。",
                "character_id": "char-a",
                "character_name": "可可",
            },
            source="director",
            interaction_job_id="job-a",
        ))

        first = await asyncio.wait_for(queue.get(), timeout=1)
        assert first["type"] == "presentation_item_ready"
        assert first["item"]["text"] == "第一句。"

        await _wait_for(lambda: provider.call_texts() == ["第一句。", "第二句。"])
        await _wait_for(
            lambda: len(storage.list_presentation_items("live-a")) == 2
            and storage.list_presentation_items("live-a")[1]["status"] == "ready"
            and bool(storage.list_presentation_items("live-a")[1]["audio_path"])
        )
        items = storage.list_presentation_items("live-a")
        assert [item["text"] for item in items] == ["第一句。", "第二句。"]
        assert items[1]["status"] == "ready"
        assert items[1]["audio_path"]
        assert queue.empty()

        await manager.ack_presentation_item("live-a", first["item"]["item_id"])
        chat = await asyncio.wait_for(queue.get(), timeout=1)
        assert chat["type"] == "chat_message"
        second = await asyncio.wait_for(queue.get(), timeout=1)
        assert second["type"] == "presentation_item_ready"
        assert second["item"]["text"] == "第二句。"

        await manager.ack_presentation_item("live-a", second["item"]["item_id"])
        await asyncio.wait_for(task, timeout=1)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_presentation_queue_keeps_text_moving_when_tts_fails():
    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "enabled": True,
        })
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "presentation_enabled": True,
            "tts_enabled": True,
            "presentation_ack_timeout_seconds": 3,
        })
        storage.upsert_tts_profile({
            "character_id": "char-a",
            "ref_audio_path": "voice.wav",
        })
        manager = YouTubeBridgeManager(storage, tts_provider_factory=FailingTTSProvider)
        queue = await manager.subscribe("live-a")

        task = asyncio.create_task(manager.present_stream_result(
            "live-a",
            {
                "message_id": "msg-a",
                "reply": "音訊失敗也要播文字。",
                "character_id": "char-a",
                "character_name": "可可",
            },
            source="director",
            interaction_job_id="job-a",
        ))

        ready = await asyncio.wait_for(queue.get(), timeout=1)
        assert ready["type"] == "presentation_item_ready"
        assert ready["item"]["audio_url"] == ""
        assert ready["item"]["status"] == "failed"

        await manager.ack_presentation_item("live-a", ready["item"]["item_id"])
        chat = await asyncio.wait_for(queue.get(), timeout=1)
        assert chat["type"] == "chat_message"
        assert chat["message"]["content"] == "音訊失敗也要播文字。"
        await asyncio.wait_for(task, timeout=1)
        item = storage.get_presentation_item(ready["item"]["item_id"])
        assert item["status"] == "failed"
        assert item["error"] == "tts offline"
        assert item["acked_at"]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
