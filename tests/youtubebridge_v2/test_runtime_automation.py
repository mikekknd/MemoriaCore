from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tests.youtubebridge_v2.fakes import (
    FakeAftertalkRunner,
    FakeClosingRunner,
    FakePlannedShowRunner,
    InMemoryV2StorageManager,
)
from YouTubeBridgeV2.composition import create_v2_composition
from YouTubeBridgeV2.runtime.application_service import RuntimeCommand
from YouTubeBridgeV2.runtime.application_service import RuntimeCommandType
from YouTubeBridgeV2.runtime.automation import (
    AutomationTickPolicy,
    SchedulerSessionRef,
    build_scheduler_cycle_intents,
    build_scheduler_tick_intent,
    dispatch_scheduler_cycle,
    dispatch_scheduler_tick,
)
from YouTubeBridgeV2.runtime.phase import LiveSessionPhase


ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 5, 12, 8, 0, tzinfo=timezone.utc)


class FakeRuntimeService:
    def __init__(self):
        self.calls = []

    def tick_session(self, command, now):
        self.calls.append((command, now))
        return {
            "status": "ok",
            "session_id": command.session_id,
            "phase": "planned_show",
            "events": [],
            "errors": [],
            "correlation_id": f"runtime-{command.command_id}",
        }


def test_scheduler_tick_intent_builds_deterministic_tick_command():
    intent = build_scheduler_tick_intent(
        "session-1",
        NOW,
        AutomationTickPolicy(interval_seconds=10),
    )

    assert intent.should_dispatch is True
    assert intent.session_id == "session-1"
    assert intent.command_type is RuntimeCommandType.TICK
    assert intent.command_id == "scheduler:session-1:20260512T080000Z"
    assert intent.next_run_delay_seconds == 10
    assert intent.payload == {
        "source": "runtime_scheduler",
        "scheduler": {
            "interval_seconds": 10,
            "issued_at": "2026-05-12T08:00:00+00:00",
        },
    }


def test_scheduler_tick_intent_skips_disabled_paused_and_ended_sessions():
    disabled = build_scheduler_tick_intent(
        "session-1",
        NOW,
        AutomationTickPolicy(enabled=False),
    )
    paused = build_scheduler_tick_intent(
        "session-1",
        NOW,
        AutomationTickPolicy(paused=True),
    )
    ended = build_scheduler_tick_intent(
        "session-1",
        NOW,
        AutomationTickPolicy(),
        current_phase=LiveSessionPhase.ENDED,
    )

    assert disabled.should_dispatch is False
    assert disabled.skip_reason == "automation_disabled"
    assert paused.should_dispatch is False
    assert paused.skip_reason == "automation_paused"
    assert ended.should_dispatch is False
    assert ended.skip_reason == "session_ended"


def test_dispatch_scheduler_tick_calls_runtime_once_with_internal_context():
    service = FakeRuntimeService()
    intent = build_scheduler_tick_intent("session-1", NOW, AutomationTickPolicy())

    result = dispatch_scheduler_tick(service, intent, NOW)

    assert result["status"] == "ok"
    assert len(service.calls) == 1
    command, called_now = service.calls[0]
    assert command.command_id == intent.command_id
    assert command.command_type is RuntimeCommandType.TICK
    assert command.payload == intent.payload
    assert command.permission_context == {
        "auth_method": "scheduler",
        "permission_group": "internal",
        "source": "runtime_scheduler",
    }
    assert called_now == NOW


def test_dispatch_scheduler_tick_skips_without_runtime_side_effect():
    service = FakeRuntimeService()
    intent = build_scheduler_tick_intent(
        "session-1",
        NOW,
        AutomationTickPolicy(enabled=False),
    )

    result = dispatch_scheduler_tick(service, intent, NOW)

    assert result == {
        "status": "skipped",
        "session_id": "session-1",
        "phase": None,
        "events": [],
        "errors": [],
        "correlation_id": "scheduler:session-1:20260512T080000Z",
        "skip_reason": "automation_disabled",
    }
    assert service.calls == []


def test_scheduler_cycle_dispatches_active_phase_refs_and_skips_safe_refs():
    service = FakeRuntimeService()
    sessions = [
        SchedulerSessionRef("planned", current_phase=LiveSessionPhase.PLANNED_SHOW),
        SchedulerSessionRef("aftertalk", current_phase=LiveSessionPhase.AFTERTALK),
        SchedulerSessionRef("closing", current_phase=LiveSessionPhase.CLOSING),
        SchedulerSessionRef("ended", current_phase=LiveSessionPhase.ENDED),
        SchedulerSessionRef(
            "paused",
            current_phase=LiveSessionPhase.PLANNED_SHOW,
            automation_paused=True,
        ),
    ]

    result = dispatch_scheduler_cycle(service, sessions, NOW, AutomationTickPolicy())

    assert [command.session_id for command, _now in service.calls] == [
        "planned",
        "aftertalk",
        "closing",
    ]
    assert [command.command_id for command, _now in service.calls] == [
        "scheduler:planned:20260512T080000Z",
        "scheduler:aftertalk:20260512T080000Z",
        "scheduler:closing:20260512T080000Z",
    ]
    assert [intent.session_id for intent in result.skipped] == ["ended", "paused"]
    assert [intent.skip_reason for intent in result.skipped] == [
        "session_ended",
        "automation_paused",
    ]
    assert len(result.dispatched) == 3
    assert result.next_run_delay_seconds == 5


def test_scheduler_cycle_builds_intents_from_mapping_refs():
    intents = build_scheduler_cycle_intents(
        [
            {
                "session_id": "session-map",
                "current_phase": "planned_show",
                "automation_enabled": False,
            }
        ],
        NOW,
        AutomationTickPolicy(interval_seconds=15),
    )

    assert len(intents) == 1
    assert intents[0].session_id == "session-map"
    assert intents[0].should_dispatch is False
    assert intents[0].skip_reason == "automation_disabled"
    assert intents[0].next_run_delay_seconds == 15


def test_scheduler_cycle_auto_advances_planned_aftertalk_closing_to_ended():
    storage = InMemoryV2StorageManager()
    planned_show = FakePlannedShowRunner(storage)
    aftertalk = FakeAftertalkRunner(storage)
    closing = FakeClosingRunner(storage)
    composition = create_v2_composition(
        storage_manager=storage,
        planned_show_runner=planned_show,
        aftertalk_runner=aftertalk,
        closing_runner=closing,
    )
    session_id = "session-auto-advance"
    composition.runtime_service.create_session(
        _runtime_command(
            "auto-create",
            session_id,
            RuntimeCommandType.CREATE_SESSION,
            NOW,
            {
                "aftertalk_policy": "auto",
                "metadata": {
                    "duration_policy": {
                        "planned_duration_seconds": 30,
                        "auto_finalize_on_duration": True,
                        "aftertalk_requires_remaining_time": True,
                    }
                },
            },
        ),
        NOW,
    )
    composition.runtime_service.bind_plan(
        _runtime_command(
            "auto-bind",
            session_id,
            RuntimeCommandType.BIND_PLAN,
            NOW,
            {"plan": _plan()},
        ),
        NOW,
    )

    first = dispatch_scheduler_cycle(
        composition.runtime_service,
        [_current_ref(storage, session_id)],
        NOW + timedelta(seconds=10),
    )
    second = dispatch_scheduler_cycle(
        composition.runtime_service,
        [_current_ref(storage, session_id)],
        NOW + timedelta(seconds=20),
    )
    third = dispatch_scheduler_cycle(
        composition.runtime_service,
        [_current_ref(storage, session_id)],
        NOW + timedelta(seconds=35),
    )
    fourth = dispatch_scheduler_cycle(
        composition.runtime_service,
        [_current_ref(storage, session_id)],
        NOW + timedelta(seconds=40),
    )

    assert [result.phase for result in first.dispatched] == [LiveSessionPhase.PLANNED_SHOW]
    assert [result.phase for result in second.dispatched] == [LiveSessionPhase.AFTERTALK]
    assert [result.phase for result in third.dispatched] == [LiveSessionPhase.CLOSING]
    assert [result.phase for result in fourth.dispatched] == [LiveSessionPhase.ENDED]
    assert len(planned_show.calls) == 1
    assert len(aftertalk.calls) == 1
    assert len(closing.calls) == 1
    assert storage.get_v2_session(session_id)["current_phase"] == "ended"
    assert storage.get_v2_session(session_id)["closing_completed"] is True


def _runtime_command(command_id, session_id, command_type, now, payload=None):
    return RuntimeCommand(
        command_id=command_id,
        session_id=session_id,
        command_type=command_type,
        issued_at=now,
        permission_context={"operator_id": "automation-test"},
        payload=payload or {},
    )


def _plan():
    return {
        "plan_id": "plan-auto",
        "title": "Automation phase advancement",
        "turns": [
            {
                "id": "opening",
                "purpose": "Open the show.",
                "topic_cue": "Automation test.",
                "speaker_policy": {"type": "fixed", "speaker_ids": ["host"]},
                "audience_insertion": {
                    "enabled": False,
                    "allow_super_chats": False,
                },
            }
        ],
        "raw_topic_pack": "must not leak",
    }


def _current_ref(storage, session_id):
    record = storage.get_v2_session(session_id)
    return SchedulerSessionRef(session_id, current_phase=record["current_phase"])


def _imported_modules(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.append(node.module or "")
    return modules


def test_runtime_automation_contract_does_not_import_server_storage_or_adapters():
    modules = _imported_modules(ROOT / "YouTubeBridgeV2" / "runtime" / "automation.py")
    forbidden = [
        module
        for module in modules
        if module.startswith("YouTubeBridgeV2.server")
        or module.startswith("YouTubeBridgeV2.storage")
        or module.startswith("YouTubeBridgeV2.adapters")
        or module in {"sqlite3", "aiosqlite", "requests", "googleapiclient"}
    ]

    assert forbidden == []
