"""YouTubeBridge 留言注入與 interaction 執行 mixin。"""
from __future__ import annotations

import asyncio
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
        super_chats.sort(key=lambda item: (-int(item.get("sc_tier", 0) or 0), int(item.get("id", 0) or 0)))
        normal.sort(key=lambda item: int(item.get("id", 0) or 0))
        selected = super_chats[:max(1, int(max_sc_per_batch or 5))]
        remaining = max(0, int(max_events or 1) - len(selected))
        selected.extend(normal[:remaining])
        return selected[:max(1, int(max_events or 1))]

    def _sc_interrupt_allowed(self, runtime: LiveRuntime, session: dict[str, Any]) -> bool:
        cooldown = max(0, int(session.get("sc_interrupt_cooldown_seconds", 30) or 30))
        last = self._parse_iso_datetime(runtime.last_sc_interrupt_at)
        if not last:
            return True
        return (datetime.now() - last).total_seconds() >= cooldown

    async def _auto_inject_loop(self, runtime: LiveRuntime) -> None:
        while runtime.running:
            session = self.storage.get_session(runtime.session_id)
            if not session:
                return
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
                    selected = self._select_pending_events_for_injection(
                        active_pending,
                        max_events=max_pending,
                        max_sc_per_batch=max_sc_per_batch,
                    )
                    selected_sc = [event for event in selected if event.get("priority_class") == "super_chat"]
                    active_interaction = bool(self.storage.get_active_interaction(runtime.session_id))
                    sleep_seconds = self._auto_inject_delay(
                        session,
                        len(active_pending),
                        active_interaction=active_interaction,
                    )
                    if (selected_sc or len(active_pending) >= min_pending) and selected:
                        sc_interrupt_allowed = bool(selected_sc and self._sc_interrupt_allowed(runtime, session))
                        if active_interaction and not sc_interrupt_allowed and len(active_pending) < max_pending:
                            await asyncio.sleep(sleep_seconds)
                            continue
                        forced_by_backlog = active_interaction and len(active_pending) >= max_pending
                        if selected_sc:
                            max_tier = max(int(event.get("sc_tier", 0) or 0) for event in selected_sc)
                            priority = 320 if max_tier >= 3 else 260
                            source = "super_chat"
                            if active_interaction and sc_interrupt_allowed:
                                runtime.last_sc_interrupt_at = datetime.now().isoformat()
                        else:
                            priority = 180 if forced_by_backlog else 100
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
            await self.classify_pending_events(session_id)
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
                        "summary": summary,
                    },
                }
            )
            job_id = interaction["job_id"]
            claimed = await self._claim_interaction_for_execution(runtime, interaction)
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

            def should_cancel() -> bool:
                current = self.storage.get_interaction(job_id)
                return cancel_event.is_set() or bool(current and current.get("status") == "interrupt_requested")

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
