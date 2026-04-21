"""Module A — Router Agent：輕量 LLM 判斷是否需要呼叫工具。

關鍵設計：
- 注入 dummy `direct_chat` tool，讓 LLM 在「真工具」與「純聊天」之間做多選，
  避免單選 schema 下的 hallucination。
- 只輸出 tool call，不產生文字；thinking_speech 由 coordinator 用模板生成。
"""
from core.system_logger import SystemLogger
from core.prompt_manager import get_prompt_manager
from core.chat_orchestrator.dataclasses import RouterResult


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


# ════════════════════════════════════════════════════════════
# SECTION: run_router_agent
# ════════════════════════════════════════════════════════════

def run_router_agent(
    user_prompt: str,
    tools_list: list[dict],
    router,
    temperature: float = 0.7,
    recent_history: list[dict] | None = None,
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

    Returns:
        RouterResult — needs_tools / tool_calls。
    """
    # 注入 dummy tool，讓 LLM 在「真工具」與「純聊天」之間做多選
    augmented_tools = tools_list + [DIRECT_CHAT_SCHEMA]

    sys_prompt = _get_router_prompt()
    messages = [{"role": "system", "content": sys_prompt}]

    # 注入最近對話歷史，保留 user/assistant 交替結構以還原對話節奏。
    # assistant 訊息以 "[已回覆]" 替代全文：讓 router 知道上一輪已完成 exchange，
    # 避免將「對結果的後續回應」誤判為新工具需求；同時防止角色扮演台詞污染路由判斷。
    if recent_history:
        for m in recent_history[-8:]:
            if m["role"] == "user":
                messages.append({"role": "user", "content": m["content"]})
            elif m["role"] == "assistant":
                messages.append({"role": "assistant", "content": "[已回覆]"})

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
            return RouterResult(needs_tools=True, tool_calls=real_tool_calls)

    return RouterResult(needs_tools=False)
