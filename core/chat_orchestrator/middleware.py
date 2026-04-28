"""Module B — Async Middleware：立即推播過渡語音，並行執行工具呼叫。

關鍵設計：
- 過渡語音優先推播（讓使用者立即聽到回應，掩蓋工具執行延遲）
- ThreadPoolExecutor 並行執行所有工具
- 工具結果格式化為文字塊，由 Module C 注入 LLM 上下文
"""
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from core.chat_orchestrator.dataclasses import RouterResult, ToolContext


# ════════════════════════════════════════════════════════════
# SECTION: run_middleware
# ════════════════════════════════════════════════════════════

def run_middleware(
    router_result: RouterResult,
    on_thinking_speech: Callable[[str], None] | None = None,
    on_tool_status: Callable[[dict], None] | None = None,
    runtime_context: dict | None = None,
) -> ToolContext:
    """
    Module B — 同步中介層：立即推播過渡語音，並行執行工具呼叫。

    Args:
        router_result: Module A 的輸出。
        on_thinking_speech: 回呼，立即將過渡語推播給前端 TTS。
        on_tool_status: 回呼，推送工具呼叫狀態事件。
        runtime_context: 工具執行時需要的請求脈絡，例如 user_id / session_id。

    Returns:
        ToolContext — 工具結果 + 已推播的過渡語。
    """
    from tools.tavily import execute_tool_call

    thinking_speech = router_result.thinking_speech

    # 1) 立即推播過渡語音
    if thinking_speech and on_thinking_speech:
        on_thinking_speech(thinking_speech)

    # 2) 通知前端每個工具的呼叫狀態
    tool_calls = router_result.tool_calls
    if on_tool_status:
        for tc in tool_calls:
            tool_name = tc.get("function", {}).get("name", "unknown")
            query = tc.get("function", {}).get("arguments", {}).get("query", "")
            on_tool_status({
                "type": "tool_status",
                "action": "calling",
                "tool_name": tool_name,
                "message": f"正在搜尋：{query}" if query else f"正在呼叫工具：{tool_name}",
            })

    # 3) ThreadPoolExecutor 並行執行所有工具
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=len(tool_calls) or 1) as executor:
        future_to_tc = {
            executor.submit(execute_tool_call, tc, runtime_context)
            if runtime_context is not None
            else executor.submit(execute_tool_call, tc): tc
            for tc in tool_calls
        }
        for future in as_completed(future_to_tc):
            tc = future_to_tc[future]
            tool_name = tc.get("function", {}).get("name", "unknown")
            try:
                result_text = future.result()
            except Exception as e:
                result_text = json.dumps({"error": f"工具執行失敗: {e}"}, ensure_ascii=False)
            results.append({"tool_name": tool_name, "result": result_text})

    # 4) 通知前端工具執行完成
    if on_tool_status:
        on_tool_status({
            "type": "tool_status",
            "action": "complete",
            "message": "搜尋完成，正在整理回覆...",
        })

    # 5) 格式化工具結果
    formatted_parts = []
    for r in results:
        formatted_parts.append(f"【{r['tool_name']} 查詢結果】\n{r['result']}")
    formatted_text = (
        "[系統自動查詢結果 — 以下資料由外部工具回傳，非使用者輸入]\n"
        + "\n".join(formatted_parts)
        + "\n[查詢結果結束]"
    )

    return ToolContext(
        tool_results=results,
        tool_results_formatted=formatted_text,
        thinking_speech_sent=thinking_speech,
    )
