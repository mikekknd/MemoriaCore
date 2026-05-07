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
async def test_poll_loop_marks_live_chat_ended():
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
            "auto_connect": True,
        })
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())

        await manager.start_session("live-a")
        for _ in range(20):
            if storage.get_session("live-a")["status"] == "ended":
                break
            await asyncio.sleep(0.05)

        session = storage.get_session("live-a")
        assert session["status"] == "ended"
        assert session["finalized_at"]
        assert session["summary_status"] == "pending"
        assert manager.get_status("live-a")["running"] is False
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

@pytest.mark.asyncio
async def test_poll_loop_classifies_and_broadcasts_clean_live_event(monkeypatch):
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
            "auto_connect": True,
        })
        manager = YouTubeBridgeManager(storage, youtube_client=OneMessagePollingClient())
        monkeypatch.setattr(manager, "_memoria_client", lambda: FakeSafetyMemoriaClient())
        queue = await manager.subscribe("live-a")

        await manager.start_session("live-a")
        payloads = []
        for _ in range(40):
            while not queue.empty():
                payloads.append(await queue.get())
            if any(payload.get("type") == "youtube_live_event" for payload in payloads):
                break
            await asyncio.sleep(0.05)
        await manager.stop_session("live-a")

        live_events = [payload["event"] for payload in payloads if payload.get("type") == "youtube_live_event"]
        assert live_events
        assert live_events[0]["message_text"] == "即時測試留言"
        assert live_events[0]["safety_status"] == "completed"
        assert live_events[0]["safety_label"] == "clean"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

@pytest.mark.asyncio
async def test_start_session_without_video_id_uses_test_mode_without_clearing_llm_trace(monkeypatch):
    tmp_dir = _tmp_dir()
    try:
        trace_path = tmp_dir / "runtime" / "llm_trace.jsonl"
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        trace_path.write_text("stale trace\n", encoding="utf-8")
        monkeypatch.setattr(bridge_engine, "DEFAULT_LLM_TRACE_PATH", trace_path)
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
            "auto_inject": True,
        })
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())

        status = await manager.start_session("live-a")

        session = storage.get_session("live-a")
        assert status["running"] is True
        assert status["mode"] == "test"
        assert session["status"] == "running"
        assert session["started_at"]
        assert trace_path.read_text(encoding="utf-8") == "stale trace\n"

        stopped = await manager.stop_session("live-a")
        assert stopped["running"] is False
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

@pytest.mark.asyncio
async def test_start_session_auto_enables_single_connector_from_legacy_disabled_state(monkeypatch):
    tmp_dir = _tmp_dir()
    try:
        trace_path = tmp_dir / "runtime" / "llm_trace.jsonl"
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(bridge_engine, "DEFAULT_LLM_TRACE_PATH", trace_path)
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "youtube-main",
            "display_name": "YouTube Main",
            "enabled": False,
        })
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "youtube-main",
            "display_name": "QA Live",
        })
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())

        status = await manager.start_session("live-a")

        connector = storage.get_connector("youtube-main")
        assert status["running"] is True
        assert connector["enabled"] is True
        await manager.stop_session("live-a")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

@pytest.mark.asyncio
async def test_autostart_skips_finalized_session():
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
            "auto_connect": True,
        })
        storage.update_session_summary_state(
            "live-a",
            summary_status="completed",
            summary_id=1,
            finalized_at="2026-05-03T10:00:00",
        )
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())

        await manager.sync_autostart()

        assert manager.get_status("live-a")["running"] is False
        assert storage.get_session("live-a")["summary_status"] == "completed"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

@pytest.mark.asyncio
async def test_autostart_marks_unavailable_live_session_stopped_without_crashing():
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
            "video_id": "ended-video",
            "auto_connect": True,
            "status": "running",
        })
        manager = YouTubeBridgeManager(storage, youtube_client=ResolveLiveChatFailedClient())

        await manager.sync_autostart()

        assert manager.get_status("live-a")["running"] is False
        assert storage.get_session("live-a")["status"] == "stopped"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

@pytest.mark.asyncio
async def test_real_youtube_session_blocks_manual_and_auto_test_events():
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
            "display_name": "Real YouTube Live",
            "video_id": "real-video",
            "auto_test_events_enabled": True,
            "test_event_use_llm": True,
        })
        manager = YouTubeBridgeManager(storage)

        with pytest.raises(ValueError, match="真實 YouTube 直播不允許插入測試留言"):
            await manager.generate_test_events("live-a", count=1, use_llm=True)

        with pytest.raises(ValueError, match="真實 YouTube 直播不允許插入測試留言"):
            await manager.start_auto_test_events("live-a")

        assert storage.get_session("live-a")["auto_test_events_enabled"] is False
        assert storage.list_events("live-a") == []
        assert manager.get_status("live-a")["auto_test_events_running"] is False
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

@pytest.mark.asyncio
async def test_autostart_finalizes_stale_running_interactions_before_resume():
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
            "auto_connect": True,
            "status": "running",
            "auto_inject": False,
            "auto_test_events_enabled": False,
        })
        stale = storage.create_interaction({
            "session_id": "live-a",
            "source": "super_chat",
            "priority": 260,
            "status": "running",
            "event_ids": [1, 2, 3],
            "memoria_session_id": "mem-a",
            "content": "舊 process 未完成的回應。",
        })
        manager = YouTubeBridgeManager(storage, memoria_client_factory=FakeClosingMemoriaClient)

        await manager.sync_autostart()

        interaction = storage.get_interaction(stale["job_id"])
        assert interaction["status"] == "interrupted"
        assert interaction["reason"] == "server_restarted"
        assert interaction["metadata"]["finalized_by"] == "sync_autostart"
        assert storage.get_active_interaction("live-a") is None
        assert manager.get_status("live-a")["running"] is True
    finally:
        await manager.stop_all()
        shutil.rmtree(tmp_dir, ignore_errors=True)
