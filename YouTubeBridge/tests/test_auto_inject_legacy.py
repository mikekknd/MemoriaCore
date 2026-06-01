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
async def test_build_external_context_falls_back_when_audience_research_is_running(monkeypatch):
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
            "research_enabled": True,
        })
        event = storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt-main",
            "youtube_message_id": "question-a",
            "message_text": "可以查一下現在巴哈熱門排行前三名嗎？",
            "author_display_name": "viewer",
            "author_channel_id": "viewer",
            "message_type": "textMessageEvent",
            "safety_status": "completed",
            "safety_label": "clean",
            "safe_message_text": "可以查一下現在巴哈熱門排行前三名嗎？",
        })
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())
        monkeypatch.setattr(
            manager,
            "_audience_query_intent_from_events",
            lambda _events: {
                "is_factual_question": True,
                "needs_external_search": True,
                "safe_search_allowed": True,
                "sanitized_query": "巴哈姆特 動畫 熱門排行 前三名",
                "topic_scope": "anime_new_release",
                "risk_label": "clean",
                "reason": "fact question",
            },
        )
        monkeypatch.setattr(
            manager,
            "_topic_pack_entries_for_query",
            lambda *_args, **_kwargs: ([], {"top_similarity": None}),
        )
        monkeypatch.setattr(
            manager,
            "_ensure_audience_research_worker",
            lambda *_args, **_kwargs: {"status": "running"},
        )

        context, summary = manager.build_external_context("live-a", event_ids=[event["id"]])

        assert "可以查一下現在巴哈熱門排行前三名嗎" in context["context_text"]
        assert summary["query_resolution"]["research_status"] == "running"
        assert summary["query_resolution"]["fallback_reason"] == "research_incomplete"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_auto_inject_waits_while_presentation_is_active(monkeypatch):
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
            "auto_inject": True,
            "min_pending_events": 1,
            "max_pending_events": 1,
            "inject_interval_seconds": 1,
            "presentation_enabled": True,
        })
        event = storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt-main",
            "youtube_message_id": "msg-a",
            "message_text": "下一個留言要等目前句子播完",
            "author_display_name": "viewer",
            "author_channel_id": "viewer-a",
            "message_type": "textMessageEvent",
        })
        _mark_event_clean(storage, event)
        storage.create_interaction({
            "session_id": "live-a",
            "source": "director",
            "priority": 50,
            "status": "presenting",
        })
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())
        monkeypatch.setattr(manager, "classify_pending_events_serialized", lambda *_args, **_kwargs: asyncio.sleep(0))

        async def forbidden_inject(*_args, **_kwargs):
            raise AssertionError("auto inject must wait while a presentation item is active")

        monkeypatch.setattr(manager, "inject_recent", forbidden_inject)
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        task = asyncio.create_task(manager._auto_inject_loop(runtime))
        await asyncio.sleep(0.2)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_auto_inject_batches_pending_events_into_one_interaction(monkeypatch):
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
            "auto_inject": True,
            "min_pending_events": 1,
            "max_pending_events": 5,
            "inject_interval_seconds": 5,
        })
        event_ids = []
        for index in range(3):
            event = storage.save_event({
                "bridge_session_id": "live-a",
                "connector_id": "yt-main",
                "youtube_message_id": f"msg-{index}",
                "message_text": f"請合併處理留言 {index}",
                "author_display_name": f"viewer-{index}",
                "author_channel_id": f"viewer-{index}",
                "message_type": "textMessageEvent",
            })
            _mark_event_clean(storage, event)
            event_ids.append(event["id"])
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())
        captured: list[list[int]] = []
        seen = asyncio.Event()

        async def capture_inject(_session_id, **kwargs):
            captured.append(list(kwargs.get("event_ids") or []))
            seen.set()
            return {"injected_at": "now"}

        monkeypatch.setattr(manager, "inject_recent", capture_inject)
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")

        task = asyncio.create_task(manager._auto_inject_loop(runtime))
        await asyncio.wait_for(seen.wait(), timeout=1)
        runtime.running = False
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        assert captured == [event_ids]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_legacy_auto_inject_does_not_consume_sc_cooldown_when_active_priority_blocks_interrupt(monkeypatch):
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
            "sc_interrupt_cooldown_seconds": 0,
        })
        event = storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt-main",
            "youtube_message_id": "legacy-sc-same-priority",
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
        storage.create_interaction({
            "session_id": "live-a",
            "source": "director",
            "priority": 320,
            "status": "running",
            "content": "同級高優先級回應中。",
        })
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())
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
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")

        await asyncio.wait_for(manager._auto_inject_loop(runtime), timeout=1)

        assert inject_called is False
        assert runtime.last_auto_inject_at is None
        assert runtime.last_sc_interrupt_at is None
    finally:
        monkeypatch.setattr(engine_injection.asyncio, "sleep", original_sleep)
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_legacy_auto_inject_non_running_active_does_not_consume_sc_cooldown(monkeypatch):
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
            "auto_inject": True,
            "min_pending_events": 1,
            "max_pending_events": 5,
            "inject_interval_seconds": 5,
            "sc_interrupt_cooldown_seconds": 0,
        })
        event = storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt-main",
            "youtube_message_id": "legacy-sc-queued-active",
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
        storage.create_interaction({
            "session_id": "live-a",
            "source": "director",
            "priority": 50,
            "status": "queued",
            "content": "尚未 running 的工作。",
        })
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())
        seen = asyncio.Event()

        async def capture_inject(_session_id, **kwargs):
            assert kwargs["event_ids"] == [event["id"]]
            assert kwargs["source"] == "super_chat"
            assert kwargs["priority"] == 320
            seen.set()
            return {"injected_at": "now"}

        monkeypatch.setattr(manager, "inject_recent", capture_inject)
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")

        task = asyncio.create_task(manager._auto_inject_loop(runtime))
        await asyncio.wait_for(seen.wait(), timeout=1)
        runtime.running = False
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        assert runtime.last_sc_interrupt_at is None
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_injected_episode_plan_comments_resume_next_planned_turn():
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
            "character_ids": ["host-a", "analyst-b", "skeptic-c"],
            "presentation_enabled": False,
        })
        plan = sample_plan()
        storage.upsert_live_episode_plan(plan)
        session = storage.bind_episode_plan_to_session("live-a", "plan-general-panel")
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())
        first_turn = plan["segments"][0]["planned_turn_contracts"][0]
        planned_state = manager._planned_state_after_episode_turn(
            plan,
            manager._episode_plan_and_state(session, storage.get_director_state("live-a"))[1],
            first_turn,
        )
        storage.update_director_state(
            "live-a",
            director_enabled=True,
            status="running",
            metadata={"planned_state": planned_state},
        )
        event_ids = []
        for index in range(2):
            event = storage.save_event({
                "bridge_session_id": "live-a",
                "connector_id": "yt-main",
                "youtube_message_id": f"msg-plan-{index}",
                "message_text": f"想補充剛才那一段 {index}",
                "author_display_name": f"viewer-{index}",
                "author_channel_id": f"viewer-{index}",
                "message_type": "textMessageEvent",
            })
            _mark_event_clean(storage, event)
            event_ids.append(event["id"])

        class CommentReplyClient:
            def chat_stream_sync(self, **kwargs):
                return {
                    "session_id": kwargs.get("session_id") or "mem-a",
                    "message_id": 501,
                    "reply": "已合併回應聊天室補充。",
                }

        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient(), memoria_client_factory=CommentReplyClient)

        await manager.inject_recent(
            "live-a",
            event_ids=event_ids,
            content="請合併回應直播留言。",
            source="auto_inject",
        )
        director_state = storage.get_director_state("live-a")
        next_decision = manager._episode_plan_next_decision(
            storage.get_session("live-a"),
            director_state,
        )

        assert storage.list_events("live-a")[0]["injected_at"]
        assert storage.list_events("live-a")[1]["injected_at"]
        assert next_decision["episode_plan"]["mode"] == "planned_turn"
        assert next_decision["episode_plan"]["turn_contract"]["turn_id"] == "seg_01_turn_02"
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
