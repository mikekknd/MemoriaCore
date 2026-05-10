import asyncio
import contextlib
import json
import shutil
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from bridge_engine_test_support import (
    BRIDGE_ROOT,
    BridgeStorage,
    CapturingDirectorDecisionClient,
    ContractOnlyQueryClient,
    FakeBatchRecordingSafetyClient,
    FakeClosingFailingSafetyClient,
    FakeClosingMemoriaClient,
    FakeClosingSystemEventClient,
    FakeEmbeddingMemoriaClient,
    FakeFailingSafetyMemoriaClient,
    FakeSafetyMemoriaClient,
    LiveEndedClient,
    LiveRuntime,
    OffTopicEmbeddingMemoriaClient,
    OneMessagePollingClient,
    ResolveLiveChatFailedClient,
    YouTubeBridgeManager,
    _mark_event_clean,
    _tmp_dir,
    bridge_engine,
    normalize_message,
)


@pytest.mark.asyncio
async def test_inject_recent_sends_hidden_prompt_and_visible_chat_lines_separately():
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
            "target_memoria_session_id": "mem-a",
            "character_ids": ["char-a", "char-b"],
        })
        event = storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt-main",
            "youtube_message_id": "msg-a",
            "message_text": "被看到大型debug現場",
            "author_display_name": "@yodawnla",
            "author_channel_id": "UCFakeChannelId",
            "message_type": "textMessageEvent",
        })
        _mark_event_clean(storage, event)
        captured = {}

        class CaptureClient:
            def chat_stream_sync(self, **kwargs):
                captured.update(kwargs)
                return {"session_id": "mem-a", "message_id": 1, "reply": "已回應。"}

        manager = YouTubeBridgeManager(storage, memoria_client_factory=CaptureClient)

        await manager.inject_recent(
            "live-a",
            content="請根據已帶入的 YouTube 直播留言上下文回應。不要開啟瀏覽器或搜尋網頁。",
        )

        assert "請根據已帶入" in captured["content"]
        assert captured["display_content"] == "@yodawnla: 被看到大型debug現場"
        assert "UCFakeChannelId" not in captured["display_content"]
        assert "textMessageEvent" not in captured["display_content"]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

@pytest.mark.asyncio
async def test_inject_recent_streams_each_assistant_turn_to_live_chat():
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
            "target_memoria_session_id": "mem-a",
            "character_ids": ["char-a", "char-b"],
        })
        event = storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt-main",
            "youtube_message_id": "msg-a",
            "message_text": "請聊動畫新番",
            "author_display_name": "viewer",
            "author_channel_id": "channel-a",
            "message_type": "textMessageEvent",
        })
        _mark_event_clean(storage, event)

        class StreamingClient:
            def chat_stream_sync(self, **kwargs):
                kwargs["on_result"]({
                    "type": "result",
                    "session_id": "mem-a",
                    "message_id": 101,
                    "reply": "可可先接話。",
                    "character_id": "char-a",
                    "character_name": "可可",
                    "turn_index": 0,
                })
                kwargs["on_result"]({
                    "type": "result",
                    "session_id": "mem-a",
                    "message_id": 102,
                    "reply": "白蓮補充觀點。",
                    "character_id": "char-b",
                    "character_name": "白蓮",
                    "turn_index": 1,
                })
                return {
                    "session_id": "mem-a",
                    "message_id": 102,
                    "reply": "白蓮補充觀點。",
                    "character_id": "char-b",
                    "character_name": "白蓮",
                }

        manager = YouTubeBridgeManager(storage, memoria_client_factory=StreamingClient)
        queue = await manager.subscribe("live-a")

        await manager.inject_recent("live-a", content="請回應直播留言。")

        chat_messages = []
        while not queue.empty():
            payload = await queue.get()
            if payload.get("type") == "chat_message":
                chat_messages.append(payload["message"])

        assert [message["content"] for message in chat_messages] == ["可可先接話。", "白蓮補充觀點。"]
        assert [message["character_id"] for message in chat_messages] == ["char-a", "char-b"]
        assert all(message["role"] == "assistant" for message in chat_messages)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

def test_dynamic_auto_inject_delay_accelerates_with_pending_count():
    base_session = {
        "inject_interval_seconds": 60,
        "min_pending_events": 1,
        "max_pending_events": 10,
        "dynamic_inject_enabled": True,
        "inject_min_interval_ratio": 0.32,
    }

    low = YouTubeBridgeManager._auto_inject_delay(base_session, 1, active_interaction=False)
    high = YouTubeBridgeManager._auto_inject_delay(base_session, 10, active_interaction=False)
    active = YouTubeBridgeManager._auto_inject_delay(base_session, 3, active_interaction=True)

    assert high < low
    assert active == 60

def test_dynamic_auto_inject_delay_uses_configured_min_seconds_and_stays_enabled():
    session = {
        "inject_interval_seconds": 60,
        "min_pending_events": 1,
        "max_pending_events": 10,
        "dynamic_inject_enabled": False,
        "inject_min_interval_seconds": 20,
    }

    assert YouTubeBridgeManager._auto_inject_delay(session, 10, active_interaction=False) == 20.0

def test_dynamic_auto_inject_does_not_accelerate_while_generation_is_active():
    session = {
        "inject_interval_seconds": 60,
        "min_pending_events": 1,
        "max_pending_events": 12,
        "inject_min_interval_seconds": 15,
    }

    assert YouTubeBridgeManager._auto_inject_delay(session, 8, active_interaction=True) == 60.0

@pytest.mark.asyncio
async def test_generate_test_events_variants_repeated_super_chat_text(monkeypatch):
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
            "display_name": "QA Live",
        })
        storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt-main",
            "youtube_message_id": "existing-sc",
            "message_type": "testSuperChatEvent",
            "author_display_name": "SC路人",
            "message_text": "感謝開台，可以請角色各自補一句看法嗎？",
            "amount_display_string": "NT$150",
            "amount_micros": 150000000,
            "priority_class": "super_chat",
            "published_at": "2026-05-04T00:00:00",
            "received_at": "2026-05-04T00:00:00",
            "status": "active",
        })

        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())
        monkeypatch.setattr(manager, "_generate_test_comments", lambda *_args, **_kwargs: [])
        monkeypatch.setattr(
            manager,
            "_generate_test_super_chats",
            lambda *_args, **_kwargs: [
                {
                    "author_display_name": "紅色斗內",
                    "message_text": "感謝開台，可以請角色各自補一句看法嗎？",
                    "amount_micros": 300000000,
                    "amount_display_string": "NT$300",
                    "currency": "TWD",
                }
            ],
        )

        result = await manager.generate_test_events(
            "live-a",
            count=1,
            use_llm=False,
            super_chat_count=1,
        )

        assert result["super_chat_generated"] == 1
        new_sc = [event for event in storage.list_events("live-a") if event["youtube_message_id"].startswith("test-sc-")][0]
        assert new_sc["message_text"] != "感謝開台，可以請角色各自補一句看法嗎？"
        assert "想補問" in new_sc["message_text"]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

def test_select_pending_events_prioritizes_super_chat_before_normal_events():
    normal = {
        "id": 1,
        "message_text": "一般留言",
        "priority_class": "normal",
        "sc_tier": 0,
        "status": "active",
    }
    sc_low = {
        "id": 2,
        "message_text": "小額 SC",
        "priority_class": "super_chat",
        "sc_tier": 1,
        "status": "active",
    }
    sc_high = {
        "id": 3,
        "message_text": "高 tier SC",
        "priority_class": "super_chat",
        "sc_tier": 4,
        "status": "active",
    }

    selected = YouTubeBridgeManager._select_pending_events_for_injection(
        [normal, sc_low, sc_high],
        max_events=3,
        max_sc_per_batch=5,
    )

    assert [event["id"] for event in selected] == [3, 2, 1]

def test_normalize_message_marks_super_chat_priority_fields():
    item = {
        "id": "yt-sc-1",
        "snippet": {
            "type": "superChatEvent",
            "displayMessage": "請回應這個 SC",
            "publishedAt": "2026-05-04T10:00:00Z",
            "superChatDetails": {
                "amountDisplayString": "NT$150",
                "amountMicros": 150000000,
                "currency": "TWD",
                "tier": 2,
            },
        },
        "authorDetails": {
            "channelId": "author-a",
            "displayName": "SC觀眾",
        },
    }

    event = normalize_message(
        item,
        session={"session_id": "live-a", "video_id": "video-a", "live_chat_id": "chat-a"},
        connector={"connector_id": "yt-main"},
    )

    assert event["priority_class"] == "super_chat"
    assert event["amount_micros"] == 150000000
    assert event["sc_tier"] == 2
