"""Production-safe no-op runners for YouTubeBridgeV2 app wiring.

這些 runner 只讓主 app 可以接上真 durable storage composition。它們不呼叫
YouTube、MemoriaCore 或 TTS，也不自行寫入 runtime state。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from YouTubeBridgeV2.runtime.application_service import (
    AdapterDispatchResult,
    RuntimeCommand,
)
from YouTubeBridgeV2.runtime.phase import LiveSessionSnapshot, PhaseTransition


@dataclass(frozen=True)
class _NoopRunner:
    runner_name: str

    def run(
        self,
        *,
        command: RuntimeCommand,
        snapshot: LiveSessionSnapshot,
        transition: PhaseTransition,
        now: datetime,
    ) -> AdapterDispatchResult:
        """回傳 public-safe no-op dispatch result，不產生外部 side effect。"""

        return AdapterDispatchResult(
            status="ok",
            summary={
                "mode": "noop",
                "runner": self.runner_name,
                "external_adapter": "not_configured",
                "next_action": transition.next_action,
            },
            retryable=False,
        )


class NoopPlannedShowRunner(_NoopRunner):
    """正式節目 phase 的 production-safe no-op runner。"""

    def __init__(self) -> None:
        super().__init__(runner_name="planned_show")


class NoopAftertalkRunner(_NoopRunner):
    """Aftertalk phase 的 production-safe no-op runner。"""

    def __init__(self) -> None:
        super().__init__(runner_name="aftertalk")


class NoopClosingRunner(_NoopRunner):
    """Closing phase 的 production-safe no-op runner。"""

    def __init__(self) -> None:
        super().__init__(runner_name="closing")


__all__ = [
    "NoopAftertalkRunner",
    "NoopClosingRunner",
    "NoopPlannedShowRunner",
]
