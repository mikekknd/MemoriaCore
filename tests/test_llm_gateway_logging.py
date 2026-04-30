from core.llm_gateway import ILLMProvider, LLMRouter


class _FakeProvider(ILLMProvider):
    def __init__(self, first_response: str = "這不是 JSON"):
        self.calls = 0
        self.messages_per_call = []
        self.first_response = first_response

    def generate_chat(
        self,
        messages: list,
        model: str,
        temperature: float = 0.0,
        response_format: dict | None = None,
        tools: list | None = None,
        tool_choice: str | dict = "auto",
    ) -> tuple[str, list]:
        self.calls += 1
        self.messages_per_call.append(messages)
        if self.calls == 1:
            return self.first_response, []
        return '{"reply": "ok"}', []


def test_non_json_retry_regenerates_when_response_looks_like_document_dump(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        "core.llm_gateway.SystemLogger.log_llm_prompt",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "core.llm_gateway.SystemLogger.log_llm_response",
        lambda *args, **kwargs: None,
    )

    def _capture_error(context, message, details=None):
        captured["context"] = context
        captured["message"] = message
        captured["details"] = details

    monkeypatch.setattr("core.llm_gateway.SystemLogger.log_error", _capture_error)

    router = LLMRouter()
    bad_response = "# 一、介绍\n\n## 2.1 安装\n\n```shell\nsudo pip install docker-compose\n```"
    provider = _FakeProvider(bad_response)
    router.register_route("chat", provider, "fake-model")
    messages = [
        {"role": "system", "content": "請輸出 JSON"},
        {"role": "user", "content": "原始使用者訊息"},
    ]

    result = router.generate(
        "chat",
        messages,
        temperature=0.7,
        response_format={"type": "object"},
        log_context={"session_mode": "group", "group_name": "測試群組"},
    )

    assert result == '{"reply": "ok"}'
    assert captured["context"] == "LLMRouter/chat"
    assert "[system]\n請輸出 JSON" in captured["details"]["original_prompt"]
    assert "[user]\n原始使用者訊息" in captured["details"]["original_prompt"]
    assert captured["details"]["original_messages"] == messages
    assert captured["details"]["log_context"]["group_name"] == "測試群組"
    assert captured["details"]["response_preview"] == bad_response
    assert captured["details"]["retry_strategy"] == "regenerate"
    assert "系統警告" in captured["details"]["retry_warning"]
    assert "原封不動" not in captured["details"]["retry_warning"]
    assert provider.messages_per_call[1][0] == messages[0]
    assert provider.messages_per_call[1][1]["role"] == "user"
    assert bad_response not in provider.messages_per_call[1][1]["content"]
    assert "系統警告" in provider.messages_per_call[1][1]["content"]


def test_non_json_retry_preserves_plain_conversational_response(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        "core.llm_gateway.SystemLogger.log_llm_prompt",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "core.llm_gateway.SystemLogger.log_llm_response",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "core.llm_gateway.SystemLogger.log_error",
        lambda context, message, details=None: captured.update(details=details),
    )

    plain_response = "早安，今日也算是有些精神，汝倒是起得不晚。"
    provider = _FakeProvider(plain_response)
    router = LLMRouter()
    router.register_route("chat", provider, "fake-model")
    messages = [
        {"role": "system", "content": "請輸出 JSON"},
        {"role": "user", "content": "早安"},
    ]

    result = router.generate("chat", messages, response_format={"type": "object"})

    assert result == '{"reply": "ok"}'
    assert captured["details"]["retry_strategy"] == "preserve_previous"
    assert "原封不動" in captured["details"]["retry_warning"]
    assert provider.messages_per_call[1][-2] == {"role": "assistant", "content": plain_response}
    assert provider.messages_per_call[1][-1]["role"] == "user"
    assert "原封不動" in provider.messages_per_call[1][-1]["content"]


def test_non_json_retry_regenerates_when_group_response_copies_other_agent(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        "core.llm_gateway.SystemLogger.log_llm_prompt",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "core.llm_gateway.SystemLogger.log_llm_response",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "core.llm_gateway.SystemLogger.log_error",
        lambda context, message, details=None: captured.update(details=details),
    )

    leaked_response = (
        "[白蓮|char-lotus]: 哼...本座姑且指點一二。"
        "[可可|char-coco]: 太好了喵～主人好厲害！"
    )
    provider = _FakeProvider(leaked_response)
    router = LLMRouter()
    router.register_route("chat", provider, "fake-model")
    messages = [
        {"role": "system", "content": "請輸出 JSON"},
        {"role": "user", "content": "早安"},
    ]

    result = router.generate(
        "chat",
        messages,
        response_format={"type": "object"},
        log_context={
            "session_mode": "group",
            "current_character_id": "char-lotus",
            "current_character_name": "白蓮",
            "participants": [
                {"character_id": "char-lotus", "name": "白蓮"},
                {"character_id": "char-coco", "name": "可可"},
            ],
        },
    )

    assert result == '{"reply": "ok"}'
    assert captured["details"]["retry_strategy"] == "regenerate"
    assert captured["details"]["retry_reason"] == "group_speaker_leak"
    assert leaked_response not in provider.messages_per_call[1][-1]["content"]
