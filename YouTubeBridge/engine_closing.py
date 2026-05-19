"""YouTubeBridge 直播收尾與 duration finalize mixin。"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import wave
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from bridge_contracts import SAFETY_CLASSIFIER_BATCH_LIMIT
from bridge_runtime import LiveRuntime


logger = logging.getLogger("youtube_bridge")
PROJECT_ROOT = Path(__file__).resolve().parents[1]

DURATION_CLOSING_ACTIVE_WAIT_TIMEOUT_SECONDS = 180.0
DURATION_CLOSING_ACTIVE_WAIT_POLL_SECONDS = 1.0
DURATION_CLOSING_ACTIVE_INTERRUPT_TIMEOUT_SECONDS = 1.0
CLOSING_PRESENTATION_MIN_GRACE_SECONDS = 12.0
CLOSING_PRESENTATION_MAX_GRACE_SECONDS = 45.0
CLOSING_PRESENTATION_SECONDS_PER_CHAR = 0.32
CLOSING_PRESENTATION_AUDIO_GRACE_MARGIN_SECONDS = 3.0


def _parse_iso(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _presentation_audio_duration_seconds(item: dict[str, Any]) -> float | None:
    audio_path_text = str(item.get("audio_path") or "").strip()
    if not audio_path_text:
        return None
    audio_format = str(item.get("audio_format") or "").strip().lower()
    audio_path = Path(audio_path_text)
    if not audio_path.is_absolute():
        audio_path = PROJECT_ROOT / audio_path
    if audio_path.suffix.lower() != ".wav" and audio_format != "wav":
        return None
    try:
        with wave.open(str(audio_path), "rb") as wav:
            frame_rate = wav.getframerate()
            if frame_rate <= 0:
                return None
            return wav.getnframes() / float(frame_rate)
    except (OSError, EOFError, wave.Error):
        return None


class ClosingManagerMixin:
    def _list_unhandled_super_chats_for_closing(
        self,
        session_id: str,
        *,
        batch_size: int = 100,
    ) -> list[dict[str, Any]]:
        batch_size = max(1, min(int(batch_size or 100), 500))
        offset = 0
        super_chats: list[dict[str, Any]] = []
        while True:
            batch = self.storage.list_super_chats(
                session_id,
                unhandled_only=True,
                limit=batch_size,
                offset=offset,
            )
            if not batch:
                break
            super_chats.extend(batch)
            if len(batch) < batch_size:
                break
            offset += len(batch)
        return super_chats

    def _closing_super_chat_credit_lines(self, super_chats: list[dict[str, Any]]) -> list[str]:
        lines: list[str] = []
        for event in super_chats:
            author = str(event.get("author_display_name") or "匿名觀眾").strip() or "匿名觀眾"
            amount = str(event.get("amount_display_string") or "").strip()
            amount_text = f"{amount} " if amount else ""
            if self._is_public_live_event_displayable(event):
                summary = self._single_line(self._event_safe_text(event))[:120]
                suffix = f"：{summary}" if summary else "。"
            else:
                suffix = "（內容不公開）。"
            lines.append(f"感謝 {author} 的 {amount_text}SC{suffix}")
        return lines

    async def _drain_live_session_before_closing(
        self,
        runtime: LiveRuntime,
        session: dict[str, Any],
        *,
        timeout_seconds: float = 180.0,
        before_ready_presentation_callback=None,
    ) -> dict[str, Any]:
        runtime.graceful_closing_requested = True
        runtime.accepting_audience_events = False
        runtime.stop_after_current_turn = True
        runtime.drain_started_at = datetime.now().isoformat()
        if runtime.audience_preprocess_wake:
            runtime.audience_preprocess_wake.set()

        def ready_prepared_items() -> list[dict[str, Any]]:
            finder = getattr(self, "_ready_prepared_items_for_session", None)
            if callable(finder):
                return [
                    item for item in finder(runtime.session_id)
                    if (
                        not self._is_final_closing_prefetch_item(item)
                        and not self._is_closing_super_chat_prefetch_item(item)
                    )
                ]
            return []

        def pending_presentation_items() -> list[dict[str, Any]]:
            presenting = self.storage.list_presentation_items(
                runtime.session_id,
                statuses={"presenting"},
                limit=500,
            )
            failed_finder = getattr(self.storage, "list_unacked_failed_presentation_items", None)
            if callable(failed_finder):
                unacked_failed = failed_finder(runtime.session_id, limit=500)
            else:
                unacked_failed = [
                    item
                    for item in self.storage.list_presentation_items(
                        runtime.session_id,
                        statuses={"failed"},
                        limit=500,
                    )
                    if not str(item.get("acked_at") or "").strip()
                ]
            return presenting + unacked_failed

        def closing_presentation_grace(item: dict[str, Any]) -> tuple[float, float | None]:
            audio_duration = _presentation_audio_duration_seconds(item)
            if audio_duration is not None:
                return (
                    min(
                        CLOSING_PRESENTATION_MAX_GRACE_SECONDS,
                        max(0.01, audio_duration + CLOSING_PRESENTATION_AUDIO_GRACE_MARGIN_SECONDS),
                    ),
                    audio_duration,
                )
            text_len = len(str(item.get("text") or ""))
            estimated = 4.0 + (text_len * CLOSING_PRESENTATION_SECONDS_PER_CHAR)
            return (
                min(
                    CLOSING_PRESENTATION_MAX_GRACE_SECONDS,
                    max(CLOSING_PRESENTATION_MIN_GRACE_SECONDS, estimated),
                ),
                None,
            )

        async def auto_ack_stale_presenting_items(presenting_items: list[dict[str, Any]]) -> int:
            now = datetime.now()
            acked_count = 0
            for item in presenting_items:
                if str(item.get("status") or "") != "presenting":
                    continue
                if str(item.get("acked_at") or "").strip():
                    continue
                item_id = str(item.get("item_id") or "")
                if not item_id:
                    continue
                presented_at = _parse_iso(item.get("presented_at"))
                if not presented_at:
                    continue
                elapsed = (now - presented_at).total_seconds()
                grace, audio_duration = closing_presentation_grace(item)
                if elapsed < grace:
                    continue
                metadata = {
                    "closing_grace_auto_ack": True,
                    "closing_grace_elapsed_seconds": round(elapsed, 3),
                    "closing_grace_seconds": round(grace, 3),
                    "closing_grace_source": "audio_duration" if audio_duration is not None else "text_estimate",
                }
                if audio_duration is not None:
                    metadata["closing_audio_duration_seconds"] = round(audio_duration, 3)
                self.storage.update_presentation_item(
                    item_id,
                    metadata=metadata,
                )
                updated = await self.ack_presentation_item(runtime.session_id, item_id)
                if updated:
                    acked_count += 1
            return acked_count

        def active_generation(active: dict[str, Any] | None, ready_items: list[dict[str, Any]]) -> dict[str, Any] | None:
            if not active:
                return None
            if (
                self._is_final_closing_prefetch_interaction(active)
                or self._is_closing_super_chat_prefetch_interaction(active)
            ):
                return None
            ready_job_ids = {
                str(item.get("interaction_job_id") or "")
                for item in ready_items
                if str(item.get("interaction_job_id") or "")
            }
            status = str(active.get("status") or "")
            job_id = str(active.get("job_id") or "")
            if status in {"prepared", "prefetched"} and job_id in ready_job_ids:
                return None
            return active

        def has_ready_prefetch(ready_items: list[dict[str, Any]]) -> bool:
            return any(
                str((item.get("metadata") or {}).get("source") or "") == "director_prefetch"
                for item in ready_items
            )

        def ready_item_block_reason(item: dict[str, Any]) -> str:
            source = str((item.get("metadata") or {}).get("source") or "")
            job_id = str(item.get("interaction_job_id") or "")
            if not job_id:
                return "closing_drain_missing_ready_interaction"
            interaction = self.storage.get_interaction(job_id)
            if not interaction:
                return "closing_drain_missing_ready_interaction"
            status = str(interaction.get("status") or "")
            expected_status = {
                "director_audience_prepare": "prepared",
                "director_prefetch": "prefetched",
            }.get(source)
            if expected_status and status != expected_status:
                return f"closing_drain_invalid_ready_interaction_status:{status or 'missing'}"
            return ""

        def classify_ready_items(ready_items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[tuple[dict[str, Any], str]]]:
            valid: list[dict[str, Any]] = []
            blocked: list[tuple[dict[str, Any], str]] = []
            for item in ready_items:
                reason = ready_item_block_reason(item)
                if reason:
                    blocked.append((item, reason))
                else:
                    valid.append(item)
            return valid, blocked

        def blocked_result(
            blocked_items: list[tuple[dict[str, Any], str]],
            *,
            active_blocker: dict[str, Any] | None,
            presenting: list[dict[str, Any]],
            ready_prepared_count: int,
        ) -> dict[str, Any]:
            reasons: list[str] = []
            for item, reason in blocked_items:
                if reason not in reasons:
                    reasons.append(reason)
                self.storage.update_presentation_item(
                    item["item_id"],
                    status="cancelled",
                    error=reason,
                )
            return {
                "status": "blocked",
                "active_job_id": str((active_blocker or {}).get("job_id") or ""),
                "presenting_count": len(presenting),
                "ready_prepared_count": ready_prepared_count,
                "blocked_ready_prepared_count": len(blocked_items),
                "blocked_ready_reasons": reasons,
            }

        async def wait_remaining(awaitable) -> bool:
            remaining = (deadline - datetime.now()).total_seconds()
            if remaining <= 0:
                awaitable.close()
                return False
            try:
                await asyncio.wait_for(awaitable, timeout=max(0.01, remaining))
            except asyncio.TimeoutError:
                return False
            return True

        def timeout_result() -> dict[str, Any]:
            active = self.storage.get_active_interaction(runtime.session_id)
            ready_prepared = ready_prepared_items()
            active_blocker = active_generation(active, ready_prepared)
            deferred_count = sum(
                1
                for item in ready_prepared
                if str(item.get("item_id") or "") in deferred_ready_item_ids
            )
            result = {
                "status": "timeout",
                "active_job_id": str((active_blocker or {}).get("job_id") or ""),
                "presenting_count": len(pending_presentation_items()),
                "ready_prepared_count": len(ready_prepared),
            }
            if deferred_count:
                result["deferred_ready_prepared_count"] = deferred_count
            return result

        async def present_ready_prepared(ready_items: list[dict[str, Any]]) -> None:
            if has_ready_prefetch(ready_items):
                await self._present_ready_prefetch_for_closing_drain(runtime, session, ready_items)
                return
            state = self.storage.get_director_state(runtime.session_id)
            await self._present_ready_audience_batch_after_turn(runtime, session, state)

        async def notify_before_ready_presentation(ready_items: list[dict[str, Any]]) -> None:
            if not callable(before_ready_presentation_callback):
                return
            await before_ready_presentation_callback(list(ready_items))

        deadline = datetime.now() + timedelta(seconds=max(0.01, timeout_seconds))
        deferred_ready_item_ids: set[str] = set()
        while True:
            active = self.storage.get_active_interaction(runtime.session_id)
            raw_ready_prepared = ready_prepared_items()
            ready_prepared, blocked_ready = classify_ready_items(raw_ready_prepared)
            active_blocker = active_generation(active, ready_prepared)
            presenting = pending_presentation_items()
            if presenting and await auto_ack_stale_presenting_items(presenting):
                active = self.storage.get_active_interaction(runtime.session_id)
                raw_ready_prepared = ready_prepared_items()
                ready_prepared, blocked_ready = classify_ready_items(raw_ready_prepared)
                active_blocker = active_generation(active, ready_prepared)
                presenting = pending_presentation_items()
            if blocked_ready and not ready_prepared and not active_blocker and not presenting:
                return blocked_result(
                    blocked_ready,
                    active_blocker=active_blocker,
                    presenting=presenting,
                    ready_prepared_count=len(raw_ready_prepared),
                )
            if not active_blocker and not presenting and not ready_prepared:
                return {
                    "status": "drained",
                    "active_job_id": "",
                    "presenting_count": 0,
                    "ready_prepared_count": 0,
                }
            if not active_blocker and ready_prepared and not presenting:
                deadline_reached = datetime.now() >= deadline
                ready_ids_before = {
                    str(item.get("item_id") or "")
                    for item in ready_prepared
                    if str(item.get("item_id") or "")
                }
                if ready_ids_before and ready_ids_before.issubset(deferred_ready_item_ids):
                    if deadline_reached:
                        return timeout_result()
                    remaining = (deadline - datetime.now()).total_seconds()
                    await asyncio.sleep(max(0.01, min(0.5, remaining)))
                    continue
                await notify_before_ready_presentation(ready_prepared)
                if has_ready_prefetch(ready_prepared):
                    await present_ready_prepared(ready_prepared)
                elif not await wait_remaining(present_ready_prepared(ready_prepared)):
                    return timeout_result()
                ready_ids_after = {
                    str(item.get("item_id") or "")
                    for item in ready_prepared_items()
                    if str(item.get("item_id") or "")
                }
                if ready_ids_after == ready_ids_before:
                    deferred_ready_item_ids.update(ready_ids_before)
                    continue
                continue
            if datetime.now() >= deadline:
                return timeout_result()
            remaining = (deadline - datetime.now()).total_seconds()
            await asyncio.sleep(max(0.01, min(0.5, remaining)))

    async def _present_ready_prefetch_for_closing_drain(
        self,
        runtime: LiveRuntime,
        session: dict[str, Any],
        ready_items: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        job_ids: list[str] = []
        for item in ready_items:
            item_source = str((item.get("metadata") or {}).get("source") or "")
            if item_source not in {"director_prefetch", "director_audience_prepare"}:
                continue
            job_id = str(item.get("interaction_job_id") or "")
            if job_id and job_id not in job_ids:
                job_ids.append(job_id)
        for job_id in job_ids:
            interaction = self.storage.get_interaction(job_id)
            if not interaction:
                continue
            interaction_source = str(interaction.get("source") or "")
            if interaction_source not in {"director_prefetch", "director_audience_prepare"}:
                continue
            expected_status = "prepared" if interaction_source == "director_audience_prepare" else "prefetched"
            if str(interaction.get("status") or "") != expected_status:
                continue
            prepared_results = self._prepared_results_for_interaction(
                runtime.session_id,
                interaction,
                require_complete=True,
            )
            if not prepared_results:
                continue
            if hasattr(self.storage, "update_interaction_if_status"):
                started = self.storage.update_interaction_if_status(
                    job_id,
                    expected_status,
                    status="presenting",
                )
            else:
                started = self.storage.update_interaction(job_id, status="presenting")
            if not started or str(started.get("status") or "") != "presenting":
                continue
            is_audience_prepare = interaction_source == "director_audience_prepare"
            presentation_source = "director_audience_gap" if is_audience_prepare else "director"
            await self._broadcast(runtime.session_id, {"type": "interaction_started", "interaction": started})
            await self.present_prepared_stream_results(
                runtime.session_id,
                prepared_results,
                source=presentation_source,
                interaction_job_id=job_id,
            )
            visible_results = self._visible_prepared_results(session, prepared_results)
            played_item_count = self._prepared_result_item_count(visible_results)
            marked_injected = 0
            if is_audience_prepare and played_item_count > 0:
                event_ids: list[int] = []
                for raw_event_id in started.get("event_ids") or []:
                    try:
                        event_id = int(raw_event_id)
                    except (TypeError, ValueError):
                        continue
                    if event_id > 0:
                        event_ids.append(event_id)
                marked_injected = self.storage.mark_events_injected(runtime.session_id, event_ids) if event_ids else 0
            interaction_metadata = {
                "prefetch_consumed": not is_audience_prepare,
                "audience_prepare_consumed": is_audience_prepare,
                "prefetch_consumed_during_closing_drain": True,
                "played_item_count": played_item_count,
                "marked_injected": marked_injected,
            }
            if hasattr(self.storage, "update_interaction_if_status"):
                completed = self.storage.update_interaction_if_status(
                    job_id,
                    "presenting",
                    status="completed",
                    reply_text=str(started.get("reply_text") or ""),
                    completed_at=datetime.now().isoformat(),
                    metadata=interaction_metadata,
                )
            else:
                completed = self.storage.update_interaction(
                    job_id,
                    status="completed",
                    reply_text=str(started.get("reply_text") or ""),
                    completed_at=datetime.now().isoformat(),
                    metadata=interaction_metadata,
                )
            await self._broadcast(runtime.session_id, {
                "type": "interaction_completed",
                "interaction": completed or started,
                "memoria_session_id": session.get("target_memoria_session_id") or "",
                "source": presentation_source,
            })
            return completed or started
        return None

    @staticmethod
    def _is_main_phase_event(event: dict[str, Any]) -> bool:
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        phase = str(metadata.get("phase") or metadata.get("live_phase") or "planned_content").strip()
        return phase in {"", "main", "planned_content"}

    async def _run_main_audience_sc_closing(
        self,
        runtime: LiveRuntime,
        session: dict[str, Any],
        *,
        reason: str,
    ) -> dict[str, Any]:
        if not session.get("auto_sc_thanks_on_finalize", True):
            return {
                "status": "skipped",
                "reason": "auto_sc_thanks_disabled",
                "super_chat_count": 0,
            }
        super_chats = [
            event
            for event in self._list_unhandled_super_chats_for_closing(runtime.session_id, batch_size=100)
            if self._is_main_phase_event(event)
        ]
        if not super_chats:
            return {
                "status": "skipped",
                "reason": "no_unhandled_main_super_chats",
                "super_chat_count": 0,
            }

        event_ids = [int(event["id"]) for event in super_chats]
        result = await self.inject_recent(
            runtime.session_id,
            event_ids=event_ids,
            max_events=len(event_ids),
            content="正式節目段落結束，請逐一感謝尚未處理的 Super Chat。",
            memoria_session_id=str(session.get("target_memoria_session_id") or ""),
            character_ids=session.get("character_ids", []),
            source="main_audience_closing",
            priority=320,
            claim_timeout_seconds=0.2,
        )
        injected_ids = [
            int(event_id)
            for event_id in (result.get("summary") or {}).get("event_ids", [])
        ]
        marked = self.storage.mark_super_chats_handled_in_closing(runtime.session_id, injected_ids)
        await self._broadcast(runtime.session_id, {
            "type": "main_audience_sc_closing_completed",
            "session_id": runtime.session_id,
            "marked": marked,
            "event_ids": injected_ids,
            "reason": str(reason or "")[:120],
            "interaction": result.get("interaction"),
        })
        return {
            "status": "completed",
            "super_chat_count": len(injected_ids),
            "candidate_super_chat_count": len(super_chats),
            "marked": marked,
            "result": result,
        }

    async def _finalize_for_duration(self, runtime: LiveRuntime, session: dict[str, Any]) -> None:
        async with runtime.closing_lock:
            session = self.storage.get_session(runtime.session_id) or session
            if not runtime.running or runtime.status in {"closing", "ended"} or session.get("status") == "ended":
                return
            started_at = datetime.now().isoformat()
            runtime.status = "closing"
            runtime.graceful_closing_requested = True
            runtime.accepting_audience_events = False
            runtime.stop_after_current_turn = True
            self.storage.update_session_fields(
                runtime.session_id,
                status="closing",
                auto_inject=False,
                auto_test_events_enabled=False,
            )
            director_state = self.storage.update_director_state(
                runtime.session_id,
                status="duration_closing",
                metadata={
                    "duration_closing_started_at": started_at,
                    "duration_closing_reason": "planned_duration_reached",
                },
            )
            await self._broadcast(runtime.session_id, {"type": "director_state", "director": director_state})
            await self._broadcast(
                runtime.session_id,
                {
                    "type": "status",
                    "status": "closing",
                    "message": "planned duration reached; starting graceful live closing",
                },
            )
            active_cleared = await self._wait_for_active_interaction_before_duration_closing(runtime)
            drain_timeout_seconds = 180.0
            if active_cleared:
                duration_closing_result = await self._run_duration_closing_turn(runtime, session)
            else:
                duration_closing_result = {
                    "status": "skipped",
                    "reason": "active_wait_timeout",
                }
                drain_timeout_seconds = max(
                    0.01,
                    float(DURATION_CLOSING_ACTIVE_INTERRUPT_TIMEOUT_SECONDS),
                )
            finalized = await self._finalize_live_session(
                runtime,
                self.storage.get_session(runtime.session_id) or session,
                finalized_by="duration_finalize",
                closing_message="planned duration reached; closing live session",
                ended_message="planned duration reached",
                metadata={"duration_closing": duration_closing_result},
                drain_timeout_seconds=drain_timeout_seconds,
            )
            try:
                await self._run_auto_finalize_archive_callback(
                    runtime.session_id,
                    finalized_by="duration_finalize",
                    finalized=finalized,
                )
            except Exception as exc:
                logger.warning("auto finalize archive failed session_id=%s error=%s", runtime.session_id, exc)

    async def _finalize_for_episode_plan_completed(
        self,
        runtime: LiveRuntime,
        session: dict[str, Any],
        planned_state: dict[str, Any],
    ) -> None:
        if not runtime.running or runtime.status in {"closing", "ended"} or session.get("status") == "ended":
            return
        completed_at = datetime.now().isoformat()
        logger.info(
            "episode plan completed; entering phase pipeline session_id=%s plan_id=%s completed_turn_count=%s",
            runtime.session_id,
            planned_state.get("plan_id") or session.get("episode_plan_id") or "",
            len(planned_state.get("completed_turn_ids") or []),
        )
        self.storage.update_director_state(
            runtime.session_id,
            status="episode_plan_completed",
            metadata={
                "episode_plan_completed_at": completed_at,
                "episode_plan_completed_state": planned_state,
            },
        )
        await self.finish_main_phase(
            runtime.session_id,
            reason="episode_plan_completed",
            enter_free_talk=True,
            topic_root=PROJECT_ROOT / "runtime" / "YouTubeBridge" / "freeTalkTopics",
        )

    async def finalize_session(self, session_id: str) -> dict[str, Any]:
        session = self.storage.get_session(session_id)
        if not session:
            raise ValueError("live session 不存在")
        runtime = self._runtimes.get(session_id)
        if runtime is None:
            runtime = LiveRuntime(
                session_id=session_id,
                running=False,
                status=str(session.get("status") or "stopped"),
            )
            self._runtimes[session_id] = runtime
        return await self._finalize_live_session(
            runtime,
            session,
            finalized_by="manual_finalize",
            closing_message="manual finalize requested; closing live session",
            ended_message="manual finalize requested",
        )

    async def _finalize_live_session(
        self,
        runtime: LiveRuntime,
        session: dict[str, Any],
        *,
        finalized_by: str,
        closing_message: str,
        ended_message: str,
        metadata: dict[str, Any] | None = None,
        drain_timeout_seconds: float = 180.0,
    ) -> dict[str, Any]:
        if runtime.status == "ended" and session.get("status") == "ended":
            director_metadata = self.storage.get_director_state(runtime.session_id).get("metadata") or {}
            return {
                **(self.storage.get_session(runtime.session_id) or session),
                "runtime_status": self.get_status(runtime.session_id),
                "closing_super_chat_thanks": director_metadata.get("closing_super_chat_thanks"),
                "closing_safety_resolution": director_metadata.get("closing_safety_resolution"),
            }
        runtime.status = "closing"
        runtime.graceful_closing_requested = True
        runtime.accepting_audience_events = False
        runtime.stop_after_current_turn = True
        self.storage.update_session_fields(
            runtime.session_id,
            status="closing",
            auto_inject=False,
            auto_test_events_enabled=False,
        )
        await self._cancel_runtime_task(runtime, "task")
        await self._cancel_runtime_task(runtime, "test_event_task")
        await self._broadcast(
            runtime.session_id,
            {
                "type": "status",
                "status": "closing",
                "message": closing_message,
            },
        )
        closing_result = None
        final_closing_prefetch: dict[str, Any] | None = None
        closing_super_chat_prefetch: dict[str, Any] | None = None
        no_sc_closing_result: dict[str, Any] | None = None
        pending_super_chats_before_drain: list[dict[str, Any]] = []
        pending_safety_before_drain = False
        if session.get("auto_sc_thanks_on_finalize", True):
            pending_super_chats_before_drain = self._list_unhandled_super_chats_for_closing(runtime.session_id, batch_size=500)
            pending_safety_before_drain = bool(self.storage.list_events_pending_safety(runtime.session_id, limit=1))
            if not pending_super_chats_before_drain:
                no_sc_closing_result = {
                    "status": "skipped",
                    "reason": "no_unhandled_super_chats",
                    "super_chat_count": 0,
                }
                if not self._has_active_closing_drain_audience_prepare(runtime.session_id):
                    drain_visible_target = self._final_closing_pending_drain_visible_target(runtime.session_id)
                    final_closing_prefetch = self._start_final_closing_prefetch(
                        runtime,
                        self.storage.get_session(runtime.session_id) or session,
                        closing_super_chat_thanks=no_sc_closing_result,
                        visible_reply_target=drain_visible_target,
                        reason="no_unhandled_super_chats_before_drain",
                    )
            elif (
                self._presentation_enabled(session)
                and not pending_safety_before_drain
                and not self._has_active_closing_drain_audience_prepare(runtime.session_id)
            ):
                drain_visible_target = (
                    self._final_closing_pending_drain_visible_target(runtime.session_id)
                    or self._latest_visible_message_for_session(runtime.session_id)
                )
                if drain_visible_target:
                    closing_super_chat_prefetch = self._start_closing_super_chat_prefetch(
                        runtime,
                        self.storage.get_session(runtime.session_id) or session,
                        pending_super_chats_before_drain,
                        visible_reply_target=drain_visible_target,
                        reason="pending_super_chats_before_drain",
                    )
        if final_closing_prefetch is not None:
            await asyncio.sleep(0)
        if closing_super_chat_prefetch is not None:
            await asyncio.sleep(0)

        async def refresh_final_closing_prefetch_for_drain_target(_ready_items: list[dict[str, Any]]) -> None:
            nonlocal final_closing_prefetch, closing_super_chat_prefetch
            visible_target = (
                self._final_closing_pending_drain_visible_target(runtime.session_id)
                or self._latest_visible_message_for_session(runtime.session_id)
            )
            target_signature = self._final_closing_visible_target_signature(visible_target)
            if not target_signature:
                return
            if no_sc_closing_result is not None:
                if (
                    isinstance(final_closing_prefetch, dict)
                    and final_closing_prefetch.get("visible_target_signature") == target_signature
                ):
                    return
                await self._cancel_final_closing_prefetch(
                    runtime,
                    final_closing_prefetch,
                    reason="final_closing_prefetch_superseded_by_drain_target",
                )
                final_closing_prefetch = self._start_final_closing_prefetch(
                    runtime,
                    self.storage.get_session(runtime.session_id) or session,
                    closing_super_chat_thanks=no_sc_closing_result,
                    visible_reply_target=visible_target,
                    reason="drain_target_changed",
                )
                if final_closing_prefetch is not None:
                    await asyncio.sleep(0)
                return
            if (
                pending_super_chats_before_drain
                and not pending_safety_before_drain
                and self._presentation_enabled(session)
            ):
                if (
                    isinstance(closing_super_chat_prefetch, dict)
                    and closing_super_chat_prefetch.get("visible_target_signature") == target_signature
                ):
                    return
                await self._cancel_closing_super_chat_prefetch(
                    runtime,
                    closing_super_chat_prefetch,
                    reason="closing_super_chat_prefetch_superseded_by_drain_target",
                )
                closing_super_chat_prefetch = self._start_closing_super_chat_prefetch(
                    runtime,
                    self.storage.get_session(runtime.session_id) or session,
                    pending_super_chats_before_drain,
                    visible_reply_target=visible_target,
                    reason="drain_target_changed",
                )
                if closing_super_chat_prefetch is not None:
                    await asyncio.sleep(0)

        drain_result = await self._drain_live_session_before_closing(
            runtime,
            session,
            timeout_seconds=drain_timeout_seconds,
            before_ready_presentation_callback=refresh_final_closing_prefetch_for_drain_target,
        )
        runtime.running = False
        await self._cancel_runtime_task(runtime, "inject_task")
        await self._cancel_runtime_task(runtime, "audience_preprocess_task")
        await self._cancel_runtime_task(runtime, "audience_gap_prepare_task")
        await self._cancel_runtime_task(runtime, "director_task")
        await self._cancel_runtime_task(runtime, "director_kickoff_task")
        safety_closing_result = await self._resolve_pending_safety_for_closing(runtime.session_id)
        if session.get("auto_sc_thanks_on_finalize", True):
            pending_super_chats = self._list_unhandled_super_chats_for_closing(runtime.session_id, batch_size=500)
            if not pending_super_chats:
                closing_result = {
                    "status": "skipped",
                    "reason": "no_unhandled_super_chats",
                    "super_chat_count": 0,
                }
            else:
                try:
                    if self._presentation_enabled(session):
                        async def start_final_closing_prefetch_after_sc(memoria_result: dict[str, Any]) -> None:
                            nonlocal final_closing_prefetch
                            visible_target = self._final_closing_visible_target_from_reply(
                                str((memoria_result or {}).get("reply") or ""),
                                source="closing_super_chat_thanks",
                            )
                            final_closing_prefetch = self._start_final_closing_prefetch(
                                runtime,
                                self.storage.get_session(runtime.session_id) or session,
                                closing_super_chat_thanks={
                                    "status": "completed",
                                    "super_chat_count": len(pending_super_chats),
                                },
                                visible_reply_target=visible_target,
                                reason="after_closing_super_chat_thanks_memoria",
                            )

                        closing_result = await self._consume_closing_super_chat_prefetch(
                            runtime,
                            self.storage.get_session(runtime.session_id) or session,
                            closing_super_chat_prefetch,
                            super_chats=pending_super_chats,
                            after_memoria_callback=start_final_closing_prefetch_after_sc,
                        )
                        if closing_result is None:
                            closing_result = await self.run_closing_super_chat_thanks(
                                runtime.session_id,
                                after_memoria_callback=start_final_closing_prefetch_after_sc,
                            )
                    else:
                        closing_result = await asyncio.wait_for(
                            self.run_closing_super_chat_thanks(runtime.session_id),
                            timeout=45,
                        )
                except asyncio.TimeoutError:
                    closing_result = await self._complete_closing_super_chat_thanks_fallback(
                        runtime.session_id,
                        reason="timeout",
                    )
                except Exception as exc:
                    logger.warning("closing super chat thanks failed session_id=%s error=%s", runtime.session_id, exc)
                    closing_result = {"status": "failed", "error": str(exc)[:500]}
        final_closing_result = await self._consume_final_closing_prefetch(
            runtime,
            self.storage.get_session(runtime.session_id) or session,
            final_closing_prefetch,
            closing_super_chat_thanks=closing_result,
        )
        if final_closing_result is None:
            final_closing_result = await self._run_final_closing_turn(
                runtime,
                self.storage.get_session(runtime.session_id) or session,
                closing_super_chat_thanks=closing_result,
            )
        finalized_at = datetime.now().isoformat()
        runtime.status = "ended"
        self.storage.finalize_incomplete_interactions(
            runtime.session_id,
            status="interrupted",
            reason="live_session_ended",
            metadata={"finalized_by": finalized_by},
        )
        self.storage.update_session_summary_state(
            runtime.session_id,
            summary_status=session.get("summary_status") or "pending",
            summary_error=session.get("summary_error", ""),
            finalized_at=finalized_at,
        )
        self.storage.update_session_fields(runtime.session_id, status="ended")
        director_state = self.storage.update_director_state(
            runtime.session_id,
            director_enabled=False,
            status="ended",
            consecutive_ai_turns=0,
            metadata={
                **(metadata or {}),
                "finalized_by": finalized_by,
                "graceful_drain": drain_result,
                "closing_super_chat_thanks": closing_result,
                "final_closing": final_closing_result,
                "closing_safety_resolution": safety_closing_result,
            },
        )
        closing_super_chat_status = closing_result.get("status") if isinstance(closing_result, dict) else "none"
        final_closing_status = final_closing_result.get("status") if isinstance(final_closing_result, dict) else "none"
        logger.info(
            "live session finalized session_id=%s finalized_by=%s status=ended finalized_at=%s closing_super_chat_status=%s final_closing_status=%s",
            runtime.session_id,
            finalized_by,
            finalized_at,
            closing_super_chat_status,
            final_closing_status,
        )
        await self._broadcast(runtime.session_id, {"type": "director_state", "director": director_state})
        await self._broadcast(
            runtime.session_id,
            {
                "type": "status",
                "status": "ended",
                "message": ended_message,
                "finalized_at": finalized_at,
                "closing_super_chat_thanks": closing_result,
                "closing_safety_resolution": safety_closing_result,
            },
        )
        return {
            **(self.storage.get_session(runtime.session_id) or session),
            "runtime_status": self.get_status(runtime.session_id),
            "closing_super_chat_thanks": closing_result,
            "closing_safety_resolution": safety_closing_result,
        }

    async def _wait_for_active_interaction_before_duration_closing(self, runtime: LiveRuntime) -> bool:
        last_job_id = ""
        wait_started_at = datetime.now()
        deadline = wait_started_at + timedelta(seconds=max(0.0, float(DURATION_CLOSING_ACTIVE_WAIT_TIMEOUT_SECONDS)))
        while runtime.running and runtime.status == "closing":
            active = self.storage.get_active_interaction(runtime.session_id)
            if not active:
                return True
            now = datetime.now()
            job_id = str(active.get("job_id") or "")
            if now >= deadline:
                director_state = self.storage.update_director_state(
                    runtime.session_id,
                    status="duration_closing_active_wait_timeout",
                    metadata={
                        "duration_closing_active_wait_timeout": True,
                        "duration_closing_active_wait_started_at": wait_started_at.isoformat(),
                        "duration_closing_active_wait_timed_out_at": now.isoformat(),
                        "duration_closing_active_wait_job_id": job_id,
                    },
                )
                await self._broadcast(runtime.session_id, {"type": "director_state", "director": director_state})
                return False
            if job_id != last_job_id:
                last_job_id = job_id
                director_state = self.storage.update_director_state(
                    runtime.session_id,
                    status="duration_closing_waiting_active",
                    metadata={
                        "duration_closing_waiting_job_id": job_id,
                        "duration_closing_waiting_since": datetime.now().isoformat(),
                        "duration_closing_waiting_deadline": deadline.isoformat(),
                    },
                )
                await self._broadcast(runtime.session_id, {"type": "director_state", "director": director_state})
            await asyncio.sleep(max(0.01, float(DURATION_CLOSING_ACTIVE_WAIT_POLL_SECONDS)))
        return False

    async def _run_duration_closing_turn(
        self,
        runtime: LiveRuntime,
        session: dict[str, Any],
    ) -> dict[str, Any]:
        if not session.get("target_memoria_session_id") or not session.get("character_ids"):
            return {"status": "skipped", "reason": "missing_memoria_session_or_characters"}
        state = self.storage.get_director_state(runtime.session_id)
        topic = (
            str(state.get("current_topic") or "").strip()
            or str(session.get("director_guidance") or "").strip()
            or str(session.get("display_name") or "本場直播").strip()
        )
        decision = {
            "action": "duration_closing",
            "reason": "預定直播時間已到，先讓角色自然收束整場直播，再進入最終結束流程。",
            "prompt": "",
            "current_topic": topic[:200],
        }
        try:
            result = await self._send_director_turn(session, state, decision)
            interaction = result.get("interaction") if isinstance(result, dict) else None
            return {
                "status": str((interaction or {}).get("status") or "completed"),
                "interaction": interaction,
            }
        except Exception as exc:
            logger.warning("duration closing turn failed session_id=%s error=%s", runtime.session_id, exc, exc_info=True)
            return {"status": "failed", "error": str(exc)[:500]}

    def _latest_visible_message_for_session(self, session_id: str) -> dict[str, Any] | None:
        latest: dict[str, Any] | None = None
        latest_rank: tuple[datetime, int, int] | None = None
        for interaction in self.storage.list_interactions(session_id, limit=500):
            metadata = interaction.get("metadata") if isinstance(interaction.get("metadata"), dict) else {}
            visible_messages = metadata.get("visible_messages")
            if not isinstance(visible_messages, list):
                continue
            interaction_time = _parse_iso(
                interaction.get("completed_at")
                or interaction.get("created_at")
                or interaction.get("started_at")
            ) or datetime.min
            interaction_id = int(interaction.get("id") or 0)
            for index, message in enumerate(visible_messages):
                if not isinstance(message, dict):
                    continue
                content = str(message.get("content") or "").strip()
                if not content:
                    continue
                timestamp = _parse_iso(message.get("timestamp") or message.get("created_at")) or interaction_time
                rank = (timestamp, interaction_id, index)
                if latest_rank is None or rank >= latest_rank:
                    latest = message
                    latest_rank = rank
        return latest

    @staticmethod
    def _is_final_closing_prefetch_interaction(interaction: dict[str, Any] | None) -> bool:
        if not isinstance(interaction, dict):
            return False
        if str(interaction.get("source") or "") != "director_prefetch":
            return False
        metadata = interaction.get("metadata") if isinstance(interaction.get("metadata"), dict) else {}
        decision = metadata.get("decision") if isinstance(metadata.get("decision"), dict) else {}
        return str(decision.get("action") or "") == "final_closing"

    @staticmethod
    def _is_closing_super_chat_prefetch_interaction(interaction: dict[str, Any] | None) -> bool:
        if not isinstance(interaction, dict):
            return False
        if str(interaction.get("source") or "") != "director_prefetch":
            return False
        metadata = interaction.get("metadata") if isinstance(interaction.get("metadata"), dict) else {}
        decision = metadata.get("decision") if isinstance(metadata.get("decision"), dict) else {}
        return str(decision.get("action") or "") == "closing_super_chat_thanks"

    def _is_final_closing_prefetch_item(self, item: dict[str, Any] | None) -> bool:
        if not isinstance(item, dict):
            return False
        job_id = str(item.get("interaction_job_id") or "")
        if not job_id:
            return False
        return self._is_final_closing_prefetch_interaction(self.storage.get_interaction(job_id))

    def _is_closing_super_chat_prefetch_item(self, item: dict[str, Any] | None) -> bool:
        if not isinstance(item, dict):
            return False
        job_id = str(item.get("interaction_job_id") or "")
        if not job_id:
            return False
        return self._is_closing_super_chat_prefetch_interaction(self.storage.get_interaction(job_id))

    def _has_active_closing_drain_audience_prepare(self, session_id: str) -> bool:
        for interaction in self.storage.list_interactions(session_id, limit=200):
            if not isinstance(interaction, dict):
                continue
            if self._is_final_closing_prefetch_interaction(interaction):
                continue
            if str(interaction.get("source") or "") != "director_audience_prepare":
                continue
            if str(interaction.get("status") or "") not in {
                "prepared",
                "completed",
                "interrupted",
                "discarded",
                "failed",
            }:
                return True
        return False

    def _final_closing_visible_target_signature(self, target: dict[str, Any] | None) -> str:
        if not isinstance(target, dict):
            return ""
        return self._single_line(target.get("content") or "")

    def _latest_visible_target_signature(self, session_id: str) -> str:
        return self._final_closing_visible_target_signature(
            self._latest_visible_message_for_session(session_id)
        )

    def _final_closing_pending_drain_visible_target(self, session_id: str) -> dict[str, Any] | None:
        items = self.storage.list_presentation_items(
            session_id,
            statuses={"presenting", "ready"},
            limit=500,
        )
        candidates: list[tuple[int, int, dict[str, Any]]] = []
        for item in items:
            if self._is_final_closing_prefetch_item(item) or self._is_closing_super_chat_prefetch_item(item):
                continue
            text = self._single_line(item.get("text") or "")
            if not text:
                continue
            status = str(item.get("status") or "")
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            source = str(metadata.get("source") or "")
            if status == "ready" and source not in {"director_prefetch", "director_audience_prepare"}:
                continue
            status_rank = 0 if status == "presenting" else 1
            candidates.append((status_rank, int(item.get("id") or 0), item))
        if not candidates:
            return None
        _, _, item = max(candidates, key=lambda entry: (entry[0], entry[1]))
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        return {
            "message_id": item.get("message_id") or item.get("item_id") or "closing_drain:latest",
            "role": "assistant",
            "content": self._single_line(item.get("text") or ""),
            "timestamp": item.get("presented_at") or item.get("created_at") or datetime.now().isoformat(),
            "character_id": item.get("character_id") or "",
            "character_name": item.get("character_name") or "上一位角色",
            "source": metadata.get("source") or "closing_drain",
        }

    @staticmethod
    def _final_closing_sc_state_signature(closing_super_chat_thanks: dict[str, Any] | None) -> str:
        if not isinstance(closing_super_chat_thanks, dict):
            return ""
        status = str(closing_super_chat_thanks.get("status") or "")
        reason = str(closing_super_chat_thanks.get("reason") or "")
        count = str(closing_super_chat_thanks.get("super_chat_count") or "")
        return "|".join([status, reason, count])

    def _final_closing_visible_target_from_reply(
        self,
        reply_text: str,
        *,
        source: str,
    ) -> dict[str, Any] | None:
        utterances = self._split_presentation_utterances(reply_text)
        content = utterances[-1] if utterances else self._single_line(reply_text)
        content = self._single_line(content)
        if not content:
            return None
        return {
            "message_id": f"{source}:latest",
            "role": "assistant",
            "content": content,
            "timestamp": datetime.now().isoformat(),
            "character_name": "SC 感謝" if source == "closing_super_chat_thanks" else "上一位角色",
            "source": source,
        }

    def _closing_super_chat_state_signature(self, super_chats: list[dict[str, Any]]) -> str:
        parts: list[str] = []
        for event in super_chats:
            parts.append(
                "|".join([
                    str(event.get("id") or ""),
                    str(event.get("safety_status") or ""),
                    str(event.get("safety_label") or ""),
                    self._single_line(self._event_safe_text(event))[:120],
                ])
            )
        return "\n".join(parts)

    def _build_closing_super_chat_thanks_decision(
        self,
        session_id: str,
        session: dict[str, Any],
        super_chats: list[dict[str, Any]],
        *,
        visible_reply_target: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], str, str]:
        credit_lines = self._closing_super_chat_credit_lines(super_chats)
        closing_instruction = (
            "請像片尾名單一樣逐一點名所有 SC，每則獨立一句，語氣接近「感謝 XXX 的 SC」。"
            "可以簡短帶過乾淨留言內容；不適合公開回覆的內容只感謝支持，不要重述原文。"
            "不可只挑高 tier、不可只挑部分留言、不可省略名單。"
        )
        state = self.storage.get_director_state(session_id)
        decision = {
            "action": "closing_super_chat_thanks",
            "reason": "直播收尾前感謝本場 Super Chat，並避免逐字重述可疑內容。",
            "prompt": (
                "直播即將收尾，請感謝本場 Super Chat 支持。\n"
                f"{closing_instruction}\n\n"
                "本場 SC 片尾名單：\n" + "\n".join(f"- {line}" for line in credit_lines)
            ),
            "current_topic": state.get("current_topic") or session.get("director_guidance") or "直播收尾",
        }
        target_signature = self._final_closing_visible_target_signature(visible_reply_target)
        if visible_reply_target:
            speaker = str(
                visible_reply_target.get("character_name")
                or visible_reply_target.get("role")
                or "上一位角色"
            ).strip()
            content = str(visible_reply_target.get("content") or "").strip()
            decision["visible_reply_target"] = visible_reply_target
            decision["prompt"] = (
                decision["prompt"]
                + "\n\n最後已顯示訊息："
                + f"{speaker}: {content}\n"
                + "開始感謝 SC 前，請先用一句話自然承接這句已顯示內容；"
                + "不要回到更早的留言重答，也不要開新話題。"
            )
        return decision, self._closing_super_chat_state_signature(super_chats), target_signature

    def _build_final_closing_decision(
        self,
        runtime: LiveRuntime,
        session: dict[str, Any],
        *,
        closing_super_chat_thanks: dict[str, Any] | None = None,
        visible_reply_target: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], str, str]:
        state = self.storage.get_director_state(runtime.session_id)
        topic = (
            str(state.get("current_topic") or "").strip()
            or str(session.get("director_guidance") or "").strip()
            or str(session.get("display_name") or "本場直播").strip()
        )
        sc_status = str((closing_super_chat_thanks or {}).get("status") or "")
        sc_note = "本場已完成 Super Chat 感謝，請不要再逐一重唸 SC。" if sc_status == "completed" else "本場沒有需要公開感謝的未處理 Super Chat。"
        decision = {
            "action": "final_closing",
            "reason": "直播收尾流程最後一步，做正式道別並結束直播。",
            "prompt": (
                f"請做本場最後完整收尾，主題是「{topic[:160]}」。"
                f"{sc_note}"
                "每位角色最多 1 句，總共 1 到 2 輪內完成。"
                "不要開新話題，不要再次要求觀眾回覆，不要重複前面已說過的收尾比喻。"
            ),
            "current_topic": topic[:200],
            "group_turn_limit": 2,
        }
        target = visible_reply_target or self._latest_visible_message_for_session(runtime.session_id)
        target_signature = self._final_closing_visible_target_signature(target)
        if target:
            speaker = str(
                target.get("character_name")
                or target.get("role")
                or "上一位角色"
            ).strip()
            content = str(target.get("content") or "").strip()
            decision["visible_reply_target"] = target
            decision["prompt"] = (
                decision["prompt"]
                + "\n\n最後已顯示訊息："
                + f"{speaker}: {content}\n"
                + "收尾回應必須優先承接這句已顯示內容；若這句在回答問題，請承認它已回答過；"
                + "若這句在提問或交接給下一位角色，請對這句完成自然收束。"
                + "不要回到更早的問題重答。"
            )
        return decision, target_signature, self._final_closing_sc_state_signature(closing_super_chat_thanks)

    def _start_final_closing_prefetch(
        self,
        runtime: LiveRuntime,
        session: dict[str, Any],
        *,
        closing_super_chat_thanks: dict[str, Any] | None = None,
        visible_reply_target: dict[str, Any] | None = None,
        reason: str,
    ) -> dict[str, Any] | None:
        if not self._presentation_enabled(session):
            return None
        if not session.get("target_memoria_session_id") or not session.get("character_ids"):
            return None
        state = self.storage.get_director_state(runtime.session_id)
        decision, target_signature, sc_signature = self._build_final_closing_decision(
            runtime,
            session,
            closing_super_chat_thanks=closing_super_chat_thanks,
            visible_reply_target=visible_reply_target,
        )
        decision["final_closing_prefetch"] = True
        task = asyncio.create_task(
            self._send_director_turn(
                session,
                state,
                decision,
                prefetch_only=True,
            )
        )
        return {
            "task": task,
            "visible_target_signature": target_signature,
            "sc_state_signature": sc_signature,
            "reason": reason,
        }

    def _start_closing_super_chat_prefetch(
        self,
        runtime: LiveRuntime,
        session: dict[str, Any],
        super_chats: list[dict[str, Any]],
        *,
        visible_reply_target: dict[str, Any] | None = None,
        reason: str,
    ) -> dict[str, Any] | None:
        if not self._presentation_enabled(session):
            return None
        if not session.get("target_memoria_session_id") or not session.get("character_ids"):
            return None
        if not super_chats:
            return None
        state = self.storage.get_director_state(runtime.session_id)
        decision, sc_signature, target_signature = self._build_closing_super_chat_thanks_decision(
            runtime.session_id,
            session,
            super_chats,
            visible_reply_target=visible_reply_target,
        )
        decision["closing_super_chat_prefetch"] = True
        task = asyncio.create_task(
            self._send_director_turn(
                session,
                state,
                decision,
                prefetch_only=True,
            )
        )
        return {
            "task": task,
            "sc_state_signature": sc_signature,
            "visible_target_signature": target_signature,
            "reason": reason,
        }

    def _final_closing_prefetch_payload_job_id(self, payload: dict[str, Any] | None) -> str:
        if not isinstance(payload, dict):
            return ""
        interaction = payload.get("interaction") if isinstance(payload.get("interaction"), dict) else {}
        return str(interaction.get("job_id") or "")

    async def _cancel_final_closing_prefetch(
        self,
        runtime: LiveRuntime,
        prefetch_context: dict[str, Any] | None,
        *,
        reason: str,
    ) -> None:
        if not isinstance(prefetch_context, dict):
            return
        task = prefetch_context.get("task")
        payload: dict[str, Any] | None = None
        job_id = self._prefetch_task_job_id(task) if task is not None else ""
        if task is not None and hasattr(task, "done") and task.done() and not task.cancelled():
            with contextlib.suppress(Exception):
                payload = task.result()
            job_id = job_id or self._final_closing_prefetch_payload_job_id(payload)
        if job_id:
            cancel_event = runtime.cancel_events.get(job_id)
            if cancel_event:
                cancel_event.set()
            self._cancel_prepared_items_for_interaction(runtime.session_id, job_id, reason)
            current = self.storage.get_interaction(job_id)
            if current and str(current.get("status") or "") in {
                "queued",
                "running",
                "presenting",
                "prefetching",
                "prefetched",
                "interrupt_requested",
            }:
                self.storage.update_interaction(
                    job_id,
                    status="interrupted",
                    reason=reason,
                    completed_at=datetime.now().isoformat(),
                    interrupted_at=datetime.now().isoformat(),
                    metadata={
                        "final_closing_prefetch_cancelled": True,
                        "final_closing_prefetch_cancel_reason": reason,
                    },
                )
        if task is not None and hasattr(task, "done") and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _cancel_closing_super_chat_prefetch(
        self,
        runtime: LiveRuntime,
        prefetch_context: dict[str, Any] | None,
        *,
        reason: str,
    ) -> None:
        if not isinstance(prefetch_context, dict):
            return
        task = prefetch_context.get("task")
        payload: dict[str, Any] | None = None
        job_id = self._prefetch_task_job_id(task) if task is not None else ""
        if task is not None and hasattr(task, "done") and task.done() and not task.cancelled():
            with contextlib.suppress(Exception):
                payload = task.result()
            job_id = job_id or self._final_closing_prefetch_payload_job_id(payload)
        if job_id:
            cancel_event = runtime.cancel_events.get(job_id)
            if cancel_event:
                cancel_event.set()
            self._cancel_prepared_items_for_interaction(runtime.session_id, job_id, reason)
            current = self.storage.get_interaction(job_id)
            if current and str(current.get("status") or "") in {
                "queued",
                "running",
                "presenting",
                "prefetching",
                "prefetched",
                "interrupt_requested",
            }:
                self.storage.update_interaction(
                    job_id,
                    status="interrupted",
                    reason=reason,
                    completed_at=datetime.now().isoformat(),
                    interrupted_at=datetime.now().isoformat(),
                    metadata={
                        "closing_super_chat_prefetch_cancelled": True,
                        "closing_super_chat_prefetch_cancel_reason": reason,
                    },
                )
        if task is not None and hasattr(task, "done") and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _consume_closing_super_chat_prefetch(
        self,
        runtime: LiveRuntime,
        session: dict[str, Any],
        prefetch_context: dict[str, Any] | None,
        *,
        super_chats: list[dict[str, Any]],
        after_memoria_callback=None,
    ) -> dict[str, Any] | None:
        if not isinstance(prefetch_context, dict):
            return None
        task = prefetch_context.get("task")
        if task is None or not hasattr(task, "done"):
            return None
        if not task.done():
            await self._cancel_closing_super_chat_prefetch(
                runtime,
                prefetch_context,
                reason="closing_super_chat_prefetch_not_ready",
            )
            return None
        if task.cancelled():
            return None
        exc = task.exception()
        if exc is not None:
            logger.warning(
                "closing super chat prefetch failed session_id=%s error=%s",
                runtime.session_id,
                exc,
                exc_info=True,
            )
            await self._cancel_closing_super_chat_prefetch(
                runtime,
                prefetch_context,
                reason="closing_super_chat_prefetch_failed",
            )
            return None
        prefetch = task.result()
        if not isinstance(prefetch, dict):
            return None
        if prefetch_context.get("sc_state_signature") != self._closing_super_chat_state_signature(super_chats):
            await self._cancel_closing_super_chat_prefetch(
                runtime,
                prefetch_context,
                reason="closing_super_chat_prefetch_stale_sc_state",
            )
            return None
        target_signature = str(prefetch_context.get("visible_target_signature") or "")
        if target_signature and target_signature != self._latest_visible_target_signature(runtime.session_id):
            await self._cancel_closing_super_chat_prefetch(
                runtime,
                prefetch_context,
                reason="closing_super_chat_prefetch_stale_visible_target",
            )
            return None
        interaction = prefetch.get("interaction") if isinstance(prefetch.get("interaction"), dict) else {}
        job_id = str(interaction.get("job_id") or "")
        current = self.storage.get_interaction(job_id) if job_id else None
        if not current or not self._is_closing_super_chat_prefetch_interaction(current):
            await self._cancel_closing_super_chat_prefetch(
                runtime,
                prefetch_context,
                reason="closing_super_chat_prefetch_invalid_interaction",
            )
            return None
        if str(current.get("status") or "") != "prefetched":
            await self._cancel_closing_super_chat_prefetch(
                runtime,
                prefetch_context,
                reason="closing_super_chat_prefetch_not_prefetched",
            )
            return None
        prepared_results = [
            prepared for prepared in prefetch.get("prepared_results") or []
            if isinstance(prepared, dict)
        ]
        if not prepared_results:
            prepared_results = self._prepared_results_for_interaction(
                runtime.session_id,
                current,
                require_complete=True,
            )
        if not prepared_results:
            await self._cancel_closing_super_chat_prefetch(
                runtime,
                prefetch_context,
                reason="closing_super_chat_prefetch_missing_prepared_items",
            )
            return None
        if hasattr(self.storage, "update_interaction_if_status"):
            started = self.storage.update_interaction_if_status(
                job_id,
                "prefetched",
                status="presenting",
            )
        else:
            started = self.storage.update_interaction(job_id, status="presenting")
        if not started or str(started.get("status") or "") != "presenting":
            await self._cancel_closing_super_chat_prefetch(
                runtime,
                prefetch_context,
                reason="closing_super_chat_prefetch_presenting_claim_failed",
            )
            return None
        await self._broadcast(runtime.session_id, {"type": "interaction_started", "interaction": started})

        callback_task = None
        result = prefetch.get("memoria_result") if isinstance(prefetch.get("memoria_result"), dict) else {}
        if after_memoria_callback:
            maybe_callback_result = after_memoria_callback(result)
            if asyncio.iscoroutine(maybe_callback_result):
                callback_task = asyncio.create_task(maybe_callback_result)
        await self.present_prepared_stream_results(
            runtime.session_id,
            prepared_results,
            source="director",
            interaction_job_id=job_id,
        )
        if callback_task is not None:
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await callback_task
        visible_results = self._visible_prepared_results(session, prepared_results)
        played_item_count = self._prepared_result_item_count(visible_results)
        if hasattr(self.storage, "update_interaction_if_status"):
            updated = self.storage.update_interaction_if_status(
                job_id,
                "presenting",
                status="completed",
                reply_text=str(result.get("reply") or started.get("reply_text") or ""),
                completed_at=datetime.now().isoformat(),
                metadata={
                    "closing_super_chat_prefetch_consumed": True,
                    "played_item_count": played_item_count,
                },
            )
        else:
            updated = self.storage.update_interaction(
                job_id,
                status="completed",
                reply_text=str(result.get("reply") or started.get("reply_text") or ""),
                completed_at=datetime.now().isoformat(),
                metadata={
                    "closing_super_chat_prefetch_consumed": True,
                    "played_item_count": played_item_count,
                },
            )
        if not updated or str(updated.get("status") or "") != "completed":
            await self._cancel_closing_super_chat_prefetch(
                runtime,
                prefetch_context,
                reason="closing_super_chat_prefetch_complete_failed",
            )
            return None
        await self._broadcast(runtime.session_id, {
            "type": "interaction_completed",
            "interaction": updated,
            "memoria_session_id": result.get("session_id") or session.get("target_memoria_session_id") or "",
            "source": "director",
        })
        await self._broadcast(runtime.session_id, {
            "type": "director_injected",
            "interaction": updated,
            "memoria_session_id": result.get("session_id") or session.get("target_memoria_session_id") or "",
        })
        marked = self.storage.mark_super_chats_handled_in_closing(
            runtime.session_id,
            [int(event["id"]) for event in super_chats],
        )
        await self._broadcast(runtime.session_id, {
            "type": "closing_super_chat_thanks_completed",
            "session_id": runtime.session_id,
            "marked": marked,
            "interaction": updated,
        })
        return {
            "status": "completed",
            "super_chat_count": len(super_chats),
            "marked": marked,
            "interaction": updated,
            "prefetch_consumed": True,
        }

    async def _consume_final_closing_prefetch(
        self,
        runtime: LiveRuntime,
        session: dict[str, Any],
        prefetch_context: dict[str, Any] | None,
        *,
        closing_super_chat_thanks: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not isinstance(prefetch_context, dict):
            return None
        task = prefetch_context.get("task")
        if task is None or not hasattr(task, "done"):
            return None
        if not task.done():
            await self._cancel_final_closing_prefetch(
                runtime,
                prefetch_context,
                reason="final_closing_prefetch_not_ready",
            )
            return None
        if task.cancelled():
            return None
        exc = task.exception()
        if exc is not None:
            logger.warning(
                "final closing prefetch failed session_id=%s error=%s",
                runtime.session_id,
                exc,
                exc_info=True,
            )
            await self._cancel_final_closing_prefetch(
                runtime,
                prefetch_context,
                reason="final_closing_prefetch_failed",
            )
            return None
        prefetch = task.result()
        if not isinstance(prefetch, dict):
            return None
        if prefetch_context.get("sc_state_signature") != self._final_closing_sc_state_signature(closing_super_chat_thanks):
            await self._cancel_final_closing_prefetch(
                runtime,
                prefetch_context,
                reason="final_closing_prefetch_stale_sc_state",
            )
            return None
        if prefetch_context.get("visible_target_signature") != self._latest_visible_target_signature(runtime.session_id):
            await self._cancel_final_closing_prefetch(
                runtime,
                prefetch_context,
                reason="final_closing_prefetch_stale_visible_target",
            )
            return None
        interaction = prefetch.get("interaction") if isinstance(prefetch.get("interaction"), dict) else {}
        job_id = str(interaction.get("job_id") or "")
        current = self.storage.get_interaction(job_id) if job_id else None
        if not current or not self._is_final_closing_prefetch_interaction(current):
            await self._cancel_final_closing_prefetch(
                runtime,
                prefetch_context,
                reason="final_closing_prefetch_invalid_interaction",
            )
            return None
        if str(current.get("status") or "") != "prefetched":
            await self._cancel_final_closing_prefetch(
                runtime,
                prefetch_context,
                reason="final_closing_prefetch_not_prefetched",
            )
            return None
        prepared_results = [
            prepared for prepared in prefetch.get("prepared_results") or []
            if isinstance(prepared, dict)
        ]
        if not prepared_results:
            prepared_results = self._prepared_results_for_interaction(
                runtime.session_id,
                current,
                require_complete=True,
            )
        if not prepared_results:
            await self._cancel_final_closing_prefetch(
                runtime,
                prefetch_context,
                reason="final_closing_prefetch_missing_prepared_items",
            )
            return None
        if hasattr(self.storage, "update_interaction_if_status"):
            started = self.storage.update_interaction_if_status(
                job_id,
                "prefetched",
                status="presenting",
            )
        else:
            started = self.storage.update_interaction(job_id, status="presenting")
        if not started or str(started.get("status") or "") != "presenting":
            await self._cancel_final_closing_prefetch(
                runtime,
                prefetch_context,
                reason="final_closing_prefetch_presenting_claim_failed",
            )
            return None
        await self._broadcast(runtime.session_id, {"type": "interaction_started", "interaction": started})
        await self.present_prepared_stream_results(
            runtime.session_id,
            prepared_results,
            source="director",
            interaction_job_id=job_id,
        )
        visible_results = self._visible_prepared_results(session, prepared_results)
        played_item_count = self._prepared_result_item_count(visible_results)
        result = prefetch.get("memoria_result") if isinstance(prefetch.get("memoria_result"), dict) else {}
        if hasattr(self.storage, "update_interaction_if_status"):
            updated = self.storage.update_interaction_if_status(
                job_id,
                "presenting",
                status="completed",
                reply_text=str(result.get("reply") or started.get("reply_text") or ""),
                completed_at=datetime.now().isoformat(),
                metadata={
                    "final_closing_prefetch_consumed": True,
                    "played_item_count": played_item_count,
                },
            )
        else:
            updated = self.storage.update_interaction(
                job_id,
                status="completed",
                reply_text=str(result.get("reply") or started.get("reply_text") or ""),
                completed_at=datetime.now().isoformat(),
                metadata={
                    "final_closing_prefetch_consumed": True,
                    "played_item_count": played_item_count,
                },
            )
        if not updated or str(updated.get("status") or "") != "completed":
            await self._cancel_final_closing_prefetch(
                runtime,
                prefetch_context,
                reason="final_closing_prefetch_complete_failed",
            )
            return None
        await self._broadcast(runtime.session_id, {
            "type": "interaction_completed",
            "interaction": updated,
            "memoria_session_id": result.get("session_id") or session.get("target_memoria_session_id") or "",
            "source": "director",
        })
        await self._broadcast(runtime.session_id, {
            "type": "director_injected",
            "interaction": updated,
            "memoria_session_id": result.get("session_id") or session.get("target_memoria_session_id") or "",
        })
        return {
            "status": str(updated.get("status") or "completed"),
            "interaction": updated,
            "prefetch_consumed": True,
        }

    async def _run_final_closing_turn(
        self,
        runtime: LiveRuntime,
        session: dict[str, Any],
        *,
        closing_super_chat_thanks: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not session.get("target_memoria_session_id") or not session.get("character_ids"):
            return {"status": "skipped", "reason": "missing_memoria_session_or_characters"}
        state = self.storage.get_director_state(runtime.session_id)
        decision, _target_signature, _sc_signature = self._build_final_closing_decision(
            runtime,
            session,
            closing_super_chat_thanks=closing_super_chat_thanks,
        )
        try:
            result = await self._send_director_turn(session, state, decision)
            interaction = result.get("interaction") if isinstance(result, dict) else None
            return {
                "status": str((interaction or {}).get("status") or "completed"),
                "interaction": interaction,
            }
        except Exception as exc:
            logger.warning("final closing turn failed session_id=%s error=%s", runtime.session_id, exc, exc_info=True)
            return {"status": "failed", "error": str(exc)[:500]}

    async def _complete_closing_super_chat_thanks_fallback(
        self,
        session_id: str,
        *,
        reason: str,
    ) -> dict[str, Any]:
        session = self.storage.get_session(session_id)
        if not session:
            return {"status": "failed", "error": "live session 不存在", "super_chat_count": 0}
        super_chats = self._list_unhandled_super_chats_for_closing(session_id, batch_size=500)
        if not super_chats:
            return {"status": "skipped", "reason": "no_unhandled_super_chats", "super_chat_count": 0}

        credit_lines = self._closing_super_chat_credit_lines(super_chats)
        reply_text = "感謝本場 Super Chat 支持。\n" + "\n".join(credit_lines)
        target_session_id = str(session.get("target_memoria_session_id") or "")
        message_result: dict[str, Any] = {}
        if target_session_id:
            client = self._memoria_client()
            add_system_event = getattr(client, "add_system_event", None)
            if callable(add_system_event):
                try:
                    message_result = await asyncio.to_thread(
                        add_system_event,
                        session_id=target_session_id,
                        content=reply_text,
                        debug_info={
                            "event_type": "youtube_live_closing_super_chat_fallback",
                            "source_session_id": session_id,
                            "reason": reason,
                            "super_chat_count": len(super_chats),
                        },
                    )
                except Exception as exc:
                    message_result = {"error": str(exc)[:300]}

        state = self.storage.get_director_state(session_id)
        decision = {
            "action": "closing_super_chat_thanks",
            "reason": f"直播收尾 fallback：{reason}",
            "current_topic": state.get("current_topic") or session.get("director_guidance") or "直播收尾",
        }
        interaction = self.storage.create_interaction({
            "session_id": session_id,
            "source": "director",
            "priority": 50,
            "status": "completed",
            "event_ids": [],
            "memoria_session_id": target_session_id,
            "character_ids": session.get("character_ids", []),
            "content": "直播即將收尾，感謝本場 Super Chat 支持。",
            "reply_text": reply_text,
            "completed_at": datetime.now().isoformat(),
            "metadata": {
                "decision": decision,
                "fallback": True,
                "fallback_reason": reason,
                "result_message_id": message_result.get("message_id"),
                "system_event_error": message_result.get("error", ""),
            },
        })
        marked = self.storage.mark_super_chats_handled_in_closing(
            session_id,
            [int(event["id"]) for event in super_chats],
        )
        await self._broadcast(session_id, {
            "type": "closing_super_chat_thanks_completed",
            "session_id": session_id,
            "marked": marked,
            "interaction": interaction,
            "fallback": True,
        })
        return {
            "status": "completed_by_timeout" if reason == "timeout" else "completed_by_fallback",
            "super_chat_count": len(super_chats),
            "marked": marked,
            "interaction": interaction,
            "message_result": message_result,
        }

    async def _resolve_pending_safety_for_closing(
        self,
        session_id: str,
        *,
        timeout_seconds: float = 20.0,
        per_batch_timeout_seconds: float = 75.0,
        batch_limit: int = 10,
    ) -> dict[str, Any]:
        """Resolve last-minute pending events before final closing interactions.

        Auto-generated test events can arrive close to the planned end time. The
        live page must fail closed instead of leaving ended sessions with pending
        safety state that may later leak into summaries or audits.
        """
        initial_pending = self.storage.list_events_pending_safety(session_id, limit=500)
        if not initial_pending:
            return {"status": "no_pending", "initial_pending_count": 0, "fallback_count": 0}

        classified_count = 0
        failed_count = 0
        batch_count = 0
        classify_error = ""
        loop = asyncio.get_running_loop()
        closing_batch_limit = min(max(1, int(batch_limit or 10)), SAFETY_CLASSIFIER_BATCH_LIMIT)
        per_batch_timeout = max(1.0, float(per_batch_timeout_seconds))
        total_timeout = max(1.0, float(timeout_seconds))
        deadline = loop.time() + total_timeout
        while self.storage.list_events_pending_safety(session_id, limit=1):
            remaining_seconds = deadline - loop.time()
            if remaining_seconds <= 0:
                classify_error = "timeout"
                break
            try:
                classify_result = await asyncio.wait_for(
                    self.classify_pending_events(
                        session_id,
                        limit=closing_batch_limit,
                    ),
                    timeout=max(0.1, min(per_batch_timeout, remaining_seconds)),
                )
            except asyncio.TimeoutError:
                classify_error = "timeout"
                break
            except Exception as exc:
                classify_error = str(exc)[:300]
                break
            batch_count += 1
            classified_count += int(classify_result.get("classified_count") or 0)
            failed_count += int(classify_result.get("failed_count") or 0)
            if classify_result.get("error"):
                classify_error = str(classify_result.get("error") or "")[:300]
                break

        fallback_events: list[dict[str, Any]] = []
        remaining = self.storage.list_events_pending_safety(session_id, limit=500)
        for event in remaining:
            updated = self.storage.update_event_safety(
                int(event["id"]),
                status="failed",
                label="unclassified",
                safe_message_text="安全檢查未完成，暫不顯示原始留言。",
                safety_summary="直播收尾前安全檢查未完成，已採用 fail-closed 處理。",
                reason=classify_error or "closing fail-closed",
                confidence=0.0,
            )
            if updated:
                public_event = self._public_event(updated)
                fallback_events.append(public_event)
                await self._broadcast(session_id, {"type": "safety_classified", "event": public_event})

        status = "completed"
        if classify_error:
            status = "fallback_after_error"
        elif fallback_events:
            status = "fallback_after_partial"
        return {
            "status": status,
            "initial_pending_count": len(initial_pending),
            "classified_count": classified_count,
            "failed_count": failed_count,
            "fallback_count": len(fallback_events),
            "batch_count": batch_count,
            "error": classify_error,
        }

    async def _interrupt_active_generation_for_closing(
        self,
        runtime: LiveRuntime,
        *,
        timeout_seconds: float = 1.0,
    ) -> list[dict[str, Any]]:
        active = self.storage.get_active_interaction(runtime.session_id)
        if not active:
            return []

        interrupted = self.storage.request_interrupt(
            runtime.session_id,
            reason="live_session_closing",
        )
        for interaction in interrupted:
            cancel_event = runtime.cancel_events.get(str(interaction.get("job_id") or ""))
            if cancel_event:
                cancel_event.set()
            await self._broadcast(
                runtime.session_id,
                {"type": "interaction_interrupted", "interaction": interaction},
            )

        poll_seconds = min(0.1, max(0.01, timeout_seconds))
        deadline = datetime.now() + timedelta(seconds=max(0.01, timeout_seconds))
        while datetime.now() < deadline:
            if not self.storage.get_active_interaction(runtime.session_id):
                return interrupted
            await asyncio.sleep(poll_seconds)

        finalized = self.storage.finalize_incomplete_interactions(
            runtime.session_id,
            status="interrupted",
            reason="live_session_closing",
            metadata={
                "finalized_by": "duration_closing",
                "forced_before_closing_thanks": True,
            },
        )
        for interaction in finalized:
            await self._broadcast(
                runtime.session_id,
                {"type": "interaction_interrupted", "interaction": interaction},
            )
        return finalized or interrupted

    async def run_closing_super_chat_thanks(
        self,
        session_id: str,
        *,
        after_memoria_callback=None,
    ) -> dict[str, Any]:
        session = self.storage.get_session(session_id)
        if not session:
            raise ValueError("live session 不存在")
        if not session.get("auto_sc_thanks_on_finalize", True):
            return {"status": "skipped", "reason": "auto_sc_thanks_disabled", "super_chat_count": 0}
        super_chats = self._list_unhandled_super_chats_for_closing(session_id, batch_size=100)
        if not super_chats:
            return {"status": "skipped", "reason": "no_unhandled_super_chats", "super_chat_count": 0}
        state = self.storage.get_director_state(session_id)
        decision, _sc_signature, _target_signature = self._build_closing_super_chat_thanks_decision(
            session_id,
            session,
            super_chats,
        )
        send_kwargs = {"after_memoria_callback": after_memoria_callback} if after_memoria_callback else {}
        result = await self._send_director_turn(
            session,
            state,
            decision,
            **send_kwargs,
        )
        callback_task = result.get("after_memoria_task") if isinstance(result, dict) else None
        if callback_task is not None:
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await callback_task
        marked = self.storage.mark_super_chats_handled_in_closing(
            session_id,
            [int(event["id"]) for event in super_chats],
        )
        await self._broadcast(session_id, {
            "type": "closing_super_chat_thanks_completed",
            "session_id": session_id,
            "marked": marked,
            "interaction": result.get("interaction"),
        })
        return {
            "status": "completed",
            "super_chat_count": len(super_chats),
            "marked": marked,
            "interaction": result.get("interaction"),
        }
