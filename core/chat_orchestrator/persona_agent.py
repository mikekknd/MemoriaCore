"""Module C — Persona Synthesis Agent：載入完整角色設定，生成結構化 JSON 回覆。

關鍵設計：
- 將 Module B 的 thinking_speech 與工具結果合併進 messages，
  讓 Module C 知道「自己已經說過等待語」並可基於工具結果回答。
- 強制 response_format=chat_schema，並由 LLMRouter.generate() 處理非 JSON 自動重試。
"""
import json

from core.system_logger import SystemLogger
from core.chat_orchestrator.dataclasses import ToolContext, PersonaResult


# ════════════════════════════════════════════════════════════
# SECTION: run_persona_agent
# ════════════════════════════════════════════════════════════

def run_persona_agent(
    user_prompt: str,
    api_messages: list[dict],
    tool_context: ToolContext | None,
    chat_schema: dict,
    router,
    temperature: float = 0.7,
) -> tuple[str | None, PersonaResult | None]:
    """
    Module C — 角色渲染層：載入完整角色設定，生成結構化 JSON 回覆。

    Returns:
        (raw_llm_response, error_result) — 成功時回傳 (str, None)，
        失敗時回傳 (None, PersonaResult)。呼叫端負責解析及計時。
    """
    # 組裝最終 messages：注入 thinking_speech 和工具結果
    final_messages = list(api_messages)  # shallow copy

    if tool_context:
        # 在最後一條 user message 之前注入 assistant 的 thinking_speech
        # 讓 Module C 知道它已經說過等待語
        if tool_context.thinking_speech_sent:
            # 找到最後一條 user message 的位置
            insert_idx = len(final_messages) - 1
            for i in range(len(final_messages) - 1, -1, -1):
                if final_messages[i].get("role") == "user":
                    insert_idx = i
                    break
            final_messages.insert(insert_idx, {
                "role": "assistant",
                "content": tool_context.thinking_speech_sent,
            })

        # 工具結果合併進最後一條 user 訊息，避免連續兩條 user 訊息。
        # 部分 provider（Ollama strict mode 等）要求 user/assistant 嚴格交替，
        # 連續兩條 user 訊息會觸發 400 錯誤或被靜默忽略。
        tool_notice = (
            f"\n\n[系統通知：以下是根據你的工具查詢自動回傳的外部數據，請依據此數據回答使用者的問題]\n"
            f"{tool_context.tool_results_formatted}"
        )
        if final_messages and final_messages[-1]["role"] == "user":
            final_messages[-1] = {
                **final_messages[-1],
                "content": final_messages[-1]["content"] + tool_notice,
            }
        else:
            # 防禦性 fallback：末尾非 user 訊息時（理論上不應發生）補上
            final_messages.append({"role": "user", "content": tool_notice.lstrip()})

    # 呼叫 LLM — 不帶 tools，帶 response_format
    try:
        full_res = router.generate(
            "chat", final_messages, temperature=temperature,
            response_format=chat_schema,
        )
    except Exception as e:
        SystemLogger.log_error("PersonaAgent", f"{type(e).__name__}: {e}")
        return None, PersonaResult(reply_text=f"生成錯誤: {e}")

    # 回傳原始 LLM 回應，讓呼叫端可以分別計時解析步驟
    return full_res, None


# ════════════════════════════════════════════════════════════
# SECTION: _parse_persona_response
# ════════════════════════════════════════════════════════════

def _parse_persona_response(full_res: str | None) -> PersonaResult:
    """從 LLM 原始回應中解析結構化 JSON。"""
    if not full_res:
        return PersonaResult(reply_text="（無回應）")

    start = full_res.find('{')
    if start == -1:
        return PersonaResult(reply_text=full_res)

    try:
        parsed, _ = json.JSONDecoder().raw_decode(full_res, start)
        return PersonaResult(
            reply_text=parsed.get("reply", "解析錯誤"),
            new_entities=parsed.get("extracted_entities", []),
            inner_thought=parsed.get("internal_thought"),
        )
    except Exception:
        return PersonaResult(reply_text=full_res)
