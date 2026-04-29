"""群組對話回合控制，共用於 REST / SSE / WebSocket。"""
import asyncio
import inspect
from typing import Callable, Any

from api.dependencies import get_character_manager, get_router
from api.session_manager import SessionState, session_manager
from api.routers.chat.orchestration import _unpack_orchestration_result
from api.routers.chat.pipeline import _run_memory_pipeline_bg
from core.chat_orchestrator.group_router import run_group_router
from core.prompt_manager import get_prompt_manager


MAX_GROUP_TURNS_HARD_LIMIT = 5
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
    last_speaker_id: str | None = None
    last_reply = ""
    last_character_name = ""

    for turn_index in range(max_turns):
        route = await asyncio.to_thread(
            run_group_router,
            working_messages,
            participants,
            get_router(),
            temperature=0.0,
            last_speaker_id=last_speaker_id,
            honor_mentions=(turn_index == 0),
        )
        if not route.should_respond or not route.target_character_id:
            if turn_index == 0:
                target_character_id = participants[0]["character_id"]
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

        generation_messages = list(working_messages)
        generation_prompt = user_prompt
        if turn_index > 0:
            generation_prompt = _build_followup_prompt(
                user_prompt=user_prompt,
                last_character_name=last_character_name,
                last_reply=last_reply,
            )
            generation_messages = generation_messages + [{"role": "user", "content": generation_prompt}]

        session_ctx = {
            "user_id": session.user_id,
            "character_id": target_character_id,
            "persona_face": session.persona_face,
            "session_id": session.session_id,
            "bot_id": session.bot_id,
            "channel": session.channel,
            "profile_allowed": turn_index == 0,
        }

        result = await asyncio.to_thread(
            orchestration_fn,
            generation_messages,
            last_entities,
            generation_prompt,
            user_prefs,
            on_event=on_event,
            session_ctx=session_ctx,
        )
        reply_text, new_entities, retrieval_ctx, topic_shifted, pipeline_data, \
            inner_thought, status_metrics, tone, speech, thinking_speech, cited_uids = \
            _unpack_orchestration_result(result)

        saved_reply_text = reply_text
        if cited_uids:
            saved_reply_text = f"{reply_text} " + " ".join([f"[Ref: {u}]" for u in cited_uids])

        await session_manager.add_assistant_message(
            session.session_id,
            saved_reply_text,
            retrieval_ctx,
            new_entities,
            persona_state={"internal_thought": inner_thought},
            character_name=character_name,
            character_id=target_character_id,
        )

        assistant_msg = {
            "role": "assistant",
            "content": saved_reply_text,
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
        turns[-1]["is_final"] = True
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


def _build_followup_prompt(user_prompt: str, last_character_name: str, last_reply: str) -> str:
    return get_prompt_manager().get("group_followup_user").format(
        user_prompt=user_prompt,
        last_character_name=last_character_name,
        last_reply=last_reply,
    )
