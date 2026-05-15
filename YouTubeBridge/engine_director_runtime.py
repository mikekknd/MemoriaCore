"""YouTubeBridge director runtime mixin。"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from bridge_runtime import LiveRuntime
from free_talk_topics import load_free_talk_topic_library
from memoria_client import GenerationInterrupted


logger = logging.getLogger("youtube_bridge")


def _director_timing_log(event: str, **fields: Any) -> None:
    payload = {
        "event": event,
        "at": datetime.now().isoformat(),
        **fields,
    }
    try:
        message = json.dumps(payload, ensure_ascii=False, default=str)
    except Exception:
        message = str(payload)
    logger.warning("DIRECTOR_TIMING %s", message)


class DirectorRuntimeManagerMixin:
    _POST_PLAN_FREE_TALK_ACTIONS = {"post_plan_free_talk_topic", "post_plan_free_talk_natural"}

    async def start_director(
        self,
        session_id: str,
        *,
        idle_seconds: int = 60,
        guidance: str = "",
        kickoff: bool = False,
    ) -> dict[str, Any]:
        session = self.storage.get_session(session_id)
        if not session:
            raise ValueError("live session 不存在")
        guidance = str(guidance or "").strip()
        guidance_changed = bool(guidance and guidance != str(session.get("director_guidance") or "").strip())
        if guidance:
            self.storage.update_session_fields(session_id, director_guidance=guidance[:2000])
        state_fields: dict[str, Any] = {
            "director_enabled": True,
            "idle_seconds": max(1, min(int(idle_seconds or 60), 3600)),
            "status": "running",
        }
        if guidance_changed:
            state_fields["consecutive_ai_turns"] = 0
            state_fields["metadata"] = {
                "guidance_updated_at": datetime.now().isoformat(),
                "guidance_reset_turn_limit": True,
            }
        state = self.storage.update_director_state(session_id, **state_fields)
        runtime = self._runtimes.setdefault(session_id, LiveRuntime(session_id=session_id))
        if runtime.running and (not runtime.director_task or runtime.director_task.done()):
            runtime.director_task = asyncio.create_task(self._director_loop(runtime))
        if kickoff and runtime.running and (not runtime.director_kickoff_task or runtime.director_kickoff_task.done()):
            runtime.director_kickoff_task = asyncio.create_task(self._director_kickoff(runtime))
        await self._broadcast(session_id, {"type": "director_state", "director": state})
        return state

    async def start_post_plan_free_talk_test(
        self,
        session_id: str,
        *,
        topic_root: Path,
        transition_reason: str = "operator_debug_start_free_talk",
    ) -> dict[str, Any]:
        session = self.storage.get_session(session_id)
        if not session:
            raise ValueError("live session 不存在")
        if str(session.get("status") or "") != "running":
            raise ValueError("live session 尚未開始")

        now = datetime.now()
        runtime = self._runtimes.setdefault(session_id, LiveRuntime(session_id=session_id, mode="test"))
        runtime.running = True
        runtime.status = "running"
        if str(runtime.mode or "") != "youtube":
            runtime.mode = "test"
        if str(session.get("status") or "") != "running" or not session.get("started_at"):
            update_fields: dict[str, Any] = {"status": "running"}
            if not session.get("started_at"):
                update_fields["started_at"] = now.isoformat()
            session = self.storage.update_session_fields(session_id, **update_fields) or session

        library = load_free_talk_topic_library(Path(topic_root))
        raw_selected_ids = session.get("post_plan_free_talk_topic_pack_ids")
        use_explicit_selection = isinstance(raw_selected_ids, list)
        selected_ids = [
            str(pack_id or "").strip()
            for pack_id in (raw_selected_ids if use_explicit_selection else [])
            if str(pack_id or "").strip()
        ]
        if use_explicit_selection:
            packs = [
                pack for pack in library.get("packs", [])
                if isinstance(pack, dict) and str(pack.get("pack_id") or "") in selected_ids
            ]
        else:
            packs = [pack for pack in library.get("packs", []) if isinstance(pack, dict)]
        topic_queue: list[dict[str, str]] = []
        for pack in packs:
            pack_id = str(pack.get("pack_id") or "").strip()
            for topic in pack.get("topics") or []:
                if not isinstance(topic, dict):
                    continue
                title = str(topic.get("title") or "").strip()
                prompt = str(topic.get("prompt") or "").strip()
                if title and prompt:
                    topic_queue.append({
                        "pack_id": pack_id,
                        "title": title[:120],
                        "prompt": prompt[:1000],
                    })
        runtime.post_plan_free_talk_topic_queue = topic_queue

        deadline = now + timedelta(minutes=max(0, int(session.get("post_plan_free_talk_minutes", 20) or 20)))
        free_talk_state = {
            "topic_count": len(topic_queue),
            "topic_cursor": 0,
            "selected_pack_ids": selected_ids,
            "selected_available_pack_ids": [str(pack.get("pack_id") or "") for pack in packs],
            "started_at": now.isoformat(),
            "deadline_at": deadline.isoformat(),
            "transition_reason": str(transition_reason or "")[:200],
            "last_tick_action": "",
            "last_tick_at": "",
            "last_topic_title": "",
        }
        director_state = self.storage.update_director_state(
            session_id,
            director_enabled=True,
            status="running",
            metadata={
                "phase": "post_plan_free_talk",
                "post_plan_free_talk": free_talk_state,
                "transition_reason": str(transition_reason or "")[:200],
            },
        )
        await self._broadcast(session_id, {"type": "director_state", "director": director_state})
        return await self._run_post_plan_free_talk_tick(runtime, session, director_state)

    async def _run_post_plan_free_talk_tick(
        self,
        runtime: LiveRuntime,
        session: dict[str, Any],
        director_state: dict[str, Any],
    ) -> dict[str, Any]:
        session_id = session["session_id"]
        if self.storage.get_active_interaction(session_id):
            next_state = self.storage.update_director_state(session_id, status="waiting_active_interaction")
            await self._broadcast(session_id, {"type": "director_state", "director": next_state})
            return {"phase": "post_plan_free_talk", "status": "wait", "director": next_state}

        metadata = dict(director_state.get("metadata") or {})
        free_talk_state = dict(metadata.get("post_plan_free_talk") or {})
        topic_queue = [
            topic for topic in getattr(runtime, "post_plan_free_talk_topic_queue", [])
            if isinstance(topic, dict)
        ]
        try:
            cursor = int(free_talk_state.get("topic_cursor", 0) or 0)
        except (TypeError, ValueError):
            cursor = 0
        active_topic = topic_queue[cursor] if 0 <= cursor < len(topic_queue) else None
        now = datetime.now().isoformat()
        if active_topic:
            title = str(active_topic.get("title") or "").strip()[:120]
            topic_prompt = str(active_topic.get("prompt") or "").strip()[:1000]
            public_prompt = "\n".join([
                f"雜談話題：{title}",
                topic_prompt,
                "請自然延伸這個雜談話題，讓角色彼此接話、補充或提出不同角度；不要提到幕後流程。",
            ])
            decision = {
                "action": "post_plan_free_talk_topic",
                "reason": "post plan free talk topic tick",
                "prompt": public_prompt,
                "current_topic": title,
                "group_turn_limit": self._post_plan_free_talk_group_turn_limit(session, "idle"),
            }
            status = "topic_chat"
            cursor += 1
            free_talk_state["last_topic_title"] = title
        else:
            public_prompt = (
                "自然雜談：請延續直播餘韻，讓角色彼此聊一段輕鬆近況或現場感想；"
                "不要提到幕後流程，也不要把問題丟回聊天室。"
            )
            decision = {
                "action": "post_plan_free_talk_natural",
                "reason": "post plan free talk natural fallback",
                "prompt": public_prompt,
                "current_topic": "自然雜談",
                "group_turn_limit": self._post_plan_free_talk_group_turn_limit(session, "idle"),
            }
            status = "natural_chat"

        free_talk_state["topic_cursor"] = cursor
        free_talk_state["last_tick_action"] = status
        free_talk_state["last_tick_at"] = now
        updated_state = self.storage.update_director_state(
            session_id,
            status="post_plan_free_talk",
            last_director_action_at=now,
            current_topic=str(decision.get("current_topic") or ""),
            metadata={
                **metadata,
                "phase": "post_plan_free_talk",
                "post_plan_free_talk": free_talk_state,
                "last_tick_action": status,
                "last_decision": self._public_decision(decision),
            },
        )
        await self._broadcast(session_id, {"type": "director_state", "director": updated_state})
        result = await self._send_director_turn(session, updated_state, decision)
        final_state = self.storage.update_director_state(
            session_id,
            status="running",
            metadata={
                **(updated_state.get("metadata") or {}),
                "last_result_job_id": result.get("interaction", {}).get("job_id", ""),
            },
        )
        await self._broadcast(session_id, {"type": "director_state", "director": final_state})
        return {
            "phase": "post_plan_free_talk",
            "status": status,
            "director": final_state,
            "interaction": result.get("interaction"),
        }

    async def stop_director(self, session_id: str) -> dict[str, Any]:
        runtime = self._runtimes.get(session_id)
        if runtime and runtime.director_task:
            runtime.director_task.cancel()
            try:
                await runtime.director_task
            except asyncio.CancelledError:
                pass
            runtime.director_task = None
        if runtime and runtime.director_kickoff_task:
            runtime.director_kickoff_task.cancel()
            try:
                await runtime.director_kickoff_task
            except asyncio.CancelledError:
                pass
            runtime.director_kickoff_task = None
        state = self.storage.update_director_state(session_id, director_enabled=False, status="stopped")
        await self._broadcast(session_id, {"type": "director_state", "director": state})
        return state

    async def _director_kickoff(self, runtime: LiveRuntime) -> None:
        try:
            session = self.storage.get_session(runtime.session_id)
            state = self.storage.get_director_state(runtime.session_id)
            if not runtime.running or not session or not state.get("director_enabled"):
                return
            if self.storage.get_active_interaction(runtime.session_id):
                next_state = self.storage.update_director_state(runtime.session_id, status="waiting_active_interaction")
                await self._broadcast(runtime.session_id, {"type": "director_state", "director": next_state})
                return
            episode_decision = self._episode_plan_next_decision(session, state)
            if episode_decision is not None:
                if not episode_decision:
                    _, planned_state = self._episode_plan_and_state(session, state)
                    completed = str(planned_state.get("plan_status") or "") == "completed"
                    if completed:
                        await self._finalize_for_episode_plan_completed(runtime, session, planned_state)
                        return
                    wait_decision = {
                        "action": "wait",
                        "reason": "episode plan has no runnable planned turn",
                        "episode_plan": {
                            "mode": "no_runnable_turn",
                            "planned_state": planned_state,
                        },
                    }
                    next_state = self.storage.update_director_state(
                        runtime.session_id,
                        status="running",
                        last_director_action_at=datetime.now().isoformat(),
                        metadata={
                            "last_decision": wait_decision,
                            "opening_decision": None,
                            "post_opening_decision": None,
                            "segment_state": {},
                        },
                    )
                    await self._broadcast(runtime.session_id, {"type": "director_state", "director": next_state})
                    return
                episode_state = self.storage.update_director_state(
                    runtime.session_id,
                    status="episode_planned_turn",
                    metadata={
                        "last_decision": episode_decision,
                        "opening_decision": None,
                        "post_opening_decision": None,
                        "segment_state": {},
                    },
                )
                await self._broadcast(runtime.session_id, {"type": "director_state", "director": episode_state})
                prefetch_callback = None
                if self._presentation_enabled(session):
                    async def prefetch_callback(memoria_result=None):
                        prefetch_session = self._session_with_memoria_result(session, memoria_result)
                        return await self._prefetch_next_episode_planned_turn(
                            runtime,
                            prefetch_session,
                            state,
                            episode_decision,
                        )
                send_kwargs = {"after_memoria_callback": prefetch_callback} if prefetch_callback else {}
                result = await self._send_director_turn(
                    session,
                    state,
                    episode_decision,
                    **send_kwargs,
                )
                episode_mode = str((episode_decision.get("episode_plan") or {}).get("mode") or "")
                next_state = self.storage.update_director_state(
                    runtime.session_id,
                    status="running",
                    last_director_action_at=datetime.now().isoformat(),
                    consecutive_ai_turns=(
                        0
                        if episode_mode == "planned_turn"
                        else int(state.get("consecutive_ai_turns", 0) or 0) + 1
                    ),
                    current_topic=str(episode_decision.get("current_topic") or state.get("current_topic") or ""),
                    metadata={
                        "last_decision": episode_decision,
                        "last_result_job_id": result.get("interaction", {}).get("job_id", ""),
                        "opening_decision": None,
                        "post_opening_decision": None,
                        "chat_batches_since_anchor": 0,
                        "segment_state": {},
                        **self._episode_metadata_after_turn(session, state, episode_decision),
                    },
                )
                await self._broadcast(runtime.session_id, {"type": "director_state", "director": next_state})
                prefetch_task = result.get("after_memoria_task")
                if prefetch_task:
                    await self._consume_prefetched_episode_chain(
                        runtime,
                        session,
                        prefetch_task,
                        next_state,
                        reset_opening_metadata=True,
                    )
                return
            decision = self._director_opening_decision(session, state)
            opening_state = self.storage.update_director_state(
                runtime.session_id,
                status="opening",
                metadata={"opening_decision": decision},
            )
            await self._broadcast(runtime.session_id, {"type": "director_state", "director": opening_state})
            result = await self._send_director_turn(session, state, decision)
            if self._presentation_enabled(session):
                next_state = self.storage.update_director_state(
                    runtime.session_id,
                    status="running",
                    last_director_action_at=datetime.now().isoformat(),
                    consecutive_ai_turns=int(state.get("consecutive_ai_turns", 0) or 0) + 1,
                    current_topic=str(decision.get("current_topic") or state.get("current_topic") or ""),
                    metadata={
                        "last_decision": decision,
                        "last_result_job_id": result.get("interaction", {}).get("job_id", ""),
                        "opening_decision": decision,
                        "post_opening_decision": None,
                        "chat_batches_since_anchor": 0,
                        "segment_state": self._segment_state_after_turn(
                            session,
                            state,
                            decision,
                            self._segment_topic_entry_for_session(session),
                        ),
                    },
                )
                await self._broadcast(runtime.session_id, {"type": "director_state", "director": next_state})
                return
            final_decision = decision
            final_result = result
            sent_turns = 1
            post_opening_decision: dict[str, Any] | None = None
            if runtime.running and self.storage.list_session_topic_pack_entries(runtime.session_id, limit=1):
                refreshed_session = self.storage.get_session(runtime.session_id) or session
                refreshed_state = self.storage.get_director_state(runtime.session_id)
                if not self.storage.get_active_interaction(runtime.session_id):
                    post_opening_decision = self._director_post_opening_topic_decision(
                        refreshed_session,
                        refreshed_state,
                    )
                    post_opening_state = self.storage.update_director_state(
                        runtime.session_id,
                        status="post_opening_topic_anchor",
                        metadata={"post_opening_decision": post_opening_decision},
                    )
                    await self._broadcast(
                        runtime.session_id,
                        {"type": "director_state", "director": post_opening_state},
                    )
                    final_result = await self._send_director_turn(
                        refreshed_session,
                        post_opening_state,
                        post_opening_decision,
                    )
                    final_decision = post_opening_decision
                    sent_turns += 1
            next_state = self.storage.update_director_state(
                runtime.session_id,
                status="running",
                last_director_action_at=datetime.now().isoformat(),
                consecutive_ai_turns=int(state.get("consecutive_ai_turns", 0) or 0) + sent_turns,
                current_topic=str(final_decision.get("current_topic") or state.get("current_topic") or ""),
                metadata={
                    "last_decision": final_decision,
                    "last_result_job_id": final_result.get("interaction", {}).get("job_id", ""),
                    "opening_decision": decision,
                    "post_opening_decision": post_opening_decision,
                    "chat_batches_since_anchor": 0,
                    "segment_state": self._segment_state_after_turn(
                        session,
                        state,
                        final_decision,
                        self._segment_topic_entry_for_session(session),
                    ),
                },
            )
            await self._broadcast(runtime.session_id, {"type": "director_state", "director": next_state})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("YouTube director kickoff error session_id=%s error=%s", runtime.session_id, exc, exc_info=True)
            state = self.storage.update_director_state(runtime.session_id, status="error", metadata={"last_error": str(exc)})
            await self._broadcast(runtime.session_id, {"type": "director_state", "director": state})
            await self._broadcast(runtime.session_id, {"type": "director_error", "message": str(exc)})

    async def _director_loop(self, runtime: LiveRuntime) -> None:
        while runtime.running:
            try:
                state = self.storage.get_director_state(runtime.session_id)
                if not state.get("director_enabled"):
                    return
                idle_seconds = max(1, min(int(state.get("idle_seconds", 60) or 60), 3600))
                session = self.storage.get_session(runtime.session_id)
                if not session:
                    return
                if runtime.status == "closing" or session.get("status") == "closing":
                    _director_timing_log(
                        "loop_blocked_closing",
                        session_id=runtime.session_id,
                        runtime_status=runtime.status,
                        session_status=session.get("status"),
                    )
                    await asyncio.sleep(1.0)
                    continue
                if self._duration_reached(session):
                    _director_timing_log("loop_duration_reached", session_id=runtime.session_id)
                    await self._finalize_for_duration(runtime, session)
                    return
                if runtime.director_prefetch_in_flight > 0:
                    _director_timing_log(
                        "loop_blocked_prefetch_in_flight",
                        session_id=runtime.session_id,
                        prefetch_in_flight=runtime.director_prefetch_in_flight,
                    )
                    next_state = self.storage.update_director_state(
                        runtime.session_id,
                        status="waiting_prefetch",
                    )
                    await self._broadcast(runtime.session_id, {"type": "director_state", "director": next_state})
                    await asyncio.sleep(1.0)
                    continue
                active_interaction = self.storage.get_active_interaction(runtime.session_id)
                if active_interaction:
                    _director_timing_log(
                        "loop_blocked_active_interaction",
                        session_id=runtime.session_id,
                        job_id=active_interaction.get("job_id"),
                        status=active_interaction.get("status"),
                        source=active_interaction.get("source"),
                    )
                    next_state = self.storage.update_director_state(
                        runtime.session_id,
                        status="waiting_active_interaction",
                    )
                    await self._broadcast(runtime.session_id, {"type": "director_state", "director": next_state})
                    await asyncio.sleep(1.0)
                    continue
                episode_decision = self._episode_plan_next_decision(session, state)
                has_episode_decision = isinstance(episode_decision, dict) and bool(episode_decision)
                pending = [
                    event for event in self.storage.list_events(runtime.session_id, limit=5, uninjected_only=True)
                    if self._should_block_director_for_pending_inject(event)
                ]
                if pending and not has_episode_decision:
                    latest = max(int(event["id"]) for event in pending)
                    _director_timing_log(
                        "loop_blocked_pending_events",
                        session_id=runtime.session_id,
                        pending_count=len(pending),
                        latest_event_id=latest,
                    )
                    next_state = self.storage.update_director_state(
                        runtime.session_id,
                        last_seen_event_id=latest,
                        status="pending_chat_seen",
                    )
                    await self._broadcast(runtime.session_id, {"type": "director_state", "director": next_state})
                    await asyncio.sleep(1.0)
                    continue
                if has_episode_decision:
                    episode_payload = (
                        episode_decision.get("episode_plan")
                        if isinstance(episode_decision.get("episode_plan"), dict)
                        else {}
                    )
                    turn_contract = (
                        episode_payload.get("turn_contract")
                        if isinstance(episode_payload.get("turn_contract"), dict)
                        else {}
                    )
                    _director_timing_log(
                        "loop_episode_decision_ready",
                        session_id=runtime.session_id,
                        mode=episode_payload.get("mode"),
                        action=episode_decision.get("action"),
                        turn_id=turn_contract.get("turn_id"),
                    )
                    backlog_snapshot = (
                        episode_payload.get("backlog_snapshot")
                        if isinstance(episode_payload.get("backlog_snapshot"), dict)
                        else {}
                    )
                    if backlog_snapshot:
                        _director_timing_log(
                            "audience_backpressure_snapshot",
                            session_id=runtime.session_id,
                            mode=episode_payload.get("mode"),
                            total_count=backlog_snapshot.get("total_count"),
                            normal_count=backlog_snapshot.get("normal_count"),
                            super_chat_count=backlog_snapshot.get("super_chat_count"),
                            selected_count=backlog_snapshot.get("selected_count"),
                            deferred_event_count=backlog_snapshot.get("deferred_event_count"),
                            defer_reason=backlog_snapshot.get("defer_reason"),
                        )
                        if episode_payload.get("mode") == "audience_interrupt":
                            _director_timing_log(
                                "audience_batch_selected",
                                session_id=runtime.session_id,
                                event_type=episode_payload.get("event_type"),
                                selected_count=backlog_snapshot.get("selected_count"),
                                deferred_event_count=backlog_snapshot.get("deferred_event_count"),
                            )
                            if (
                                episode_payload.get("event_type") == "super_chat"
                                and int(backlog_snapshot.get("deferred_event_count") or 0) > 0
                            ):
                                _director_timing_log(
                                    "sc_burst_coalesced",
                                    session_id=runtime.session_id,
                                    selected_count=backlog_snapshot.get("selected_count"),
                                    deferred_event_count=backlog_snapshot.get("deferred_event_count"),
                                )
                        elif int(backlog_snapshot.get("deferred_event_count") or 0) > 0:
                            _director_timing_log(
                                "audience_batch_deferred",
                                session_id=runtime.session_id,
                                deferred_event_count=backlog_snapshot.get("deferred_event_count"),
                                defer_reason=backlog_snapshot.get("defer_reason"),
                            )
                    if pending:
                        _director_timing_log(
                            "director_prioritized_planned_turn",
                            session_id=runtime.session_id,
                            mode=episode_payload.get("mode"),
                            pending_count=len(pending),
                            latest_event_id=max(int(event["id"]) for event in pending),
                            deferred_event_count=backlog_snapshot.get("deferred_event_count"),
                            defer_reason=backlog_snapshot.get("defer_reason"),
                        )
                if episode_decision is not None and not has_episode_decision:
                    _, planned_state = self._episode_plan_and_state(session, state)
                    completed = str(planned_state.get("plan_status") or "") == "completed"
                    if completed:
                        _director_timing_log("loop_episode_plan_completed", session_id=runtime.session_id)
                        await self._finalize_for_episode_plan_completed(runtime, session, planned_state)
                        return
                    wait_decision = {
                        "action": "wait",
                        "reason": "episode plan has no runnable planned turn",
                        "episode_plan": {
                            "mode": "no_runnable_turn",
                            "planned_state": planned_state,
                        },
                    }
                    next_state = self.storage.update_director_state(
                        runtime.session_id,
                        status="episode_plan_wait",
                        metadata={"last_decision": wait_decision},
                    )
                    _director_timing_log(
                        "loop_episode_plan_wait",
                        session_id=runtime.session_id,
                        plan_status=planned_state.get("plan_status"),
                        current_turn_id=planned_state.get("current_turn_id"),
                    )
                    await self._broadcast(runtime.session_id, {"type": "director_state", "director": next_state})
                    await asyncio.sleep(1.0)
                    continue
                if not has_episode_decision and self._director_should_pause_for_turn_limit(state, idle_seconds, session):
                    update_fields = {"status": "turn_limit_wait"}
                    if not state.get("last_director_action_at"):
                        update_fields["last_director_action_at"] = datetime.now().isoformat()
                    _director_timing_log(
                        "loop_turn_limit_wait",
                        session_id=runtime.session_id,
                        consecutive_ai_turns=state.get("consecutive_ai_turns"),
                        idle_seconds=idle_seconds,
                    )
                    next_state = self.storage.update_director_state(runtime.session_id, **update_fields)
                    await self._broadcast(runtime.session_id, {"type": "director_state", "director": next_state})
                    await asyncio.sleep(1.0)
                    continue
                if not has_episode_decision and self._director_topic_turn_limit_reached(session, state):
                    state = self.storage.update_director_state(
                        runtime.session_id,
                        status="turn_limit_released",
                        consecutive_ai_turns=0,
                    )
                    _director_timing_log("loop_turn_limit_released", session_id=runtime.session_id)
                    await self._broadcast(runtime.session_id, {"type": "director_state", "director": state})

                last_action_at = self._parse_iso_datetime(state.get("last_director_action_at"))
                if last_action_at:
                    if has_episode_decision:
                        delay_info = self._episode_plan_director_delay_info(
                            session,
                            state,
                            episode_decision,
                            idle_seconds,
                        )
                        delay_seconds = int(delay_info.get("delay_seconds") or 0)
                    else:
                        delay_info = {
                            "delay_seconds": idle_seconds,
                            "reason": "legacy_idle",
                            "label": "導播 idle",
                        }
                        delay_seconds = idle_seconds
                    elapsed_since_last_action = (datetime.now() - last_action_at).total_seconds()
                    remaining_seconds = delay_seconds - elapsed_since_last_action
                    if remaining_seconds > 0:
                        _director_timing_log(
                            "loop_delay_wait",
                            session_id=runtime.session_id,
                            delay_seconds=delay_seconds,
                            elapsed_since_last_action=round(elapsed_since_last_action, 3),
                            remaining_seconds=round(remaining_seconds, 3),
                            delay_reason=delay_info.get("reason"),
                            last_action_at=last_action_at.isoformat(),
                        )
                        await asyncio.sleep(min(1.0, max(0.2, remaining_seconds)))
                        continue
                    _director_timing_log(
                        "loop_delay_ready",
                        session_id=runtime.session_id,
                        delay_seconds=delay_seconds,
                        elapsed_since_last_action=round(elapsed_since_last_action, 3),
                        delay_reason=delay_info.get("reason"),
                        last_action_at=last_action_at.isoformat(),
                    )
                elif runtime.director_kickoff_task and not runtime.director_kickoff_task.done():
                    _director_timing_log("loop_waiting_kickoff_task", session_id=runtime.session_id)
                    await asyncio.sleep(1.0)
                    continue

                decision = episode_decision
                if decision is None:
                    decision_started = time.perf_counter()
                    _director_timing_log("loop_legacy_director_decision_start", session_id=runtime.session_id)
                    decision = await asyncio.to_thread(self._director_decision, session, state)
                    _director_timing_log(
                        "loop_legacy_director_decision_done",
                        session_id=runtime.session_id,
                        duration_ms=round((time.perf_counter() - decision_started) * 1000, 1),
                        action=decision.get("action"),
                    )
                action = str(decision.get("action") or "wait").strip()
                if action == "closing_super_chat_thanks":
                    decision = self._director_idle_continue_decision(session, state)
                    decision["reason"] = (
                        "一般導播決策不得提前進入 Super Chat 收尾；"
                        "SC 感謝只允許在直播時間到達後由 finalize 流程執行。"
                    )
                    action = str(decision.get("action") or "continue_topic").strip()
                if self._director_decision_is_early_live_closing(decision):
                    decision = self._director_idle_continue_decision(session, state)
                    decision["reason"] = (
                        "預定直播時間尚未到達，阻止導播因時間進度提前 recap/close；"
                        "正式收尾只由 duration finalize 流程執行。"
                    )
                    action = str(decision.get("action") or "continue_topic").strip()
                chat_batches = int((state.get("metadata") or {}).get("chat_batches_since_anchor", 0) or 0)
                max_chat_batches = max(1, int(session.get("director_max_chat_batches_before_anchor", 2) or 2))
                if chat_batches >= max_chat_batches and action in {"wait", "reply_chat_batch", "reply_super_chat_batch", "defer_offtopic"}:
                    decision = self._director_anchor_decision(session, state)
                    action = str(decision.get("action") or "anchor_to_topic").strip()
                if action == "wait" and self._director_should_force_guidance_turn(session, state):
                    decision = self._director_guidance_transition_decision(session, state)
                    action = str(decision.get("action") or "transition_topic").strip()
                if action == "wait" and self._director_should_force_idle_turn(state, session):
                    decision = self._director_idle_continue_decision(session, state)
                    action = str(decision.get("action") or "continue_topic").strip()
                if action == "wait":
                    _director_timing_log(
                        "loop_decision_wait",
                        session_id=runtime.session_id,
                        reason=decision.get("reason"),
                    )
                    next_state = self.storage.update_director_state(
                        runtime.session_id,
                        status="waiting",
                        last_director_action_at=datetime.now().isoformat(),
                        metadata={"last_decision": decision},
                    )
                    await self._broadcast(runtime.session_id, {"type": "director_state", "director": next_state})
                    continue
                send_started = time.perf_counter()
                episode_payload = decision.get("episode_plan") if isinstance(decision.get("episode_plan"), dict) else {}
                turn_contract = episode_payload.get("turn_contract") if isinstance(episode_payload.get("turn_contract"), dict) else {}
                _director_timing_log(
                    "loop_send_start",
                    session_id=runtime.session_id,
                    action=action,
                    episode_mode=episode_payload.get("mode"),
                    turn_id=turn_contract.get("turn_id"),
                )
                prefetch_callback = None
                if self._presentation_enabled(session):
                    async def prefetch_callback(memoria_result=None):
                        prefetch_session = self._session_with_memoria_result(session, memoria_result)
                        return await self._prefetch_next_episode_planned_turn(
                            runtime,
                            prefetch_session,
                            state,
                            decision,
                        )
                send_kwargs = {"after_memoria_callback": prefetch_callback} if prefetch_callback else {}
                result = await self._send_director_turn(
                    session,
                    state,
                    decision,
                    **send_kwargs,
                )
                _director_timing_log(
                    "loop_send_done",
                    session_id=runtime.session_id,
                    duration_ms=round((time.perf_counter() - send_started) * 1000, 1),
                    job_id=result.get("interaction", {}).get("job_id"),
                    interaction_status=result.get("interaction", {}).get("status"),
                )
                episode_mode = str((decision.get("episode_plan") or {}).get("mode") or "")
                next_count = (
                    0
                    if episode_mode == "planned_turn"
                    else int(state.get("consecutive_ai_turns", 0) or 0) + 1
                )
                next_state = self.storage.update_director_state(
                    runtime.session_id,
                    status="running",
                    last_director_action_at=datetime.now().isoformat(),
                    consecutive_ai_turns=next_count,
                    current_topic=str(decision.get("current_topic") or state.get("current_topic") or ""),
                    metadata={
                        "last_decision": decision,
                        "last_result_job_id": result.get("interaction", {}).get("job_id", ""),
                        "chat_batches_since_anchor": 0,
                        "segment_state": self._segment_state_after_turn(
                            session,
                            state,
                            decision,
                            self._segment_topic_entry_for_session(session),
                        ),
                        **self._episode_metadata_after_turn(session, state, decision),
                    },
                )
                await self._broadcast(runtime.session_id, {"type": "director_state", "director": next_state})
                prefetch_task = result.get("after_memoria_task")
                if prefetch_task:
                    next_state = await self._consume_prefetched_episode_chain(
                        runtime,
                        session,
                        prefetch_task,
                        next_state,
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("YouTube director error session_id=%s error=%s", runtime.session_id, exc, exc_info=True)
                state = self.storage.update_director_state(runtime.session_id, status="error", metadata={"last_error": str(exc)})
                await self._broadcast(runtime.session_id, {"type": "director_state", "director": state})
                await self._broadcast(runtime.session_id, {"type": "director_error", "message": str(exc)})
                await asyncio.sleep(15)

    def _pending_director_blocking_events(self, session_id: str) -> list[dict[str, Any]]:
        return [
            event for event in self.storage.list_events(session_id, limit=5, uninjected_only=True)
            if self._should_block_director_for_pending_inject(event)
        ]

    def _project_state_after_episode_decision(
        self,
        session: dict[str, Any],
        state: dict[str, Any],
        decision: dict[str, Any],
    ) -> dict[str, Any]:
        projected = dict(state)
        metadata = dict(state.get("metadata") if isinstance(state.get("metadata"), dict) else {})
        metadata.update(self._episode_metadata_after_turn(session, state, decision))
        metadata["last_decision"] = decision
        projected["metadata"] = metadata
        projected["current_topic"] = str(decision.get("current_topic") or state.get("current_topic") or "")
        return projected

    @staticmethod
    def _session_with_memoria_result(
        session: dict[str, Any],
        memoria_result: dict[str, Any] | None,
    ) -> dict[str, Any]:
        updated = dict(session)
        if isinstance(memoria_result, dict):
            result_session_id = str(memoria_result.get("session_id") or "")
            if result_session_id:
                updated["target_memoria_session_id"] = result_session_id
        return updated

    async def _prefetch_next_episode_planned_turn(
        self,
        runtime: LiveRuntime,
        session: dict[str, Any],
        state: dict[str, Any],
        decision: dict[str, Any],
    ) -> dict[str, Any] | None:
        payload = decision.get("episode_plan") if isinstance(decision.get("episode_plan"), dict) else {}
        if str(payload.get("mode") or "") != "planned_turn":
            return None
        if self._pending_director_blocking_events(runtime.session_id):
            return None
        projected_state = self._project_state_after_episode_decision(session, state, decision)
        next_decision = self._episode_plan_next_decision(session, projected_state)
        if not isinstance(next_decision, dict) or not next_decision:
            return None
        next_payload = next_decision.get("episode_plan") if isinstance(next_decision.get("episode_plan"), dict) else {}
        if str(next_payload.get("mode") or "") != "planned_turn":
            return None
        return await self._send_director_turn(
            session,
            projected_state,
            next_decision,
            prefetch_only=True,
        )

    async def _consume_prefetched_episode_chain(
        self,
        runtime: LiveRuntime,
        session: dict[str, Any],
        prefetch_task,
        current_state: dict[str, Any],
        *,
        reset_opening_metadata: bool = False,
    ) -> dict[str, Any]:
        next_state = current_state
        consumed_count = 0
        while prefetch_task:
            prefetched = await prefetch_task
            consumed = await self._consume_prefetched_episode_turn(runtime, session, prefetched)
            if not consumed or consumed.get("discarded"):
                return next_state
            consumed_decision = (
                consumed.get("decision")
                if isinstance(consumed.get("decision"), dict)
                else {}
            )
            consumed_base_state = (
                consumed.get("base_state")
                if isinstance(consumed.get("base_state"), dict)
                else next_state
            )
            consumed_episode_mode = str((consumed_decision.get("episode_plan") or {}).get("mode") or "")
            metadata = {
                "last_decision": consumed_decision,
                "last_result_job_id": consumed.get("interaction", {}).get("job_id", ""),
                "chat_batches_since_anchor": 0,
                **self._episode_metadata_after_turn(session, consumed_base_state, consumed_decision),
            }
            if reset_opening_metadata and consumed_count == 0:
                metadata.update({
                    "opening_decision": None,
                    "post_opening_decision": None,
                    "segment_state": {},
                })
            else:
                metadata["segment_state"] = self._segment_state_after_turn(
                    session,
                    consumed_base_state,
                    consumed_decision,
                    self._segment_topic_entry_for_session(session),
                )
            next_state = self.storage.update_director_state(
                runtime.session_id,
                status="running",
                last_director_action_at=datetime.now().isoformat(),
                consecutive_ai_turns=(
                    0
                    if consumed_episode_mode == "planned_turn"
                    else int(next_state.get("consecutive_ai_turns", 0) or 0) + 1
                ),
                current_topic=str(consumed_decision.get("current_topic") or next_state.get("current_topic") or ""),
                metadata=metadata,
            )
            await self._broadcast(runtime.session_id, {"type": "director_state", "director": next_state})
            consumed_count += 1
            prefetch_task = consumed.get("after_memoria_task")
        return next_state

    async def _consume_prefetched_episode_turn(
        self,
        runtime: LiveRuntime,
        session: dict[str, Any],
        prefetch: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not isinstance(prefetch, dict):
            return None
        interaction = prefetch.get("interaction") if isinstance(prefetch.get("interaction"), dict) else {}
        job_id = str(interaction.get("job_id") or "")
        prepared_results = [
            prepared for prepared in prefetch.get("prepared_results") or []
            if isinstance(prepared, dict)
        ]
        if not job_id or not prepared_results:
            return None
        if self._pending_director_blocking_events(runtime.session_id):
            _director_timing_log(
                "prefetch_discarded_pending_chat",
                session_id=runtime.session_id,
                job_id=job_id,
            )
            updated = self.storage.update_interaction(
                job_id,
                status="discarded",
                reason="prefetch_discarded_pending_chat",
                completed_at=datetime.now().isoformat(),
                metadata={"discarded_before_presentation": True},
            )
            for prepared in prepared_results:
                for item in prepared.get("items") or []:
                    if isinstance(item, dict) and item.get("item_id"):
                        self.storage.update_presentation_item(
                            item["item_id"],
                            status="skipped",
                            error="prefetch discarded because chat is pending",
                        )
            return {"interaction": updated or interaction, "discarded": True}
        _director_timing_log(
            "prefetch_consume_start",
            session_id=runtime.session_id,
            job_id=job_id,
            prepared_result_count=len(prepared_results),
        )
        started = self.storage.update_interaction(job_id, status="presenting") or interaction
        result = prefetch.get("memoria_result") if isinstance(prefetch.get("memoria_result"), dict) else {}
        result_session_id = str(result.get("session_id") or "")
        next_prefetch_task = None
        decision = prefetch.get("decision") if isinstance(prefetch.get("decision"), dict) else {}
        base_state = prefetch.get("base_state") if isinstance(prefetch.get("base_state"), dict) else {}
        if decision and base_state:
            chained_session = dict(session)
            if result_session_id:
                chained_session["target_memoria_session_id"] = result_session_id
            _director_timing_log(
                "prefetch_chain_scheduled",
                session_id=runtime.session_id,
                job_id=job_id,
            )
            runtime.director_prefetch_in_flight += 1

            async def run_next_prefetch():
                try:
                    return await self._prefetch_next_episode_planned_turn(
                        runtime,
                        chained_session,
                        base_state,
                        decision,
                    )
                finally:
                    runtime.director_prefetch_in_flight = max(
                        0,
                        runtime.director_prefetch_in_flight - 1,
                    )

            next_prefetch_task = asyncio.create_task(run_next_prefetch())
        await self._broadcast(runtime.session_id, {"type": "interaction_started", "interaction": started})
        await self.present_prepared_stream_results(
            runtime.session_id,
            prepared_results,
            source="director",
            interaction_job_id=job_id,
        )
        updated = self.storage.update_interaction(
            job_id,
            status="completed",
            reply_text=str(result.get("reply") or ""),
            memoria_session_id=str(result.get("session_id") or session.get("target_memoria_session_id") or ""),
            completed_at=datetime.now().isoformat(),
            metadata={"prefetch_consumed": True},
        ) or started
        _director_timing_log(
            "prefetch_consume_done",
            session_id=runtime.session_id,
            job_id=job_id,
        )
        if result_session_id and result_session_id != str(session.get("target_memoria_session_id") or ""):
            self.storage.update_session_fields(runtime.session_id, target_memoria_session_id=result_session_id)
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
        response = {**prefetch, "interaction": updated, "discarded": False}
        if next_prefetch_task is not None:
            response["after_memoria_task"] = next_prefetch_task
        return response

    async def _send_director_turn(
        self,
        session: dict[str, Any],
        state: dict[str, Any],
        decision: dict[str, Any],
        *,
        prefetch_only: bool = False,
        after_memoria_callback=None,
    ) -> dict[str, Any]:
        session_id = session["session_id"]
        send_started = time.perf_counter()
        target_session_id = session.get("target_memoria_session_id", "")
        target_character_ids = session.get("character_ids", [])
        action = str(decision.get("action") or "continue_topic")
        is_free_talk_action = action in self._POST_PLAN_FREE_TALK_ACTIONS
        prompt = str(decision.get("prompt") or "").strip()
        has_episode_plan = self._episode_plan_for_session(session) is not None and not is_free_talk_action
        public_prompt = self._public_director_prompt(action, session, state)
        if action == "opening":
            public_prompt = self._public_director_opening_prompt(session, state)
        if is_free_talk_action and prompt:
            public_prompt = prompt
        public_topic = self._public_director_topic(session, state)
        elapsed_minutes, elapsed_percent, remaining_minutes = self._session_elapsed(session)
        if not prompt:
            prompt = f"目前適合執行 {action}，請自然延續直播對話，不要提到幕後流程。"
        dialogue_expansion_enabled = self._director_dialogue_expansion_enabled(session)
        topic_context = ""
        if not has_episode_plan and not is_free_talk_action:
            if action == "opening":
                topic_context = self._topic_pack_sequence_preview_context_for_session(session_id)
            else:
                topic_context = self._topic_pack_sequence_context_for_session(
                    session_id,
                    "\n".join([
                        str(public_topic or ""),
                        str(public_prompt or ""),
                        str(state.get("current_topic") or ""),
                    ]),
                    usage_source="director",
                )
        public_action_label = "free_talk" if is_free_talk_action else action
        context_parts = [f"直播流程 action={public_action_label}"]
        if not has_episode_plan:
            context_parts.append(f"直播進度：{elapsed_percent}%（已 {elapsed_minutes} 分鐘，剩餘約 {remaining_minutes} 分鐘）")
        context_parts.append(f"處理提示：{public_prompt}")
        if (
            dialogue_expansion_enabled
            and not has_episode_plan
            and action not in {"reply_chat_batch", "reply_super_chat_batch"}
        ):
            context_parts.append(
                "直播互動規則：目前不是回應留言批次；請讓角色彼此接話、補充、反駁或提出下一個切入點，不要把問題丟回聊天室。"
            )
        if action == "closing_super_chat_thanks" and prompt:
            context_parts.append("本場 Super Chat 參考內容：\n" + prompt[:3000])
        decision_episode_payload = (
            decision.get("episode_plan")
            if isinstance(decision.get("episode_plan"), dict)
            else {}
        )
        episode_character_records: list[dict[str, Any]] | None = None
        if has_episode_plan and decision_episode_payload:
            episode_character_records = self._memoria_client().list_characters()
        episode_patch, episode_context_text, episode_topic_context = self._episode_plan_external_context_patch(
            session,
            state,
            decision,
            character_records=episode_character_records,
        )
        if episode_context_text:
            context_parts.append(episode_context_text)
        allowed_turn_character_ids: list[str] = []
        if episode_patch:
            turn_contract = (
                (decision.get("episode_plan") or {}).get("turn_contract")
                if isinstance(decision.get("episode_plan"), dict)
                else {}
            )
            if not isinstance(turn_contract, dict) or not turn_contract:
                turn_contract = (
                    (episode_patch.get("live_episode_plan") or {}).get("turn_contract")
                    if isinstance(episode_patch.get("live_episode_plan"), dict)
                    else {}
                )
            # Keep the full episode roster in MemoriaCore so the session starts
            # as a group. Per-turn speaker limits are projected in
            # live_episode_plan.speaker_policy.allowed_character_ids.
            allowed_turn_character_ids = self._episode_character_ids_for_turn(
                session,
                turn_contract or {},
                character_records=episode_character_records,
            )
            target_character_ids = self._episode_character_ids_for_session(
                session,
                character_records=episode_character_records,
            )
        group_turn_limit = self._director_group_turn_limit_for_action(session, action)
        live_episode_plan = episode_patch.get("live_episode_plan") if isinstance(episode_patch, dict) else {}
        planned_turn_type = ""
        if isinstance(live_episode_plan, dict) and str(live_episode_plan.get("mode") or "") == "planned_turn":
            planned_turn_type = str(live_episode_plan.get("turn_type") or "").strip()
            dialogue_policy = (
                live_episode_plan.get("dialogue_policy")
                if isinstance(live_episode_plan.get("dialogue_policy"), dict)
                else {}
            )
            group_turn_limit = self._episode_plan_group_turn_limit(
                session,
                planned_turn_type,
                dialogue_policy,
            )
            if prompt:
                public_prompt = prompt
                context_parts = [
                    part for part in context_parts
                    if not str(part).startswith("處理提示：")
                ]
        presentation_mode = self._presentation_enabled(session)
        if not dialogue_expansion_enabled:
            group_turn_limit = 1
        elif presentation_mode:
            group_turn_limit = (
                1
                if action == "opening" or planned_turn_type in {"opening", "cohost_intro"}
                else 2
            )
        if action == "opening" and not has_episode_plan:
            opening_intro_context = self._opening_intro_context_for_session(session)
            if opening_intro_context:
                context_parts.append(opening_intro_context)
            if topic_context:
                context_parts.append(
                    "開場後話題導入資料：以下 <topic_pack_fact_cards> 只能在固定開場白與自我介紹完成後使用；"
                    "請用其中一個具體切入點帶入討論，不得自行捏造資料卡未提供的作品、集數或事件。"
                )
        elif planned_turn_type in {"opening", "cohost_intro"}:
            opening_intro_context = self._opening_intro_context_for_session(
                session,
                character_ids=allowed_turn_character_ids or target_character_ids,
            )
            if opening_intro_context:
                context_parts.append(opening_intro_context)
        if episode_topic_context:
            context_parts.append(episode_topic_context)
        if action == "post_opening_topic_anchor" and not has_episode_plan:
            if topic_context:
                context_parts.append(
                    "話題導入規則：開場已完成，接下來必須優先使用下方 <topic_pack_fact_cards> "
                    "中的具體作品、集數、事件或觀點，不得自行捏造未提供的內容。"
                )
            else:
                context_parts.append(
                    "話題導入規則：目前沒有可用話題資料卡；請延續開場與既有直播方向，不得自行捏造具體作品、集數或事件。"
                )
        live_hosting = {}
        if not has_episode_plan and not episode_patch:
            live_hosting = self._live_hosting_context_for_session(session, state)
        if live_hosting:
            context_parts.append(self._live_hosting_context_text(live_hosting))
        if topic_context:
            context_parts.append(topic_context)
        external_context = {
            "source": "youtube_live_director",
            "source_session_id": session_id,
            "connector_id": session.get("connector_id", ""),
            "video_id": session.get("video_id", ""),
            "live_chat_id": session.get("live_chat_id", ""),
            "director_dialogue_expansion_enabled": dialogue_expansion_enabled,
            "group_turn_limit": group_turn_limit,
            "context_text": "\n".join(context_parts),
            "event_ids": [],
            "visible_events": [],
            "max_chars": (
                1200
                if presentation_mode
                else 4000 if action == "closing_super_chat_thanks" else 2500
            ),
            "summary": {
                "source": "youtube_live_director",
                "source_session_id": session_id,
                "event_count": 0,
                "action": action,
                "director_dialogue_expansion_enabled": dialogue_expansion_enabled,
                "group_turn_limit": group_turn_limit,
            },
        }
        if presentation_mode:
            external_context["summary"]["presentation_enabled"] = True
            external_context["context_text"] = "\n".join([
                external_context["context_text"],
                "直播輸出模式：請產生短 spoken beat；最多輸出目前角色與下一位角色各一個短句。"
                "下一位角色只是預先準備，內容仍需保持可被聊天室打斷。",
            ])
        if live_hosting:
            external_context["live_hosting"] = live_hosting
        if episode_patch:
            external_context.update(episode_patch)
            live_episode_plan = episode_patch.get("live_episode_plan") or {}
            external_context["summary"]["episode_plan_id"] = live_episode_plan.get("plan_id", "")
            external_context["summary"]["episode_plan_turn_id"] = live_episode_plan.get("turn_id", "")
            external_context["summary"]["episode_plan_mode"] = live_episode_plan.get("mode", "")
        if "group_turn_limit" in decision:
            try:
                group_turn_limit = int(decision.get("group_turn_limit") or group_turn_limit)
            except (TypeError, ValueError):
                pass
            group_turn_limit = max(1, min(group_turn_limit, 12))
            external_context["group_turn_limit"] = group_turn_limit
            external_context["summary"]["group_turn_limit"] = group_turn_limit
        external_context = self._attach_live_persona_overrides(session, external_context)
        display_content = self._director_display_content(action)
        if isinstance(live_episode_plan, dict) and str(live_episode_plan.get("mode") or "") == "planned_turn":
            turn_type = planned_turn_type
            if turn_type == "opening":
                display_content = "直播開場。"
            elif turn_type == "cohost_intro":
                display_content = "共同主持開場。"
        _director_timing_log(
            "send_prepared_context",
            session_id=session_id,
            action=action,
            target_memoria_session_id=target_session_id,
            target_character_count=len(target_character_ids or []),
            allowed_turn_character_count=len(allowed_turn_character_ids or []),
            group_turn_limit=group_turn_limit,
            episode_mode=live_episode_plan.get("mode") if isinstance(live_episode_plan, dict) else "",
            episode_turn_id=live_episode_plan.get("turn_id") if isinstance(live_episode_plan, dict) else "",
            planned_turn_type=planned_turn_type,
            context_chars=len(external_context.get("context_text") or ""),
        )
        interaction = self.storage.create_interaction(
            {
                "session_id": session_id,
                "source": "director_prefetch" if prefetch_only else "director",
                "priority": 40 if prefetch_only else 50,
                "status": "prefetching" if prefetch_only else "queued",
                "event_ids": [],
                "memoria_session_id": target_session_id,
                "character_ids": target_character_ids,
                "content": public_prompt,
                "metadata": {"decision": decision, "prefetch_only": prefetch_only},
            }
        )
        _director_timing_log(
            "send_interaction_created",
            session_id=session_id,
            job_id=interaction.get("job_id"),
            elapsed_ms=round((time.perf_counter() - send_started) * 1000, 1),
        )
        runtime = self._runtimes.setdefault(session_id, LiveRuntime(session_id=session_id))
        if not prefetch_only:
            claim_started = time.perf_counter()
            claimed = await self._claim_interaction_for_execution(runtime, interaction)
            _director_timing_log(
                "send_interaction_claimed",
                session_id=session_id,
                job_id=(claimed or interaction).get("job_id"),
                status=(claimed or interaction).get("status"),
                duration_ms=round((time.perf_counter() - claim_started) * 1000, 1),
            )
            if not claimed or claimed.get("status") != "running":
                return {"interaction": claimed or interaction, "memoria_result": {}}
            interaction = claimed
        cancel_event = threading.Event()
        if not prefetch_only:
            runtime.cancel_events[interaction["job_id"]] = cancel_event
        loop = asyncio.get_running_loop()

        def should_cancel() -> bool:
            current = self.storage.get_interaction(interaction["job_id"])
            return cancel_event.is_set() or bool(current and current.get("status") == "interrupt_requested")

        presentation_futures = []
        after_memoria_task = None

        def on_stream_result(event: dict[str, Any]) -> None:
            if prefetch_only:
                future = asyncio.run_coroutine_threadsafe(
                    self.prepare_stream_result(
                        session_id,
                        event,
                        source="director",
                        interaction_job_id=interaction["job_id"],
                    ),
                    loop,
                )
                presentation_futures.append(future)
                return
            future = self._dispatch_stream_chat_result(
                loop,
                session_id,
                event,
                source="director",
                interaction_job_id=interaction["job_id"],
                wait_for_completion=not presentation_mode,
            )
            if presentation_mode and future is not None:
                presentation_futures.append(future)

        try:
            memoria_started = time.perf_counter()
            _director_timing_log(
                "send_memoria_call_start",
                session_id=session_id,
                job_id=interaction.get("job_id"),
                target_memoria_session_id=target_session_id,
                group_turn_limit=group_turn_limit,
            )
            result = await asyncio.to_thread(
                self._memoria_client().chat_stream_sync,
                content=public_prompt,
                display_content=display_content,
                session_id=target_session_id,
                character_ids=target_character_ids,
                external_context=external_context,
                should_cancel=should_cancel,
                cancel_event=cancel_event,
                on_result=on_stream_result,
            )
            _director_timing_log(
                "send_memoria_call_done",
                session_id=session_id,
                job_id=interaction.get("job_id"),
                duration_ms=round((time.perf_counter() - memoria_started) * 1000, 1),
                result_session_id=result.get("session_id"),
                result_message_id=result.get("message_id"),
            )
            result_session_id = str(result.get("session_id") or "")
            if not prefetch_only and result_session_id and result_session_id != target_session_id:
                self.storage.update_session_fields(session_id, target_memoria_session_id=result_session_id)
            if after_memoria_callback and not prefetch_only:
                maybe_callback_result = after_memoria_callback(result)
                if asyncio.iscoroutine(maybe_callback_result):
                    after_memoria_task = asyncio.create_task(maybe_callback_result)
            if presentation_futures:
                presentation_started = time.perf_counter()
                prepared_results = await asyncio.gather(*(asyncio.wrap_future(future) for future in presentation_futures))
                _director_timing_log(
                    "send_presentation_done",
                    session_id=session_id,
                    job_id=interaction.get("job_id"),
                    presentation_event_count=len(presentation_futures),
                    duration_ms=round((time.perf_counter() - presentation_started) * 1000, 1),
                )
            else:
                prepared_results = []
        except GenerationInterrupted:
            _director_timing_log(
                "send_memoria_call_interrupted",
                session_id=session_id,
                job_id=interaction.get("job_id"),
                duration_ms=round((time.perf_counter() - memoria_started) * 1000, 1),
            )
            updated = self.storage.update_interaction(
                interaction["job_id"],
                status="interrupted",
                closure_text="先停在這裡，剛剛聊天室有新的問題，我們切過去看。",
                completed_at=datetime.now().isoformat(),
                metadata={"discarded": True},
            )
            await self._broadcast(session_id, {"type": "interaction_interrupted", "interaction": updated})
            return {"interaction": updated, "memoria_result": {}}
        except Exception as exc:
            current = self.storage.get_interaction(interaction["job_id"])
            was_interrupted = cancel_event.is_set() or bool(current and current.get("status") == "interrupt_requested")
            reason = self._normalized_interrupt_reason(current, exc)
            _director_timing_log(
                "send_memoria_call_failed",
                session_id=session_id,
                job_id=interaction.get("job_id"),
                duration_ms=round((time.perf_counter() - memoria_started) * 1000, 1),
                was_interrupted=was_interrupted,
                reason=reason,
                error=str(exc)[:300],
            )
            updated = self.storage.update_interaction(
                interaction["job_id"],
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
                return {"interaction": updated, "memoria_result": {}}
            raise
        finally:
            runtime.cancel_events.pop(interaction["job_id"], None)

        if prefetch_only:
            prepared_clean = [
                prepared for prepared in prepared_results
                if isinstance(prepared, dict) and prepared.get("message")
            ]
            updated = self.storage.update_interaction(
                interaction["job_id"],
                status="prefetched",
                reply_text=str(result.get("reply") or ""),
                memoria_session_id=str(result.get("session_id") or target_session_id),
                completed_at=datetime.now().isoformat(),
                metadata={
                    "result_message_id": result.get("message_id"),
                    "prefetch_ready": True,
                    "prepared_result_count": len(prepared_clean),
                },
            )
            return {
                "interaction": updated or interaction,
                "memoria_result": result,
                "prepared_results": prepared_clean,
                "decision": decision,
                "base_state": state,
            }

        current_after = self.storage.get_interaction(interaction["job_id"])
        interrupted_after_provider = bool(
            current_after and current_after.get("status") in {"interrupt_requested", "interrupted", "discarded"}
        )
        if interrupted_after_provider:
            interaction_status = "discarded"
            closure_text = "先停在這裡，剛剛聊天室有新的問題，我們切過去看。"
            reply_text = ""
        else:
            interaction_status = "completed"
            closure_text = ""
            reply_text = str(result.get("reply") or "")
        updated = self.storage.update_interaction(
            interaction["job_id"],
            status=interaction_status,
            reply_text=reply_text,
            closure_text=closure_text,
            memoria_session_id=str(result.get("session_id") or target_session_id),
            completed_at=datetime.now().isoformat(),
            metadata={
                "result_message_id": result.get("message_id"),
                "discarded_after_provider_return": interrupted_after_provider,
            },
        )
        await self._broadcast(session_id, {
            "type": "interaction_completed",
            "interaction": updated,
            "memoria_session_id": result.get("session_id") or target_session_id,
            "source": "director",
        })
        await self._broadcast(session_id, {
            "type": "director_injected",
            "interaction": updated,
            "memoria_session_id": result.get("session_id") or target_session_id,
        })
        _director_timing_log(
            "send_completed",
            session_id=session_id,
            job_id=interaction.get("job_id"),
            status=interaction_status,
            total_duration_ms=round((time.perf_counter() - send_started) * 1000, 1),
        )
        response = {"interaction": updated, "memoria_result": result}
        if after_memoria_task is not None:
            response["after_memoria_task"] = after_memoria_task
        return response
