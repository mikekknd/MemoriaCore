"""Module A — Router Agent：輕量 LLM 判斷是否需要呼叫工具，並產生過渡語音。

關鍵設計：
- 注入 dummy `direct_chat` tool，讓 LLM 在「真工具」與「純聊天」之間做多選，
  避免單選 schema 下的 hallucination。
- LLM 輸出的 content 視為過渡語（thinking_speech），由 Module B 立刻推播給前端 TTS。
"""
from core.system_logger import SystemLogger
from core.prompt_manager import get_prompt_manager
from core.chat_orchestrator.dataclasses import RouterResult


# ════════════════════════════════════════════════════════════
# SECTION: 路由器 Prompt 與 Dummy Tool Schema
# ════════════════════════════════════════════════════════════

def _get_router_prompt():
    return get_prompt_manager().get("router_system")


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


# ════════════════════════════════════════════════════════════
# SECTION: run_router_agent
# ════════════════════════════════════════════════════════════

def run_router_agent(
    user_prompt: str,
    char_hint: str,
    tools_list: list[dict],
    router,
    temperature: float = 0.7,
    recent_history: list[dict] | None = None,
) -> RouterResult:
    """
    Module A — 輕量 LLM 判斷是否需要工具，並產生過渡語音。

    Args:
        user_prompt: 使用者的原始輸入。
        char_hint: 角色語氣的一行描述（例如「傲嬌女僕，語氣帶點不耐煩但其實很認真」）。
        tools_list: 所有可用的 Tool Schema 列表（不含 direct_chat，會自動注入）。
        router: LLMRouter 實例。
        temperature: LLM 溫度參數。
        recent_history: 最近的對話歷史（用於提供上下文判斷意圖）。
                        ⚠️ 不應包含當前 user_prompt（會自行追加），否則重複。

    Returns:
        RouterResult — needs_tools / tool_calls / thinking_speech。
    """
    # 注入 dummy tool，讓 LLM 在「真工具」與「純聊天」之間做多選
    augmented_tools = tools_list + [DIRECT_CHAT_SCHEMA]

    sys_prompt = _get_router_prompt().format(char_hint=char_hint)
    messages = [{"role": "system", "content": sys_prompt}]

    # 注入最近對話歷史，讓 Router Agent 有上下文可判斷意圖
    if recent_history:
        for m in recent_history[-6:]:
            messages.append({"role": m["role"], "content": m["content"]})

    messages.append({"role": "user", "content": user_prompt})

    try:
        content, tool_calls = router.generate_with_tools(
            "router", messages, tools=augmented_tools, temperature=temperature,
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
            thinking_speech = (content or "").strip()
            return RouterResult(
                needs_tools=True,
                tool_calls=real_tool_calls,
                thinking_speech=thinking_speech,
            )

    return RouterResult(needs_tools=False)
