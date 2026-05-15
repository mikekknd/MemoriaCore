import json
import asyncio
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from bridge_engine_test_support import (
    BridgeStorage,
    FakeClosingMemoriaClient,
    LiveRuntime,
    YouTubeBridgeManager,
)
from storage import DEFAULT_CONNECTOR_ID


def _storage(tmp_path: Path) -> BridgeStorage:
    storage = BridgeStorage(tmp_path / "youtube_live.db")
    storage.upsert_connector({
        "connector_id": DEFAULT_CONNECTOR_ID,
        "display_name": "YouTube Main",
        "api_key": "",
        "enabled": True,
    })
    return storage


def _create_session(storage: BridgeStorage, **overrides) -> dict:
    config = {
        "session_id": "live-a",
        "connector_id": DEFAULT_CONNECTOR_ID,
        "display_name": "Phase Pipeline",
        "status": "running",
        "target_memoria_session_id": "mem-a",
        "character_ids": ["koko", "byakuren"],
        "auto_sc_thanks_on_finalize": True,
        "post_plan_free_talk_enabled": True,
        "post_plan_free_talk_topic_pack_ids": ["casual"],
        "post_plan_free_talk_idle_turns_min": 4,
        "post_plan_free_talk_idle_turns_max": 4,
        "post_plan_free_talk_tick_interval_seconds": 5,
    }
    config.update(overrides)
    return storage.upsert_session(config)


def _topic_root(tmp_path: Path) -> Path:
    topic_root = tmp_path / "runtime" / "YouTubeBridge" / "freeTalkTopics"
    topic_root.mkdir(parents=True)
    (topic_root / "casual.json").write_text(
        json.dumps([
            {
                "title": "雜談題",
                "prompt": "請聊一輪雜談。",
            }
        ], ensure_ascii=False),
        encoding="utf-8",
    )
    return topic_root


async def _cancel_director_task(runtime: LiveRuntime) -> None:
    runtime.running = False
    if runtime.director_task:
        runtime.director_task.cancel()
        try:
            await runtime.director_task
        except asyncio.CancelledError:
            pass
        runtime.director_task = None


def _save_clean_event(
    storage: BridgeStorage,
    *,
    youtube_message_id: str,
    message_type: str,
    priority_class: str,
    message_text: str,
    metadata: dict | None = None,
    amount_display_string: str = "",
    amount_micros: int = 0,
) -> dict:
    event = storage.save_event({
        "bridge_session_id": "live-a",
        "connector_id": DEFAULT_CONNECTOR_ID,
        "youtube_message_id": youtube_message_id,
        "message_type": message_type,
        "author_channel_id": f"{youtube_message_id}-author",
        "author_display_name": "測試觀眾",
        "message_text": message_text,
        "published_at": "2026-05-15T10:00:00",
        "received_at": "2026-05-15T10:00:00",
        "status": "active",
        "amount_display_string": amount_display_string,
        "amount_micros": amount_micros,
        "priority_class": priority_class,
        "safety_label": "clean",
        "safety_status": "completed",
        "safe_message_text": message_text,
        "safety_summary": message_text,
        "metadata": metadata or {},
    })
    assert event is not None
    return event


@pytest.mark.asyncio
async def test_finish_main_phase_handles_sc_and_enters_free_talk(tmp_path):
    FakeClosingMemoriaClient.calls.clear()
    topic_root = _topic_root(tmp_path)
    storage = _storage(tmp_path)
    session = _create_session(storage)
    storage.update_director_state("live-a", director_enabled=True, status="running", metadata={"phase": "planned_content"})
    _save_clean_event(
        storage,
        youtube_message_id="sc-1",
        message_type="superChatEvent",
        priority_class="super_chat",
        message_text="謝謝直播",
        amount_display_string="NT$75",
        amount_micros=75000000,
        metadata={"phase": "planned_content"},
    )
    manager = YouTubeBridgeManager(storage, memoria_client_factory=FakeClosingMemoriaClient)
    runtime = LiveRuntime(session_id=session["session_id"], running=True, status="running")
    manager._runtimes["live-a"] = runtime

    try:
        result = await manager.finish_main_phase(
            "live-a",
            reason="episode_plan_completed",
            enter_free_talk=True,
            topic_root=topic_root,
        )

        assert result["phase"] == "post_plan_free_talk"
        assert storage.list_super_chats("live-a", unhandled_only=True) == []
        state = storage.get_director_state("live-a")
        metadata = state["metadata"]
        assert metadata["phase"] == "post_plan_free_talk"
        assert metadata["main_audience_closing"]["status"] == "completed"
        assert metadata["main_audience_closing"]["closing"]["super_chat_count"] == 1
        assert metadata["main_summary"]["status"] in {"queued", "running"}
        assert metadata["post_plan_free_talk"]["transition_reason"] == "episode_plan_completed"
    finally:
        await _cancel_director_task(runtime)


@pytest.mark.asyncio
async def test_free_talk_continues_on_director_loop_interval_after_finish_main(tmp_path, monkeypatch):
    FakeClosingMemoriaClient.calls.clear()
    topic_root = _topic_root(tmp_path)
    storage = _storage(tmp_path)
    session = _create_session(storage)
    manager = YouTubeBridgeManager(storage, memoria_client_factory=FakeClosingMemoriaClient)
    runtime = LiveRuntime(session_id=session["session_id"], running=True, status="running")
    manager._runtimes["live-a"] = runtime

    await manager.finish_main_phase(
        "live-a",
        reason="episode_plan_completed",
        enter_free_talk=True,
        topic_root=topic_root,
    )
    assert len(FakeClosingMemoriaClient.calls) == 1

    state = storage.get_director_state("live-a")
    metadata = dict(state["metadata"])
    metadata["post_plan_free_talk"] = {
        **metadata["post_plan_free_talk"],
        "last_tick_at": (datetime.now() - timedelta(seconds=10)).isoformat(),
    }
    storage.update_director_state(
        "live-a",
        last_director_action_at=(datetime.now() - timedelta(seconds=10)).isoformat(),
        metadata=metadata,
    )

    def fail_if_episode_plan_reruns(*args, **kwargs):
        raise AssertionError("LiveEpisodePlan should not be evaluated during free-talk ticks")

    original_tick = manager._run_post_plan_free_talk_tick

    async def tick_once(*args, **kwargs):
        result = await original_tick(*args, **kwargs)
        runtime.running = False
        return result

    monkeypatch.setattr(manager, "_episode_plan_next_decision", fail_if_episode_plan_reruns)
    monkeypatch.setattr(manager, "_run_post_plan_free_talk_tick", tick_once)

    await manager._director_loop(runtime)

    assert len(FakeClosingMemoriaClient.calls) == 2
    state = storage.get_director_state("live-a")
    assert state["metadata"]["phase"] == "post_plan_free_talk"


@pytest.mark.asyncio
async def test_finish_main_phase_starts_director_loop_for_second_natural_free_talk_tick(tmp_path, monkeypatch):
    FakeClosingMemoriaClient.calls.clear()
    topic_root = _topic_root(tmp_path)
    storage = _storage(tmp_path)
    session = _create_session(
        storage,
        post_plan_free_talk_topic_pack_ids=[],
        post_plan_free_talk_idle_turns_min=4,
        post_plan_free_talk_idle_turns_max=4,
    )
    manager = YouTubeBridgeManager(storage, memoria_client_factory=FakeClosingMemoriaClient)
    runtime = LiveRuntime(session_id=session["session_id"], running=True, status="running")
    manager._runtimes["live-a"] = runtime
    tick_statuses: list[str] = []
    original_tick = manager._run_post_plan_free_talk_tick

    async def observed_tick(*args, **kwargs):
        result = await original_tick(*args, **kwargs)
        tick_statuses.append(str(result.get("status") or ""))
        if len(tick_statuses) == 1:
            state = storage.get_director_state("live-a")
            metadata = dict(state["metadata"])
            metadata["post_plan_free_talk"] = {
                **metadata["post_plan_free_talk"],
                "last_tick_at": (datetime.now() - timedelta(seconds=10)).isoformat(),
            }
            storage.update_director_state(
                "live-a",
                last_director_action_at=(datetime.now() - timedelta(seconds=10)).isoformat(),
                metadata=metadata,
            )
        else:
            runtime.running = False
        return result

    monkeypatch.setattr(manager, "_run_post_plan_free_talk_tick", observed_tick)

    await manager.finish_main_phase(
        "live-a",
        reason="episode_plan_completed",
        enter_free_talk=True,
        topic_root=topic_root,
    )
    assert runtime.director_task is not None
    await runtime.director_task

    assert tick_statuses == ["natural_chat", "natural_chat"]
    assert len(FakeClosingMemoriaClient.calls) == 2
    runtime.director_task = None


@pytest.mark.asyncio
async def test_main_audience_sc_closing_does_not_consume_normal_comments(tmp_path):
    FakeClosingMemoriaClient.calls.clear()
    storage = _storage(tmp_path)
    session = _create_session(storage)
    sc_event = _save_clean_event(
        storage,
        youtube_message_id="sc-1",
        message_type="superChatEvent",
        priority_class="super_chat",
        message_text="主節目 SC",
        amount_display_string="NT$150",
        amount_micros=150000000,
        metadata={"phase": "planned_content"},
    )
    normal_event = _save_clean_event(
        storage,
        youtube_message_id="normal-1",
        message_type="textMessageEvent",
        priority_class="normal",
        message_text="這是一般留言，不應被主階段 SC closing 消耗。",
        metadata={"phase": "planned_content"},
    )
    manager = YouTubeBridgeManager(storage, memoria_client_factory=FakeClosingMemoriaClient)
    runtime = LiveRuntime(session_id=session["session_id"], running=True, status="running")

    result = await manager._run_main_audience_sc_closing(
        runtime,
        session,
        reason="episode_plan_completed",
    )

    assert result["status"] == "completed"
    assert result["super_chat_count"] == 1
    refreshed_sc = storage.get_events_by_ids("live-a", [sc_event["id"]])[0]
    refreshed_normal = storage.get_events_by_ids("live-a", [normal_event["id"]])[0]
    assert refreshed_sc["handled_in_closing_at"]
    assert refreshed_sc["injected_at"]
    assert not refreshed_normal["handled_in_closing_at"]
    assert not refreshed_normal["injected_at"]
    assert [interaction["event_ids"] for interaction in storage.list_interactions("live-a")] == [[sc_event["id"]]]


@pytest.mark.asyncio
async def test_main_audience_sc_closing_marks_only_injected_super_chats_when_context_caps(tmp_path):
    FakeClosingMemoriaClient.calls.clear()
    storage = _storage(tmp_path)
    session = _create_session(storage, max_context_chars=100000)
    manager = YouTubeBridgeManager(storage, memoria_client_factory=FakeClosingMemoriaClient)
    runtime = LiveRuntime(session_id=session["session_id"], running=True, status="running")
    event_ids: list[int] = []
    for index in range(101):
        event = _save_clean_event(
            storage,
            youtube_message_id=f"sc-{index}",
            message_type="superChatEvent",
            priority_class="super_chat",
            message_text=f"主節目 SC {index}",
            amount_display_string="NT$75",
            amount_micros=75000000,
            metadata={"phase": "planned_content"},
        )
        event_ids.append(int(event["id"]))

    result = await manager._run_main_audience_sc_closing(
        runtime,
        session,
        reason="episode_plan_completed",
    )

    interaction = result["result"]["interaction"]
    injected_ids = interaction["event_ids"]
    handled_ids = {
        int(event["id"])
        for event in storage.get_events_by_ids("live-a", event_ids, limit=200)
        if event["handled_in_closing_at"]
    }

    assert len(injected_ids) == 100
    assert result["marked"] == len(injected_ids)
    assert handled_ids == set(injected_ids)
    assert len(storage.list_super_chats("live-a", unhandled_only=True, limit=200)) == 1


@pytest.mark.asyncio
async def test_finish_main_phase_quiesces_auto_inject_and_soft_interrupts_active_generation(tmp_path):
    FakeClosingMemoriaClient.calls.clear()
    storage = _storage(tmp_path)
    session = _create_session(storage, auto_inject=True)
    active = storage.create_interaction({
        "session_id": "live-a",
        "source": "director",
        "priority": 50,
        "status": "running",
        "content": "主階段發言中",
    })
    _save_clean_event(
        storage,
        youtube_message_id="sc-1",
        message_type="superChatEvent",
        priority_class="super_chat",
        message_text="主節目 SC",
        amount_display_string="NT$75",
        amount_micros=75000000,
        metadata={"phase": "planned_content"},
    )
    manager = YouTubeBridgeManager(storage, memoria_client_factory=FakeClosingMemoriaClient)
    runtime = LiveRuntime(session_id=session["session_id"], running=True, status="running")
    manager._runtimes["live-a"] = runtime

    try:
        await manager.finish_main_phase(
            "live-a",
            reason="episode_plan_completed",
            enter_free_talk=True,
            topic_root=_topic_root(tmp_path),
        )

        refreshed = storage.get_session("live-a")
        active_after = storage.get_interaction(active["job_id"])
        assert refreshed["auto_inject"] is False
        assert active_after["status"] == "interrupt_requested"
        assert active_after["reason"] == "higher_priority:main_audience_closing"
        assert not active_after["completed_at"]
    finally:
        await _cancel_director_task(runtime)


@pytest.mark.asyncio
async def test_finish_main_phase_finalizes_without_free_talk_when_disabled(tmp_path):
    storage = _storage(tmp_path)
    _create_session(
        storage,
        character_ids=[],
        target_memoria_session_id="",
        post_plan_free_talk_enabled=False,
        auto_sc_thanks_on_finalize=False,
    )
    manager = YouTubeBridgeManager(storage, memoria_client_factory=FakeClosingMemoriaClient)
    manager._runtimes["live-a"] = LiveRuntime(session_id="live-a", running=True, status="running")

    result = await manager.finish_main_phase(
        "live-a",
        reason="episode_plan_completed",
        enter_free_talk=True,
        topic_root=_topic_root(tmp_path),
    )

    assert result["phase"] == "finalizing_main_only"
    assert storage.get_session("live-a")["status"] == "ended"
    metadata = storage.get_director_state("live-a")["metadata"]
    assert "post_plan_free_talk" not in metadata
    assert metadata["phase_finalize"]["reason"] == "episode_plan_completed"


@pytest.mark.asyncio
async def test_finish_main_phase_refuses_stopped_session_before_mutation(tmp_path):
    storage = _storage(tmp_path)
    _create_session(storage, status="stopped")
    storage.update_director_state(
        "live-a",
        director_enabled=False,
        status="stopped",
        metadata={"phase": "planned_content"},
    )
    manager = YouTubeBridgeManager(storage, memoria_client_factory=FakeClosingMemoriaClient)

    with pytest.raises(ValueError, match="尚未開始"):
        await manager.finish_main_phase(
            "live-a",
            reason="operator",
            enter_free_talk=True,
            topic_root=_topic_root(tmp_path),
        )

    assert storage.get_session("live-a")["status"] == "stopped"
    state = storage.get_director_state("live-a")
    assert state["status"] == "stopped"
    assert state["metadata"] == {"phase": "planned_content"}
    assert storage.list_interactions("live-a") == []


@pytest.mark.asyncio
async def test_finish_main_phase_refuses_missing_runtime_before_mutation(tmp_path):
    storage = _storage(tmp_path)
    _create_session(storage, status="running")
    storage.update_director_state(
        "live-a",
        director_enabled=True,
        status="running",
        metadata={"phase": "planned_content"},
    )
    sc_event = _save_clean_event(
        storage,
        youtube_message_id="sc-1",
        message_type="superChatEvent",
        priority_class="super_chat",
        message_text="不應被處理",
        amount_display_string="NT$75",
        amount_micros=75000000,
        metadata={"phase": "planned_content"},
    )
    manager = YouTubeBridgeManager(storage, memoria_client_factory=FakeClosingMemoriaClient)

    with pytest.raises(ValueError, match="尚未開始"):
        await manager.finish_main_phase(
            "live-a",
            reason="operator",
            enter_free_talk=True,
            topic_root=_topic_root(tmp_path),
        )

    assert storage.get_session("live-a")["status"] == "running"
    state = storage.get_director_state("live-a")
    assert state["status"] == "running"
    assert state["metadata"] == {"phase": "planned_content"}
    assert storage.get_events_by_ids("live-a", [sc_event["id"]])[0]["handled_in_closing_at"] == ""
    assert storage.list_interactions("live-a") == []


@pytest.mark.asyncio
async def test_episode_plan_completed_enters_phase_pipeline_instead_of_direct_finalize(tmp_path, monkeypatch):
    storage = _storage(tmp_path)
    session = _create_session(storage, post_plan_free_talk_enabled=False)
    manager = YouTubeBridgeManager(storage)
    called = []

    async def fake_finish_main_phase(session_id, *, reason, enter_free_talk, topic_root):
        called.append({
            "session_id": session_id,
            "reason": reason,
            "enter_free_talk": enter_free_talk,
            "topic_root": Path(topic_root),
        })
        return {"phase": "finalizing_main_only"}

    monkeypatch.setattr(manager, "finish_main_phase", fake_finish_main_phase, raising=False)
    runtime = manager._runtimes.setdefault("live-a", LiveRuntime(session_id="live-a", running=True, status="running"))

    await manager._finalize_for_episode_plan_completed(
        runtime,
        session,
        {"plan_status": "completed"},
    )

    assert called == [{
        "session_id": "live-a",
        "reason": "episode_plan_completed",
        "enter_free_talk": True,
        "topic_root": Path(__file__).resolve().parents[2] / "runtime" / "YouTubeBridge" / "freeTalkTopics",
    }]
