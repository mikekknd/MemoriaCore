"""Module B (Middleware) 測試：並行工具執行與過渡語音推播"""
import pytest
from unittest.mock import patch, MagicMock
from core.chat_orchestrator.middleware import run_middleware
from core.chat_orchestrator.dataclasses import RouterResult, ToolContext


class TestRunMiddleware:
    def test_thinking_speech_triggers_callback(self):
        """有 thinking_speech 時 on_thinking_speech 應被呼叫"""
        router_result = RouterResult(
            needs_tools=True,
            tool_calls=[{
                "id": "call_tavily",
                "type": "function",
                "function": {"name": "tavily_search", "arguments": {"query": "比特幣"}},
            }],
            thinking_speech="讓我查一下...",
        )

        callback_calls = []
        on_thinking = lambda s: callback_calls.append(("thinking", s))

        with patch('tools.tavily.execute_tool_call', return_value='{"answer": "95000"}'):
            ctx = run_middleware(router_result, on_thinking_speech=on_thinking)

        assert ("thinking", "讓我查一下...") in callback_calls
        assert ctx.thinking_speech_sent == "讓我查一下..."

    def test_empty_tools_returns_empty_results(self):
        """無工具時 tool_results 應為空（格式化文字仍會有 header/footer 但無內容）"""
        router_result = RouterResult(needs_tools=False, thinking_speech="")

        ctx = run_middleware(router_result)

        assert ctx.tool_results == []
        assert "<tool_results>" in ctx.tool_results_formatted  # wrapper 仍在
        assert ctx.thinking_speech_sent == ""

    def test_tool_status_callback_per_tool(self):
        """每個 tool 應觸發一次 on_tool_status(calling)"""
        router_result = RouterResult(
            needs_tools=True,
            tool_calls=[
                {"id": "call_1", "type": "function", "function": {"name": "tavily_search", "arguments": {"query": "a"}}},
                {"id": "call_2", "type": "function", "function": {"name": "weather", "arguments": {"query": "b"}}},
            ],
            thinking_speech="",
        )

        status_calls = []
        on_status = lambda d: status_calls.append(d)

        with patch('tools.tavily.execute_tool_call', return_value='{}'):
            run_middleware(router_result, on_tool_status=on_status)

        calling_calls = [c for c in status_calls if c.get("action") == "calling"]
        assert len(calling_calls) == 2

    def test_complete_status_after_tools(self):
        """所有工具完成後應觸發一次 on_tool_status(complete)"""
        router_result = RouterResult(
            needs_tools=True,
            tool_calls=[{"id": "call_1", "type": "function", "function": {"name": "tavily_search", "arguments": {"query": "test"}}}],
            thinking_speech="",
        )

        status_calls = []
        on_status = lambda d: status_calls.append(d)

        with patch('tools.tavily.execute_tool_call', return_value='{}'):
            run_middleware(router_result, on_tool_status=on_status)

        complete_calls = [c for c in status_calls if c.get("action") == "complete"]
        assert len(complete_calls) == 1

    def test_formatted_results_structure(self):
        """工具結果格式化驗證"""
        router_result = RouterResult(
            needs_tools=True,
            tool_calls=[{
                "id": "call_tavily",
                "type": "function",
                "function": {"name": "tavily_search", "arguments": {"query": "比特幣"}},
            }],
            thinking_speech="",
        )

        with patch('tools.tavily.execute_tool_call', return_value='{"answer": "比特幣: $95000"}'):
            ctx = run_middleware(router_result)

        assert "tavily_search" in ctx.tool_results_formatted
        assert "$95000" in ctx.tool_results_formatted
        assert len(ctx.tool_results) == 1
        assert ctx.tool_results[0]["tool_name"] == "tavily_search"

    def test_multiple_tools_parallel_execution(self):
        """多工具時應並行執行"""
        import time
        router_result = RouterResult(
            needs_tools=True,
            tool_calls=[
                {"id": "call_1", "type": "function", "function": {"name": "slow_tool", "arguments": {}}},
                {"id": "call_2", "type": "function", "function": {"name": "slow_tool", "arguments": {}}},
            ],
            thinking_speech="",
        )

        def slow_execute(tc):
            time.sleep(0.1)
            return '{"result": "done"}'

        with patch('tools.tavily.execute_tool_call', side_effect=slow_execute):
            start = time.time()
            ctx = run_middleware(router_result)
            elapsed = time.time() - start

        # 兩個 0.1s 並行應該 < 0.25s（串行會是 0.2s）
        assert elapsed < 0.25, f"執行時間 {elapsed:.2f}s，超過並行預期"
        assert len(ctx.tool_results) == 2

    def test_tool_execution_error_handled(self):
        """工具執行例外時應產生 error result"""
        router_result = RouterResult(
            needs_tools=True,
            tool_calls=[{"id": "call_fail", "type": "function", "function": {"name": "fail_tool", "arguments": {}}}],
            thinking_speech="",
        )

        def fail_execute(tc):
            raise RuntimeError("工具執行失敗")

        with patch('tools.tavily.execute_tool_call', side_effect=fail_execute):
            ctx = run_middleware(router_result)

        assert len(ctx.tool_results) == 1
        assert "error" in ctx.tool_results[0]["result"]
        assert "工具執行失敗" in ctx.tool_results[0]["result"]

    def test_no_callbacks_does_not_crash(self):
        """on_thinking_speech=None 時不應拋例外"""
        router_result = RouterResult(
            needs_tools=True,
            tool_calls=[{"id": "call_1", "type": "function", "function": {"name": "tavily_search", "arguments": {"query": "test"}}}],
            thinking_speech="思考中",
        )

        with patch('tools.tavily.execute_tool_call', return_value='{}'):
            ctx = run_middleware(router_result, on_thinking_speech=None, on_tool_status=None)

        assert ctx.tool_results[0]["tool_name"] == "tavily_search"


class TestToolContextOutput:
    def test_thinking_speech_sent_preserved(self):
        """thinking_speech_sent 應正確保留"""
        router_result = RouterResult(
            needs_tools=True,
            tool_calls=[{"id": "c", "type": "function", "function": {"name": "t", "arguments": {}}}],
            thinking_speech="查詢中...",
        )

        with patch('tools.tavily.execute_tool_call', return_value='{}'):
            ctx = run_middleware(router_result)

        assert ctx.thinking_speech_sent == "查詢中..."

    def test_empty_thinking_speech(self):
        """無 thinking_speech 時 thinking_speech_sent 為空"""
        router_result = RouterResult(needs_tools=True, tool_calls=[], thinking_speech="")

        ctx = run_middleware(router_result)

        assert ctx.thinking_speech_sent == ""
