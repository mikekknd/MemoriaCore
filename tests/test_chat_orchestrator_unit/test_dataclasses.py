"""dataclasses 測試：RouterResult, ToolContext, PersonaResult"""
import pytest
from core.chat_orchestrator.dataclasses import RouterResult, ToolContext, PersonaResult


class TestRouterResult:
    def test_defaults(self):
        """預設值驗證"""
        r = RouterResult(needs_tools=False)
        assert r.needs_tools is False
        assert r.tool_calls == []
        assert r.thinking_speech == ""

    def test_full_construction(self):
        """完整建構驗證"""
        r = RouterResult(
            needs_tools=True,
            tool_calls=[{"id": "call_1", "function": {"name": "tavily_search"}}],
            thinking_speech="讓我查一下...",
        )
        assert r.needs_tools is True
        assert len(r.tool_calls) == 1
        assert r.tool_calls[0]["function"]["name"] == "tavily_search"
        assert r.thinking_speech == "讓我查一下..."

    def test_tool_calls_defaults_to_empty_list(self):
        """tool_calls 預設為空 list（不是 None）"""
        r = RouterResult(needs_tools=True)
        assert r.tool_calls == []
        assert r.tool_calls is not None


class TestToolContext:
    def test_defaults(self):
        """預設值驗證"""
        t = ToolContext()
        assert t.tool_results == []
        assert t.tool_results_formatted == ""
        assert t.thinking_speech_sent == ""

    def test_full_construction(self):
        """完整建構驗證"""
        t = ToolContext(
            tool_results=[{"tool_name": "tavily", "result": '{"answer": "台北25度"}'}],
            tool_results_formatted="【tavily 查詢結果】\n台北25度",
            thinking_speech_sent="讓我查一下...",
        )
        assert len(t.tool_results) == 1
        assert t.tool_results[0]["tool_name"] == "tavily"
        assert "台北25度" in t.tool_results_formatted
        assert t.thinking_speech_sent == "讓我查一下..."

    def test_multiple_tool_results(self):
        """多工具結果驗證"""
        t = ToolContext(
            tool_results=[
                {"tool_name": "tavily", "result": "result1"},
                {"tool_name": "weather", "result": "result2"},
            ]
        )
        assert len(t.tool_results) == 2


class TestPersonaResult:
    def test_defaults(self):
        """預設值驗證"""
        p = PersonaResult()
        assert p.reply_text == ""
        assert p.new_entities == []
        assert p.inner_thought is None
        assert p.status_metrics is None
        assert p.tone is None
        assert p.speech is None

    def test_full_construction(self):
        """完整建構驗證"""
        p = PersonaResult(
            reply_text="比特幣目前約 95000 美元",
            new_entities=["比特幣", "美元"],
            inner_thought="比特幣查詢結果",
            status_metrics={"professionalism": 85, "friendliness": 90},
            tone="Professional",
            speech="比特幣目前約九萬五千美元",
        )
        assert p.reply_text == "比特幣目前約 95000 美元"
        assert "比特幣" in p.new_entities
        assert p.status_metrics["professionalism"] == 85
        assert p.tone == "Professional"
        assert p.speech == "比特幣目前約九萬五千美元"

    def test_reply_text_only(self):
        """僅設定 reply_text 時其他欄位為 None"""
        p = PersonaResult(reply_text="僅回覆文字")
        assert p.reply_text == "僅回覆文字"
        assert p.inner_thought is None
        assert p.new_entities == []
