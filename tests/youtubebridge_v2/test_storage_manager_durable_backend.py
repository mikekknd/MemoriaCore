from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from core.storage_manager import StorageManager
from YouTubeBridgeV2.adapters.youtube import YouTubePollingCursor
from YouTubeBridgeV2.runtime.application_service import (
    RuntimeServiceEvent,
    RuntimeServiceResult,
)
from YouTubeBridgeV2.runtime.phase import (
    AftertalkPolicy,
    DurationPolicy,
    LiveSessionPhase,
)
from YouTubeBridgeV2.storage.runtime_store import RuntimeStoragePort


STARTED_AT = datetime(2026, 5, 12, 8, 0, tzinfo=timezone.utc)
NOW = datetime(2026, 5, 12, 8, 5, tzinfo=timezone.utc)


def _storage(tmp_path):
    return StorageManager(
        prefs_file=str(tmp_path / "prefs.json"),
        history_file=str(tmp_path / "history.json"),
        persona_snapshot_db_path=str(tmp_path / "persona.db"),
        youtube_bridge_v2_db_path=str(tmp_path / "youtubebridge_v2.db"),
    )


def _session_record(**overrides):
    record = {
        "session_id": "session-1",
        "current_phase": "planned_show",
        "session_started_at": STARTED_AT,
        "plan_completed": False,
        "aftertalk_policy": "auto",
        "duration_policy": {
            "planned_duration_seconds": 3600,
            "auto_finalize_on_duration": True,
            "aftertalk_requires_remaining_time": True,
        },
        "manual_close_requested": False,
        "closing_completed": False,
        "public_summary": {
            "title": "V2 show",
            "hidden_prompt": "must not leak",
        },
    }
    record.update(overrides)
    return record


def _assert_no_private_payload(value):
    text = repr(value).lower()
    for forbidden in (
        "hidden_prompt",
        "raw_payload",
        "topic_pack",
        "raw_topic_pack",
        "raw_memoriacore_payload",
        "factcard",
        "fact_card",
        "topic_pack_fact_cards",
        "access_token",
        "token",
        "must not leak",
    ):
        assert forbidden not in text


def test_v2_storage_manager_initializes_schema(tmp_path):
    storage = _storage(tmp_path)

    storage.create_v2_session(_session_record())

    conn = sqlite3.connect(storage.youtube_bridge_v2_db_path)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'yb2_%'"
    )
    tables = {row[0] for row in cursor.fetchall()}
    conn.close()

    assert tables >= {
        "yb2_sessions",
        "yb2_phase_transitions",
        "yb2_live_events",
        "yb2_interactions",
        "yb2_finalizations",
        "yb2_command_results",
    }


def test_create_and_read_v2_session_snapshot_from_storage_manager(tmp_path):
    storage = _storage(tmp_path)

    created = storage.create_v2_session(_session_record())
    loaded = storage.get_v2_session("session-1")

    assert created["session_id"] == "session-1"
    assert loaded["current_phase"] == "planned_show"
    assert loaded["session_started_at"] == STARTED_AT
    assert loaded["plan_completed"] is False
    assert loaded["aftertalk_policy"] == "auto"
    assert loaded["duration_policy"] == {
        "planned_duration_seconds": 3600,
        "auto_finalize_on_duration": True,
        "aftertalk_requires_remaining_time": True,
    }
    _assert_no_private_payload(loaded)


def test_duplicate_create_v2_session_does_not_overwrite_existing_snapshot(tmp_path):
    storage = _storage(tmp_path)
    storage.create_v2_session(_session_record())
    storage.update_v2_session(
        "session-1",
        {
            "current_phase": "closing",
            "plan_completed": True,
            "public_summary": {"title": "kept"},
        },
    )

    duplicate = storage.create_v2_session(
        _session_record(
            current_phase="planned_show",
            plan_completed=False,
            public_summary={"title": "overwritten"},
        )
    )
    loaded = storage.get_v2_session("session-1")

    assert duplicate["current_phase"] == "closing"
    assert loaded["current_phase"] == "closing"
    assert loaded["plan_completed"] is True
    assert loaded["public_summary"] == {"title": "kept"}


def test_list_v2_sessions_for_recovery_returns_active_sessions_only(tmp_path):
    storage = _storage(tmp_path)
    storage.create_v2_session(_session_record(session_id="planned-session"))
    storage.create_v2_session(
        _session_record(
            session_id="closing-session",
            current_phase="closing",
            plan_completed=True,
        )
    )
    storage.create_v2_session(
        _session_record(
            session_id="ended-session",
            current_phase="ended",
            plan_completed=True,
            closing_completed=True,
        )
    )

    sessions = storage.list_v2_sessions_for_recovery(limit=10)

    assert [session["session_id"] for session in sessions] == [
        "planned-session",
        "closing-session",
    ]
    assert all(session["current_phase"] != "ended" for session in sessions)
    _assert_no_private_payload(sessions)


def test_update_v2_session_preserves_snapshot_contract(tmp_path):
    storage = _storage(tmp_path)
    storage.create_v2_session(_session_record())

    updated = storage.update_v2_session(
        "session-1",
        {
            "current_phase": LiveSessionPhase.AFTERTALK,
            "plan_completed": True,
            "manual_close_requested": True,
            "public_summary": {
                "safe": "visible",
                "topic_pack": "must not leak",
                "factcard": "must not leak",
                "fact_card": "must not leak",
                "raw_topic_pack": "must not leak",
            },
        },
    )

    assert updated["current_phase"] == "aftertalk"
    assert updated["plan_completed"] is True
    assert updated["manual_close_requested"] is True
    assert updated["duration_policy"]["planned_duration_seconds"] == 3600
    _assert_no_private_payload(updated)


def test_append_v2_phase_transition_is_idempotent_by_transition_id(tmp_path):
    storage = _storage(tmp_path)
    storage.create_v2_session(_session_record())

    first = storage.append_v2_phase_transition(
        "session-1",
        {
            "transition_id": "transition-1",
            "previous_phase": LiveSessionPhase.PLANNED_SHOW,
            "next_phase": LiveSessionPhase.AFTERTALK,
            "reason": "aftertalk_enabled",
            "metadata": {
                "safe": "visible",
                "hidden_prompt": "must not leak",
            },
            "created_at": NOW,
        },
    )
    duplicate = storage.append_v2_phase_transition(
        "session-1",
        {
            "transition_id": "transition-1",
            "previous_phase": "planned_show",
            "next_phase": "closing",
            "reason": "manual_close",
            "metadata": {"safe": "changed"},
            "created_at": NOW,
        },
    )

    assert duplicate == first
    assert storage.get_v2_phase_transition("transition-1") == first
    _assert_no_private_payload(first)


def test_append_v2_phase_transition_treats_duplicate_insert_as_idempotent(
    tmp_path,
    monkeypatch,
):
    storage = _storage(tmp_path)
    storage.create_v2_session(_session_record())
    first = storage.append_v2_phase_transition(
        "session-1",
        {
            "transition_id": "transition-race",
            "previous_phase": LiveSessionPhase.PLANNED_SHOW,
            "next_phase": LiveSessionPhase.AFTERTALK,
            "reason": "aftertalk_enabled",
            "metadata": {"safe": "visible"},
            "created_at": NOW,
        },
    )
    original_get = storage.get_v2_phase_transition
    calls = 0

    def stale_once(transition_id: str):
        nonlocal calls
        calls += 1
        if calls == 1:
            return None
        return original_get(transition_id)

    monkeypatch.setattr(storage, "get_v2_phase_transition", stale_once)

    duplicate = storage.append_v2_phase_transition(
        "session-1",
        {
            "transition_id": "transition-race",
            "previous_phase": LiveSessionPhase.PLANNED_SHOW,
            "next_phase": LiveSessionPhase.CLOSING,
            "reason": "manual_close",
            "metadata": {"safe": "changed"},
            "created_at": NOW,
        },
    )

    assert duplicate == first


def test_append_and_list_v2_live_events_are_ordered_and_limited(tmp_path):
    storage = _storage(tmp_path)
    storage.create_v2_session(_session_record())

    for idx in range(3):
        storage.append_v2_live_event(
            "session-1",
            {
                "event_id": f"event-{idx}",
                "event_type": "runtime_action_dispatched",
                "public_metadata": {"idx": idx, "raw_payload": "must not leak"},
                "created_at": datetime(2026, 5, 12, 8, idx, tzinfo=timezone.utc),
            },
        )

    events = storage.list_v2_live_events("session-1", limit=2)

    assert [event["event_id"] for event in events] == ["event-1", "event-2"]
    assert [event["public_metadata"]["idx"] for event in events] == [1, 2]
    _assert_no_private_payload(events)


def test_append_v2_live_event_rejects_unknown_session_id(tmp_path):
    storage = _storage(tmp_path)

    with pytest.raises(sqlite3.IntegrityError):
        storage.append_v2_live_event(
            "missing-session",
            {
                "event_id": "event-orphan",
                "event_type": "runtime_action_dispatched",
                "public_metadata": {"safe": "visible"},
                "created_at": NOW,
            },
        )


def test_append_v2_interaction_redacts_private_public_summary(tmp_path):
    storage = _storage(tmp_path)
    storage.create_v2_session(_session_record())

    interaction = storage.append_v2_interaction(
        "session-1",
        {
            "interaction_id": "interaction-1",
            "phase": "aftertalk",
            "speaker_id": "cast",
            "public_content_summary": {
                "text": "visible",
                "raw_memoriacore_payload": {"token": "must not leak"},
            },
            "correlation_id": "runtime-cmd",
            "created_at": NOW,
        },
    )

    assert interaction["interaction_id"] == "interaction-1"
    assert interaction["public_content_summary"] == {"text": "visible"}
    _assert_no_private_payload(interaction)


def test_append_v2_finalization_marks_session_closing_completed(tmp_path):
    storage = _storage(tmp_path)
    storage.create_v2_session(_session_record(current_phase="closing"))

    finalization = storage.append_v2_finalization(
        "session-1",
        {
            "finalization_id": "finalization-1",
            "closing_completion_status": "complete",
            "completed_at": NOW,
            "display_summary": {
                "message": "done",
                "hidden_prompt": "must not leak",
            },
            "error_summary": {},
        },
    )
    loaded = storage.get_v2_session("session-1")

    assert finalization["closing_completion_status"] == "complete"
    assert loaded["closing_completed"] is True
    _assert_no_private_payload(finalization)


def test_append_v2_finalization_rejects_unknown_session_id(tmp_path):
    storage = _storage(tmp_path)

    with pytest.raises(sqlite3.IntegrityError):
        storage.append_v2_finalization(
            "missing-session",
            {
                "finalization_id": "finalization-orphan",
                "closing_completion_status": "complete",
                "completed_at": NOW,
                "display_summary": {"message": "done"},
                "error_summary": {},
            },
        )


def test_v2_command_result_round_trips_through_storage_manager(tmp_path):
    storage = _storage(tmp_path)
    result = RuntimeServiceResult(
        status="ok",
        session_id="session-1",
        phase=LiveSessionPhase.AFTERTALK,
        events=[
            RuntimeServiceEvent(
                event_type="runtime_action_dispatched",
                session_id="session-1",
                phase=LiveSessionPhase.AFTERTALK,
                payload={"safe": "visible", "raw_payload": "must not leak"},
                correlation_id="runtime-cmd-1",
            )
        ],
        errors=[],
        correlation_id="runtime-cmd-1",
    )

    storage.save_v2_command_result("cmd-1", result)
    loaded = storage.get_v2_command_result("cmd-1")

    assert loaded["status"] == "ok"
    assert loaded["phase"] == "aftertalk"
    assert loaded["events"][0]["phase"] == "aftertalk"
    _assert_no_private_payload(loaded)


def test_v2_storage_schema_init_is_idempotent_across_manager_instances(tmp_path):
    first = _storage(tmp_path)
    first.create_v2_session(_session_record())
    second = _storage(tmp_path)

    loaded = second.get_v2_session("session-1")
    second.update_v2_session("session-1", {"current_phase": "aftertalk"})

    assert loaded["session_id"] == "session-1"
    assert second.get_v2_session("session-1")["current_phase"] == "aftertalk"


def test_youtube_polling_cursor_survives_storage_manager_restart(tmp_path):
    storage = _storage(tmp_path)
    storage.create_v2_session(_session_record())
    RuntimeStoragePort(storage).save_youtube_polling_cursor(
        "session-1",
        YouTubePollingCursor(
            live_chat_id="live-chat-1",
            next_page_token="page-2",
            polling_interval_millis=2500,
            seen_event_ids=("yt-evt-1", "yt-evt-2"),
        ),
        NOW,
    )

    restarted = _storage(tmp_path)
    loaded = RuntimeStoragePort(restarted).load_youtube_polling_cursor("session-1")

    assert loaded == YouTubePollingCursor(
        live_chat_id="live-chat-1",
        next_page_token="page-2",
        polling_interval_millis=2500,
        seen_event_ids=("yt-evt-1", "yt-evt-2"),
    )
    text = repr(restarted.get_v2_session("session-1")).lower()
    assert "access_token" not in text
    assert "authorization" not in text
    assert "secret" not in text
    assert "must not leak" not in text
