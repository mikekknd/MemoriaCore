"""YouTubeBridge director runtime mixin。"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from datetime import datetime
from typing import Any

from bridge_runtime import LiveRuntime
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
                result = await self._send_director_turn(session, state, episode_decision)
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
                return
            decision = self._director_opening_decision(session, state)
            opening_state = self.storage.update_director_state(
                runtime.session_id,
                status="opening",
                metadata={"opening_decision": decision},
            )
            await self._broadcast(runtime.session_id, {"type": "director_state", "director": opening_state})
            result = await self._send_director_turn(session, state, decision)
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
                pending = [
                    event for event in self.storage.list_events(runtime.session_id, limit=5, uninjected_only=True)
                    if self._should_block_director_for_pending_inject(event)
                ]
                if pending:
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
                result = await self._send_director_turn(session, state, decision)
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
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("YouTube director error session_id=%s error=%s", runtime.session_id, exc, exc_info=True)
                state = self.storage.update_director_state(runtime.session_id, status="error", metadata={"last_error": str(exc)})
                await self._broadcast(runtime.session_id, {"type": "director_state", "director": state})
                await self._broadcast(runtime.session_id, {"type": "director_error", "message": str(exc)})
                await asyncio.sleep(15)

    async def _send_director_turn(
        self,
        session: dict[str, Any],
        state: dict[str, Any],
        decision: dict[str, Any],
    ) -> dict[str, Any]:
        session_id = session["session_id"]
        send_started = time.perf_counter()
        target_session_id = session.get("target_memoria_session_id", "")
        target_character_ids = session.get("character_ids", [])
        action = str(decision.get("action") or "continue_topic")
        prompt = str(decision.get("prompt") or "").strip()
        has_episode_plan = self._episode_plan_for_session(session) is not None
        public_prompt = self._public_director_prompt(action, session, state)
        if action == "opening":
            public_prompt = self._public_director_opening_prompt(session, state)
        public_topic = self._public_director_topic(session, state)
        elapsed_minutes, elapsed_percent, remaining_minutes = self._session_elapsed(session)
        if not prompt:
            prompt = f"目前適合執行 {action}，請自然延續直播對話，不要提到幕後流程。"
        dialogue_expansion_enabled = self._director_dialogue_expansion_enabled(session)
        topic_context = ""
        if not has_episode_plan:
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
        context_parts = [f"直播流程 action={action}"]
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
        if not dialogue_expansion_enabled:
            group_turn_limit = 1
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
            "max_chars": 4000 if action == "closing_super_chat_thanks" else 2500,
            "summary": {
                "source": "youtube_live_director",
                "source_session_id": session_id,
                "event_count": 0,
                "action": action,
                "director_dialogue_expansion_enabled": dialogue_expansion_enabled,
                "group_turn_limit": group_turn_limit,
            },
        }
        if live_hosting:
            external_context["live_hosting"] = live_hosting
        if episode_patch:
            external_context.update(episode_patch)
            live_episode_plan = episode_patch.get("live_episode_plan") or {}
            external_context["summary"]["episode_plan_id"] = live_episode_plan.get("plan_id", "")
            external_context["summary"]["episode_plan_turn_id"] = live_episode_plan.get("turn_id", "")
            external_context["summary"]["episode_plan_mode"] = live_episode_plan.get("mode", "")
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
                "source": "director",
                "priority": 50,
                "status": "queued",
                "event_ids": [],
                "memoria_session_id": target_session_id,
                "character_ids": target_character_ids,
                "content": public_prompt,
                "metadata": {"decision": decision},
            }
        )
        _director_timing_log(
            "send_interaction_created",
            session_id=session_id,
            job_id=interaction.get("job_id"),
            elapsed_ms=round((time.perf_counter() - send_started) * 1000, 1),
        )
        runtime = self._runtimes.setdefault(session_id, LiveRuntime(session_id=session_id))
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
        runtime.cancel_events[interaction["job_id"]] = cancel_event
        loop = asyncio.get_running_loop()

        def should_cancel() -> bool:
            current = self.storage.get_interaction(interaction["job_id"])
            return cancel_event.is_set() or bool(current and current.get("status") == "interrupt_requested")

        def on_stream_result(event: dict[str, Any]) -> None:
            self._broadcast_stream_chat_message(loop, session_id, event, source="director")

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
        result_session_id = str(result.get("session_id") or "")
        if result_session_id and result_session_id != target_session_id:
            self.storage.update_session_fields(session_id, target_memoria_session_id=result_session_id)
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
        return {"interaction": updated, "memoria_result": result}
