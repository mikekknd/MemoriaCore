from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

from YouTubeBridgeV2.runtime.application_service import RuntimeCommandType
from YouTubeBridgeV2.runtime.automation import (
    AutomationTickPolicy,
    build_scheduler_tick_intent,
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
