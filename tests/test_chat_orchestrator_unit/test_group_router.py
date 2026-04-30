"""Group Router 單元測試。"""
from core.chat_orchestrator.group_router import run_group_router


class _Router:
    def __init__(self, parsed=None):
        self.parsed = parsed or {}
        self.called = False

    def generate_json(self, *args, **kwargs):
        self.called = True
        self.args = args
        self.kwargs = kwargs
        return self.parsed


def _chars():
    return [
        {"character_id": "char-a", "name": "角色A", "system_prompt": "理性分析"},
        {"character_id": "char-b", "name": "角色B", "system_prompt": "感性補充"},
    ]


def test_explicit_mention_takes_priority_without_llm_call():
    router = _Router({"should_respond": False, "target_character_id": None, "reason": "stop"})

    result = run_group_router(
        [{"role": "user", "content": "@角色B 你怎麼看？"}],
        _chars(),
        router,
    )

    assert result.should_respond is True
    assert result.target_character_id == "char-b"
    assert router.called is False


def test_router_can_stop_group_reply():
    router = _Router({"should_respond": False, "target_character_id": None, "reason": "已充分回答"})

    result = run_group_router(
        [{"role": "assistant", "content": "已回答", "character_id": "char-a"}],
        _chars(),
        router,
        last_speaker_id="char-a",
        honor_mentions=False,
    )

    assert result.should_respond is False
    assert result.target_character_id is None


def test_repeated_speaker_is_replaced_with_alternative():
    router = _Router({"should_respond": True, "target_character_id": "char-a", "reason": "補充"})

    result = run_group_router(
        [{"role": "assistant", "content": "上一句", "character_id": "char-a"}],
        _chars(),
        router,
        last_speaker_id="char-a",
        honor_mentions=False,
    )

    assert result.should_respond is True
    assert result.target_character_id == "char-b"


def test_all_participants_spoke_is_soft_context_not_hard_stop():
    router = _Router({"should_respond": True, "target_character_id": "char-a", "reason": "修正另一位角色的誤解"})

    result = run_group_router(
        [
            {"role": "user", "content": "你們有辦法幫我嗎？"},
            {"role": "assistant", "content": "請貼錯誤內容。", "character_id": "char-a"},
            {"role": "assistant", "content": "我可以幫你整理。", "character_id": "char-b"},
        ],
        _chars(),
        router,
        last_speaker_id="char-b",
        honor_mentions=False,
    )

    assert result.should_respond is True
    assert result.target_character_id == "char-a"
    assert router.called is True
    prompt_messages = router.args[1]
    prompt_text = "\n".join(str(m.get("content", "")) for m in prompt_messages)
    assert "<all_participants_spoke_after_latest_user>true</all_participants_spoke_after_latest_user>" in prompt_text
    assert '<spoken_after_latest_user_json>["char-a", "char-b"]</spoken_after_latest_user_json>' in prompt_text


def test_more_turns_request_can_continue_after_all_participants_spoke():
    router = _Router({"should_respond": True, "target_character_id": "char-a", "reason": "使用者要求繼續"})

    result = run_group_router(
        [
            {"role": "user", "content": "請你們輪流多講幾輪"},
            {"role": "assistant", "content": "第一輪 A。", "character_id": "char-a"},
            {"role": "assistant", "content": "第一輪 B。", "character_id": "char-b"},
        ],
        _chars(),
        router,
        last_speaker_id="char-b",
        honor_mentions=False,
    )

    assert result.should_respond is True
    assert result.target_character_id == "char-a"
    assert router.called is True


def test_router_participant_summary_prefers_character_summary():
    router = _Router({"should_respond": False, "target_character_id": None, "reason": "stop"})
    chars = [
        {
            "character_id": "char-a",
            "name": "角色A",
            "character_summary": "短版簡介 A",
            "system_prompt": "很長的完整人設 A",
        },
        {
            "character_id": "char-b",
            "name": "角色B",
            "character_summary": "",
            "system_prompt": " fallback 人設 B ",
        },
    ]

    run_group_router(
        [{"role": "user", "content": "大家怎麼看？"}],
        chars,
        router,
        honor_mentions=False,
    )

    prompt_messages = router.args[1]
    prompt_text = "\n".join(str(m.get("content", "")) for m in prompt_messages)
    assert "短版簡介 A" in prompt_text
    assert "很長的完整人設 A" not in prompt_text
    assert "fallback 人設 B" in prompt_text
