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
    _next_queue_event,
    _tmp_dir,
    bridge_engine,
    normalize_message,
    temp_storage,
)
import engine_injection
from live_episode_plan_contract import initial_planned_state
from test_live_episode_plan_contract import sample_plan
from tts_gpt_sovits import TTSResult

# Split from test_bridge_engine_injection.py: injection and auto-inject behavior.

@pytest.mark.asyncio
async def test_stream_result_drops_message_if_interrupted_before_broadcast():
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
            "target_memoria_session_id": "mem-a",
            "character_ids": ["char-a"],
            "presentation_enabled": False,
        })
        interaction = storage.create_interaction({
            "session_id": "live-a",
            "source": "director",
            "priority": 50,
            "status": "running",
            "memoria_session_id": "mem-a",
            "character_ids": ["char-a"],
            "content": "雜談生成中",
        })
        manager = YouTubeBridgeManager(storage)
        queue = await manager.subscribe("live-a")

        manager._dispatch_stream_chat_result(
            asyncio.get_running_loop(),
            "live-a",
            {
                "message_id": 42,
                "reply": "這段舊雜談不應在收尾後顯示。",
                "character_id": "char-a",
                "character_name": "可可",
            },
            source="director",
            interaction_job_id=interaction["job_id"],
        )
        storage.update_interaction(
            interaction["job_id"],
            status="interrupted",
            reason="live_session_closing",
            completed_at=datetime.now().isoformat(),
        )
        await asyncio.sleep(0.05)

        messages = []
        while not queue.empty():
            payload = queue.get_nowait()
            if payload.get("type") == "chat_message":
                messages.append(payload["message"]["content"])
        assert messages == []
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_stream_result_marks_message_visible_after_broadcast():
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
            "target_memoria_session_id": "mem-a",
            "character_ids": ["char-a"],
            "presentation_enabled": False,
        })
        interaction = storage.create_interaction({
            "session_id": "live-a",
            "source": "director",
            "priority": 50,
            "status": "running",
            "memoria_session_id": "mem-a",
            "character_ids": ["char-a"],
            "content": "收尾前一則生成",
        })
        manager = YouTubeBridgeManager(storage)
        queue = await manager.subscribe("live-a")

        manager._dispatch_stream_chat_result(
            asyncio.get_running_loop(),
            "live-a",
            {
                "message_id": 42,
                "reply": "這句已經出現在畫面上。",
                "character_id": "char-a",
                "character_name": "可可",
                "timestamp": "2026-05-16T09:20:19",
            },
            source="director",
            interaction_job_id=interaction["job_id"],
        )

        payload = await _next_queue_event(queue, "chat_message")
        assert payload["message"]["content"] == "這句已經出現在畫面上。"

        updated = storage.get_interaction(interaction["job_id"])
        visible = updated["metadata"]["visible_messages"]
        assert visible == [{
            "message_id": 42,
            "role": "assistant",
            "content": "這句已經出現在畫面上。",
            "created_at": "2026-05-16T09:20:19",
            "timestamp": "2026-05-16T09:20:19",
            "character_id": "char-a",
            "character_name": "可可",
            "source": "director",
        }]
        assert updated["metadata"]["last_visible_message"]["content"] == "這句已經出現在畫面上。"
        assert updated["metadata"]["has_visible_output"] is True
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_inject_recent_classifies_only_selected_event_ids(monkeypatch):
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
            "character_ids": ["char-a"],
        })
        event_ids = []
        for index in range(25):
            event = storage.save_event({
                "bridge_session_id": "live-a",
                "connector_id": "yt-main",
                "youtube_message_id": f"pending-{index}",
                "message_text": f"待安全分類留言 {index}",
                "author_display_name": f"viewer-{index}",
                "author_channel_id": f"viewer-{index}",
                "message_type": "textMessageEvent",
            })
            event_ids.append(event["id"])

        class CaptureClient:
            def chat_stream_sync(self, **kwargs):
                return {"session_id": "mem-a", "message_id": 1, "reply": "已回應。"}

        manager = YouTubeBridgeManager(storage, memoria_client_factory=CaptureClient)
        classified_batches = []

        async def classify_selected(session_id, selected_ids):
            classified_batches.append(list(selected_ids))
            for event in storage.get_events_by_ids(session_id, selected_ids, limit=len(selected_ids)):
                storage.update_event_safety(
                    int(event["id"]),
                    status="completed",
                    label="clean",
                    safe_message_text=str(event.get("message_text") or ""),
                )
            return {"classified_count": len(selected_ids), "failed_count": 0, "events": []}

        async def fail_global_classification(*_args, **_kwargs):
            raise AssertionError("inject_recent 不應分類整個 pending backlog")

        monkeypatch.setattr(manager, "classify_event_ids_serialized", classify_selected)
        monkeypatch.setattr(manager, "classify_pending_events_serialized", fail_global_classification)

        await manager.inject_recent(
            "live-a",
            event_ids=event_ids[:2],
            content="請回應指定留言。",
        )

        assert classified_batches == [event_ids[:2]]
        assert storage.get_events_by_ids("live-a", event_ids[:2], limit=2)[0]["injected_at"]
        assert storage.get_events_by_ids("live-a", event_ids[2:3], limit=1)[0]["safety_status"] == "pending"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


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


@pytest.mark.asyncio
async def test_inject_recent_uses_presentation_queue_when_enabled():
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
            "character_ids": ["char-a"],
            "presentation_enabled": True,
            "tts_enabled": True,
            "presentation_ack_timeout_seconds": 3,
        })
        storage.upsert_tts_profile({
            "character_id": "char-a",
            "ref_audio_path": "voice.wav",
            "text_lang": "zh",
            "prompt_lang": "zh",
        })
        event = storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt-main",
            "youtube_message_id": "msg-a",
            "message_text": "請回應直播留言",
            "author_display_name": "viewer",
            "author_channel_id": "viewer-a",
            "message_type": "textMessageEvent",
        })
        _mark_event_clean(storage, event)

        class OneResultClient:
            def chat_stream_sync(self, **kwargs):
                external_context = kwargs["external_context"]
                assert external_context["group_turn_limit"] == 1
                assert external_context["max_chars"] <= 1200
                assert external_context["summary"]["presentation_enabled"] is True
                kwargs["on_result"]({
                    "session_id": "mem-a",
                    "message_id": 42,
                    "reply": "第一句。",
                    "character_id": "char-a",
                    "character_name": "可可",
                })
                return {
                    "session_id": "mem-a",
                    "message_id": 42,
                    "reply": "第一句。",
                    "character_id": "char-a",
                    "character_name": "可可",
                }

        class FakeTTSProvider:
            def synthesize(self, text, profile):
                return TTSResult(ok=True, audio_bytes=b"wav", audio_format="wav")

        manager = YouTubeBridgeManager(
            storage,
            memoria_client_factory=OneResultClient,
            tts_provider_factory=FakeTTSProvider,
        )
        queue = await manager.subscribe("live-a")
        task = asyncio.create_task(manager.inject_recent("live-a", content="請回應。"))

        first = await asyncio.wait_for(queue.get(), timeout=1)
        assert first["type"] == "interaction_started"
        second = await _next_queue_event(queue, "presentation_item_ready", timeout=1)
        assert second["item"]["text"] == "第一句。"
        assert queue.empty()

        active = storage.get_active_interaction("live-a")
        assert active["status"] == "presenting"

        await manager.ack_presentation_item("live-a", second["item"]["item_id"])
        chat = await _next_queue_event(queue, "chat_message", timeout=1)
        assert chat["message"]["content"] == "第一句。"
        await asyncio.wait_for(task, timeout=1)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_legacy_auto_inject_recent_super_chat_cooldown_does_not_replace_timestamp(monkeypatch):
    tmp_dir = _tmp_dir()
    original_sleep = engine_injection.asyncio.sleep
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
            "auto_inject": True,
            "min_pending_events": 1,
            "max_pending_events": 5,
            "inject_interval_seconds": 5,
            "sc_interrupt_cooldown_seconds": 3600,
        })
        storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt-main",
            "youtube_message_id": "legacy-cooldown-sc",
            "message_type": "superChatEvent",
            "author_display_name": "紅色斗內",
            "message_text": "請優先回應這個 SC",
            "amount_display_string": "NT$750",
            "amount_micros": 750_000_000,
            "priority_class": "super_chat",
            "sc_tier": 4,
            "safety_status": "completed",
            "safety_label": "clean",
            "safe_message_text": "請優先回應這個 SC",
            "status": "active",
        })
        active = storage.create_interaction({
            "session_id": "live-a",
            "source": "director",
            "priority": 50,
            "status": "running",
            "content": "低優先級回應中。",
        })
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        cooldown_started_at = datetime.now().isoformat()
        runtime.last_sc_interrupt_at = cooldown_started_at
        inject_called = False

        async def forbidden_inject(*_args, **_kwargs):
            nonlocal inject_called
            inject_called = True
            return {"injected_at": "now"}

        async def stop_after_sleep(_seconds):
            runtime.running = False
            await original_sleep(0)

        monkeypatch.setattr(manager, "inject_recent", forbidden_inject)
        monkeypatch.setattr(engine_injection.asyncio, "sleep", stop_after_sleep)

        await asyncio.wait_for(manager._auto_inject_loop(runtime), timeout=1)

        assert inject_called is False
        assert runtime.last_sc_interrupt_at == cooldown_started_at
        assert storage.get_interaction(active["job_id"])["status"] == "running"
    finally:
        monkeypatch.setattr(engine_injection.asyncio, "sleep", original_sleep)
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_inject_recent_super_chat_routes_to_preprocessing_queue_when_enabled(monkeypatch):
    with temp_storage() as storage:
        storage.upsert_connector({"connector_id": "yt", "name": "YouTube", "api_key": "key", "enabled": True})
        plan = sample_plan()
        storage.upsert_live_episode_plan(plan)
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt",
            "display_name": "Live A",
            "status": "running",
            "presentation_enabled": True,
            "tts_enabled": True,
            "episode_plan_id": plan["plan_id"],
            "target_memoria_session_id": "mem-a",
            "character_ids": ["char-a"],
        })
        storage.update_director_state(
            "live-a",
            director_enabled=True,
            metadata={"planned_state": initial_planned_state(plan)},
        )
        active = storage.create_interaction({
            "session_id": "live-a",
            "source": "director",
            "priority": 50,
            "status": "running",
            "event_ids": [],
            "memoria_session_id": "mem-a",
            "character_ids": ["char-a"],
            "content": "planned running",
            "metadata": {},
        })
        event = storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt",
            "youtube_message_id": "sc-route-1",
            "author_display_name": "SC viewer",
            "message_text": "SC question",
            "priority_class": "super_chat",
            "amount_display_string": "NT$150",
            "sc_tier": 3,
            "status": "active",
        })
        storage.update_event_safety(
            int(event["id"]),
            status="completed",
            label="clean",
            safe_message_text="SC question",
            safety_summary="clean",
            reason="test",
            confidence=1.0,
        )
        manager = YouTubeBridgeManager(storage)
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        manager._runtimes["live-a"] = runtime

        async def fail_interrupt(*_args, **_kwargs):
            raise AssertionError("inject_recent must not interrupt in preprocessing mode")

        monkeypatch.setattr(manager, "interrupt_session", fail_interrupt)
        result = await manager.inject_recent(
            "live-a",
            event_ids=[event["id"]],
            source="super_chat",
            priority=320,
        )

        assert result["interaction"]["status"] == "queued_for_audience_preprocessing"
        assert result["summary"]["event_ids"] == [event["id"]]
        assert storage.get_interaction(active["job_id"])["status"] == "running"
        assert runtime.audience_preprocess_wake.is_set()


@pytest.mark.asyncio
async def test_inject_recent_preprocessing_does_not_queue_when_runtime_stopped():
    with temp_storage() as storage:
        storage.upsert_connector({"connector_id": "yt", "name": "YouTube", "api_key": "key", "enabled": True})
        plan = sample_plan()
        storage.upsert_live_episode_plan(plan)
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt",
            "display_name": "Live A",
            "status": "running",
            "presentation_enabled": True,
            "tts_enabled": True,
            "episode_plan_id": plan["plan_id"],
            "target_memoria_session_id": "mem-a",
            "character_ids": ["char-a"],
        })
        storage.update_director_state(
            "live-a",
            director_enabled=True,
            metadata={"planned_state": initial_planned_state(plan)},
        )
        event = storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt",
            "youtube_message_id": "stopped-runtime-comment",
            "author_display_name": "viewer",
            "message_text": "runtime stopped question",
            "priority_class": "chat",
            "status": "active",
        })
        storage.update_event_safety(
            int(event["id"]),
            status="completed",
            label="clean",
            safe_message_text="runtime stopped question",
            safety_summary="clean",
            reason="test",
            confidence=1.0,
        )

        class ReplyClient:
            def chat_stream_sync(self, **kwargs):
                return {
                    "session_id": kwargs.get("session_id") or "mem-a",
                    "message_id": 701,
                    "reply": "handled by legacy inject",
                }

        manager = YouTubeBridgeManager(storage, memoria_client_factory=ReplyClient)
        runtime = LiveRuntime(session_id="live-a", running=False, status="stopped")
        manager._runtimes["live-a"] = runtime

        result = await manager.inject_recent(
            "live-a",
            event_ids=[event["id"]],
            source="manual_inject",
            priority=200,
        )

        assert result["interaction"]["status"] != "queued_for_audience_preprocessing"
        assert runtime.audience_preprocess_wake.is_set() is False


@pytest.mark.asyncio
@pytest.mark.parametrize("session_status", ["closing", "ended"])
async def test_inject_recent_preprocessing_preserves_closing_session_error(session_status):
    with temp_storage() as storage:
        storage.upsert_connector({"connector_id": "yt", "name": "YouTube", "api_key": "key", "enabled": True})
        plan = sample_plan()
        storage.upsert_live_episode_plan(plan)
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt",
            "display_name": "Live A",
            "status": session_status,
            "presentation_enabled": True,
            "tts_enabled": True,
            "episode_plan_id": plan["plan_id"],
            "target_memoria_session_id": "mem-a",
            "character_ids": ["char-a"],
        })
        storage.update_director_state(
            "live-a",
            director_enabled=True,
            metadata={"planned_state": initial_planned_state(plan)},
        )
        event = storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt",
            "youtube_message_id": f"{session_status}-comment",
            "author_display_name": "viewer",
            "message_text": "closing question",
            "priority_class": "chat",
            "status": "active",
        })
        storage.update_event_safety(
            int(event["id"]),
            status="completed",
            label="clean",
            safe_message_text="closing question",
            safety_summary="clean",
            reason="test",
            confidence=1.0,
        )
        manager = YouTubeBridgeManager(storage)
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        manager._runtimes["live-a"] = runtime

        with pytest.raises(ValueError, match="closing/ended"):
            await manager.inject_recent(
                "live-a",
                event_ids=[event["id"]],
                source="manual_inject",
                priority=200,
            )

        assert runtime.audience_preprocess_wake.is_set() is False


@pytest.mark.asyncio
async def test_inject_recent_preprocessing_requested_event_ids_drive_next_prepare_selection():
    with temp_storage() as storage:
        storage.upsert_connector({"connector_id": "yt", "name": "YouTube", "api_key": "key", "enabled": True})
        plan = sample_plan()
        storage.upsert_live_episode_plan(plan)
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt",
            "display_name": "Live A",
            "status": "running",
            "presentation_enabled": True,
            "tts_enabled": True,
            "episode_plan_id": plan["plan_id"],
            "target_memoria_session_id": "mem-a",
            "character_ids": ["char-a"],
            "max_pending_events": 1,
        })
        storage.update_director_state(
            "live-a",
            director_enabled=True,
            metadata={"planned_state": initial_planned_state(plan)},
        )
        backlog = storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt",
            "youtube_message_id": "backlog-first",
            "author_display_name": "viewer 1",
            "message_text": "older backlog question",
            "priority_class": "chat",
            "status": "active",
        })
        target = storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt",
            "youtube_message_id": "target-second",
            "author_display_name": "viewer 2",
            "message_text": "target queued question?",
            "priority_class": "chat",
            "status": "active",
        })
        for event in (backlog, target):
            storage.update_event_safety(
                int(event["id"]),
                status="completed",
                label="clean",
                safe_message_text=str(event["message_text"]),
                safety_summary="clean",
                reason="test",
                confidence=1.0,
            )
        manager = YouTubeBridgeManager(storage)
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        manager._runtimes["live-a"] = runtime

        result = await manager.inject_recent(
            "live-a",
            event_ids=[target["id"]],
            source="manual_inject",
            priority=200,
        )
        state = storage.get_director_state("live-a")
        decision = manager._episode_plan_next_audience_prepare_decision(
            storage.get_session("live-a"),
            state,
        )

        assert result["interaction"]["status"] == "queued_for_audience_preprocessing"
        assert state["metadata"]["audience_preprocess_requested_event_ids"] == [target["id"]]
        assert decision["episode_plan"]["interrupt_state"]["source_event_ids"] == [target["id"]]
