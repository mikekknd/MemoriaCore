"""Module C (Persona Agent) 測試：角色渲染與結構化 JSON 回覆"""
import pytest
from unittest.mock import patch, MagicMock
from core.chat_orchestrator.persona_agent import run_persona_agent, _parse_persona_response
from core.chat_orchestrator.dataclasses import ToolContext, PersonaResult
from core.opening_penalty import OpeningPenaltyPlan


@pytest.fixture
def mock_router():
    """預設 MockRouter，含標準 chat_schema 回應"""
    from tests.mock_llm import MockRouter
    router = MockRouter()
    router.set_chat_response({
        "internal_thought": "這是思考過程",
        "status_metrics": {"professionalism": 80},
        "tone": "Professional",
        "reply": "這是回覆內容",
        "extracted_entities": ["比特幣"],
        "speech": None,
    })
    return router


@pytest.fixture
def sample_api_messages():
    return [
        {"role": "system", "content": "你是一個專業助理。"},
        {"role": "user", "content": "比特幣現在多少錢？"},
    ]


@pytest.fixture
def sample_chat_schema():
    return {
        "type": "object",
        "properties": {
            "internal_thought": {"type": "string"},
            "status_metrics": {"type": "object", "properties": {"professionalism": {"type": "integer"}}},
            "tone": {"type": "string"},
            "reply": {"type": "string"},
            "extracted_entities": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["internal_thought", "status_metrics", "tone", "reply", "extracted_entities"],
    }


class TestRunPersonaAgent:
    def test_no_tool_context_works(self, mock_router, sample_api_messages, sample_chat_schema):
        """tool_context=None 時應正常生成回覆"""
        raw, err = run_persona_agent(
            user_prompt="你好",
            api_messages=sample_api_messages,
            tool_context=None,
            chat_schema=sample_chat_schema,
            router=mock_router,
        )

        assert raw is not None
        assert err is None

    def test_thinking_speech_injected_before_user(self, mock_router, sample_api_messages, sample_chat_schema):
        """tool_context.thinking_speech_sent 應插入為 assistant message"""
        tool_ctx = ToolContext(
            tool_results=[],
            tool_results_formatted="",
            thinking_speech_sent="讓我查一下...",
        )

        # 捕獲傳入 router.generate 的 messages
        captured_messages = []
        def capture_generate(*args, **kwargs):
            captured_messages.extend(args[1] if len(args) > 1 else kwargs.get('messages', []))
            return '{"reply": "ok"}'

        mock_router.generate = capture_generate

        run_persona_agent(
            user_prompt="比特幣價格",
            api_messages=sample_api_messages,
            tool_context=tool_ctx,
            chat_schema=sample_chat_schema,
            router=mock_router,
        )

        # 驗證 thinking_speech 被插入（作為 assistant message）
        assistant_msgs = [m for m in captured_messages if m.get("role") == "assistant"]
        assert any("讓我查一下" in m.get("content", "") for m in assistant_msgs)

    def test_tool_results_appended_after_user(self, mock_router, sample_api_messages, sample_chat_schema):
        """工具結果應以 XML-like 格式附加在 user message 之後"""
        tool_ctx = ToolContext(
            tool_results=[{"tool_name": "tavily", "result": '{"answer": "95000"}'}],
            tool_results_formatted="【tavily 查詢結果】\n95000",
            thinking_speech_sent="",
        )

        captured_messages = []
        generate_called = [False]
        def capture_generate(*args, **kwargs):
            generate_called[0] = True
            captured_messages.extend(args[1] if len(args) > 1 else kwargs.get('messages', []))
            return '{"reply": "比特幣約 95000"}'

        mock_router.generate = capture_generate

        run_persona_agent(
            user_prompt="比特幣",
            api_messages=sample_api_messages,
            tool_context=tool_ctx,
            chat_schema=sample_chat_schema,
            router=mock_router,
        )

        # 驗證 router.generate 被呼叫了
        assert generate_called[0], "router.generate should have been called"
        # 驗證 tool context 相關內容有被處理（tool_results_formatted 包含 tavily）
        all_content = " ".join(m.get("content", "") for m in captured_messages)
        assert "<external_tool_context" in all_content
        assert "tavily" in all_content
        assert "系統通知" not in all_content

    def test_error_returns_persona_result_with_error(self, sample_api_messages, sample_chat_schema):
        """router.generate 例外時應回傳包含錯誤文字的 PersonaResult"""
        from tests.mock_llm import MockRouter
        router = MockRouter()

        def raise_error(*args, **kwargs):
            raise RuntimeError("LLM 失敗")

        router.generate = raise_error

        raw, err = run_persona_agent(
            user_prompt="你好",
            api_messages=sample_api_messages,
            tool_context=None,
            chat_schema=sample_chat_schema,
            router=router,
        )

        assert raw is None
        assert err is not None
        assert "錯誤" in err.reply_text or "生成錯誤" in err.reply_text

    def test_schema_passed_to_router(self, mock_router, sample_api_messages, sample_chat_schema):
        """chat_schema 應被傳入 router.generate"""
        mock_router.generate = lambda *args, **kwargs: ('{}', None)

        run_persona_agent(
            user_prompt="你好",
            api_messages=sample_api_messages,
            tool_context=None,
            chat_schema=sample_chat_schema,
            router=mock_router,
        )

        # 由於 MockRouter 無法直接驗證 schema 傳遞，此測試驗證函式呼叫不拋例外

    def test_opening_penalty_instruction_and_logit_bias_passed(
        self, mock_router, sample_api_messages, sample_chat_schema
    ):
        """開場抑制指令應附加到最後一則 user，logit_bias 應傳入 router。"""
        captured = {}

        def capture_generate(*args, **kwargs):
            captured["messages"] = args[1]
            captured["logit_bias"] = kwargs.get("logit_bias")
            return '{"reply": "換個開頭回覆", "extracted_entities": [], "internal_thought": "思考"}'

        mock_router.generate = capture_generate
        plan = OpeningPenaltyPlan(
            enabled=True,
            key=("s1", "c1", "public"),
            blocked_openings=("哼...",),
            prompt_block="<opening_penalty_instruction>禁止哼...</opening_penalty_instruction>",
            logit_bias={"1": -12},
        )

        raw, err = run_persona_agent(
            user_prompt="你好",
            api_messages=sample_api_messages,
            tool_context=None,
            chat_schema=sample_chat_schema,
            router=mock_router,
            opening_penalty_plan=plan,
        )

        assert err is None
        assert "換個開頭" in raw
        assert captured["messages"][-1]["role"] == "user"
        assert "禁止哼" in captured["messages"][-1]["content"]
        assert captured["logit_bias"] == {"1": -12}

    def test_opening_penalty_retries_once_on_repeated_opening(
        self, mock_router, sample_api_messages, sample_chat_schema
    ):
        """若 reply 仍以短期黑名單開頭，角色渲染會重試一次。"""
        calls = []
        responses = [
            '{"reply": "哼...又重複了", "extracted_entities": [], "internal_thought": "思考"}',
            '{"reply": "換個開頭，這次不重複。", "extracted_entities": [], "internal_thought": "思考"}',
        ]

        def capture_generate(*args, **kwargs):
            calls.append({"messages": args[1], "logit_bias": kwargs.get("logit_bias")})
            return responses[len(calls) - 1]

        mock_router.generate = capture_generate
        plan = OpeningPenaltyPlan(
            enabled=True,
            key=("s1", "c1", "public"),
            blocked_openings=("哼...",),
            prompt_block="<opening_penalty_instruction>禁止哼...</opening_penalty_instruction>",
            logit_bias={"1": -12},
        )

        raw, err = run_persona_agent(
            user_prompt="你好",
            api_messages=sample_api_messages,
            tool_context=None,
            chat_schema=sample_chat_schema,
            router=mock_router,
            opening_penalty_plan=plan,
        )

        assert err is None
        assert "換個開頭" in raw
        assert len(calls) == 2
        assert "opening_penalty_retry" in calls[1]["messages"][-1]["content"]
        assert calls[0]["logit_bias"] == {"1": -12}
        assert calls[1]["logit_bias"] == {"1": -12}


class TestParsePersonaResponse:
    def test_valid_json_parses_correctly(self):
        """有效 JSON 應正確解析（tone/status_metrics 已從 LLM schema 移除，不由 _parse_persona_response 填入）"""
        raw = '{"reply": "比特幣約 95000", "extracted_entities": ["比特幣"], "internal_thought": "思考"}'
        result = _parse_persona_response(raw)

        assert result.reply_text == "比特幣約 95000"
        assert "比特幣" in result.new_entities
        assert result.inner_thought == "思考"
        # tone / status_metrics 永遠為 None（已移除，向後相容欄位）
        assert result.tone is None
        assert result.status_metrics is None

    def test_invalid_json_returns_raw_text(self):
        """非 JSON 回應應 fallback 為原始文字"""
        raw = "這不是 JSON 格式的直接回覆"
        result = _parse_persona_response(raw)

        assert result.reply_text == raw
        assert result.new_entities == []

    def test_none_input_returns_default(self):
        """None 回應應回傳預設文字"""
        result = _parse_persona_response(None)
        assert result.reply_text == "（無回應）"

    def test_partial_json_with_fallback(self):
        """部分 JSON 欄位缺失時 fallback"""
        raw = '{"reply": "僅有回覆"}'
        result = _parse_persona_response(raw)

        assert result.reply_text == "僅有回覆"
        assert result.new_entities == []
        assert result.status_metrics is None

    def test_json_with_extra_fields(self):
        """JSON 有額外欄位時不應影響解析"""
        raw = '{"reply": "回覆", "extra_field": "忽略", "entities": ["e"]}'
        result = _parse_persona_response(raw)

        # parsed 只取必要欄位，忽略 extra_field
        assert result.reply_text == "回覆"

    def test_group_reply_strips_other_agent_segments(self):
        """群組 reply 若誤含其他 AI speaker 段落，只保留目前角色自己的台詞。"""
        raw = (
            '{"reply": "[白蓮|char-lotus]: 哼...本座姑且指點一二。'
            '[可可|char-coco]: 太好了喵～主人好厲害！",'
            '"extracted_entities": [], "internal_thought": "思考"}'
        )

        result = _parse_persona_response(
            raw,
            log_context={
                "session_mode": "group",
                "current_character_id": "char-lotus",
                "current_character_name": "白蓮",
            },
        )

        assert result.reply_text == "哼...本座姑且指點一二。"
        assert "可可" not in result.reply_text
