"""Group Router 單元測試。"""
import json
import re

from core.chat_orchestrator.group_router import GROUP_ROUTER_SCHEMA, run_group_router


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
    router = _Router({
        "conversation_intent": "single_response",
        "action": "stop_no_new_value",
        "target_character_id": None,
        "reason": "stop",
    })

    result = run_group_router(
        [{"role": "user", "content": "@角色B 你怎麼看？"}],
        _chars(),
        router,
    )

    assert result.should_respond is True
    assert result.target_character_id == "char-b"
    assert result.action == "explicit_user_request"
    assert router.called is False


def test_router_can_stop_group_reply():
    router = _Router({
        "conversation_intent": "single_response",
        "action": "stop_no_new_value",
        "target_character_id": None,
        "reason": "已充分回答",
    })

    result = run_group_router(
        [{"role": "assistant", "content": "已回答", "character_id": "char-a"}],
        _chars(),
        router,
        last_speaker_id="char-a",
        honor_mentions=False,
    )

    assert result.should_respond is False
    assert result.target_character_id is None
    assert result.action == "stop_no_new_value"


def test_new_speaker_action_targeting_spoken_character_falls_back_to_unspoken():
    router = _Router({
        "conversation_intent": "group_discussion",
        "action": "new_speaker_add",
        "target_character_id": "char-a",
        "reason": "補充",
    })

    result = run_group_router(
        [
            {"role": "user", "content": "你們怎麼看？"},
            {"role": "assistant", "content": "上一句", "character_id": "char-a"},
        ],
        _chars(),
        router,
        last_speaker_id="char-a",
        honor_mentions=False,
    )

    assert result.should_respond is True
    assert result.target_character_id == "char-b"
    assert result.action == "new_speaker_add"


def test_stop_all_spoken_with_unspoken_character_falls_back_to_unspoken():
    router = _Router({
        "conversation_intent": "group_discussion",
        "action": "stop_all_spoken",
        "target_character_id": None,
        "reason": "所有人都已回應",
    })

    result = run_group_router(
        [
            {"role": "user", "content": "兩位晚安"},
            {"role": "assistant", "content": "晚安。", "character_id": "char-a"},
        ],
        _chars(),
        router,
        last_speaker_id="char-a",
        honor_mentions=False,
    )

    assert result.should_respond is True
    assert result.target_character_id == "char-b"
    assert result.action == "new_speaker_add"


def test_all_participants_spoke_is_soft_context_not_hard_stop():
    router = _Router({
        "conversation_intent": "continue_group_discussion",
        "action": "repeat_speaker_correction",
        "target_character_id": "char-a",
        "reason": "修正另一位角色的誤解",
    })

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
    assert result.action == "repeat_speaker_correction"
    assert router.called is True
    prompt_messages = router.args[1]
    prompt_text = "\n".join(str(m.get("content", "")) for m in prompt_messages)
    turn_state = _extract_turn_state(prompt_text)
    assert turn_state["all_participants_already_spoke_this_turn"] is True
    assert turn_state["remaining_bot_turns_including_next"] is None
    assert turn_state["last_speaker"] == {"character_id": "char-b", "name": "角色B"}
    assert turn_state["already_spoken_this_turn"] == [
        {"character_id": "char-a", "name": "角色A"},
        {"character_id": "char-b", "name": "角色B"},
    ]
    assert turn_state["not_yet_spoken_this_turn"] == []
    assert "participants_who_already_spoke_after_latest_user" not in turn_state
    assert "all_participants_already_spoke_after_latest_user" not in turn_state
    assert "user_explicitly_requested_multi_turn_discussion" not in turn_state
    assert "<spoken_after_latest_user_json>" not in prompt_text
    assert "<latest_user_requests_more_turns>" not in prompt_text
    assert "<mentioned_character_id>" not in prompt_text


def test_repeat_speaker_action_allows_a_b_a_flow():
    router = _Router({
        "conversation_intent": "continue_group_discussion",
        "action": "repeat_speaker_reply_to_ai",
        "target_character_id": "char-a",
        "reason": "回應角色B提問",
    })

    result = run_group_router(
        [
            {"role": "user", "content": "你們討論一下"},
            {"role": "assistant", "content": "A 的看法。", "character_id": "char-a"},
            {"role": "assistant", "content": "B 問 A 一個問題。", "character_id": "char-b"},
        ],
        _chars(),
        router,
        last_speaker_id="char-b",
        honor_mentions=False,
    )

    assert result.should_respond is True
    assert result.target_character_id == "char-a"
    assert result.action == "repeat_speaker_reply_to_ai"
    assert result.conversation_intent == "continue_group_discussion"


def test_more_turns_request_is_left_to_router_semantics():
    router = _Router({
        "conversation_intent": "continue_group_discussion",
        "action": "explicit_user_request",
        "target_character_id": "char-a",
        "reason": "使用者要求繼續",
    })

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
    assert result.action == "explicit_user_request"
    assert router.called is True
    prompt_messages = router.args[1]
    prompt_text = "\n".join(str(m.get("content", "")) for m in prompt_messages)
    turn_state = _extract_turn_state(prompt_text)
    assert turn_state["latest_user_text"] == "請你們輪流多講幾輪"
    assert turn_state["bot_turn_index"] == 0
    assert turn_state["max_bot_turns"] is None
    assert turn_state["remaining_bot_turns_including_next"] is None
    assert "user_explicitly_requested_multi_turn_discussion" not in turn_state
    assert "<available_intents>" in prompt_text
    assert "不要期待 turn_state_json 另外提供布林旗標" in prompt_text


def test_router_prompt_separates_original_request_from_recent_exchange():
    router = _Router({
        "conversation_intent": "continue_group_discussion",
        "action": "repeat_speaker_reply_to_ai",
        "target_character_id": "char-a",
        "reason": "最近交換中角色B直接詢問角色A",
    })

    run_group_router(
        [
            {"role": "user", "content": "用這個主題聊看看，我幫你們評分"},
            {"role": "assistant", "content": "A 第一輪。", "character_id": "char-a", "character_name": "角色A"},
            {"role": "assistant", "content": "B 第一輪。", "character_id": "char-b", "character_name": "角色B"},
            {"role": "assistant", "content": "A 第二輪。", "character_id": "char-a", "character_name": "角色A"},
            {"role": "assistant", "content": "B 第二輪，並問 A。", "character_id": "char-b", "character_name": "角色B"},
            {"role": "assistant", "content": "A 第三輪回應。", "character_id": "char-a", "character_name": "角色A"},
        ],
        _chars(),
        router,
        last_speaker_id="char-a",
        honor_mentions=False,
        bot_turn_index=5,
        max_bot_turns=10,
    )

    prompt_messages = router.args[1]
    prompt_text = "\n".join(str(m.get("content", "")) for m in prompt_messages)
    turn_state = _extract_turn_state(prompt_text)
    assert turn_state["original_user_request"] == "用這個主題聊看看，我幫你們評分"
    assert turn_state["latest_user_text"] == "用這個主題聊看看，我幫你們評分"
    assert [item["content"] for item in turn_state["recent_assistant_exchange_this_turn"]] == [
        "B 第一輪。",
        "A 第二輪。",
        "B 第二輪，並問 A。",
        "A 第三輪回應。",
    ]
    assert "主題、評分方式或開場任務" in prompt_text
    assert "只有原文包含" not in prompt_text


def test_continue_discussion_intent_with_stop_all_spoken_stops_when_valid():
    router = _Router({
        "conversation_intent": "continue_group_discussion",
        "action": "stop_all_spoken",
        "target_character_id": None,
        "reason": "已自然收尾",
    })

    result = run_group_router(
        [
            {"role": "user", "content": "再多討論一下嘛"},
            {"role": "assistant", "content": "A 的新選項。", "character_id": "char-a"},
            {"role": "assistant", "content": "B 的反駁。", "character_id": "char-b"},
        ],
        _chars(),
        router,
        last_speaker_id="char-b",
        honor_mentions=False,
        bot_turn_index=1,
        max_bot_turns=3,
    )

    assert result.should_respond is False
    assert result.target_character_id is None
    assert result.action == "stop_all_spoken"
    assert result.conversation_intent == "continue_group_discussion"


def test_router_participant_summary_prefers_character_summary():
    router = _Router({
        "conversation_intent": "single_response",
        "action": "stop_no_new_value",
        "target_character_id": None,
        "reason": "stop",
    })
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


def test_group_router_schema_uses_action_enum():
    assert "conversation_intent" in GROUP_ROUTER_SCHEMA["properties"]
    assert "continue_group_discussion" in GROUP_ROUTER_SCHEMA["properties"]["conversation_intent"]["enum"]
    assert "conversation_intent" in GROUP_ROUTER_SCHEMA["required"]
    assert "action" in GROUP_ROUTER_SCHEMA["properties"]
    assert "should_respond" not in GROUP_ROUTER_SCHEMA["properties"]
    assert GROUP_ROUTER_SCHEMA["additionalProperties"] is False
    assert "new_speaker_ack" in GROUP_ROUTER_SCHEMA["properties"]["action"]["enum"]


def _extract_turn_state(prompt_text: str) -> dict:
    match = re.search(r"<turn_state_json>\s*(.*?)\s*</turn_state_json>", prompt_text, re.S)
    assert match, prompt_text
    return json.loads(match.group(1))
