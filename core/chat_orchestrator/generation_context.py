"""單層 / 雙層 chat 編排共用的 scope、tool 與 prompt/context 組裝。"""
from __future__ import annotations

from dataclasses import dataclass

from core.prompt_manager import get_prompt_manager
from core.prompt_utils import (
    build_retrieved_memory_context_user_block,
    build_user_prefix,
    format_latest_user_message_for_llm,
)
from core.chat_orchestrator.dialogue_format import format_history_for_llm
from core.chat_orchestrator.group_context import is_group_context


@dataclass(frozen=True)
class OrchestrationScope:
    ctx: dict
    user_id: str
    character_id: str
    persona_face: str
    write_visibility: str
    visibility_filter: list[str]
    force_group: bool


def resolve_orchestration_scope(session_ctx: dict | None) -> OrchestrationScope:
    ctx = session_ctx or {}
    user_id = ctx.get("user_id", "default")
    character_id = ctx.get("character_id", "default")
    persona_face = ctx.get("persona_face", "public")
    write_visibility = persona_face
    visibility_filter = ["private", "public"] if persona_face == "private" else ["public"]
    return OrchestrationScope(
        ctx=ctx,
        user_id=user_id,
        character_id=character_id,
        persona_face=persona_face,
        write_visibility=write_visibility,
        visibility_filter=visibility_filter,
        force_group=is_group_context(ctx),
    )


def build_chat_response_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "internal_thought": {"type": "string"},
            "reply": {"type": "string"},
            "extracted_entities": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["internal_thought", "reply", "extracted_entities"],
    }


def _tool_calls_disabled_for_context(session_ctx: dict | None) -> bool:
    if not isinstance(session_ctx, dict):
        return False
    if str(session_ctx.get("channel") or "").strip() == "youtube_live":
        return True
    external_context = session_ctx.get("external_chat_context")
    if not isinstance(external_context, dict):
        return False
    source = str(external_context.get("source") or "").strip()
    return source in {"youtube_live", "youtube_live_director"}


def memory_lookup_skip_reason(session_ctx: dict | None) -> str | None:
    """回傳記憶檢索應跳過的原因；None 表示照常跑記憶檢索。"""
    if not isinstance(session_ctx, dict):
        return None
    external_context = session_ctx.get("external_chat_context")
    source = ""
    if isinstance(external_context, dict):
        source = str(external_context.get("source") or "").strip()
    if source in {"youtube_live", "youtube_live_director"}:
        return source
    if str(session_ctx.get("channel") or "").strip() == "youtube_live":
        return "youtube_live"
    return None


def build_available_tools(user_prefs: dict, session_ctx: dict | None = None) -> list[dict]:
    if _tool_calls_disabled_for_context(session_ctx):
        return []
    tools_list: list[dict] = []
    try:
        from tools.tavily import TAVILY_SEARCH_SCHEMA
        if user_prefs.get("tavily_api_key"):
            tools_list.append(TAVILY_SEARCH_SCHEMA)
    except ImportError:
        pass
    try:
        from tools.weather import WEATHER_SCHEMA
        if user_prefs.get("openweather_api_key"):
            tools_list.append(WEATHER_SCHEMA)
    except ImportError:
        pass
    try:
        from tools.bash_tool import BASH_TOOL_SCHEMA
        if user_prefs.get("bash_tool_enabled"):
            tools_list.append(BASH_TOOL_SCHEMA)
    except ImportError:
        pass
    try:
        from tools.browser_agent import BROWSER_AGENT_SCHEMA
        if user_prefs.get("browser_agent_enabled"):
            tools_list.append(BROWSER_AGENT_SCHEMA)
    except ImportError:
        pass
    try:
        from tools.minimax_image import GENERATE_IMAGE_SCHEMA, GENERATE_SELF_PORTRAIT_SCHEMA
        if user_prefs.get("image_generation_enabled") and user_prefs.get("minimax_api_key"):
            tools_list.append(GENERATE_IMAGE_SCHEMA)
            tools_list.append(GENERATE_SELF_PORTRAIT_SCHEMA)
    except ImportError:
        pass
    return tools_list


def build_final_chat_context(
    *,
    char_sys_prompt: str,
    group_participants_block: str,
    mem_ctx: str,
    reply_rules: str,
    session_messages: list[dict],
    context_window: int,
    user_prefs: dict,
    session_ctx: dict,
    force_group: bool,
) -> tuple[list[dict], list[dict], str]:
    pm = get_prompt_manager()
    speech_instruction = pm.get("chat_speech_instruction_no_tts").format(
        reply_rules=reply_rules,
    )
    suffix_key = "chat_system_suffix_youtube_live" if _is_youtube_live_prompt_context(session_ctx) else "chat_system_suffix"
    suffix = pm.get(suffix_key).format(
        mem_ctx="",
        speech_instruction=speech_instruction,
    )
    sys_prompt = f"""{char_sys_prompt}
{group_participants_block}
{suffix}"""

    api_messages = [{"role": "system", "content": sys_prompt}]
    clean_history = format_history_for_llm(session_messages[-context_window:], force_group=force_group)
    # 關鍵不變式：對話紀錄必須在 sys_prompt 後納入 LLM 上下文。
    api_messages.extend(clean_history)

    if api_messages and api_messages[-1]["role"] == "user":
        prefix = build_user_prefix(session_messages, user_prefs=user_prefs, session_ctx=session_ctx)
        memory_context = build_retrieved_memory_context_user_block(mem_ctx)
        latest_user = format_latest_user_message_for_llm(api_messages[-1]["content"], session_ctx)
        api_messages[-1] = {**api_messages[-1], "content": memory_context + prefix + latest_user}

    return api_messages, clean_history, sys_prompt


def _is_youtube_live_prompt_context(session_ctx: dict | None) -> bool:
    ctx = session_ctx or {}
    external = ctx.get("external_chat_context")
    if not isinstance(external, dict):
        return False
    return (
        str(ctx.get("channel") or "").strip() == "youtube_live"
        and str(external.get("source") or "").strip() in {"youtube_live", "youtube_live_director"}
    )


def build_history_preview(clean_history: list[dict]) -> str:
    ctx_preview_lines = []
    for message in clean_history:
        role_label = "使用者" if message["role"] == "user" else "助理"
        preview = message["content"][:300] + ("..." if len(message["content"]) > 300 else "")
        ctx_preview_lines.append(f"[{role_label}]: {preview}")
    if clean_history:
        return f"\n\n{'─'*40}\n[對話紀錄窗口（共 {len(clean_history)} 則）]\n" + "\n".join(ctx_preview_lines)
    return "\n\n[對話紀錄窗口：空（首輪對話）]"
