"""Runtime adapters for prepared turn consumption."""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Callable

from bridge_runtime import LiveRuntime
from turn_pipeline import PreparedTurnPayload


class PreparedTurnRuntimeAdapter:
    def __init__(
        self,
        manager: Any,
        runtime: LiveRuntime,
        session: dict[str, Any],
        *,
        extra_completion_metadata: dict[str, Any] | None = None,
    ) -> None:
        self.manager = manager
        self.runtime = runtime
        self.session = session
        self.extra_completion_metadata = dict(extra_completion_metadata or {})

    def get_interaction(self, job_id: str) -> dict[str, Any] | None:
        return self.manager.storage.get_interaction(job_id)

    def prepared_results_for_interaction(
        self,
        interaction: dict[str, Any],
        *,
        require_complete: bool,
    ) -> list[dict[str, Any]]:
        return self.manager._prepared_results_for_interaction(
            self.runtime.session_id,
            interaction,
            require_complete=require_complete,
        )

    def claim_interaction(self, job_id: str, expected_status: str) -> dict[str, Any] | None:
        if hasattr(self.manager.storage, "update_interaction_if_status"):
            return self.manager.storage.update_interaction_if_status(
                job_id,
                expected_status,
                status="presenting",
            )
        return self.manager.storage.update_interaction(job_id, status="presenting")

    async def broadcast(self, payload: dict[str, Any]) -> None:
        await self.manager._broadcast(self.runtime.session_id, payload)

    async def present_prepared_results(
        self,
        prepared_results: list[dict[str, Any]],
        *,
        source: str,
        interaction_job_id: str,
    ) -> Any:
        return await self.manager.present_prepared_stream_results(
            self.runtime.session_id,
            prepared_results,
            source=source,
            interaction_job_id=interaction_job_id,
        )

    def visible_prepared_results(
        self,
        prepared_results: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return self.manager._visible_prepared_results(self.session, prepared_results)

    def prepared_result_item_count(self, prepared_results: list[dict[str, Any]]) -> int:
        return self.manager._prepared_result_item_count(prepared_results)

    def mark_audience_events_injected(self, interaction: dict[str, Any]) -> int:
        event_ids: list[int] = []
        for raw_event_id in interaction.get("event_ids") or []:
            try:
                event_id = int(raw_event_id)
            except (TypeError, ValueError):
                continue
            if event_id > 0:
                event_ids.append(event_id)
        return (
            self.manager.storage.mark_events_injected(self.runtime.session_id, event_ids)
            if event_ids
            else 0
        )

    def complete_interaction(
        self,
        job_id: str,
        *,
        reply_text: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any] | None:
        if self.extra_completion_metadata:
            metadata = {**metadata, **self.extra_completion_metadata}
        metadata = self.completion_metadata(metadata)
        if hasattr(self.manager.storage, "update_interaction_if_status"):
            return self.manager.storage.update_interaction_if_status(
                job_id,
                "presenting",
                status="completed",
                reply_text=reply_text,
                completed_at=datetime.now().isoformat(),
                metadata=metadata,
            )
        return self.manager.storage.update_interaction(
            job_id,
            status="completed",
            reply_text=reply_text,
            completed_at=datetime.now().isoformat(),
            metadata=metadata,
        )

    def completion_metadata(self, metadata: dict[str, Any]) -> dict[str, Any]:
        return metadata

    async def schedule_followup_prefetch(
        self,
        payload: PreparedTurnPayload,
        *,
        allow_audience: bool,
    ) -> Any:
        return None


class DirectorPreparedTurnAdapter(PreparedTurnRuntimeAdapter):
    def __init__(
        self,
        manager: Any,
        runtime: LiveRuntime,
        session: dict[str, Any],
        *,
        delay_before_followup: bool = True,
        extra_completion_metadata: dict[str, Any] | None = None,
        timing_log: Callable[..., None] | None = None,
    ) -> None:
        super().__init__(
            manager,
            runtime,
            session,
            extra_completion_metadata=extra_completion_metadata,
        )
        self.delay_before_followup = delay_before_followup
        self.timing_log = timing_log
        self.after_memoria_task: Any = None

    def completion_metadata(self, metadata: dict[str, Any]) -> dict[str, Any]:
        if metadata.get("audience_prepare_consumed") is True:
            metadata = dict(metadata)
            metadata["audience_gap_presented"] = int(metadata.get("played_item_count") or 0) > 0
        return metadata

    async def schedule_followup_prefetch(
        self,
        payload: PreparedTurnPayload,
        *,
        allow_audience: bool,
    ) -> Any:
        if self.after_memoria_task is not None:
            return self.after_memoria_task
        metadata = (
            payload.interaction.get("metadata")
            if isinstance(payload.interaction.get("metadata"), dict)
            else {}
        )
        chained_session = dict(self.session)
        main_session_id = str(
            metadata.get("main_memoria_session_id")
            or self.session.get("target_memoria_session_id")
            or ""
        )
        if main_session_id:
            chained_session["target_memoria_session_id"] = main_session_id
        if self.timing_log is not None:
            self.timing_log(
                "prefetch_chain_scheduled",
                session_id=self.runtime.session_id,
                job_id=payload.interaction.get("job_id"),
                source=payload.interaction.get("source"),
            )
        self.runtime.director_prefetch_in_flight += 1

        async def run_next_prefetch():
            try:
                if self.delay_before_followup:
                    await self.manager._yield_before_presentation_chain_prefetch()
                return await self.manager._prefetch_next_presentation_turn(
                    self.runtime,
                    chained_session,
                    payload.base_state,
                    payload.decision,
                    allow_audience=allow_audience,
                )
            finally:
                self.runtime.director_prefetch_in_flight = max(
                    0,
                    self.runtime.director_prefetch_in_flight - 1,
                )

        self.after_memoria_task = asyncio.create_task(run_next_prefetch())
        return self.after_memoria_task


class ClosingPreparedTurnAdapter(PreparedTurnRuntimeAdapter):
    def __init__(
        self,
        manager: Any,
        runtime: LiveRuntime,
        session: dict[str, Any],
        *,
        extra_completion_metadata: dict[str, Any] | None = None,
        before_present_callback=None,
    ) -> None:
        super().__init__(
            manager,
            runtime,
            session,
            extra_completion_metadata=extra_completion_metadata,
        )
        self.before_present_callback = before_present_callback
        self.callback_task: asyncio.Task | None = None

    async def present_prepared_results(
        self,
        prepared_results: list[dict[str, Any]],
        *,
        source: str,
        interaction_job_id: str,
    ) -> Any:
        if self.before_present_callback is not None and self.callback_task is None:
            maybe_callback_result = self.before_present_callback()
            if asyncio.iscoroutine(maybe_callback_result):
                self.callback_task = asyncio.create_task(maybe_callback_result)
        return await super().present_prepared_results(
            prepared_results,
            source=source,
            interaction_job_id=interaction_job_id,
        )
