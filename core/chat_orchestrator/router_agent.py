"""Module A — Router Agent：輕量 LLM 判斷是否需要呼叫工具。

關鍵設計：
- 注入 dummy `direct_chat` tool，讓 LLM 在「真工具」與「純聊天」之間做多選，
  避免單選 schema 下的 hallucination。
- 只輸出 tool call，不產生文字；thinking_speech 由 coordinator 用模板生成。
"""
import json

from core.system_logger import SystemLogger
from core.prompt_manager import get_prompt_manager
from core.chat_orchestrator.dataclasses import RouterResult
from core.xml_prompt import xml_attr


# ════════════════════════════════════════════════════════════
# SECTION: 路由器 Prompt 與 Dummy Tool Schema
# ════════════════════════════════════════════════════════════

def _get_router_prompt() -> str:
    return get_prompt_manager().get("router_system").format()


# Dummy Tool Schema — 利用 function calling 的多選機制穩定意圖判定
DIRECT_CHAT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "direct_chat",
        "description": "當使用者只是在日常閒聊、表達主觀意見、分享心情或生活狀態，沒有明確詢問客觀知識或即時數據時，呼叫此工具將控制權直接交還角色進行對話。",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}


def _tool_names(tools: list[dict]) -> list[str]:
    names = []
    for tool in tools:
        name = tool.get("function", {}).get("name")
        if name:
            names.append(str(name))
    return names


def _format_messages_for_fallback(messages: list[dict]) -> str:
    lines = []
    for msg in messages:
        role = msg.get("role", "unknown")
        content = str(msg.get("content", ""))
        lines.append(f"[{role}]\n{content}")
    return "\n\n".join(lines)


def _fallback_schema(tool_names: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "selected_tool": {
                "type": "string",
                "enum": tool_names,
            },
            "arguments": {
                "type": "object",
            },
            "reason": {
                "type": "string",
            },
        },
        "required": ["selected_tool", "arguments", "reason"],
        "additionalProperties": False,
    }


def _coerce_fallback_tool_call(parsed: dict, augmented_tools: list[dict]) -> dict | None:
    selected = str(parsed.get("selected_tool") or "").strip()
    if not selected or selected == "direct_chat":
        return None

    valid_names = set(_tool_names(augmented_tools))
    if selected not in valid_names:
        return None

    arguments = parsed.get("arguments")
    if not isinstance(arguments, dict):
        arguments = {}
    return {
        "id": f"fallback_{selected}",
        "type": "function",
        "function": {
            "name": selected,
            "arguments": arguments,
        },
    }


def _run_json_fallback(
    *,
    router,
    messages: list[dict],
    augmented_tools: list[dict],
    original_content: str,
) -> RouterResult:
    tool_names = _tool_names(augmented_tools)
    if not tool_names:
        return RouterResult(needs_tools=False)

    try:
        prompt = get_prompt_manager().get("router_json_fallback").format(
            tools_json=json.dumps(augmented_tools, ensure_ascii=False, indent=2),
            conversation_messages=_format_messages_for_fallback(messages),
            original_content=original_content[:1200],
        )
        parsed = router.generate_json(
            "router",
            [{"role": "user", "content": prompt}],
            schema=_fallback_schema(tool_names),
            temperature=0.0,
        )
    except Exception as exc:
        SystemLogger.log_error("RouterAgent", f"JSON fallback failed: {type(exc).__name__}: {exc}")
        return RouterResult(needs_tools=False)

    if not isinstance(parsed, dict):
        return RouterResult(needs_tools=False)

    fallback_call = _coerce_fallback_tool_call(parsed, augmented_tools)
    if not fallback_call:
        return RouterResult(needs_tools=False)
    return RouterResult(needs_tools=True, tool_calls=[fallback_call])


# ════════════════════════════════════════════════════════════
# SECTION: run_router_agent
# ════════════════════════════════════════════════════════════

def run_router_agent(
    user_prompt: str,
    tools_list: list[dict],
    router,
    temperature: float = 0.7,
    recent_history: list[dict] | None = None,
    context_hints: dict | None = None,
) -> RouterResult:
    """
    Module A — 輕量 LLM 判斷是否需要工具。只輸出 tool call，不產生文字。

    Args:
        user_prompt: 使用者的原始輸入。
        tools_list: 所有可用的 Tool Schema 列表（不含 direct_chat，會自動注入）。
        router: LLMRouter 實例。
        temperature: LLM 溫度參數。
        recent_history: 最近的對話歷史（用於提供上下文判斷意圖）。
                        ⚠️ 不應包含當前 user_prompt（會自行追加），否則重複。
                        ⚠️ 呼叫端應先用 `strip_system_events` 過濾掉 role='system_event'，
                           本函式僅取 role='user' 訊息，但 system_event 經過 format_history_for_llm
                           會被改寫成 user 角色 + `<session_event>` 包裝，需在更上游剝除。
        context_hints: 上下文線索 dict（例如 user_profile_location、su_weather_city、
                       recent_mentions）。供 LLM 在使用者最新訊息缺少工具參數時參考；
                       若依然無法可靠解析，應改呼叫 direct_chat（見 router_system prompt）。

    Returns:
        RouterResult — needs_tools / tool_calls。
    """
    # 注入 dummy tool，讓 LLM 在「真工具」與「純聊天」之間做多選
    augmented_tools = tools_list + [DIRECT_CHAT_SCHEMA]

    sys_prompt = _get_router_prompt()
    if context_hints:
        hint_lines = [
            f'<hint key="{xml_attr(k)}">{v}</hint>'
            for k, v in context_hints.items()
            if v
        ]
        if hint_lines:
            sys_prompt = (
                f"{sys_prompt}\n\n<router_context_hints>\n"
                + "\n".join(hint_lines)
                + "\n</router_context_hints>"
            )
    messages = [{"role": "system", "content": sys_prompt}]

    # 注入最近對話歷史（僅 user 訊息），在歷史訊息後附加 [已處理] 標記，
    # 讓 router 知道該輪已有 AI 回覆，避免對後續回應誤判為新工具需求。
    # 不注入 assistant 角色訊息，防止 router LLM 模仿該模式輸出文字而非工具呼叫。
    if recent_history:
        user_msgs = [m for m in recent_history[-8:] if m["role"] == "user"]
        for m in user_msgs:
            messages.append({"role": "user", "content": f"{m['content']} [已處理]"})

    messages.append({"role": "user", "content": user_prompt})

    try:
        content, tool_calls = router.generate_with_tools(
            "router", messages, tools=augmented_tools, temperature=0.0,
            tool_choice="required",
        )
    except Exception as e:
        SystemLogger.log_error("RouterAgent", f"{type(e).__name__}: {e}")
        return RouterResult(needs_tools=False)

    if tool_calls:
        # 過濾掉 direct_chat — 它只是路由信號，不是真正的工具
        real_tool_calls = [
            tc for tc in tool_calls
            if tc.get("function", {}).get("name") != "direct_chat"
        ]
        if real_tool_calls:
            return RouterResult(needs_tools=True, tool_calls=real_tool_calls)

    # 某些 provider / cloud model 會忽略 tool_choice="required"，直接回自然語言。
    # 不能讓這段文字進入最終回覆，也不能靜默漏掉真正需要的工具，改用 JSON fallback 再判斷一次。
    if tools_list and content.strip():
        SystemLogger.log_error(
            "RouterAgent",
            "tool_choice=required 未產生 tool_calls，改用 JSON fallback。",
            details={"content_preview": content[:500]},
        )
        return _run_json_fallback(
            router=router,
            messages=messages,
            augmented_tools=augmented_tools,
            original_content=content,
        )

    return RouterResult(needs_tools=False)
