"""Plan-aware director helpers for LiveEpisodePlan sessions."""
from __future__ import annotations

import copy
import json
from typing import Any

from bridge_contracts import AUDIENCE_EVENT_CLASSIFIER_SCHEMA
from live_episode_plan_contract import (
    current_segment,
    current_turn_contract,
    initial_planned_state,
    initial_segment_memory,
    validate_live_episode_plan,
)


class EpisodePlanManagerMixin:
    @staticmethod
    def _episode_classifier_actions(plan: dict[str, Any]) -> dict[str, str]:
        classifier = (
            plan.get("audience_event_classifier")
            if isinstance(plan.get("audience_event_classifier"), dict)
            else {}
        )
        actions = classifier.get("actions") if isinstance(classifier.get("actions"), dict) else {}
        return {str(key): str(value) for key, value in actions.items()}

    def _episode_plan_for_session(self, session: dict[str, Any]) -> dict[str, Any] | None:
        plan_id = str(session.get("episode_plan_id") or "").strip()
        if not plan_id:
            return None
        record = self.storage.get_live_episode_plan(plan_id)
        if not record:
            return None
        return validate_live_episode_plan(record.get("plan_json") or {})

    def _episode_plan_and_state(
        self,
        session: dict[str, Any],
        state: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        plan = self._episode_plan_for_session(session)
        if not plan:
            return None, {}
        metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
        raw_state = metadata.get("planned_state") if isinstance(metadata.get("planned_state"), dict) else {}
        if raw_state.get("plan_id") != plan.get("plan_id"):
            return plan, initial_planned_state(plan)
        planned_state = copy.deepcopy(raw_state)
        planned_state.setdefault("plan_status", "running")
        planned_state.setdefault("completed_segment_ids", [])
        planned_state.setdefault("completed_turn_ids", [])
        planned_state.setdefault("completed_turn_types", [])
        planned_state.setdefault("segment_memory", initial_segment_memory())
        planned_state.setdefault("last_planned_turn_contract_id", "")
        return plan, planned_state

    @staticmethod
    def _episode_current_segment(
        plan: dict[str, Any],
        planned_state: dict[str, Any],
    ) -> dict[str, Any] | None:
        return current_segment(plan, planned_state)

    @staticmethod
    def _episode_current_turn_contract(
        plan: dict[str, Any],
        planned_state: dict[str, Any],
    ) -> dict[str, Any] | None:
        return current_turn_contract(plan, planned_state)

    def _planned_state_after_episode_turn(
        self,
        plan: dict[str, Any],
        planned_state: dict[str, Any],
        completed_turn: dict[str, Any],
    ) -> dict[str, Any]:
        next_state = copy.deepcopy(planned_state)
        segment = self._episode_current_segment(plan, next_state) or {}
        turns = (
            segment.get("planned_turn_contracts")
            if isinstance(segment.get("planned_turn_contracts"), list)
            else []
        )
        turn_id = str(completed_turn.get("turn_id") or "")
        turn_type = str(completed_turn.get("turn_type") or "")
        if turn_id:
            next_state.setdefault("completed_turn_ids", []).append(turn_id)
            next_state["last_planned_turn_contract_id"] = turn_id
        if turn_type:
            next_state.setdefault("completed_turn_types", []).append(turn_type)

        memory = (
            next_state.get("segment_memory")
            if isinstance(next_state.get("segment_memory"), dict)
            else initial_segment_memory()
        )
        if turn_id:
            memory.setdefault("covered_claims", []).append(f"completed:{turn_id}")
        forbidden = (
            completed_turn.get("forbidden_repetition")
            if isinstance(completed_turn.get("forbidden_repetition"), dict)
            else {}
        )
        repeats: list[str] = []
        for key in ("claims", "metaphors", "openings"):
            repeats.extend(
                str(item).strip()
                for item in forbidden.get(key) or []
                if str(item).strip()
            )
        memory["forbidden_next_repeats"] = repeats[:20]
        next_state["segment_memory"] = memory

        completion = (
            segment.get("completion_conditions")
            if isinstance(segment.get("completion_conditions"), dict)
            else {}
        )
        completed_types = set(next_state.get("completed_turn_types") or [])
        required_types = {
            str(item).strip()
            for item in completion.get("required_turn_types") or []
            if str(item).strip()
        }
        min_turns = int(completion.get("min_planned_turns") or 1)
        max_turns = int(completion.get("max_planned_turns") or max(min_turns, len(turns), 1))
        completed_count = len(next_state.get("completed_turn_ids") or [])
        segment_done = completed_count >= min_turns and required_types.issubset(completed_types)
        segment_done = segment_done or completed_count >= max_turns
        if segment_done:
            segment_index = int(next_state.get("current_segment_index") or 0)
            segment_id = str(segment.get("segment_id") or "")
            if segment_id:
                next_state.setdefault("completed_segment_ids", []).append(segment_id)
            if segment_index < len(plan.get("segments") or []) - 1:
                next_state["current_segment_index"] = segment_index + 1
                next_state["current_turn_index"] = 0
                next_state["completed_turn_ids"] = []
                next_state["completed_turn_types"] = []
                next_state["segment_memory"] = initial_segment_memory()
                next_state["plan_status"] = "running"
                return next_state
            next_state["plan_status"] = "completed"
            next_state["current_turn_index"] = max(0, len(turns) - 1)
            return next_state

        current_turn_index = int(next_state.get("current_turn_index") or 0)
        next_state["current_turn_index"] = min(current_turn_index + 1, max(0, len(turns) - 1))
        next_state["plan_status"] = "running"
        return next_state

    def _interrupt_state_for_audience_event(
        self,
        plan: dict[str, Any],
        planned_state: dict[str, Any],
        event: dict[str, Any],
        event_type: str,
        action: str,
    ) -> dict[str, Any]:
        segment = self._episode_current_segment(plan, planned_state) or {}
        handling = (
            segment.get("audience_handling")
            if isinstance(segment.get("audience_handling"), dict)
            else {}
        )
        remaining_turns = max(1, min(int(handling.get("max_interrupt_turns") or 1), 4))
        return {
            "status": "handling_audience",
            "source_event_ids": [int(event.get("id") or 0)] if event.get("id") else [],
            "interrupt_type": str(event_type or "question"),
            "action": str(action or "bounded_interrupt"),
            "return_segment_index": int(planned_state.get("current_segment_index") or 0),
            "return_turn_index": int(planned_state.get("current_turn_index") or 0),
            "remaining_interrupt_turns": remaining_turns,
            "resume_rule": str(handling.get("resume_rule") or "bridge_back_to_segment_goal"),
        }

    def _classify_episode_audience_event(
        self,
        plan: dict[str, Any],
        event: dict[str, Any],
    ) -> dict[str, str]:
        actions = self._episode_classifier_actions(plan)
        label = str(event.get("safety_label") or "").lower()
        if "prompt" in label or "injection" in label:
            return {
                "event_type": "prompt_injection",
                "action": "ignore",
                "reason": "safety_label",
            }
        if str(event.get("priority_class") or "") == "super_chat":
            return {
                "event_type": "super_chat",
                "action": str(actions.get("super_chat") or "bounded_interrupt"),
                "reason": "priority_class",
            }
        text = str(event.get("safe_message_text") or "").strip()
        if any(token in text for token in ("更正", "不是", "說錯", "補充一下")):
            return {
                "event_type": "correction",
                "action": str(actions.get("correction") or "verify_then_ack"),
                "reason": "safe_text",
            }
        if "?" in text or "？" in text:
            return {
                "event_type": "question",
                "action": str(actions.get("question") or "bounded_interrupt"),
                "reason": "safe_text",
            }
        if len(text) <= 40:
            return {
                "event_type": "reaction",
                "action": str(actions.get("reaction") or "optional_ack"),
                "reason": "short_reaction",
            }

        classifier = (
            plan.get("audience_event_classifier")
            if isinstance(plan.get("audience_event_classifier"), dict)
            else {}
        )
        try:
            result = self._memoria_client().generate_prompt_json(
                prompt_key="youtube_live_audience_event_classifier_prompt",
                variables={
                    "event_json": json.dumps(event, ensure_ascii=False, indent=2),
                    "allowed_event_types": "\n".join(
                        str(item) for item in classifier.get("event_types") or []
                    ),
                    "actions_json": json.dumps(actions, ensure_ascii=False, indent=2),
                },
                task_key="router",
                temperature=0.0,
                schema=AUDIENCE_EVENT_CLASSIFIER_SCHEMA,
            )
        except Exception:
            return {
                "event_type": "off_topic",
                "action": str(actions.get("off_topic") or "ignore_or_soft_ack"),
                "reason": "classifier_fallback",
            }

        event_type = str(result.get("event_type") or "off_topic")
        action = str(result.get("action") or actions.get(event_type) or "ignore_or_soft_ack")
        expected_action = str(actions.get(event_type) or action)
        if action != expected_action:
            action = expected_action
        return {
            "event_type": event_type,
            "action": action,
            "reason": str(result.get("reason") or "llm_classifier")[:240],
        }

    def _episode_interrupt_decision_for_event(
        self,
        plan: dict[str, Any],
        planned_state: dict[str, Any],
        event: dict[str, Any],
    ) -> dict[str, Any] | None:
        classified = self._classify_episode_audience_event(plan, event)
        action = classified["action"]
        if action == "ignore":
            return None
        if action not in {
            "bounded_interrupt",
            "verify_then_ack",
            "ignore_or_soft_ack",
            "ignore_or_deescalate",
        }:
            return None
        event_type = classified["event_type"]
        interrupt_state = self._interrupt_state_for_audience_event(
            plan,
            planned_state,
            event,
            event_type,
            action,
        )
        director_action = "reply_super_chat_batch" if event_type == "super_chat" else "reply_chat_batch"
        return {
            "action": director_action,
            "reason": f"episode audience event: {event_type}",
            "prompt": str(event.get("safe_message_text") or "")[:500],
            "current_topic": "",
            "episode_plan": {
                "mode": "audience_interrupt",
                "event_type": event_type,
                "event_action": action,
                "interrupt_state": interrupt_state,
                "classification_reason": classified["reason"],
            },
        }

    def _episode_planned_turn_decision(
        self,
        session: dict[str, Any],
        state: dict[str, Any],
    ) -> dict[str, Any]:
        plan, planned_state = self._episode_plan_and_state(session, state)
        if not plan:
            return {}
        turn = self._episode_current_turn_contract(plan, planned_state)
        segment = self._episode_current_segment(plan, planned_state)
        if not turn or not segment:
            return {}
        return {
            "action": "continue_topic",
            "reason": f"episode planned turn {turn['turn_id']}",
            "prompt": str(turn.get("intent") or segment.get("goal") or ""),
            "current_topic": str(segment.get("title") or ""),
            "episode_plan": {
                "mode": "planned_turn",
                "planned_state": planned_state,
                "segment": {
                    "segment_id": str(segment.get("segment_id") or ""),
                    "title": str(segment.get("title") or ""),
                    "goal": str(segment.get("goal") or ""),
                },
                "turn_contract": turn,
            },
        }

    def _episode_plan_context_text(
        self,
        plan: dict[str, Any],
        planned_state: dict[str, Any],
        turn: dict[str, Any],
        *,
        interrupt_state: dict[str, Any],
    ) -> str:
        segment = self._episode_current_segment(plan, planned_state) or {}
        speaker = turn.get("speaker_policy") if isinstance(turn.get("speaker_policy"), dict) else {}
        evidence = turn.get("evidence_policy") if isinstance(turn.get("evidence_policy"), dict) else {}
        output = (
            turn.get("output_requirements")
            if isinstance(turn.get("output_requirements"), dict)
            else {}
        )
        forbidden = (
            turn.get("forbidden_repetition")
            if isinstance(turn.get("forbidden_repetition"), dict)
            else {}
        )
        queries = [
            str(query).strip()
            for query in evidence.get("queries") or []
            if str(query).strip()
        ]
        required_entities = [
            str(item).strip()
            for item in evidence.get("required_entities") or []
            if str(item).strip()
        ]
        preferred_functions = [
            str(item).strip()
            for item in speaker.get("preferred_role_functions") or []
            if str(item).strip()
        ]
        allowed_participant_ids = [
            str(item).strip()
            for item in speaker.get("allowed_participant_ids") or []
            if str(item).strip()
        ]
        lines = [
            "<live_episode_director_context>",
            f"plan_id: {plan.get('plan_id')}",
            f"segment: {segment.get('segment_id')} / {segment.get('title')}",
            f"segment_goal: {segment.get('goal')}",
            f"turn_contract: {turn.get('turn_id')}",
            f"turn_type: {turn.get('turn_type')}",
            f"turn_intent: {turn.get('intent')}",
            "speaker_policy:",
            f"  selection_mode: {speaker.get('selection_mode') or 'router_select'}",
            "  preferred_role_functions: "
            + (", ".join(preferred_functions) if preferred_functions else "未指定"),
            "  allowed_participant_ids: "
            + (", ".join(allowed_participant_ids) if allowed_participant_ids else "未指定"),
            f"  avoid_repeat_speaker: {bool(speaker.get('avoid_repeat_speaker'))}",
            "evidence_policy:",
            f"  queries: {' | '.join(queries)}",
            "  required_entities: "
            + (", ".join(required_entities) if required_entities else "未指定"),
            f"  max_cards: {int(evidence.get('max_cards') or 0)}",
            f"  allow_unverified_claims: {bool(evidence.get('allow_unverified_claims'))}",
            "output_requirements:",
            f"  max_sentences: {int(output.get('max_sentences') or 2)}",
            f"  must_end_with_question: {bool(output.get('must_end_with_question'))}",
            f"  allow_audience_question: {bool(output.get('allow_audience_question'))}",
            f"  should_handoff: {bool(output.get('should_handoff'))}",
            f"  handoff_target_function: {output.get('handoff_target_function') or '未指定'}",
            "forbidden_repetition:",
            "  claims: " + ", ".join(str(item) for item in forbidden.get("claims") or []),
            "  metaphors: " + ", ".join(str(item) for item in forbidden.get("metaphors") or []),
            "  openings: " + ", ".join(str(item) for item in forbidden.get("openings") or []),
        ]
        if interrupt_state:
            lines.append(f"interrupt_type: {interrupt_state.get('interrupt_type')}")
            lines.append(f"resume_rule: {interrupt_state.get('resume_rule')}")
        else:
            lines.append("resume_rule: 本輪不是聊天室打斷，完成後依 required_turn_types 檢查段落進度。")
        lines.append("</live_episode_director_context>")
        return "\n".join(lines)

    def _episode_turn_topic_context(self, session_id: str, turn: dict[str, Any]) -> str:
        evidence = turn.get("evidence_policy") if isinstance(turn.get("evidence_policy"), dict) else {}
        queries = [
            str(query).strip()
            for query in evidence.get("queries") or []
            if str(query).strip()
        ]
        if not queries:
            return ""
        max_cards = max(1, min(int(evidence.get("max_cards") or 3), 8))
        return self._topic_pack_context_for_query(
            session_id,
            "\n".join(queries),
            limit=max_cards,
            usage_source="episode_plan",
            allow_fallback=bool(evidence.get("allow_unverified_claims")),
        )

    def _episode_plan_external_context_patch(
        self,
        session: dict[str, Any],
        state: dict[str, Any],
        decision: dict[str, Any],
    ) -> tuple[dict[str, Any], str]:
        payload = decision.get("episode_plan") if isinstance(decision.get("episode_plan"), dict) else {}
        if not payload:
            return {}, ""
        plan, planned_state = self._episode_plan_and_state(session, state)
        if not plan:
            return {}, ""
        turn = (
            payload.get("turn_contract")
            if isinstance(payload.get("turn_contract"), dict)
            else self._episode_current_turn_contract(plan, planned_state)
        )
        if not turn:
            return {}, ""
        interrupt_state = (
            payload.get("interrupt_state")
            if isinstance(payload.get("interrupt_state"), dict)
            else {}
        )
        segment_payload = payload.get("segment") if isinstance(payload.get("segment"), dict) else {}
        current = self._episode_current_segment(plan, planned_state) or {}
        context_text = self._episode_plan_context_text(
            plan,
            planned_state,
            turn,
            interrupt_state=interrupt_state,
        )
        topic_context = self._episode_turn_topic_context(
            str(session.get("session_id") or ""),
            turn,
        )
        patch = {
            "live_episode_plan": {
                "plan_id": str(plan.get("plan_id") or ""),
                "title": str(plan.get("title") or ""),
                "mode": str(payload.get("mode") or "planned_turn"),
                "segment_id": str(
                    segment_payload.get("segment_id")
                    or current.get("segment_id")
                    or ""
                ),
                "turn_id": str(turn.get("turn_id") or ""),
                "turn_type": str(turn.get("turn_type") or ""),
                "interrupt_state": interrupt_state,
            }
        }
        return patch, "\n".join(part for part in (context_text, topic_context) if part)
