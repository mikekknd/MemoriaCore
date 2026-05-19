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
from types import SimpleNamespace

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
async def test_presentation_director_prefetches_next_role_before_current_ack(monkeypatch):
    tmp_dir = _tmp_dir()
    task = None
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "enabled": True,
        })
        session = storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "display_name": "QA Live",
            "target_memoria_session_id": "mem-a",
            "character_ids": ["host-a", "analyst-b"],
            "director_group_turn_limit": 7,
            "presentation_enabled": True,
            "tts_enabled": True,
            "presentation_ack_timeout_seconds": 5,
        })
        for character_id in ["host-a", "analyst-b"]:
            storage.upsert_tts_profile({
                "character_id": character_id,
                "ref_audio_path": f"{character_id}.wav",
                "prompt_text": "參考語音文字。",
            })

        generated_markers = []
        captured = {}

        class FakeTTSProvider:
            def __init__(self):
                self.calls = []

            def synthesize(self, text, profile):
                self.calls.append({"text": text, "profile": dict(profile)})
                return TTSResult(ok=True, audio_bytes=f"audio:{text}".encode("utf-8"), audio_format="wav")

            def call_texts(self):
                return [call["text"] for call in self.calls]

        provider = FakeTTSProvider()

        class CaptureStreamClient:
            def chat_stream_sync(self, **kwargs):
                captured.update(kwargs)
                kwargs["on_result"]({
                    "message_id": "msg-host",
                    "reply": "目前角色。",
                    "character_id": "host-a",
                    "character_name": "主持A",
                })
                generated_markers.append("first-returned")
                kwargs["on_result"]({
                    "message_id": "msg-analyst",
                    "reply": "下一角色。",
                    "character_id": "analyst-b",
                    "character_name": "分析B",
                })
                generated_markers.append("second-returned")
                return {
                    "session_id": "mem-a",
                    "message_id": 42,
                    "reply": "回合完成。",
                }

        monkeypatch.setattr("bridge_engine.MemoriaClient", CaptureStreamClient)
        manager = YouTubeBridgeManager(
            storage,
            youtube_client=LiveEndedClient(),
            tts_provider_factory=lambda: provider,
        )
        queue = await manager.subscribe("live-a")

        task = asyncio.create_task(manager._send_director_turn(
            session,
            storage.get_director_state("live-a"),
            {
                "action": "continue_topic",
                "prompt": "請自然延續。",
                "current_topic": "四月新番",
            },
        ))

        first = await _next_queue_event(queue, "presentation_item_ready", timeout=1)
        assert first["item"]["text"] == "目前角色。"
        assert generated_markers == ["first-returned", "second-returned"]
        await _wait_until(lambda: provider.call_texts() == ["目前角色。", "下一角色。"])
        await _wait_until(
            lambda: (
                len(storage.list_presentation_items("live-a")) == 2
                and storage.list_presentation_items("live-a")[1]["status"] == "ready"
            )
        )
        items = storage.list_presentation_items("live-a")
        assert [item["text"] for item in items] == ["目前角色。", "下一角色。"]
        assert items[1]["status"] == "ready"
        assert items[1]["audio_path"]

        await manager.ack_presentation_item("live-a", first["item"]["item_id"])
        first_chat = await _next_queue_event(queue, "chat_message", timeout=1)
        assert first_chat["message"]["content"] == "目前角色。"
        second = await _next_queue_event(queue, "presentation_item_ready", timeout=1)
        assert second["item"]["text"] == "下一角色。"

        await manager.ack_presentation_item("live-a", second["item"]["item_id"])
        await asyncio.wait_for(task, timeout=1)

        external_context = captured["external_context"]
        assert external_context["group_turn_limit"] == 2
        assert external_context["summary"]["group_turn_limit"] == 2
    finally:
        if task and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_prefetch_only_presentation_items_are_debugged_as_waiting_not_playable(monkeypatch):
    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "enabled": True,
        })
        session = storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "target_memoria_session_id": "mem-a",
            "character_ids": ["host-a"],
            "presentation_enabled": True,
            "tts_enabled": True,
        })
        storage.upsert_tts_profile({
            "character_id": "host-a",
            "ref_audio_path": "host-a.wav",
            "prompt_text": "參考語音文字。",
        })

        class FakeTTSProvider:
            def synthesize(self, text, profile):
                return TTSResult(ok=True, audio_bytes=f"audio:{text}".encode("utf-8"), audio_format="wav")

        captured = {}

        class PrefetchStreamClient:
            def chat_stream_sync(self, **kwargs):
                captured.update(kwargs)
                kwargs["on_result"]({
                    "message_id": "msg-prefetch",
                    "reply": "預取句子。",
                    "character_id": "host-a",
                    "character_name": "主持A",
                })
                return {
                    "session_id": "mem-a",
                    "message_id": 42,
                    "reply": "預取完成。",
                }

        monkeypatch.setattr("bridge_engine.MemoriaClient", PrefetchStreamClient)
        manager = YouTubeBridgeManager(
            storage,
            youtube_client=LiveEndedClient(),
            tts_provider_factory=FakeTTSProvider,
        )
        queue = await manager.subscribe("live-a")

        result = await manager._send_director_turn(
            session,
            storage.get_director_state("live-a"),
            {
                "action": "continue_topic",
                "prompt": "請預取下一輪。",
                "current_topic": "四月新番",
            },
            prefetch_only=True,
        )

        item = result["prepared_results"][0]["items"][0]
        assert captured["external_context"]["group_turn_limit"] == 1
        assert captured["external_context"]["summary"]["group_turn_limit"] == 1
        assert item["metadata"]["source"] == "director_prefetch"

        phases = []
        event_types = []
        while not queue.empty():
            event = queue.get_nowait()
            event_types.append(event.get("type"))
            if event.get("type") == "presentation_debug":
                phases.append(event["event"]["phase"])

        assert "presentation_item_ready" not in event_types
        assert "item_prefetch_ready" in phases
        assert "item_ready" not in phases
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_consume_prefetched_episode_turn_uses_turn_pipeline_policy(monkeypatch):
    with temp_storage() as storage:
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "enabled": True,
        })
        session = storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "display_name": "Plan Live",
            "target_memoria_session_id": "mem-main",
            "character_ids": ["host-a", "analyst-b"],
            "presentation_enabled": True,
            "tts_enabled": True,
            "presentation_ack_timeout_seconds": 5,
            "status": "running",
        })
        interaction = storage.create_interaction({
            "session_id": "live-a",
            "source": "director_prefetch",
            "priority": 40,
            "status": "sentinel_ready",
            "memoria_session_id": "mem-main",
            "content": "prefetched planned turn",
            "metadata": {
                "decision": {
                    "action": "planned_turn",
                    "episode_plan": {"mode": "planned_turn"},
                },
                "base_state": {"status": "running"},
            },
        })
        item = storage.create_presentation_item({
            "session_id": "live-a",
            "interaction_job_id": interaction["job_id"],
            "message_id": "prefetch-policy-msg:0",
            "character_id": "host-a",
            "character_name": "主持A",
            "sequence_index": 0,
            "status": "ready",
            "text": "prefetched planned turn",
            "audio_format": "wav",
            "metadata": {"source": "director_prefetch"},
        })
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        presented_sources = []

        async def fake_present_prepared_stream_results(session_id, prepared_results, *, source, interaction_job_id):
            presented_sources.append(source)
            storage.update_presentation_item(item["item_id"], status="played", acked_at="now")

        async def fake_prefetch_next_presentation_turn(*args, **kwargs):
            return None

        sentinel_policy = SimpleNamespace(
            expected_status="sentinel_ready",
            presentation_source="sentinel_director",
            may_chain=True,
            mark_audience_events_injected=False,
            dedicated_closing=False,
        )

        monkeypatch.setattr(
            "engine_director_runtime.prepared_turn_policy_for_interaction",
            lambda current_interaction: sentinel_policy,
        )
        monkeypatch.setattr(manager, "present_prepared_stream_results", fake_present_prepared_stream_results)
        monkeypatch.setattr(manager, "_prefetch_next_presentation_turn", fake_prefetch_next_presentation_turn)

        result = await manager._consume_prefetched_episode_turn(runtime, session, {
            "interaction": interaction,
            "decision": {
                "action": "planned_turn",
                "episode_plan": {"mode": "planned_turn"},
            },
            "base_state": {"status": "running"},
            "memoria_result": {"session_id": "mem-main", "reply": "prefetched planned turn"},
            "prepared_results": [{
                "message": {"content": "prefetched planned turn"},
                "items": [item],
            }],
        })

        assert result and result["discarded"] is False
        assert presented_sources == ["sentinel_director"]
        assert storage.get_interaction(interaction["job_id"])["status"] == "completed"


@pytest.mark.asyncio
async def test_consume_prefetched_episode_turn_refuses_dedicated_closing_policy(monkeypatch):
    with temp_storage() as storage:
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "enabled": True,
        })
        session = storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "display_name": "Plan Live",
            "target_memoria_session_id": "mem-main",
            "character_ids": ["host-a"],
            "presentation_enabled": True,
            "tts_enabled": True,
            "status": "running",
        })
        interaction = storage.create_interaction({
            "session_id": "live-a",
            "source": "director_prefetch",
            "priority": 40,
            "status": "prefetched",
            "memoria_session_id": "mem-main",
            "content": "final closing",
            "metadata": {"decision": {"action": "final_closing"}},
        })
        item = storage.create_presentation_item({
            "session_id": "live-a",
            "interaction_job_id": interaction["job_id"],
            "message_id": "closing-policy-msg:0",
            "character_id": "host-a",
            "character_name": "主持A",
            "sequence_index": 0,
            "status": "ready",
            "text": "final closing",
            "audio_format": "wav",
            "metadata": {"source": "director_prefetch"},
        })
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        dedicated_policy = SimpleNamespace(
            expected_status="prefetched",
            presentation_source="director_closing",
            may_chain=False,
            mark_audience_events_injected=False,
            dedicated_closing=True,
        )

        async def unexpected_present(*args, **kwargs):
            raise AssertionError("dedicated closing policy must not be consumed here")

        monkeypatch.setattr(
            "engine_director_runtime.prepared_turn_policy_for_interaction",
            lambda current_interaction: dedicated_policy,
        )
        monkeypatch.setattr(manager, "present_prepared_stream_results", unexpected_present)

        result = await manager._consume_prefetched_episode_turn(runtime, session, {
            "interaction": interaction,
            "memoria_result": {"session_id": "mem-main", "reply": "final closing"},
            "prepared_results": [{
                "message": {"content": "final closing"},
                "items": [item],
            }],
        })

        assert result is None
        assert storage.get_interaction(interaction["job_id"])["status"] == "prefetched"


@pytest.mark.asyncio
async def test_prefetched_planned_turn_is_not_discarded_when_pending_chat_exists(monkeypatch):
    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "enabled": True,
        })
        session = storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "display_name": "Plan Live",
            "target_memoria_session_id": "mem-main",
            "character_ids": ["host-a", "analyst-b"],
            "presentation_enabled": True,
            "tts_enabled": True,
            "presentation_ack_timeout_seconds": 5,
            "status": "running",
        })
        storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt-main",
            "youtube_message_id": "pending-chat-1",
            "message_text": "這是下一個觀眾問題。",
            "author_display_name": "觀眾B",
            "author_channel_id": "viewer-b",
            "message_type": "textMessageEvent",
            "safety_status": "completed",
            "safety_label": "clean",
            "safe_message_text": "這是下一個觀眾問題。",
        })
        interaction = storage.create_interaction({
            "session_id": "live-a",
            "source": "director_prefetch",
            "priority": 40,
            "status": "prefetched",
            "memoria_session_id": "mem-main",
            "metadata": {"prefetch_ready": True},
        })
        item = storage.create_presentation_item({
            "session_id": "live-a",
            "interaction_job_id": interaction["job_id"],
            "message_id": "prefetch-msg-1:0",
            "character_id": "host-a",
            "character_name": "主持A",
            "sequence_index": 0,
            "status": "ready",
            "text": "下一個企劃段落照常播放。",
            "audio_format": "wav",
        })
        prefetch = {
            "interaction": interaction,
            "memoria_result": {
                "session_id": "mem-main",
                "message_id": "prefetch-result-1",
                "reply": "下一個企劃段落照常播放。",
            },
            "prepared_results": [{
                "message": {
                    "message_id": "prefetch-msg-1",
                    "role": "assistant",
                    "content": "下一個企劃段落照常播放。",
                    "character_id": "host-a",
                    "character_name": "主持A",
                },
                "items": [item],
            }],
        }
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())
        queue = await manager.subscribe("live-a")
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")

        task = asyncio.create_task(manager._consume_prefetched_episode_turn(runtime, session, prefetch))
        ready = await _next_queue_event(queue, "presentation_item_ready", timeout=1)
        assert ready["item"]["item_id"] == item["item_id"]
        await manager.ack_presentation_item("live-a", item["item_id"])
        consumed = await asyncio.wait_for(task, timeout=1)

        assert consumed["discarded"] is False
        assert consumed["interaction"]["status"] == "completed"
        assert storage.get_interaction(interaction["job_id"])["status"] == "completed"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_prefetched_turn_does_not_chain_next_prefetch_when_stop_after_current_turn(monkeypatch):
    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "enabled": True,
        })
        session = storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "display_name": "Plan Live",
            "target_memoria_session_id": "mem-main",
            "character_ids": ["host-a", "analyst-b"],
            "presentation_enabled": True,
            "tts_enabled": True,
            "presentation_ack_timeout_seconds": 5,
            "status": "running",
        })
        interaction = storage.create_interaction({
            "session_id": "live-a",
            "source": "director_prefetch",
            "priority": 40,
            "status": "prefetched",
            "memoria_session_id": "mem-main",
            "metadata": {"prefetch_ready": True},
        })
        item = storage.create_presentation_item({
            "session_id": "live-a",
            "interaction_job_id": interaction["job_id"],
            "message_id": "prefetch-msg-1:0",
            "character_id": "host-a",
            "character_name": "主持A",
            "sequence_index": 0,
            "status": "ready",
            "text": "播放目前這個已準備好的段落。",
            "audio_format": "wav",
        })
        prefetch = {
            "interaction": interaction,
            "memoria_result": {
                "session_id": "mem-main",
                "message_id": "prefetch-result-1",
                "reply": "播放目前這個已準備好的段落。",
            },
            "prepared_results": [{
                "message": {
                    "message_id": "prefetch-msg-1",
                    "role": "assistant",
                    "content": "播放目前這個已準備好的段落。",
                    "character_id": "host-a",
                    "character_name": "主持A",
                },
                "items": [item],
            }],
            "decision": {
                "action": "continue_topic",
                "episode_plan": {"mode": "planned_turn"},
            },
            "base_state": {"metadata": {"planned_state": {"current_turn_index": 1}}},
        }
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())
        scheduled: list[dict] = []

        async def fake_prefetch_next_episode_planned_turn(*args, **kwargs):
            scheduled.append({"args": args, "kwargs": kwargs})
            return None

        monkeypatch.setattr(manager, "_prefetch_next_episode_planned_turn", fake_prefetch_next_episode_planned_turn)
        queue = await manager.subscribe("live-a")
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        runtime.stop_after_current_turn = True

        task = asyncio.create_task(manager._consume_prefetched_episode_turn(runtime, session, prefetch))
        ready = await _next_queue_event(queue, "presentation_item_ready", timeout=1)
        assert ready["item"]["item_id"] == item["item_id"]
        await manager.ack_presentation_item("live-a", item["item_id"])
        consumed = await asyncio.wait_for(task, timeout=1)

        assert consumed["discarded"] is False
        assert "after_memoria_task" not in consumed
        assert scheduled == []
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_skipped_prefetched_planned_turn_is_not_recommitted_to_main_session(monkeypatch):
    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "enabled": True,
        })
        session = storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "display_name": "Plan Live",
            "target_memoria_session_id": "mem-main",
            "character_ids": ["host-a", "analyst-b"],
            "presentation_enabled": True,
            "tts_enabled": True,
            "status": "running",
        })
        interaction = storage.create_interaction({
            "session_id": "live-a",
            "source": "director_prefetch",
            "priority": 40,
            "status": "prefetched",
            "memoria_session_id": "mem-main",
            "metadata": {
                "prefetch_ready": True,
                "main_memoria_session_id": "mem-main",
            },
        })
        item = storage.create_presentation_item({
            "session_id": "live-a",
            "interaction_job_id": interaction["job_id"],
            "message_id": "skipped-msg-1:0",
            "character_id": "host-a",
            "character_name": "主持A",
            "sequence_index": 0,
            "status": "ready",
            "text": "這句逾時不應寫回主 session。",
            "audio_format": "wav",
        })

        class CommitTrackingClient:
            assistant_events: list[dict] = []

            def add_assistant_event(self, **kwargs):
                self.__class__.assistant_events.append(kwargs)
                return {"message_id": 9001}

        CommitTrackingClient.assistant_events.clear()
        manager = YouTubeBridgeManager(
            storage,
            youtube_client=LiveEndedClient(),
            memoria_client_factory=CommitTrackingClient,
        )
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")

        async def fake_present(*args, **kwargs):
            storage.update_presentation_item(
                item["item_id"],
                status="skipped",
                error="presentation ack timeout",
            )

        monkeypatch.setattr(manager, "present_prepared_stream_results", fake_present)

        consumed = await manager._consume_prefetched_episode_turn(
            runtime,
            session,
            {
                "interaction": interaction,
                "memoria_result": {
                    "session_id": "mem-main",
                    "message_id": "skipped-result",
                    "reply": "這句逾時不應寫回主 session。",
                },
                "prepared_results": [{
                    "message": {
                        "message_id": "skipped-msg-1",
                        "role": "assistant",
                        "content": "這句逾時不應寫回主 session。",
                        "character_id": "host-a",
                        "character_name": "主持A",
                    },
                    "items": [item],
                }],
            },
        )

        assert consumed["interaction"]["status"] == "completed"
        assert CommitTrackingClient.assistant_events == []
        metadata = storage.get_interaction(interaction["job_id"])["metadata"]
        assert "played_commit_status" not in metadata
        assert "draft_memoria_session_id" not in metadata
        assert metadata["played_item_count"] == 0
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_prefetched_played_completion_does_not_recommit_to_memoria(monkeypatch):
    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "enabled": True,
        })
        session = storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "display_name": "Plan Live",
            "target_memoria_session_id": "mem-main",
            "character_ids": ["host-a", "analyst-b"],
            "presentation_enabled": True,
            "tts_enabled": True,
            "status": "running",
        })
        interaction = storage.create_interaction({
            "session_id": "live-a",
            "source": "director_prefetch",
            "priority": 40,
            "status": "prefetched",
            "memoria_session_id": "mem-main",
            "metadata": {
                "prefetch_ready": True,
                "main_memoria_session_id": "mem-main",
            },
        })
        item = storage.create_presentation_item({
            "session_id": "live-a",
            "interaction_job_id": interaction["job_id"],
            "message_id": "commit-fails-msg-1:0",
            "character_id": "host-a",
            "character_name": "主持A",
            "sequence_index": 0,
            "status": "ready",
            "text": "這句已播出但 commit 失敗。",
            "audio_format": "wav",
        })

        class FailingCommitClient:
            def add_assistant_event(self, **kwargs):
                raise RuntimeError("memoria write failed")

        manager = YouTubeBridgeManager(
            storage,
            youtube_client=LiveEndedClient(),
            memoria_client_factory=FailingCommitClient,
        )
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")

        async def fake_present(*args, **kwargs):
            storage.update_presentation_item(
                item["item_id"],
                status="played",
                presented_at=datetime.now().isoformat(),
                acked_at=datetime.now().isoformat(),
            )

        monkeypatch.setattr(manager, "present_prepared_stream_results", fake_present)

        consumed = await manager._consume_prefetched_episode_turn(
            runtime,
            session,
            {
                "interaction": interaction,
                "memoria_result": {
                    "session_id": "mem-main",
                    "message_id": "commit-fails-result",
                    "reply": "這句已播出但 commit 失敗。",
                },
                "prepared_results": [{
                    "message": {
                        "message_id": "commit-fails-msg-1",
                        "role": "assistant",
                        "content": "這句已播出但 commit 失敗。",
                        "character_id": "host-a",
                        "character_name": "主持A",
                    },
                    "items": [item],
                }],
            },
        )

        assert consumed["interaction"]["status"] == "completed"
        metadata = storage.get_interaction(interaction["job_id"])["metadata"]
        assert "played_commit_status" not in metadata
        assert "played_commit_error" not in metadata
        assert "draft_memoria_session_id" not in metadata
        assert metadata["played_item_count"] == 1
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_planned_prefetch_uses_main_session_and_never_recommits_after_presentation(monkeypatch):
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
        session["status"] = "running"

        class FakeTTSProvider:
            def synthesize(self, text, profile):
                return TTSResult(ok=True, audio_bytes=f"audio:{text}".encode("utf-8"), audio_format="wav")

        class PrefetchCommitClient:
            chat_session_ids: list[str] = []
            assistant_events: list[dict] = []

            def list_characters(self):
                return _episode_plan_characters()

            def chat_stream_sync(self, **kwargs):
                self.__class__.chat_session_ids.append(kwargs["session_id"])
                assert kwargs["session_id"] == "mem-main"
                kwargs["on_result"]({
                    "message_id": "main-msg-1",
                    "reply": "下一個企劃段落已預載。",
                    "character_id": "analyst-b",
                    "character_name": "分析B",
                    "extracted_entities": ["企劃段落", "預載"],
                })
                return {
                    "session_id": kwargs["session_id"],
                    "message_id": "main-result-1",
                    "reply": "下一個企劃段落已預載。",
                    "extracted_entities": ["企劃段落", "預載"],
                }

            def add_assistant_event(self, **kwargs):
                self.__class__.assistant_events.append(kwargs)
                return {"message_id": 9001}

        PrefetchCommitClient.chat_session_ids.clear()
        PrefetchCommitClient.assistant_events.clear()
        manager = YouTubeBridgeManager(
            storage,
            youtube_client=LiveEndedClient(),
            memoria_client_factory=PrefetchCommitClient,
            tts_provider_factory=FakeTTSProvider,
        )
        queue = await manager.subscribe("live-a")
        runtime = manager._runtimes["live-a"]
        runtime.running = True
        runtime.status = "running"
        state = storage.get_director_state("live-a")
        current_decision = manager._episode_plan_next_decision(session, state)

        prefetch = await manager._prefetch_next_episode_planned_turn(
            runtime,
            session,
            state,
            current_decision,
        )

        assert prefetch is not None
        assert PrefetchCommitClient.chat_session_ids
        assert PrefetchCommitClient.assistant_events == []
        interaction = storage.get_interaction(prefetch["interaction"]["job_id"])
        metadata = interaction["metadata"]
        assert PrefetchCommitClient.chat_session_ids == ["mem-main"]
        assert interaction["memoria_session_id"] == "mem-main"
        assert metadata["main_memoria_session_id"] == "mem-main"
        assert "draft_memoria_session_id" not in metadata
        assert "played_commit_status" not in metadata
        assert metadata["prefetch_ready"] is True
        assert metadata["prepared_result_count"] == 1

        prefetch["decision"] = {}
        prefetch["base_state"] = {}
        task = asyncio.create_task(manager._consume_prefetched_episode_turn(runtime, session, prefetch))
        ready = await _next_queue_event(queue, "presentation_item_ready", timeout=1)
        await manager.ack_presentation_item("live-a", ready["item"]["item_id"])
        consumed = await asyncio.wait_for(task, timeout=1)

        assert consumed["interaction"]["status"] == "completed"
        assert storage.get_session("live-a")["target_memoria_session_id"] == "mem-main"
        assert PrefetchCommitClient.assistant_events == []
        final_metadata = storage.get_interaction(interaction["job_id"])["metadata"]
        assert final_metadata["played_item_count"] == 1
        assert "played_commit_status" not in final_metadata
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_ended_runtime_prefetch_ready_items_are_not_played_or_committed(monkeypatch):
    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "enabled": True,
        })
        session = storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "display_name": "Plan Live",
            "target_memoria_session_id": "mem-main",
            "character_ids": ["host-a", "analyst-b"],
            "presentation_enabled": True,
            "tts_enabled": True,
            "status": "ended",
        })
        interaction = storage.create_interaction({
            "session_id": "live-a",
            "source": "director_prefetch",
            "priority": 40,
            "status": "prefetched",
            "memoria_session_id": "live-a:prefetch:ended",
            "metadata": {
                "prefetch_ready": True,
                "played_commit_status": "pending",
                "main_memoria_session_id": "mem-main",
                "draft_memoria_session_id": "live-a:prefetch:ended",
            },
        })
        item = storage.create_presentation_item({
            "session_id": "live-a",
            "interaction_job_id": interaction["job_id"],
            "message_id": "ended-msg-1:0",
            "character_id": "host-a",
            "character_name": "主持A",
            "sequence_index": 0,
            "status": "ready",
            "text": "這句不應播放。",
            "audio_format": "wav",
        })

        class CommitTrackingClient:
            assistant_events: list[dict] = []

            def add_assistant_event(self, **kwargs):
                self.__class__.assistant_events.append(kwargs)
                return {"message_id": 9001}

        CommitTrackingClient.assistant_events.clear()
        manager = YouTubeBridgeManager(
            storage,
            youtube_client=LiveEndedClient(),
            memoria_client_factory=CommitTrackingClient,
        )
        runtime = LiveRuntime(session_id="live-a", running=False, status="ended")
        presented = []

        async def fake_present(*args, **kwargs):
            presented.append((args, kwargs))

        monkeypatch.setattr(manager, "present_prepared_stream_results", fake_present)

        consumed = await manager._consume_prefetched_episode_turn(
            runtime,
            session,
            {
                "interaction": interaction,
                "memoria_result": {
                    "session_id": "live-a:prefetch:ended",
                    "message_id": "ended-result",
                    "reply": "這句不應播放。",
                    "extracted_entities": ["不應播放"],
                },
                "prepared_results": [{
                    "message": {
                        "message_id": "ended-msg-1",
                        "role": "assistant",
                        "content": "這句不應播放。",
                        "character_id": "host-a",
                        "character_name": "主持A",
                    },
                    "items": [item],
                }],
            },
        )

        assert consumed is None
        assert presented == []
        assert CommitTrackingClient.assistant_events == []
        assert storage.get_interaction(interaction["job_id"])["status"] == "prefetched"
        assert storage.get_session("live-a")["target_memoria_session_id"] == "mem-main"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_interrupted_prefetch_ready_items_are_not_played_or_committed(monkeypatch):
    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "enabled": True,
        })
        session = storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "display_name": "Plan Live",
            "target_memoria_session_id": "mem-main",
            "character_ids": ["host-a", "analyst-b"],
            "presentation_enabled": True,
            "tts_enabled": True,
        })
        interaction = storage.create_interaction({
            "session_id": "live-a",
            "source": "director_prefetch",
            "priority": 40,
            "status": "interrupted",
            "memoria_session_id": "live-a:prefetch:discarded",
            "metadata": {
                "prefetch_ready": True,
                "played_commit_status": "pending",
                "main_memoria_session_id": "mem-main",
                "draft_memoria_session_id": "live-a:prefetch:discarded",
            },
        })
        item = storage.create_presentation_item({
            "session_id": "live-a",
            "interaction_job_id": interaction["job_id"],
            "message_id": "discarded-msg-1:0",
            "character_id": "host-a",
            "character_name": "主持A",
            "sequence_index": 0,
            "status": "ready",
            "text": "這句不應播放。",
            "audio_format": "wav",
        })

        class CommitTrackingClient:
            assistant_events: list[dict] = []

            def add_assistant_event(self, **kwargs):
                self.__class__.assistant_events.append(kwargs)
                return {"message_id": 9001}

        CommitTrackingClient.assistant_events.clear()
        manager = YouTubeBridgeManager(
            storage,
            youtube_client=LiveEndedClient(),
            memoria_client_factory=CommitTrackingClient,
        )
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        presented = []

        async def fake_present(*args, **kwargs):
            presented.append((args, kwargs))

        monkeypatch.setattr(manager, "present_prepared_stream_results", fake_present)

        consumed = await manager._consume_prefetched_episode_turn(
            runtime,
            session,
            {
                "interaction": interaction,
                "memoria_result": {
                    "session_id": "live-a:prefetch:discarded",
                    "message_id": "discarded-result",
                    "reply": "這句不應播放。",
                },
                "prepared_results": [{
                    "message": {
                        "message_id": "discarded-msg-1",
                        "role": "assistant",
                        "content": "這句不應播放。",
                        "character_id": "host-a",
                        "character_name": "主持A",
                    },
                    "items": [item],
                }],
            },
        )

        assert consumed is None
        assert presented == []
        assert CommitTrackingClient.assistant_events == []
        assert storage.get_interaction(interaction["job_id"])["status"] == "interrupted"
        assert storage.get_session("live-a")["target_memoria_session_id"] == "mem-main"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_presentation_episode_plan_prefetches_next_planned_turn_before_current_ack(monkeypatch):
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
            "target_memoria_session_id": "mem-a",
            "character_ids": ["host-a", "analyst-b"],
            "presentation_enabled": True,
            "tts_enabled": True,
            "presentation_ack_timeout_seconds": 5,
        })
        for character_id in ["host-a", "analyst-b"]:
            storage.upsert_tts_profile({
                "character_id": character_id,
                "ref_audio_path": f"{character_id}.wav",
                "prompt_text": "參考語音文字。",
            })
        plan = sample_plan()
        turns = plan["segments"][0]["planned_turn_contracts"]
        turns[0]["turn_type"] = "opening"
        turns[0]["dialogue_policy"] = {"min_replies": 1, "max_replies": 1, "autonomy": "strict"}
        turns[0]["speaker_policy"]["selection_mode"] = "fixed"
        turns[0]["speaker_policy"]["allowed_participant_ids"] = ["host-a"]
        turns[1]["turn_type"] = "cohost_intro"
        turns[1]["dialogue_policy"] = {"min_replies": 1, "max_replies": 1, "autonomy": "strict"}
        turns[1]["speaker_policy"]["selection_mode"] = "fixed"
        turns[1]["speaker_policy"]["allowed_participant_ids"] = ["analyst-b"]
        plan["segments"][0]["completion_conditions"]["required_turn_types"] = ["opening", "cohost_intro"]
        plan["segments"][0]["completion_conditions"]["optional_turn_types"] = []
        storage.upsert_live_episode_plan(plan)
        session = storage.bind_episode_plan_to_session("live-a", "plan-general-panel")
        session = storage.update_session_fields("live-a", status="running") or session
        storage.update_director_state("live-a", director_enabled=True, status="running")

        class FakeTTSProvider:
            def __init__(self):
                self.calls = []

            def synthesize(self, text, profile):
                self.calls.append({"text": text, "profile": dict(profile)})
                return TTSResult(ok=True, audio_bytes=f"audio:{text}".encode("utf-8"), audio_format="wav")

            def call_texts(self):
                return [call["text"] for call in self.calls]

        provider = FakeTTSProvider()
        memoria_turns = []
        memoria_calls = []

        class CaptureStreamClient:
            def list_characters(self):
                return _episode_plan_characters()

            def chat_stream_sync(self, **kwargs):
                turn_id = kwargs["external_context"]["live_episode_plan"]["turn_id"]
                memoria_turns.append(turn_id)
                memoria_calls.append({
                    "turn_id": turn_id,
                    "session_id": kwargs.get("session_id"),
                    "conversation_history_session_id": kwargs["external_context"].get("conversation_history_session_id"),
                })
                if turn_id == "seg_01_turn_01":
                    kwargs["on_result"]({
                        "message_id": "msg-opening",
                        "reply": "第一企劃句。",
                        "character_id": "host-a",
                        "character_name": "主持A",
                    })
                else:
                    kwargs["on_result"]({
                        "message_id": "msg-cohost",
                        "reply": "第二企劃句。",
                        "character_id": "analyst-b",
                        "character_name": "分析B",
                    })
                return {
                    "session_id": "mem-opening" if turn_id == "seg_01_turn_01" else kwargs["session_id"],
                    "message_id": len(memoria_turns),
                    "reply": f"{turn_id} complete",
                }

        monkeypatch.setattr("bridge_engine.MemoriaClient", CaptureStreamClient)
        manager = YouTubeBridgeManager(
            storage,
            youtube_client=LiveEndedClient(),
            tts_provider_factory=lambda: provider,
        )
        queue = await manager.subscribe("live-a")
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")

        task = asyncio.create_task(manager._director_kickoff(runtime))
        first = await _next_queue_event(queue, "presentation_item_ready", timeout=1)
        assert first["item"]["text"] == "第一企劃句。"

        await _wait_until(lambda: memoria_turns == ["seg_01_turn_01", "seg_01_turn_02"])
        assert memoria_calls[0] == {
            "turn_id": "seg_01_turn_01",
            "session_id": "mem-a",
            "conversation_history_session_id": None,
        }
        assert memoria_calls[1]["turn_id"] == "seg_01_turn_02"
        assert memoria_calls[1]["session_id"] == "mem-opening"
        assert memoria_calls[1]["conversation_history_session_id"] is None
        prefetched_interaction = next(
            item for item in storage.list_interactions("live-a", limit=20)
            if item["source"] == "director_prefetch"
        )
        assert prefetched_interaction["metadata"]["main_memoria_session_id"] == "mem-opening"
        assert "draft_memoria_session_id" not in prefetched_interaction["metadata"]
        await _wait_until(lambda: provider.call_texts() == ["第一企劃句。", "第二企劃句。"])
        await _wait_until(
            lambda: (
                len(storage.list_presentation_items("live-a")) == 2
                and storage.list_presentation_items("live-a")[1]["status"] == "ready"
            )
        )

        items = storage.list_presentation_items("live-a")
        assert [item["text"] for item in items] == ["第一企劃句。", "第二企劃句。"]
        assert items[1]["status"] == "ready"
        assert items[1]["audio_path"]

        await manager.ack_presentation_item("live-a", first["item"]["item_id"])
        first_chat = await _next_queue_event(queue, "chat_message", timeout=1)
        assert first_chat["message"]["content"] == "第一企劃句。"
        second = await _next_queue_event(queue, "presentation_item_ready", timeout=1)
        assert second["item"]["text"] == "第二企劃句。"

        await manager.ack_presentation_item("live-a", second["item"]["item_id"])
        await asyncio.wait_for(task, timeout=1)
        planned_state = storage.get_director_state("live-a")["metadata"]["planned_state"]
        assert planned_state["last_planned_turn_contract_id"] == "seg_01_turn_02"
    finally:
        if task and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_presentation_prefetch_chain_continues_while_prefetched_turn_is_playing(monkeypatch):
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
            "target_memoria_session_id": "mem-a",
            "character_ids": ["host-a", "analyst-b"],
            "presentation_enabled": True,
            "tts_enabled": True,
            "presentation_ack_timeout_seconds": 5,
            "status": "running",
        })
        for character_id in ["host-a", "analyst-b"]:
            storage.upsert_tts_profile({
                "character_id": character_id,
                "ref_audio_path": f"{character_id}.wav",
                "prompt_text": "參考語音文字。",
            })
        plan = sample_plan()
        first_segment = plan["segments"][0]
        first_segment["segment_id"] = "seg_01"
        first_segment["completion_conditions"] = {
            "min_planned_turns": 2,
            "max_planned_turns": 2,
            "required_turn_types": ["opening", "bridge"],
            "optional_turn_types": [],
        }
        first_turn = first_segment["planned_turn_contracts"][0]
        first_turn["turn_id"] = "seg_01_turn_01"
        first_turn["turn_type"] = "opening"
        first_turn["dialogue_policy"] = {"min_replies": 1, "max_replies": 1, "autonomy": "strict"}
        first_turn["speaker_policy"]["selection_mode"] = "fixed"
        first_turn["speaker_policy"]["allowed_participant_ids"] = ["host-a"]
        second_turn = first_segment["planned_turn_contracts"][1]
        second_turn["turn_id"] = "seg_01_turn_02"
        second_turn["turn_type"] = "bridge"
        second_turn["dialogue_policy"] = {"min_replies": 1, "max_replies": 1, "autonomy": "strict"}
        second_turn["speaker_policy"]["selection_mode"] = "fixed"
        second_turn["speaker_policy"]["allowed_participant_ids"] = ["analyst-b"]

        second_segment = json.loads(json.dumps(first_segment))
        second_segment["segment_id"] = "seg_02"
        second_segment["title"] = "第二大區塊"
        second_segment["planned_turn_contracts"] = [second_segment["planned_turn_contracts"][0]]
        third_turn = second_segment["planned_turn_contracts"][0]
        third_turn["turn_id"] = "seg_02_turn_01"
        third_turn["turn_type"] = "next_segment_hook"
        third_turn["intent"] = "跨到下一個大區塊"
        third_turn["dialogue_policy"] = {"min_replies": 1, "max_replies": 1, "autonomy": "strict"}
        third_turn["speaker_policy"]["selection_mode"] = "fixed"
        third_turn["speaker_policy"]["allowed_participant_ids"] = ["host-a"]
        second_segment["completion_conditions"] = {
            "min_planned_turns": 1,
            "max_planned_turns": 1,
            "required_turn_types": ["next_segment_hook"],
            "optional_turn_types": [],
        }
        plan["segments"] = [first_segment, second_segment]
        storage.upsert_live_episode_plan(plan)
        session = storage.bind_episode_plan_to_session("live-a", "plan-general-panel")
        session = storage.update_session_fields("live-a", status="running") or session
        storage.update_director_state("live-a", director_enabled=True, status="running")

        class FakeTTSProvider:
            def __init__(self):
                self.calls = []

            def synthesize(self, text, profile):
                self.calls.append({"text": text, "profile": dict(profile)})
                return TTSResult(ok=True, audio_bytes=f"audio:{text}".encode("utf-8"), audio_format="wav")

            def call_texts(self):
                return [call["text"] for call in self.calls]

        provider = FakeTTSProvider()
        memoria_turns = []
        replies_by_turn = {
            "seg_01_turn_01": ("第一區塊第一句。", "host-a", "主持A"),
            "seg_01_turn_02": ("第一區塊收束句。", "analyst-b", "分析B"),
            "seg_02_turn_01": ("第二區塊開場句。", "host-a", "主持A"),
        }

        class CaptureStreamClient:
            def list_characters(self):
                return _episode_plan_characters()

            def chat_stream_sync(self, **kwargs):
                turn_id = kwargs["external_context"]["live_episode_plan"]["turn_id"]
                memoria_turns.append(turn_id)
                reply, character_id, character_name = replies_by_turn[turn_id]
                kwargs["on_result"]({
                    "message_id": f"msg-{turn_id}",
                    "reply": reply,
                    "character_id": character_id,
                    "character_name": character_name,
                })
                return {
                    "session_id": "mem-a",
                    "message_id": len(memoria_turns),
                    "reply": f"{turn_id} complete",
                }

        monkeypatch.setattr("bridge_engine.MemoriaClient", CaptureStreamClient)
        manager = YouTubeBridgeManager(
            storage,
            youtube_client=LiveEndedClient(),
            tts_provider_factory=lambda: provider,
        )
        queue = await manager.subscribe("live-a")
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")

        task = asyncio.create_task(manager._director_kickoff(runtime))
        first = await _next_queue_event(queue, "presentation_item_ready", timeout=1)
        assert first["item"]["text"] == "第一區塊第一句。"

        await _wait_until(lambda: memoria_turns == ["seg_01_turn_01", "seg_01_turn_02"])
        await manager.ack_presentation_item("live-a", first["item"]["item_id"])
        await _next_queue_event(queue, "chat_message", timeout=1)
        second = await _next_queue_event(queue, "presentation_item_ready", timeout=1)
        assert second["item"]["text"] == "第一區塊收束句。"

        await _wait_until(
            lambda: memoria_turns == ["seg_01_turn_01", "seg_01_turn_02", "seg_02_turn_01"]
        )
        await _wait_until(
            lambda: provider.call_texts() == ["第一區塊第一句。", "第一區塊收束句。", "第二區塊開場句。"]
        )
        await _wait_until(
            lambda: len(storage.list_presentation_items("live-a")) == 3
            and storage.list_presentation_items("live-a")[2]["status"] == "ready"
        )
        items = storage.list_presentation_items("live-a")
        assert [item["text"] for item in items] == ["第一區塊第一句。", "第一區塊收束句。", "第二區塊開場句。"]
        assert items[2]["status"] == "ready"
        assert items[2]["audio_path"]

        await manager.ack_presentation_item("live-a", second["item"]["item_id"])
        await _next_queue_event(queue, "chat_message", timeout=1)
        third = await _next_queue_event(queue, "presentation_item_ready", timeout=1)
        assert third["item"]["text"] == "第二區塊開場句。"

        await manager.ack_presentation_item("live-a", third["item"]["item_id"])
        await asyncio.wait_for(task, timeout=1)
        planned_state = storage.get_director_state("live-a")["metadata"]["planned_state"]
        assert planned_state["last_planned_turn_contract_id"] == "seg_02_turn_01"
        assert planned_state["plan_status"] == "completed"
    finally:
        if task and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_prefetch_chain_yields_presentation_ready_before_next_context_work(monkeypatch):
    tmp_dir = _tmp_dir()
    consume_task = None
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
            "character_ids": ["host-a", "analyst-b"],
            "presentation_enabled": True,
            "tts_enabled": True,
            "presentation_ack_timeout_seconds": 5,
            "status": "running",
        })
        for character_id in ["host-a", "analyst-b"]:
            storage.upsert_tts_profile({
                "character_id": character_id,
                "ref_audio_path": f"{character_id}.wav",
                "prompt_text": "參考語音文字。",
            })

        class FakeTTSProvider:
            def synthesize(self, text, profile):
                return TTSResult(ok=True, audio_bytes=f"audio:{text}".encode("utf-8"), audio_format="wav")

        manager = YouTubeBridgeManager(
            storage,
            youtube_client=LiveEndedClient(),
            tts_provider_factory=FakeTTSProvider,
        )
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        manager._runtimes["live-a"] = runtime
        queue = await manager.subscribe("live-a")
        interaction = storage.create_interaction({
            "session_id": "live-a",
            "source": "director_prefetch",
            "status": "prefetched",
            "memoria_session_id": "mem-a",
            "character_ids": ["host-a", "analyst-b"],
            "content": "prefetched",
            "metadata": {
                "decision": {"action": "continue_topic", "episode_plan": {"mode": "planned_turn"}},
                "base_state": {"metadata": {}},
                "prefetch_only": True,
            },
        })
        prepared = await manager.prepare_stream_result(
            "live-a",
            {
                "message_id": "msg-prefetched",
                "reply": "已預先準備的句子。",
                "character_id": "host-a",
                "character_name": "主持A",
            },
            source="director_prefetch",
            interaction_job_id=interaction["job_id"],
        )

        prefetch_started = asyncio.Event()

        async def blocking_next_prefetch(*_args, **_kwargs):
            prefetch_started.set()
            time.sleep(0.12)
            return None

        monkeypatch.setattr(manager, "_prefetch_next_presentation_turn", blocking_next_prefetch)
        consume_task = asyncio.create_task(
            manager._consume_prefetched_episode_turn(
                runtime,
                storage.get_session("live-a") or {},
                {
                    "interaction": interaction,
                    "prepared_results": [prepared],
                    "decision": {"action": "continue_topic", "episode_plan": {"mode": "planned_turn"}},
                    "base_state": {"metadata": {}},
                    "memoria_result": {"session_id": "mem-a"},
                },
            )
        )

        ready = await _next_queue_event(queue, "presentation_item_ready", timeout=1)
        assert ready["item"]["text"] == "已預先準備的句子。"
        assert not prefetch_started.is_set()

        await manager.ack_presentation_item("live-a", ready["item"]["item_id"])
        await asyncio.wait_for(prefetch_started.wait(), timeout=1)
        await asyncio.wait_for(consume_task, timeout=1)
    finally:
        if consume_task and not consume_task.done():
            consume_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await consume_task
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_audience_context_build_does_not_block_event_loop_during_prefetch(monkeypatch):
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
            "target_memoria_session_id": "mem-a",
            "character_ids": ["host-a", "analyst-b"],
            "presentation_enabled": True,
            "tts_enabled": True,
            "presentation_ack_timeout_seconds": 5,
            "status": "running",
        })
        event = storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt-main",
            "youtube_message_id": "yt-1",
            "message_type": "textMessageEvent",
            "author_display_name": "觀眾",
            "message_text": "這季怪獸8號討論度很高是因為聲優嗎？",
            "status": "active",
            "safety_label": "clean",
            "safety_status": "completed",
            "safe_message_text": "這季怪獸8號討論度很高是因為聲優嗎？",
        })

        class FastClient:
            def chat_stream_sync(self, **kwargs):
                kwargs["on_result"]({
                    "message_id": "msg-audience",
                    "reply": "觀眾留言回應。",
                    "character_id": "host-a",
                    "character_name": "主持A",
                })
                return {"session_id": "mem-a", "message_id": 1, "reply": "觀眾留言回應。"}

        manager = YouTubeBridgeManager(
            storage,
            youtube_client=LiveEndedClient(),
            memoria_client_factory=FastClient,
        )
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        manager._runtimes["live-a"] = runtime

        def slow_build_external_context(*_args, **_kwargs):
            time.sleep(0.12)
            return (
                {
                    "context_text": "觀眾: 這季怪獸8號討論度很高是因為聲優嗎？",
                    "visible_events": [],
                },
                {"event_ids": [event["id"]], "event_count": 1},
            )

        monkeypatch.setattr(manager, "build_external_context", slow_build_external_context)
        decision = {
            "action": "reply_chat_batch",
            "prompt": "觀眾: 這季怪獸8號討論度很高是因為聲優嗎？",
            "episode_plan": {
                "mode": "audience_gap_prepare",
                "interrupt_state": {"source_event_ids": [event["id"]]},
            },
        }
        task = asyncio.create_task(
            manager._send_director_turn(
                storage.get_session("live-a") or {},
                {"metadata": {}},
                decision,
                prepare_only=True,
                prepare_source="director_audience_prepare",
            )
        )
        marker_started = time.perf_counter()
        await asyncio.sleep(0.02)
        marker_elapsed = time.perf_counter() - marker_started

        assert marker_elapsed < 0.08
        await asyncio.wait_for(task, timeout=1)
    finally:
        if task and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_director_loop_waits_for_in_flight_prefetch_before_next_plan_turn(monkeypatch):
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
        storage.upsert_live_episode_plan(sample_plan())
        storage.bind_episode_plan_to_session("live-a", "plan-general-panel")
        storage.update_director_state(
            "live-a",
            director_enabled=True,
            idle_seconds=10,
            status="running",
            last_director_action_at=(datetime.now() - timedelta(seconds=30)).isoformat(),
        )
        calls = []
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        runtime.director_prefetch_in_flight = 1

        async def fake_send(self, session, state, decision):
            calls.append(decision["episode_plan"]["turn_contract"]["turn_id"])
            runtime.running = False
            return {"interaction": {"job_id": "fake-job"}}

        monkeypatch.setattr(YouTubeBridgeManager, "_send_director_turn", fake_send)
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())

        task = asyncio.create_task(manager._director_loop(runtime))
        await asyncio.sleep(0.25)
        runtime.running = False
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        assert calls == []
        assert storage.get_director_state("live-a")["status"] == "waiting_prefetch"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_prefetch_wait_timeout_cancels_task_and_clears_active_prefetch():
    with temp_storage() as storage:
        manager = YouTubeBridgeManager(storage)
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        interaction = storage.create_interaction({
            "session_id": "live-a",
            "source": "director_prefetch",
            "priority": 40,
            "status": "prefetching",
            "memoria_session_id": "live-a:prefetch:timeout",
            "metadata": {"prefetch_ready": False},
        })
        keep_pending = asyncio.Event()

        async def pending_prefetch():
            await keep_pending.wait()
            return {"ready": True}

        prefetch_task = asyncio.create_task(pending_prefetch())
        setattr(prefetch_task, "director_prefetch_job_id", interaction["job_id"])
        try:
            result = await manager._await_prefetch_task_ready(
                runtime,
                prefetch_task,
                timeout_seconds=0.01,
            )

            assert result is None
            assert prefetch_task.cancelled() is True
            assert prefetch_task.done() is True
            assert storage.get_active_interaction("live-a") is None
            updated = storage.get_interaction(interaction["job_id"])
            assert updated["status"] == "interrupted"
            assert updated["reason"] == "prefetch_wait_timeout"
            assert updated["metadata"]["prefetch_wait_timeout"] is True
        finally:
            if not prefetch_task.done():
                prefetch_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await prefetch_task


def test_prefetch_wait_timeout_cleanup_preserves_ready_prefetched_items():
    with temp_storage() as storage:
        manager = YouTubeBridgeManager(storage)
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        base_state = {"session_id": "live-a", "metadata": {"planned_state": {}}}
        decision = {"action": "continue_topic", "episode_plan": {"mode": "planned_turn"}}
        interaction = storage.create_interaction({
            "session_id": "live-a",
            "source": "director_prefetch",
            "priority": 40,
            "status": "prefetched",
            "memoria_session_id": "live-a:prefetch:ready",
            "metadata": {
                "prefetch_ready": True,
                "decision": decision,
                "base_state": base_state,
            },
        })
        item = storage.create_presentation_item({
            "session_id": "live-a",
            "interaction_job_id": interaction["job_id"],
            "message_id": "prefetch-ready-msg:0",
            "character_id": "host-a",
            "character_name": "主持A",
            "sequence_index": 0,
            "text": "已經 ready 的企劃句。",
            "status": "ready",
            "audio_path": "prefetch-ready.wav",
            "audio_format": "wav",
            "metadata": {"source": "director_prefetch"},
        })

        manager._clear_timed_out_prefetch_interactions(runtime, expected_job_id=interaction["job_id"])

        assert storage.get_interaction(interaction["job_id"])["status"] == "prefetched"
        assert storage.get_presentation_item(item["item_id"])["status"] == "ready"


def test_prefetch_wait_timeout_cleanup_cancels_ready_item_while_prefetching():
    with temp_storage() as storage:
        manager = YouTubeBridgeManager(storage)
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        interaction = storage.create_interaction({
            "session_id": "live-a",
            "source": "director_prefetch",
            "priority": 40,
            "status": "prefetching",
            "memoria_session_id": "live-a:prefetch:ready-race",
            "metadata": {"prefetch_ready": False},
        })
        item = storage.create_presentation_item({
            "session_id": "live-a",
            "interaction_job_id": interaction["job_id"],
            "message_id": "prefetch-ready-race-msg:0",
            "character_id": "host-a",
            "character_name": "主持A",
            "sequence_index": 0,
            "text": "prefetching 但已經 ready 的企劃句。",
            "status": "ready",
            "audio_path": "prefetch-ready-race.wav",
            "audio_format": "wav",
            "metadata": {"source": "director_prefetch"},
        })

        manager._clear_timed_out_prefetch_interactions(runtime, expected_job_id=interaction["job_id"])

        updated = storage.get_interaction(interaction["job_id"])
        updated_item = storage.get_presentation_item(item["item_id"])
        assert updated["status"] == "interrupted"
        assert updated["reason"] == "prefetch_wait_timeout"
        assert updated_item["status"] == "cancelled"
        assert updated_item["error"] == "prefetch_wait_timeout"
        assert storage.get_active_interaction("live-a") is None


def test_prefetch_stop_cleanup_preserves_ready_item_for_closing_drain():
    with temp_storage() as storage:
        manager = YouTubeBridgeManager(storage)
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        interaction = storage.create_interaction({
            "session_id": "live-a",
            "source": "director_prefetch",
            "priority": 40,
            "status": "prefetched",
            "memoria_session_id": "live-a:prefetch:stop-ready",
            "metadata": {"prefetch_ready": True},
        })
        item = storage.create_presentation_item({
            "session_id": "live-a",
            "interaction_job_id": interaction["job_id"],
            "message_id": "prefetch-stop-ready-msg:0",
            "character_id": "host-a",
            "character_name": "主持A",
            "sequence_index": 0,
            "text": "停止時已 ready 的企劃句。",
            "status": "ready",
            "audio_path": "prefetch-stop-ready.wav",
            "audio_format": "wav",
            "metadata": {"source": "director_prefetch"},
        })

        manager._clear_timed_out_prefetch_interactions(
            runtime,
            reason="prefetch_stopped_after_current_turn",
            expected_job_id=interaction["job_id"],
        )

        updated = storage.get_interaction(interaction["job_id"])
        updated_item = storage.get_presentation_item(item["item_id"])
        assert updated_item["status"] == "ready"
        assert updated_item["error"] == ""
        assert updated["status"] == "prefetched"
        assert storage.get_active_interaction("live-a")["job_id"] == interaction["job_id"]


@pytest.mark.asyncio
async def test_prefetch_timeout_recovers_complete_prefetched_payload_with_base_state(monkeypatch):
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
            "episode_plan_id": plan["plan_id"],
            "target_memoria_session_id": "mem-a",
            "character_ids": ["host-a"],
        })
        base_state = {
            "session_id": "live-a",
            "current_topic": "base topic",
            "consecutive_ai_turns": 4,
            "metadata": {"planned_state": initial_planned_state(plan)},
        }
        decision = {
            "action": "continue_topic",
            "current_topic": "recovered topic",
            "episode_plan": {
                "mode": "planned_turn",
                "turn_id": "turn-recovered",
                "turn_index": 2,
                "turn_type": "discussion",
                "total_turns": 4,
                "speaker_policy": "fixed",
            },
        }
        interaction = storage.create_interaction({
            "session_id": "live-a",
            "source": "director_prefetch",
            "priority": 40,
            "status": "prefetched",
            "memoria_session_id": "live-a:prefetch:complete",
            "metadata": {
                "prefetch_ready": True,
                "decision": decision,
                "base_state": base_state,
                "main_memoria_session_id": "mem-a",
                "result_message_id": "prefetch-complete-result",
            },
            "reply_text": "完整 recovered prefetched 句。",
        })
        item = storage.create_presentation_item({
            "session_id": "live-a",
            "interaction_job_id": interaction["job_id"],
            "message_id": "prefetch-complete-msg:0",
            "character_id": "host-a",
            "character_name": "主持A",
            "sequence_index": 0,
            "text": "完整 recovered prefetched 句。",
            "status": "ready",
            "audio_path": "prefetch-complete.wav",
            "audio_format": "wav",
            "metadata": {"source": "director_prefetch"},
        })
        item_2 = storage.create_presentation_item({
            "session_id": "live-a",
            "interaction_job_id": interaction["job_id"],
            "message_id": "prefetch-complete-msg:1",
            "character_id": "host-a",
            "character_name": "主持A",
            "sequence_index": 1,
            "text": "第二個 recovered utterance。",
            "status": "ready",
            "audio_path": "prefetch-complete-2.wav",
            "audio_format": "wav",
            "metadata": {"source": "director_prefetch"},
        })
        manager = YouTubeBridgeManager(storage, tts_provider_factory=lambda: FakeTTSProvider())
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        storage.update_director_state("live-a", metadata=base_state["metadata"])

        class CommitOnlyMemoriaClient:
            def add_assistant_event(self, **kwargs):
                return {"ok": True, **kwargs}

        async def pending_prefetch():
            await asyncio.Event().wait()

        async def no_next_prefetch(*args, **kwargs):
            return None

        monkeypatch.setattr("bridge_engine.MemoriaClient", CommitOnlyMemoriaClient)
        monkeypatch.setattr(manager, "_prefetch_next_episode_planned_turn", no_next_prefetch)
        monkeypatch.setattr(manager, "_prefetch_next_presentation_turn", no_next_prefetch)
        queue = await manager.subscribe("live-a")
        prefetch_task = asyncio.create_task(pending_prefetch())
        setattr(prefetch_task, "director_prefetch_job_id", interaction["job_id"])
        try:
            recovered = await manager._await_prefetch_task_ready(
                runtime,
                prefetch_task,
                timeout_seconds=0.01,
            )
            assert recovered is not None
            assert recovered["base_state"] == base_state
            assert len(recovered["prepared_results"]) == 1
            assert [item["item_id"] for item in recovered["prepared_results"][0]["items"]] == [
                item["item_id"],
                item_2["item_id"],
            ]
            consume_task = asyncio.create_task(
                manager._consume_prefetched_episode_turn(runtime, session, recovered)
            )
            ready = await _next_queue_event(queue, "presentation_item_ready", timeout=1)
            assert ready["item"]["item_id"] == item["item_id"]
            await manager.ack_presentation_item("live-a", item["item_id"])
            ready_2 = await _next_queue_event(queue, "presentation_item_ready", timeout=1)
            assert ready_2["item"]["item_id"] == item_2["item_id"]
            await manager.ack_presentation_item("live-a", item_2["item_id"])
            consumed = await asyncio.wait_for(consume_task, timeout=1)
            latest = await asyncio.wait_for(
                manager._update_director_state_after_prefetch_consumed(
                    runtime,
                    session,
                    storage.get_director_state("live-a"),
                    consumed,
                ),
                timeout=1,
            )

            assert latest["current_topic"] == "recovered topic"
            assert latest["metadata"]["last_decision"] == decision
        finally:
            if not prefetch_task.done():
                prefetch_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await prefetch_task


@pytest.mark.asyncio
async def test_prefetch_timeout_signals_provider_cancel_event():
    with temp_storage() as storage:
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
            "prefetch_wait_timeout_seconds": 0.1,
        })
        storage.upsert_live_episode_plan(sample_plan())
        session = storage.bind_episode_plan_to_session("live-a", "plan-general-panel")
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        started = threading.Event()
        should_cancel_seen = threading.Event()
        cancel_event_seen = threading.Event()

        class CooperativePrefetchClient:
            def list_characters(self):
                return _episode_plan_characters()

            def chat_stream_sync(self, **kwargs):
                started.set()
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    if kwargs["should_cancel"]():
                        should_cancel_seen.set()
                        if kwargs["cancel_event"].is_set():
                            cancel_event_seen.set()
                        break
                    time.sleep(0.01)
                return {
                    "session_id": "mem-prefetch",
                    "message_id": "cancelled-prefetch-result",
                    "reply": "should not be used",
                }

        manager = YouTubeBridgeManager(
            storage,
            youtube_client=LiveEndedClient(),
            memoria_client_factory=CooperativePrefetchClient,
            tts_provider_factory=lambda: FakeTTSProvider(),
        )
        manager._runtimes["live-a"] = runtime
        state = storage.get_director_state("live-a")
        current_decision = manager._episode_plan_next_decision(session, state)
        prefetch_task = asyncio.create_task(manager._prefetch_next_episode_planned_turn(
            runtime,
            session,
            state,
            current_decision,
        ))
        try:
            await asyncio.to_thread(started.wait, 1)
            result = await manager._await_prefetch_task_ready(
                runtime,
                prefetch_task,
                timeout_seconds=0.01,
            )

            assert result is None
            assert await asyncio.to_thread(should_cancel_seen.wait, 1) is True
            assert cancel_event_seen.is_set() is True
            assert storage.get_active_interaction("live-a") is None
        finally:
            if not prefetch_task.done():
                prefetch_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await prefetch_task


@pytest.mark.asyncio
async def test_prefetch_final_ready_update_does_not_resurrect_interrupted_interaction(monkeypatch):
    with temp_storage() as storage:
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

        class FastPrefetchClient:
            def list_characters(self):
                return _episode_plan_characters()

            def chat_stream_sync(self, **kwargs):
                kwargs["on_result"]({
                    "message_id": "prefetch-race-msg",
                    "reply": "不應被復活的企劃句。",
                    "character_id": "analyst-b",
                    "character_name": "分析B",
                })
                return {
                    "session_id": "mem-prefetch-race",
                    "message_id": "prefetch-race-result",
                    "reply": "不應被復活的企劃句。",
                }

        manager = YouTubeBridgeManager(
            storage,
            youtube_client=LiveEndedClient(),
            memoria_client_factory=FastPrefetchClient,
            tts_provider_factory=lambda: FakeTTSProvider(),
        )
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        manager._runtimes["live-a"] = runtime
        state = storage.get_director_state("live-a")
        current_decision = manager._episode_plan_next_decision(session, state)
        original_update = storage.update_interaction
        original_conditional_update = storage.update_interaction_if_status
        injected = {"done": False}

        def racing_conditional_update(job_id, expected_status, **fields):
            if expected_status == "prefetching" and fields.get("status") == "prefetched" and not injected["done"]:
                injected["done"] = True
                original_update(
                    job_id,
                    status="interrupted",
                    reason="prefetch_wait_timeout",
                    completed_at=datetime.now().isoformat(),
                    interrupted_at=datetime.now().isoformat(),
                )
            return original_conditional_update(job_id, expected_status, **fields)

        monkeypatch.setattr(storage, "update_interaction_if_status", racing_conditional_update, raising=False)

        result = await manager._prefetch_next_episode_planned_turn(
            runtime,
            session,
            state,
            current_decision,
        )

        assert result is not None
        assert result["interaction"]["status"] == "interrupted"
        assert result["prepared_results"] == []
        assert injected["done"] is True


@pytest.mark.asyncio
async def test_prefetch_timeout_does_not_recover_stale_prefetched_row():
    with temp_storage() as storage:
        manager = YouTubeBridgeManager(storage)
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        stale = storage.create_interaction({
            "session_id": "live-a",
            "source": "director_prefetch",
            "priority": 40,
            "status": "prefetched",
            "memoria_session_id": "live-a:prefetch:stale",
            "metadata": {
                "prefetch_ready": True,
                "decision": {"action": "continue_topic", "episode_plan": {"mode": "planned_turn"}},
                "base_state": {"session_id": "live-a", "metadata": {"planned_state": {}}},
            },
        })
        stale_item = storage.create_presentation_item({
            "session_id": "live-a",
            "interaction_job_id": stale["job_id"],
            "message_id": "stale-prefetch-msg:0",
            "character_id": "host-a",
            "character_name": "主持A",
            "sequence_index": 0,
            "text": "stale prefetched item",
            "status": "ready",
            "audio_path": "stale-prefetch.wav",
            "audio_format": "wav",
            "metadata": {"source": "director_prefetch"},
        })
        current = storage.create_interaction({
            "session_id": "live-a",
            "source": "director_prefetch",
            "priority": 45,
            "status": "prefetching",
            "memoria_session_id": "live-a:prefetch:current",
            "metadata": {"prefetch_ready": False},
        })

        async def pending_prefetch():
            await asyncio.Event().wait()

        prefetch_task = asyncio.create_task(pending_prefetch())
        setattr(prefetch_task, "director_prefetch_job_id", current["job_id"])
        try:
            result = await manager._await_prefetch_task_ready(
                runtime,
                prefetch_task,
                timeout_seconds=0.01,
            )

            assert result is None
            assert storage.get_interaction(current["job_id"])["status"] == "interrupted"
            stale_after = storage.get_interaction(stale["job_id"])
            assert stale_after["status"] == "interrupted"
            assert stale_after["reason"] == "stale_prefetch_wait_timeout"
            assert storage.get_presentation_item(stale_item["item_id"])["status"] == "ready"
            assert storage.get_active_interaction("live-a") is None
        finally:
            if not prefetch_task.done():
                prefetch_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await prefetch_task


@pytest.mark.asyncio
async def test_prefetch_timeout_without_job_id_does_not_recover_stale_prefetched_row():
    with temp_storage() as storage:
        manager = YouTubeBridgeManager(storage)
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        stale = storage.create_interaction({
            "session_id": "live-a",
            "source": "director_prefetch",
            "priority": 40,
            "status": "prefetched",
            "memoria_session_id": "live-a:prefetch:stale-no-job-id",
            "metadata": {
                "prefetch_ready": True,
                "decision": {"action": "continue_topic", "episode_plan": {"mode": "planned_turn"}},
                "base_state": {"session_id": "live-a", "metadata": {"planned_state": {}}},
            },
        })
        stale_item = storage.create_presentation_item({
            "session_id": "live-a",
            "interaction_job_id": stale["job_id"],
            "message_id": "stale-no-job-id-msg:0",
            "character_id": "host-a",
            "character_name": "主持A",
            "sequence_index": 0,
            "text": "stale prefetched item",
            "status": "ready",
            "audio_path": "stale-no-job-id.wav",
            "audio_format": "wav",
            "metadata": {"source": "director_prefetch"},
        })

        async def pending_prefetch():
            await asyncio.Event().wait()

        prefetch_task = asyncio.create_task(pending_prefetch())
        try:
            result = await manager._await_prefetch_task_ready(
                runtime,
                prefetch_task,
                timeout_seconds=0.01,
            )

            assert result is None
            assert storage.get_interaction(stale["job_id"])["status"] == "prefetched"
            assert storage.get_presentation_item(stale_item["item_id"])["status"] == "ready"
        finally:
            if not prefetch_task.done():
                prefetch_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await prefetch_task


@pytest.mark.asyncio
async def test_prefetch_timeout_does_not_recover_prefetched_without_decision_base_state():
    with temp_storage() as storage:
        manager = YouTubeBridgeManager(storage)
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        interaction = storage.create_interaction({
            "session_id": "live-a",
            "source": "director_prefetch",
            "priority": 40,
            "status": "prefetched",
            "memoria_session_id": "live-a:prefetch:missing-metadata",
            "metadata": {"prefetch_ready": True},
        })
        item = storage.create_presentation_item({
            "session_id": "live-a",
            "interaction_job_id": interaction["job_id"],
            "message_id": "prefetch-missing-metadata:0",
            "character_id": "host-a",
            "character_name": "主持A",
            "sequence_index": 0,
            "text": "metadata incomplete item",
            "status": "ready",
            "audio_path": "prefetch-missing-metadata.wav",
            "audio_format": "wav",
            "metadata": {"source": "director_prefetch"},
        })

        async def pending_prefetch():
            await asyncio.Event().wait()

        prefetch_task = asyncio.create_task(pending_prefetch())
        setattr(prefetch_task, "director_prefetch_job_id", interaction["job_id"])
        try:
            result = await manager._await_prefetch_task_ready(
                runtime,
                prefetch_task,
                timeout_seconds=0.01,
            )

            assert result is None
            assert storage.get_interaction(interaction["job_id"])["status"] == "interrupted"
            assert storage.get_presentation_item(item["item_id"])["status"] == "cancelled"
        finally:
            if not prefetch_task.done():
                prefetch_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await prefetch_task


@pytest.mark.asyncio
async def test_prefetch_timeout_does_not_recover_mixed_prefetched_items():
    with temp_storage() as storage:
        manager = YouTubeBridgeManager(storage)
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        interaction = storage.create_interaction({
            "session_id": "live-a",
            "source": "director_prefetch",
            "priority": 40,
            "status": "prefetched",
            "memoria_session_id": "live-a:prefetch:mixed",
            "metadata": {"prefetch_ready": True},
        })
        ready_item = storage.create_presentation_item({
            "session_id": "live-a",
            "interaction_job_id": interaction["job_id"],
            "message_id": "prefetch-mixed-ready:0",
            "character_id": "host-a",
            "character_name": "主持A",
            "sequence_index": 0,
            "text": "ready item",
            "status": "ready",
            "audio_path": "prefetch-mixed-ready.wav",
            "audio_format": "wav",
            "metadata": {"source": "director_prefetch"},
        })
        synthesizing_item = storage.create_presentation_item({
            "session_id": "live-a",
            "interaction_job_id": interaction["job_id"],
            "message_id": "prefetch-mixed-pending:1",
            "character_id": "host-a",
            "character_name": "主持A",
            "sequence_index": 1,
            "text": "pending item",
            "status": "synthesizing",
            "metadata": {"source": "director_prefetch"},
        })

        async def pending_prefetch():
            await asyncio.Event().wait()

        prefetch_task = asyncio.create_task(pending_prefetch())
        setattr(prefetch_task, "director_prefetch_job_id", interaction["job_id"])
        try:
            result = await manager._await_prefetch_task_ready(
                runtime,
                prefetch_task,
                timeout_seconds=0.01,
            )

            assert result is None
            assert storage.get_active_interaction("live-a") is None
            assert storage.get_interaction(interaction["job_id"])["status"] == "interrupted"
            assert storage.get_presentation_item(ready_item["item_id"])["status"] == "cancelled"
            assert storage.get_presentation_item(synthesizing_item["item_id"])["status"] == "cancelled"
        finally:
            if not prefetch_task.done():
                prefetch_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await prefetch_task


@pytest.mark.asyncio
async def test_after_main_turn_sequence_does_not_reconsume_same_chained_prefetch_task(monkeypatch):
    with temp_storage() as storage:
        manager = YouTubeBridgeManager(storage)
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        session = {"session_id": "live-a", "presentation_enabled": True}
        state = {"session_id": "live-a", "metadata": {}}

        async def completed_prefetch_task():
            return {
                "interaction": {"job_id": "looping-prefetch", "source": "director_prefetch"},
                "prepared_results": [{"message": {"message_id": "loop-msg"}, "items": []}],
                "decision": {"action": "continue_topic", "episode_plan": {"mode": "planned_turn"}},
                "base_state": state,
            }

        prefetch_task = asyncio.create_task(completed_prefetch_task())
        consume_calls = 0

        async def fake_consume(_runtime, _session, prefetched):
            nonlocal consume_calls
            consume_calls += 1
            if consume_calls > 1:
                raise AssertionError("same chained prefetch task was consumed more than once")
            return {
                **prefetched,
                "interaction": prefetched["interaction"],
                "discarded": False,
                "after_memoria_task": prefetch_task,
            }

        async def fake_update(_runtime, _session, current_state, _consumed, **_kwargs):
            return current_state

        async def fake_present_ready_audience(_runtime, _session, current_state):
            return current_state

        monkeypatch.setattr(manager, "_consume_prefetched_episode_turn", fake_consume)
        monkeypatch.setattr(manager, "_update_director_state_after_prefetch_consumed", fake_update)
        monkeypatch.setattr(manager, "_present_ready_audience_batch_after_turn", fake_present_ready_audience)

        await asyncio.wait_for(
            manager._after_main_turn_sequence(runtime, session, state, prefetch_task),
            timeout=1,
        )

        assert consume_calls == 1


@pytest.mark.asyncio
async def test_after_main_turn_sequence_stop_guard_cancels_pending_prefetch_task(monkeypatch):
    with temp_storage() as storage:
        manager = YouTubeBridgeManager(storage)
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        runtime.stop_after_current_turn = True
        session = {"session_id": "live-a", "presentation_enabled": True}
        state = {"session_id": "live-a", "metadata": {}}
        created = asyncio.Event()

        async def fake_present(_runtime, _session, current_state):
            return current_state

        async def pending_prefetch():
            await asyncio.sleep(0.02)
            storage.create_interaction({
                "session_id": "live-a",
                "source": "director_prefetch",
                "priority": 40,
                "status": "prefetched",
                "memoria_session_id": "live-a:prefetch:stop-orphan",
                "metadata": {"prefetch_ready": True},
            })
            created.set()
            return {"unexpected": True}

        monkeypatch.setattr(manager, "_present_ready_audience_batch_after_turn", fake_present)
        prefetch_task = asyncio.create_task(pending_prefetch())
        try:
            result = await manager._after_main_turn_sequence(
                runtime,
                session,
                state,
                prefetch_task,
            )
            await asyncio.sleep(0.05)

            assert result is state
            assert prefetch_task.cancelled() is True
            assert created.is_set() is False
            assert storage.get_active_interaction("live-a") is None
        finally:
            if not prefetch_task.done():
                prefetch_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await prefetch_task


@pytest.mark.asyncio
async def test_after_main_turn_sequence_preserves_ready_prefetch_after_stop(monkeypatch):
    with temp_storage() as storage:
        manager = YouTubeBridgeManager(storage)
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        session = {"session_id": "live-a", "presentation_enabled": True}
        state = {"session_id": "live-a", "metadata": {}}
        interaction = storage.create_interaction({
            "session_id": "live-a",
            "source": "director_prefetch",
            "priority": 40,
            "status": "prefetched",
            "memoria_session_id": "live-a:prefetch:stop-after-wait",
            "metadata": {
                "prefetch_ready": True,
                "decision": {"action": "continue_topic", "episode_plan": {"mode": "planned_turn"}},
                "base_state": state,
            },
        })
        item = storage.create_presentation_item({
            "session_id": "live-a",
            "interaction_job_id": interaction["job_id"],
            "message_id": "stop-after-wait-msg:0",
            "character_id": "host-a",
            "character_name": "主持A",
            "sequence_index": 0,
            "text": "stop should prevent playback",
            "status": "ready",
            "audio_path": "stop-after-wait.wav",
            "audio_format": "wav",
            "metadata": {"source": "director_prefetch"},
        })
        consumed = {"called": False}

        async def fake_present(_runtime, _session, current_state):
            return current_state

        async def fake_wait(_runtime, _prefetch_task, *, timeout_seconds):
            runtime.stop_after_current_turn = True
            return {
                "interaction": interaction,
                "memoria_result": {"session_id": "draft", "message_id": "msg", "reply": "reply"},
                "prepared_results": [{
                    "message": {"message_id": "stop-after-wait-msg", "content": "reply"},
                    "items": [item],
                }],
                "decision": {"action": "continue_topic", "episode_plan": {"mode": "planned_turn"}},
                "base_state": state,
            }

        async def fake_consume(*args, **kwargs):
            consumed["called"] = True
            return {"unexpected": True}

        monkeypatch.setattr(manager, "_present_ready_audience_batch_after_turn", fake_present)
        monkeypatch.setattr(manager, "_await_prefetch_task_ready", fake_wait)
        monkeypatch.setattr(manager, "_consume_prefetched_episode_turn", fake_consume)
        prefetch_task = asyncio.Future()

        result = await manager._after_main_turn_sequence(
            runtime,
            session,
            state,
            prefetch_task,
        )

        assert result is state
        assert consumed["called"] is False
        assert storage.get_active_interaction("live-a")["job_id"] == interaction["job_id"]
        updated = storage.get_interaction(interaction["job_id"])
        assert updated["status"] == "prefetched"
        assert updated["metadata"]["prefetch_stop_ready_preserved"] is True
        assert storage.get_presentation_item(item["item_id"])["status"] == "ready"


@pytest.mark.asyncio
async def test_after_main_turn_sequence_uses_prefetch_wait_timeout_setting(monkeypatch):
    with temp_storage() as storage:
        session = {
            "session_id": "live-a",
            "presentation_enabled": True,
            "presentation_ack_timeout_seconds": 30,
            "prefetch_wait_timeout_seconds": 0.25,
        }
        state = {"session_id": "live-a", "metadata": {}}
        manager = YouTubeBridgeManager(storage)
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        captured = {}

        async def fake_present(_runtime, _session, current_state):
            return current_state

        async def fake_wait(_runtime, _prefetch_task, *, timeout_seconds):
            captured["timeout_seconds"] = timeout_seconds
            return None

        monkeypatch.setattr(manager, "_present_ready_audience_batch_after_turn", fake_present)
        monkeypatch.setattr(manager, "_await_prefetch_task_ready", fake_wait)

        await manager._after_main_turn_sequence(
            runtime,
            session,
            state,
            asyncio.Future(),
        )

        assert captured["timeout_seconds"] == 0.25


def test_prefetch_wait_timeout_default_is_ten_seconds():
    with temp_storage() as storage:
        manager = YouTubeBridgeManager(storage)
        assert manager._prefetch_wait_timeout_seconds({}, {"metadata": {}}) == 10.0
        assert manager._prefetch_wait_timeout_seconds(
            {"prefetch_wait_timeout_seconds": ""},
            {"metadata": {}},
        ) == 10.0
        assert manager._prefetch_wait_timeout_seconds(
            {"prefetch_wait_timeout_seconds": None},
            {"metadata": {}},
        ) == 10.0
        assert manager._prefetch_wait_timeout_seconds(
            {"prefetch_wait_timeout_seconds": 9999},
            {"metadata": {}},
        ) == 600.0


@pytest.mark.asyncio
async def test_late_prefetch_stream_result_after_timeout_does_not_create_ready_item(monkeypatch):
    with temp_storage() as storage:
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
            "prefetch_wait_timeout_seconds": 0.1,
        })
        for character_id in ["host-a", "analyst-b", "skeptic-c"]:
            storage.upsert_tts_profile({
                "character_id": character_id,
                "ref_audio_path": f"{character_id}.wav",
                "prompt_text": "參考語音文字。",
            })
        storage.upsert_live_episode_plan(sample_plan())
        session = storage.bind_episode_plan_to_session("live-a", "plan-general-panel")
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        started = threading.Event()
        release = threading.Event()
        late_result_sent = threading.Event()

        class SlowPrefetchClient:
            def list_characters(self):
                return _episode_plan_characters()

            def chat_stream_sync(self, **kwargs):
                started.set()
                release.wait(timeout=2)
                kwargs["on_result"]({
                    "message_id": "late-prefetch-msg",
                    "reply": "逾時後才抵達的企劃句。",
                    "character_id": "analyst-b",
                    "character_name": "分析B",
                })
                late_result_sent.set()
                return {
                    "session_id": "mem-prefetch",
                    "message_id": "late-prefetch-result",
                    "reply": "逾時後才抵達的企劃句。",
                }

        manager = YouTubeBridgeManager(
            storage,
            youtube_client=LiveEndedClient(),
            memoria_client_factory=SlowPrefetchClient,
            tts_provider_factory=lambda: FakeTTSProvider(),
        )
        manager._runtimes["live-a"] = runtime
        state = storage.get_director_state("live-a")
        current_decision = manager._episode_plan_next_decision(session, state)
        prefetch_task = asyncio.create_task(manager._prefetch_next_episode_planned_turn(
            runtime,
            session,
            state,
            current_decision,
        ))
        try:
            await asyncio.to_thread(started.wait, 1)
            result = await manager._await_prefetch_task_ready(
                runtime,
                prefetch_task,
                timeout_seconds=0.01,
            )
            assert result is None
            assert storage.get_active_interaction("live-a") is None

            release.set()
            await asyncio.to_thread(late_result_sent.wait, 1)
            await asyncio.sleep(0.2)

            assert storage.get_active_interaction("live-a") is None
            assert [
                item for item in storage.list_presentation_items("live-a")
                if item["status"] == "ready"
            ] == []
            timed_out = [
                interaction for interaction in storage.list_interactions("live-a", limit=20)
                if interaction["source"] == "director_prefetch"
            ][0]
            assert timed_out["status"] == "interrupted"
            assert timed_out["reason"] == "prefetch_wait_timeout"
        finally:
            release.set()
            if not prefetch_task.done():
                prefetch_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await prefetch_task


@pytest.mark.asyncio
async def test_scheduled_prefetch_prepare_rechecks_status_after_cleanup(monkeypatch):
    with temp_storage() as storage:
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
            "character_ids": ["host-a"],
            "presentation_enabled": True,
            "tts_enabled": True,
        })
        manager = YouTubeBridgeManager(storage, tts_provider_factory=lambda: FakeTTSProvider())
        interaction = storage.create_interaction({
            "session_id": "live-a",
            "source": "director_prefetch",
            "priority": 40,
            "status": "prefetching",
            "memoria_session_id": "live-a:prefetch:scheduled-race",
            "metadata": {"prefetch_ready": False},
        })
        called = {"prepare": False}

        async def unexpected_prepare(*args, **kwargs):
            called["prepare"] = True
            return {"message": {"message_id": "unexpected"}, "items": []}

        monkeypatch.setattr(manager, "prepare_stream_result", unexpected_prepare)
        scheduled = manager._prepare_stream_result_if_interaction_active(
            "live-a",
            {
                "message_id": "scheduled-race-msg",
                "reply": "cleanup 後不應該準備的企劃句。",
                "character_id": "host-a",
                "character_name": "主持A",
            },
            source="director_prefetch",
            interaction_job_id=interaction["job_id"],
            expected_status="prefetching",
        )
        storage.update_interaction(
            interaction["job_id"],
            status="interrupted",
            reason="prefetch_wait_timeout",
            completed_at=datetime.now().isoformat(),
            interrupted_at=datetime.now().isoformat(),
        )

        result = await scheduled

        assert result is None
        assert called["prepare"] is False
        assert storage.list_presentation_items("live-a") == []


@pytest.mark.asyncio
async def test_late_prefetch_prepare_after_cleanup_cancels_items_without_skipping(monkeypatch):
    with temp_storage() as storage:
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
            "character_ids": ["host-a"],
            "presentation_enabled": True,
            "tts_enabled": True,
        })
        manager = YouTubeBridgeManager(storage, tts_provider_factory=lambda: FakeTTSProvider())
        interaction = storage.create_interaction({
            "session_id": "live-a",
            "source": "director_prefetch",
            "priority": 40,
            "status": "prefetching",
            "memoria_session_id": "live-a:prefetch:postcheck-race",
            "metadata": {"prefetch_ready": False},
        })
        existing_item = storage.create_presentation_item({
            "session_id": "live-a",
            "interaction_job_id": interaction["job_id"],
            "message_id": "existing-ready-msg:0",
            "character_id": "host-a",
            "character_name": "主持A",
            "sequence_index": 0,
            "text": "cleanup 前已存在的 ready item。",
            "status": "ready",
            "audio_path": "existing-ready.wav",
            "audio_format": "wav",
            "metadata": {"source": "director_prefetch"},
        })
        prepare_entered = asyncio.Event()
        allow_prepare = asyncio.Event()

        async def delayed_prepare(*args, **kwargs):
            prepare_entered.set()
            await allow_prepare.wait()
            late_item = storage.create_presentation_item({
                "session_id": "live-a",
                "interaction_job_id": interaction["job_id"],
                "message_id": "late-ready-msg:0",
                "character_id": "host-a",
                "character_name": "主持A",
                "sequence_index": 1,
                "text": "cleanup 後才準備完成的 item。",
                "status": "ready",
                "audio_path": "late-ready.wav",
                "audio_format": "wav",
                "metadata": {"source": "director_prefetch"},
            })
            return {
                "message": {"message_id": "late-ready-msg", "content": late_item["text"]},
                "items": [late_item],
            }

        monkeypatch.setattr(manager, "prepare_stream_result", delayed_prepare)
        prepare_task = asyncio.create_task(manager._prepare_stream_result_if_interaction_active(
            "live-a",
            {
                "message_id": "late-ready-msg",
                "reply": "cleanup 後才準備完成的 item。",
                "character_id": "host-a",
                "character_name": "主持A",
            },
            source="director_prefetch",
            interaction_job_id=interaction["job_id"],
            expected_status="prefetching",
        ))
        await prepare_entered.wait()
        manager._clear_timed_out_prefetch_interactions(
            runtime=LiveRuntime(session_id="live-a"),
            expected_job_id=interaction["job_id"],
        )
        allow_prepare.set()

        result = await prepare_task

        assert result is None
        assert storage.get_presentation_item(existing_item["item_id"])["status"] == "cancelled"
        late_items = [
            item for item in storage.list_presentation_items("live-a")
            if item["item_id"] != existing_item["item_id"]
        ]
        assert late_items and all(item["status"] == "cancelled" for item in late_items)
        assert all(item["status"] != "skipped" for item in storage.list_presentation_items("live-a"))

@pytest.mark.asyncio
async def test_after_main_turn_sequence_uses_audience_chain_task_instead_of_stale_prepare(monkeypatch):
    with temp_storage() as storage:
        manager = YouTubeBridgeManager(storage)
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        session = {
            "session_id": "live-a",
            "presentation_enabled": True,
            "prefetch_wait_timeout_seconds": 0.2,
        }
        state = {"session_id": "live-a", "metadata": {}}
        stale_prepare_released = asyncio.Event()
        order: list[str] = []

        async def current_prefetch_task():
            return {
                "interaction": {"job_id": "current-planned-prefetch", "source": "director_prefetch"},
                "prepared_results": [{"message": {"message_id": "current-msg"}, "items": []}],
                "decision": {"action": "continue_topic", "episode_plan": {"mode": "planned_turn"}},
                "base_state": state,
            }

        async def stale_prepare_task():
            await stale_prepare_released.wait()
            return {
                "interaction": {"job_id": "stale-audience-prepare", "source": "director_audience_prepare"},
                "prepared_results": [{"message": {"message_id": "audience-msg"}, "items": []}],
            }

        async def planned_prefetch_task():
            return {
                "interaction": {"job_id": "planned-after-audience", "source": "director_prefetch"},
                "prepared_results": [{"message": {"message_id": "planned-msg"}, "items": []}],
                "decision": {"action": "continue_topic", "episode_plan": {"mode": "planned_turn"}},
                "base_state": state,
            }

        first_task = asyncio.create_task(current_prefetch_task())
        old_task = asyncio.create_task(stale_prepare_task())
        new_task = asyncio.create_task(planned_prefetch_task())
        presented_once = False

        async def fake_present_ready_audience(_runtime, _session, current_state):
            nonlocal presented_once
            if presented_once:
                return current_state
            presented_once = True
            order.append("audience-presented")
            stale_prepare_released.set()
            runtime.audience_gap_after_memoria_task = new_task
            return current_state

        async def fake_consume(_runtime, _session, prefetched):
            job_id = prefetched["interaction"]["job_id"]
            order.append(f"consume:{job_id}")
            if job_id == "current-planned-prefetch":
                return {**prefetched, "interaction": prefetched["interaction"], "discarded": False, "after_memoria_task": old_task}
            if job_id == "stale-audience-prepare":
                return None
            return {**prefetched, "interaction": prefetched["interaction"], "discarded": False}

        async def fake_update(_runtime, _session, current_state, _consumed, **_kwargs):
            return current_state

        monkeypatch.setattr(manager, "_present_ready_audience_batch_after_turn", fake_present_ready_audience)
        monkeypatch.setattr(manager, "_consume_prefetched_episode_turn", fake_consume)
        monkeypatch.setattr(manager, "_update_director_state_after_prefetch_consumed", fake_update)

        try:
            await asyncio.wait_for(
                manager._after_main_turn_sequence(runtime, session, state, first_task),
                timeout=1,
            )
        finally:
            for task in (first_task, old_task, new_task):
                if not task.done():
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task

        assert order == [
            "consume:current-planned-prefetch",
            "audience-presented",
            "consume:planned-after-audience",
        ]
