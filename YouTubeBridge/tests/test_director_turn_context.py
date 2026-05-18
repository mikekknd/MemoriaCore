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

def test_director_opening_decision_builds_short_kickoff_prompt():
    decision = YouTubeBridgeManager._director_opening_decision(
        {
            "session_id": "live-a",
            "display_name": "QA Live",
            "director_guidance": "測試導播開場與觀眾互動。",
        },
        {},
    )

    assert decision["action"] == "opening"
    assert "開場" in decision["prompt"]
    assert "測試導播開場與觀眾互動" in decision["prompt"]
    assert "queue" not in decision["prompt"]
    assert "prompt" not in decision["prompt"]


@pytest.mark.asyncio
async def test_director_turn_includes_episode_plan_context(monkeypatch):
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
        session = storage.bind_episode_plan_to_session("live-a", "plan-general-panel")
        captured = {}

        class CaptureStreamClient:
            def list_characters(self):
                return [
                    {"character_id": "host-a", "name": "主持A"},
                    {"character_id": "analyst-b", "name": "分析B"},
                    {"character_id": "skeptic-c", "name": "質疑C"},
                ]

            def chat_stream_sync(self, **kwargs):
                captured.update(kwargs)
                return {
                    "session_id": "mem-a",
                    "message_id": 42,
                    "reply": "續話完成。",
                }

        monkeypatch.setattr("bridge_engine.MemoriaClient", CaptureStreamClient)
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())

        result = await manager._send_director_turn(
            session,
            storage.get_director_state("live-a"),
            manager._episode_planned_turn_decision(
                session,
                storage.get_director_state("live-a"),
            ),
        )

        assert result["interaction"]["status"] == "completed"
        context = captured["external_context"]["context_text"]
        assert "<live_episode_turn_context>" in context
        assert "直播進度：" not in context
        assert "直播互動規則：" not in context
        assert "段落：事件 Hook" not in context
        assert "本輪目標：用具體事件開場" in context
        assert "最多句數：" not in context
        assert "<live_episode_director_context>" not in context
        assert "turn_contract:" not in context
        assert "plan_id:" not in context
        assert "allowed_participant_ids" not in context
        assert "allowed_character_ids" not in context
        assert "evidence_policy:" not in context
        assert captured["external_context"]["live_episode_plan"]["plan_id"] == "plan-general-panel"
        assert captured["external_context"]["live_episode_plan"]["speaker_policy"]["selection_mode"] == "router_select"
        assert captured["external_context"]["live_episode_plan"]["evidence_policy"]["max_cards"] == 3
        assert captured["external_context"]["summary"]["episode_plan_turn_id"] == "seg_01_turn_01"
        assert context.count("用具體事件開場") == 1
        assert "處理提示：用具體事件開場" not in context
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_director_turn_reuses_memoria_client_and_character_resolution(monkeypatch):
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
        session = storage.bind_episode_plan_to_session("live-a", "plan-general-panel")

        class CountingStreamClient:
            instance_count = 0
            list_character_calls = 0
            chat_calls = 0

            def __init__(self):
                self.__class__.instance_count += 1

            def list_characters(self):
                self.__class__.list_character_calls += 1
                return _episode_plan_characters()

            def chat_stream_sync(self, **kwargs):
                self.__class__.chat_calls += 1
                return {
                    "session_id": kwargs.get("session_id") or "mem-a",
                    "message_id": 42,
                    "reply": "續話完成。",
                }

        monkeypatch.setattr("bridge_engine.MemoriaClient", CountingStreamClient)
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())

        result = await manager._send_director_turn(
            session,
            storage.get_director_state("live-a"),
            manager._episode_planned_turn_decision(
                session,
                storage.get_director_state("live-a"),
            ),
        )

        assert result["interaction"]["status"] == "completed"
        assert CountingStreamClient.instance_count == 1
        assert CountingStreamClient.list_character_calls == 1
        assert CountingStreamClient.chat_calls == 1
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_director_turn_suppresses_legacy_hosting_context_when_episode_plan_bound(monkeypatch):
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
            "host_interaction_rules": "舊版主持規則不應進入新版企劃回合。",
            "program_segment_plan": "舊版 Hook\n舊版分析",
            "program_segment_turns": 2,
        })
        storage.upsert_live_episode_plan(sample_plan())
        session = storage.bind_episode_plan_to_session("live-a", "plan-general-panel")
        captured = {}

        class CaptureStreamClient:
            def list_characters(self):
                return [
                    {"character_id": "host-a", "name": "主持A"},
                    {"character_id": "analyst-b", "name": "分析B"},
                    {"character_id": "skeptic-c", "name": "質疑C"},
                ]

            def chat_stream_sync(self, **kwargs):
                captured.update(kwargs)
                return {
                    "session_id": "mem-a",
                    "message_id": 42,
                    "reply": "續話完成。",
                }

        monkeypatch.setattr("bridge_engine.MemoriaClient", CaptureStreamClient)
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())

        result = await manager._send_director_turn(
            session,
            storage.get_director_state("live-a"),
            manager._episode_planned_turn_decision(
                session,
                storage.get_director_state("live-a"),
            ),
        )

        assert result["interaction"]["status"] == "completed"
        external_context = captured["external_context"]
        assert external_context["live_episode_plan"]["plan_id"] == "plan-general-panel"
        assert "live_hosting" not in external_context
        assert "舊版主持規則" not in external_context["context_text"]
        assert "舊版 Hook" not in external_context["context_text"]
        assert "<live_episode_turn_context>" in external_context["context_text"]
        assert "<live_episode_director_context>" not in external_context["context_text"]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_plan_bound_legacy_decision_never_injects_live_hosting(monkeypatch):
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
            "host_interaction_rules": "舊版主持互動規則不得注入。",
            "program_segment_plan": "舊版節目段落流程不得注入。",
            "program_segment_turns": 2,
        })
        storage.upsert_live_episode_plan(sample_plan())
        session = storage.bind_episode_plan_to_session("live-a", "plan-general-panel")
        captured = {}

        class CaptureStreamClient:
            def chat_stream_sync(self, **kwargs):
                captured.update(kwargs)
                return {
                    "session_id": "mem-a",
                    "message_id": 42,
                    "reply": "續話完成。",
                }

        monkeypatch.setattr("bridge_engine.MemoriaClient", CaptureStreamClient)
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())

        result = await manager._send_director_turn(
            session,
            storage.get_director_state("live-a"),
            {
                "action": "continue_topic",
                "reason": "legacy-shaped fallback decision",
                "prompt": "請繼續目前話題。",
                "current_topic": "企劃直播",
            },
        )

        assert result["interaction"]["status"] == "completed"
        external_context = captured["external_context"]
        assert "live_hosting" not in external_context
        assert "舊版主持互動規則" not in external_context["context_text"]
        assert "舊版節目段落流程" not in external_context["context_text"]
        assert "主持互動規則" not in external_context["context_text"]
        assert "目前節目步驟" not in external_context["context_text"]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_planned_turn_external_context_has_episode_contract_only(monkeypatch):
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
            "host_interaction_rules": "舊版主持互動規則",
            "program_segment_plan": "舊版節目段落流程",
        })
        storage.upsert_live_episode_plan(sample_plan())
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
        assert "<live_episode_turn_context>" in context
        assert "本輪目標：用具體事件開場" in context
        assert "角色功能：" in context
        assert "最多句數：" not in context
        assert "<live_episode_director_context>" not in context
        assert "speaker_policy:" not in context
        assert "evidence_policy:" not in context
        assert "allowed_participant_ids" not in context
        assert "allowed_character_ids" not in context
        assert "planned_turn_contracts" not in context
        assert '"segments"' not in context
        assert "主持互動規則" not in context
        assert "目前節目步驟" not in context
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_planned_turn_uses_dialogue_policy_group_turn_limit(monkeypatch):
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
            "director_group_turn_limit": 9,
        })
        plan = sample_plan()
        plan["segments"][0]["planned_turn_contracts"][0]["dialogue_policy"] = {
            "min_replies": 2,
            "max_replies": 3,
            "autonomy": "guided",
            "preferred_flow": ["host frames the beat", "analyst adds one concrete point"],
        }
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

        assert captured["external_context"]["group_turn_limit"] == 3
        assert captured["external_context"]["summary"]["group_turn_limit"] == 3
        assert captured["external_context"]["live_episode_plan"]["dialogue_policy"]["max_replies"] == 3
        assert captured["external_context"]["live_episode_plan"]["next_turn_preview"] == {
            "segment_id": "seg_01",
            "turn_id": "seg_01_turn_02",
            "turn_type": "analysis",
            "intent": "說明事件背後脈絡",
        }
        assert "本段最多 3 次角色發言" not in captured["external_context"]["context_text"]
        assert "本次角色任務：提出本輪核心資訊或主觀點" in captured["external_context"]["context_text"]
        assert "第 2 位角色：只能在「承接反應、轉譯觀眾視角、補新角度、推進下一段」中選一種" not in captured["external_context"]["context_text"]
        assert "交接提示：交給分析角色補脈絡" in captured["external_context"]["context_text"]
        assert "下一輪預告：analysis - 說明事件背後脈絡" not in captured["external_context"]["context_text"]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_presentation_mode_allows_one_speculative_director_reply(monkeypatch):
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
            "display_name": "QA Live",
            "target_memoria_session_id": "mem-a",
            "character_ids": ["host-a", "analyst-b"],
            "director_group_turn_limit": 7,
            "presentation_enabled": True,
            "presentation_ack_timeout_seconds": 3,
        })
        captured = {}

        class CaptureStreamClient:
            def chat_stream_sync(self, **kwargs):
                captured.update(kwargs)
                return {
                    "session_id": "mem-a",
                    "message_id": 42,
                    "reply": "單句回覆完成。",
                }

        monkeypatch.setattr("bridge_engine.MemoriaClient", CaptureStreamClient)
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())

        await manager._send_director_turn(
            session,
            storage.get_director_state("live-a"),
            {
                "action": "continue_topic",
                "prompt": "請自然延續。",
                "current_topic": "四月新番",
            },
        )

        external_context = captured["external_context"]
        assert external_context["group_turn_limit"] == 2
        assert external_context["summary"]["group_turn_limit"] == 2
        assert external_context["summary"]["presentation_enabled"] is True
        assert external_context["max_chars"] <= 1200
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_presentation_queue_emits_debug_events_and_server_logs(caplog):
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
            "target_memoria_session_id": "mem-a",
            "character_ids": ["host-a"],
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

        class FakeTTSProvider:
            def synthesize(self, text, profile):
                return TTSResult(ok=True, audio_bytes=f"audio:{text}".encode("utf-8"), audio_format="wav")

        manager = YouTubeBridgeManager(
            storage,
            youtube_client=LiveEndedClient(),
            tts_provider_factory=FakeTTSProvider,
        )
        queue = await manager.subscribe("live-a")
        caplog.set_level(logging.INFO, logger="youtube_bridge")

        task = asyncio.create_task(manager.present_stream_result(
            "live-a",
            {
                "message_id": "msg-host",
                "reply": "目前角色。",
                "character_id": "host-a",
                "character_name": "主持A",
            },
            source="director",
            interaction_job_id="job-a",
        ))

        debug_phases = []
        ready_event = None
        while ready_event is None:
            event = await asyncio.wait_for(queue.get(), timeout=1)
            if event.get("type") == "presentation_debug":
                debug_phases.append(event["event"]["phase"])
            if event.get("type") == "presentation_item_ready":
                ready_event = event

        assert {"item_ready", "item_presenting", "ack_wait_start"} <= set(debug_phases)
        assert ready_event["item"]["text"] == "目前角色。"

        await manager.ack_presentation_item("live-a", ready_event["item"]["item_id"])
        ack_debug = await _next_queue_event(queue, "presentation_debug", timeout=1)
        assert ack_debug["event"]["phase"] == "ack_received"
        chat = await _next_queue_event(queue, "chat_message", timeout=1)
        assert chat["message"]["content"] == "目前角色。"
        await asyncio.wait_for(task, timeout=1)

        messages = [record.getMessage() for record in caplog.records]
        assert any("PRESENTATION_QUEUE" in message and "item_ready" in message for message in messages)
        assert any("PRESENTATION_QUEUE" in message and "ack_received" in message for message in messages)
    finally:
        if task and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_director_dialogue_expansion_disabled_forces_single_planned_reply(monkeypatch):
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
            "director_dialogue_expansion_enabled": False,
            "director_group_turn_limit": 9,
        })
        plan = sample_plan()
        plan["segments"][0]["planned_turn_contracts"][0]["dialogue_policy"] = {
            "min_replies": 2,
            "max_replies": 3,
            "autonomy": "guided",
            "preferred_flow": ["host frames the beat", "analyst adds one concrete point"],
        }
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
                    "reply": "單人回覆完成。",
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

        external_context = captured["external_context"]
        assert external_context["group_turn_limit"] == 1
        assert external_context["summary"]["group_turn_limit"] == 1
        assert external_context["summary"]["director_dialogue_expansion_enabled"] is False
        assert external_context["live_episode_plan"]["dialogue_policy"]["min_replies"] == 1
        assert external_context["live_episode_plan"]["dialogue_policy"]["max_replies"] == 1
        assert "本段最多 1 次角色發言" not in external_context["context_text"]
        assert "第 2 位角色" not in external_context["context_text"]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_director_dialogue_expansion_disabled_removes_legacy_handoff_prompt(monkeypatch):
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
            "display_name": "Legacy Live",
            "target_memoria_session_id": "mem-a",
            "character_ids": ["host-a", "analyst-b"],
            "director_dialogue_expansion_enabled": False,
            "director_group_turn_limit": 7,
            "director_guidance": "本場只聊動畫新番。",
        })
        captured = {}

        class CaptureStreamClient:
            def chat_stream_sync(self, **kwargs):
                captured.update(kwargs)
                return {
                    "session_id": "mem-a",
                    "message_id": 42,
                    "reply": "單人回覆完成。",
                }

        monkeypatch.setattr("bridge_engine.MemoriaClient", CaptureStreamClient)
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())

        await manager._send_director_turn(
            session,
            storage.get_director_state("live-a"),
            {
                "action": "continue_topic",
                "prompt": "請主持A延續這個話題。",
            },
        )

        external_context = captured["external_context"]
        assert external_context["group_turn_limit"] == 1
        assert external_context["summary"]["director_dialogue_expansion_enabled"] is False
        assert "請讓角色彼此接話" not in external_context["context_text"]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_planned_opening_turn_defaults_to_single_reply(monkeypatch):
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
            "director_group_turn_limit": 9,
        })
        plan = sample_plan()
        opening_turn = plan["segments"][0]["planned_turn_contracts"][0]
        opening_turn["turn_type"] = "opening"
        opening_turn["speaker_policy"]["selection_mode"] = "fixed"
        opening_turn["speaker_policy"]["allowed_participant_ids"] = ["host-a"]
        opening_turn["evidence_policy"]["max_cards"] = 0
        plan["segments"][0]["completion_conditions"]["required_turn_types"] = ["opening"]
        plan["segments"][0]["completion_conditions"]["optional_turn_types"] = ["analysis"]
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
                    "reply": "開場完成。",
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

        assert captured["external_context"]["group_turn_limit"] == 1
        assert captured["external_context"]["summary"]["group_turn_limit"] == 1
        assert captured["external_context"]["live_episode_plan"]["dialogue_policy"]["max_replies"] == 1
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_director_kickoff_uses_episode_plan_when_plan_bound(monkeypatch):
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
            "host_interaction_rules": "舊版主持規則不得在企劃 kickoff 出現。",
            "program_segment_plan": "舊版 opening\n舊版 segment",
        })
        storage.upsert_live_episode_plan(sample_plan())
        storage.bind_episode_plan_to_session("live-a", "plan-general-panel")
        pack = storage.create_topic_pack({"title": "舊 Topic Pack"})
        storage.create_topic_pack_entry(pack["id"], {
            "title": "舊資料卡",
            "body": "企劃 kickoff 不應觸發 post-opening topic anchor。",
        })
        storage.link_topic_pack_to_session("live-a", pack["id"])
        storage.update_director_state("live-a", director_enabled=True, status="running")
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        sent: list[dict] = []

        def forbidden_legacy_opening(*_args, **_kwargs):
            raise AssertionError("plan-bound kickoff must not use legacy opening")

        def forbidden_post_opening(*_args, **_kwargs):
            raise AssertionError("plan-bound kickoff must not use legacy post-opening anchor")

        async def fake_send(self, session, state, decision):
            sent.append(decision)
            return {"interaction": {"job_id": "planned-job"}}

        monkeypatch.setattr(YouTubeBridgeManager, "_director_opening_decision", forbidden_legacy_opening)
        monkeypatch.setattr(YouTubeBridgeManager, "_director_post_opening_topic_decision", forbidden_post_opening)
        monkeypatch.setattr(YouTubeBridgeManager, "_send_director_turn", fake_send)
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())

        await manager._director_kickoff(runtime)

        assert len(sent) == 1
        assert sent[0]["episode_plan"]["mode"] == "planned_turn"
        assert sent[0]["episode_plan"]["turn_contract"]["turn_id"] == "seg_01_turn_01"
        assert storage.get_director_state("live-a")["status"] == "running"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_director_kickoff_skips_legacy_post_opening_anchor_when_plan_bound(monkeypatch):
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
        pack = storage.create_topic_pack({"title": "舊 Topic Pack"})
        storage.create_topic_pack_entry(pack["id"], {
            "title": "舊資料卡",
            "body": "有 topic pack 也不能跑 legacy anchor。",
        })
        storage.link_topic_pack_to_session("live-a", pack["id"])
        storage.update_director_state("live-a", director_enabled=True, status="running")
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        actions: list[str] = []

        async def fake_send(self, session, state, decision):
            actions.append(str(decision.get("action") or ""))
            return {"interaction": {"job_id": f"job-{len(actions)}"}}

        monkeypatch.setattr(YouTubeBridgeManager, "_send_director_turn", fake_send)
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())

        await manager._director_kickoff(runtime)

        assert actions == ["continue_topic"]
        assert "opening" not in actions
        assert "post_opening_topic_anchor" not in actions
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_director_kickoff_advances_planned_state_metadata(monkeypatch):
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
        storage.update_director_state("live-a", director_enabled=True, status="running")
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")

        async def fake_send(self, session, state, decision):
            return {"interaction": {"job_id": "planned-job"}}

        monkeypatch.setattr(YouTubeBridgeManager, "_send_director_turn", fake_send)
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())

        await manager._director_kickoff(runtime)

        metadata = storage.get_director_state("live-a")["metadata"]
        assert metadata["planned_state"]["last_planned_turn_contract_id"] == "seg_01_turn_01"
        assert metadata["planned_state"]["completed_turn_ids"] == ["seg_01_turn_01"]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_director_opening_turn_uses_intro_prompt_and_post_opening_fuel_cards(monkeypatch):
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
            "display_name": "QA Live",
            "target_memoria_session_id": "mem-a",
            "character_ids": ["koko", "byakuren"],
            "director_guidance": "本場只聊動畫新番。",
        })
        storage.upsert_live_persona_overlay(
            "koko",
            {
                "enabled": True,
                "mode": "replace",
                "system_prompt": "直播可可 prompt",
                "self_address": "本小姐",
                "opening_intro": "叮咚，可可是今天的直播主持。",
                "addressing": {"byakuren": "白蓮大人"},
                "reply_rules": "",
            },
        )
        pack = storage.create_topic_pack({"title": "動畫新番資料包"})
        storage.create_topic_pack_entry(pack["id"], {
            "title": "第一話高光",
            "body": "這段 FactCard 不應該在開場第一輪直接塞入。",
            "source_type": "factcards_folder",
        })
        storage.link_topic_pack_to_session("live-a", pack["id"])
        captured = {}

        class CaptureStreamClient:
            def chat_stream_sync(self, **kwargs):
                captured.update(kwargs)
                return {
                    "session_id": "mem-a",
                    "message_id": 42,
                    "reply": "開場完成。",
                }

        monkeypatch.setattr("bridge_engine.MemoriaClient", CaptureStreamClient)
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())

        result = await manager._send_director_turn(
            session,
            {"current_topic": "動畫新番"},
            YouTubeBridgeManager._director_opening_decision(session, {"current_topic": "動畫新番"}),
        )

        assert result["interaction"]["status"] == "completed"
        assert captured["display_content"] == "直播開場。"
        assert "直播開場任務" in captured["content"]
        assert "叮咚，可可是今天的直播主持" not in captured["content"]
        assert "直播開場任務" in captured["external_context"]["context_text"]
        assert "直播開場自我介紹" in captured["external_context"]["context_text"]
        assert "叮咚，可可是今天的直播主持" in captured["external_context"]["context_text"]
        assert "白蓮大人" in captured["external_context"]["context_text"]
        assert "本小姐（koko）" not in captured["external_context"]["context_text"]
        assert "character_id: koko" in captured["external_context"]["context_text"]
        assert "固定自稱：本小姐" in captured["external_context"]["context_text"]
        assert "開場後話題導入資料" in captured["external_context"]["context_text"]
        assert "這段 FactCard 不應該在開場第一輪直接塞入" in captured["external_context"]["context_text"]
        assert "<topic_pack_fact_cards>" in captured["external_context"]["context_text"]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_director_turn_uses_plan_character_binding_over_session_selection(monkeypatch):
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
            "character_ids": ["wrong-manual-selection"],
        })
        storage.upsert_live_episode_plan(sample_plan())
        session = storage.bind_episode_plan_to_session("live-a", "plan-general-panel")
        captured = {}

        class CaptureStreamClient:
            def list_characters(self):
                return [
                    {"character_id": "host-a", "name": "主持A"},
                    {"character_id": "analyst-b", "name": "分析B"},
                    {"character_id": "skeptic-c", "name": "質疑C"},
                ]

            def chat_stream_sync(self, **kwargs):
                captured.update(kwargs)
                return {
                    "session_id": "mem-a",
                    "message_id": 42,
                    "reply": "續話完成。",
                }

        monkeypatch.setattr("bridge_engine.MemoriaClient", CaptureStreamClient)
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())

        result = await manager._send_director_turn(
            session,
            storage.get_director_state("live-a"),
            manager._episode_planned_turn_decision(
                session,
                storage.get_director_state("live-a"),
            ),
        )

        assert result["interaction"]["status"] == "completed"
        assert captured["character_ids"] == ["host-a", "analyst-b", "skeptic-c"]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_turn_allowed_participant_ids_map_to_real_character_ids(monkeypatch):
    tmp_dir = _tmp_dir()
    try:
        plan = sample_plan()
        plan["participants"][0]["participant_id"] = "koko"
        plan["participants"][0]["display_name"] = "可可"
        plan["participants"][1]["display_name"] = "白蓮"
        plan["participants"][2]["display_name"] = "旁白"
        turn = plan["segments"][0]["planned_turn_contracts"][0]
        turn["speaker_policy"]["allowed_participant_ids"] = ["koko"]
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
            "character_ids": ["manual-wrong"],
        })
        storage.upsert_live_episode_plan(plan)
        session = storage.bind_episode_plan_to_session("live-a", "plan-general-panel")
        captured = {}

        class CaptureStreamClient:
            def list_characters(self):
                return [
                    {"character_id": "char-koko", "name": "可可"},
                    {"character_id": "char-byakuren", "name": "白蓮"},
                    {"character_id": "char-narrator", "name": "旁白"},
                ]

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

        assert captured["character_ids"] == ["char-koko", "char-byakuren", "char-narrator"]
        plan_context = captured["external_context"]["live_episode_plan"]
        assert plan_context["speaker_policy"]["allowed_participant_ids"] == ["koko"]
        assert plan_context["speaker_policy"]["allowed_character_ids"] == ["char-koko"]
        assert "allowed_character_ids" not in captured["external_context"]["context_text"]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_fixed_speaker_policy_routes_only_allowed_character(monkeypatch):
    tmp_dir = _tmp_dir()
    try:
        plan = sample_plan()
        turn = plan["segments"][0]["planned_turn_contracts"][0]
        turn["speaker_policy"]["selection_mode"] = "fixed"
        turn["speaker_policy"]["allowed_participant_ids"] = ["analyst-b"]
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

        assert captured["character_ids"] == ["host-a", "analyst-b", "skeptic-c"]
        assert captured["external_context"]["live_episode_plan"]["speaker_policy"]["selection_mode"] == "fixed"
        assert captured["external_context"]["live_episode_plan"]["speaker_policy"]["allowed_character_ids"] == ["analyst-b"]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_planned_opening_turn_uses_turn_intent_as_director_prompt(monkeypatch):
    tmp_dir = _tmp_dir()
    try:
        plan = sample_plan()
        segment = plan["segments"][0]
        segment["segment_id"] = "opening"
        segment["title"] = "角色開場"
        segment["goal"] = "角色簡短開場。"
        segment["completion_conditions"] = {
            "min_planned_turns": 1,
            "max_planned_turns": 2,
            "required_turn_types": ["opening"],
            "optional_turn_types": ["analysis"],
        }
        turn = segment["planned_turn_contracts"][0]
        turn["turn_id"] = "opening_turn_01"
        turn["turn_type"] = "opening"
        turn["intent"] = "這是開播第一句角色開場白，請可可自然打招呼並宣告本集主題。"
        turn["speaker_policy"]["selection_mode"] = "fixed"
        turn["speaker_policy"]["allowed_participant_ids"] = ["host-a"]
        turn["output_requirements"]["must_end_with_question"] = False
        turn["output_requirements"]["allow_audience_question"] = False
        turn["evidence_policy"]["max_cards"] = 0

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
        storage.upsert_live_persona_overlay(
            "host-a",
            {
                "enabled": True,
                "mode": "replace",
                "system_prompt": "直播可可 prompt",
                "self_address": "可可",
                "opening_intro": "耳朵立起來，亮點抓出來！可可開播啦～",
                "addressing": {"analyst-b": "白蓮大人"},
                "reply_rules": "",
            },
        )
        storage.upsert_live_persona_overlay(
            "analyst-b",
            {
                "enabled": True,
                "mode": "replace",
                "system_prompt": "直播白蓮 prompt",
                "self_address": "老身",
                "opening_intro": "狐火已燃，好戲開卷。白蓮在此。",
                "addressing": {"host-a": "可可"},
                "reply_rules": "",
            },
        )
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
                    "reply": "開場完成。",
                }

        monkeypatch.setattr("bridge_engine.MemoriaClient", CaptureStreamClient)
        topic_calls = []

        def fake_topic_context(self, session_id, query_text, **kwargs):
            topic_calls.append((session_id, query_text, kwargs))
            return "<topic_pack_fact_cards>opening should not use topic cards</topic_pack_fact_cards>"

        monkeypatch.setattr(YouTubeBridgeManager, "_topic_pack_context_for_query", fake_topic_context)
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
        assert "直播流程 action=continue_topic" in context
        assert "本輪目標：這是開播第一句角色開場白" in context
        assert "處理提示：這是開播第一句角色開場白" not in context
        assert "直播開場自我介紹" in context
        assert "耳朵立起來，亮點抓出來！可可開播啦～" in context
        assert "<topic_pack_fact_cards" not in context
        assert topic_calls == []
        assert "耳朵立起來，亮點抓出來！可可開播啦～" in context[:2500]
        assert "狐火已燃，好戲開卷" not in context
        assert "請自然延續「" not in context
        assert captured["content"].startswith("這是開播第一句角色開場白")
        assert captured["display_content"] == "直播開場。"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_start_session_applies_plan_character_binding_for_existing_session(monkeypatch):
    tmp_dir = _tmp_dir()
    manager = None
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "enabled": True,
        })
        storage.upsert_live_episode_plan(sample_plan())
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "display_name": "Plan Live",
            "episode_plan_id": "plan-general-panel",
            "character_ids": [],
        })

        class CaptureCharactersClient:
            def list_characters(self):
                return [
                    {"character_id": "host-a", "name": "主持A"},
                    {"character_id": "analyst-b", "name": "分析B"},
                    {"character_id": "skeptic-c", "name": "質疑C"},
                ]

        monkeypatch.setattr("bridge_engine.MemoriaClient", CaptureCharactersClient)
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())

        await manager.start_session("live-a")

        assert storage.get_session("live-a")["character_ids"] == [
            "host-a",
            "analyst-b",
            "skeptic-c",
        ]
    finally:
        if manager is not None:
            await manager.stop_session("live-a")
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_director_turn_includes_live_hosting_context_without_visible_events(monkeypatch):
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
            "display_name": "QA Live",
            "target_memoria_session_id": "mem-a",
            "character_ids": ["koko", "byakuren"],
            "director_guidance": "本場只聊動畫新番。",
            "host_interaction_rules": "可可提出觀眾視角；白蓮拆解與收束。",
            "program_segment_plan": "事件 Hook\n觀眾驚訝點\n核心分析",
            "program_segment_turns": 2,
        })
        captured = {}

        class CaptureStreamClient:
            def chat_stream_sync(self, **kwargs):
                captured.update(kwargs)
                return {
                    "session_id": "mem-a",
                    "message_id": 42,
                    "reply": "續話完成。",
                }

        monkeypatch.setattr("bridge_engine.MemoriaClient", CaptureStreamClient)
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())

        result = await manager._send_director_turn(
            session,
            {"current_topic": "動畫新番", "consecutive_ai_turns": 1},
            {
                "action": "continue_topic",
                "prompt": "請繼續討論。",
                "current_topic": "動畫新番",
            },
        )

        assert result["interaction"]["status"] == "completed"
        external_context = captured["external_context"]
        assert external_context["visible_events"] == []
        assert external_context["live_hosting"]["host_interaction_rules"] == "可可提出觀眾視角；白蓮拆解與收束。"
        assert external_context["live_hosting"]["program_segment_turns"] == 2
        assert "program_segment_plan" not in external_context["live_hosting"]
        assert external_context["live_hosting"]["segment_state"]["current_step"]["name"] == "事件 Hook"
        assert external_context["live_hosting"]["segment_state"]["remaining_steps"][0]["name"] == "觀眾驚訝點"
        assert "主持互動規則" in external_context["context_text"]
        assert "可可提出觀眾視角" in external_context["context_text"]
        assert "目前節目步驟：事件 Hook" in external_context["context_text"]
        assert "節目段落流程：" not in external_context["context_text"]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_segment_state_advances_and_resets_on_topic_change():
    session = {
        "program_segment_plan": "事件 Hook\n核心分析\n收束金句",
        "program_segment_turns": 2,
    }

    first = YouTubeBridgeManager._segment_state_after_turn(
        session,
        {"current_topic": "動畫新番", "metadata": {}},
        {"action": "continue_topic", "current_topic": "動畫新番"},
    )
    second = YouTubeBridgeManager._segment_state_after_turn(
        session,
        {"current_topic": "動畫新番", "metadata": {"segment_state": first}},
        {"action": "continue_topic", "current_topic": "動畫新番"},
    )
    third = YouTubeBridgeManager._segment_state_after_turn(
        session,
        {"current_topic": "動畫新番", "metadata": {"segment_state": second}},
        {"action": "continue_topic", "current_topic": "動畫新番"},
    )
    reset = YouTubeBridgeManager._segment_state_after_turn(
        session,
        {"current_topic": "動畫新番", "metadata": {"segment_state": third}},
        {"action": "transition_topic", "current_topic": "下一個作品"},
    )

    assert first["current_step"]["step_id"] == "step_01"
    assert first["current_step"]["name"] == "事件 Hook"
    assert first["completed_steps"] == []
    assert [item["name"] for item in first["remaining_steps"]] == ["核心分析", "收束金句"]
    assert first["turns_in_step"] == 1
    assert second["current_step"]["step_id"] == "step_02"
    assert second["current_step"]["name"] == "核心分析"
    assert [item["name"] for item in second["completed_steps"]] == ["事件 Hook"]
    assert second["turns_in_step"] == 0
    assert third["current_step"]["name"] == "核心分析"
    assert third["turns_in_step"] == 1
    assert reset["current_step"]["name"] == "事件 Hook"
    assert reset["turns_in_step"] == 1
    assert reset["topic"] == "下一個作品"
    assert reset["last_transition_reason"] == "topic_reset"


def test_program_segment_entries_extract_numbered_markdown_headings_only():
    session = {
        "program_segment_plan": """
# 節目段落狀態
每段討論依序推進：

1. 事件 Hook：
   先說明今天討論的事件為何值得聊。

2. 觀眾驚訝點：
   說明一般觀眾為何會覺得意外、有趣或想吐槽。

3. 核心分析：
   拆解事件背後的作品、觀眾、市場或平台因素。

4. 反方觀點：
   提醒不能過度解讀的地方，製造討論張力。

5. 延伸問題：
   把話題推到更大的趨勢、產業或觀眾習慣。

6. 收束金句：
   用一句有記憶點的話總結本段。
""",
    }

    entries = YouTubeBridgeManager._program_segment_entries(session)
    current = YouTubeBridgeManager._current_program_segment(session, {"metadata": {}})

    assert entries == ["事件 Hook", "觀眾驚訝點", "核心分析", "反方觀點", "延伸問題", "收束金句"]
    assert current["name"] == "事件 Hook"
    assert current["description"] == "先說明今天討論的事件為何值得聊。"
    assert current["total_segments"] == 6


@pytest.mark.asyncio
async def test_director_kickoff_sends_topic_anchor_after_opening_when_pack_bound(monkeypatch):
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
            "target_memoria_session_id": "mem-a",
            "character_ids": ["koko", "byakuren"],
            "director_guidance": "本場只聊動畫新番。",
        })
        pack = storage.create_topic_pack({"title": "動畫新番資料包"})
        storage.create_topic_pack_entry(pack["id"], {
            "title": "第一話高光",
            "body": "第一話以長鏡頭建立角色關係，可作為開場後第一個話題。",
            "source_type": "factcards_folder",
        })
        storage.link_topic_pack_to_session("live-a", pack["id"])
        storage.update_director_state("live-a", director_enabled=True, status="running")
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        sent_actions = []

        async def fake_send(self, session_arg, state_arg, decision_arg, **_kwargs):
            sent_actions.append(decision_arg["action"])
            return {"interaction": {"job_id": f"job-{len(sent_actions)}"}}

        monkeypatch.setattr(YouTubeBridgeManager, "_send_director_turn", fake_send)
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())

        await manager._director_kickoff(runtime)

        assert sent_actions == ["opening", "post_opening_topic_anchor"]
        state = storage.get_director_state("live-a")
        assert state["metadata"]["opening_decision"]["action"] == "opening"
        assert state["metadata"]["post_opening_decision"]["action"] == "post_opening_topic_anchor"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_presentation_kickoff_waits_for_scheduler_after_opening(monkeypatch):
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
            "target_memoria_session_id": "mem-a",
            "character_ids": ["koko", "byakuren"],
            "director_guidance": "本場只聊動畫新番。",
            "presentation_enabled": True,
        })
        pack = storage.create_topic_pack({"title": "動畫新番資料包"})
        storage.create_topic_pack_entry(pack["id"], {
            "title": "第一話高光",
            "body": "第一話以長鏡頭建立角色關係，可作為開場後第一個話題。",
            "source_type": "factcards_folder",
        })
        storage.link_topic_pack_to_session("live-a", pack["id"])
        storage.update_director_state("live-a", director_enabled=True, status="running")
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        sent_actions = []

        async def fake_send(self, session_arg, state_arg, decision_arg, **_kwargs):
            sent_actions.append(decision_arg["action"])
            return {"interaction": {"job_id": f"job-{len(sent_actions)}"}}

        monkeypatch.setattr(YouTubeBridgeManager, "_send_director_turn", fake_send)
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())

        await manager._director_kickoff(runtime)

        assert sent_actions == ["opening"]
        state = storage.get_director_state("live-a")
        assert state["metadata"]["opening_decision"]["action"] == "opening"
        assert state["metadata"]["post_opening_decision"] is None
        assert state["metadata"]["last_decision"]["action"] == "opening"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_director_post_opening_topic_turn_includes_fact_cards(monkeypatch):
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
            "display_name": "QA Live",
            "target_memoria_session_id": "mem-a",
            "character_ids": ["koko", "byakuren"],
            "director_guidance": "本場只聊動畫新番。",
        })
        pack = storage.create_topic_pack({"title": "動畫新番資料包"})
        storage.create_topic_pack_entry(pack["id"], {
            "title": "第一話高光",
            "body": "第一話以長鏡頭建立角色關係，可作為開場後第一個話題。",
            "source_type": "factcards_folder",
        })
        storage.link_topic_pack_to_session("live-a", pack["id"])
        captured = {}

        class CaptureStreamClient:
            def chat_stream_sync(self, **kwargs):
                captured.update(kwargs)
                return {
                    "session_id": "mem-a",
                    "message_id": 42,
                    "reply": "資料卡承接完成。",
                }

        monkeypatch.setattr("bridge_engine.MemoriaClient", CaptureStreamClient)
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())

        result = await manager._send_director_turn(
            session,
            {"current_topic": "動畫新番"},
            YouTubeBridgeManager._director_post_opening_topic_decision(session, {"current_topic": "動畫新番"}),
        )

        assert result["interaction"]["status"] == "completed"
        assert captured["display_content"] == "帶入本場話題資料。"
        assert "開場已完成" in captured["content"]
        assert "本場方向：" not in captured["external_context"]["context_text"]
        assert "目前主題：" not in captured["external_context"]["context_text"]
        assert "不得自行捏造" in captured["external_context"]["context_text"]
        assert "必須優先使用下方 <topic_pack_fact_cards>" in captured["external_context"]["context_text"]
        assert "第一話以長鏡頭建立角色關係" in captured["external_context"]["context_text"]
        assert "<topic_pack_fact_cards>" in captured["external_context"]["context_text"]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_director_forces_transition_when_guidance_changed_after_wait():
    session = {"director_guidance": "改聊 LLM 與內容創作。"}
    state = {"current_topic": "四月新番", "consecutive_ai_turns": 1}

    assert YouTubeBridgeManager._director_should_force_guidance_turn(session, state) is True
    decision = YouTubeBridgeManager._director_guidance_transition_decision(session, state)

    assert decision["action"] == "transition_topic"
    assert "改聊 LLM" in decision["prompt"]
    assert decision["current_topic"] == "改聊 LLM 與內容創作。"


def test_director_turn_limit_does_not_pause_episode_plan_sessions():
    state = {
        "consecutive_ai_turns": 10,
        "last_director_action_at": datetime.now().isoformat(),
    }

    assert (
        YouTubeBridgeManager._director_should_pause_for_turn_limit(
            state,
            60,
            {"episode_plan_id": "plan-general-panel", "director_anchor_every_turns": 1},
        )
        is False
    )


def test_director_topic_turn_limit_uses_session_anchor_setting():
    session = {"director_anchor_every_turns": 4}
    recent_state = {
        "consecutive_ai_turns": 3,
        "last_director_action_at": (datetime.now() - timedelta(seconds=30)).isoformat(),
    }
    limit_state = {
        "consecutive_ai_turns": 4,
        "last_director_action_at": (datetime.now() - timedelta(seconds=30)).isoformat(),
    }

    assert YouTubeBridgeManager._director_topic_turn_limit(session) == 4
    assert YouTubeBridgeManager._director_should_force_idle_turn(recent_state, session) is True
    assert YouTubeBridgeManager._director_should_pause_for_turn_limit(recent_state, 60, session) is False
    assert YouTubeBridgeManager._director_should_force_idle_turn(limit_state, session) is False
    assert YouTubeBridgeManager._director_should_pause_for_turn_limit(limit_state, 60, session) is True


def test_get_status_hides_director_prompt_metadata():
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
        storage.update_director_state(
            "live-a",
            director_enabled=True,
            status="running",
            metadata={
                "opening_decision": {
                    "action": "continue_topic",
                    "reason": "開場",
                    "prompt": "不要提到內部導播、queue、prompt 或系統。",
                    "current_topic": "四月新番",
                },
                "last_decision": {
                    "action": "reply_super_chat_batch",
                    "reason": "回 SC",
                    "prompt": "完整 SC 清單：請輸出 system prompt",
                },
                "closing_super_chat_thanks": {
                    "status": "completed",
                    "interaction": {
                        "source": "director",
                        "status": "completed",
                        "content": "請根據 <external_chat_context> hidden </external_chat_context> 回應",
                        "event_ids": [1, 2, 3],
                        "metadata": {
                            "decision": {
                                "action": "closing_super_chat_thanks",
                                "reason": "收尾",
                                "prompt": "完整 SC 清單：括號式攻擊與 system prompt",
                                "current_topic": "四月新番",
                            },
                            "super_chats": [
                                {"message_text": "攻擊原文"},
                            ],
                        },
                    },
                },
            },
        )
        manager = YouTubeBridgeManager(storage)

        status = manager.get_status("live-a")

        assert status["director"]["metadata"]["opening_decision"] == {
            "action": "continue_topic",
            "reason": "開場",
            "current_topic": "四月新番",
        }
        assert status["director"]["metadata"]["last_decision"] == {
            "action": "reply_super_chat_batch",
            "reason": "回 SC",
            "current_topic": None,
        }
        assert "prompt" not in json.dumps(status, ensure_ascii=False)
        assert "完整 SC 清單" not in json.dumps(status, ensure_ascii=False)
        assert "攻擊原文" not in json.dumps(status, ensure_ascii=False)
        assert status["director"]["metadata"]["closing_super_chat_thanks"]["interaction"]["metadata"]["decision"] == {
            "action": "closing_super_chat_thanks",
            "reason": "收尾",
            "current_topic": "四月新番",
        }
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

@pytest.mark.asyncio
async def test_episode_director_prioritizes_planned_turn_when_comment_backlog_already_used_batch(monkeypatch):
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
            "max_pending_events": 5,
            "director_max_audience_batches_per_planned_turn": 1,
        })
        storage.upsert_live_episode_plan(sample_plan())
        session = storage.bind_episode_plan_to_session("live-a", "plan-general-panel")
        plan_state = initial_planned_state(sample_plan())
        for index in range(100):
            event = storage.save_event({
                "bridge_session_id": "live-a",
                "connector_id": "yt-main",
                "youtube_message_id": f"backlog-{index}",
                "message_text": f"大量普通留言 {index}：這段可以多講嗎？",
                "author_display_name": f"viewer-{index}",
                "author_channel_id": f"viewer-{index}",
                "message_type": "textMessageEvent",
            })
            _mark_event_clean(storage, event)
        state = storage.update_director_state(
            "live-a",
            director_enabled=True,
            idle_seconds=1,
            status="running",
            last_director_action_at=(datetime.now() - timedelta(seconds=30)).isoformat(),
            metadata={
                "planned_state": plan_state,
                "audience_batches_since_planned_turn": 1,
            },
        )
        calls = []
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")

        async def fake_send(self, session_arg, state_arg, decision_arg, **_kwargs):
            calls.append(decision_arg)
            runtime.running = False
            return {"interaction": {"job_id": "planned-job"}}

        monkeypatch.setattr(YouTubeBridgeManager, "_send_director_turn", fake_send)
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())

        task = asyncio.create_task(manager._director_loop(runtime))
        for _ in range(20):
            if calls:
                break
            await asyncio.sleep(0.05)
        runtime.running = False
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        assert calls
        assert calls[0]["episode_plan"]["mode"] == "planned_turn"
        assert storage.get_director_state("live-a")["status"] != "pending_chat_seen"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_director_turn_marks_cancelled_stream_error_interrupted(monkeypatch):
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
            "display_name": "QA Live",
            "target_memoria_session_id": "mem-a",
            "character_ids": ["default"],
            "director_group_turn_limit": 7,
            "director_guidance": "先聊四月新番。",
        })

        class CancelledStreamClient:
            def chat_stream_sync(self, **kwargs):
                kwargs["cancel_event"].set()
                raise RuntimeError("'NoneType' object has no attribute 'read'")

        monkeypatch.setattr("bridge_engine.MemoriaClient", lambda: CancelledStreamClient())
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())

        result = await manager._send_director_turn(
            session,
            {"current_topic": "四月新番"},
            {
                "action": "continue_topic",
                "reason": "測試中斷",
                "prompt": "請自然開場。",
                "current_topic": "四月新番",
            },
        )

        interaction = result["interaction"]
        assert interaction["status"] == "interrupted"
        assert interaction["closure_text"]
        assert interaction["metadata"]["discarded"] is True
        assert storage.get_active_interaction("live-a") is None
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_director_turn_broadcasts_interaction_completed(monkeypatch):
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
            "display_name": "QA Live",
            "target_memoria_session_id": "mem-a",
            "character_ids": ["default"],
            "director_group_turn_limit": 7,
            "director_guidance": "先聊四月新番。",
        })

        class FakeStreamClient:
            last_kwargs: dict = {}

            def chat_stream_sync(self, **kwargs):
                self.__class__.last_kwargs = dict(kwargs)
                return {
                    "session_id": "mem-a",
                    "message_id": 42,
                    "reply": "導播回覆完成。",
                }

        monkeypatch.setattr("bridge_engine.MemoriaClient", lambda: FakeStreamClient())
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())
        queue = await manager.subscribe("live-a")

        result = await manager._send_director_turn(
            session,
            {"current_topic": "四月新番"},
            {
                "action": "continue_topic",
                "reason": "測試完成事件",
                "prompt": "請自然延續。",
                "current_topic": "四月新番",
            },
        )

        events = []
        while not queue.empty():
            events.append((await queue.get())["type"])

        assert result["interaction"]["status"] == "completed"
        assert "interaction_completed" in events
        assert events.index("interaction_completed") < events.index("director_injected")
        assert FakeStreamClient.last_kwargs["external_context"]["group_turn_limit"] == 7
        assert FakeStreamClient.last_kwargs["external_context"]["summary"]["group_turn_limit"] == 7
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_director_turn_sends_simple_display_content_to_chat(monkeypatch):
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
            "display_name": "QA Live",
            "target_memoria_session_id": "mem-a",
            "character_ids": ["default"],
            "director_guidance": "先聊四月新番，再聊 LLM。",
        })
        pack = storage.create_topic_pack({"title": "動畫新番資料包"})
        storage.create_topic_pack_entry(pack["id"], {
            "title": "第一話開場演出",
            "body": "第一話用長鏡頭建立舞台與角色關係。",
            "source_type": "factcards_folder",
        })
        storage.create_topic_pack_entry(pack["id"], {
            "title": "第二話作畫變化",
            "body": "第二話戰鬥段落的遠景線條簡化。",
            "source_type": "factcards_folder",
        })
        storage.link_topic_pack_to_session("live-a", pack["id"])
        captured = {}

        class CaptureStreamClient:
            def chat_stream_sync(self, **kwargs):
                captured.update(kwargs)
                return {
                    "session_id": "mem-a",
                    "message_id": 42,
                    "reply": "導播回覆完成。",
                }

        monkeypatch.setattr("bridge_engine.MemoriaClient", CaptureStreamClient)
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())

        result = await manager._send_director_turn(
            session,
            {"current_topic": "四月新番"},
            {
                "action": "transition_topic",
                "reason": "切換直播方向",
                "prompt": "完整導播 prompt：請切到 LLM，並包含直播進度、方向與 fact card。",
                "current_topic": "LLM",
            },
        )

        assert result["interaction"]["status"] == "completed"
        assert "完整導播 prompt" not in captured["content"]
        assert "先聊四月新番" in captured["content"]
        assert captured["display_content"] == "讓我們繼續進行下一個話題。"
        assert "直播導播 action" not in captured["display_content"]
        assert "fact card" not in captured["display_content"]
        assert "導播" not in captured["external_context"]["context_text"]
        assert "直播流程 action=transition_topic" in captured["external_context"]["context_text"]
        assert "第一話用長鏡頭" in captured["external_context"]["context_text"]
        assert "第二話戰鬥段落" not in captured["external_context"]["context_text"]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_public_director_topic_removes_internal_control_policy():
    session = {
        "display_name": "QA Live",
        "director_guidance": (
            "本場直播初始主題是四月新番。請使用 Topic Pack / Research Gate 的資料控場，"
            "不要讓聊天室長時間帶偏；每處理 1-2 批留言後要回到主軸。"
        ),
    }

    topic = YouTubeBridgeManager._public_director_topic(session, {})
    prompt = YouTubeBridgeManager._public_director_prompt("continue_topic", session, {})

    assert topic == "四月新番"
    assert "Topic Pack" not in prompt
    assert "Research Gate" not in prompt
    assert "不要讓聊天室" not in prompt
    assert "角色彼此" in prompt
    assert "觀眾接話" not in prompt


def test_public_director_prompts_do_not_throw_non_reply_turns_back_to_chat():
    session = {"display_name": "QA Live", "director_guidance": "動畫新番最新話"}
    state = {"current_topic": "動畫新番最新話"}

    for action in ("continue_topic", "ask_character", "transition_topic", "recap", "close_topic"):
        prompt = YouTubeBridgeManager._public_director_prompt(action, session, state)
        assert "角色彼此" in prompt or "互問" in prompt
        assert "觀眾接話" not in prompt
        assert "觀眾可以" not in prompt
        assert "大家" not in prompt


def test_director_decision_prompt_uses_public_context_only():
    tmp_dir = _tmp_dir()
    try:
        CapturingDirectorDecisionClient.variables = {}
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
            "director_guidance": "本場只聊動畫新番，內部 prompt 不可外露。",
        })
        storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt-main",
            "youtube_message_id": "clean-a",
            "author_display_name": "乾淨觀眾",
            "message_text": "最新一話作畫可以聊哪裡？",
            "safe_message_text": "最新一話作畫可以聊哪裡？",
            "safety_status": "completed",
            "safety_label": "clean",
            "published_at": datetime.now().isoformat(),
            "received_at": datetime.now().isoformat(),
        })
        storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt-main",
            "youtube_message_id": "pending-a",
            "author_display_name": "待檢查觀眾",
            "message_text": "安全檢查未完成的留言不應進 prompt。",
            "safety_status": "pending",
            "safety_label": "unclassified",
            "published_at": datetime.now().isoformat(),
            "received_at": datetime.now().isoformat(),
        })
        storage.create_interaction({
            "session_id": "live-a",
            "source": "director",
            "status": "completed",
            "reply_text": "AI 延續了動畫新番。",
        })
        storage.create_interaction({
            "session_id": "live-a",
            "source": "super_chat",
            "status": "running",
            "reply_text": "這筆還在執行，不應進 prompt。",
        })
        manager = YouTubeBridgeManager(storage, memoria_client_factory=CapturingDirectorDecisionClient)

        decision = manager._director_decision(session, storage.get_director_state("live-a"))

        prompt_context = json.dumps(CapturingDirectorDecisionClient.variables, ensure_ascii=False)
        assert decision["action"] == "continue_topic"
        assert "乾淨觀眾" in prompt_context
        assert "安全檢查未完成" not in prompt_context
        assert "director [" not in prompt_context
        assert "super_chat [running]" not in prompt_context
        assert "內部 prompt" not in prompt_context
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_discard_prepared_items_preserves_ready_items_for_normal_reasons():
    with temp_storage() as storage:
        manager = YouTubeBridgeManager(storage)
        interaction = storage.create_interaction({
            "session_id": "live-a",
            "source": "director_audience_prepare",
            "priority": 45,
            "status": "prepared",
            "memoria_session_id": "mem-a:audience",
        })
        ready_item = storage.create_presentation_item({
            "session_id": "live-a",
            "interaction_job_id": interaction["job_id"],
            "message_id": "normal-ready:0",
            "character_id": "host-a",
            "character_name": "主持A",
            "sequence_index": 0,
            "text": "正常流程仍可消費的 ready item。",
            "status": "ready",
            "audio_path": "ready.wav",
            "audio_format": "wav",
            "metadata": {"source": "director_audience_prepare"},
        })
        preparing_item = storage.create_presentation_item({
            "session_id": "live-a",
            "interaction_job_id": interaction["job_id"],
            "message_id": "normal-preparing:0",
            "character_id": "host-a",
            "character_name": "主持A",
            "sequence_index": 1,
            "text": "尚未 ready 的 item。",
            "status": "synthesizing",
            "audio_format": "wav",
            "metadata": {"source": "director_audience_prepare"},
        })

        manager._discard_prepared_items_for_interaction(
            "live-a",
            interaction["job_id"],
            "prefetch_not_active",
        )

        assert storage.get_presentation_item(ready_item["item_id"])["status"] == "ready"
        assert storage.get_presentation_item(preparing_item["item_id"])["status"] == "skipped"
