"""Group Router 單元測試。"""
from core.chat_orchestrator.group_router import run_group_router


class _Router:
    def __init__(self, parsed=None):
        self.parsed = parsed or {}
        self.called = False

    def generate_json(self, *args, **kwargs):
        self.called = True
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
