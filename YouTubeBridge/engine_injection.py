"""YouTubeBridge 留言注入與 interaction 執行 mixin。"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import threading
from datetime import datetime, timedelta
from typing import Any

from bridge_contracts import CONTROLLED_CONTEXT_CONTENT, DEFAULT_INJECT_CONTENT
from bridge_runtime import LiveRuntime
from memoria_client import GenerationInterrupted


logger = logging.getLogger("youtube_bridge")


class InjectionManagerMixin:
    @staticmethod
    def _auto_inject_delay(session: dict[str, Any], pending_count: int, *, active_interaction: bool) -> float:
        base = max(5, min(int(session.get("inject_interval_seconds", 30) or 30), 600))
        max_pending = max(
            int(session.get("min_pending_events", 1) or 1),
            int(session.get("max_pending_events", 12) or 12),
        )
        if active_interaction:
            return float(base)
        try:
            min_seconds_raw = session.get("inject_min_interval_seconds")
            if min_seconds_raw is None:
                legacy_ratio = float(session.get("inject_min_interval_ratio", 0.32) or 0.32)
                min_seconds = int(round(base * legacy_ratio))
            else:
                min_seconds = int(min_seconds_raw or 5)
        except (TypeError, ValueError):
            min_seconds = int(round(base * 0.32))
        min_seconds = max(5, min(min_seconds, base))
        ratio = max(0.0, min(1.0, pending_count / max_pending))
        return float(max(min_seconds, int(round(base - ((base - min_seconds) * math.sqrt(ratio))))))

    @staticmethod
    def _select_pending_events_for_injection(
        events: list[dict[str, Any]],
        *,
        max_events: int,
        max_sc_per_batch: int = 5,
    ) -> list[dict[str, Any]]:
        active = [
            event for event in events
            if event.get("status") == "active" and str(event.get("message_text") or "").strip()
        ]
        super_chats = [event for event in active if event.get("priority_class") == "super_chat"]
        normal = [event for event in active if event.get("priority_class") != "super_chat"]
        if super_chats:
            super_chats.sort(key=lambda item: (-int(item.get("sc_tier", 0) or 0), int(item.get("id", 0) or 0)))
            return super_chats[:max(1, int(max_sc_per_batch or 5))]
        normal.sort(key=lambda item: int(item.get("id", 0) or 0))
        return normal[:max(1, int(max_events or 1))]

    def _sc_interrupt_allowed(self, runtime: LiveRuntime, session: dict[str, Any]) -> bool:
        cooldown = max(0, int(session.get("sc_interrupt_cooldown_seconds", 30) or 30))
        last = self._parse_iso_datetime(runtime.last_sc_interrupt_at)
        if not last:
            return True
        return (datetime.now() - last).total_seconds() >= cooldown

    def _director_owns_auto_inject(self, session: dict[str, Any]) -> bool:
        session_id = str(session.get("session_id") or "").strip()
        if not session_id or not self._episode_plan_for_session(session):
            return False
        director_state = self.storage.get_director_state(session_id)
        return bool(director_state.get("director_enabled"))

    @staticmethod
    def _active_director_interaction_matches_events(
        active: dict[str, Any] | None,
        *,
        action: str,
        event_ids: list[int],
    ) -> bool:
        if not active or active.get("status") != "running" or active.get("source") != "director":
            return False
        metadata = active.get("metadata") if isinstance(active.get("metadata"), dict) else {}
        decision = metadata.get("decision") if isinstance(metadata.get("decision"), dict) else {}
        if str(decision.get("action") or "") != action:
            return False

        raw_ids = active.get("event_ids_json") if "event_ids_json" in active else active.get("event_ids")
        if raw_ids is None:
            external_context = (
                metadata.get("external_context")
                if isinstance(metadata.get("external_context"), dict)
                else {}
            )
            raw_ids = external_context.get("event_ids")
        if isinstance(raw_ids, str):
            try:
                raw_ids = json.loads(raw_ids)
            except json.JSONDecodeError:
                raw_ids = []
        if not isinstance(raw_ids, list):
            raw_ids = []

        active_ids: set[int] = set()
        for event_id in raw_ids:
            try:
                active_ids.add(int(event_id))
            except (TypeError, ValueError):
                continue
        selected_ids = {int(event_id) for event_id in event_ids}
        return bool(selected_ids) and active_ids == selected_ids

    async def _prepare_director_owned_auto_inject(
        self,
        runtime: LiveRuntime,
        session: dict[str, Any],
        active_pending: list[dict[str, Any]],
        *,
        max_events: int,
        max_sc_per_batch: int,
        active: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        session_id = str(session.get("session_id") or runtime.session_id)
        candidate_ids: list[int] = []
        classify_ids: list[int] = []
        for event in active_pending:
            try:
                event_id = int(event.get("id") or 0)
            except (TypeError, ValueError):
                continue
            if (
                event_id <= 0
                or str(event.get("status") or "active") != "active"
                or not str(event.get("message_text") or "").strip()
            ):
                continue
            candidate_ids.append(event_id)
            if str(event.get("safety_status") or "pending") != "completed":
                classify_ids.append(event_id)
        if classify_ids:
            await self.classify_event_ids_serialized(session_id, classify_ids)

        if candidate_ids:
            refreshed = self.storage.get_events_by_ids(session_id, candidate_ids, limit=len(candidate_ids))
        else:
            refreshed = []
        refreshed = [
            event for event in refreshed
            if str(event.get("status") or "active") == "active"
            and str(event.get("message_text") or "").strip()
            and not str(event.get("injected_at") or "").strip()
            and self._is_public_live_event_displayable(event)
        ]
        selection_session = dict(session)
        selection_session["max_pending_events"] = max_events
        selection_session["max_sc_per_batch"] = max_sc_per_batch
        selected = self._episode_select_audience_event_batch(selection_session, refreshed)
        selected_ids = [int(event["id"]) for event in selected if int(event.get("id") or 0)]
        selected_sc = [event for event in selected if str(event.get("priority_class") or "") == "super_chat"]
        selected_source = "super_chat" if selected_sc else ("chat" if selected else "none")
        interrupted_active = False
        return {
            "handled_by_director": True,
            "selected_event_ids": selected_ids,
            "selected_source": selected_source,
            "interrupted_active": interrupted_active,
        }

    async def prepare_director_super_chat_reply_batch(
        self,
        session_id: str,
        *,
        event_ids: list[int],
    ) -> dict[str, Any]:
        session = self.storage.get_session(session_id)
        if not session:
            raise ValueError("live session 不存在")
        if not self._director_owns_auto_inject(session):
            raise ValueError("director 未接管此 live session")
        normalized_ids: list[int] = []
        for event_id in event_ids:
            try:
                normalized_ids.append(int(event_id))
            except (TypeError, ValueError):
                continue
        if not normalized_ids:
            raise ValueError("沒有未處理 Super Chat")

        events = self.storage.get_events_by_ids(session_id, normalized_ids, limit=len(normalized_ids))
        runtime = self._runtimes.setdefault(session_id, LiveRuntime(session_id=session_id))
        result = await self._prepare_director_owned_auto_inject(
            runtime,
            session,
            events,
            max_events=max(1, int(session.get("max_pending_events", 12) or 12)),
            max_sc_per_batch=max(1, int(session.get("max_sc_per_batch", 5) or 5)),
        )
        selected_ids = [int(event_id) for event_id in result.get("selected_event_ids", [])]
        if not selected_ids:
            raise ValueError("沒有可交給導播回應的 Super Chat")
        await self._broadcast(session_id, {
            "type": "director_audience_events_ready",
            "event_ids": selected_ids,
            "source": "super_chat",
            "count": len(selected_ids),
            "interrupted_active": bool(result.get("interrupted_active")),
        })
        return {
            "status": "queued_for_director",
            "session_id": session_id,
            "event_ids": selected_ids,
            "source": "super_chat",
            "interrupted_active": bool(result.get("interrupted_active")),
        }

    def _director_audience_prepare_blocked(
        self,
        session_id: str,
        state: dict[str, Any] | None = None,
    ) -> bool:
        current_state = state or self.storage.get_director_state(session_id)
        metadata = current_state.get("metadata") if isinstance(current_state.get("metadata"), dict) else {}
        if metadata.get("audience_prepare_in_flight"):
            return True
        finder = getattr(self, "_audience_gap_interaction_by_status", None)
        if callable(finder):
            return bool(finder(session_id, {"preparing", "prepared", "presenting"}))
        return False

    def _mark_director_audience_prepare_in_flight(
        self,
        session_id: str,
        *,
        event_ids: list[int],
        source: str,
    ) -> dict[str, Any]:
        return self.storage.update_director_state(
            session_id,
            metadata={
                "audience_prepare_in_flight": True,
                "latest_audience_prepare_event_ids": list(event_ids),
                "latest_audience_prepare_source": source,
                "last_audience_prepare_error": "",
            },
        )

    def _mark_director_audience_prepare_finished(
        self,
        session_id: str,
        *,
        error: str = "",
        interaction: dict[str, Any] | None = None,
        cancelled_reason: str = "",
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "audience_prepare_in_flight": False,
            "last_audience_prepare_error": error,
        }
        if cancelled_reason:
            metadata["audience_prepare_cancelled_reason"] = cancelled_reason
        if interaction:
            metadata["latest_audience_gap_job_id"] = interaction.get("job_id", "")
        return self.storage.update_director_state(session_id, metadata=metadata)

    def _director_audience_prepare_session_live(self, runtime: LiveRuntime) -> bool:
        stopped_statuses = {"closing", "stopped", "ended"}
        if not runtime.running or str(runtime.status or "") in stopped_statuses:
            return False
        session = self.storage.get_session(runtime.session_id)
        if not session or str(session.get("status") or "") in stopped_statuses:
            return False
        return True

    async def _run_director_audience_gap_prepare_background(
        self,
        runtime: LiveRuntime,
        session: dict[str, Any],
        state: dict[str, Any],
        *,
        event_ids: list[int],
        source: str,
        decision: dict[str, Any] | None = None,
    ) -> None:
        session_id = runtime.session_id
        try:
            prepare_result = await self._prepare_next_audience_gap_turn(
                runtime,
                session,
                state,
                decision=decision,
            )
            interaction = (
                prepare_result.get("interaction")
                if isinstance(prepare_result, dict) and isinstance(prepare_result.get("interaction"), dict)
                else None
            )
            status = str((interaction or {}).get("status") or "")
            if not self._director_audience_prepare_session_live(runtime):
                error = "session_not_running"
                runtime.last_auto_inject_error = error
                if interaction and str(interaction.get("status") or "") in {"preparing", "prepared"}:
                    self._discard_prepared_items_for_interaction(session_id, interaction.get("job_id", ""), error)
                    interaction = self.storage.update_interaction(
                        interaction["job_id"],
                        status="interrupted",
                        reason=error,
                        completed_at=datetime.now().isoformat(),
                        interrupted_at=datetime.now().isoformat(),
                        metadata={
                            "prepare_ready": False,
                            "audience_prepare_cancelled_reason": error,
                        },
                    ) or interaction
                self._mark_director_audience_prepare_finished(
                    session_id,
                    error=error,
                    interaction=interaction,
                    cancelled_reason=error,
                )
                return
            if status != "prepared":
                error = f"audience_gap_prepare_failed:{status or 'none'}"
                runtime.last_auto_inject_error = error
                self._mark_director_audience_prepare_finished(
                    session_id,
                    error=error,
                    interaction=interaction,
                )
                return
            runtime.last_auto_inject_error = None
            self._mark_director_audience_prepare_finished(session_id, interaction=interaction)
            await self._broadcast(session_id, {
                "type": "director_audience_gap_ready",
                "interaction": interaction,
                "event_ids": event_ids,
                "source": source,
            })
        except asyncio.CancelledError:
            error = "audience_gap_prepare_cancelled"
            runtime.last_auto_inject_error = error
            self._mark_director_audience_prepare_finished(
                session_id,
                error=error,
                cancelled_reason=error,
            )
            raise
        except Exception as exc:  # pragma: no cover - defensive background task guard
            error = str(exc)
            runtime.last_auto_inject_error = error
            self._mark_director_audience_prepare_finished(session_id, error=error)
            logger.exception("YouTube audience gap prepare error session_id=%s error=%s", session_id, error)

    @staticmethod
    def _consume_background_task_exception(task: asyncio.Task) -> None:
        try:
            task.exception()
        except asyncio.CancelledError:
            return

    async def _auto_inject_loop(self, runtime: LiveRuntime) -> None:
        while runtime.running:
            session = self.storage.get_session(runtime.session_id)
            if not session:
                return
            if runtime.status == "closing" or session.get("status") == "closing":
                await asyncio.sleep(1.0)
                continue
            if self._duration_reached(session):
                await self._finalize_for_duration(runtime, session)
                return
            interval = max(5, min(int(session.get("inject_interval_seconds", 30) or 30), 600))
            sleep_seconds = float(interval)
            try:
                if session.get("auto_inject"):
                    pending = self.storage.list_events(
                        runtime.session_id,
                        limit=int(session.get("max_context_messages", 50) or 50),
                        uninjected_only=True,
                    )
                    active_pending = [
                        event for event in pending
                        if event.get("status") == "active" and event.get("message_text")
                    ]
                    min_pending = max(1, int(session.get("min_pending_events", 1) or 1))
                    max_pending = max(min_pending, int(session.get("max_pending_events", 12) or 12))
                    max_sc_per_batch = max(1, int(session.get("max_sc_per_batch", 5) or 5))
                    active = self.storage.get_active_interaction(runtime.session_id)
                    active_interaction = bool(active)
                    active_running = bool(active and active.get("status") == "running")
                    sleep_seconds = self._auto_inject_delay(
                        session,
                        len(active_pending),
                        active_interaction=active_interaction,
                    )
                    if active and active.get("status") == "presenting":
                        await asyncio.sleep(sleep_seconds)
                        continue
                    if self._director_owns_auto_inject(session):
                        result = await self._prepare_director_owned_auto_inject(
                            runtime,
                            session,
                            active_pending,
                            max_events=max_pending,
                            max_sc_per_batch=max_sc_per_batch,
                            active=active,
                        )
                        selected_event_ids = result.get("selected_event_ids", [])
                        selected_source = result.get("selected_source", "none")
                        if not selected_event_ids:
                            await asyncio.sleep(sleep_seconds)
                            continue
                        if selected_source != "super_chat" and len(selected_event_ids) < min_pending:
                            await asyncio.sleep(sleep_seconds)
                            continue
                        scheduled = await self._schedule_audience_gap_prepare_if_needed(
                            runtime,
                            session,
                            self.storage.get_director_state(runtime.session_id),
                            trigger="auto_inject_loop",
                        )
                        if not scheduled:
                            await asyncio.sleep(sleep_seconds)
                            continue
                        await asyncio.sleep(sleep_seconds)
                        continue
                    selected = self._select_pending_events_for_injection(
                        active_pending,
                        max_events=max_pending,
                        max_sc_per_batch=max_sc_per_batch,
                    )
                    raw_selected_sc = [event for event in selected if event.get("priority_class") == "super_chat"]
                    if raw_selected_sc:
                        selected_sc = [
                            event for event in raw_selected_sc
                            if self._is_public_live_event_displayable(event)
                        ]
                        if not selected_sc:
                            selected = [
                                event for event in active_pending
                                if event.get("priority_class") != "super_chat"
                                and self._is_public_live_event_displayable(event)
                            ]
                            selected.sort(key=lambda item: int(item.get("id", 0) or 0))
                            selected = selected[:max_pending]
                            selected_sc = []
                            if not selected:
                                await asyncio.sleep(sleep_seconds)
                                continue
                        else:
                            selected = selected_sc
                    else:
                        selected_sc = []
                    if (selected_sc or len(selected) >= min_pending) and selected:
                        sc_interrupt_allowed = bool(selected_sc and self._sc_interrupt_allowed(runtime, session))
                        if active_interaction and not selected_sc:
                            await asyncio.sleep(sleep_seconds)
                            continue
                        if selected_sc:
                            max_tier = max(int(event.get("sc_tier", 0) or 0) for event in selected_sc)
                            priority = 320 if max_tier >= 3 else 260
                            source = "super_chat"
                            active_priority = int((active or {}).get("priority", 100) or 100)
                            if active_interaction and priority <= active_priority:
                                await asyncio.sleep(sleep_seconds)
                                continue
                            if active_running and not sc_interrupt_allowed:
                                await asyncio.sleep(sleep_seconds)
                                continue
                            if active_running and sc_interrupt_allowed and priority > active_priority:
                                runtime.last_sc_interrupt_at = datetime.now().isoformat()
                        else:
                            priority = 100
                            source = "auto_inject"
                        result = await self.inject_recent(
                            runtime.session_id,
                            event_ids=[event["id"] for event in selected],
                            max_events=session.get("max_context_messages", 50),
                            content=CONTROLLED_CONTEXT_CONTENT,
                            source=source,
                            priority=priority,
                        )
                        runtime.last_auto_inject_at = result.get("injected_at")
                        runtime.last_auto_inject_error = None
                        if selected_sc:
                            await self._broadcast(runtime.session_id, {
                                "type": "super_chat_batch_injected",
                                "event_ids": [event["id"] for event in selected_sc],
                                "count": len(selected_sc),
                            })
                await asyncio.sleep(sleep_seconds)
            except asyncio.CancelledError:
                raise
            except ValueError as exc:
                expected_wait = "沒有可注入" in str(exc) or "觀眾查詢資料搜尋中" in str(exc)
                if not expected_wait:
                    runtime.last_auto_inject_error = str(exc)
                    await self._broadcast(runtime.session_id, {
                        "type": "auto_inject_error",
                        "message": str(exc),
                    })
                await asyncio.sleep(sleep_seconds)
            except Exception as exc:
                runtime.last_auto_inject_error = str(exc)
                logger.error("YouTube auto inject error session_id=%s error=%s", runtime.session_id, exc, exc_info=True)
                await self._broadcast(runtime.session_id, {
                    "type": "auto_inject_error",
                    "message": str(exc),
                })
                await asyncio.sleep(sleep_seconds)

    async def interrupt_session(self, session_id: str, *, reason: str = "manual_interrupt") -> dict[str, Any]:
        session = self.storage.get_session(session_id)
        if not session:
            raise ValueError("live session 不存在")
        interactions = self.storage.request_interrupt(session_id, reason=reason)
        runtime = self._runtimes.get(session_id)
        if runtime:
            for interaction in interactions:
                cancel_event = runtime.cancel_events.get(interaction.get("job_id", ""))
                if cancel_event:
                    cancel_event.set()
        closure_text = "先停在這裡，剛剛聊天室有新的問題，我們切過去看。"
        await self._broadcast(session_id, {
            "type": "interrupt_requested",
            "reason": reason,
            "closure_text": closure_text,
            "interactions": interactions,
        })
        return {
            "session_id": session_id,
            "reason": reason,
            "closure_text": closure_text,
            "interrupted_count": len(interactions),
            "interactions": interactions,
        }

    async def _claim_interaction_for_execution(
        self,
        runtime: LiveRuntime,
        interaction: dict[str, Any],
        *,
        timeout_seconds: float = 30.0,
    ) -> dict[str, Any] | None:
        """等待 interaction 成為單一 running job。"""
        job_id = str(interaction.get("job_id") or "")
        deadline = datetime.now() + timedelta(seconds=max(1.0, timeout_seconds))
        while True:
            claimed = self.storage.claim_interaction(job_id)
            if claimed and claimed.get("status") == "running":
                await self._broadcast(runtime.session_id, {"type": "interaction_started", "interaction": claimed})
                return claimed
            current = self.storage.get_interaction(job_id)
            if not current or current.get("status") != "queued":
                return current
            if datetime.now() >= deadline:
                updated = self.storage.update_interaction(
                    job_id,
                    status="interrupted",
                    reason="claim_timeout_active_generation",
                    completed_at=datetime.now().isoformat(),
                    metadata={"claim_timeout": True},
                )
                await self._broadcast(runtime.session_id, {"type": "interaction_interrupted", "interaction": updated})
                return updated
            await asyncio.sleep(0.2)

    @staticmethod
    def _normalized_interrupt_reason(current: dict[str, Any] | None, exc: Exception) -> str:
        existing = str((current or {}).get("reason") or "").strip()
        if existing:
            return existing[:500]
        message = str(exc)
        if "NoneType" in message and "read" in message:
            return "interrupted_by_higher_priority"
        return message[:500]

    async def inject_recent(
        self,
        session_id: str,
        *,
        event_ids: list[int] | None = None,
        max_events: int | None = None,
        content: str = DEFAULT_INJECT_CONTENT,
        memoria_session_id: str = "",
        character_ids: list[str] | None = None,
        source: str = "manual_inject",
        priority: int = 200,
        claim_timeout_seconds: float = 30.0,
    ) -> dict[str, Any]:
        runtime = self._runtimes.setdefault(session_id, LiveRuntime(session_id=session_id))
        active = self.storage.get_active_interaction(session_id)
        if active and active.get("status") == "running" and int(priority) > int(active.get("priority", 100)):
            await self.interrupt_session(session_id, reason=f"higher_priority:{source}")
        async with runtime.inject_lock:
            session = self.storage.get_session(session_id)
            if not session:
                raise ValueError("live session 不存在")
            if session.get("status") in {"closing", "ended"} and source != "director":
                raise ValueError("live session closing/ended，不再接受一般注入")
            if event_ids:
                await self.classify_event_ids_serialized(session_id, event_ids)
            else:
                await self.classify_pending_events_serialized(session_id)
            external_context, summary = self.build_external_context(
                session_id,
                event_ids=event_ids,
                max_events=max_events,
            )
            target_session_id = memoria_session_id or session.get("target_memoria_session_id", "")
            target_character_ids = character_ids or session.get("character_ids", [])
            interaction = self.storage.create_interaction(
                {
                    "session_id": session_id,
                    "source": source,
                    "priority": priority,
                    "status": "queued",
                    "event_ids": summary.get("event_ids", []),
                    "memoria_session_id": target_session_id,
                    "character_ids": target_character_ids,
                    "content": content,
                    "metadata": {
                        "phase": self._interaction_phase_for_session(session_id, source=source),
                        "summary": summary,
                    },
                }
            )
            job_id = interaction["job_id"]
            claimed = await self._claim_interaction_for_execution(
                runtime,
                interaction,
                timeout_seconds=claim_timeout_seconds,
            )
            if not claimed or claimed.get("status") != "running":
                return {
                    "summary": summary,
                    "marked_injected": 0,
                    "memoria_result": {},
                    "interaction": claimed or interaction,
                    "injected_at": datetime.now().isoformat(),
                }
            interaction = claimed

            client = self._memoria_client()
            cancel_event = threading.Event()
            runtime.cancel_events[job_id] = cancel_event
            loop = asyncio.get_running_loop()

            def should_cancel() -> bool:
                current = self.storage.get_interaction(job_id)
                return cancel_event.is_set() or bool(current and current.get("status") == "interrupt_requested")

            def on_stream_result(event: dict[str, Any]) -> None:
                self._dispatch_stream_chat_result(
                    loop,
                    session_id,
                    event,
                    source=source,
                    interaction_job_id=job_id,
                )

            try:
                result = await asyncio.to_thread(
                    client.chat_stream_sync,
                    content=content,
                    display_content=self._display_content_from_external_context(external_context),
                    session_id=target_session_id,
                    character_ids=target_character_ids,
                    external_context=external_context,
                    should_cancel=should_cancel,
                    cancel_event=cancel_event,
                    on_result=on_stream_result,
                )
            except GenerationInterrupted:
                closure_text = "先停在這裡，剛剛聊天室有新的問題，我們切過去看。"
                interrupted = self.storage.update_interaction(
                    job_id,
                    status="interrupted",
                    closure_text=closure_text,
                    completed_at=datetime.now().isoformat(),
                    metadata={"discarded": True},
                )
                await self._broadcast(session_id, {"type": "interaction_interrupted", "interaction": interrupted})
                return {
                    "summary": summary,
                    "marked_injected": 0,
                    "memoria_result": {},
                    "interaction": interrupted,
                    "injected_at": datetime.now().isoformat(),
                }
            except Exception as exc:
                current = self.storage.get_interaction(job_id)
                was_interrupted = cancel_event.is_set() or bool(current and current.get("status") == "interrupt_requested")
                reason = self._normalized_interrupt_reason(current, exc)
                updated = self.storage.update_interaction(
                    job_id,
                    status="interrupted" if was_interrupted else "failed",
                    reason=reason,
                    closure_text="先停在這裡，剛剛聊天室有新的問題，我們切過去看。" if was_interrupted else "",
                    completed_at=datetime.now().isoformat(),
                    metadata={
                        "discarded": was_interrupted,
                        "error": str(exc)[:500],
                        "normalized_reason": reason,
                    },
                )
                await self._broadcast(
                    session_id,
                    {
                        "type": "interaction_interrupted" if was_interrupted else "interaction_failed",
                        "interaction": updated,
                    },
                )
                if was_interrupted:
                    return {
                        "summary": summary,
                        "marked_injected": 0,
                        "memoria_result": {},
                        "interaction": updated,
                        "injected_at": datetime.now().isoformat(),
                    }
                raise
            finally:
                runtime.cancel_events.pop(job_id, None)

            current_after = self.storage.get_interaction(job_id)
            interrupted_after_provider = bool(
                current_after and current_after.get("status") in {"interrupt_requested", "interrupted", "discarded"}
            )
            marked_injected = self.storage.mark_events_injected(session_id, summary.get("event_ids", []))
            result_session_id = result.get("session_id") if isinstance(result, dict) else ""
            if result_session_id and result_session_id != session.get("target_memoria_session_id"):
                self.storage.update_session_fields(session_id, target_memoria_session_id=result_session_id)
            injected_at = datetime.now().isoformat()
            if interrupted_after_provider:
                interaction_status = "discarded"
                closure_text = "先停在這裡，剛剛聊天室有新的問題，我們切過去看。"
                reply_text = ""
            else:
                interaction_status = "completed"
                closure_text = ""
                reply_text = str(result.get("reply") or "") if isinstance(result, dict) else ""
            updated_interaction = self.storage.update_interaction(
                job_id,
                status=interaction_status,
                reply_text=reply_text,
                closure_text=closure_text,
                memoria_session_id=result_session_id or target_session_id,
                completed_at=injected_at,
                metadata={
                    "result_message_id": result.get("message_id") if isinstance(result, dict) else None,
                    "discarded_after_provider_return": interrupted_after_provider,
                },
            )
            payload = {
                "summary": summary,
                "marked_injected": marked_injected,
                "memoria_result": result,
                "interaction": updated_interaction,
                "injected_at": injected_at,
            }
            await self._broadcast(session_id, {
                "type": "interaction_completed",
                "interaction": updated_interaction,
                "memoria_session_id": result_session_id or target_session_id,
                "source": source,
            })
            await self._broadcast(session_id, {
                "type": "memoria_injected",
                "summary": summary,
                "marked_injected": marked_injected,
                "memoria_session_id": result_session_id or target_session_id,
                "interaction": updated_interaction,
            })
            director_state = self.storage.get_director_state(session_id)
            if source != "director" and director_state.get("director_enabled"):
                metadata = dict(director_state.get("metadata") or {})
                chat_batches = int(metadata.get("chat_batches_since_anchor", 0) or 0) + 1
                metadata["chat_batches_since_anchor"] = chat_batches
                max_batches = max(1, int(session.get("director_max_chat_batches_before_anchor", 2) or 2))
                if chat_batches >= max_batches:
                    metadata["anchor_requested_at"] = datetime.now().isoformat()
                next_state = self.storage.update_director_state(
                    session_id,
                    status="running",
                    consecutive_ai_turns=0,
                    last_seen_event_id=max(summary.get("event_ids", [0]) or [0]),
                    last_director_action_at=datetime.now().isoformat(),
                    metadata=metadata,
                )
                await self._broadcast(session_id, {"type": "director_state", "director": next_state})
            return payload
