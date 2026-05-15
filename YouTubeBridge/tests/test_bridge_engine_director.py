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
from live_episode_plan_contract import initial_planned_state
from test_live_episode_plan_contract import sample_plan
from tts_gpt_sovits import TTSResult


def _episode_plan_characters() -> list[dict]:
    return [
        {"character_id": "host-a", "name": "主持A"},
        {"character_id": "analyst-b", "name": "分析B"},
        {"character_id": "skeptic-c", "name": "質疑C"},
    ]


async def _next_queue_event(queue: asyncio.Queue, event_type: str, *, timeout: float = 1.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining = max(0.01, deadline - time.monotonic())
        event = await asyncio.wait_for(queue.get(), timeout=remaining)
        if event.get("type") == event_type:
            return event
    raise AssertionError(f"{event_type} was not received before timeout")


async def _wait_until(condition, *, timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition was not met before timeout")


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
        assert "最多句數：" in context
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
        assert "最多句數：" in context
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
        assert "本段最多 3 次角色發言" in captured["external_context"]["context_text"]
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
                    "session_id": "mem-opening" if turn_id == "seg_01_turn_01" else "mem-cohost",
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
        assert memoria_calls == [
            {"turn_id": "seg_01_turn_01", "session_id": "mem-a"},
            {"turn_id": "seg_01_turn_02", "session_id": "mem-opening"},
        ]
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
        assert "本段最多 1 次角色發言" in external_context["context_text"]
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
async def test_director_loop_uses_legacy_decision_when_no_episode_plan(monkeypatch):
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
            "director_guidance": "先聊四月新番。",
            "target_memoria_session_id": "mem-a",
            "character_ids": ["koko", "byakuren"],
        })
        storage.update_director_state(
            "live-a",
            director_enabled=True,
            idle_seconds=10,
            status="running",
            last_director_action_at=(datetime.now() - timedelta(seconds=30)).isoformat(),
        )
        calls = []
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")

        def fake_decision(self, session, state):
            calls.append("legacy")
            return {
                "action": "continue_topic",
                "reason": "legacy",
                "prompt": "續話。",
                "current_topic": "四月新番",
            }

        async def fake_send(self, session, state, decision):
            runtime.running = False
            return {"interaction": {"job_id": "fake-job"}}

        monkeypatch.setattr(YouTubeBridgeManager, "_director_decision", fake_decision)
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

        assert calls == ["legacy"]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_director_loop_uses_episode_plan_decision_when_plan_bound(monkeypatch):
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

        def forbidden_legacy_decision(self, session, state):
            raise AssertionError(
                "episode plan sessions must not use legacy LLM director decision for planned turns"
            )

        async def fake_send(self, session, state, decision):
            calls.append(decision["episode_plan"]["turn_contract"]["turn_id"])
            runtime.running = False
            return {"interaction": {"job_id": "fake-job"}}

        monkeypatch.setattr(YouTubeBridgeManager, "_director_decision", forbidden_legacy_decision)
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

        assert calls == ["seg_01_turn_01"]
        director_state = storage.get_director_state("live-a")
        assert director_state["consecutive_ai_turns"] == 0
        planned_state = director_state["metadata"]["planned_state"]
        assert planned_state["last_planned_turn_contract_id"] == "seg_01_turn_01"
    finally:
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
async def test_director_loop_does_not_idle_gate_next_episode_plan_turn(monkeypatch):
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
            "director_anchor_every_turns": 2,
        })
        plan = sample_plan()
        storage.upsert_live_episode_plan(plan)
        session = storage.bind_episode_plan_to_session("live-a", "plan-general-panel")
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())
        planned_state = manager._planned_state_after_episode_turn(
            plan,
            initial_planned_state(plan),
            plan["segments"][0]["planned_turn_contracts"][0],
        )
        storage.update_director_state(
            "live-a",
            director_enabled=True,
            idle_seconds=60,
            status="running",
            consecutive_ai_turns=2,
            last_director_action_at=(datetime.now() - timedelta(seconds=10)).isoformat(),
            metadata={"planned_state": planned_state},
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
        assert session["episode_plan_id"] == "plan-general-panel"
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
async def test_director_loop_finalizes_when_episode_plan_completed(monkeypatch):
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
            "director_anchor_every_turns": 2,
        })
        plan = sample_plan()
        storage.upsert_live_episode_plan(plan)
        storage.bind_episode_plan_to_session("live-a", "plan-general-panel")
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())
        planned_state = initial_planned_state(plan)
        for turn in plan["segments"][0]["planned_turn_contracts"]:
            planned_state = manager._planned_state_after_episode_turn(plan, planned_state, turn)
        storage.update_director_state(
            "live-a",
            director_enabled=True,
            idle_seconds=10,
            status="running",
            consecutive_ai_turns=1,
            last_director_action_at=(datetime.now() - timedelta(seconds=30)).isoformat(),
            metadata={"planned_state": planned_state},
        )
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        finalized: list[dict] = []

        def forbidden_legacy_decision(self, session, state):
            raise AssertionError("completed episode plans must not fall back to legacy director decisions")

        async def forbidden_send(self, session, state, decision):
            raise AssertionError("completed episode plans must not send the final planned turn again")

        async def fake_finalize(self, runtime_arg, session_arg, planned_state_arg):
            finalized.append({
                "session_id": runtime_arg.session_id,
                "plan_status": planned_state_arg.get("plan_status"),
            })
            runtime_arg.running = False
            self.storage.update_director_state(
                runtime_arg.session_id,
                director_enabled=False,
                status="ended",
                metadata={
                    "finalized_by": "episode_plan_complete",
                    "planned_state": planned_state_arg,
                },
            )

        monkeypatch.setattr(YouTubeBridgeManager, "_director_decision", forbidden_legacy_decision)
        monkeypatch.setattr(YouTubeBridgeManager, "_send_director_turn", forbidden_send)
        monkeypatch.setattr(YouTubeBridgeManager, "_finalize_for_episode_plan_completed", fake_finalize)

        task = asyncio.create_task(manager._director_loop(runtime))
        for _ in range(20):
            if finalized:
                break
            await asyncio.sleep(0.05)
        runtime.running = False
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        director_state = storage.get_director_state("live-a")
        assert finalized == [{"session_id": "live-a", "plan_status": "completed"}]
        assert director_state["status"] == "ended"
        assert director_state["metadata"]["planned_state"]["plan_status"] == "completed"
        assert director_state["metadata"]["finalized_by"] == "episode_plan_complete"
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

        async def fake_send(self, session_arg, state_arg, decision_arg):
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

        async def fake_send(self, session_arg, state_arg, decision_arg):
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

def test_director_forces_idle_continue_when_wait_has_no_blockers():
    session = {
        "display_name": "QA Live",
        "director_guidance": "先聊四月新番。",
    }
    state = {"current_topic": "四月新番", "consecutive_ai_turns": 1}

    assert YouTubeBridgeManager._director_should_force_idle_turn(state) is True
    decision = YouTubeBridgeManager._director_idle_continue_decision(session, state)

    assert decision["action"] == "continue_topic"
    assert "四月新番" in decision["prompt"]
    assert "角色彼此" in decision["prompt"]
    assert "丟回聊天室" in decision["prompt"]
    assert "觀眾接話" not in decision["prompt"]

def test_director_turn_limit_releases_after_idle_window():
    recent_state = {
        "consecutive_ai_turns": 2,
        "last_director_action_at": (datetime.now() - timedelta(seconds=30)).isoformat(),
    }
    stale_state = {
        "consecutive_ai_turns": 2,
        "last_director_action_at": (datetime.now() - timedelta(seconds=130)).isoformat(),
    }

    assert YouTubeBridgeManager._director_should_pause_for_turn_limit(recent_state, 60) is True
    assert YouTubeBridgeManager._director_should_pause_for_turn_limit(stale_state, 60) is False
    assert YouTubeBridgeManager._director_should_pause_for_turn_limit({"consecutive_ai_turns": 1}, 60) is False

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
async def test_start_director_allows_one_second_idle():
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
        })
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())

        state = await manager.start_director("live-a", idle_seconds=1, kickoff=False)

        assert state["idle_seconds"] == 1
        assert storage.get_director_state("live-a")["idle_seconds"] == 1
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_director_loop_applies_idle_update_without_restart(monkeypatch):
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
            "director_guidance": "先聊四月新番。",
        })
        storage.update_director_state(
            "live-a",
            director_enabled=True,
            idle_seconds=60,
            status="running",
            last_director_action_at=(datetime.now() - timedelta(seconds=30)).isoformat(),
        )
        calls = []

        def fake_decision(self, session, state):
            return {
                "action": "continue_topic",
                "reason": "idle 已到，延續測試話題。",
                "prompt": "請自然延續本場直播話題。",
                "current_topic": session.get("director_guidance", ""),
            }

        async def fake_send(self, session, state, decision):
            calls.append((session["session_id"], state["idle_seconds"], decision["action"]))
            return {"interaction": {"job_id": "fake-job"}}

        monkeypatch.setattr(YouTubeBridgeManager, "_director_decision", fake_decision)
        monkeypatch.setattr(YouTubeBridgeManager, "_send_director_turn", fake_send)
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())

        await manager.start_session("live-a")
        await asyncio.sleep(0.2)
        assert calls == []

        await manager.start_director("live-a", idle_seconds=10, guidance="改聊 LLM。", kickoff=False)
        for _ in range(30):
            if calls:
                break
            await asyncio.sleep(0.05)

        assert calls
        assert calls[0] == ("live-a", 10, "continue_topic")
        assert storage.get_director_state("live-a")["last_director_action_at"]

        await manager.stop_session("live-a")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

@pytest.mark.asyncio
async def test_director_idle_ignores_pending_safety_events(monkeypatch):
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
            "director_guidance": "先聊四月新番。",
            "target_memoria_session_id": "mem-a",
            "character_ids": ["koko", "byakuren"],
        })
        storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt-main",
            "youtube_message_id": "pending-a",
            "message_text": "這則還在安全檢查，不應永遠卡住導播。",
            "published_at": datetime.now().isoformat(),
            "received_at": datetime.now().isoformat(),
            "status": "active",
            "safety_status": "pending",
            "safety_label": "unclassified",
        })
        storage.update_director_state(
            "live-a",
            director_enabled=True,
            idle_seconds=10,
            status="running",
            last_director_action_at=(datetime.now() - timedelta(seconds=30)).isoformat(),
        )
        calls = []
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")

        def fake_decision(self, session, state):
            return {
                "action": "continue_topic",
                "reason": "pending safety 不阻塞 idle。",
                "prompt": "請自然延續目前話題。",
                "current_topic": "四月新番",
            }

        async def fake_send(self, session, state, decision):
            calls.append(decision["action"])
            runtime.running = False
            return {"interaction": {"job_id": "fake-job"}}

        monkeypatch.setattr(YouTubeBridgeManager, "_director_decision", fake_decision)
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

        assert calls == ["continue_topic"]
        assert storage.get_director_state("live-a")["status"] != "pending_chat_seen"
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
async def test_director_loop_blocks_closing_thanks_before_duration_finalize(monkeypatch):
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
            "director_guidance": "先聊動畫新番。",
            "current_topic": "動畫新番",
            "auto_finalize_on_duration": True,
            "auto_sc_thanks_on_finalize": True,
            "planned_duration_minutes": 10,
            "started_at": datetime.now().isoformat(),
            "target_memoria_session_id": "mem-a",
            "character_ids": ["koko", "byakuren"],
        })
        storage.update_director_state(
            "live-a",
            director_enabled=True,
            idle_seconds=10,
            status="running",
            current_topic="動畫新番",
            consecutive_ai_turns=0,
            last_director_action_at=(datetime.now() - timedelta(seconds=30)).isoformat(),
        )
        calls = []
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")

        def premature_closing_decision(self, session_arg, state_arg):
            return {
                "action": "closing_super_chat_thanks",
                "reason": "LLM 過早判斷要收尾。",
                "prompt": "直播即將收尾，請感謝本場 Super Chat。",
                "current_topic": "動畫新番",
            }

        async def fake_send(self, session_arg, state_arg, decision_arg):
            calls.append(decision_arg)
            runtime.running = False
            return {"interaction": {"job_id": "fake-job"}}

        monkeypatch.setattr(YouTubeBridgeManager, "_director_decision", premature_closing_decision)
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
        assert calls[0]["action"] == "continue_topic"
        assert "Super Chat" not in calls[0]["prompt"]
        assert storage.get_session("live-a")["status"] != "ended"
        assert storage.list_interactions("live-a") == []
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

@pytest.mark.asyncio
async def test_director_loop_blocks_time_based_recap_before_duration_finalize(monkeypatch):
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
            "director_guidance": "先聊動畫新番。",
            "auto_finalize_on_duration": True,
            "planned_duration_minutes": 10,
            "started_at": datetime.now().isoformat(),
            "target_memoria_session_id": "mem-a",
            "character_ids": ["koko", "byakuren"],
        })
        storage.update_director_state(
            "live-a",
            director_enabled=True,
            idle_seconds=10,
            status="running",
            current_topic="動畫新番",
            consecutive_ai_turns=0,
            last_director_action_at=(datetime.now() - timedelta(seconds=30)).isoformat(),
        )
        calls = []
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")

        def premature_recap_decision(self, session_arg, state_arg):
            return {
                "action": "recap",
                "reason": "elapsed_percent 已達 80%，需要為直播收尾做準備。",
                "prompt": "我們來回顧一下並準備收尾。",
                "current_topic": "動畫新番",
            }

        async def fake_send(self, session_arg, state_arg, decision_arg):
            calls.append(decision_arg)
            runtime.running = False
            return {"interaction": {"job_id": "fake-job"}}

        monkeypatch.setattr(YouTubeBridgeManager, "_director_decision", premature_recap_decision)
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
        assert calls[0]["action"] == "continue_topic"
        assert "收尾" not in calls[0]["prompt"]
        assert storage.get_session("live-a")["status"] != "ended"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

@pytest.mark.asyncio
async def test_start_director_guidance_change_resets_turn_limit():
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
            "director_guidance": "先聊四月新番。",
        })
        storage.update_director_state(
            "live-a",
            director_enabled=True,
            consecutive_ai_turns=2,
            status="turn_limit_wait",
        )
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())

        state = await manager.start_director("live-a", idle_seconds=10, guidance="改聊美食。", kickoff=False)

        assert state["consecutive_ai_turns"] == 0
        assert state["status"] == "running"
        assert state["metadata"]["guidance_reset_turn_limit"] is True
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
