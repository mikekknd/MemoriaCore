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
