from datetime import datetime, timezone
from pathlib import Path
import ast

import pytest

from YouTubeBridgeV2.runtime.phase import (
    AftertalkPolicy,
    DurationPolicy,
    LiveSessionPhase,
    LiveSessionSnapshot,
    PhaseTransition,
    PhaseTransitionReason,
)
from YouTubeBridgeV2.storage.runtime_store import RuntimeStoragePort
from YouTubeBridgeV2.storage.repositories import (
    EventRepository,
    FinalizationRepository,
    InteractionRepository,
    PhaseTransitionRepository,
    SessionRepository,
    StorageBackendNotConfigured,
    StorageManagerBackedRepository,
    StorageContractError,
    StorageRecordNotFound,
    append_interaction,
    append_live_event,
    append_phase_transition,
    read_live_session_snapshot,
)


STARTED_AT = datetime(2026, 5, 12, 8, 0, tzinfo=timezone.utc)
NOW = datetime(2026, 5, 12, 8, 15, tzinfo=timezone.utc)


class FakeStorageManager:
    def __init__(self):
        self.sessions = {}
        self.transitions = {}
        self.events = []
        self.interactions = []
        self.finalizations = []

    def create_v2_session(self, record):
        self.sessions[record["session_id"]] = dict(record)
        return self.sessions[record["session_id"]]

    def get_v2_session(self, session_id):
        return self.sessions.get(session_id)

    def get_v2_phase_transition(self, transition_id):
        return self.transitions.get(transition_id)

    def append_v2_phase_transition(self, session_id, record):
        self.transitions[record["transition_id"]] = dict(record)
        return self.transitions[record["transition_id"]]

    def append_v2_live_event(self, session_id, record):
        self.events.append(dict(record))
        return self.events[-1]

    def append_v2_interaction(self, session_id, record):
        self.interactions.append(dict(record))
        return self.interactions[-1]

    def append_v2_finalization(self, session_id, record):
        self.finalizations.append(dict(record))
        self.sessions.setdefault(session_id, {})["ended_metadata"] = {
            "closing_completion_status": record["closing_completion_status"],
            "ended_at": record["completed_at"],
        }
        return self.finalizations[-1]


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
    }
    record.update(overrides)
    return record


def _transition(**overrides):
    base = {
        "transition_id": "transition-1",
        "previous_phase": LiveSessionPhase.PLANNED_SHOW,
        "next_phase": LiveSessionPhase.AFTERTALK,
        "reason": PhaseTransitionReason.AFTERTALK_ENABLED,
        "metadata": {
            "safe": "visible",
            "hidden_prompt": "must not leak",
            "raw_memoriacore_payload": {"token": "secret"},
        },
        "created_at": NOW,
    }
    base.update(overrides)
    return base


def _phase_transition():
    return PhaseTransition(
        current_phase=LiveSessionPhase.PLANNED_SHOW,
        next_phase=LiveSessionPhase.AFTERTALK,
        changed=True,
        reason=PhaseTransitionReason.AFTERTALK_ENABLED,
        metadata={"safe": "visible", "raw_payload": {"token": "secret"}},
        next_action="start_aftertalk",
    )


def _assert_no_private_payload(value):
    text = repr(value).lower()
    for forbidden in (
        "hidden_prompt",
        "raw_prompt",
        "raw_payload",
        "raw_memoriacore_payload",
        "raw_topic_pack",
        "youtube_raw",
        "access_token",
        "token",
        "secret",
    ):
        assert forbidden not in text


def test_create_session_and_read_snapshot():
    storage = FakeStorageManager()
    repo = SessionRepository(storage)

    created = repo.create_session(_session_record())
    snapshot = repo.read_live_session_snapshot("session-1")

    assert isinstance(created, LiveSessionSnapshot)
    assert isinstance(snapshot, LiveSessionSnapshot)
    assert snapshot.current_phase == LiveSessionPhase.PLANNED_SHOW
    assert snapshot.session_started_at == STARTED_AT
    assert snapshot.plan_completed is False
    assert snapshot.aftertalk_policy == AftertalkPolicy.AUTO
    assert snapshot.duration_policy == DurationPolicy(
        planned_duration_seconds=3600,
        auto_finalize_on_duration=True,
        aftertalk_requires_remaining_time=True,
    )


def test_read_missing_session_returns_not_found():
    repo = SessionRepository(FakeStorageManager())

    with pytest.raises(StorageRecordNotFound):
        repo.read_live_session_snapshot("missing-session")


def test_append_phase_transition_persists_record():
    storage = FakeStorageManager()
    repo = PhaseTransitionRepository(storage)

    record = repo.append_phase_transition("session-1", _transition())

    assert record["transition_id"] == "transition-1"
    assert record["session_id"] == "session-1"
    assert record["previous_phase"] == "planned_show"
    assert record["next_phase"] == "aftertalk"
    assert record["reason"] == "aftertalk_enabled"
    assert record["metadata"] == {"safe": "visible"}
    _assert_no_private_payload(record)


def test_duplicate_transition_id_is_idempotent():
    storage = FakeStorageManager()
    repo = PhaseTransitionRepository(storage)

    first = repo.append_phase_transition("session-1", _transition())
    duplicate = repo.append_phase_transition(
        "session-1",
        _transition(metadata={"safe": "changed"}),
    )

    assert duplicate == first
    assert len(storage.transitions) == 1


def test_append_live_event_persists_normalized_event():
    storage = FakeStorageManager()
    repo = EventRepository(storage)

    event = repo.append_live_event(
        "session-1",
        {
            "event_id": "evt-1",
            "event_type": "youtube_chat",
            "public_metadata": {
                "author": "Alice",
                "raw_payload": {"youtube_raw": "must not leak"},
            },
            "raw_youtube_event": {"secret": "must not leak"},
        },
    )

    assert event["event_id"] == "evt-1"
    assert event["event_type"] == "youtube_chat"
    assert event["public_metadata"] == {"author": "Alice"}
    _assert_no_private_payload(event)


def test_runtime_storage_port_persists_normalized_youtube_event_shape():
    storage = FakeStorageManager()
    port = RuntimeStoragePort(storage)

    port.persist_youtube_event(
        "session-1",
        {
            "event_id": "yt-evt-1",
            "event_type": "youtube_text_message",
            "public_payload": {
                "event_id": "yt-evt-1",
                "message_text": "Hello runtime",
                "raw_payload": {"youtube_raw": "must not leak"},
            },
            "display_event": {
                "event_id": "yt-evt-1",
                "event_type": "audience_message",
                "message_text": "Hello runtime",
            },
            "should_dispatch": True,
            "raw_youtube_payload": {"access_token": "secret-value"},
        },
        NOW,
    )

    stored = storage.events[0]
    assert stored["event_id"] == "yt-evt-1"
    assert stored["event_type"] == "youtube_text_message"
    assert stored["public_metadata"] == {
        "public_payload": {
            "event_id": "yt-evt-1",
            "message_text": "Hello runtime",
        },
        "display_event": {
            "event_id": "yt-evt-1",
            "event_type": "audience_message",
            "message_text": "Hello runtime",
        },
        "should_dispatch": True,
    }
    assert stored["created_at"] == NOW
    _assert_no_private_payload(stored)


def test_append_interaction_persists_response_summary():
    storage = FakeStorageManager()
    repo = InteractionRepository(storage)

    interaction = repo.append_interaction(
        "session-1",
        {
            "interaction_id": "int-1",
            "phase": "aftertalk",
            "speaker_id": "host",
            "public_content_summary": {
                "message_count": 2,
                "hidden_prompt": "must not leak",
            },
            "correlation_id": "corr-1",
            "raw_adapter_payload": {"token": "must not leak"},
        },
    )

    assert interaction["interaction_id"] == "int-1"
    assert interaction["phase"] == "aftertalk"
    assert interaction["speaker_id"] == "host"
    assert interaction["public_content_summary"] == {"message_count": 2}
    assert interaction["correlation_id"] == "corr-1"
    _assert_no_private_payload(interaction)


def test_finalization_record_moves_session_to_ended_metadata():
    storage = FakeStorageManager()
    SessionRepository(storage).create_session(_session_record(current_phase="closing"))
    repo = FinalizationRepository(storage)

    record = repo.append_finalization_record(
        "session-1",
        {
            "finalization_id": "fin-1",
            "closing_completion_status": "complete",
            "completed_at": NOW,
            "display_summary": {"safe": "visible"},
            "error_summary": {},
        },
    )

    assert record["finalization_id"] == "fin-1"
    assert record["closing_completion_status"] == "complete"
    assert storage.sessions["session-1"]["ended_metadata"] == {
        "closing_completion_status": "complete",
        "ended_at": NOW,
    }


def test_public_metadata_redacts_raw_prompt_and_adapter_payload():
    storage = FakeStorageManager()
    facade = StorageManagerBackedRepository(storage)

    facade.events.append_live_event(
        "session-1",
        {
            "event_id": "evt-private",
            "event_type": "system",
            "public_metadata": {
                "safe": "visible",
                "hidden_prompt": "must not leak",
                "nested": {"raw_prompt": "must not leak"},
            },
            "raw_adapter_payload": {"access_token": "secret"},
        },
    )
    facade.interactions.append_interaction(
        "session-1",
        {
            "interaction_id": "int-private",
            "phase": "planned_show",
            "public_content_summary": {
                "safe": "visible",
                "raw_memoriacore_payload": {"token": "secret"},
            },
            "raw_topic_pack": "must not leak",
        },
    )

    _assert_no_private_payload(storage.events)
    _assert_no_private_payload(storage.interactions)


def test_v2_storage_uses_storage_manager_boundary(monkeypatch):
    storage = FakeStorageManager()
    facade = StorageManagerBackedRepository(storage)
    monkeypatch.setattr(
        "YouTubeBridgeV2.storage.repositories._default_repository",
        lambda: facade,
    )

    facade.sessions.create_session(_session_record())
    snapshot = read_live_session_snapshot("session-1")
    transition_ref = append_phase_transition("session-1", _transition())
    event = append_live_event("session-1", {"event_id": "evt-1", "event_type": "system"})
    interaction = append_interaction(
        "session-1",
        {"interaction_id": "int-1", "phase": "planned_show"},
    )

    assert snapshot.current_phase == LiveSessionPhase.PLANNED_SHOW
    assert transition_ref["transition_id"] == "transition-1"
    assert event["event_id"] == "evt-1"
    assert interaction["interaction_id"] == "int-1"
    assert storage.transitions
    assert storage.events
    assert storage.interactions


def test_default_repository_without_configured_backend_fails_clearly():
    with pytest.raises(StorageBackendNotConfigured, match="backend is not configured"):
        read_live_session_snapshot("session-1")


def test_facade_does_not_claim_runtime_application_service_storage_contract():
    facade = StorageManagerBackedRepository(FakeStorageManager())

    for service_method in (
        "create_session",
        "read_snapshot",
        "persist_transition",
        "bind_plan",
        "start_session",
        "request_manual_close",
        "finalize_closing",
    ):
        assert not hasattr(facade, service_method)


def test_phase_transition_requires_explicit_transition_id():
    repo = PhaseTransitionRepository(FakeStorageManager())

    with pytest.raises(StorageContractError, match="transition missing transition_id"):
        repo.append_phase_transition("session-1", _phase_transition())


def test_v2_modules_do_not_import_sqlite_or_aiosqlite():
    v2_root = Path(__file__).resolve().parents[2] / "YouTubeBridgeV2"
    offenders = []

    for path in v2_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                names = {node.module or ""}
            else:
                continue
            if names & {"sqlite3", "aiosqlite"}:
                offenders.append(str(path.relative_to(v2_root)))

    assert offenders == []
