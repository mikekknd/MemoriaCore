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
async def test_auto_inject_loop_preprocessing_wakes_worker_without_prepare_or_inject(monkeypatch):
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
        plan = sample_plan()
        storage.upsert_live_episode_plan(plan)
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "target_memoria_session_id": "mem-a",
            "character_ids": ["host-a", "analyst-b", "skeptic-c"],
            "episode_plan_id": plan["plan_id"],
            "status": "running",
            "presentation_enabled": True,
            "auto_inject": True,
            "min_pending_events": 1,
            "max_pending_events": 5,
            "inject_interval_seconds": 5,
        })
        storage.update_director_state("live-a", director_enabled=True, status="running")
        event = storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt-main",
            "youtube_message_id": "comment-preprocess-wake",
            "message_type": "textMessageEvent",
            "author_display_name": "星夜旅人",
            "message_text": "這段想補充一下",
            "safety_status": "completed",
            "safety_label": "clean",
            "safe_message_text": "這段想補充一下",
            "status": "active",
        })
        manager = YouTubeBridgeManager(storage, memoria_client_factory=FakeSafetyMemoriaClient)
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        runtime.audience_preprocess_wake.clear()
        manager._runtimes["live-a"] = runtime

        async def forbidden_prepare(*_args, **_kwargs):
            raise AssertionError("preprocessing auto loop must not call director-owned prepare")

        async def forbidden_inject(*_args, **_kwargs):
            raise AssertionError("preprocessing auto loop must not call legacy inject")

        async def stop_after_sleep(_seconds):
            runtime.running = False
            await original_sleep(0)

        monkeypatch.setattr(manager, "_prepare_director_owned_auto_inject", forbidden_prepare)
        monkeypatch.setattr(manager, "inject_recent", forbidden_inject)
        monkeypatch.setattr(manager, "_auto_inject_delay", lambda *_args, **_kwargs: 0.01)
        monkeypatch.setattr(engine_injection.asyncio, "sleep", stop_after_sleep)

        await asyncio.wait_for(manager._auto_inject_loop(runtime), timeout=1)

        assert runtime.audience_preprocess_wake.is_set()
        assert storage.get_events_by_ids("live-a", [event["id"]])[0]["injected_at"] == ""
    finally:
        monkeypatch.setattr(engine_injection.asyncio, "sleep", original_sleep)
        shutil.rmtree(tmp_dir, ignore_errors=True)
