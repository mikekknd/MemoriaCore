import asyncio
import shutil
import time

import pytest

from bridge_engine_test_support import (
    BridgeStorage,
    FakeTTSProvider,
    YouTubeBridgeManager,
    _next_queue_event,
    _tmp_dir,
    _wait_until as _wait_for,
)
from tts_gpt_sovits import TTSResult


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

        first = await _next_queue_event(queue, "presentation_item_ready")
        assert first["item"]["text"] == "第一句。"

        await manager.ack_presentation_item("live-a", first["item"]["item_id"])
        second = await _next_queue_event(queue, "chat_message")
        assert second["message"]["content"] == "第一句。"
        third = await _next_queue_event(queue, "presentation_item_ready")
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

        first = await _next_queue_event(queue, "presentation_item_ready")
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

        await manager.ack_presentation_item("live-a", first["item"]["item_id"])
        chat = await _next_queue_event(queue, "chat_message")
        second = await _next_queue_event(queue, "presentation_item_ready")
        assert second["item"]["text"] == "第二句。"

        await manager.ack_presentation_item("live-a", second["item"]["item_id"])
        await asyncio.wait_for(task, timeout=1)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_prepared_prefetch_broadcasts_audio_preload_without_revealing_text():
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

        prepared = await manager.prepare_stream_result(
            "live-a",
            {
                "message_id": "msg-prefetch",
                "reply": "下一輪先準備。",
                "character_id": "char-a",
                "character_name": "可可",
            },
            source="director_prefetch",
            interaction_job_id="job-prefetch",
        )

        preload = await _next_queue_event(queue, "presentation_item_preload")
        assert preload["item"]["item_id"] == prepared["items"][0]["item_id"]
        assert preload["item"]["audio_url"].endswith(f"/presentation/{preload['item']['item_id']}/audio")
        assert preload["item"]["audio_format"] == "wav"
        assert "text" not in preload["item"]
        assert "character_name" not in preload["item"]

        try:
            await _next_queue_event(queue, "presentation_item_ready", timeout=0.05)
        except (AssertionError, asyncio.TimeoutError):
            pass
        else:
            raise AssertionError("presentation_item_ready should not expose prepared text before playback")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_active_turn_does_not_preload_current_presentation_item():
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
                "message_id": "msg-active",
                "reply": "目前這句直接播放。",
                "character_id": "char-a",
                "character_name": "可可",
            },
            source="director",
            interaction_job_id="job-active",
        ))

        event_types = []
        ready = None
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            event = await asyncio.wait_for(queue.get(), timeout=max(0.01, deadline - time.monotonic()))
            event_types.append(event.get("type"))
            if event.get("type") == "presentation_item_ready":
                ready = event
                break
        assert ready is not None
        assert "presentation_item_preload" not in event_types

        await manager.ack_presentation_item("live-a", ready["item"]["item_id"])
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

        ready = await _next_queue_event(queue, "presentation_item_ready")
        assert ready["item"]["audio_url"] == ""
        assert ready["item"]["status"] == "failed"

        await manager.ack_presentation_item("live-a", ready["item"]["item_id"])
        chat = await _next_queue_event(queue, "chat_message")
        assert chat["message"]["content"] == "音訊失敗也要播文字。"
        await asyncio.wait_for(task, timeout=1)
        item = storage.get_presentation_item(ready["item"]["item_id"])
        assert item["status"] == "failed"
        assert item["error"] == "tts offline"
        assert item["acked_at"]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
