"""Plan-aware director helpers for LiveEpisodePlan sessions."""
from __future__ import annotations

import copy
import json
from datetime import datetime
from typing import Any

from bridge_contracts import AUDIENCE_EVENT_CLASSIFIER_SCHEMA
from episode_plan_character_binding import (
    EpisodePlanCharacterBindingError,
    resolve_episode_plan_character_ids,
)
from live_episode_plan_contract import (
    current_segment,
    current_turn_contract,
    dialogue_policy_for_turn,
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

    def _episode_character_ids_for_session(
        self,
        session: dict[str, Any],
        *,
        character_records: list[dict[str, Any]] | None = None,
    ) -> list[str]:
        plan = self._episode_plan_for_session(session)
        if not plan:
            return list(session.get("character_ids") or [])
        try:
            return resolve_episode_plan_character_ids(
                plan,
                character_records
                if character_records is not None
                else self._memoria_client().list_characters(),
            )
        except EpisodePlanCharacterBindingError as exc:
            raise RuntimeError(f"企劃角色對應失敗：{exc}") from exc

    def _episode_participant_character_map_for_session(
        self,
        session: dict[str, Any],
        *,
        character_records: list[dict[str, Any]] | None = None,
    ) -> dict[str, str]:
        plan = self._episode_plan_for_session(session)
        if not plan:
            return {}
        participants = plan.get("participants") if isinstance(plan.get("participants"), list) else []
        character_ids = self._episode_character_ids_for_session(
            session,
            character_records=character_records,
        )
        mapping: dict[str, str] = {}
        for index, participant in enumerate(participants):
            if not isinstance(participant, dict) or index >= len(character_ids):
                continue
            participant_id = str(participant.get("participant_id") or "").strip()
            character_id = str(character_ids[index] or "").strip()
            if participant_id and character_id:
                mapping[participant_id] = character_id
        return mapping

    def _episode_speaker_policy_for_turn(
        self,
        session: dict[str, Any],
        turn: dict[str, Any],
        *,
        character_records: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        speaker = turn.get("speaker_policy") if isinstance(turn.get("speaker_policy"), dict) else {}
        projected = copy.deepcopy(speaker)
        allowed_participant_ids = [
            str(item).strip()
            for item in speaker.get("allowed_participant_ids") or []
            if str(item).strip()
        ]
        if not allowed_participant_ids:
            projected.setdefault("allowed_character_ids", [])
            return projected
        mapping = self._episode_participant_character_map_for_session(
            session,
            character_records=character_records,
        )
        allowed_character_ids = [
            mapping[participant_id]
            for participant_id in allowed_participant_ids
            if mapping.get(participant_id)
        ]
        missing = [participant_id for participant_id in allowed_participant_ids if participant_id not in mapping]
        if missing:
            raise RuntimeError(
                "企劃角色對應失敗：speaker_policy.allowed_participant_ids "
                f"無法對應實際角色：{', '.join(missing)}"
            )
        projected["allowed_character_ids"] = list(dict.fromkeys(allowed_character_ids))
        return projected

    def _episode_character_ids_for_turn(
        self,
        session: dict[str, Any],
        turn: dict[str, Any],
        *,
        character_records: list[dict[str, Any]] | None = None,
    ) -> list[str]:
        all_character_ids = self._episode_character_ids_for_session(
            session,
            character_records=character_records,
        )
        speaker = turn.get("speaker_policy") if isinstance(turn.get("speaker_policy"), dict) else {}
        allowed_participant_ids = [
            str(item).strip()
            for item in speaker.get("allowed_participant_ids") or []
            if str(item).strip()
        ]
        if not allowed_participant_ids:
            return all_character_ids
        projected = self._episode_speaker_policy_for_turn(
            session,
            turn,
            character_records=character_records,
        )
        allowed_character_ids = [
            str(item).strip()
            for item in projected.get("allowed_character_ids") or []
            if str(item).strip()
        ]
        return allowed_character_ids or all_character_ids

    @staticmethod
    def _episode_dialogue_policy(turn: dict[str, Any]) -> dict[str, Any]:
        return dialogue_policy_for_turn(turn if isinstance(turn, dict) else {})

    @staticmethod
    def _episode_dialogue_reply_label(policy: dict[str, Any]) -> str:
        min_replies = int(policy.get("min_replies") or 1)
        max_replies = int(policy.get("max_replies") or min_replies)
        if min_replies == max_replies:
            return str(max_replies)
        return f"{min_replies}-{max_replies}"

    @staticmethod
    def _episode_plan_group_turn_limit(
        session: dict[str, Any],
        turn_type: str,
        dialogue_policy: dict[str, Any],
    ) -> int:
        try:
            configured_limit = int(session.get("director_group_turn_limit", 3) or 3)
        except (TypeError, ValueError):
            configured_limit = 3
        try:
            policy_limit = int(dialogue_policy.get("max_replies") or 1)
        except (TypeError, ValueError):
            policy_limit = 1
        if str(turn_type or "").strip() in {"opening", "cohost_intro"}:
            policy_limit = min(policy_limit, 1)
        return max(1, min(policy_limit, configured_limit, 4))

    def _episode_next_turn_preview(
        self,
        plan: dict[str, Any],
        planned_state: dict[str, Any],
    ) -> dict[str, str]:
        segments = plan.get("segments") if isinstance(plan.get("segments"), list) else []
        segment_index = int(planned_state.get("current_segment_index") or 0)
        turn_index = int(planned_state.get("current_turn_index") or 0)
        if segment_index < 0 or segment_index >= len(segments):
            return {}
        segment = segments[segment_index] if isinstance(segments[segment_index], dict) else {}
        turns = segment.get("planned_turn_contracts") if isinstance(segment.get("planned_turn_contracts"), list) else []
        next_segment = segment
        next_turn_index = turn_index + 1
        if next_turn_index >= len(turns):
            segment_index += 1
            if segment_index < 0 or segment_index >= len(segments):
                return {}
            next_segment = segments[segment_index] if isinstance(segments[segment_index], dict) else {}
            turns = (
                next_segment.get("planned_turn_contracts")
                if isinstance(next_segment.get("planned_turn_contracts"), list)
                else []
            )
            next_turn_index = 0
        if next_turn_index < 0 or next_turn_index >= len(turns):
            return {}
        next_turn = turns[next_turn_index] if isinstance(turns[next_turn_index], dict) else {}
        return {
            "segment_id": str(next_segment.get("segment_id") or ""),
            "turn_id": str(next_turn.get("turn_id") or ""),
            "turn_type": str(next_turn.get("turn_type") or ""),
            "intent": str(next_turn.get("intent") or ""),
        }

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
        if str(planned_state.get("plan_status") or "") == "completed":
            return None
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
        claim_policy = (
            completed_turn.get("claim_policy")
            if isinstance(completed_turn.get("claim_policy"), dict)
            else {}
        )
        used_claim_ids = [
            str(item).strip()
            for item in memory.get("used_claim_ids") or []
            if str(item).strip()
        ]
        for claim_id in claim_policy.get("new_claim_ids") or []:
            text = str(claim_id).strip()
            if text and text not in used_claim_ids:
                used_claim_ids.append(text)
        memory["used_claim_ids"] = used_claim_ids[:50]
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
        if any(token in label for token in ("hostile", "abuse", "toxic", "harass")):
            return {
                "event_type": "hostile",
                "action": str(actions.get("hostile") or "ignore_or_deescalate"),
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
        *,
        batch_events: list[dict[str, Any]] | None = None,
        backlog_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if not self._is_public_live_event_displayable(event):
            return None
        classified = self._classify_episode_audience_event(plan, event)
        action = classified["action"]
        event_type = classified["event_type"]
        interrupt_state = self._interrupt_state_for_audience_event(
            plan,
            planned_state,
            event,
            event_type,
            action,
        )
        selected_events = [item for item in batch_events or [event] if isinstance(item, dict)]
        source_event_ids = [
            int(item.get("id") or 0)
            for item in selected_events
            if int(item.get("id") or 0)
        ]
        if source_event_ids:
            interrupt_state["source_event_ids"] = source_event_ids
        prompt_lines = []
        for item in selected_events:
            author = str(item.get("author_display_name") or "匿名觀眾").strip() or "匿名觀眾"
            text = str(item.get("safe_message_text") or "").strip()
            if not text:
                continue
            if str(item.get("priority_class") or "") == "super_chat":
                amount = str(item.get("amount_display_string") or "").strip()
                prefix = f"[SC {amount}] " if amount else "[SC] "
                prompt_lines.append(f"{prefix}{author}: {text}")
            else:
                prompt_lines.append(f"{author}: {text}")
        director_action = "reply_super_chat_batch" if event_type == "super_chat" else "reply_chat_batch"
        return {
            "action": director_action,
            "reason": f"episode audience event: {event_type}",
            "prompt": "\n".join(prompt_lines)[:2000] or str(event.get("safe_message_text") or "")[:500],
            "current_topic": "",
            "episode_plan": {
                "mode": "audience_interrupt",
                "event_type": event_type,
                "event_action": action,
                "interrupt_state": interrupt_state,
                "classification_reason": classified["reason"],
                "backlog_snapshot": backlog_snapshot or {},
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
        if str(planned_state.get("plan_status") or "") == "completed":
            return {}
        turn = self._episode_current_turn_contract(plan, planned_state)
        segment = self._episode_current_segment(plan, planned_state)
        if not turn or not segment:
            return {}
        projected_turn, audience_event_context = self._episode_project_turn_for_audience_availability(
            session,
            planned_state,
            turn,
        )
        return {
            "action": "continue_topic",
            "reason": f"episode planned turn {projected_turn['turn_id']}",
            "prompt": str(projected_turn.get("intent") or segment.get("goal") or ""),
            "current_topic": str(segment.get("title") or ""),
            "episode_plan": {
                "mode": "planned_turn",
                "planned_state": planned_state,
                "audience_event_context": audience_event_context,
                "segment": {
                    "segment_id": str(segment.get("segment_id") or ""),
                    "title": str(segment.get("title") or ""),
                    "goal": str(segment.get("goal") or ""),
                },
                "turn_contract": projected_turn,
            },
        }

    def _episode_plan_context_text(
        self,
        plan: dict[str, Any],
        planned_state: dict[str, Any],
        turn: dict[str, Any],
        *,
        interrupt_state: dict[str, Any],
        speaker_policy: dict[str, Any] | None = None,
        next_turn_preview: dict[str, str] | None = None,
        audience_event_context: dict[str, Any] | None = None,
    ) -> str:
        segment = self._episode_current_segment(plan, planned_state) or {}
        speaker = speaker_policy if isinstance(speaker_policy, dict) else (
            turn.get("speaker_policy") if isinstance(turn.get("speaker_policy"), dict) else {}
        )
        evidence = turn.get("evidence_policy") if isinstance(turn.get("evidence_policy"), dict) else {}
        output = (
            turn.get("output_requirements")
            if isinstance(turn.get("output_requirements"), dict)
            else {}
        )
        dialogue_policy = self._episode_dialogue_policy(turn)
        forbidden = (
            turn.get("forbidden_repetition")
            if isinstance(turn.get("forbidden_repetition"), dict)
            else {}
        )
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
        memory = (
            planned_state.get("segment_memory")
            if isinstance(planned_state.get("segment_memory"), dict)
            else initial_segment_memory()
        )
        forbidden_next_repeats = [
            str(item).strip()
            for item in memory.get("forbidden_next_repeats") or []
            if str(item).strip()
        ]
        covered_claims = [
            str(item).strip()
            for item in memory.get("covered_claims") or []
            if str(item).strip()
        ]
        used_claim_ids = [
            str(item).strip()
            for item in memory.get("used_claim_ids") or []
            if str(item).strip()
        ]
        claim_policy = turn.get("claim_policy") if isinstance(turn.get("claim_policy"), dict) else {}
        new_claim_ids = [
            str(item).strip()
            for item in claim_policy.get("new_claim_ids") or []
            if str(item).strip()
        ]
        forbidden_claim_ids = [
            str(item).strip()
            for item in claim_policy.get("forbidden_claim_ids") or []
            if str(item).strip()
        ]
        must_not_paraphrase_claims = bool(claim_policy.get("must_not_paraphrase_used_claims"))
        claim_catalog = self._episode_claim_catalog(plan)
        max_cards = self._episode_evidence_max_cards(evidence)
        dialogue_lines = self._episode_dialogue_context_lines(dialogue_policy)
        lines = [
            "<live_episode_turn_context>",
            f"本輪類型：{turn.get('turn_type') or '未指定'}",
            f"本輪目標：{turn.get('intent') or segment.get('goal') or '未指定'}",
            "角色功能：" + (", ".join(preferred_functions) if preferred_functions else "依角色設定自然接話"),
            *dialogue_lines,
            *self._episode_segment_rhythm_context_lines(segment, turn),
            *self._episode_focus_context_lines(plan, turn),
            *self._episode_recommendation_context_lines(plan, turn),
            *self._episode_stance_context_lines(turn),
            *self._episode_evidence_brief_context_lines(turn),
            self._episode_output_context_line(output),
            "交接要求："
            f"{'需要' if bool(output.get('should_handoff')) else '不需要'}；"
            f"交接功能：{output.get('handoff_target_function') or '未指定'}",
            "句型與用詞獨立：只參考前文內容，不模仿前文標點、用詞、節奏、句型或修辭骨架。",
        ]
        audience_context = audience_event_context if isinstance(audience_event_context, dict) else {}
        if str(audience_context.get("status") or "") == "empty_fallback":
            lines.append(
                "即時聊天室狀態："
                f"{audience_context.get('instruction') or '目前沒有可用的真實聊天室留言或 Super Chat；禁止杜撰觀眾留言。'}"
                "本輪請使用 fallback 目標，不要假裝已收到觀眾回應。"
            )
        if max_cards > 0:
            lines.append(
                "證據需求：本輪需要導播規劃的查證邊界；"
                f"證據容量上限 {max_cards} 個重點，只能作為事實依據，不是立場或段落策略。"
            )
        else:
            lines.append("證據需求：本輪不使用外部話題卡；請依本輪目標與角色開場/收束要求回應。")
        if required_entities:
            lines.append("必須涵蓋：" + ", ".join(required_entities))
        handoff = turn.get("handoff") if isinstance(turn.get("handoff"), dict) else {}
        handoff_hint = str(handoff.get("next_turn_hint") or "").strip()
        if handoff_hint:
            lines.append(f"交接提示：{handoff_hint}；只提示轉場方向，不要提前完整展開下一輪。")
        else:
            preview = next_turn_preview if isinstance(next_turn_preview, dict) else {}
            preview_type = str(preview.get("turn_type") or "").strip()
            if preview.get("turn_id") and preview_type:
                lines.append(f"交接提示：下一輪類型 {preview_type}；只提示轉場方向，不要提前完整展開下一輪。")
        forbidden_parts = []
        for label, key in (("避免重複主張", "claims"), ("避免重複比喻", "metaphors"), ("避免重複開頭", "openings")):
            values = [str(item).strip() for item in forbidden.get(key) or [] if str(item).strip()]
            if values:
                forbidden_parts.append(f"{label}：" + ", ".join(values))
        if forbidden_parts:
            lines.append("重複限制：" + "；".join(forbidden_parts))
        if used_claim_ids and must_not_paraphrase_claims:
            lines.append(
                "已使用語義主張，禁止改寫重複："
                + self._episode_claim_refs_text(used_claim_ids, claim_catalog)
            )
        if forbidden_claim_ids:
            lines.append(
                "本輪禁止再講的語義主張："
                + self._episode_claim_refs_text(forbidden_claim_ids, claim_catalog)
            )
        if new_claim_ids:
            lines.append(
                "本輪必須使用的新主張："
                + self._episode_claim_refs_text(new_claim_ids, claim_catalog)
            )
        if covered_claims:
            lines.append("已涵蓋主張：" + ", ".join(covered_claims))
        if forbidden_next_repeats:
            lines.append("下一輪不可重複：" + ", ".join(forbidden_next_repeats))
        if interrupt_state:
            lines.append(f"觀眾打斷：{interrupt_state.get('interrupt_type') or '未指定'}")
            lines.append(f"回復規則：{interrupt_state.get('resume_rule') or '處理後回到原企劃節奏'}")
        else:
            lines.append("回復規則：本輪不是聊天室打斷，完成後依企劃段落節奏推進。")
        lines.append("</live_episode_turn_context>")
        return "\n".join(lines)

    @staticmethod
    def _episode_output_context_line(output: dict[str, Any]) -> str:
        try:
            max_sentences = int(output.get("max_sentences") or 2)
        except (TypeError, ValueError):
            max_sentences = 2
        max_sentences = max(1, min(max_sentences, 8))
        must_end_with_question = bool(output.get("must_end_with_question"))
        allow_audience_question = bool(output.get("allow_audience_question"))
        if must_end_with_question and allow_audience_question:
            ending_rule = "結尾必須是可回應真實觀眾事件的問句。"
        elif must_end_with_question:
            ending_rule = "結尾若用問句，只能問交接角色或作為下一段轉場，不得問觀眾。"
        elif allow_audience_question:
            ending_rule = "可向觀眾提問，但只能在本輪正在回應真實留言或 Super Chat 時使用。"
        else:
            ending_rule = "不要求問句結尾；不得向觀眾提問。"
        return f"輸出限制：最多句數：{max_sentences}；{ending_rule}"

    @staticmethod
    def _episode_claim_catalog(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
        ledger = plan.get("claim_ledger") if isinstance(plan.get("claim_ledger"), dict) else {}
        raw_claims = ledger.get("semantic_claims") or ledger.get("used_claims") or []
        catalog: dict[str, dict[str, Any]] = {}
        for raw_claim in raw_claims:
            if not isinstance(raw_claim, dict):
                continue
            claim_id = str(raw_claim.get("claim_id") or "").strip()
            if not claim_id:
                continue
            catalog[claim_id] = raw_claim
        return catalog

    @staticmethod
    def _episode_claim_refs_text(
        claim_ids: list[str],
        claim_catalog: dict[str, dict[str, Any]],
    ) -> str:
        parts: list[str] = []
        for claim_id in claim_ids:
            claim = claim_catalog.get(claim_id) or {}
            meaning = str(claim.get("meaning") or "").strip()
            if meaning:
                parts.append(f"{claim_id}：{meaning}")
            else:
                parts.append(claim_id)
        return "；".join(parts)

    @staticmethod
    def _episode_focus_target_catalog(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
        targets = plan.get("focus_targets") if isinstance(plan.get("focus_targets"), list) else []
        catalog: dict[str, dict[str, Any]] = {}
        for raw_target in targets:
            if not isinstance(raw_target, dict):
                continue
            target_id = str(raw_target.get("target_id") or "").strip()
            if not target_id:
                continue
            catalog[target_id] = raw_target
        return catalog

    def _episode_focus_context_lines(
        self,
        plan: dict[str, Any],
        turn: dict[str, Any],
    ) -> list[str]:
        policy = turn.get("focus_policy") if isinstance(turn.get("focus_policy"), dict) else {}
        if not policy:
            return []
        catalog = self._episode_focus_target_catalog(plan)
        target_ids = [
            str(item).strip()
            for item in policy.get("target_ids") or []
            if str(item).strip()
        ]
        target_parts: list[str] = []
        for target_id in target_ids:
            target = catalog.get(target_id) or {}
            label = str(target.get("label") or target_id).strip()
            target_type = str(target.get("target_type") or "focus_target").strip()
            reason = str(target.get("selection_reason") or "").strip()
            analysis_angles = [
                str(item).strip()
                for item in target.get("analysis_angles") or []
                if str(item).strip()
            ]
            recommendation_axes = [
                str(item).strip()
                for item in target.get("recommendation_axes") or []
                if str(item).strip()
            ]
            details = [f"{target_id}（{target_type}：{label}"]
            if reason:
                details.append(f"選入理由：{reason}")
            if analysis_angles:
                details.append("可用深挖角度：" + ", ".join(analysis_angles[:4]))
            if recommendation_axes:
                details.append("推薦判斷軸：" + ", ".join(recommendation_axes[:4]))
            target_parts.append("；".join(details) + "）")
        depth_goal = str(policy.get("depth_goal") or "").strip()
        must_cover = [
            str(item).strip()
            for item in policy.get("must_cover") or []
            if str(item).strip()
        ]
        recommendation_mode = str(policy.get("recommendation_mode") or "").strip()
        lines = ["焦點對象控制："]
        if target_parts:
            lines.append("本輪焦點：" + "；".join(target_parts))
        if depth_goal:
            lines.append(f"深挖目標：{depth_goal}")
        if must_cover:
            lines.append("必須覆蓋角度：" + ", ".join(must_cover[:4]))
        if bool(policy.get("avoid_generic_reframe")):
            lines.append(
                "避免泛泛重框：不要回到抽象框架或泛泛選擇原則；"
                "必須停留在本輪焦點對象的具體特徵、取捨或使用情境。"
            )
        if recommendation_mode:
            lines.append(f"推薦模式：{recommendation_mode}")
        if str(turn.get("turn_type") or "").strip() == "personal_recommendation":
            lines.append(
                "主觀推薦規則：允許角色以個人偏好推薦，不需偽裝中立；"
                "但必須說清楚推薦給誰、推薦理由、避雷條件。"
            )
        return lines

    def _episode_recommendation_context_lines(
        self,
        plan: dict[str, Any],
        turn: dict[str, Any],
    ) -> list[str]:
        policy = (
            turn.get("recommendation_policy")
            if isinstance(turn.get("recommendation_policy"), dict)
            else {}
        )
        if not policy:
            return []
        catalog = self._episode_focus_target_catalog(plan)
        lines = ["角色具體推薦："]
        style = str(policy.get("recommendation_style") or "").strip()
        if style:
            lines.append(f"推薦風格：{style}")
        ranked_order = [
            str(item).strip()
            for item in policy.get("ranked_order") or []
            if str(item).strip()
        ]
        if ranked_order:
            labels = [
                str((catalog.get(target_id) or {}).get("label") or target_id).strip()
                for target_id in ranked_order
            ]
            lines.append("推薦排序：" + " > ".join(labels))
        recommendations = (
            policy.get("recommendations")
            if isinstance(policy.get("recommendations"), list)
            else []
        )
        for raw_recommendation in recommendations:
            if not isinstance(raw_recommendation, dict):
                continue
            target_id = str(raw_recommendation.get("target_id") or "").strip()
            target = catalog.get(target_id) or {}
            label = str(target.get("label") or target_id or "未指定焦點").strip()
            best_for = str(raw_recommendation.get("best_for") or "").strip()
            why = str(raw_recommendation.get("why") or "").strip()
            avoid_if = str(raw_recommendation.get("avoid_if") or "").strip()
            personal_bias = str(raw_recommendation.get("personal_bias") or "").strip()
            parts = []
            if best_for:
                parts.append(f"推薦給{best_for}")
            if why:
                parts.append(f"理由：{why}")
            if avoid_if:
                parts.append(f"避雷：{avoid_if}")
            if personal_bias:
                parts.append(f"個人偏好：{personal_bias}")
            if parts:
                lines.append(f"{label}：" + "；".join(parts))
        return lines

    @staticmethod
    def _episode_evidence_brief_context_lines(turn: dict[str, Any]) -> list[str]:
        brief = turn.get("evidence_brief") if isinstance(turn.get("evidence_brief"), dict) else {}
        if not brief:
            return []
        facts = [
            str(item).strip()
            for item in brief.get("facts_to_state") or []
            if str(item).strip()
        ]
        boundaries = [
            str(item).strip()
            for item in brief.get("source_boundaries") or []
            if str(item).strip()
        ]
        lines = ["企劃內嵌事實摘要："]
        if facts:
            lines.append("可直接使用的事實：")
            lines.extend(f"- {fact}" for fact in facts[:6])
        if boundaries:
            lines.append("來源邊界：")
            lines.extend(f"- {boundary}" for boundary in boundaries[:4])
        if bool(brief.get("do_not_delegate_to_character")):
            lines.append(
                "查證責任邊界：上述摘要已由企劃層從來源工件整理完成；"
                "不得把查證責任推給角色，不要在台詞中提到 FactCards、來源卡或自己正在查資料。"
            )
        return lines

    @staticmethod
    def _episode_stance_context_lines(turn: dict[str, Any]) -> list[str]:
        policy = turn.get("stance_policy") if isinstance(turn.get("stance_policy"), dict) else {}
        if not policy:
            return []
        phrases = [
            str(item).strip()
            for item in policy.get("avoid_disclaimer_phrases") or []
            if str(item).strip()
        ]
        lines = [
            "立場強度控制：",
            f"立場模式：{str(policy.get('stance_mode') or '').strip() or 'assertive'}",
            "必須站邊："
            f"{bool(policy.get('must_take_side'))}；"
            f"免責聲明預算：{int(policy.get('disclaimer_budget') or 0)}",
        ]
        if phrases:
            lines.append("本輪不要使用的安全退路：" + ", ".join(phrases))
        edge_instruction = str(policy.get("edge_instruction") or "").strip()
        if edge_instruction:
            lines.append(f"進攻角度：{edge_instruction}")
        lines.append(
            "反平板規則：不要用『每個人喜好不同』、『僅供參考』或同義句作為固定開場/結尾；"
            "若需要承認差異，只能服務本輪具體推薦或取捨。"
        )
        return lines

    @staticmethod
    def _episode_dialogue_context_lines(dialogue_policy: dict[str, Any]) -> list[str]:
        try:
            max_replies = int(dialogue_policy.get("max_replies") or 1)
        except (TypeError, ValueError):
            max_replies = 1
        max_replies = max(1, min(max_replies, 4))
        autonomy = str(dialogue_policy.get("autonomy") or "guided").strip() or "guided"
        lines = [
            "對話彈性：",
            f"自主度：{autonomy}",
            f"本段最多 {max_replies} 次角色發言；這是硬上限，不是必須用完。",
            "本次角色任務：提出本輪核心資訊或主觀點；不得一次講完整段落。",
            "接力煞車：若無新資訊，短收束並推進。",
        ]
        return lines

    @staticmethod
    def _episode_segment_rhythm_context_lines(
        segment: dict[str, Any],
        turn: dict[str, Any],
    ) -> list[str]:
        rhythm = (
            segment.get("rhythm_control")
            if isinstance(segment.get("rhythm_control"), dict)
            else {}
        )
        evidence = (
            turn.get("evidence_policy")
            if isinstance(turn.get("evidence_policy"), dict)
            else {}
        )
        completion = (
            segment.get("completion_conditions")
            if isinstance(segment.get("completion_conditions"), dict)
            else {}
        )
        discussion_goal = str(
            rhythm.get("discussion_goal")
            or segment.get("goal")
            or turn.get("intent")
            or "完成本段規劃目標"
        ).strip()
        data_points = [
            str(item).strip()
            for item in rhythm.get("data_points") or []
            if str(item).strip()
        ]
        if not data_points:
            data_points = [
                str(item).strip()
                for item in evidence.get("required_entities") or []
                if str(item).strip()
            ]
        if not data_points:
            data_points = [
                str(item).strip()
                for item in evidence.get("queries") or []
                if str(item).strip()
            ][:2]
        audience_understanding = str(
            rhythm.get("audience_understanding")
            or "觀眾能抓住本段目標與下一步判斷方向，不需要聽完整資料清單。"
        ).strip()
        close_when = [
            str(item).strip()
            for item in rhythm.get("close_when") or []
            if str(item).strip()
        ]
        if not close_when:
            required_types = [
                str(item).strip()
                for item in completion.get("required_turn_types") or []
                if str(item).strip()
            ]
            max_turns = completion.get("max_planned_turns")
            if required_types:
                close_when.append("已涵蓋必要 turn 類型：" + ", ".join(required_types))
            if max_turns:
                close_when.append(f"達到本段最多 {max_turns} 個 planned turns")
            close_when.append("同一觀點、比喻類型或結論已經出現兩次")
        return [
            "段落節奏煞車：",
            f"本段討論目標：{discussion_goal}",
            "需要使用的資料點："
            + (", ".join(data_points) if data_points else "依本輪目標取最少必要資料")
            + "；資料點只作為素材，不要求逐句覆蓋。",
            f"本段應達成的觀眾理解：{audience_understanding}",
            "收束提示：必要內容已完成或同一觀點開始重複時，請用一句短收束語推進下一輪。",
        ]

    @staticmethod
    def _episode_evidence_max_cards(evidence: dict[str, Any]) -> int:
        raw_value = evidence.get("max_cards") if isinstance(evidence, dict) else None
        if raw_value is None:
            return 3
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            return 3
        return max(0, min(value, 8))

    @staticmethod
    def _episode_turn_is_audience_event_dependent(turn: dict[str, Any]) -> bool:
        policy = turn.get("audience_event_policy") if isinstance(turn.get("audience_event_policy"), dict) else {}
        if isinstance(policy.get("requires_real_events"), bool):
            return policy["requires_real_events"]
        turn_type = str(turn.get("turn_type") or "").strip()
        return turn_type in {"chat_bridge", "audience_answer"}

    @staticmethod
    def _episode_empty_audience_fallback_intent(turn: dict[str, Any]) -> str:
        policy = turn.get("audience_event_policy") if isinstance(turn.get("audience_event_policy"), dict) else {}
        fallback = str(policy.get("empty_fallback_intent") or "").strip()
        if fallback:
            return fallback
        return (
            "目前沒有可用的真實聊天室留言或 Super Chat；不要引用、承認、感謝或杜撰任何具體觀眾發言。"
            "請改用一個具體觀眾情境或選擇壓力推進本輪主題，不要加通用偏好免責聲明。"
        )

    def _episode_completed_audience_events(
        self,
        session_id: str,
        planned_state: dict[str, Any],
        *,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        handled_event_ids = {
            int(event_id)
            for event_id in (
                (planned_state.get("segment_memory") or {}).get("audience_reactions")
                or []
            )
            if str(event_id).isdigit()
        }
        recent_events = self.storage.list_events(session_id, limit=limit)
        return [
            event
            for event in recent_events
            if int(event.get("id") or 0) not in handled_event_ids
            and not str(event.get("injected_at") or "").strip()
            and str(event.get("safety_status") or "") == "completed"
            and str(event.get("status") or "active") == "active"
            and str(event.get("safe_message_text") or "").strip()
            and self._is_public_live_event_displayable(event)
        ]

    @staticmethod
    def _episode_backpressure_int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        return max(minimum, min(parsed, maximum))

    def _episode_audience_backlog_snapshot(
        self,
        events: list[dict[str, Any]],
        selected_events: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        displayable = [event for event in events if self._is_public_live_event_displayable(event)]
        selected_ids = {
            int(event.get("id") or 0)
            for event in selected_events or []
            if int(event.get("id") or 0)
        }
        super_chat_count = sum(1 for event in displayable if str(event.get("priority_class") or "") == "super_chat")
        normal_count = len(displayable) - super_chat_count
        latest_event_id = max((int(event.get("id") or 0) for event in displayable), default=0)
        return {
            "total_count": len(displayable),
            "normal_count": normal_count,
            "super_chat_count": super_chat_count,
            "selected_count": len(selected_ids),
            "deferred_event_count": max(0, len(displayable) - len(selected_ids)),
            "latest_event_id": latest_event_id,
        }

    def _episode_select_audience_event_batch(
        self,
        session: dict[str, Any],
        events: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        displayable = [event for event in events if self._is_public_live_event_displayable(event)]
        super_chats = [event for event in displayable if str(event.get("priority_class") or "") == "super_chat"]
        if super_chats:
            max_sc = self._episode_backpressure_int(
                session.get("max_sc_per_batch", 5),
                5,
                minimum=1,
                maximum=30,
            )
            super_chats.sort(key=lambda item: (-int(item.get("sc_tier", 0) or 0), int(item.get("id", 0) or 0)))
            return super_chats[:max_sc]
        max_events = self._episode_backpressure_int(
            session.get("max_pending_events", 12),
            12,
            minimum=1,
            maximum=200,
        )
        normal = [event for event in displayable if str(event.get("priority_class") or "") != "super_chat"]
        normal.sort(key=lambda item: int(item.get("id", 0) or 0))
        return normal[:max_events]

    def _episode_preprocess_requested_event_batch(
        self,
        session: dict[str, Any],
        state: dict[str, Any],
        events: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
        raw_ids = metadata.get("audience_preprocess_requested_event_ids")
        if not isinstance(raw_ids, list) or not raw_ids:
            return []
        requested_ids: list[int] = []
        for event_id in raw_ids:
            try:
                parsed = int(event_id)
            except (TypeError, ValueError):
                continue
            if parsed > 0 and parsed not in requested_ids:
                requested_ids.append(parsed)
        if not requested_ids:
            return []
        event_by_id = {
            int(event.get("id") or 0): event
            for event in events
            if int(event.get("id") or 0)
        }
        requested_events = [
            event_by_id[event_id]
            for event_id in requested_ids
            if event_id in event_by_id
        ]
        if not requested_events:
            self.storage.update_director_state(session["session_id"], metadata={
                "audience_preprocess_requested_event_ids": [],
                "audience_preprocess_requested_source": "",
                "audience_preprocess_requested_at": "",
                "audience_preprocess_request_cleared_at": datetime.now().isoformat(),
                "audience_preprocess_request_clear_reason": "no_eligible_requested_events",
            })
            return []
        return self._episode_select_audience_event_batch(session, requested_events)

    def _episode_audience_batch_block_reason(
        self,
        session: dict[str, Any],
        state: dict[str, Any],
        selected_events: list[dict[str, Any]],
        *,
        audience_last_key: str,
        sc_last_key: str,
    ) -> str:
        if not selected_events:
            return "no_selected_events"
        metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
        max_batches = self._episode_backpressure_int(
            session.get("director_max_audience_batches_per_planned_turn", 1),
            1,
            minimum=0,
            maximum=20,
        )
        used_batches = self._episode_backpressure_int(
            metadata.get("audience_batches_since_planned_turn", 0),
            0,
            minimum=0,
            maximum=100,
        )
        if used_batches >= max_batches:
            return "planned_turn_batch_limit"
        has_sc = any(str(event.get("priority_class") or "") == "super_chat" for event in selected_events)
        if has_sc:
            cooldown = self._episode_backpressure_int(
                session.get("sc_interrupt_cooldown_seconds", 30),
                30,
                minimum=0,
                maximum=3600,
            )
            last_key = sc_last_key
        else:
            cooldown = self._episode_backpressure_int(
                session.get("director_audience_interrupt_cooldown_seconds", 30),
                30,
                minimum=0,
                maximum=3600,
            )
            last_key = audience_last_key
        last_at = self._parse_iso_datetime(metadata.get(last_key))
        if last_at and cooldown > 0 and (datetime.now() - last_at).total_seconds() < cooldown:
            return "interrupt_cooldown"
        return ""

    def _episode_audience_interrupt_block_reason(
        self,
        session: dict[str, Any],
        state: dict[str, Any],
        selected_events: list[dict[str, Any]],
    ) -> str:
        return self._episode_audience_batch_block_reason(
            session,
            state,
            selected_events,
            audience_last_key="last_audience_interrupt_at",
            sc_last_key="last_sc_interrupt_at",
        )

    def _episode_audience_gap_block_reason(
        self,
        session: dict[str, Any],
        state: dict[str, Any],
        selected_events: list[dict[str, Any]],
    ) -> str:
        return self._episode_audience_batch_block_reason(
            session,
            state,
            selected_events,
            audience_last_key="last_audience_gap_at",
            sc_last_key="last_sc_gap_at",
        )

    def _episode_project_turn_for_audience_availability(
        self,
        session: dict[str, Any],
        planned_state: dict[str, Any],
        turn: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        projected = copy.deepcopy(turn)
        requires_real_events = self._episode_turn_is_audience_event_dependent(projected)
        if not requires_real_events:
            return projected, {"requires_real_events": False, "status": "not_required"}

        events = self._episode_completed_audience_events(
            str(session.get("session_id") or ""),
            planned_state,
            limit=20,
        )
        if events:
            return projected, {
                "requires_real_events": True,
                "status": "available",
                "available_event_count": len(events),
            }

        projected["intent"] = self._episode_empty_audience_fallback_intent(projected)
        output = (
            copy.deepcopy(projected.get("output_requirements"))
            if isinstance(projected.get("output_requirements"), dict)
            else {}
        )
        output["must_end_with_question"] = False
        output["allow_audience_question"] = False
        projected["output_requirements"] = output
        return projected, {
            "requires_real_events": True,
            "status": "empty_fallback",
            "available_event_count": 0,
            "instruction": (
                "目前沒有可用的真實聊天室留言或 Super Chat；禁止杜撰觀眾留言、Super Chat、"
                "觀眾名稱或「剛剛有人說」。"
            ),
        }

    def _episode_plan_external_context_patch(
        self,
        session: dict[str, Any],
        state: dict[str, Any],
        decision: dict[str, Any],
        *,
        character_records: list[dict[str, Any]] | None = None,
    ) -> tuple[dict[str, Any], str, str]:
        payload = decision.get("episode_plan") if isinstance(decision.get("episode_plan"), dict) else {}
        if not payload:
            return {}, "", ""
        plan, planned_state = self._episode_plan_and_state(session, state)
        if not plan:
            return {}, "", ""
        turn = (
            payload.get("turn_contract")
            if isinstance(payload.get("turn_contract"), dict)
            else self._episode_current_turn_contract(plan, planned_state)
        )
        if not turn:
            return {}, "", ""
        interrupt_state = (
            payload.get("interrupt_state")
            if isinstance(payload.get("interrupt_state"), dict)
            else {}
        )
        audience_event_context = (
            payload.get("audience_event_context")
            if isinstance(payload.get("audience_event_context"), dict)
            else {}
        )
        if not audience_event_context:
            turn, audience_event_context = self._episode_project_turn_for_audience_availability(
                session,
                planned_state,
                turn,
            )
        segment_payload = payload.get("segment") if isinstance(payload.get("segment"), dict) else {}
        current = self._episode_current_segment(plan, planned_state) or {}
        speaker_policy = self._episode_speaker_policy_for_turn(
            session,
            turn,
            character_records=character_records,
        )
        dialogue_policy = self._episode_dialogue_policy(turn)
        context_turn = turn
        expansion_enabled = getattr(
            self,
            "_director_dialogue_expansion_enabled",
            lambda _session: True,
        )(session)
        if not expansion_enabled:
            dialogue_policy = {
                **dialogue_policy,
                "min_replies": 1,
                "max_replies": 1,
            }
            context_turn = copy.deepcopy(turn)
            context_turn["dialogue_policy"] = dialogue_policy
        next_turn_preview = self._episode_next_turn_preview(plan, planned_state)
        context_text = self._episode_plan_context_text(
            plan,
            planned_state,
            context_turn,
            interrupt_state=interrupt_state,
            speaker_policy=speaker_policy,
            next_turn_preview=next_turn_preview,
            audience_event_context=audience_event_context,
        )
        topic_context = ""
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
                "turn_contract": {
                    "turn_id": str(turn.get("turn_id") or ""),
                    "turn_type": str(turn.get("turn_type") or ""),
                    "intent": str(turn.get("intent") or ""),
                },
                "speaker_policy": speaker_policy,
                "dialogue_policy": dialogue_policy,
                "focus_policy": copy.deepcopy(
                    turn.get("focus_policy")
                    if isinstance(turn.get("focus_policy"), dict)
                    else {}
                ),
                "recommendation_policy": copy.deepcopy(
                    turn.get("recommendation_policy")
                    if isinstance(turn.get("recommendation_policy"), dict)
                    else {}
                ),
                "stance_policy": copy.deepcopy(
                    turn.get("stance_policy")
                    if isinstance(turn.get("stance_policy"), dict)
                    else {}
                ),
                "handoff": copy.deepcopy(
                    turn.get("handoff")
                    if isinstance(turn.get("handoff"), dict)
                    else {}
                ),
                "evidence_brief": copy.deepcopy(
                    turn.get("evidence_brief")
                    if isinstance(turn.get("evidence_brief"), dict)
                    else {}
                ),
                "next_turn_preview": next_turn_preview,
                "audience_event_context": audience_event_context,
                "output_requirements": copy.deepcopy(
                    turn.get("output_requirements")
                    if isinstance(turn.get("output_requirements"), dict)
                    else {}
                ),
                "forbidden_repetition": copy.deepcopy(
                    turn.get("forbidden_repetition")
                    if isinstance(turn.get("forbidden_repetition"), dict)
                    else {}
                ),
                "evidence_policy": copy.deepcopy(
                    turn.get("evidence_policy")
                    if isinstance(turn.get("evidence_policy"), dict)
                    else {}
                ),
                "segment_memory": copy.deepcopy(
                    planned_state.get("segment_memory")
                    if isinstance(planned_state.get("segment_memory"), dict)
                    else initial_segment_memory()
                ),
                "interrupt_state": interrupt_state,
            }
        }
        return patch, context_text, topic_context

    def _episode_plan_next_decision(
        self,
        session: dict[str, Any],
        state: dict[str, Any],
    ) -> dict[str, Any] | None:
        plan, planned_state = self._episode_plan_and_state(session, state)
        if not plan:
            return None
        if str(planned_state.get("plan_status") or "") == "completed":
            return {}
        metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
        interrupt_state = (
            metadata.get("interrupt_state")
            if isinstance(metadata.get("interrupt_state"), dict)
            else {}
        )
        if (
            interrupt_state.get("status") == "handling_audience"
            and int(interrupt_state.get("remaining_interrupt_turns") or 0) > 0
        ):
            return self._episode_planned_turn_decision(session, state)

        completed_events = self._episode_completed_audience_events(
            session["session_id"],
            planned_state,
            limit=500,
        )
        selected_events = self._episode_select_audience_event_batch(session, completed_events)
        snapshot = self._episode_audience_backlog_snapshot(completed_events, selected_events)
        presentation_mode = self._presentation_enabled(session)
        block_reason = (
            self._episode_audience_gap_block_reason(session, state, selected_events)
            if presentation_mode
            else self._episode_audience_interrupt_block_reason(session, state, selected_events)
        )
        if selected_events and not block_reason and not presentation_mode:
            decision = self._episode_interrupt_decision_for_event(
                plan,
                planned_state,
                selected_events[0],
                batch_events=selected_events,
                backlog_snapshot=snapshot,
            )
            if decision:
                return decision
        planned = self._episode_planned_turn_decision(session, state)
        if isinstance(planned, dict) and planned.get("episode_plan"):
            defer_reason = block_reason or "no_interrupt_decision"
            if presentation_mode and selected_events and not block_reason:
                defer_reason = "presentation_audience_gap_lane"
            planned.setdefault("episode_plan", {})["backlog_snapshot"] = {
                **snapshot,
                "defer_reason": defer_reason,
            }
        return planned

    def _episode_plan_next_audience_gap_decision(
        self,
        session: dict[str, Any],
        state: dict[str, Any],
    ) -> dict[str, Any] | None:
        plan, planned_state = self._episode_plan_and_state(session, state)
        if not plan:
            return None
        if str(planned_state.get("plan_status") or "") == "completed":
            return None
        completed_events = self._episode_completed_audience_events(
            session["session_id"],
            planned_state,
            limit=500,
        )
        selected_events = self._episode_select_audience_event_batch(session, completed_events)
        snapshot = self._episode_audience_backlog_snapshot(completed_events, selected_events)
        block_reason = self._episode_audience_gap_block_reason(session, state, selected_events)
        if not selected_events or block_reason:
            return None
        decision = self._episode_interrupt_decision_for_event(
            plan,
            planned_state,
            selected_events[0],
            batch_events=selected_events,
            backlog_snapshot=snapshot,
        )
        if not decision:
            return None
        payload = decision.setdefault("episode_plan", {})
        payload["mode"] = "audience_gap"
        payload["backlog_snapshot"] = snapshot
        return decision

    def _episode_plan_next_audience_prepare_decision(
        self,
        session: dict[str, Any],
        state: dict[str, Any],
    ) -> dict[str, Any] | None:
        plan, planned_state = self._episode_plan_and_state(session, state)
        if not plan:
            return None
        if str(planned_state.get("plan_status") or "") == "completed":
            return None
        completed_events = self._episode_completed_audience_events(
            session["session_id"],
            planned_state,
            limit=500,
        )
        covered_ids: set[int] = set()
        finder = getattr(self.storage, "list_audience_prepare_event_ids", None)
        if callable(finder):
            covered_ids = finder(session["session_id"])
        if covered_ids:
            completed_events = [
                event
                for event in completed_events
                if int(event.get("id") or 0) not in covered_ids
            ]
        selected_events = self._episode_preprocess_requested_event_batch(session, state, completed_events)
        if not selected_events:
            selected_events = self._episode_select_audience_event_batch(session, completed_events)
        snapshot = self._episode_audience_backlog_snapshot(completed_events, selected_events)
        if not selected_events:
            return None
        decision = self._episode_interrupt_decision_for_event(
            plan,
            planned_state,
            selected_events[0],
            batch_events=selected_events,
            backlog_snapshot=snapshot,
        )
        if not decision:
            return None
        payload = decision.setdefault("episode_plan", {})
        payload["mode"] = "audience_gap_prepare"
        payload["backlog_snapshot"] = snapshot
        return decision

    @staticmethod
    def _episode_plan_director_delay_info(
        session: dict[str, Any],
        state: dict[str, Any],
        decision: dict[str, Any],
        idle_seconds: int,
    ) -> dict[str, Any]:
        payload = decision.get("episode_plan") if isinstance(decision.get("episode_plan"), dict) else {}
        mode = str(payload.get("mode") or "").strip()
        if mode == "audience_interrupt":
            return {
                "delay_seconds": 0,
                "reason": "audience_interrupt",
                "label": "觀眾打斷",
            }
        if mode != "planned_turn":
            return {
                "delay_seconds": int(idle_seconds or 60),
                "reason": "director_idle",
                "label": "導播 idle",
            }
        return {
            "delay_seconds": 0,
            "reason": "planned_turn_ready",
            "label": "企劃立即推進",
        }

    @staticmethod
    def _episode_plan_director_delay_seconds(
        session: dict[str, Any],
        state: dict[str, Any],
        decision: dict[str, Any],
        idle_seconds: int,
    ) -> int:
        info = EpisodePlanManagerMixin._episode_plan_director_delay_info(
            session,
            state,
            decision,
            idle_seconds,
        )
        return int(info.get("delay_seconds") or 0)

    def _episode_metadata_after_turn(
        self,
        session: dict[str, Any],
        state: dict[str, Any],
        decision: dict[str, Any],
    ) -> dict[str, Any]:
        payload = decision.get("episode_plan") if isinstance(decision.get("episode_plan"), dict) else {}
        if not payload:
            return {}
        plan, planned_state = self._episode_plan_and_state(session, state)
        if not plan:
            return {}
        mode = str(payload.get("mode") or "")
        metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
        preserved_interrupt_times = {
            key: metadata.get(key)
            for key in ("last_audience_interrupt_at", "last_sc_interrupt_at")
            if metadata.get(key)
        }
        if mode == "planned_turn" and str(planned_state.get("plan_status") or "") == "completed":
            snapshot = self._episode_audience_backlog_snapshot(
                self._episode_completed_audience_events(session["session_id"], planned_state, limit=500),
                [],
            )
            return {
                "planned_state": planned_state,
                "interrupt_state": {"status": "idle"},
                "audience_batches_since_planned_turn": 0,
                "deferred_event_count": int(snapshot.get("total_count") or 0),
                "latest_backlog_snapshot": snapshot,
                **preserved_interrupt_times,
            }
        if mode in {"audience_interrupt", "audience_gap", "audience_gap_prepare"}:
            interrupt_state = (
                payload.get("interrupt_state")
                if isinstance(payload.get("interrupt_state"), dict)
                else {}
            )
            interrupt_state = dict(interrupt_state)
            interrupt_state["remaining_interrupt_turns"] = max(
                0,
                int(interrupt_state.get("remaining_interrupt_turns") or 1) - 1,
            )
            if interrupt_state["remaining_interrupt_turns"] <= 0:
                interrupt_state["status"] = "idle"
            memory = dict(planned_state.get("segment_memory") or initial_segment_memory())
            memory.setdefault("audience_reactions", []).extend(
                interrupt_state.get("source_event_ids") or []
            )
            planned_state["segment_memory"] = memory
            used_batches = self._episode_backpressure_int(
                metadata.get("audience_batches_since_planned_turn", 0),
                0,
                minimum=0,
                maximum=100,
            )
            now = datetime.now().isoformat()
            snapshot = (
                payload.get("backlog_snapshot")
                if isinstance(payload.get("backlog_snapshot"), dict)
                else {}
            )
            interrupt_type = str(interrupt_state.get("interrupt_type") or "")
            update = {
                "planned_state": planned_state,
                "interrupt_state": interrupt_state,
                "audience_batches_since_planned_turn": used_batches + 1,
                "deferred_event_count": int(snapshot.get("deferred_event_count") or 0),
                "latest_backlog_snapshot": snapshot,
            }
            if mode in {"audience_gap", "audience_gap_prepare"}:
                update["last_audience_gap_at"] = now
                if interrupt_type == "super_chat":
                    update["last_sc_gap_at"] = now
            else:
                update["last_audience_interrupt_at"] = now
                if interrupt_type == "super_chat":
                    update["last_sc_interrupt_at"] = now
                elif metadata.get("last_sc_interrupt_at"):
                    update["last_sc_interrupt_at"] = metadata.get("last_sc_interrupt_at")
            return update

        turn = (
            payload.get("turn_contract")
            if isinstance(payload.get("turn_contract"), dict)
            else self._episode_current_turn_contract(plan, planned_state)
        )
        if not turn:
            snapshot = self._episode_audience_backlog_snapshot(
                self._episode_completed_audience_events(session["session_id"], planned_state, limit=500),
                [],
            )
            return {
                "planned_state": planned_state,
                "interrupt_state": {"status": "idle"},
                "audience_batches_since_planned_turn": 0,
                "deferred_event_count": int(snapshot.get("total_count") or 0),
                "latest_backlog_snapshot": snapshot,
                **preserved_interrupt_times,
            }
        next_state = self._planned_state_after_episode_turn(plan, planned_state, turn)
        snapshot = self._episode_audience_backlog_snapshot(
            self._episode_completed_audience_events(session["session_id"], next_state, limit=500),
            [],
        )
        return {
            "planned_state": next_state,
            "interrupt_state": {"status": "idle"},
            "audience_batches_since_planned_turn": 0,
            "deferred_event_count": int(snapshot.get("total_count") or 0),
            "latest_backlog_snapshot": snapshot,
            **preserved_interrupt_times,
        }
