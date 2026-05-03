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
) -> list[dict[str, Any]]:
    """執行一輪使用者輸入後的多 AI 接力，並負責持久化 assistant turn。"""
    participants = get_session_characters(session)
    if not participants:
        return []

    max_turns = _group_turn_limit(user_prefs)
    turn_delay_seconds = _group_turn_delay_seconds(user_prefs)
    working_messages = list(session.messages)
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

    for turn_index in range(max_turns):
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
        )
        if not route.should_respond or not route.target_character_id:
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
                "user_prompt_original": user_prompt,
                "conversation_intent": route.conversation_intent,
                "routing_action": route.action,
            }

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
        }
        if extra_session_ctx:
            session_ctx.update(extra_session_ctx)

        result = await asyncio.to_thread(
            orchestration_fn,
            generation_messages,
            last_entities,
            user_prompt,
            user_prefs,
            on_event=on_event,
            session_ctx=session_ctx,
        )
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
        if not was_final and on_turn:
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
