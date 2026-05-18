import asyncio
import contextlib
import json
import logging
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
    FakeTTSProvider,
    LiveEndedClient,
    LiveRuntime,
    OffTopicEmbeddingMemoriaClient,
    OneMessagePollingClient,
    ResolveLiveChatFailedClient,
    YouTubeBridgeManager,
    _mark_event_clean,
    _next_queue_event,
    _tmp_dir,
    _wait_until,
    bridge_engine,
    normalize_message,
    temp_storage,
)
from live_episode_plan_contract import initial_planned_state
from test_live_episode_plan_contract import sample_plan
from tts_gpt_sovits import TTSResult


def _episode_plan_characters() -> list[dict]:
    return [
        {"character_id": "host-a", "name": "主持A"},
        {"character_id": "analyst-b", "name": "分析B"},
        {"character_id": "skeptic-c", "name": "質疑C"},
    ]

# Split from test_bridge_engine_director.py: director runtime behavior.

@pytest.mark.asyncio
async def test_audience_gap_prepare_uses_main_session_without_injecting(monkeypatch):
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
            "display_name": "Plan Live",
            "target_memoria_session_id": "mem-main",
            "character_ids": ["host-a", "analyst-b", "skeptic-c"],
            "presentation_enabled": True,
            "tts_enabled": True,
            "presentation_ack_timeout_seconds": 5,
            "status": "running",
        })
        storage.upsert_tts_profile({
            "character_id": "host-a",
            "ref_audio_path": "host-a.wav",
            "prompt_text": "參考語音文字。",
        })
        storage.upsert_live_episode_plan(sample_plan())
        session = storage.bind_episode_plan_to_session("live-a", "plan-general-panel")
        session = storage.update_session_fields("live-a", status="running") or session
        storage.update_director_state(
            "live-a",
            metadata={"last_audience_gap_at": datetime.now().isoformat()},
        )
        captured = {}
        event = storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt-main",
            "youtube_message_id": "audience-gap-prepare-1",
            "message_text": "這一段可以補充一下嗎？",
            "author_display_name": "觀眾A",
            "author_channel_id": "viewer-a",
            "message_type": "textMessageEvent",
            "safety_status": "completed",
            "safety_label": "clean",
            "safe_message_text": "這一段可以補充一下嗎？",
        })
        assert event is not None

        class FakeTTSProvider:
            def synthesize(self, text, profile):
                return TTSResult(ok=True, audio_bytes=f"audio:{text}".encode("utf-8"), audio_format="wav")

        class AudiencePrepareClient:
            def list_characters(self):
                return _episode_plan_characters()

            def chat_stream_sync(self, **kwargs):
                captured.update(kwargs)
                assert kwargs["session_id"] == "mem-main"
                kwargs["on_result"]({
                    "message_id": "audience-msg-1",
                    "reply": "這題可以補充一個重點。",
                    "character_id": "host-a",
                    "character_name": "主持A",
                })
                return {
                    "session_id": kwargs["session_id"],
                    "message_id": "audience-result-1",
                    "reply": "這題可以補充一個重點。",
                }

        manager = YouTubeBridgeManager(
            storage,
            youtube_client=LiveEndedClient(),
            memoria_client_factory=AudiencePrepareClient,
            tts_provider_factory=FakeTTSProvider,
        )
        queue = await manager.subscribe("live-a")
        runtime = manager._runtimes["live-a"]
        runtime.running = True
        runtime.status = "running"

        result = await manager._prepare_next_audience_gap_turn(
            runtime,
            session,
            storage.get_director_state("live-a"),
        )

        assert result is not None
        assert result["interaction"]["source"] == "director_audience_prepare"
        assert result["interaction"]["status"] == "prepared"
        interaction_metadata = result["interaction"]["metadata"]
        assert interaction_metadata["prepare_ready"] is True
        assert interaction_metadata["audience_prepare_started_at"]
        assert interaction_metadata["audience_prepare_completed_at"]
        assert interaction_metadata["prepared_result_count"] == 1
        external_context = captured["external_context"]
        assert "live_episode_plan" not in external_context
        assert "episode_plan_mode" not in external_context["summary"]
        assert "<live_episode_turn_context>" not in external_context["context_text"]
        assert external_context["suppress_external_turn_instruction"] is True
        assert "直播流程 action=reply_chat_batch" not in external_context["context_text"]
        assert "直播進度：" not in external_context["context_text"]
        assert "處理提示：" not in external_context["context_text"]
        assert "觀眾查詢資料狀態" not in external_context["context_text"]
        assert "直播輸出模式" not in external_context["context_text"]
        assert "本輪已安全過濾的聊天室留言內容" in external_context["context_text"]
        assert "觀眾A: 這一段可以補充一下嗎？" in external_context["context_text"]
        assert external_context["context_text"].rstrip().endswith("請簡短回應上面的聊天室留言。")
        assert storage.get_session("live-a")["target_memoria_session_id"] == "mem-main"
        metadata = storage.get_director_state("live-a")["metadata"]
        assert "audience_sidecar_memoria_session_id" not in metadata
        assert metadata["latest_audience_gap_job_id"] == result["interaction"]["job_id"]
        items = storage.list_presentation_items("live-a", statuses={"ready"})
        assert items
        assert items[0]["interaction_job_id"] == result["interaction"]["job_id"]
        assert storage.get_events_by_ids("live-a", [event["id"]])[0]["injected_at"] == ""

        event_types = []
        phases = []
        while not queue.empty():
            event_payload = queue.get_nowait()
            event_types.append(event_payload.get("type"))
            if event_payload.get("type") == "presentation_debug":
                phases.append(event_payload["event"]["phase"])
        assert "presentation_item_ready" not in event_types
        assert "interaction_completed" not in event_types
        assert "director_injected" not in event_types
        assert "item_prefetch_ready" in phases
        assert "item_ready" not in phases
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_audience_gap_prepare_failure_does_not_broadcast_foreground_lifecycle(monkeypatch):
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
            "display_name": "Plan Live",
            "target_memoria_session_id": "mem-main",
            "character_ids": ["host-a", "analyst-b", "skeptic-c"],
            "presentation_enabled": True,
            "tts_enabled": True,
        })
        storage.upsert_live_episode_plan(sample_plan())
        session = storage.bind_episode_plan_to_session("live-a", "plan-general-panel")
        storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt-main",
            "youtube_message_id": "audience-gap-fail-1",
            "message_text": "這一段可以補充一下嗎？",
            "author_display_name": "觀眾A",
            "author_channel_id": "viewer-a",
            "message_type": "textMessageEvent",
            "safety_status": "completed",
            "safety_label": "clean",
            "safe_message_text": "這一段可以補充一下嗎？",
        })

        class FailingAudiencePrepareClient:
            def list_characters(self):
                return _episode_plan_characters()

            def chat_stream_sync(self, **kwargs):
                raise RuntimeError("sidecar prepare failed")

        manager = YouTubeBridgeManager(
            storage,
            youtube_client=LiveEndedClient(),
            memoria_client_factory=FailingAudiencePrepareClient,
        )
        queue = await manager.subscribe("live-a")

        result = await manager._prepare_next_audience_gap_turn(
            LiveRuntime(session_id="live-a"),
            session,
            storage.get_director_state("live-a"),
        )

        assert result is not None
        assert result["interaction"]["source"] == "director_audience_prepare"
        assert result["interaction"]["status"] == "failed"
        assert result["interaction"]["metadata"]["prepare_only"] is True
        assert result["interaction"]["metadata"]["error"] == "sidecar prepare failed"

        event_types = []
        while not queue.empty():
            event_types.append(queue.get_nowait().get("type"))
        assert "interaction_interrupted" not in event_types
        assert "interaction_failed" not in event_types
        assert "interaction_completed" not in event_types
        assert "director_injected" not in event_types
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_director_loop_does_not_schedule_audience_gap_prepare_while_prefetch_in_flight(monkeypatch):
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
            "display_name": "Plan Live",
            "target_memoria_session_id": "mem-main",
            "character_ids": ["host-a", "analyst-b", "skeptic-c"],
            "presentation_enabled": True,
            "tts_enabled": True,
            "auto_inject": False,
        })
        storage.upsert_live_episode_plan(sample_plan())
        storage.bind_episode_plan_to_session("live-a", "plan-general-panel")
        storage.update_director_state(
            "live-a",
            director_enabled=True,
            idle_seconds=10,
            status="running",
            last_director_action_at=(datetime.now() - timedelta(seconds=30)).isoformat(),
        )
        event = storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt-main",
            "youtube_message_id": "audience-gap-prefetch-blocked-1",
            "message_text": "這一段可以補充一下嗎？",
            "author_display_name": "觀眾A",
            "author_channel_id": "viewer-a",
            "message_type": "textMessageEvent",
            "safety_status": "completed",
            "safety_label": "clean",
            "safe_message_text": "這一段可以補充一下嗎？",
            "status": "active",
        })
        assert event is not None
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        runtime.director_prefetch_in_flight = 1
        manager._runtimes["live-a"] = runtime
        calls: list[tuple[str, str]] = []

        async def fake_schedule(prepared_runtime, prepared_session, _state, *, trigger):
            calls.append((prepared_runtime.session_id, trigger))
            return True

        monkeypatch.setattr(manager, "_schedule_audience_gap_prepare_if_needed", fake_schedule)

        task = asyncio.create_task(manager._director_loop(runtime))
        await _wait_until(lambda: storage.get_director_state("live-a")["status"] == "waiting_prefetch")
        runtime.running = False
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        assert calls == []
        assert storage.get_director_state("live-a")["status"] == "waiting_prefetch"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_director_loop_does_not_schedule_audience_gap_prepare_while_interaction_presenting(monkeypatch):
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
            "display_name": "Plan Live",
            "target_memoria_session_id": "mem-main",
            "character_ids": ["host-a", "analyst-b", "skeptic-c"],
            "presentation_enabled": True,
            "tts_enabled": True,
            "auto_inject": False,
        })
        storage.upsert_live_episode_plan(sample_plan())
        storage.bind_episode_plan_to_session("live-a", "plan-general-panel")
        storage.update_director_state(
            "live-a",
            director_enabled=True,
            idle_seconds=10,
            status="running",
            last_director_action_at=(datetime.now() - timedelta(seconds=30)).isoformat(),
        )
        storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt-main",
            "youtube_message_id": "audience-gap-presenting-blocked-1",
            "message_text": "這一段可以補充一下嗎？",
            "author_display_name": "觀眾A",
            "author_channel_id": "viewer-a",
            "message_type": "textMessageEvent",
            "safety_status": "completed",
            "safety_label": "clean",
            "safe_message_text": "這一段可以補充一下嗎？",
            "status": "active",
        })
        storage.create_interaction({
            "session_id": "live-a",
            "source": "director",
            "priority": 50,
            "status": "presenting",
            "content": "展示中。",
        })
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        manager._runtimes["live-a"] = runtime
        calls: list[tuple[str, str]] = []

        async def fake_schedule(prepared_runtime, prepared_session, _state, *, trigger):
            calls.append((prepared_runtime.session_id, trigger))
            return True

        monkeypatch.setattr(manager, "_schedule_audience_gap_prepare_if_needed", fake_schedule)

        task = asyncio.create_task(manager._director_loop(runtime))
        await _wait_until(lambda: storage.get_director_state("live-a")["status"] == "waiting_active_interaction")
        runtime.running = False
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        assert calls == []
        assert storage.get_director_state("live-a")["status"] == "waiting_active_interaction"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_audience_gap_scheduler_does_not_require_auto_inject(monkeypatch):
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
            "display_name": "Plan Live",
            "target_memoria_session_id": "mem-main",
            "character_ids": ["host-a", "analyst-b", "skeptic-c"],
            "presentation_enabled": True,
            "tts_enabled": True,
            "auto_inject": False,
            "status": "running",
        })
        storage.upsert_live_episode_plan(sample_plan())
        session = storage.bind_episode_plan_to_session("live-a", "plan-general-panel")
        storage.update_director_state("live-a", director_enabled=True, status="running")
        event = storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt-main",
            "youtube_message_id": "audience-gap-scheduler-1",
            "message_text": "這一段可以補充一下嗎？",
            "author_display_name": "觀眾A",
            "author_channel_id": "viewer-a",
            "message_type": "textMessageEvent",
            "safety_status": "completed",
            "safety_label": "clean",
            "safe_message_text": "這一段可以補充一下嗎？",
            "status": "active",
        })
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        manager._runtimes["live-a"] = runtime
        emitted: list[dict] = []
        gap_ready = asyncio.Event()
        prepared_decisions: list[dict] = []

        async def capture_broadcast(_session_id, payload):
            emitted.append(payload)
            if payload.get("type") == "director_audience_gap_ready":
                gap_ready.set()

        async def fake_prepare_next_audience_gap_turn(_runtime, _session, _state, *, decision=None):
            assert decision is not None
            prepared_decisions.append(decision)
            return {
                "interaction": {
                    "job_id": "audience-gap-scheduled-job",
                    "source": "director_audience_prepare",
                    "status": "prepared",
                },
            }

        monkeypatch.setattr(manager, "_broadcast", capture_broadcast)
        monkeypatch.setattr(manager, "_prepare_next_audience_gap_turn", fake_prepare_next_audience_gap_turn)

        scheduled = await manager._schedule_audience_gap_prepare_if_needed(
            runtime,
            session,
            storage.get_director_state("live-a"),
            trigger="director_loop",
        )
        assert scheduled is True
        assert runtime.audience_gap_prepare_task is not None
        await asyncio.wait_for(gap_ready.wait(), timeout=1)
        await _wait_until(lambda: runtime.audience_gap_prepare_task is None, timeout=1)

        events_ready = next(payload for payload in emitted if payload.get("type") == "director_audience_events_ready")
        assert events_ready["event_ids"] == [event["id"]]
        assert events_ready["source"] == "chat"
        assert prepared_decisions[0]["episode_plan"]["mode"] == "audience_gap_prepare"
        metadata = storage.get_director_state("live-a")["metadata"]
        assert metadata["audience_prepare_in_flight"] is False
        assert metadata["latest_audience_gap_job_id"] == "audience-gap-scheduled-job"
        assert storage.get_events_by_ids("live-a", [event["id"]])[0]["injected_at"] == ""
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_audience_preprocessing_loop_does_not_prepare_or_broadcast(monkeypatch):
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
            "display_name": "Plan Live",
            "target_memoria_session_id": "mem-main",
            "character_ids": ["host-a", "analyst-b", "skeptic-c"],
            "presentation_enabled": True,
            "tts_enabled": True,
            "status": "running",
        })
        storage.upsert_live_episode_plan(sample_plan())
        session = storage.bind_episode_plan_to_session("live-a", "plan-general-panel")
        storage.update_director_state("live-a", director_enabled=True, status="running")
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        runtime.audience_preprocess_wake.clear()
        manager._runtimes["live-a"] = runtime
        emitted: list[dict] = []
        calls: list[str] = []

        async def capture_broadcast(_session_id, payload):
            emitted.append(payload)

        async def fake_prepare_next_audience_gap_turn(_runtime, _session, _state, *, decision=None):
            calls.append(_session["session_id"])
            return {}

        monkeypatch.setattr(manager, "_broadcast", capture_broadcast)
        monkeypatch.setattr(manager, "_prepare_next_audience_gap_turn", fake_prepare_next_audience_gap_turn)

        task = asyncio.create_task(manager._audience_preprocessing_loop(runtime))
        await asyncio.sleep(0)
        runtime.audience_preprocess_wake.set()
        await asyncio.sleep(0.05)
        runtime.running = False
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        assert calls == []
        assert all(payload.get("type") != "director_audience_preprocessed" for payload in emitted)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_audience_preprocessing_loop_does_not_signal_failed_prepare(monkeypatch):
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
            "display_name": "Plan Live",
            "target_memoria_session_id": "mem-main",
            "character_ids": ["host-a", "analyst-b", "skeptic-c"],
            "presentation_enabled": True,
            "tts_enabled": True,
            "status": "running",
        })
        storage.upsert_live_episode_plan(sample_plan())
        storage.bind_episode_plan_to_session("live-a", "plan-general-panel")
        storage.update_director_state("live-a", director_enabled=True, status="running")
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        runtime.audience_preprocess_wake.clear()
        manager._runtimes["live-a"] = runtime
        emitted: list[dict] = []
        calls: list[str] = []

        async def capture_broadcast(_session_id, payload):
            emitted.append(payload)

        async def fake_prepare_next_audience_gap_turn(_runtime, _session, _state, *, decision=None):
            calls.append(_session["session_id"])
            return {}

        monkeypatch.setattr(manager, "_broadcast", capture_broadcast)
        monkeypatch.setattr(manager, "_prepare_next_audience_gap_turn", fake_prepare_next_audience_gap_turn)

        task = asyncio.create_task(manager._audience_preprocessing_loop(runtime))
        await asyncio.sleep(0)
        runtime.audience_preprocess_wake.set()
        await asyncio.sleep(0.05)
        runtime.running = False
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        assert calls == []
        assert runtime.audience_preprocess_wake.is_set() is False
        assert all(payload.get("type") != "director_audience_preprocessed" for payload in emitted)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_audience_preprocessing_loop_records_failed_prepare_result(monkeypatch):
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
            "display_name": "Plan Live",
            "target_memoria_session_id": "mem-main",
            "character_ids": ["host-a", "analyst-b", "skeptic-c"],
            "presentation_enabled": True,
            "tts_enabled": True,
            "status": "running",
        })
        storage.upsert_live_episode_plan(sample_plan())
        storage.bind_episode_plan_to_session("live-a", "plan-general-panel")
        storage.update_director_state(
            "live-a",
            director_enabled=True,
            status="running",
            metadata={
                "audience_prepare_in_flight": True,
                "last_audience_prepare_error": "previous failure",
            },
        )
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        runtime.audience_preprocess_wake.clear()
        manager._runtimes["live-a"] = runtime
        emitted: list[dict] = []
        calls: list[str] = []

        async def capture_broadcast(_session_id, payload):
            emitted.append(payload)

        async def fake_prepare_next_audience_gap_turn(_runtime, _session, _state, *, decision=None):
            calls.append(_session["session_id"])
            return {}

        monkeypatch.setattr(manager, "_broadcast", capture_broadcast)
        monkeypatch.setattr(manager, "_prepare_next_audience_gap_turn", fake_prepare_next_audience_gap_turn)

        task = asyncio.create_task(manager._audience_preprocessing_loop(runtime))
        await asyncio.sleep(0)
        runtime.audience_preprocess_wake.set()
        await asyncio.sleep(0.05)
        runtime.running = False
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        metadata = storage.get_director_state("live-a")["metadata"]
        assert calls == []
        assert runtime.audience_preprocess_wake.is_set() is False
        assert all(payload.get("type") != "director_audience_preprocessed" for payload in emitted)
        assert metadata["audience_prepare_in_flight"] is True
        assert metadata["last_audience_prepare_error"] == "previous failure"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_audience_preprocessing_loop_discards_prepared_when_session_stops(monkeypatch):
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
            "display_name": "Plan Live",
            "target_memoria_session_id": "mem-main",
            "character_ids": ["host-a", "analyst-b", "skeptic-c"],
            "presentation_enabled": True,
            "tts_enabled": True,
            "status": "running",
        })
        storage.upsert_live_episode_plan(sample_plan())
        storage.bind_episode_plan_to_session("live-a", "plan-general-panel")
        storage.update_director_state("live-a", director_enabled=True, status="running")
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        runtime.audience_preprocess_wake.clear()
        manager._runtimes["live-a"] = runtime
        emitted: list[dict] = []
        calls: list[str] = []

        async def capture_broadcast(_session_id, payload):
            emitted.append(payload)

        async def fake_prepare_next_audience_gap_turn(_runtime, _session, _state, *, decision=None):
            calls.append(_session["session_id"])
            return {}

        monkeypatch.setattr(manager, "_broadcast", capture_broadcast)
        monkeypatch.setattr(manager, "_prepare_next_audience_gap_turn", fake_prepare_next_audience_gap_turn)

        task = asyncio.create_task(manager._audience_preprocessing_loop(runtime))
        await asyncio.sleep(0)
        runtime.audience_preprocess_wake.set()
        await asyncio.sleep(0.05)
        runtime.running = False
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        items = storage.list_presentation_items("live-a", limit=10)
        metadata = storage.get_director_state("live-a")["metadata"]
        assert calls == []
        assert runtime.audience_preprocess_wake.is_set() is False
        assert all(payload.get("type") != "director_audience_preprocessed" for payload in emitted)
        assert items == []
        assert metadata.get("audience_prepare_in_flight") is not True
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_audience_preprocessing_disabled_when_director_disabled():
    tmp_dir = _tmp_dir()
    manager = None
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
            "display_name": "Plan Live",
            "target_memoria_session_id": "mem-main",
            "character_ids": ["host-a", "analyst-b", "skeptic-c"],
            "presentation_enabled": True,
            "tts_enabled": True,
            "status": "running",
        })
        storage.upsert_live_episode_plan(sample_plan())
        session = storage.bind_episode_plan_to_session("live-a", "plan-general-panel")
        storage.update_director_state("live-a", director_enabled=False, status="running")

        class ListCharactersClient:
            def list_characters(self):
                return _episode_plan_characters()

        manager = YouTubeBridgeManager(
            storage,
            youtube_client=LiveEndedClient(),
            memoria_client_factory=ListCharactersClient,
        )
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")

        assert manager._audience_preprocessing_enabled(session) is False
        assert manager._audience_preprocessing_accepts_events(runtime, session) is False
        await manager.start_session("live-a")
        runtime = manager._runtimes["live-a"]
        assert runtime.audience_preprocess_task is None
    finally:
        if manager is not None:
            await manager.stop_all()
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_start_director_starts_audience_preprocessing_worker_after_session_running(monkeypatch):
    tmp_dir = _tmp_dir()
    manager = None
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
            "display_name": "Plan Live",
            "target_memoria_session_id": "mem-main",
            "character_ids": ["host-a", "analyst-b", "skeptic-c"],
            "presentation_enabled": True,
            "tts_enabled": True,
            "status": "running",
        })
        storage.upsert_live_episode_plan(sample_plan())
        storage.bind_episode_plan_to_session("live-a", "plan-general-panel")
        storage.update_director_state("live-a", director_enabled=False, status="stopped")

        class ListCharactersClient:
            def list_characters(self):
                return _episode_plan_characters()

        manager = YouTubeBridgeManager(
            storage,
            youtube_client=LiveEndedClient(),
            memoria_client_factory=ListCharactersClient,
        )
        worker_started = asyncio.Event()
        worker_cancelled = asyncio.Event()

        async def fake_audience_preprocessing_loop(_runtime):
            worker_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                worker_cancelled.set()
                raise

        monkeypatch.setattr(manager, "_audience_preprocessing_loop", fake_audience_preprocessing_loop)

        await manager.start_session("live-a")
        runtime = manager._runtimes["live-a"]
        assert runtime.audience_preprocess_task is None

        await manager.start_director("live-a", idle_seconds=1, kickoff=False)

        assert runtime.audience_preprocess_task is not None
        assert not runtime.audience_preprocess_task.done()
        await asyncio.wait_for(worker_started.wait(), timeout=1)

        await manager.stop_session("live-a")

        assert runtime.audience_preprocess_task is None
        assert worker_cancelled.is_set()
    finally:
        if manager is not None:
            await manager.stop_all()
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_audience_gap_prepare_success_clears_stale_error(monkeypatch):
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
            "display_name": "Plan Live",
            "target_memoria_session_id": "mem-main",
            "character_ids": ["host-a", "analyst-b", "skeptic-c"],
            "presentation_enabled": True,
            "tts_enabled": True,
            "status": "running",
        })
        storage.upsert_live_episode_plan(sample_plan())
        session = storage.bind_episode_plan_to_session("live-a", "plan-general-panel")
        state = storage.update_director_state(
            "live-a",
            director_enabled=True,
            status="running",
            metadata={
                "audience_prepare_in_flight": True,
                "last_audience_prepare_error": "previous failure",
            },
        )
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")

        async def fake_send_director_turn(_session, _state, _decision, **_kwargs):
            return {
                "interaction": {
                    "job_id": "audience-gap-success-job",
                    "source": "director_audience_prepare",
                    "status": "prepared",
                },
                "memoria_result": {"session_id": "mem-main"},
            }

        monkeypatch.setattr(manager, "_send_director_turn", fake_send_director_turn)
        result = await manager._prepare_next_audience_gap_turn(
            runtime,
            session,
            state,
            decision={"action": "reply_chat_batch", "prompt": "回應觀眾。"},
        )

        metadata = storage.get_director_state("live-a")["metadata"]
        assert result["interaction"]["status"] == "prepared"
        assert metadata["audience_prepare_in_flight"] is False
        assert metadata["last_audience_prepare_error"] == ""
        assert "audience_sidecar_memoria_session_id" not in metadata
        assert metadata["latest_audience_gap_job_id"] == "audience-gap-success-job"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_safety_displayed_event_wakes_audience_preprocessing_worker(monkeypatch):
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
            "display_name": "Plan Live",
            "target_memoria_session_id": "mem-main",
            "status": "running",
        })
        pending_event = storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt-main",
            "youtube_message_id": "safety-wake-1",
            "message_text": "這一段可以補充一下嗎？",
            "author_display_name": "觀眾A",
            "author_channel_id": "viewer-a",
            "message_type": "textMessageEvent",
            "safety_status": "pending",
            "status": "active",
        })
        manager = YouTubeBridgeManager(
            storage,
            youtube_client=LiveEndedClient(),
            memoria_client_factory=FakeSafetyMemoriaClient,
        )
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        runtime.audience_preprocess_wake.clear()
        manager._runtimes["live-a"] = runtime
        emitted: list[dict] = []

        async def capture_broadcast(_session_id, payload):
            emitted.append(payload)

        monkeypatch.setattr(manager, "_broadcast", capture_broadcast)

        result = await manager._classify_event_batch("live-a", [pending_event])

        assert result["classified_count"] == 1
        assert runtime.audience_preprocess_wake.is_set()
        assert any(payload.get("type") == "youtube_live_event" for payload in emitted)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_stop_session_cancels_audience_gap_prepare_task():
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
            "display_name": "Plan Live",
            "target_memoria_session_id": "mem-main",
            "status": "running",
        })
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def long_running_prepare():
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        task = asyncio.create_task(long_running_prepare())
        await asyncio.wait_for(started.wait(), timeout=1)
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        runtime.audience_gap_prepare_task = task
        manager._runtimes["live-a"] = runtime

        await manager.stop_session("live-a")

        assert cancelled.is_set()
        assert runtime.audience_gap_prepare_task is None
        assert storage.get_session("live-a")["status"] == "stopped"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_closing_helper_cancels_audience_gap_prepare_task():
    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "enabled": True,
        })
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def long_running_prepare():
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        task = asyncio.create_task(long_running_prepare())
        await asyncio.wait_for(started.wait(), timeout=1)
        runtime = LiveRuntime(session_id="live-a", running=True, status="closing")
        runtime.audience_gap_prepare_task = task

        await manager._stop_runtime_background_tasks_for_closing(runtime)

        assert cancelled.is_set()
        assert runtime.audience_gap_prepare_task is None
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_audience_gap_prepare_does_not_become_prepared_after_session_closing(monkeypatch):
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
            "display_name": "Plan Live",
            "target_memoria_session_id": "mem-main",
            "character_ids": ["host-a", "analyst-b"],
            "presentation_enabled": True,
            "tts_enabled": True,
            "status": "running",
        })
        session = storage.get_session("live-a")
        storage.update_director_state("live-a", director_enabled=True, status="running")
        decision = {
            "action": "reply_chat_batch",
            "prompt": "回應觀眾補充問題。",
            "episode_plan": {
                "mode": "audience_gap_prepare",
                "interrupt_state": {
                    "status": "active",
                    "interrupt_type": "chat",
                    "remaining_interrupt_turns": 1,
                    "source_event_ids": [],
                },
            },
        }

        class ClosingDuringPrepareClient:
            def chat_stream_sync(self, **kwargs):
                kwargs["on_result"]({
                    "message_id": "prepare-msg-1",
                    "reply": "這句不應變成 ready prepared output。",
                    "character_id": "host-a",
                    "character_name": "主持A",
                })
                storage.update_session_fields("live-a", status="closing")
                return {
                    "session_id": "audience-sidecar",
                    "message_id": "prepare-result-1",
                    "reply": "這句不應變成 ready prepared output。",
                }

        monkeypatch.setattr("bridge_engine.MemoriaClient", ClosingDuringPrepareClient)
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())
        runtime = LiveRuntime(session_id="live-a", running=True, status="closing")
        manager._runtimes["live-a"] = runtime

        result = await manager._send_director_turn(
            session,
            storage.get_director_state("live-a"),
            decision,
            prepare_only=True,
            prepare_source="director_audience_prepare",
        )

        interaction = storage.get_interaction(result["interaction"]["job_id"])
        assert interaction["status"] == "interrupted"
        assert interaction["metadata"]["prepare_ready"] is False
        assert interaction["metadata"]["audience_prepare_cancelled_reason"] == "session_not_running"
        assert storage.list_presentation_items("live-a", statuses={"ready"}, limit=10) == []
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_audience_gap_prepare_finishes_during_graceful_closing(monkeypatch):
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
            "display_name": "Plan Live",
            "target_memoria_session_id": "mem-main",
            "character_ids": ["host-a", "analyst-b"],
            "presentation_enabled": True,
            "tts_enabled": True,
            "status": "closing",
        })
        session = storage.get_session("live-a")
        storage.update_director_state("live-a", director_enabled=True, status="finalizing_main_only")
        decision = {
            "action": "reply_chat_batch",
            "prompt": "回應觀眾補充問題。",
            "episode_plan": {
                "mode": "audience_gap_prepare",
                "interrupt_state": {
                    "status": "active",
                    "interrupt_type": "chat",
                    "remaining_interrupt_turns": 1,
                    "source_event_ids": [101, 102],
                },
            },
        }

        class GracefulClosingPrepareClient:
            def chat_stream_sync(self, **kwargs):
                kwargs["on_result"]({
                    "message_id": "prepare-msg-1",
                    "reply": "先接聊天室這兩個補充問題。",
                    "character_id": "host-a",
                    "character_name": "主持A",
                })
                return {
                    "session_id": "audience-sidecar",
                    "message_id": "prepare-result-1",
                    "reply": "先接聊天室這兩個補充問題。",
                }

        monkeypatch.setattr("bridge_engine.MemoriaClient", GracefulClosingPrepareClient)
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())
        runtime = LiveRuntime(session_id="live-a", running=True, status="closing")
        runtime.graceful_closing_requested = True
        manager._runtimes["live-a"] = runtime

        result = await manager._send_director_turn(
            session,
            storage.get_director_state("live-a"),
            decision,
            prepare_only=True,
            prepare_source="director_audience_prepare",
        )

        interaction = storage.get_interaction(result["interaction"]["job_id"])
        ready_items = storage.list_presentation_items("live-a", statuses={"ready"}, limit=10)
        assert interaction["status"] == "prepared"
        assert interaction["metadata"]["prepare_ready"] is True
        assert interaction["metadata"].get("audience_prepare_cancelled_reason", "") == ""
        assert [item["text"] for item in ready_items] == ["先接聊天室這兩個補充問題。"]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_audience_gap_present_ready_items_marks_events_injected_without_session_switch(monkeypatch):
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
            "display_name": "Plan Live",
            "target_memoria_session_id": "mem-main",
            "character_ids": ["host-a", "analyst-b", "skeptic-c"],
            "presentation_enabled": True,
            "tts_enabled": True,
            "presentation_ack_timeout_seconds": 5,
            "status": "running",
        })
        storage.upsert_live_episode_plan(sample_plan())
        session = storage.bind_episode_plan_to_session("live-a", "plan-general-panel")
        storage.update_director_state("live-a", director_enabled=True, status="running")
        event = storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt-main",
            "youtube_message_id": "audience-gap-present-1",
            "message_text": "這一段可以補充一下嗎？",
            "author_display_name": "觀眾A",
            "author_channel_id": "viewer-a",
            "message_type": "textMessageEvent",
            "safety_status": "completed",
            "safety_label": "clean",
            "safe_message_text": "這一段可以補充一下嗎？",
        })
        decision = {
            "action": "reply_chat_batch",
            "prompt": "回應觀眾補充問題。",
            "current_topic": "四月新番",
            "episode_plan": {
                "mode": "audience_gap",
                "backlog_snapshot": {"deferred_event_count": 0},
                "interrupt_state": {
                    "status": "active",
                    "interrupt_type": "chat",
                    "remaining_interrupt_turns": 1,
                    "source_event_ids": [event["id"]],
                },
            },
        }
        interaction = storage.create_interaction({
            "session_id": "live-a",
            "source": "director_audience_prepare",
            "priority": 45,
            "status": "prepared",
            "event_ids": [event["id"]],
            "memoria_session_id": "mem-audience",
            "metadata": {"decision": decision},
        })
        item = storage.create_presentation_item({
            "session_id": "live-a",
            "interaction_job_id": interaction["job_id"],
            "message_id": "audience-msg-1:0",
            "character_id": "host-a",
            "character_name": "主持A",
            "sequence_index": 0,
            "status": "ready",
            "text": "這題可以補充一個重點。",
            "audio_format": "wav",
        })

        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())
        queue = await manager.subscribe("live-a")
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")

        task = asyncio.create_task(manager._present_ready_audience_gap_turn(
            runtime,
            session,
            storage.get_director_state("live-a"),
        ))
        presenting = await asyncio.wait_for(queue.get(), timeout=1)
        assert presenting["type"] == "director_audience_gap_presenting"
        ready = await _next_queue_event(queue, "presentation_item_ready", timeout=1)
        assert ready["item"]["item_id"] == item["item_id"]
        await manager.ack_presentation_item("live-a", item["item_id"])
        updated = await asyncio.wait_for(task, timeout=1)

        assert updated["status"] == "completed"
        assert storage.get_events_by_ids("live-a", [event["id"]])[0]["injected_at"]
        assert storage.get_session("live-a")["target_memoria_session_id"] == "mem-main"
        metadata = storage.get_director_state("live-a")["metadata"]
        assert metadata["last_audience_gap_presented_at"]
        assert metadata["last_audience_gap_at"]

        event_types = ["director_audience_gap_presenting", "presentation_item_ready"]
        while not queue.empty():
            event_types.append(queue.get_nowait().get("type"))
        assert "director_audience_gap_presenting" in event_types
        assert "director_audience_gap_presented" in event_types
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_skipped_audience_gap_items_do_not_mark_events_injected(monkeypatch):
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
            "display_name": "Plan Live",
            "target_memoria_session_id": "mem-main",
            "character_ids": ["host-a", "analyst-b"],
            "presentation_enabled": True,
            "tts_enabled": True,
            "status": "running",
        })
        storage.upsert_live_episode_plan(sample_plan())
        session = storage.bind_episode_plan_to_session("live-a", "plan-general-panel")
        storage.update_director_state("live-a", director_enabled=True, status="running")
        event = storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt-main",
            "youtube_message_id": "audience-gap-skipped-1",
            "message_text": "這一段可以補充一下嗎？",
            "author_display_name": "觀眾A",
            "author_channel_id": "viewer-a",
            "message_type": "textMessageEvent",
            "safety_status": "completed",
            "safety_label": "clean",
            "safe_message_text": "這一段可以補充一下嗎？",
        })
        decision = {
            "action": "reply_chat_batch",
            "prompt": "回應觀眾補充問題。",
            "episode_plan": {
                "mode": "audience_gap",
                "interrupt_state": {
                    "status": "active",
                    "interrupt_type": "chat",
                    "remaining_interrupt_turns": 1,
                    "source_event_ids": [event["id"]],
                },
            },
        }
        interaction = storage.create_interaction({
            "session_id": "live-a",
            "source": "director_audience_prepare",
            "priority": 45,
            "status": "prepared",
            "event_ids": [event["id"]],
            "metadata": {"decision": decision},
        })
        item = storage.create_presentation_item({
            "session_id": "live-a",
            "interaction_job_id": interaction["job_id"],
            "message_id": "audience-skipped-msg-1:0",
            "character_id": "host-a",
            "character_name": "主持A",
            "sequence_index": 0,
            "status": "ready",
            "text": "這題逾時不應標記留言已注入。",
            "audio_format": "wav",
        })

        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")

        async def fake_present(*args, **kwargs):
            storage.update_presentation_item(
                item["item_id"],
                status="skipped",
                error="presentation ack timeout",
            )

        monkeypatch.setattr(manager, "present_prepared_stream_results", fake_present)

        updated = await manager._present_ready_audience_gap_turn(
            runtime,
            session,
            storage.get_director_state("live-a"),
        )

        assert updated["status"] == "completed"
        assert storage.get_events_by_ids("live-a", [event["id"]])[0]["injected_at"] == ""
        metadata = storage.get_interaction(interaction["job_id"])["metadata"]
        assert metadata["audience_gap_presented"] is False
        assert metadata["marked_injected"] == 0
        assert metadata["played_item_count"] == 0
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_audience_gap_present_gate_keeps_prepared_when_cooldown_blocks(monkeypatch):
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
            "display_name": "Plan Live",
            "target_memoria_session_id": "mem-main",
            "character_ids": ["host-a", "analyst-b", "skeptic-c"],
            "presentation_enabled": True,
            "tts_enabled": True,
            "director_audience_interrupt_cooldown_seconds": 60,
        })
        storage.upsert_live_episode_plan(sample_plan())
        session = storage.bind_episode_plan_to_session("live-a", "plan-general-panel")
        storage.update_director_state(
            "live-a",
            director_enabled=True,
            status="running",
            metadata={"last_audience_gap_at": datetime.now().isoformat()},
        )
        event = storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt-main",
            "youtube_message_id": "audience-gap-present-gated-1",
            "message_text": "這一段可以補充一下嗎？",
            "author_display_name": "觀眾A",
            "author_channel_id": "viewer-a",
            "message_type": "textMessageEvent",
            "safety_status": "completed",
            "safety_label": "clean",
            "safe_message_text": "這一段可以補充一下嗎？",
        })
        decision = {
            "action": "reply_chat_batch",
            "prompt": "回應觀眾補充問題。",
            "episode_plan": {
                "mode": "audience_gap",
                "interrupt_state": {
                    "status": "active",
                    "interrupt_type": "chat",
                    "remaining_interrupt_turns": 1,
                    "source_event_ids": [event["id"]],
                },
            },
        }
        interaction = storage.create_interaction({
            "session_id": "live-a",
            "source": "director_audience_prepare",
            "priority": 45,
            "status": "prepared",
            "event_ids": [event["id"]],
            "metadata": {"decision": decision},
        })
        storage.create_presentation_item({
            "session_id": "live-a",
            "interaction_job_id": interaction["job_id"],
            "message_id": "audience-msg-1:0",
            "character_id": "host-a",
            "character_name": "主持A",
            "sequence_index": 0,
            "status": "ready",
            "text": "這題可以補充一個重點。",
            "audio_format": "wav",
        })
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        presented = []

        async def fake_present(*args, **kwargs):
            presented.append((args, kwargs))

        monkeypatch.setattr(manager, "present_prepared_stream_results", fake_present)

        result = await manager._present_ready_audience_gap_turn(
            runtime,
            session,
            storage.get_director_state("live-a"),
        )

        assert result is None
        assert presented == []
        assert storage.get_interaction(interaction["job_id"])["status"] == "prepared"
        assert storage.get_events_by_ids("live-a", [event["id"]])[0]["injected_at"] == ""
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_prefetch_creation_ignores_pending_audience_gap_events(monkeypatch):
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
            "display_name": "Plan Live",
            "target_memoria_session_id": "mem-main",
            "character_ids": ["host-a", "analyst-b", "skeptic-c"],
            "presentation_enabled": True,
            "tts_enabled": True,
            "presentation_ack_timeout_seconds": 5,
        })
        for character_id in ["host-a", "analyst-b", "skeptic-c"]:
            storage.upsert_tts_profile({
                "character_id": character_id,
                "ref_audio_path": f"{character_id}.wav",
                "prompt_text": "參考語音文字。",
            })
        storage.upsert_live_episode_plan(sample_plan())
        session = storage.bind_episode_plan_to_session("live-a", "plan-general-panel")
        pending_event = storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt-main",
            "youtube_message_id": "pending-chat-prefetch-create-1",
            "message_text": "先回我這個問題。",
            "author_display_name": "觀眾C",
            "author_channel_id": "viewer-c",
            "message_type": "textMessageEvent",
            "safety_status": "completed",
            "safety_label": "clean",
            "safe_message_text": "先回我這個問題。",
        })

        class FakeTTSProvider:
            def synthesize(self, text, profile):
                return TTSResult(ok=True, audio_bytes=f"audio:{text}".encode("utf-8"), audio_format="wav")

        memoria_turns = []

        class PrefetchClient:
            def list_characters(self):
                return _episode_plan_characters()

            def chat_stream_sync(self, **kwargs):
                turn_id = kwargs["external_context"]["live_episode_plan"]["turn_id"]
                memoria_turns.append(turn_id)
                kwargs["on_result"]({
                    "message_id": f"{turn_id}-msg-1",
                    "reply": "下一個企劃段落已預載。",
                    "character_id": "analyst-b",
                    "character_name": "分析B",
                })
                return {
                    "session_id": "mem-prefetch",
                    "message_id": f"{turn_id}-result",
                    "reply": "下一個企劃段落已預載。",
                }

        manager = YouTubeBridgeManager(
            storage,
            youtube_client=LiveEndedClient(),
            memoria_client_factory=PrefetchClient,
            tts_provider_factory=FakeTTSProvider,
        )
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        state = storage.get_director_state("live-a")
        current_decision = manager._episode_plan_next_decision(session, state)

        result = await manager._prefetch_next_episode_planned_turn(
            runtime,
            session,
            state,
            current_decision,
        )

        assert result is not None
        assert result["interaction"]["source"] == "director_prefetch"
        assert result["interaction"]["status"] == "prefetched"
        assert result["interaction"].get("reason") != "prefetch_discarded_pending_chat"
        assert result["prepared_results"]
        assert memoria_turns == ["seg_01_turn_02"]
        assert storage.get_events_by_ids("live-a", [pending_event["id"]])[0]["injected_at"] == ""
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_late_audience_batch_waits_for_prefetched_planned_turn_and_uses_main_context(monkeypatch):
    tmp_dir = _tmp_dir()
    task = None
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
            "display_name": "Plan Live",
            "target_memoria_session_id": "mem-main",
            "character_ids": ["host-a", "analyst-b", "skeptic-c"],
            "presentation_enabled": True,
            "tts_enabled": True,
            "presentation_ack_timeout_seconds": 5,
        })
        for character_id in ["host-a", "analyst-b", "skeptic-c"]:
            storage.upsert_tts_profile({
                "character_id": character_id,
                "ref_audio_path": f"{character_id}.wav",
                "prompt_text": "參考語音文字。",
            })
        plan = sample_plan()
        turns = plan["segments"][0]["planned_turn_contracts"]
        turns[0]["dialogue_policy"] = {"min_replies": 1, "max_replies": 1, "autonomy": "strict"}
        turns[0]["speaker_policy"]["selection_mode"] = "fixed"
        turns[0]["speaker_policy"]["allowed_participant_ids"] = ["host-a"]
        turns[1]["dialogue_policy"] = {"min_replies": 1, "max_replies": 1, "autonomy": "strict"}
        turns[1]["speaker_policy"]["selection_mode"] = "fixed"
        turns[1]["speaker_policy"]["allowed_participant_ids"] = ["analyst-b"]
        third_turn = json.loads(json.dumps(turns[1]))
        third_turn["turn_id"] = "seg_01_turn_03"
        third_turn["turn_type"] = "counterpoint"
        third_turn["intent"] = "補一個反方觀點"
        third_turn["speaker_policy"]["allowed_participant_ids"] = ["skeptic-c"]
        turns.append(third_turn)
        plan["segments"][0]["completion_conditions"]["required_turn_types"] = [
            "hook",
            "analysis",
            "counterpoint",
        ]
        storage.upsert_live_episode_plan(plan)
        session = storage.bind_episode_plan_to_session("live-a", "plan-general-panel")
        session = storage.update_session_fields("live-a", status="running") or session
        storage.update_director_state("live-a", director_enabled=True, status="running")

        provider = FakeTTSProvider()
        memoria_calls = []

        class CaptureStreamClient:
            def list_characters(self):
                return _episode_plan_characters()

            def chat_stream_sync(self, **kwargs):
                external_context = kwargs["external_context"]
                if isinstance(external_context.get("live_episode_plan"), dict):
                    turn_id = external_context["live_episode_plan"]["turn_id"]
                    memoria_calls.append({
                        "kind": turn_id,
                        "session_id": kwargs["session_id"],
                    })
                    if turn_id == "seg_01_turn_01":
                        kwargs["on_result"]({
                            "message_id": "msg-a",
                            "reply": "A 企劃句。",
                            "character_id": "host-a",
                            "character_name": "主持A",
                        })
                    else:
                        kwargs["on_result"]({
                            "message_id": "msg-c",
                            "reply": "C 企劃句。",
                            "character_id": "analyst-b",
                            "character_name": "分析B",
                        })
                else:
                    memoria_calls.append({
                        "kind": "audience",
                        "session_id": kwargs["session_id"],
                        "context_text": external_context.get("context_text"),
                    })
                    kwargs["on_result"]({
                        "message_id": "msg-audience",
                        "reply": "回應晚到的留言。",
                        "character_id": "host-a",
                        "character_name": "主持A",
                    })
                return {
                    "session_id": kwargs["session_id"],
                    "message_id": len(memoria_calls),
                    "reply": "ok",
                }

        monkeypatch.setattr("bridge_engine.MemoriaClient", CaptureStreamClient)
        manager = YouTubeBridgeManager(
            storage,
            youtube_client=LiveEndedClient(),
            tts_provider_factory=lambda: provider,
        )
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        manager._runtimes["live-a"] = runtime
        queue = await manager.subscribe("live-a")

        task = asyncio.create_task(manager._director_kickoff(runtime))
        first = await _next_queue_event(queue, "presentation_item_ready", timeout=1)
        assert first["item"]["text"] == "A 企劃句。"
        await _wait_until(
            lambda: [call["kind"] for call in memoria_calls] == ["seg_01_turn_01", "seg_01_turn_02"]
        )

        late_event = storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt-main",
            "youtube_message_id": "late-batch-after-c-generated",
            "message_text": "C 之後可以回這個嗎？",
            "author_display_name": "晚到觀眾",
            "author_channel_id": "viewer-late",
            "message_type": "textMessageEvent",
            "safety_status": "completed",
            "safety_label": "clean",
            "safe_message_text": "C 之後可以回這個嗎？",
        })

        await manager.ack_presentation_item("live-a", first["item"]["item_id"])
        await _next_queue_event(queue, "chat_message", timeout=1)
        second = await _next_queue_event(queue, "presentation_item_ready", timeout=1)
        assert second["item"]["text"] == "C 企劃句。"

        await _wait_until(
            lambda: [call["kind"] for call in memoria_calls]
            == ["seg_01_turn_01", "seg_01_turn_02", "audience"]
        )
        audience_call = memoria_calls[-1]
        assert audience_call["session_id"] == "mem-main"
        assert "晚到觀眾: C 之後可以回這個嗎？" in audience_call["context_text"]
        await _wait_until(lambda: any(
            str(interaction.get("source") or "") == "director_audience_prepare"
            for interaction in storage.list_interactions("live-a", limit=20)
        ))
        assert storage.get_events_by_ids("live-a", [late_event["id"]])[0]["injected_at"] == ""

        await manager.ack_presentation_item("live-a", second["item"]["item_id"])
        await _next_queue_event(queue, "chat_message", timeout=1)
        audience_ready = await _next_queue_event(queue, "presentation_item_ready", timeout=1)
        assert audience_ready["item"]["text"] == "回應晚到的留言。"
        await manager.ack_presentation_item("live-a", audience_ready["item"]["item_id"])
        audience_chat = await _next_queue_event(queue, "chat_message", timeout=1)
        assert audience_chat["message"]["content"] == "回應晚到的留言。"
    finally:
        if task and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_planned_chat_bridge_without_real_events_forbids_simulated_audience(monkeypatch):
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
            "display_name": "Plan Live",
            "target_memoria_session_id": "mem-a",
            "character_ids": ["host-a", "analyst-b", "skeptic-c"],
        })
        plan = sample_plan()
        turn = plan["segments"][0]["planned_turn_contracts"][0]
        turn["turn_type"] = "chat_bridge"
        turn["intent"] = "接 1-2 個聊天室反應或 super chat，承認觀眾偏好差異，並把討論拉回排行榜該怎麼用。"
        turn["output_requirements"]["must_end_with_question"] = True
        turn["output_requirements"]["allow_audience_question"] = True
        plan["segments"][0]["completion_conditions"]["required_turn_types"] = ["chat_bridge"]
        storage.upsert_live_episode_plan(plan)
        session = storage.bind_episode_plan_to_session("live-a", "plan-general-panel")
        captured = {}

        class CaptureStreamClient:
            def list_characters(self):
                return _episode_plan_characters()

            def chat_stream_sync(self, **kwargs):
                captured.update(kwargs)
                return {
                    "session_id": "mem-a",
                    "message_id": 42,
                    "reply": "續話完成。",
                }

        monkeypatch.setattr("bridge_engine.MemoriaClient", CaptureStreamClient)
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())

        await manager._send_director_turn(
            session,
            storage.get_director_state("live-a"),
            manager._episode_planned_turn_decision(
                session,
                storage.get_director_state("live-a"),
            ),
        )

        context = captured["external_context"]["context_text"]
        assert "目前沒有可用的真實聊天室留言或 Super Chat" in context
        assert "禁止杜撰觀眾留言" in context
        assert "接 1-2 個聊天室反應" not in captured["content"]
        assert "目前沒有可用的真實聊天室留言或 Super Chat" in captured["content"]
        assert captured["external_context"]["live_episode_plan"]["output_requirements"]["allow_audience_question"] is False
        assert captured["external_context"]["live_episode_plan"]["output_requirements"]["must_end_with_question"] is False
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_director_loop_does_not_wait_for_episode_plan_gap_after_audience_turn(monkeypatch):
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
            "display_name": "Plan Live",
            "target_memoria_session_id": "mem-a",
            "character_ids": ["host-a", "analyst-b", "skeptic-c"],
        })
        plan = sample_plan()
        first_turn = plan["segments"][0]["planned_turn_contracts"][0]
        first_turn["output_requirements"]["allow_audience_question"] = True
        first_turn["output_requirements"]["must_end_with_question"] = True
        storage.upsert_live_episode_plan(plan)
        session = storage.bind_episode_plan_to_session("live-a", "plan-general-panel")
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())
        planned_state = manager._planned_state_after_episode_turn(
            plan,
            initial_planned_state(plan),
            first_turn,
        )
        storage.update_director_state(
            "live-a",
            director_enabled=True,
            idle_seconds=10,
            status="running",
            last_director_action_at=datetime.now().isoformat(),
            metadata={
                "planned_state": planned_state,
                "last_decision": {
                    "episode_plan": {
                        "mode": "planned_turn",
                        "turn_contract": first_turn,
                    },
                },
            },
        )
        calls: list[str] = []
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")

        async def fake_send(self, session, state, decision):
            calls.append(decision["episode_plan"]["turn_contract"]["turn_id"])
            runtime.running = False
            return {"interaction": {"job_id": "fake-job"}}

        monkeypatch.setattr(YouTubeBridgeManager, "_send_director_turn", fake_send)

        task = asyncio.create_task(manager._director_loop(runtime))
        for _ in range(20):
            if calls:
                break
            await asyncio.sleep(0.05)
        runtime.running = False
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        assert calls == ["seg_01_turn_02"]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_main_thread_presents_ready_audience_only_after_planned_turn_ack(monkeypatch):
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
            "presentation_ack_timeout_seconds": 5,
            "episode_plan_id": plan["plan_id"],
            "target_memoria_session_id": "mem-a",
            "character_ids": ["char-a"],
        })
        state = storage.update_director_state(
            "live-a",
            director_enabled=True,
            metadata={"planned_state": initial_planned_state(plan)},
        )
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        manager = YouTubeBridgeManager(storage, tts_provider_factory=lambda: FakeTTSProvider())
        manager._runtimes["live-a"] = runtime
        queue = await manager.subscribe("live-a")

        audience_event = storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt",
            "youtube_message_id": "audience-gap-1",
            "message_type": "textMessageEvent",
            "author_display_name": "觀眾A",
            "message_text": "這段可以補充嗎？",
            "safe_message_text": "這段可以補充嗎？",
            "safety_status": "completed",
            "safety_label": "clean",
            "status": "active",
        })
        audience_interaction = storage.create_interaction({
            "session_id": "live-a",
            "source": "director_audience_prepare",
            "priority": 45,
            "status": "prepared",
            "event_ids": [audience_event["id"]],
            "memoria_session_id": "mem-a:audience",
            "character_ids": ["char-a"],
            "content": "audience reply",
            "metadata": {"prepare_only": True, "decision": {"action": "reply_chat_batch", "episode_plan": {"mode": "audience_gap"}}},
        })
        audience_item = storage.create_presentation_item({
            "session_id": "live-a",
            "interaction_job_id": audience_interaction["job_id"],
            "message_id": "audience-msg:0",
            "character_id": "char-a",
            "character_name": "角色A",
            "sequence_index": 0,
            "text": "先回應觀眾這句。",
            "status": "ready",
            "audio_path": "ready.wav",
            "audio_format": "wav",
            "metadata": {"source": "director_audience_prepare"},
        })

        planned_message = {
            "message_id": "planned-msg",
            "role": "assistant",
            "content": "主線 planned turn。",
            "character_id": "char-a",
            "character_name": "角色A",
        }
        planned_item = await manager._prepare_presentation_item(
            storage.get_session("live-a"),
            planned_message,
            "主線 planned turn。",
            index=0,
            source="director",
            interaction_job_id="planned-job",
            runtime=runtime,
        )
        present_task = asyncio.create_task(manager.present_prepared_stream_results(
            "live-a",
            [{"message": planned_message, "items": [planned_item]}],
            source="director",
            interaction_job_id="planned-job",
        ))
        ready = await _next_queue_event(queue, "presentation_item_ready", timeout=1)
        assert ready["item"]["item_id"] == planned_item["item_id"]
        assert storage.get_interaction(audience_interaction["job_id"])["status"] == "prepared"

        audience_present_task = asyncio.create_task(
            manager._present_ready_audience_gap_turn(runtime, storage.get_session("live-a"), state)
        )
        with pytest.raises(asyncio.TimeoutError):
            await _next_queue_event(queue, "presentation_item_ready", timeout=0.1)
        assert audience_present_task.done() is False

        await manager.ack_presentation_item("live-a", planned_item["item_id"])
        await present_task
        await audience_present_task

        audience_ready = await _next_queue_event(queue, "presentation_item_ready", timeout=1)
        assert audience_ready["item"]["item_id"] == audience_item["item_id"]


@pytest.mark.asyncio
async def test_ready_audience_item_is_consumed_before_next_new_planned_generation(monkeypatch):
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
            "presentation_ack_timeout_seconds": 5,
            "episode_plan_id": plan["plan_id"],
            "target_memoria_session_id": "mem-a",
            "character_ids": ["char-a"],
        })
        state = storage.update_director_state(
            "live-a",
            director_enabled=True,
            metadata={"planned_state": initial_planned_state(plan)},
        )
        event = storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt",
            "youtube_message_id": "audience-ready-before-plan-1",
            "message_type": "textMessageEvent",
            "author_display_name": "觀眾A",
            "message_text": "先回答這句。",
            "safe_message_text": "先回答這句。",
            "safety_status": "completed",
            "safety_label": "clean",
            "status": "active",
        })
        interaction = storage.create_interaction({
            "session_id": "live-a",
            "source": "director_audience_prepare",
            "priority": 45,
            "status": "prepared",
            "event_ids": [event["id"]],
            "memoria_session_id": "mem-a:audience",
            "character_ids": ["char-a"],
            "content": "audience reply",
            "metadata": {
                "prepare_only": True,
                "decision": {
                    "action": "reply_chat_batch",
                    "episode_plan": {"mode": "audience_gap"},
                },
            },
        })
        item = storage.create_presentation_item({
            "session_id": "live-a",
            "interaction_job_id": interaction["job_id"],
            "message_id": "audience-ready-before-plan:0",
            "character_id": "char-a",
            "character_name": "角色A",
            "sequence_index": 0,
            "text": "已預處理的觀眾回應。",
            "status": "ready",
            "audio_path": "audience.wav",
            "audio_format": "wav",
            "metadata": {"source": "director_audience_prepare"},
        })
        manager = YouTubeBridgeManager(storage, tts_provider_factory=lambda: FakeTTSProvider())
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        manager._runtimes["live-a"] = runtime
        queue = await manager.subscribe("live-a")
        called_new_plan = False

        async def fail_new_plan(*_args, **_kwargs):
            nonlocal called_new_plan
            called_new_plan = True
            raise AssertionError("main thread must consume ready audience before generating another planned turn")

        monkeypatch.setattr(manager, "_send_director_turn", fail_new_plan)
        present_task = asyncio.create_task(
            manager._present_ready_audience_gap_turn(runtime, storage.get_session("live-a"), state)
        )

        ready = await _next_queue_event(queue, "presentation_item_ready", timeout=1)
        assert ready["item"]["item_id"] == item["item_id"]
        assert called_new_plan is False
        await manager.ack_presentation_item("live-a", item["item_id"])
        await asyncio.wait_for(present_task, timeout=1)

        assert called_new_plan is False
        assert storage.get_interaction(interaction["job_id"])["status"] == "completed"
        assert storage.get_presentation_item(item["item_id"])["status"] == "played"


@pytest.mark.asyncio
async def test_after_main_turn_sequence_plays_ready_prefetch_before_ready_audience(monkeypatch):
    with temp_storage() as storage:
        storage.upsert_connector({"connector_id": "yt", "name": "YouTube", "api_key": "key", "enabled": True})
        plan = sample_plan()
        storage.upsert_live_episode_plan(plan)
        session = storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt",
            "display_name": "Live A",
            "status": "running",
            "presentation_enabled": True,
            "tts_enabled": True,
            "presentation_ack_timeout_seconds": 5,
            "episode_plan_id": plan["plan_id"],
            "target_memoria_session_id": "mem-a",
            "character_ids": ["host-a"],
        })
        state = storage.update_director_state(
            "live-a",
            director_enabled=True,
            metadata={"planned_state": initial_planned_state(plan)},
        )
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        manager = YouTubeBridgeManager(storage, tts_provider_factory=lambda: FakeTTSProvider())
        manager._runtimes["live-a"] = runtime
        queue = await manager.subscribe("live-a")

        audience_event = storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt",
            "youtube_message_id": "audience-gap-sequence-1",
            "message_type": "textMessageEvent",
            "author_display_name": "觀眾A",
            "message_text": "先補充這個問題。",
            "safe_message_text": "先補充這個問題。",
            "safety_status": "completed",
            "safety_label": "clean",
            "status": "active",
        })
        audience_interaction = storage.create_interaction({
            "session_id": "live-a",
            "source": "director_audience_prepare",
            "priority": 45,
            "status": "prepared",
            "event_ids": [audience_event["id"]],
            "memoria_session_id": "mem-a:audience",
            "character_ids": ["host-a"],
            "content": "audience reply",
            "metadata": {
                "prepare_only": True,
                "decision": {
                    "action": "reply_chat_batch",
                    "episode_plan": {"mode": "audience_gap"},
                },
            },
        })
        audience_item = storage.create_presentation_item({
            "session_id": "live-a",
            "interaction_job_id": audience_interaction["job_id"],
            "message_id": "audience-sequence-msg:0",
            "character_id": "host-a",
            "character_name": "主持A",
            "sequence_index": 0,
            "text": "先回應觀眾。",
            "status": "ready",
            "audio_path": "audience-ready.wav",
            "audio_format": "wav",
            "metadata": {"source": "director_audience_prepare"},
        })
        prefetch_interaction = storage.create_interaction({
            "session_id": "live-a",
            "source": "director_prefetch",
            "priority": 40,
            "status": "prefetched",
            "memoria_session_id": "live-a:prefetch:planned",
            "metadata": {
                "prefetch_ready": True,
                "main_memoria_session_id": "mem-a",
                "draft_memoria_session_id": "live-a:prefetch:planned",
            },
        })
        prefetch_item = storage.create_presentation_item({
            "session_id": "live-a",
            "interaction_job_id": prefetch_interaction["job_id"],
            "message_id": "prefetch-sequence-msg:0",
            "character_id": "host-a",
            "character_name": "主持A",
            "sequence_index": 0,
            "text": "再播放下一個企劃。",
            "status": "ready",
            "audio_path": "prefetch-ready.wav",
            "audio_format": "wav",
            "metadata": {"source": "director_prefetch"},
        })
        chained_interaction = storage.create_interaction({
            "session_id": "live-a",
            "source": "director_prefetch",
            "priority": 40,
            "status": "prefetched",
            "memoria_session_id": "live-a:prefetch:chained",
            "metadata": {
                "prefetch_ready": True,
                "main_memoria_session_id": "mem-a",
                "draft_memoria_session_id": "live-a:prefetch:chained",
            },
        })
        chained_item = storage.create_presentation_item({
            "session_id": "live-a",
            "interaction_job_id": chained_interaction["job_id"],
            "message_id": "chained-prefetch-sequence-msg:0",
            "character_id": "host-a",
            "character_name": "主持A",
            "sequence_index": 0,
            "text": "再播放已預先生成的下一個企劃。",
            "status": "ready",
            "audio_path": "chained-prefetch-ready.wav",
            "audio_format": "wav",
            "metadata": {"source": "director_prefetch"},
        })
        prefetch_decision = {
            "action": "continue_episode_plan",
            "current_topic": "prefetched topic",
            "episode_plan": {
                "mode": "planned_turn",
                "turn_contract": {"turn_id": "prefetched-turn"},
            },
        }
        prefetch_base_state = {
            **state,
            "metadata": {"planned_state": initial_planned_state(plan)},
        }
        prefetch = {
            "interaction": prefetch_interaction,
            "memoria_result": {
                "session_id": "live-a:prefetch:planned",
                "message_id": "prefetch-result-1",
                "reply": "再播放下一個企劃。",
            },
            "prepared_results": [{
                "message": {
                    "message_id": "prefetch-sequence-msg",
                    "role": "assistant",
                    "content": "再播放下一個企劃。",
                    "character_id": "host-a",
                    "character_name": "主持A",
                },
                "items": [prefetch_item],
            }],
            "decision": prefetch_decision,
            "base_state": prefetch_base_state,
        }
        chained_decision = {
            "action": "continue_episode_plan",
            "current_topic": "chained prefetched topic",
            "episode_plan": {
                "mode": "planned_turn",
                "turn_contract": {"turn_id": "chained-prefetched-turn"},
            },
        }
        chained_base_state = {
            **state,
            "metadata": {"planned_state": initial_planned_state(plan)},
        }
        chained_prefetch = {
            "interaction": chained_interaction,
            "memoria_result": {
                "session_id": "live-a:prefetch:chained",
                "message_id": "chained-prefetch-result-1",
                "reply": "再播放已預先生成的下一個企劃。",
            },
            "prepared_results": [{
                "message": {
                    "message_id": "chained-prefetch-sequence-msg",
                    "role": "assistant",
                    "content": "再播放已預先生成的下一個企劃。",
                    "character_id": "host-a",
                    "character_name": "主持A",
                },
                "items": [chained_item],
            }],
            "decision": chained_decision,
            "base_state": chained_base_state,
        }

        class CommitOnlyMemoriaClient:
            def add_assistant_event(self, **kwargs):
                return {"ok": True, **kwargs}

        async def resolved_prefetch():
            return prefetch

        next_prefetch_calls = 0

        async def next_prefetch_once(*args, **kwargs):
            nonlocal next_prefetch_calls
            next_prefetch_calls += 1
            if next_prefetch_calls == 1:
                return chained_prefetch
            return None

        monkeypatch.setattr("bridge_engine.MemoriaClient", CommitOnlyMemoriaClient)
        monkeypatch.setattr(manager, "_prefetch_next_presentation_turn", next_prefetch_once)
        sequence_task = asyncio.create_task(manager._after_main_turn_sequence(
            runtime,
            session,
            state,
            asyncio.create_task(resolved_prefetch()),
        ))

        first_ready = await _next_queue_event(queue, "presentation_item_ready", timeout=1)
        assert first_ready["item"]["item_id"] == prefetch_item["item_id"]
        with pytest.raises(asyncio.TimeoutError):
            await _next_queue_event(queue, "presentation_item_ready", timeout=0.1)

        await manager.ack_presentation_item("live-a", prefetch_item["item_id"])
        second_ready = await _next_queue_event(queue, "presentation_item_ready", timeout=1)
        assert second_ready["item"]["item_id"] == audience_item["item_id"]
        await manager.ack_presentation_item("live-a", audience_item["item_id"])
        third_ready = await _next_queue_event(queue, "presentation_item_ready", timeout=1)
        assert third_ready["item"]["item_id"] == chained_item["item_id"]
        await manager.ack_presentation_item("live-a", chained_item["item_id"])
        await asyncio.wait_for(sequence_task, timeout=1)
        latest_state = storage.get_director_state("live-a")
        assert latest_state["current_topic"] == "chained prefetched topic"
        assert latest_state["metadata"]["last_decision"] == chained_decision


@pytest.mark.asyncio
async def test_after_main_turn_sequence_keeps_earlier_ready_chained_prefetch_before_later_audience(monkeypatch):
    with temp_storage() as storage:
        storage.upsert_connector({"connector_id": "yt", "name": "YouTube", "api_key": "key", "enabled": True})
        session = storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt",
            "display_name": "Live A",
            "status": "running",
            "presentation_enabled": True,
            "tts_enabled": True,
            "presentation_ack_timeout_seconds": 5,
            "target_memoria_session_id": "mem-a",
            "character_ids": ["host-a"],
        })
        state = storage.update_director_state("live-a", director_enabled=True, metadata={})
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        manager = YouTubeBridgeManager(storage, tts_provider_factory=lambda: FakeTTSProvider())
        manager._runtimes["live-a"] = runtime

        first_interaction = storage.create_interaction({
            "session_id": "live-a",
            "source": "director_prefetch",
            "priority": 40,
            "status": "prefetched",
            "memoria_session_id": "live-a:prefetch:first",
            "metadata": {"prefetch_ready": True},
        })
        storage.create_presentation_item({
            "session_id": "live-a",
            "interaction_job_id": first_interaction["job_id"],
            "message_id": "first-prefetch-msg:0",
            "character_id": "host-a",
            "character_name": "主持A",
            "sequence_index": 0,
            "text": "先播放第一個企劃。",
            "status": "ready",
            "audio_path": "first-prefetch-ready.wav",
            "audio_format": "wav",
            "metadata": {"source": "director_prefetch"},
        })
        chained_interaction = storage.create_interaction({
            "session_id": "live-a",
            "source": "director_prefetch",
            "priority": 40,
            "status": "prefetched",
            "memoria_session_id": "live-a:prefetch:chained",
            "metadata": {"prefetch_ready": True},
        })
        storage.create_presentation_item({
            "session_id": "live-a",
            "interaction_job_id": chained_interaction["job_id"],
            "message_id": "chained-prefetch-msg:0",
            "character_id": "host-a",
            "character_name": "主持A",
            "sequence_index": 0,
            "text": "下一個企劃已經先 ready。",
            "status": "ready",
            "audio_path": "chained-prefetch-ready.wav",
            "audio_format": "wav",
            "metadata": {"source": "director_prefetch"},
        })
        audience_event = storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt",
            "youtube_message_id": "audience-after-chained-prefetch",
            "message_type": "textMessageEvent",
            "author_display_name": "觀眾A",
            "message_text": "這則留言比較晚 ready。",
            "safe_message_text": "這則留言比較晚 ready。",
            "safety_status": "completed",
            "safety_label": "clean",
            "status": "active",
        })
        audience_interaction = storage.create_interaction({
            "session_id": "live-a",
            "source": "director_audience_prepare",
            "priority": 45,
            "status": "prepared",
            "event_ids": [audience_event["id"]],
            "memoria_session_id": "mem-a:audience",
            "character_ids": ["host-a"],
            "content": "audience reply",
            "metadata": {"prepare_only": True},
        })
        storage.create_presentation_item({
            "session_id": "live-a",
            "interaction_job_id": audience_interaction["job_id"],
            "message_id": "audience-later-msg:0",
            "character_id": "host-a",
            "character_name": "主持A",
            "sequence_index": 0,
            "text": "比較晚 ready 的觀眾回應。",
            "status": "ready",
            "audio_path": "audience-later-ready.wav",
            "audio_format": "wav",
            "metadata": {"source": "director_audience_prepare"},
        })

        async def resolved(payload):
            return payload

        first_task = asyncio.create_task(resolved({"interaction": first_interaction}))
        first_task.director_prefetch_job_id = first_interaction["job_id"]
        chained_task = asyncio.create_task(resolved({"interaction": chained_interaction}))
        chained_task.director_prefetch_job_id = chained_interaction["job_id"]

        order: list[str] = []
        audience_presented = False

        async def fake_consume(_runtime, _session, prefetched):
            interaction = prefetched["interaction"]
            job_id = interaction["job_id"]
            if job_id == first_interaction["job_id"]:
                order.append("prefetch:first")
                return {
                    "interaction": first_interaction,
                    "decision": {},
                    "base_state": state,
                    "after_memoria_task": chained_task,
                }
            if job_id == chained_interaction["job_id"]:
                order.append("prefetch:chained")
                return {
                    "interaction": chained_interaction,
                    "decision": {},
                    "base_state": state,
                    "after_memoria_task": None,
                }
            raise AssertionError(f"unexpected prefetch interaction: {job_id}")

        async def fake_update(_runtime, _session, current_state, _consumed, **_kwargs):
            return current_state

        async def fake_present(_runtime, _session, current_state):
            nonlocal audience_presented
            if not audience_presented:
                audience_presented = True
                order.append("audience")
            return current_state

        monkeypatch.setattr(manager, "_consume_prefetched_episode_turn", fake_consume)
        monkeypatch.setattr(manager, "_update_director_state_after_prefetch_consumed", fake_update)
        monkeypatch.setattr(manager, "_present_ready_audience_batch_after_turn", fake_present)

        await manager._after_main_turn_sequence(runtime, session, state, first_task)

        assert order == ["prefetch:first", "prefetch:chained", "audience"]


def test_ready_prepared_items_for_session_reports_ready_prefetch_and_audience_without_mutation():
    with temp_storage() as storage:
        manager = YouTubeBridgeManager(storage)
        audience_interaction = storage.create_interaction({
            "session_id": "live-a",
            "source": "director_audience_prepare",
            "priority": 45,
            "status": "prepared",
            "memoria_session_id": "mem-a:audience",
        })
        prefetch_interaction = storage.create_interaction({
            "session_id": "live-a",
            "source": "director_prefetch",
            "priority": 40,
            "status": "prefetched",
            "memoria_session_id": "mem-a:prefetch",
        })
        ordinary_interaction = storage.create_interaction({
            "session_id": "live-a",
            "source": "director",
            "priority": 40,
            "status": "completed",
            "memoria_session_id": "mem-a",
        })
        audience_item = storage.create_presentation_item({
            "session_id": "live-a",
            "interaction_job_id": audience_interaction["job_id"],
            "message_id": "audience-ready:0",
            "character_id": "host-a",
            "character_name": "主持A",
            "sequence_index": 0,
            "text": "ready audience",
            "status": "ready",
            "audio_path": "audience.wav",
            "audio_format": "wav",
            "metadata": {"source": "director_audience_prepare"},
        })
        prefetch_item = storage.create_presentation_item({
            "session_id": "live-a",
            "interaction_job_id": prefetch_interaction["job_id"],
            "message_id": "prefetch-ready:0",
            "character_id": "host-a",
            "character_name": "主持A",
            "sequence_index": 0,
            "text": "ready prefetch",
            "status": "ready",
            "audio_path": "prefetch.wav",
            "audio_format": "wav",
            "metadata": {"source": "director_prefetch"},
        })
        storage.create_presentation_item({
            "session_id": "live-a",
            "interaction_job_id": ordinary_interaction["job_id"],
            "message_id": "ordinary-ready:0",
            "character_id": "host-a",
            "character_name": "主持A",
            "sequence_index": 0,
            "text": "ordinary ready",
            "status": "ready",
            "audio_path": "ordinary.wav",
            "audio_format": "wav",
            "metadata": {"source": "director"},
        })
        storage.create_presentation_item({
            "session_id": "live-a",
            "interaction_job_id": audience_interaction["job_id"],
            "message_id": "audience-synth:0",
            "character_id": "host-a",
            "character_name": "主持A",
            "sequence_index": 1,
            "text": "not ready",
            "status": "synthesizing",
            "audio_format": "wav",
            "metadata": {"source": "director_audience_prepare"},
        })

        ready = manager._ready_prepared_items_for_session("live-a")

        assert [item["item_id"] for item in ready] == [
            audience_item["item_id"],
            prefetch_item["item_id"],
        ]
        assert storage.get_presentation_item(audience_item["item_id"])["status"] == "ready"
        assert storage.get_presentation_item(prefetch_item["item_id"])["status"] == "ready"


@pytest.mark.asyncio
async def test_after_main_turn_sequence_chains_planned_prefetch_after_ready_audience(monkeypatch):
    with temp_storage() as storage:
        manager = YouTubeBridgeManager(storage)
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        session = {
            "session_id": "live-a",
            "presentation_enabled": True,
            "target_memoria_session_id": "mem-main",
            "character_ids": ["host-a"],
        }
        state = {"session_id": "live-a", "metadata": {"planned_state": {}}}
        event = storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt-main",
            "youtube_message_id": "audience-chain-1",
            "message_type": "textMessageEvent",
            "author_display_name": "觀眾A",
            "message_text": "可以補充一下嗎？",
            "safe_message_text": "可以補充一下嗎？",
            "safety_status": "completed",
            "safety_label": "clean",
            "status": "active",
        })
        audience_decision = {
            "action": "reply_chat_batch",
            "prompt": "回應觀眾補充問題。",
            "episode_plan": {
                "mode": "audience_gap",
                "interrupt_state": {"source_event_ids": [event["id"]]},
            },
        }
        audience_interaction = storage.create_interaction({
            "session_id": "live-a",
            "source": "director_audience_prepare",
            "priority": 45,
            "status": "prepared",
            "event_ids": [event["id"]],
            "memoria_session_id": "mem-main",
            "metadata": {
                "decision": audience_decision,
                "main_memoria_session_id": "mem-main",
                "prepare_only": True,
            },
        })
        storage.create_presentation_item({
            "session_id": "live-a",
            "interaction_job_id": audience_interaction["job_id"],
            "message_id": "audience-chain-msg:0",
            "character_id": "host-a",
            "character_name": "主持A",
            "sequence_index": 0,
            "text": "這題可以補充一個重點。",
            "status": "ready",
            "audio_path": "audience-chain.wav",
            "audio_format": "wav",
            "metadata": {"source": "director_audience_prepare"},
        })

        order: list[str] = []

        async def fake_present(_session_id, prepared_results, **_kwargs):
            order.append("audience-presented")
            for prepared in prepared_results or []:
                for item in prepared.get("items") or []:
                    storage.update_presentation_item(item["item_id"], status="presented")

        async def fake_prefetch(_runtime, prefetch_session, prefetch_state, decision, *, allow_audience):
            order.append("planned-prefetched")
            assert allow_audience is False
            assert prefetch_session["target_memoria_session_id"] == "mem-main"
            assert decision["action"] == "reply_chat_batch"
            planned_decision = {
                "action": "continue_topic",
                "episode_plan": {"mode": "planned_turn", "turn_id": "next-planned"},
            }
            planned_interaction = storage.create_interaction({
                "session_id": "live-a",
                "source": "director_prefetch",
                "priority": 40,
                "status": "prefetched",
                "memoria_session_id": "mem-main",
                "metadata": {
                    "decision": planned_decision,
                    "base_state": prefetch_state,
                    "prefetch_ready": True,
                },
            })
            return {
                "interaction": planned_interaction,
                "memoria_result": {"session_id": "mem-main", "message_id": 42, "reply": "planned"},
                "prepared_results": [],
                "decision": planned_decision,
                "base_state": prefetch_state,
            }

        async def fake_consume(_runtime, _session, prefetched):
            order.append("planned-consumed")
            return {
                **prefetched,
                "interaction": prefetched["interaction"],
                "discarded": False,
                "after_memoria_task": None,
            }

        async def fake_update(_runtime, _session, current_state, _consumed, **_kwargs):
            return current_state

        monkeypatch.setattr(manager, "present_prepared_stream_results", fake_present)
        monkeypatch.setattr(manager, "_prefetch_next_presentation_turn", fake_prefetch)
        monkeypatch.setattr(manager, "_consume_prefetched_episode_turn", fake_consume)
        monkeypatch.setattr(manager, "_update_director_state_after_prefetch_consumed", fake_update)

        await manager._after_main_turn_sequence(runtime, session, state, None)

        assert "audience-presented" in order
        assert "planned-prefetched" in order
        assert "planned-consumed" in order
        assert order.index("planned-prefetched") < order.index("planned-consumed")
        assert storage.get_interaction(audience_interaction["job_id"])["status"] == "completed"


@pytest.mark.asyncio
async def test_after_main_turn_sequence_stops_after_first_audience_drain(monkeypatch):
    with temp_storage() as storage:
        manager = YouTubeBridgeManager(storage)
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        runtime.stop_after_current_turn = True
        session = {"session_id": "live-a", "presentation_enabled": True}
        state = {"session_id": "live-a", "metadata": {}}
        called = {"wait": False}

        async def fake_present(_runtime, _session, current_state):
            return current_state

        async def fake_wait(*args, **kwargs):
            called["wait"] = True
            return {"unexpected": True}

        monkeypatch.setattr(manager, "_present_ready_audience_batch_after_turn", fake_present)
        monkeypatch.setattr(manager, "_await_prefetch_task_ready", fake_wait)

        result = await manager._after_main_turn_sequence(
            runtime,
            session,
            state,
            asyncio.Future(),
        )

        assert result is state
        assert called["wait"] is False


@pytest.mark.asyncio
async def test_after_main_turn_sequence_does_not_preempt_prefetch_with_audience_ready_during_wait(monkeypatch):
    with temp_storage() as storage:
        storage.upsert_connector({"connector_id": "yt", "name": "YouTube", "api_key": "key", "enabled": True})
        plan = sample_plan()
        storage.upsert_live_episode_plan(plan)
        session = storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt",
            "display_name": "Live A",
            "status": "running",
            "presentation_enabled": True,
            "tts_enabled": True,
            "prefetch_wait_timeout_seconds": 1,
            "episode_plan_id": plan["plan_id"],
            "target_memoria_session_id": "mem-a",
            "character_ids": ["host-a"],
        })
        state = storage.update_director_state(
            "live-a",
            director_enabled=True,
            metadata={"planned_state": initial_planned_state(plan)},
        )
        prefetch_interaction = storage.create_interaction({
            "session_id": "live-a",
            "source": "director_prefetch",
            "priority": 40,
            "status": "prefetched",
            "memoria_session_id": "live-a:prefetch:during-wait",
            "metadata": {
                "prefetch_ready": True,
                "main_memoria_session_id": "mem-a",
                "draft_memoria_session_id": "live-a:prefetch:during-wait",
            },
        })
        prefetch_item = storage.create_presentation_item({
            "session_id": "live-a",
            "interaction_job_id": prefetch_interaction["job_id"],
            "message_id": "prefetch-during-wait-msg:0",
            "character_id": "host-a",
            "character_name": "主持A",
            "sequence_index": 0,
            "text": "等候後的企劃句。",
            "status": "ready",
            "audio_path": "prefetch-during-wait.wav",
            "audio_format": "wav",
            "metadata": {"source": "director_prefetch"},
        })
        prefetch = {
            "interaction": prefetch_interaction,
            "memoria_result": {
                "session_id": "live-a:prefetch:during-wait",
                "message_id": "prefetch-result-1",
                "reply": "等候後的企劃句。",
            },
            "prepared_results": [{
                "message": {
                    "message_id": "prefetch-during-wait-msg",
                    "role": "assistant",
                    "content": "等候後的企劃句。",
                    "character_id": "host-a",
                    "character_name": "主持A",
                },
                "items": [prefetch_item],
            }],
        }

        class CommitOnlyMemoriaClient:
            def add_assistant_event(self, **kwargs):
                return {"ok": True, **kwargs}

        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        manager = YouTubeBridgeManager(
            storage,
            memoria_client_factory=CommitOnlyMemoriaClient,
            tts_provider_factory=lambda: FakeTTSProvider(),
        )
        queue = await manager.subscribe("live-a")

        async def fake_wait(_runtime, _prefetch_task, *, timeout_seconds):
            audience_event = storage.save_event({
                "bridge_session_id": "live-a",
                "connector_id": "yt",
                "youtube_message_id": "audience-during-wait-1",
                "message_type": "textMessageEvent",
                "author_display_name": "觀眾A",
                "message_text": "等候時進來的觀眾問題。",
                "safe_message_text": "等候時進來的觀眾問題。",
                "safety_status": "completed",
                "safety_label": "clean",
                "status": "active",
            })
            audience_interaction = storage.create_interaction({
                "session_id": "live-a",
                "source": "director_audience_prepare",
                "priority": 45,
                "status": "prepared",
                "event_ids": [audience_event["id"]],
                "memoria_session_id": "mem-a:audience",
                "character_ids": ["host-a"],
                "content": "audience reply",
                "metadata": {
                    "prepare_only": True,
                    "decision": {
                        "action": "reply_chat_batch",
                        "episode_plan": {"mode": "audience_gap"},
                    },
                },
            })
            storage.create_presentation_item({
                "session_id": "live-a",
                "interaction_job_id": audience_interaction["job_id"],
                "message_id": "audience-during-wait-msg:0",
                "character_id": "host-a",
                "character_name": "主持A",
                "sequence_index": 0,
                "text": "先處理等候時的觀眾。",
                "status": "ready",
                "audio_path": "audience-during-wait.wav",
                "audio_format": "wav",
                "metadata": {"source": "director_audience_prepare"},
            })
            return prefetch

        async def no_next_prefetch(*args, **kwargs):
            return None

        monkeypatch.setattr("bridge_engine.MemoriaClient", CommitOnlyMemoriaClient)
        monkeypatch.setattr(manager, "_await_prefetch_task_ready", fake_wait)
        monkeypatch.setattr(manager, "_prefetch_next_episode_planned_turn", no_next_prefetch)
        monkeypatch.setattr(manager, "_prefetch_next_presentation_turn", no_next_prefetch)

        sequence_task = asyncio.create_task(manager._after_main_turn_sequence(
            runtime,
            session,
            state,
            asyncio.Future(),
        ))

        prefetched_ready = await _next_queue_event(queue, "presentation_item_ready", timeout=1)
        assert prefetched_ready["item"]["item_id"] == prefetch_item["item_id"]
        await manager.ack_presentation_item("live-a", prefetched_ready["item"]["item_id"])

        audience_ready = await _next_queue_event(queue, "presentation_item_ready", timeout=1)
        assert audience_ready["item"]["text"] == "先處理等候時的觀眾。"
        await manager.ack_presentation_item("live-a", audience_ready["item"]["item_id"])
        await asyncio.wait_for(sequence_task, timeout=1)
