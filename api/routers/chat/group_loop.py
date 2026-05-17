"""群組對話回合控制，共用於 REST / SSE / WebSocket。"""
import asyncio
import inspect
from typing import Callable, Any

from api.dependencies import get_character_manager, get_router
from api.session_manager import SessionState, session_manager
from api.routers.chat.orchestration import _unpack_orchestration_result
from api.routers.chat.pipeline import _run_memory_pipeline_bg
from core.chat_orchestrator.group_followup import build_group_followup_instruction
from core.chat_orchestrator.group_router import run_group_router
from core.chat_orchestrator.dataclasses import SharedExpandState, SharedToolState
from core.chat_orchestrator.live_persona import apply_live_persona_to_participants


MAX_GROUP_TURNS_HARD_LIMIT = 12
MAX_GROUP_TURN_DELAY_SECONDS = 30.0


def is_group_session(session: SessionState) -> bool:
    return session.session_mode == "group" and len(session.active_character_ids or []) > 1


def get_session_characters(session: SessionState) -> list[dict]:
    char_mgr = get_character_manager()
    characters = []
    for cid in session.active_character_ids or [session.character_id]:
        char = char_mgr.get_character(cid)
        if char:
            characters.append(char)
    return characters


def _group_loop_cancel_requested(cancel_event: asyncio.Event | None) -> bool:
    return bool(cancel_event and cancel_event.is_set())


async def run_group_chat_loop(
    *,
    session: SessionState,
    user_prompt: str,
    user_prefs: dict,
    orchestration_fn: Callable[..., tuple],
    on_event: Callable[[dict], None] | None = None,
    on_turn: Callable[[dict[str, Any]], Any] | None = None,
    user_name: str = "",
    expose_llm_trace: bool = False,
    extra_session_ctx: dict | None = None,
    transient_user_content: str = "",
    max_turns_override: int | None = None,
    cancel_event: asyncio.Event | None = None,
) -> list[dict[str, Any]]:
    """執行一輪使用者輸入後的多 AI 接力，並負責持久化 assistant turn。"""
    participants = get_session_characters(session)
    if extra_session_ctx:
        base_ctx = {
            "user_id": session.user_id,
            "channel": session.channel,
            "persona_face": session.persona_face,
            "session_id": session.session_id,
            "active_character_ids": list(session.active_character_ids or [session.character_id]),
            "session_mode": session.session_mode,
            "group_name": session.group_name,
        }
        base_ctx.update(extra_session_ctx)
        participants = apply_live_persona_to_participants(participants, base_ctx)
    if not participants:
        return []

    max_turns = _group_turn_limit(user_prefs)
    if max_turns_override is not None:
        try:
            max_turns = int(max_turns_override)
        except (TypeError, ValueError):
            pass
        max_turns = max(1, min(max_turns, MAX_GROUP_TURNS_HARD_LIMIT))
    turn_delay_seconds = _group_turn_delay_seconds(user_prefs)
    working_messages = list(session.messages)
    transient_user_content = str(transient_user_content or "").strip()
    turn_instruction = transient_user_content or user_prompt
    current_turn_start_index = len(working_messages)
    turns: list[dict[str, Any]] = []
    last_entities = list(session.last_entities)
    participant_ids = _participant_ids(participants)
    last_speaker_id: str | None = _latest_assistant_speaker_id(working_messages, participant_ids)
    last_reply = ""
    last_character_name = ""
    # 跨 turn 共用的工具狀態：turn 0 跑完工具後填入，後續 turn 直接復用，避免重複呼叫外部 API。
    shared_tool_state: SharedToolState | None = None
    # 同一輪 user 輸入的檢索意圖固定，query expansion 只需呼叫一次。
    shared_expand_state = SharedExpandState()
    discussion_mode = _discussion_mode_for_external_context(extra_session_ctx)
    live_hosting = _live_hosting_for_external_context(extra_session_ctx)
    live_episode_plan = _live_episode_plan_for_external_context(extra_session_ctx)
    final_closing_hint = _final_closing_hint_for_external_context(extra_session_ctx)
    current_turn_intent = _router_intent_for_external_context(extra_session_ctx)

    for turn_index in range(max_turns):
        if _group_loop_cancel_requested(cancel_event):
            break

        route = await asyncio.to_thread(
            run_group_router,
            working_messages,
            participants,
            get_router(),
            temperature=0.0,
            last_speaker_id=last_speaker_id,
            honor_mentions=(turn_index == 0),
            bot_turn_index=turn_index,
            max_bot_turns=max_turns,
            allow_single_participant_repeat=bool(user_prefs.get("group_chat_allow_single_character_repeat", True)),
            discussion_mode=discussion_mode,
            live_hosting=live_hosting,
            live_episode_plan=live_episode_plan,
            final_closing_hint=final_closing_hint,
            current_turn_instruction=turn_instruction,
            current_turn_intent=current_turn_intent,
            current_turn_start_index=current_turn_start_index,
        )
        if _group_loop_cancel_requested(cancel_event):
            break

        if not route.should_respond or not route.target_character_id:
            if _is_youtube_live_director_router_intent(current_turn_intent):
                break
            if turn_index == 0:
                target_character_id = _fallback_first_turn_target(
                    participants,
                    working_messages,
                    last_speaker_id,
                )
                if not target_character_id:
                    break
            else:
                break
        else:
            target_character_id = route.target_character_id

        target_char = _character_by_id(participants, target_character_id)
        if not target_char:
            break
        character_name = target_char.get("name") or target_character_id
        if _group_loop_cancel_requested(cancel_event):
            break

        live_episode_reply_task = _build_live_episode_reply_task(
            live_episode_plan,
            turn_index=turn_index,
            max_turns=max_turns,
            target_character_id=target_character_id,
            character_name=character_name,
            last_character_name=last_character_name,
            last_reply=last_reply,
            discussion_mode=discussion_mode,
        )
        if on_event:
            on_event({
                "type": "typing",
                "session_id": session.session_id,
                "turn_index": turn_index,
                "character_id": target_character_id,
                "character_name": character_name,
            })

        # 不再 append 接力指令到 generation_messages — 改透過 followup_instruction
        # 注入到 orchestration 的 api_messages（只給最終 LLM 看，不污染 expand/pipeline）。
        generation_messages = list(working_messages)

        followup_instruction: dict | None = None
        if turn_index > 0:
            followup_instruction = {
                "last_character_name": last_character_name,
                "last_reply": last_reply,
                "user_prompt_original": turn_instruction,
                "conversation_intent": route.conversation_intent,
                "routing_action": route.action,
            }
            if live_episode_reply_task:
                followup_instruction["live_episode_reply_task"] = live_episode_reply_task

        session_ctx = {
            "user_id": session.user_id,
            "character_id": target_character_id,
            "persona_face": session.persona_face,
            "session_id": session.session_id,
            "bot_id": session.bot_id,
            "channel": session.channel,
            "user_name": user_name,
            "active_character_ids": list(session.active_character_ids or [session.character_id]),
            "session_mode": session.session_mode,
            "group_name": session.group_name,
            "profile_allowed": turn_index == 0,
            "shared_tool_state": shared_tool_state,
            "shared_expand_state": shared_expand_state,
            "followup_instruction": followup_instruction,
            "expose_llm_trace": expose_llm_trace,
            "group_discussion_mode": discussion_mode,
        }
        if extra_session_ctx:
            session_ctx.update(extra_session_ctx)
        if live_episode_reply_task:
            session_ctx["live_episode_reply_task"] = live_episode_reply_task

        if _group_loop_cancel_requested(cancel_event):
            break

        result = await asyncio.to_thread(
            orchestration_fn,
            generation_messages,
            last_entities,
            turn_instruction,
            user_prefs,
            on_event=on_event,
            session_ctx=session_ctx,
        )
        if _group_loop_cancel_requested(cancel_event):
            break

        reply_text, new_entities, retrieval_ctx, topic_shifted, pipeline_data, \
            inner_thought, status_metrics, tone, speech, thinking_speech, cited_uids, \
            tool_state_export = _unpack_orchestration_result(result)
        external_summary = ((extra_session_ctx or {}).get("external_chat_context") or {}).get("summary")
        if isinstance(external_summary, dict):
            retrieval_ctx["external_context"] = external_summary

        # turn 0 完成後鎖定工具狀態，後續 turn 共用；若 turn 0 沒觸發工具就保持 None。
        if shared_tool_state is None and isinstance(tool_state_export, SharedToolState) and tool_state_export.executed:
            shared_tool_state = tool_state_export

        # cited_uids 透過 retrieval_ctx 進入 debug_info 持久化，不再拼進 content
        message_id = await session_manager.add_assistant_message(
            session.session_id,
            reply_text,
            retrieval_ctx,
            new_entities,
            persona_state={"internal_thought": inner_thought},
            character_name=character_name,
            character_id=target_character_id,
        )

        assistant_msg = {
            "message_id": message_id,
            "role": "assistant",
            "content": reply_text,
            "debug_info": retrieval_ctx,
            "character_id": target_character_id,
            "character_name": character_name,
            "persona_state": {"internal_thought": inner_thought},
        }
        working_messages.append(assistant_msg)
        last_entities = list(new_entities)
        last_speaker_id = target_character_id
        last_reply = reply_text
        last_character_name = character_name

        if topic_shifted:
            if pipeline_data:
                asyncio.create_task(_run_memory_pipeline_bg(session.session_id, pipeline_data))
            await session_manager.bridge(session.session_id)

        turn = {
            "message_id": message_id,
            "reply": reply_text,
            "extracted_entities": new_entities,
            "retrieval_context": retrieval_ctx,
            "cited_memory_uids": cited_uids,
            "internal_thought": inner_thought,
            "speech": speech,
            "thinking_speech": thinking_speech or None,
            "character_id": target_character_id,
            "character_name": character_name,
            "turn_index": turn_index,
            "is_final": turn_index == max_turns - 1,
        }
        turns.append(turn)

        if on_turn:
            callback_result = on_turn(dict(turn))
            if inspect.isawaitable(callback_result):
                await callback_result

        # 讓 UI 有時間呈現上一位 AI 的回覆，也可降低 provider rate limit 壓力。
        if turn_delay_seconds > 0 and turn_index < max_turns - 1:
            await asyncio.sleep(turn_delay_seconds)

    if turns:
        was_final = turns[-1]["is_final"]
        turns[-1]["is_final"] = True
        # 若迴圈提前結束（早退），最後一個 turn 的 is_final 在 on_turn 時是 False，
        # 需補送一次更正過的 snapshot，讓 WS/SSE 客戶端感知真實的結束旗標。
        if not was_final and on_turn and not _group_loop_cancel_requested(cancel_event):
            callback_result = on_turn(dict(turns[-1]))
            if inspect.isawaitable(callback_result):
                await callback_result
    return turns


def _group_turn_limit(user_prefs: dict) -> int:
    raw_limit = user_prefs.get("group_chat_max_bot_turns", 3)
    try:
        limit = int(raw_limit)
    except (TypeError, ValueError):
        limit = 3
    return max(1, min(limit, MAX_GROUP_TURNS_HARD_LIMIT))


def _group_turn_delay_seconds(user_prefs: dict) -> float:
    raw_delay = user_prefs.get("group_chat_turn_delay_seconds", 2.0)
    try:
        delay = float(raw_delay)
    except (TypeError, ValueError):
        delay = 2.0
    return max(0.0, min(delay, MAX_GROUP_TURN_DELAY_SECONDS))


def _discussion_mode_for_external_context(extra_session_ctx: dict | None) -> str:
    external_context = (extra_session_ctx or {}).get("external_chat_context")
    if not isinstance(external_context, dict):
        return "default"
    source = str(external_context.get("source") or "").strip()
    if source in {"youtube_live", "youtube_live_director"}:
        return "youtube_live"
    return "default"


def _live_hosting_for_external_context(extra_session_ctx: dict | None) -> dict | None:
    external_context = (extra_session_ctx or {}).get("external_chat_context")
    if not isinstance(external_context, dict):
        return None
    source = str(external_context.get("source") or "").strip()
    if source not in {"youtube_live", "youtube_live_director"}:
        return None
    hosting = external_context.get("live_hosting")
    return hosting if isinstance(hosting, dict) and hosting else None


def _live_episode_plan_for_external_context(extra_session_ctx: dict | None) -> dict | None:
    external_context = (extra_session_ctx or {}).get("external_chat_context")
    if not isinstance(external_context, dict):
        return None
    source = str(external_context.get("source") or "").strip()
    if source not in {"youtube_live", "youtube_live_director"}:
        return None
    live_episode_plan = external_context.get("live_episode_plan")
    return live_episode_plan if isinstance(live_episode_plan, dict) and live_episode_plan else None


def _final_closing_hint_for_external_context(extra_session_ctx: dict | None) -> bool:
    external_context = (extra_session_ctx or {}).get("external_chat_context")
    if not isinstance(external_context, dict):
        return False
    source = str(external_context.get("source") or "").strip()
    if source not in {"youtube_live", "youtube_live_director"}:
        return False
    turn_control = external_context.get("turn_control")
    if not isinstance(turn_control, dict):
        return False
    return turn_control.get("final_closing") is True


def _router_intent_for_external_context(extra_session_ctx: dict | None) -> dict | None:
    external_context = _youtube_live_director_external_context(extra_session_ctx)
    if not external_context:
        return None
    summary = external_context.get("summary") if isinstance(external_context.get("summary"), dict) else {}
    turn_control = (
        external_context.get("turn_control")
        if isinstance(external_context.get("turn_control"), dict)
        else {}
    )
    raw_action = _first_non_empty(
        external_context.get("raw_action"),
        external_context.get("action"),
        summary.get("raw_action"),
        summary.get("action"),
        turn_control.get("source_action"),
    )
    intent = {"source": "youtube_live_director"}
    normalized_action = _normalize_youtube_live_router_action(raw_action)
    if normalized_action:
        intent["action"] = normalized_action
    if raw_action:
        intent["raw_action"] = raw_action
    event_count = _first_non_empty(external_context.get("event_count"), summary.get("event_count"))
    if event_count is not None:
        try:
            intent["event_count"] = int(event_count)
        except (TypeError, ValueError):
            pass
    source_session_id = _first_non_empty(external_context.get("source_session_id"), summary.get("source_session_id"))
    if source_session_id:
        intent["source_session_id"] = source_session_id
    current_topic = _first_non_empty(external_context.get("current_topic"), summary.get("current_topic"))
    if current_topic:
        intent["current_topic"] = current_topic
    return intent


def _youtube_live_director_external_context(extra_session_ctx: dict | None) -> dict | None:
    if not isinstance(extra_session_ctx, dict):
        return None
    nested = extra_session_ctx.get("external_chat_context")
    candidates = [nested, extra_session_ctx]
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        if str(candidate.get("source") or "").strip() == "youtube_live_director":
            return candidate
    return None


def _normalize_youtube_live_router_action(raw_action: Any) -> str:
    action = str(raw_action or "").strip()
    if action == "reply_chat_batch":
        return "audience_response"
    if action == "reply_super_chat_batch":
        return "super_chat_response"
    return action


def _is_youtube_live_director_router_intent(current_turn_intent: dict | None) -> bool:
    return (
        isinstance(current_turn_intent, dict)
        and str(current_turn_intent.get("source") or "").strip() == "youtube_live_director"
    )


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                return stripped
            continue
        return value
    return None


def _compact_prompt_list(value: Any, *, limit: int = 4) -> list[str]:
    items = value if isinstance(value, list) else []
    return [
        " ".join(str(item or "").split())
        for item in items
        if str(item or "").strip()
    ][:limit]


def _build_live_episode_reply_task(
    live_episode_plan: dict | None,
    *,
    turn_index: int,
    max_turns: int,
    target_character_id: str,
    character_name: str,
    last_character_name: str,
    last_reply: str,
    discussion_mode: str,
) -> dict[str, Any]:
    if discussion_mode != "youtube_live" or not isinstance(live_episode_plan, dict) or not live_episode_plan:
        return {}
    reply_index = max(1, int(turn_index or 0) + 1)
    max_role_replies = _live_episode_max_role_replies(live_episode_plan, max_turns)
    stage = _live_episode_reply_stage(reply_index)
    segment_memory = (
        live_episode_plan.get("segment_memory")
        if isinstance(live_episode_plan.get("segment_memory"), dict)
        else {}
    )
    previous_claims = [
        str(item).strip()
        for item in segment_memory.get("covered_claims") or []
        if str(item).strip()
    ]
    turn_contract = (
        live_episode_plan.get("turn_contract")
        if isinstance(live_episode_plan.get("turn_contract"), dict)
        else {}
    )
    focus_policy = (
        live_episode_plan.get("focus_policy")
        if isinstance(live_episode_plan.get("focus_policy"), dict)
        else {}
    )
    evidence_policy = (
        live_episode_plan.get("evidence_policy")
        if isinstance(live_episode_plan.get("evidence_policy"), dict)
        else {}
    )
    forbidden_repetition = (
        live_episode_plan.get("forbidden_repetition")
        if isinstance(live_episode_plan.get("forbidden_repetition"), dict)
        else {}
    )
    task = {
        "stage": stage,
        "turn_reply_index": reply_index,
        "max_role_replies": max_role_replies,
        "target_character_id": str(target_character_id or "").strip(),
        "target_character_name": str(character_name or "").strip(),
        "turn_id": str(live_episode_plan.get("turn_id") or turn_contract.get("turn_id") or "").strip(),
        "turn_type": str(live_episode_plan.get("turn_type") or turn_contract.get("turn_type") or "").strip(),
        "previous_claims": previous_claims,
    }
    must_cover = _compact_prompt_list(focus_policy.get("must_cover"))
    forbidden_claims = _compact_prompt_list(forbidden_repetition.get("claims"))
    forbidden_phrases = _compact_prompt_list(forbidden_repetition.get("phrases"), limit=6)
    if must_cover:
        task["must_cover"] = must_cover
    allow_unverified_claims = evidence_policy.get("allow_unverified_claims")
    if isinstance(allow_unverified_claims, bool):
        task["allow_unverified_claims"] = allow_unverified_claims
    if forbidden_claims:
        task["forbidden_claims"] = forbidden_claims
    if forbidden_phrases:
        task["forbidden_phrases"] = forbidden_phrases
    if last_character_name:
        task["previous_speaker_name"] = str(last_character_name or "").strip()
    if last_reply:
        task["previous_reply"] = str(last_reply or "").strip()
    return {key: value for key, value in task.items() if value not in ("", [], {})}


def _live_episode_max_role_replies(live_episode_plan: dict, max_turns: int) -> int:
    dialogue_policy = (
        live_episode_plan.get("dialogue_policy")
        if isinstance(live_episode_plan.get("dialogue_policy"), dict)
        else {}
    )
    try:
        policy_limit = int(dialogue_policy.get("max_replies") or max_turns)
    except (TypeError, ValueError):
        policy_limit = max_turns
    return max(1, min(int(max_turns or 1), policy_limit, MAX_GROUP_TURNS_HARD_LIMIT))


def _live_episode_reply_stage(reply_index: int) -> str:
    if reply_index <= 1:
        return "primary_point"
    if reply_index == 2:
        return "reaction_translate_or_new_angle"
    return "bridge_close_only"


def _character_by_id(characters: list[dict], character_id: str) -> dict | None:
    for char in characters:
        if char.get("character_id") == character_id:
            return char
    return None


def _participant_ids(characters: list[dict]) -> set[str]:
    return {str(char.get("character_id") or "").strip() for char in characters if char.get("character_id")}


def _latest_assistant_speaker_id(messages: list[dict], valid_ids: set[str]) -> str | None:
    for msg in reversed(messages):
        if msg.get("role") != "assistant":
            continue
        cid = str(msg.get("character_id") or "").strip()
        if cid in valid_ids:
            return cid
    return None


def _spoken_participant_ids_after_latest_user(messages: list[dict], valid_ids: set[str]) -> set[str]:
    latest_user_index = None
    for idx in range(len(messages) - 1, -1, -1):
        if messages[idx].get("role") == "user":
            latest_user_index = idx
            break
    if latest_user_index is None:
        return set()

    spoken: set[str] = set()
    for msg in messages[latest_user_index + 1:]:
        if msg.get("role") != "assistant":
            continue
        cid = str(msg.get("character_id") or "").strip()
        if cid in valid_ids:
            spoken.add(cid)
    return spoken


def _fallback_first_turn_target(
    participants: list[dict],
    messages: list[dict],
    last_speaker_id: str | None,
) -> str | None:
    valid_ids = _participant_ids(participants)
    spoken_ids = _spoken_participant_ids_after_latest_user(messages, valid_ids)
    unspoken_ids = [
        char["character_id"]
        for char in participants
        if char.get("character_id") in valid_ids and char.get("character_id") not in spoken_ids
    ]
    for cid in unspoken_ids:
        if cid != last_speaker_id:
            return cid
    if unspoken_ids:
        return unspoken_ids[0]
    for char in participants:
        cid = char.get("character_id")
        if cid and cid != last_speaker_id:
            return cid
    return participants[0].get("character_id") if participants else None


def _build_followup_prompt(user_prompt: str, last_character_name: str, last_reply: str) -> str:
    return build_group_followup_instruction(
        {
            "user_prompt_original": user_prompt,
            "last_character_name": last_character_name,
            "last_reply": last_reply,
            "conversation_intent": "",
            "routing_action": "",
        },
        user_prompt,
    )
