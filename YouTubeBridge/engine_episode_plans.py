"""Plan-aware director helpers for LiveEpisodePlan sessions."""
from __future__ import annotations

import copy
import json
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

    def _episode_character_ids_for_session(self, session: dict[str, Any]) -> list[str]:
        plan = self._episode_plan_for_session(session)
        if not plan:
            return list(session.get("character_ids") or [])
        try:
            return resolve_episode_plan_character_ids(
                plan,
                self._memoria_client().list_characters(),
            )
        except EpisodePlanCharacterBindingError as exc:
            raise RuntimeError(f"企劃角色對應失敗：{exc}") from exc

    def _episode_participant_character_map_for_session(self, session: dict[str, Any]) -> dict[str, str]:
        plan = self._episode_plan_for_session(session)
        if not plan:
            return {}
        participants = plan.get("participants") if isinstance(plan.get("participants"), list) else []
        character_ids = self._episode_character_ids_for_session(session)
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
        mapping = self._episode_participant_character_map_for_session(session)
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
    ) -> list[str]:
        all_character_ids = self._episode_character_ids_for_session(session)
        speaker = turn.get("speaker_policy") if isinstance(turn.get("speaker_policy"), dict) else {}
        allowed_participant_ids = [
            str(item).strip()
            for item in speaker.get("allowed_participant_ids") or []
            if str(item).strip()
        ]
        if not allowed_participant_ids:
            return all_character_ids
        projected = self._episode_speaker_policy_for_turn(session, turn)
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
            "輸出限制："
            f"最多句數：{int(output.get('max_sentences') or 2)}；"
            f"必須問句結尾：{bool(output.get('must_end_with_question'))}；"
            f"允許向觀眾提問：{bool(output.get('allow_audience_question'))}",
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
            lines.append(f"證據需求：需要資料卡，最多 {max_cards} 張；資料卡只作為事實依據，不是立場或段落策略。")
        else:
            lines.append("證據需求：本輪不注入資料卡；請依本輪目標與角色開場/收束要求回應。")
        if required_entities:
            lines.append("必須涵蓋：" + ", ".join(required_entities))
        preview = next_turn_preview if isinstance(next_turn_preview, dict) else {}
        if preview.get("turn_id"):
            next_label = " - ".join(
                part for part in (
                    str(preview.get("turn_type") or "").strip(),
                    str(preview.get("intent") or "").strip(),
                )
                if part
            )
            if next_label:
                lines.append(
                    "下一輪預告："
                    f"{next_label}；自由發揮時請自然往這個方向收束，但不要提前完整講完下一輪。"
                )
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
            "第 1 位角色：提出主觀點或核心資訊。",
        ]
        if max_replies >= 2:
            lines.append("第 2 位角色：只能反應、轉譯、補一個新角度或推進，不得重述第 1 位角色主觀點。")
        if max_replies >= 3:
            lines.append("第 3 位以上角色：只允許短收束或橋接，不得新增同一資料點的重複分析。")
        lines.extend([
            "段落完成條件：核心資訊已被說出即視為 completed；completed 後不得再次呼叫 analyst 類角色補充同一資料。",
            "本次發言任務：第 1 位角色負責提出主觀點或核心資訊；不得完整覆蓋整個段落目標。",
        ])
        return lines

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

    def _episode_turn_topic_context(self, session_id: str, turn: dict[str, Any]) -> str:
        evidence = turn.get("evidence_policy") if isinstance(turn.get("evidence_policy"), dict) else {}
        queries = [
            str(query).strip()
            for query in evidence.get("queries") or []
            if str(query).strip()
        ]
        if not queries:
            return ""
        max_cards = self._episode_evidence_max_cards(evidence)
        if max_cards <= 0:
            return ""
        return self._topic_pack_context_for_query(
            session_id,
            "\n".join(queries),
            limit=max_cards,
            usage_source="episode_plan",
            allow_fallback=bool(evidence.get("allow_unverified_claims")),
        )

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
            "請改為概括不同觀眾可能有不同偏好，並把討論拉回本輪主題。"
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
            and str(event.get("safety_status") or "") == "completed"
            and str(event.get("status") or "active") == "active"
            and str(event.get("safe_message_text") or "").strip()
        ]

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
        speaker_policy = self._episode_speaker_policy_for_turn(session, turn)
        dialogue_policy = self._episode_dialogue_policy(turn)
        next_turn_preview = self._episode_next_turn_preview(plan, planned_state)
        context_text = self._episode_plan_context_text(
            plan,
            planned_state,
            turn,
            interrupt_state=interrupt_state,
            speaker_policy=speaker_policy,
            next_turn_preview=next_turn_preview,
            audience_event_context=audience_event_context,
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
                "turn_contract": {
                    "turn_id": str(turn.get("turn_id") or ""),
                    "turn_type": str(turn.get("turn_type") or ""),
                    "intent": str(turn.get("intent") or ""),
                },
                "speaker_policy": speaker_policy,
                "dialogue_policy": dialogue_policy,
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

        handled_event_ids = {
            int(event_id)
            for event_id in (
                (planned_state.get("segment_memory") or {}).get("audience_reactions")
                or []
            )
            if str(event_id).isdigit()
        }
        recent_events = self.storage.list_events(session["session_id"], limit=20)
        completed_events = [
            event
            for event in recent_events
            if int(event.get("id") or 0) not in handled_event_ids
            and str(event.get("safety_status") or "") == "completed"
            and str(event.get("status") or "active") == "active"
            and str(event.get("safe_message_text") or "").strip()
        ]
        for event in reversed(completed_events):
            decision = self._episode_interrupt_decision_for_event(plan, planned_state, event)
            if decision:
                return decision
        return self._episode_planned_turn_decision(session, state)

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

        metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
        last_decision = metadata.get("last_decision") if isinstance(metadata.get("last_decision"), dict) else {}
        last_payload = (
            last_decision.get("episode_plan")
            if isinstance(last_decision.get("episode_plan"), dict)
            else {}
        )
        last_turn = (
            last_payload.get("turn_contract")
            if isinstance(last_payload.get("turn_contract"), dict)
            else {}
        )
        last_output = (
            last_turn.get("output_requirements")
            if isinstance(last_turn.get("output_requirements"), dict)
            else {}
        )
        try:
            configured_gap = int(session.get("episode_plan_turn_gap_seconds", 8) or 8)
        except (TypeError, ValueError):
            configured_gap = 8
        if bool(last_output.get("allow_audience_question")) or bool(last_output.get("must_end_with_question")):
            return {
                "delay_seconds": max(1, min(configured_gap, int(idle_seconds or 60), 30)),
                "reason": "audience_turn_gap",
                "label": "觀眾題後等待",
            }
        if bool(last_output.get("should_handoff")) and str(last_output.get("handoff_target_function") or "").strip():
            try:
                handoff_gap = int(session.get("episode_plan_handoff_gap_seconds", 2) or 2)
            except (TypeError, ValueError):
                handoff_gap = 2
            return {
                "delay_seconds": max(1, min(handoff_gap, int(idle_seconds or 60), 5)),
                "reason": "handoff_gap",
                "label": "交接等待",
            }
        return {
            "delay_seconds": max(1, min(configured_gap, int(idle_seconds or 60), 30)),
            "reason": "turn_gap",
            "label": "一般等待",
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
        if mode == "planned_turn" and str(planned_state.get("plan_status") or "") == "completed":
            return {
                "planned_state": planned_state,
                "interrupt_state": {"status": "idle"},
            }
        if mode == "audience_interrupt":
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
            return {
                "planned_state": planned_state,
                "interrupt_state": interrupt_state,
            }

        turn = (
            payload.get("turn_contract")
            if isinstance(payload.get("turn_contract"), dict)
            else self._episode_current_turn_contract(plan, planned_state)
        )
        if not turn:
            return {
                "planned_state": planned_state,
                "interrupt_state": {"status": "idle"},
            }
        next_state = self._planned_state_after_episode_turn(plan, planned_state, turn)
        return {
            "planned_state": next_state,
            "interrupt_state": {"status": "idle"},
        }
