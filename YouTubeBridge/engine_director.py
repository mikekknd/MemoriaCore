"""YouTubeBridge director 決策 helper mixin。"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from bridge_contracts import DIRECTOR_SCHEMA


class DirectorManagerMixin:
    def _director_decision(self, session: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        recent_events = self.storage.list_events(session["session_id"], limit=20)
        recent_interactions = self.storage.list_interactions(session["session_id"], limit=20)
        elapsed_minutes, elapsed_percent, remaining_minutes = self._session_elapsed(session)
        event_lines = "\n".join(
            line
            for event in recent_events[-20:]
            if (line := self._director_event_line(event))
        ) or "（無近期留言）"
        interaction_lines = "\n".join(
            line
            for item in reversed(recent_interactions)
            if (line := self._test_comment_interaction_line(item))
        ) or "（無近期互動）"
        public_guidance = self._public_director_topic(session, state)
        decision = self._memoria_client().generate_prompt_json(
            prompt_key="youtube_live_director_decision_prompt",
            variables={
                "session_title": session.get("display_name") or session["session_id"],
                "director_guidance": public_guidance or "（未設定）",
                "current_topic": state.get("current_topic") or "",
                "consecutive_ai_turns": str(state.get("consecutive_ai_turns", 0)),
                "planned_duration_minutes": str(session.get("planned_duration_minutes", 0) or 0),
                "elapsed_minutes": str(elapsed_minutes),
                "elapsed_percent": str(elapsed_percent),
                "remaining_minutes": str(remaining_minutes),
                "recent_events": event_lines,
                "recent_interactions": interaction_lines,
            },
            task_key="router",
            temperature=0.0,
            schema=DIRECTOR_SCHEMA,
        )
        allowed = {
            "wait", "continue_topic", "ask_character", "transition_topic", "recap", "close_topic",
            "reply_chat_batch", "reply_super_chat_batch", "defer_offtopic", "anchor_to_topic",
        }
        if str(decision.get("action") or "").strip() not in allowed:
            decision["action"] = "wait"
        return decision

    @staticmethod
    def _director_decision_is_early_live_closing(decision: dict[str, Any]) -> bool:
        action = str(decision.get("action") or "").strip()
        if action not in {"recap", "close_topic"}:
            return False
        text = " ".join(
            str(decision.get(key) or "")
            for key in ("reason", "prompt", "current_topic")
        ).lower()
        early_closing_markers = (
            "elapsed_percent", "進度", "进度", "剩餘", "剩余",
            "收尾", "結尾", "结尾", "結束", "结束", "時間", "时间",
            "duration", "closing", "finalize",
        )
        return any(marker in text for marker in early_closing_markers)

    @staticmethod
    def _director_group_turn_limit_for_action(session: dict[str, Any], action: str) -> int:
        try:
            value = int(session.get("director_group_turn_limit", 3) or 3)
        except (TypeError, ValueError):
            value = 3
        value = max(1, min(value, 12))
        if not DirectorManagerMixin._director_dialogue_expansion_enabled(session):
            return 1
        if str(action or "").strip() in {"duration_closing", "closing_super_chat_thanks", "final_closing"}:
            return min(value, 2)
        return value

    @staticmethod
    def _post_plan_free_talk_group_turn_limit(session: dict[str, Any], mode: str = "idle") -> int:
        prefix = "post_plan_free_talk_audience" if str(mode or "") == "audience" else "post_plan_free_talk_idle"
        try:
            value = int(session.get(f"{prefix}_turns_min", 1) or 1)
        except (TypeError, ValueError):
            value = 1
        try:
            maximum = int(session.get(f"{prefix}_turns_max", value) or value)
        except (TypeError, ValueError):
            maximum = value
        return max(1, min(max(value, 1), min(max(maximum, value), 12)))

    @staticmethod
    def _director_dialogue_expansion_enabled(session: dict[str, Any] | None = None) -> bool:
        return bool((session or {}).get("director_dialogue_expansion_enabled", True))

    @staticmethod
    def _program_segment_turns(session: dict[str, Any] | None = None) -> int:
        try:
            value = int((session or {}).get("program_segment_turns", 3) or 3)
        except (TypeError, ValueError):
            value = 3
        return max(1, min(value, 12))

    @staticmethod
    def _program_segment_items(session: dict[str, Any] | None = None) -> list[dict[str, str]]:
        raw = str((session or {}).get("program_segment_plan") or "").replace("\r", "\n")
        numbered_items: list[dict[str, str]] = []
        numbered_pattern = re.compile(r"^\s*(?:\d+|[一二三四五六七八九十]+)[\.\)、．、]\s*(.+?)\s*$")
        current: dict[str, Any] | None = None
        description_lines: list[str] = []

        def split_name_description(value: str) -> tuple[str, str]:
            text = " ".join(str(value or "").strip().strip("-*•").split())
            for separator in ("：", ":"):
                if separator in text:
                    name, description = text.split(separator, 1)
                    return name.strip(), description.strip()
            return text.rstrip(":：").strip(), ""

        def flush_current() -> None:
            if not current:
                return
            name = str(current.get("name") or "").strip()
            if not name:
                return
            description_parts = []
            inline_description = str(current.get("description") or "").strip()
            if inline_description:
                description_parts.append(inline_description)
            description_parts.extend(
                " ".join(line.strip().strip("-*•").split())
                for line in description_lines
                if line.strip() and not line.strip().startswith("#")
            )
            numbered_items.append({
                "name": name[:160],
                "description": " ".join(part for part in description_parts if part)[:500],
            })

        for line in raw.splitlines():
            match = numbered_pattern.match(line)
            if match:
                flush_current()
                description_lines = []
                name, inline_description = split_name_description(match.group(1))
                current = {"name": name, "description": inline_description}
                continue
            if current is not None:
                description_lines.append(line)
        flush_current()
        if numbered_items:
            return numbered_items[:20]

        items: list[dict[str, str]] = []
        for line in raw.splitlines():
            item = " ".join(line.strip().strip("-*•").split())
            if item.startswith("#"):
                continue
            if item:
                name, description = split_name_description(item)
                if name:
                    items.append({"name": name[:160], "description": description[:500]})
        return items[:20]

    @classmethod
    def _program_segment_entries(cls, session: dict[str, Any] | None = None) -> list[str]:
        return [item["name"] for item in cls._program_segment_items(session)]

    @classmethod
    def _program_segment_steps(cls, session: dict[str, Any] | None = None) -> list[dict[str, str]]:
        return [
            {
                "step_id": f"step_{index + 1:02d}",
                "name": item["name"],
                "description": item.get("description", ""),
            }
            for index, item in enumerate(cls._program_segment_items(session))
        ]

    @classmethod
    def _current_program_segment(cls, session: dict[str, Any], state: dict[str, Any]) -> dict[str, Any] | None:
        items = cls._program_segment_items(session)
        if not items:
            return None
        metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
        raw_segment = metadata.get("program_segment") if isinstance(metadata.get("program_segment"), dict) else {}
        try:
            index = int(raw_segment.get("index", 0) or 0)
        except (TypeError, ValueError):
            index = 0
        try:
            turns_in_segment = int(raw_segment.get("turns_in_segment", 0) or 0)
        except (TypeError, ValueError):
            turns_in_segment = 0
        index = max(0, min(index, len(items) - 1))
        return {
            "index": index,
            "name": items[index]["name"],
            "description": items[index].get("description", ""),
            "turns_in_segment": max(0, turns_in_segment),
            "turns_per_segment": cls._program_segment_turns(session),
            "total_segments": len(items),
        }

    @classmethod
    def _program_segment_after_turn(
        cls,
        session: dict[str, Any],
        state: dict[str, Any],
        decision: dict[str, Any],
    ) -> dict[str, Any] | None:
        current = cls._current_program_segment(session, state)
        if not current:
            return None
        action = str(decision.get("action") or "").strip()
        previous_topic = str(state.get("current_topic") or "").strip()
        next_topic = str(decision.get("current_topic") or previous_topic).strip()
        reset = action in {"opening", "post_opening_topic_anchor", "transition_topic"} or (
            bool(previous_topic and next_topic) and previous_topic != next_topic
        )
        items = cls._program_segment_items(session)
        if reset:
            return {
                **current,
                "index": 0,
                "name": items[0]["name"],
                "description": items[0].get("description", ""),
                "turns_in_segment": 1,
                "topic": next_topic,
            }
        turns = int(current.get("turns_in_segment", 0) or 0) + 1
        index = int(current.get("index", 0) or 0)
        if turns >= cls._program_segment_turns(session) and index < int(current.get("total_segments", 1) or 1) - 1:
            index += 1
            turns = 0
        return {
            **current,
            "index": index,
            "name": items[index]["name"],
            "description": items[index].get("description", ""),
            "turns_in_segment": turns,
            "topic": next_topic,
        }

    @staticmethod
    def _segment_step_summary(step: dict[str, Any] | None, include_description: bool = False) -> dict[str, Any]:
        if not isinstance(step, dict):
            return {}
        item = {
            "step_id": str(step.get("step_id") or "").strip()[:40],
            "name": str(step.get("name") or "").strip()[:160],
        }
        description = str(step.get("description") or "").strip()[:500]
        if include_description and description:
            item["description"] = description
        return item if item["step_id"] and item["name"] else {}

    @classmethod
    def _sanitize_segment_state(cls, value: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        current_step = cls._segment_step_summary(value.get("current_step"), include_description=True)
        if not current_step:
            return {}
        try:
            topic_entry_id = int(value.get("topic_entry_id") or 0)
        except (TypeError, ValueError):
            topic_entry_id = 0
        try:
            turns_in_step = int(value.get("turns_in_step", 0) or 0)
        except (TypeError, ValueError):
            turns_in_step = 0
        completed_steps = [
            step
            for raw_step in value.get("completed_steps") or []
            if isinstance(raw_step, dict)
            if (step := cls._segment_step_summary(raw_step, include_description=False))
        ][:20]
        remaining_steps = [
            step
            for raw_step in value.get("remaining_steps") or []
            if isinstance(raw_step, dict)
            if (step := cls._segment_step_summary(raw_step, include_description=True))
        ][:20]
        sanitized: dict[str, Any] = {
            "topic": str(value.get("topic") or "").strip()[:200],
            "topic_entry_id": max(0, topic_entry_id),
            "current_step": current_step,
            "completed_steps": completed_steps,
            "remaining_steps": remaining_steps,
            "turns_in_step": max(0, turns_in_step),
            "last_transition_reason": str(value.get("last_transition_reason") or "").strip()[:200],
        }
        if "turns_per_step" in value:
            try:
                sanitized["turns_per_step"] = max(1, min(int(value.get("turns_per_step") or 1), 12))
            except (TypeError, ValueError):
                sanitized["turns_per_step"] = 1
        if "total_steps" in value:
            try:
                sanitized["total_steps"] = max(0, int(value.get("total_steps") or 0))
            except (TypeError, ValueError):
                sanitized["total_steps"] = 0
        if "step_index" in value:
            try:
                sanitized["step_index"] = max(0, int(value.get("step_index") or 0))
            except (TypeError, ValueError):
                sanitized["step_index"] = 0
        if bool(value.get("all_steps_completed")):
            sanitized["all_steps_completed"] = True
        return sanitized

    @classmethod
    def _current_segment_state(cls, session: dict[str, Any], state: dict[str, Any]) -> dict[str, Any] | None:
        metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
        current = cls._sanitize_segment_state(metadata.get("segment_state"))
        if current:
            return current
        steps = cls._program_segment_steps(session)
        if not steps:
            return None
        return {
            "topic": str(state.get("current_topic") or session.get("director_guidance") or "").strip()[:200],
            "topic_entry_id": 0,
            "current_step": steps[0],
            "completed_steps": [],
            "remaining_steps": steps[1:],
            "turns_in_step": 0,
            "turns_per_step": cls._program_segment_turns(session),
            "total_steps": len(steps),
            "step_index": 0,
            "last_transition_reason": "initialized",
        }

    @classmethod
    def _segment_state_after_turn(
        cls,
        session: dict[str, Any],
        state: dict[str, Any],
        decision: dict[str, Any],
        topic_entry: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        steps = cls._program_segment_steps(session)
        if not steps:
            return None
        metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
        current = cls._sanitize_segment_state(metadata.get("segment_state"))
        action = str(decision.get("action") or "").strip()
        previous_topic = str((current or {}).get("topic") or state.get("current_topic") or "").strip()
        next_topic = str(decision.get("current_topic") or previous_topic or session.get("director_guidance") or "").strip()
        topic_entry_id = 0
        if isinstance(topic_entry, dict) and topic_entry.get("id"):
            try:
                topic_entry_id = int(topic_entry.get("id") or 0)
            except (TypeError, ValueError):
                topic_entry_id = 0
            if str(topic_entry.get("title") or "").strip():
                next_topic = str(topic_entry.get("title") or "").strip()
        else:
            try:
                topic_entry_id = int((current or {}).get("topic_entry_id") or 0)
            except (TypeError, ValueError):
                topic_entry_id = 0
        reset = (
            not current
            or action in {"opening", "post_opening_topic_anchor", "transition_topic"}
            or bool(previous_topic and next_topic and previous_topic != next_topic)
        )

        def build(index: int, turns: int, reason: str, completed_until: int = -1) -> dict[str, Any]:
            index = max(0, min(index, len(steps) - 1))
            completed = steps[: max(0, completed_until + 1)]
            return {
                "topic": next_topic[:200],
                "topic_entry_id": max(0, topic_entry_id),
                "current_step": steps[index],
                "completed_steps": completed,
                "remaining_steps": steps[index + 1 :],
                "turns_in_step": max(0, turns),
                "turns_per_step": cls._program_segment_turns(session),
                "total_steps": len(steps),
                "step_index": index,
                "last_transition_reason": reason,
                "all_steps_completed": bool(completed and len(completed) >= len(steps)),
            }

        if reset:
            return build(0, 1, "topic_reset" if current else "initialized", completed_until=-1)

        step_id = str((current.get("current_step") or {}).get("step_id") or steps[0]["step_id"])
        index = next((idx for idx, step in enumerate(steps) if step["step_id"] == step_id), 0)
        turns = int(current.get("turns_in_step", 0) or 0) + 1
        turns_per_step = cls._program_segment_turns(session)
        if turns >= turns_per_step:
            if index < len(steps) - 1:
                return build(index + 1, 0, "step_advanced", completed_until=index)
            return build(index, turns, "steps_completed", completed_until=index)
        return build(index, turns, "step_hold", completed_until=index - 1)

    def _segment_topic_entry_for_session(self, session: dict[str, Any]) -> dict[str, Any] | None:
        session_id = str(session.get("session_id") or "")
        if not session_id or not hasattr(self, "_topic_pack_sequence_entries_for_session"):
            return None
        try:
            entries = self._topic_pack_sequence_entries_for_session(session_id)
        except Exception:
            return None
        return entries[0] if entries else None

    @classmethod
    def _live_hosting_context_for_session(cls, session: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        host_rules = str(session.get("host_interaction_rules") or "").replace("\r", "\n").strip()[:4000]
        segment_state = cls._current_segment_state(session, state)
        if not host_rules and not segment_state:
            return {}
        payload: dict[str, Any] = {
            "host_interaction_rules": host_rules,
            "program_segment_turns": cls._program_segment_turns(session),
        }
        if segment_state:
            payload["segment_state"] = segment_state
        return payload

    @staticmethod
    def _live_hosting_context_text(live_hosting: dict[str, Any]) -> str:
        if not live_hosting:
            return ""
        lines = ["主持結構："]
        host_rules = str(live_hosting.get("host_interaction_rules") or "").strip()
        if host_rules:
            lines.append("主持互動規則：")
            lines.append(host_rules)
        segment_state = live_hosting.get("segment_state") if isinstance(live_hosting.get("segment_state"), dict) else {}
        current = segment_state.get("current_step") if isinstance(segment_state.get("current_step"), dict) else {}
        if current:
            topic = str(segment_state.get("topic") or "").strip()
            if topic:
                lines.append(f"目前討論主題：{topic}")
            current_name = str(current.get("name") or "").strip()
            if current_name:
                lines.append(f"目前節目步驟：{current_name}")
            description = str(current.get("description") or "").strip()
            if description:
                lines.append(f"目前步驟說明：{description}")
            completed = [
                str(item.get("name") or "").strip()
                for item in segment_state.get("completed_steps") or []
                if isinstance(item, dict) and str(item.get("name") or "").strip()
            ]
            remaining = [
                str(item.get("name") or "").strip()
                for item in segment_state.get("remaining_steps") or []
                if isinstance(item, dict) and str(item.get("name") or "").strip()
            ]
            if completed:
                lines.append("已完成步驟：" + "、".join(completed[:8]))
            if remaining:
                lines.append("剩餘步驟：" + "、".join(remaining[:8]))
            turns = int(segment_state.get("turns_in_step", 0) or 0)
            turns_per_step = int(segment_state.get("turns_per_step", live_hosting.get("program_segment_turns", 0)) or 0)
            if turns_per_step:
                lines.append(f"目前步驟回合：{turns}/{turns_per_step}")
        return "\n".join(line for line in lines if line)

    @staticmethod
    def _public_director_topic(session: dict[str, Any], state: dict[str, Any] | None = None) -> str:
        """把導播內部規則壓成角色可自然說出口的主題文字。"""
        guidance = str(session.get("director_guidance") or "").strip()
        current = str((state or {}).get("current_topic") or "").strip()
        title = str(session.get("display_name") or session.get("session_id") or "目前直播話題").strip()
        raw = guidance or current or title
        if "初始主題是" in raw:
            raw = raw.split("初始主題是", 1)[1].strip()
        for separator in ("。", "\n", "；", ";", "，請", ",請"):
            if separator in raw:
                if separator == "。" and raw.endswith("。") and raw.count("。") == 1:
                    continue
                raw = raw.split(separator, 1)[0].strip()
        blocked_phrases = (
            "Topic Pack", "Research Gate", "控場", "聊天室長時間帶偏",
            "SC 可以優先", "不得提高", "結尾要安排", "queue", "prompt",
        )
        if any(phrase in raw for phrase in blocked_phrases):
            raw = title
        return raw[:80] or title[:80] or "目前直播話題"

    @staticmethod
    def _public_test_topic(session: dict[str, Any], topic_hint: str = "") -> str:
        """把測試留言可見主題限制為公開可說出口的短題目。"""
        hint_session = dict(session)
        raw_hint = str(topic_hint or "").strip()
        if raw_hint:
            hint_session["director_guidance"] = raw_hint
        topic = DirectorManagerMixin._public_director_topic(hint_session, {})
        blocked = (
            "Topic Pack", "Research Gate", "queue", "prompt", "導播", "控場",
            "不要讓聊天室", "不得提高", "內部", "系統",
        )
        if any(term.lower() in topic.lower() for term in blocked):
            topic = str(session.get("display_name") or "目前直播內容").strip()
        return topic[:80] or "目前直播內容"

    @staticmethod
    def _sanitize_test_comment_text(text: str, public_topic: str) -> str:
        clean = str(text or "").replace("\r", " ").replace("\n", " ").strip()
        replacements = {
            "Topic Pack": "資料",
            "Research Gate": "資料查詢",
            "queue": "流程",
            "prompt": "提示",
            "導播": "直播節奏",
            "控場": "帶節奏",
            "不要讓聊天室長時間帶偏": "回到主題",
            "不得提高": "不需要改變",
        }
        for bad, safe in replacements.items():
            clean = clean.replace(bad, safe)
        public_topic = str(public_topic or "目前直播內容").strip()
        if not clean:
            clean = f"想聽你們多聊 {public_topic}。"
        return clean[:500]

    @staticmethod
    def _public_director_prompt(
        action: str,
        session: dict[str, Any],
        state: dict[str, Any],
    ) -> str:
        topic = DirectorManagerMixin._public_director_topic(session, state)
        prompts = {
            "reply_chat_batch": f"請簡短回應剛剛的聊天室留言，接著讓角色彼此補充並自然拉回「{topic}」。",
            "reply_super_chat_batch": f"請感謝並回應剛剛的 Super Chat，接著讓角色彼此補充並自然拉回「{topic}」。",
            "defer_offtopic": f"請簡短帶過離題留言，並讓角色彼此把直播節奏拉回「{topic}」。",
            "anchor_to_topic": f"請自然承接剛剛的互動，讓角色彼此簡短拉回「{topic}」，不要把問題丟回聊天室。",
            "ask_character": f"請讓角色彼此互問或補充「{topic}」的一個具體觀點，不要把問題丟回聊天室。",
            "transition_topic": f"請自然把話題轉向「{topic}」，讓角色彼此接話，用 1 到 3 句推進直播，不要把問題丟回聊天室。",
            "recap": f"請讓角色彼此整理目前「{topic}」的討論重點，用 1 到 3 句收束，不要把問題丟回聊天室。",
            "close_topic": f"請讓角色彼此收束目前「{topic}」的話題，用 1 到 3 句提出下一個切入點，不要把問題丟回聊天室。",
            "opening": f"直播開場任務：請先完成固定開場白與自我介紹，再自然帶入本場方向「{topic}」。",
            "post_opening_topic_anchor": f"開場已完成，請優先根據外部上下文提供的本場話題資料卡，從「{topic}」挑一個具體切入點開始討論。讓角色彼此接話、補充或提出不同角度；不要把問題丟回聊天室，也不要自行捏造資料卡未提供的具體作品、集數或事件。",
            "duration_closing": f"預定直播時間已到，請簡短宣布本場進入收尾，承接「{topic}」但不要完整總結，不要開新話題，也不要把問題丟回聊天室。",
            "closing_super_chat_thanks": "直播即將收尾，請感謝本場 Super Chat 支持；不適合公開回覆的內容不用提起。",
            "final_closing": f"請做本場最後收尾，簡短回顧「{topic}」最重要的一個重點並正式道別。不要開新話題，不要重複前面已說過的收尾比喻，也不要把問題丟回聊天室。",
            "post_plan_free_talk_topic": f"雜談話題：{topic}\n請自然延伸這個雜談話題，讓角色彼此接話、補充或提出不同角度；不要提到幕後流程。",
            "post_plan_free_talk_natural": "自然雜談：請延續直播餘韻，讓角色彼此聊一段輕鬆近況或現場感想；不要提到幕後流程，也不要把問題丟回聊天室。",
        }
        return prompts.get(
            action,
            f"請自然延續「{topic}」，讓角色彼此接話、補充或提出不同角度，用 1 到 3 句推進話題；不要把問題丟回聊天室。",
        )

    def _public_director_opening_prompt(self, session: dict[str, Any], state: dict[str, Any]) -> str:
        topic = self._public_director_topic(session, state)
        return "\n".join([
            f"直播開場任務：請先完成固定開場白與自我介紹，再自然帶入本場方向「{topic}」。",
            "請依照本次外部上下文提供的直播開場自我介紹資料執行；不要在回覆中提到資料來源或內部設定。",
            "開場後讓角色彼此先拋出一個可延伸觀點，不要把問題丟回聊天室。",
        ])

    def _opening_intro_context_for_session(
        self,
        session: dict[str, Any],
        *,
        character_ids: list[str] | None = None,
    ) -> str:
        source_ids = character_ids if character_ids is not None else session.get("character_ids") or []
        character_ids = [
            str(character_id or "").strip()
            for character_id in source_ids
            if str(character_id or "").strip()
        ]
        if not character_ids:
            return ""
        overlays = self.storage.live_persona_prompt_overrides_for(character_ids)
        lines: list[str] = []
        for index, character_id in enumerate(character_ids, start=1):
            overlay = overlays.get(character_id) or {}
            opening_intro = str(overlay.get("opening_intro") or "").replace("\r", "\n").strip()
            if not opening_intro:
                continue
            self_address = str(overlay.get("self_address") or "").strip()
            addressing = overlay.get("addressing") if isinstance(overlay.get("addressing"), dict) else {}
            addressing_parts = [
                f"{target_id}={address}"
                for target_id, address in addressing.items()
                if str(target_id or "").strip() and str(address or "").strip()
            ]
            lines.append(f"- 角色 {index}")
            lines.append(f"  character_id: {character_id}")
            if self_address:
                lines.append(f"  固定自稱：{self_address}")
            if addressing_parts:
                lines.append(f"  固定稱呼：{', '.join(addressing_parts)}")
            lines.append("  開場自我介紹：|")
            for intro_line in opening_intro[:500].splitlines() or [opening_intro[:500]]:
                text = intro_line.strip()
                if text:
                    lines.append(f"    {text}")
        if not lines:
            return ""
        return "直播開場自我介紹：\n" + "\n".join(lines)

    @staticmethod
    def _director_topic_turn_limit(session: dict[str, Any] | None = None) -> int:
        try:
            value = int((session or {}).get("director_anchor_every_turns", 2) or 2)
        except (TypeError, ValueError):
            value = 2
        return max(1, min(value, 10))

    @staticmethod
    def _director_topic_turn_limit_reached(
        session: dict[str, Any] | None,
        state: dict[str, Any],
    ) -> bool:
        return int(state.get("consecutive_ai_turns", 0) or 0) >= DirectorManagerMixin._director_topic_turn_limit(session)

    @staticmethod
    def _director_should_force_guidance_turn(session: dict[str, Any], state: dict[str, Any]) -> bool:
        guidance = DirectorManagerMixin._public_director_topic(session, state)
        current_topic = str(state.get("current_topic") or "").strip()
        if not guidance:
            return False
        if DirectorManagerMixin._director_topic_turn_limit_reached(session, state):
            return False
        normalized_guidance = guidance.replace(" ", "")
        normalized_topic = current_topic.replace(" ", "")
        return bool(normalized_guidance and normalized_guidance[:80] not in normalized_topic)

    @staticmethod
    def _director_should_force_idle_turn(
        state: dict[str, Any],
        session: dict[str, Any] | None = None,
    ) -> bool:
        return not DirectorManagerMixin._director_topic_turn_limit_reached(session, state)

    @staticmethod
    def _parse_iso_datetime(value: Any) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value))
        except ValueError:
            return None

    @staticmethod
    def _director_should_pause_for_turn_limit(
        state: dict[str, Any],
        idle_seconds: int,
        session: dict[str, Any] | None = None,
    ) -> bool:
        if session and str(session.get("episode_plan_id") or "").strip():
            return False
        if not DirectorManagerMixin._director_topic_turn_limit_reached(session, state):
            return False
        last_action_at = DirectorManagerMixin._parse_iso_datetime(state.get("last_director_action_at"))
        if not last_action_at:
            return True
        return (datetime.now() - last_action_at).total_seconds() < max(1, int(idle_seconds or 60))

    @staticmethod
    def _director_idle_continue_decision(
        session: dict[str, Any],
        state: dict[str, Any],
    ) -> dict[str, Any]:
        topic = (
            str(state.get("current_topic") or "").strip()
            or DirectorManagerMixin._public_director_topic(session, state)
            or str(session.get("display_name") or "目前直播話題").strip()
        )
        return {
            "action": "continue_topic",
            "reason": "目前沒有未處理留言或進行中的互動，且尚未達連續 AI 主動輪數上限；導播主動延續直播節奏。",
            "prompt": (
                f"目前還沒有新的聊天室留言，請自然延續「{topic[:160]}」。"
                "讓角色彼此接話、補充或提出不同角度，用 1 到 3 句推進話題；不要把問題丟回聊天室。"
            ),
            "current_topic": topic[:200],
        }

    @staticmethod
    def _director_guidance_transition_decision(
        session: dict[str, Any],
        state: dict[str, Any],
    ) -> dict[str, Any]:
        guidance = DirectorManagerMixin._public_director_topic(session, state)
        current_topic = str(state.get("current_topic") or "").strip() or "目前話題"
        return {
            "action": "transition_topic",
            "reason": "直播方向已更新，且目前沒有未處理留言；需要主動把話題轉到新的方向。",
            "prompt": (
                f"請自然承接「{current_topic[:80]}」，把話題轉向「{guidance[:160]}」。"
                "讓角色彼此接話或互問，用 1 到 3 句推進直播；不要把問題丟回聊天室。"
            ),
            "current_topic": guidance[:200],
        }

    @staticmethod
    def _director_anchor_decision(
        session: dict[str, Any],
        state: dict[str, Any],
    ) -> dict[str, Any]:
        guidance = DirectorManagerMixin._public_director_topic(session, state)
        topic = guidance or str(state.get("current_topic") or session.get("display_name") or "本場直播方向").strip()
        return {
            "action": "anchor_to_topic",
            "reason": "聊天室已連續帶動多批互動，需要把節奏拉回本場主軸。",
            "prompt": (
                f"請自然承接剛剛聊天室互動，簡短拉回「{topic[:160]}」。"
                "讓角色彼此整理重點或提出下一個切入點；不要把問題丟回聊天室。"
            ),
            "current_topic": topic[:200],
        }

    def _director_event_line(self, event: dict[str, Any]) -> str:
        if not self._is_public_live_event_displayable(event):
            return ""
        status = "已處理" if event.get("injected_at") else "未處理"
        return f"- ({status}) {self._event_line(event).lstrip('- ')}"

    @staticmethod
    def _director_opening_decision(session: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        title = str(session.get("display_name") or session.get("session_id") or "YouTube Live").strip()
        topic = DirectorManagerMixin._public_director_topic(session, state) or title
        return {
            "action": "opening",
            "reason": "直播剛開始，需要先建立開場與觀眾互動入口。",
            "prompt": (
                "直播開場任務：請先完成固定開場白與自我介紹，再簡短帶出本場方向"
                f"「{topic[:160]}」。"
                "請讓角色彼此先拋出一個可延伸觀點，不要把問題丟回聊天室。"
            ),
            "current_topic": topic[:200] or str(state.get("current_topic") or ""),
        }

    @staticmethod
    def _director_post_opening_topic_decision(session: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        topic = DirectorManagerMixin._public_director_topic(session, state)
        current_topic = str(state.get("current_topic") or topic or session.get("display_name") or "本場直播方向").strip()
        return {
            "action": "post_opening_topic_anchor",
            "reason": "直播開場已完成，需要立刻帶入本場 Fuel Card / Fact Card 資料，避免角色空泛閒聊。",
            "prompt": (
                f"開場已完成，請根據已提供的本場話題資料，從「{current_topic[:160]}」挑一個具體切入點開始討論。"
                "讓角色彼此接話、補充或提出不同角度；不要把問題丟回聊天室。"
            ),
            "current_topic": current_topic[:200],
        }

    @staticmethod
    def _director_display_content(action: str) -> str:
        mapping = {
            "reply_chat_batch": "回應聊天室的留言。",
            "reply_super_chat_batch": "回應 Super Chat 的留言。",
            "closing_super_chat_thanks": "感謝本場 Super Chat。",
            "anchor_to_topic": "讓我們回到本場直播主題。",
            "transition_topic": "讓我們繼續進行下一個話題。",
            "continue_topic": "讓我們繼續進行下一個話題。",
            "opening": "直播開場。",
            "post_opening_topic_anchor": "帶入本場話題資料。",
            "duration_closing": "預定直播時間已到，開始收束本場直播。",
            "final_closing": "本場直播最後收尾。",
            "post_plan_free_talk_topic": "雜談話題。",
            "post_plan_free_talk_natural": "自然雜談。",
            "ask_character": "讓角色接續回應目前話題。",
            "recap": "整理一下剛剛的內容。",
            "close_topic": "收束目前話題。",
        }
        return mapping.get(str(action or ""), "讓我們繼續直播節奏。")
