import shutil

from bridge_engine_test_support import (
    BridgeStorage,
    LiveEndedClient,
    YouTubeBridgeManager,
    _tmp_dir,
)
from test_live_episode_plan_contract import sample_plan


def _manager_with_bound_plan():
    tmp_dir = _tmp_dir()
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
    manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())
    return tmp_dir, storage, manager


def test_plan_state_initializes_from_bound_plan():
    tmp_dir, storage, manager = _manager_with_bound_plan()
    try:
        session = storage.get_session("live-a")
        state = storage.get_director_state("live-a")

        plan, planned_state = manager._episode_plan_and_state(session, state)
        turn = manager._episode_current_turn_contract(plan, planned_state)

        assert planned_state["current_segment_index"] == 0
        assert planned_state["current_turn_index"] == 0
        assert turn["turn_id"] == "seg_01_turn_01"
        assert turn["speaker_policy"]["selection_mode"] == "router_select"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_planned_turn_advances_by_mechanical_conditions():
    tmp_dir, storage, manager = _manager_with_bound_plan()
    try:
        session = storage.get_session("live-a")
        state = storage.get_director_state("live-a")
        plan, planned_state = manager._episode_plan_and_state(session, state)

        after_hook = manager._planned_state_after_episode_turn(
            plan,
            planned_state,
            {"turn_id": "seg_01_turn_01", "turn_type": "hook"},
        )
        after_analysis = manager._planned_state_after_episode_turn(
            plan,
            after_hook,
            {"turn_id": "seg_01_turn_02", "turn_type": "analysis"},
        )

        assert after_hook["current_segment_index"] == 0
        assert after_hook["current_turn_index"] == 1
        assert after_hook["completed_turn_types"] == ["hook"]
        assert after_analysis["plan_status"] == "completed"
        assert after_analysis["current_segment_index"] == 0
        assert after_analysis["current_turn_index"] == 1
        assert after_analysis["segment_memory"]["covered_claims"]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_interrupt_state_does_not_advance_planned_turn():
    tmp_dir, storage, manager = _manager_with_bound_plan()
    try:
        session = storage.get_session("live-a")
        state = storage.get_director_state("live-a")
        plan, planned_state = manager._episode_plan_and_state(session, state)

        interrupt = manager._interrupt_state_for_audience_event(
            plan,
            planned_state,
            {
                "id": 7,
                "safe_message_text": "這邊是不是說錯了？",
                "priority_class": "normal",
            },
            "question",
            "bounded_interrupt",
        )

        assert interrupt["status"] == "handling_audience"
        assert interrupt["return_segment_index"] == 0
        assert interrupt["return_turn_index"] == 0
        assert planned_state["current_turn_index"] == 0
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
