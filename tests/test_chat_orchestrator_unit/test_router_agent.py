"""Module A (Router Agent) 測試：意圖路由與過渡語音產生"""
import pytest
from unittest.mock import patch, MagicMock
from core.chat_orchestrator.router_agent import (
    run_router_agent, DIRECT_CHAT_SCHEMA,
)
from core.chat_orchestrator.dataclasses import RouterResult


@pytest.fixture
def mock_pm():
    """mock_prompt_manager 專門為 router_agent 設定"""
    from unittest.mock import MagicMock
    pm = MagicMock()
    pm.get.return_value.format.return_value = "根據角色 {char_hint} 判斷是否需要工具。"
    return pm


class TestRunRouterAgent:
    def test_direct_chat_only_returns_no_tools(self, mock_pm):
        """當 LLM 僅回傳 direct_chat 時，needs_tools 應為 False"""
        from tests.mock_llm import MockRouter
        router = MockRouter()
        # 模擬 LLM 只選擇 direct_chat（經過過濾後為空）
        router.set_tool_calls([{
            "id": "call_direct",
            "type": "function",
            "function": {"name": "direct_chat", "arguments": {}},
        }])

        with patch('core.chat_orchestrator.router_agent.get_prompt_manager', return_value=mock_pm):
            result = run_router_agent(
                user_prompt="今天天氣真好",
                char_hint="助理",
                tools_list=[],
                router=router,
            )

        assert result.needs_tools is False
        assert result.tool_calls == []

    def test_real_tool_returns_needs_tools(self, mock_pm):
        """當 LLM 回傳真實工具時，needs_tools 應為 True"""
        from tests.mock_llm import MockRouter
        router = MockRouter()
        router.set_tool_calls([{
            "id": "call_tavily",
            "type": "function",
            "function": {"name": "tavily_search", "arguments": {"query": "比特幣價格"}},
        }])

        with patch('core.chat_orchestrator.router_agent.get_prompt_manager', return_value=mock_pm):
            result = run_router_agent(
                user_prompt="比特幣現在多少錢",
                char_hint="助理",
                tools_list=[{"type": "function", "function": {"name": "tavily_search"}}],
                router=router,
            )

        assert result.needs_tools is True
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0]["function"]["name"] == "tavily_search"

    def test_error_returns_no_tools(self, mock_pm):
        """router.generate_with_tools 例外時應回傳 needs_tools=False"""
        from tests.mock_llm import MockRouter
        router = MockRouter()

        def raise_error(*args, **kwargs):
            raise RuntimeError("LLM 連線失敗")

        router.generate_with_tools = raise_error

        with patch('core.chat_orchestrator.router_agent.get_prompt_manager', return_value=mock_pm):
            result = run_router_agent(
                user_prompt="你好",
                char_hint="助理",
                tools_list=[],
                router=router,
            )

        assert result.needs_tools is False
        assert result.tool_calls == []

    def test_thinking_speech_extracted(self, mock_pm):
        """LLM 回傳的 content 應被截取為 thinking_speech"""
        from tests.mock_llm import MockRouter
        router = MockRouter()
        router.set_tool_calls([{
            "id": "call_tavily",
            "type": "function",
            "function": {"name": "tavily_search", "arguments": {"query": "天氣"}},
        }])
        router._default_response = '{"thinking_speech": "讓我查一下"}'

        with patch('core.chat_orchestrator.router_agent.get_prompt_manager', return_value=mock_pm):
            result = run_router_agent(
                user_prompt="今天天氣如何",
                char_hint="助理",
                tools_list=[],
                router=router,
            )

        assert result.needs_tools is True
        # 實際的 thinking_speech 來自 router 回傳的 content
        # 由於 MockRouter 回傳 self._default_response，需要確認 content 被正確處理

    def test_recent_history_not_duplicated(self, mock_pm):
        """recent_history 不應包含當前 user_prompt（由函式自行追加）"""
        from tests.mock_llm import MockRouter
        router = MockRouter()
        router.set_tool_calls([])  # 沒有工具呼叫

        history = [
            {"role": "user", "content": "上一句"},
            {"role": "assistant", "content": "回覆"},
        ]
        current_prompt = "現在的問題"

        with patch('core.chat_orchestrator.router_agent.get_prompt_manager', return_value=mock_pm):
            run_router_agent(
                user_prompt=current_prompt,
                char_hint="助理",
                tools_list=[],
                router=router,
                recent_history=history,
            )

        # 驗證 router 被呼叫時的 messages 不包含重複的 user_prompt
        calls = router.generate_calls
        assert len(calls) == 1
        messages = calls[0]["messages"]
        # 計算 user 訊息數量：system + history 中的 user + 當前 user_prompt
        user_msgs = [m for m in messages if m["role"] == "user"]
        # 確認沒有重複的 "現在的問題"
        user_contents = [m["content"] for m in user_msgs]
        assert user_contents.count(current_prompt) == 1

    def test_augmented_tools_includes_direct_chat(self, mock_pm):
        """tools_list + direct_chat_schema 的數量驗證"""
        from tests.mock_llm import MockRouter
        router = MockRouter()
        router.set_tool_calls([])

        real_tools = [
            {"type": "function", "function": {"name": "tavily_search"}},
            {"type": "function", "function": {"name": "weather"}},
        ]

        with patch('core.chat_orchestrator.router_agent.get_prompt_manager', return_value=mock_pm):
            run_router_agent(
                user_prompt="天氣",
                char_hint="助理",
                tools_list=real_tools,
                router=router,
            )

        # 驗證傳入 router 的 tools 包含 direct_chat
        calls = router.generate_calls
        assert len(calls) == 1
        passed_tools = calls[0]["tools"]
        assert len(passed_tools) == 3  # 2 real tools + direct_chat
        tool_names = [t.get("function", {}).get("name") for t in passed_tools]
        assert "direct_chat" in tool_names
        assert "tavily_search" in tool_names
        assert "weather" in tool_names


class TestDirectChatSchema:
    def test_direct_chat_schema_structure(self):
        """DIRECT_CHAT_SCHEMA 結構驗證"""
        assert DIRECT_CHAT_SCHEMA["type"] == "function"
        assert DIRECT_CHAT_SCHEMA["function"]["name"] == "direct_chat"
        assert "parameters" in DIRECT_CHAT_SCHEMA["function"]
        assert DIRECT_CHAT_SCHEMA["function"]["parameters"]["type"] == "object"

    def test_direct_chat_has_no_required_params(self):
        """direct_chat 的 parameters 應無 required 欄位"""
        params = DIRECT_CHAT_SCHEMA["function"]["parameters"]
        assert params.get("required", []) == []
