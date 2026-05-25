"""Group Router 單元測試。"""
import json
from pathlib import Path
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


def test_single_participant_can_repeat_when_enabled_without_llm_call():
    router = _Router({
        "conversation_intent": "single_response",
        "action": "stop_no_new_value",
        "target_character_id": None,
        "reason": "stop",
    })

    result = run_group_router(
        [
            {"role": "user", "content": "繼續剛剛的話題"},
            {"role": "assistant", "content": "上一輪回應", "character_id": "char-a"},
        ],
        [_chars()[0]],
        router,
        last_speaker_id="char-a",
        honor_mentions=False,
        allow_single_participant_repeat=True,
    )

    assert result.should_respond is True
    assert result.target_character_id == "char-a"
    assert result.action == "repeat_speaker_reply_to_ai"
    assert router.called is False


def test_single_participant_repeat_can_still_be_disabled():
    router = _Router({
        "conversation_intent": "single_response",
        "action": "new_speaker_ack",
        "target_character_id": "char-a",
        "reason": "continue",
    })

    result = run_group_router(
        [
            {"role": "user", "content": "繼續剛剛的話題"},
            {"role": "assistant", "content": "上一輪回應", "character_id": "char-a"},
        ],
        [_chars()[0]],
        router,
        last_speaker_id="char-a",
        honor_mentions=False,
        allow_single_participant_repeat=False,
    )

    assert result.should_respond is False
    assert result.target_character_id is None
    assert result.action == "stop_no_new_value"
    assert router.called is False


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


def test_human_mention_still_bypasses_without_director_intent():
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
        discussion_mode="youtube_live",
        current_turn_intent={"source": "viewer_chat", "action": "audience_response"},
    )

    assert result.should_respond is True
    assert result.target_character_id == "char-b"
    assert result.action == "explicit_user_request"
    assert router.called is False


def test_youtube_live_director_intent_sanitizes_original_request_and_disables_mention_bypass():
    router = _Router({
        "conversation_intent": "group_discussion",
        "action": "new_speaker_add",
        "target_character_id": "char-a",
        "reason": "回應觀眾留言",
    })
    director_prose = "請簡短回應剛剛的聊天室留言，接著讓角色彼此補充並自然拉回「四月新番」。@角色B"

    result = run_group_router(
        [{"role": "user", "content": director_prose}],
        _chars(),
        router,
        discussion_mode="youtube_live",
        current_turn_intent={
            "source": "youtube_live_director",
            "action": "audience_response",
            "event_count": 2,
            "source_session_id": "live-a",
            "current_topic": "四月新番",
        },
    )

    prompt_text = "\n".join(str(m.get("content", "")) for m in router.args[1])
    turn_state = _extract_turn_state(prompt_text)
    assert result.action == "new_speaker_add"
    assert router.called is True
    assert turn_state["original_user_request"] == "YouTube Live audience response turn"
    assert turn_state["turn_intent"] == {
        "source": "youtube_live_director",
        "action": "audience_response",
        "event_count": 2,
        "source_session_id": "live-a",
        "current_topic": "四月新番",
    }
    assert director_prose not in turn_state["original_user_request"]


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
    assert turn_state["original_user_request"] == "請你們輪流多講幾輪"
    assert "latest_user_text" not in turn_state
    assert turn_state["bot_turn_index"] == 0
    assert turn_state["max_bot_turns"] is None
    assert turn_state["remaining_bot_turns_including_next"] is None
    assert "user_explicitly_requested_multi_turn_discussion" not in turn_state
    assert "<decision_flow>" in prompt_text
    assert "<stop_gate>" in prompt_text
    assert "<speaker_selection>" in prompt_text
    assert "<routing_priority>" not in prompt_text
    assert "<available_intents>" not in prompt_text
    assert "一般群組延伸不算 explicit" in prompt_text


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
    previous_context = _extract_previous_context(prompt_text)
    assert turn_state["original_user_request"] == "用這個主題聊看看，我幫你們評分"
    assert "latest_user_text" not in turn_state
    assert [item["content"] for item in turn_state["recent_assistant_exchange_this_turn"]] == [
        "B 第一輪。",
        "A 第二輪。",
        "B 第二輪，並問 A。",
        "A 第三輪回應。",
    ]
    assert "A 第一輪" not in previous_context
    assert "B 第二輪" not in previous_context
    assert "bot_turn_index > 0 時不可視為新的使用者追問" in prompt_text
    assert "只有原文包含" not in prompt_text


def test_previous_context_excludes_persisted_current_user_message():
    router = _Router({
        "conversation_intent": "group_discussion",
        "action": "new_speaker_add",
        "target_character_id": "char-a",
        "reason": "第一位角色開始本輪",
    })
    messages = [
        {"role": "user", "content": "上一輪主題"},
        {"role": "assistant", "content": "上一輪 A。", "character_id": "char-a", "character_name": "角色A"},
        {"role": "user", "content": "這一輪新主題"},
    ]

    run_group_router(
        messages,
        _chars(),
        router,
        honor_mentions=False,
        current_turn_instruction="這一輪新主題",
        current_turn_start_index=len(messages),
    )

    prompt_text = "\n".join(str(m.get("content", "")) for m in router.args[1])
    previous_context = _extract_previous_context(prompt_text)
    turn_state = _extract_turn_state(prompt_text)
    assert turn_state["original_user_request"] == "這一輪新主題"
    assert "上一輪主題" in previous_context
    assert "上一輪 A" in previous_context
    assert "這一輪新主題" not in previous_context


def test_group_router_prompt_includes_external_turn_context_without_polluting_previous_context():
    router = _Router({
        "conversation_intent": "group_discussion",
        "action": "new_speaker_add",
        "target_character_id": "char-a",
        "reason": "世界事件需要角色回應",
    })
    messages = [
        {"role": "user", "content": "上一輪主題"},
        {"role": "assistant", "content": "上一輪 A。", "character_id": "char-a", "character_name": "角色A"},
    ]

    run_group_router(
        messages,
        _chars(),
        router,
        honor_mentions=False,
        current_turn_instruction="請根據已帶入的外部上下文回應。",
        current_turn_start_index=len(messages),
        router_turn_context={
            "source": "personacore_world_event",
            "trigger_kind": "world_event",
            "summary": "廚房水燒開了",
            "instruction": "請自然用角色台詞延續這個已發生的事件。",
            "persistence": "hidden",
            "context_excerpt": (
                "[PersonaCore world event]\n"
                "Event summary: 廚房水燒開了\n"
                "Event instruction: 請自然用角色台詞延續這個已發生的事件。"
            ),
        },
    )

    prompt_text = "\n".join(str(m.get("content", "")) for m in router.args[1])
    turn_state = _extract_turn_state(prompt_text)
    previous_context = _extract_previous_context(prompt_text)
    external_turn_context = _extract_external_turn_context(prompt_text)

    assert turn_state["original_user_request"] == (
        "外部事件觸發（personacore_world_event）：廚房水燒開了。"
        "請自然用角色台詞延續這個已發生的事件。"
    )
    assert external_turn_context["source"] == "personacore_world_event"
    assert external_turn_context["summary"] == "廚房水燒開了"
    assert external_turn_context["persistence"] == "hidden"
    assert "上一輪主題" in previous_context
    assert "上一輪 A" in previous_context
    assert "廚房水燒開了" not in previous_context


def test_group_router_prompt_renders_null_external_turn_context_when_absent():
    router = _Router({
        "conversation_intent": "group_discussion",
        "action": "new_speaker_add",
        "target_character_id": "char-a",
        "reason": "一般使用者輸入",
    })

    run_group_router(
        [{"role": "user", "content": "一般問題"}],
        _chars(),
        router,
        honor_mentions=False,
        current_turn_instruction="一般問題",
        current_turn_start_index=1,
    )

    prompt_text = "\n".join(str(m.get("content", "")) for m in router.args[1])
    assert _extract_external_turn_context(prompt_text) is None
    assert _extract_turn_state(prompt_text)["original_user_request"] == "一般問題"


def test_group_router_prompt_compacts_external_turn_context():
    router = _Router({
        "conversation_intent": "group_discussion",
        "action": "new_speaker_add",
        "target_character_id": "char-a",
        "reason": "外部事件需要回應",
    })
    long_summary = "水" * 1300

    run_group_router(
        [{"role": "user", "content": "請根據已帶入的外部上下文回應。"}],
        _chars(),
        router,
        honor_mentions=False,
        current_turn_instruction="請根據已帶入的外部上下文回應。",
        current_turn_start_index=1,
        router_turn_context={
            "source": "personacore_world_event",
            "summary": long_summary,
            "unexpected": "不應進入 prompt",
        },
    )

    prompt_text = "\n".join(str(m.get("content", "")) for m in router.args[1])
    external_turn_context = _extract_external_turn_context(prompt_text)
    assert external_turn_context["summary"] == "水" * 1200
    assert "unexpected" not in external_turn_context
    assert "不應進入 prompt" not in prompt_text


def test_group_router_keeps_youtube_live_director_turn_label_when_external_turn_context_present():
    router = _Router({
        "conversation_intent": "group_discussion",
        "action": "new_speaker_add",
        "target_character_id": "char-a",
        "reason": "直播導播回合",
    })

    run_group_router(
        [{"role": "user", "content": "直播導播控制。"}],
        _chars(),
        router,
        honor_mentions=False,
        discussion_mode="youtube_live",
        current_turn_instruction="直播導播控制。",
        current_turn_intent={
            "source": "youtube_live_director",
            "action": "audience_response",
        },
        current_turn_start_index=1,
        router_turn_context={
            "source": "youtube_live_director",
            "trigger_kind": "live_director",
            "summary": "聊天室集中問候主持人",
            "instruction": "回應本批觀眾留言",
            "persistence": "default_visible_event",
        },
    )

    prompt_text = "\n".join(str(m.get("content", "")) for m in router.args[1])
    turn_state = _extract_turn_state(prompt_text)
    external_turn_context = _extract_external_turn_context(prompt_text)

    assert turn_state["original_user_request"] == "YouTube Live audience response turn"
    assert turn_state["turn_intent"]["source"] == "youtube_live_director"
    assert turn_state["turn_intent"]["action"] == "audience_response"
    assert external_turn_context["source"] == "youtube_live_director"
    assert external_turn_context["summary"] == "聊天室集中問候主持人"


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


def test_youtube_live_discussion_mode_discourages_early_stop_after_all_spoke():
    router = _Router({
        "conversation_intent": "continue_group_discussion",
        "action": "stop_all_spoken",
        "target_character_id": None,
        "reason": "兩位角色都已回應",
    })

    result = run_group_router(
        [
            {"role": "user", "content": "請自然延續直播。"},
            {"role": "assistant", "content": "A 先聊第 4 話演出。", "character_id": "char-a", "character_name": "角色A"},
            {"role": "assistant", "content": "B 補充社群正在討論作畫落差。", "character_id": "char-b", "character_name": "角色B"},
        ],
        _chars(),
        router,
        last_speaker_id="char-b",
        honor_mentions=False,
        bot_turn_index=2,
        max_bot_turns=6,
        discussion_mode="youtube_live",
    )

    assert result.should_respond is True
    assert result.target_character_id == "char-a"
    assert result.action == "repeat_speaker_reply_to_ai"
    assert result.conversation_intent == "continue_group_discussion"
    prompt_messages = router.args[1]
    prompt_text = "\n".join(str(m.get("content", "")) for m in prompt_messages)
    turn_state = _extract_turn_state(prompt_text)
    assert "discussion_mode" not in turn_state
    assert "live_episode_plan" not in turn_state
    assert "<youtube_live_rules>" in prompt_text
    assert "<youtube_live_group_router_rules>" not in prompt_text
    assert "避免同角色連續發言" in prompt_text
    assert "remaining_bot_turns_including_next 是硬上限" in prompt_text


def test_youtube_live_discussion_mode_honors_stop_no_new_value_outside_group_closing():
    router = _Router({
        "conversation_intent": "single_response",
        "action": "stop_no_new_value",
        "target_character_id": None,
        "reason": "這段已經自然收束，下一句只會重述",
    })

    result = run_group_router(
        [
            {"role": "user", "content": "這段已經夠了，不用再補。"},
            {"role": "assistant", "content": "A 已經完成短收束。", "character_id": "char-a", "character_name": "角色A"},
        ],
        _chars(),
        router,
        last_speaker_id="char-a",
        honor_mentions=False,
        bot_turn_index=1,
        max_bot_turns=2,
        discussion_mode="youtube_live",
    )

    assert result.should_respond is False
    assert result.target_character_id is None
    assert result.action == "stop_no_new_value"


def test_youtube_live_group_closing_prompt_marks_group_closing():
    router = _Router({
        "conversation_intent": "continue_group_discussion",
        "action": "new_speaker_reply_to_ai",
        "target_character_id": "char-b",
        "reason": "群組收尾仍有未發言角色",
    })

    run_group_router(
        [
            {"role": "user", "content": "請做本場最後收尾，正式道別，不要開新話題。"},
            {"role": "assistant", "content": "A 已回顧重點並正式道別。", "character_id": "char-a", "character_name": "角色A"},
        ],
        _chars(),
        router,
        last_speaker_id="char-a",
        honor_mentions=False,
        bot_turn_index=1,
        max_bot_turns=2,
        discussion_mode="youtube_live",
    )

    prompt_text = "\n".join(str(m.get("content", "")) for m in router.args[1])
    turn_state = _extract_turn_state(prompt_text)
    assert turn_state["closing_mode"] == "group_closing"
    assert "group_closing" in prompt_text
    assert "仍有未發言角色尚未完成簡短道別" in prompt_text


def test_youtube_live_non_closing_router_prompt_omits_closing_only_rules():
    router = _Router({
        "conversation_intent": "single_response",
        "action": "stop_no_new_value",
        "target_character_id": None,
        "reason": "無新增價值",
    })

    run_group_router(
        [{"role": "user", "content": "這段榜單分析先到這裡。"}],
        _chars(),
        router,
        honor_mentions=False,
        bot_turn_index=0,
        max_bot_turns=2,
        discussion_mode="youtube_live",
    )

    prompt_text = "\n".join(str(m.get("content", "")) for m in router.args[1])
    assert "closing_mode 表示收尾路由目標" not in prompt_text
    assert "每位可用角色最多一次簡短道別" not in prompt_text
    assert "youtube_live_closing_rules" not in prompt_text


def test_youtube_live_closing_router_prompt_appends_closing_only_rules():
    router = _Router({
        "conversation_intent": "continue_group_discussion",
        "action": "new_speaker_reply_to_ai",
        "target_character_id": "char-b",
        "reason": "群組收尾仍有未發言角色",
    })

    run_group_router(
        [
            {"role": "user", "content": "請做本場最後收尾，正式道別，不要開新話題。"},
            {"role": "assistant", "content": "A 已道別。", "character_id": "char-a", "character_name": "角色A"},
        ],
        _chars(),
        router,
        last_speaker_id="char-a",
        honor_mentions=False,
        bot_turn_index=1,
        max_bot_turns=2,
        discussion_mode="youtube_live",
    )

    prompt_text = "\n".join(str(m.get("content", "")) for m in router.args[1])
    assert "youtube_live_closing_rules" in prompt_text
    assert "closing_mode=group_closing" in prompt_text
    assert "仍有未發言角色尚未完成簡短道別" in prompt_text


def test_youtube_live_group_closing_allows_unspoken_speaker_after_stop_no_new_value():
    router = _Router({
        "conversation_intent": "single_response",
        "action": "stop_no_new_value",
        "target_character_id": None,
        "reason": "最近一則 AI 已完成回顧與道別",
    })

    result = run_group_router(
        [
            {"role": "user", "content": "請做本場最後收尾，正式道別，不要開新話題。"},
            {"role": "assistant", "content": "A 已回顧重點並正式道別。", "character_id": "char-a", "character_name": "角色A"},
        ],
        _chars(),
        router,
        last_speaker_id="char-a",
        honor_mentions=False,
        bot_turn_index=1,
        max_bot_turns=2,
        discussion_mode="youtube_live",
    )

    assert result.should_respond is True
    assert result.target_character_id == "char-b"
    assert result.action == "new_speaker_reply_to_ai"
    assert result.conversation_intent == "continue_group_discussion"


def test_youtube_live_final_closing_hint_enables_group_closing_without_phrase_match():
    router = _Router({
        "conversation_intent": "single_response",
        "action": "stop_no_new_value",
        "target_character_id": None,
        "reason": "白蓮已完成感謝，可可尚未發言但已無新增資訊需求",
    })

    result = run_group_router(
        [
            {"role": "user", "content": "直播即將收尾，請感謝本場 Super Chat 支持。"},
            {
                "role": "assistant",
                "content": "感謝本場 Super Chat。小貓，妳也該收收心，準備向大家道別了。",
                "character_id": "char-a",
                "character_name": "角色A",
            },
        ],
        _chars(),
        router,
        last_speaker_id="char-a",
        honor_mentions=False,
        bot_turn_index=1,
        max_bot_turns=2,
        discussion_mode="youtube_live",
        final_closing_hint=True,
    )

    prompt_text = "\n".join(str(m.get("content", "")) for m in router.args[1])
    turn_state = _extract_turn_state(prompt_text)
    assert turn_state["closing_mode"] == "group_closing"
    assert result.should_respond is True
    assert result.target_character_id == "char-b"
    assert result.action == "new_speaker_reply_to_ai"
    assert result.conversation_intent == "continue_group_discussion"


def test_youtube_live_group_closing_logs_post_policy_route_adjustment(monkeypatch):
    events = []

    def capture_event(category, message, details=None):
        events.append({"category": category, "message": message, "details": details or {}})

    monkeypatch.setattr(
        "core.chat_orchestrator.group_router.SystemLogger.log_system_event",
        capture_event,
    )
    router = _Router({
        "conversation_intent": "single_response",
        "action": "stop_no_new_value",
        "target_character_id": None,
        "reason": "最近一則 AI 已完成回顧與道別",
    })

    result = run_group_router(
        [
            {"role": "user", "content": "請做本場最後收尾，正式道別，不要開新話題。"},
            {"role": "assistant", "content": "A 已回顧重點並正式道別。", "character_id": "char-a", "character_name": "角色A"},
        ],
        _chars(),
        router,
        last_speaker_id="char-a",
        honor_mentions=False,
        bot_turn_index=1,
        max_bot_turns=2,
        discussion_mode="youtube_live",
    )

    assert result.should_respond is True
    assert result.target_character_id == "char-b"
    assert events == [
        {
            "category": "group_router_post_policy",
            "message": "route adjusted by youtube_live_group_closing",
            "details": {
                "policy": "youtube_live_group_closing",
                "closing_mode": "group_closing",
                "raw_action": "stop_no_new_value",
                "raw_target_character_id": None,
                "final_action": "new_speaker_reply_to_ai",
                "final_target_character_id": "char-b",
                "final_conversation_intent": "continue_group_discussion",
            },
        }
    ]


def test_youtube_live_budget_exhausted_keeps_stop_without_post_policy_event(monkeypatch):
    events = []

    def capture_event(category, message, details=None):
        events.append({"category": category, "message": message, "details": details or {}})

    monkeypatch.setattr(
        "core.chat_orchestrator.group_router.SystemLogger.log_system_event",
        capture_event,
    )
    router = _Router({
        "conversation_intent": "single_response",
        "action": "stop_no_new_value",
        "target_character_id": None,
        "reason": "planned turn has no remaining reply budget",
    })

    result = run_group_router(
        [
            {"role": "user", "content": "請照計畫進行下一段。"},
            {"role": "assistant", "content": "A 已完成本段任務。", "character_id": "char-a", "character_name": "角色A"},
        ],
        _chars(),
        router,
        last_speaker_id="char-a",
        honor_mentions=False,
        bot_turn_index=1,
        max_bot_turns=1,
        discussion_mode="youtube_live",
        live_episode_plan={
            "mode": "planned_turn",
            "turn_id": "seg_01_turn_01",
            "turn_contract": {
                "turn_id": "seg_01_turn_01",
                "turn_type": "analysis",
                "intent": "完成本段分析",
            },
        },
    )

    assert result.should_respond is False
    assert result.action == "stop_no_new_value"
    assert events == []


def test_youtube_live_router_reassigns_candidate_matching_previous_speaker():
    router = _Router({
        "conversation_intent": "continue_group_discussion",
        "action": "repeat_speaker_reply_to_ai",
        "target_character_id": "char-a",
        "reason": "錯誤地讓上一位角色繼續說",
    })

    result = run_group_router(
        [
            {"role": "user", "content": "請自然延續直播。"},
            {"role": "assistant", "content": "A 已經提出主觀點。", "character_id": "char-a", "character_name": "角色A"},
        ],
        _chars(),
        router,
        last_speaker_id="char-a",
        honor_mentions=False,
        bot_turn_index=1,
        max_bot_turns=2,
        discussion_mode="youtube_live",
        live_episode_plan={
            "mode": "planned_turn",
            "turn_id": "seg_01_turn_02",
            "turn_contract": {
                "turn_id": "seg_01_turn_02",
                "turn_type": "analysis",
                "intent": "分析作品討論點",
            },
            "dialogue_policy": {
                "min_replies": 1,
                "max_replies": 2,
                "autonomy": "guided",
            },
        },
    )

    assert result.should_respond is True
    assert result.target_character_id == "char-b"
    assert result.action == "new_speaker_reply_to_ai"
    assert "previous speaker" in result.reason


def test_youtube_live_router_stops_when_planned_turn_has_no_unique_speaker_left():
    router = _Router({
        "conversation_intent": "continue_group_discussion",
        "action": "stop_all_spoken",
        "target_character_id": None,
        "reason": "兩位角色都已完成本段任務",
    })

    result = run_group_router(
        [
            {"role": "user", "content": "請自然延續直播。"},
            {"role": "assistant", "content": "A 主觀點。", "character_id": "char-a", "character_name": "角色A"},
            {"role": "assistant", "content": "B 轉譯並推進。", "character_id": "char-b", "character_name": "角色B"},
        ],
        _chars(),
        router,
        last_speaker_id="char-b",
        honor_mentions=False,
        bot_turn_index=2,
        max_bot_turns=3,
        discussion_mode="youtube_live",
        live_episode_plan={
            "mode": "planned_turn",
            "turn_id": "seg_01_turn_02",
            "turn_contract": {
                "turn_id": "seg_01_turn_02",
                "turn_type": "analysis",
                "intent": "分析作品討論點",
            },
            "dialogue_policy": {
                "min_replies": 1,
                "max_replies": 3,
                "autonomy": "guided",
            },
        },
    )

    assert result.should_respond is False
    assert result.target_character_id is None
    assert result.action == "stop_all_spoken"


def test_youtube_live_final_closing_allows_previous_speaker_when_router_requests_it():
    router = _Router({
        "conversation_intent": "continue_group_discussion",
        "action": "repeat_speaker_reply_to_ai",
        "target_character_id": "char-a",
        "reason": "final closing needs the same speaker",
    })

    result = run_group_router(
        [
            {"role": "user", "content": "請自然延續直播。"},
            {"role": "assistant", "content": "A 收尾上半句。", "character_id": "char-a", "character_name": "角色A"},
        ],
        _chars(),
        router,
        last_speaker_id="char-a",
        honor_mentions=False,
        bot_turn_index=1,
        max_bot_turns=2,
        discussion_mode="youtube_live",
        live_episode_plan={
            "mode": "planned_turn",
            "turn_id": "seg_99_turn_01",
            "turn_contract": {
                "turn_id": "seg_99_turn_01",
                "turn_type": "final_closing",
                "intent": "雙主持正式收尾",
            },
            "dialogue_policy": {
                "min_replies": 2,
                "max_replies": 2,
                "autonomy": "guided",
            },
        },
    )

    assert result.should_respond is True
    assert result.target_character_id == "char-a"
    assert result.action == "repeat_speaker_reply_to_ai"


def test_youtube_live_final_closing_stops_after_all_speakers_completed():
    router = _Router({
        "conversation_intent": "continue_group_discussion",
        "action": "repeat_speaker_reply_to_ai",
        "target_character_id": "char-a",
        "reason": "final closing continues",
    })

    result = run_group_router(
        [
            {"role": "user", "content": "請正式收尾，不要開新話題。"},
            {"role": "assistant", "content": "A 回顧今天重點。", "character_id": "char-a", "character_name": "角色A"},
            {"role": "assistant", "content": "B 道別並結束本場。", "character_id": "char-b", "character_name": "角色B"},
        ],
        _chars(),
        router,
        last_speaker_id="char-b",
        honor_mentions=False,
        bot_turn_index=2,
        max_bot_turns=4,
        discussion_mode="youtube_live",
        live_episode_plan={
            "mode": "planned_turn",
            "turn_id": "seg_99_turn_01",
            "turn_contract": {
                "turn_id": "seg_99_turn_01",
                "turn_type": "final_closing",
                "intent": "雙主持正式收尾",
            },
            "dialogue_policy": {
                "min_replies": 2,
                "max_replies": 2,
                "autonomy": "guided",
            },
        },
    )

    assert result.should_respond is False
    assert result.target_character_id is None
    assert result.action == "stop_all_spoken"
    assert "final closing" in result.reason


def test_youtube_live_router_prompt_includes_hosting_rules():
    router = _Router({
        "conversation_intent": "continue_group_discussion",
        "action": "repeat_speaker_reply_to_ai",
        "target_character_id": "char-a",
        "reason": "接續主持節奏",
    })

    run_group_router(
        [
            {"role": "user", "content": "請自然延續直播。"},
            {"role": "assistant", "content": "A 先聊事件 Hook。", "character_id": "char-a", "character_name": "角色A"},
            {"role": "assistant", "content": "B 補充觀眾驚訝點。", "character_id": "char-b", "character_name": "角色B"},
        ],
        _chars(),
        router,
        last_speaker_id="char-b",
        honor_mentions=False,
        bot_turn_index=2,
        max_bot_turns=6,
        discussion_mode="youtube_live",
        live_hosting={
            "host_interaction_rules": "可可提出觀眾視角；白蓮負責分析收束。",
            "program_segment_turns": 3,
            "segment_state": {
                "topic": "魔法帽的工作室",
                "current_step": {"step_id": "step_02", "name": "核心分析", "description": "拆解作品與市場因素。"},
                "completed_steps": [{"step_id": "step_01", "name": "事件 Hook"}],
                "remaining_steps": [{"step_id": "step_03", "name": "反方觀點"}],
                "turns_in_step": 1,
            },
        },
    )

    prompt_messages = router.args[1]
    prompt_text = "\n".join(str(m.get("content", "")) for m in prompt_messages)
    assert "<youtube_live_hosting_router_rules>" in prompt_text
    assert "可可提出觀眾視角" in prompt_text
    assert "目前討論主題：魔法帽的工作室" in prompt_text
    assert "目前節目步驟：核心分析" in prompt_text
    assert "節目段落流程" not in prompt_text


def test_youtube_live_router_prompt_omits_live_episode_plan_details_but_keeps_candidate_restriction():
    router = _Router({
        "conversation_intent": "group_discussion",
        "action": "new_speaker_add",
        "target_character_id": "char-b",
        "reason": "符合 planned turn",
    })

    result = run_group_router(
        [{"role": "user", "content": "請自然延續直播。"}],
        _chars() + [{"character_id": "char-c", "name": "角色C", "system_prompt": "吐槽補充"}],
        router,
        honor_mentions=False,
        discussion_mode="youtube_live",
        live_episode_plan={
            "plan_id": "plan-general-panel",
            "mode": "planned_turn",
            "turn_id": "seg_01_turn_01",
            "turn_contract": {
                "turn_id": "seg_01_turn_01",
                "turn_type": "hook",
                "intent": "用具體事件開場",
                "speaker_policy": {
                    "selection_mode": "router_select",
                    "allowed_character_ids": ["char-a", "char-b"],
                    "preferred_role_functions": ["host"],
                    "avoid_repeat_speaker": True,
                },
            },
            "speaker_policy": {
                "selection_mode": "router_select",
                "allowed_character_ids": ["char-a", "char-b"],
                "preferred_role_functions": ["host"],
                "avoid_repeat_speaker": True,
            },
        },
    )

    assert result.target_character_id == "char-b"
    prompt_messages = router.args[1]
    prompt_text = "\n".join(str(m.get("content", "")) for m in prompt_messages)
    turn_state = _extract_turn_state(prompt_text)
    assert "live_episode_plan" not in turn_state
    assert "discussion_mode" not in turn_state
    assert "char-c" not in json.dumps(turn_state["not_yet_spoken_this_turn"], ensure_ascii=False)
    assert "plan-general-panel" not in prompt_text
    assert "seg_01_turn_01" not in prompt_text
    assert "allowed_character_ids" not in prompt_text
    assert "用具體事件開場" not in prompt_text


def test_youtube_live_fixed_speaker_policy_routes_to_only_allowed_character_without_llm_call():
    router = _Router({
        "conversation_intent": "group_discussion",
        "action": "new_speaker_add",
        "target_character_id": "char-a",
        "reason": "wrong",
    })

    result = run_group_router(
        [{"role": "user", "content": "請自然延續直播。"}],
        _chars(),
        router,
        honor_mentions=False,
        discussion_mode="youtube_live",
        live_episode_plan={
            "mode": "planned_turn",
            "speaker_policy": {
                "selection_mode": "fixed",
                "allowed_character_ids": ["char-b"],
            },
        },
    )

    assert result.should_respond is True
    assert result.target_character_id == "char-b"
    assert result.action == "new_speaker_ack"
    assert router.called is False


def test_youtube_live_fixed_speaker_policy_anchors_first_reply_then_allows_handoff():
    first_router = _Router({
        "conversation_intent": "group_discussion",
        "action": "new_speaker_add",
        "target_character_id": "char-a",
        "reason": "wrong first speaker",
    })
    plan = {
        "mode": "planned_turn",
        "speaker_policy": {
            "selection_mode": "fixed",
            "allowed_character_ids": ["char-b"],
        },
        "dialogue_policy": {
            "min_replies": 2,
            "max_replies": 3,
            "autonomy": "guided",
        },
    }

    first_result = run_group_router(
        [{"role": "user", "content": "請自然延續直播。"}],
        _chars(),
        first_router,
        honor_mentions=False,
        discussion_mode="youtube_live",
        bot_turn_index=0,
        max_bot_turns=3,
        live_episode_plan=plan,
    )

    assert first_result.target_character_id == "char-b"
    assert first_result.action == "new_speaker_ack"
    assert first_router.called is False

    followup_router = _Router({
        "conversation_intent": "group_discussion",
        "action": "new_speaker_reply_to_ai",
        "target_character_id": "char-a",
        "reason": "另一位角色承接第一棒",
    })
    followup_result = run_group_router(
        [
            {"role": "user", "content": "請自然延續直播。"},
            {"role": "assistant", "content": "第一棒內容。", "character_id": "char-b"},
        ],
        _chars(),
        followup_router,
        last_speaker_id="char-b",
        honor_mentions=False,
        discussion_mode="youtube_live",
        bot_turn_index=1,
        max_bot_turns=3,
        live_episode_plan=plan,
    )

    assert followup_result.should_respond is True
    assert followup_result.target_character_id == "char-a"
    assert followup_result.action == "new_speaker_reply_to_ai"
    assert followup_router.called is True
    prompt_text = "\n".join(str(m.get("content", "")) for m in followup_router.args[1])
    turn_state = _extract_turn_state(prompt_text)
    assert turn_state["not_yet_spoken_this_turn"] == [{"character_id": "char-a", "name": "角色A"}]
    assert "live_episode_plan" not in turn_state
    assert "discussion_mode" not in turn_state
    assert "first_reply_already_completed" not in prompt_text
    assert "allowed_character_ids" not in prompt_text


def test_youtube_live_fixed_speaker_policy_does_not_force_previous_speaker_on_content_turn():
    router = _Router({
        "conversation_intent": "group_discussion",
        "action": "new_speaker_add",
        "target_character_id": "char-b",
        "reason": "fixed speaker would repeat previous speaker",
    })

    result = run_group_router(
        [{"role": "user", "content": "請自然延續直播。"}],
        _chars(),
        router,
        last_speaker_id="char-b",
        honor_mentions=False,
        discussion_mode="youtube_live",
        bot_turn_index=0,
        max_bot_turns=2,
        live_episode_plan={
            "mode": "planned_turn",
            "turn_id": "seg_02_turn_01",
            "turn_contract": {
                "turn_id": "seg_02_turn_01",
                "turn_type": "analysis",
                "intent": "分析下一個話題",
            },
            "speaker_policy": {
                "selection_mode": "fixed",
                "allowed_character_ids": ["char-b"],
            },
            "dialogue_policy": {
                "min_replies": 1,
                "max_replies": 2,
                "autonomy": "guided",
            },
        },
    )

    assert router.called is True
    assert result.should_respond is True
    assert result.target_character_id == "char-a"
    assert result.action == "new_speaker_reply_to_ai"


def test_youtube_live_router_does_not_treat_participant_ids_as_character_ids():
    router = _Router({
        "conversation_intent": "group_discussion",
        "action": "new_speaker_add",
        "target_character_id": "char-b",
        "reason": "router can still choose real character",
    })

    result = run_group_router(
        [{"role": "user", "content": "請自然延續直播。"}],
        _chars(),
        router,
        honor_mentions=False,
        discussion_mode="youtube_live",
        live_episode_plan={
            "mode": "planned_turn",
            "speaker_policy": {
                "selection_mode": "fixed",
                "allowed_participant_ids": ["koko"],
            },
        },
    )

    assert result.target_character_id == "char-b"
    assert router.called is True
    prompt_text = "\n".join(str(m.get("content", "")) for m in router.args[1])
    turn_state = _extract_turn_state(prompt_text)
    assert "live_episode_plan" not in turn_state
    assert "allowed_participant_ids" not in prompt_text
    assert "allowed_character_ids" not in prompt_text


def test_default_discussion_mode_keeps_normal_stop_policy():
    router = _Router({
        "conversation_intent": "continue_group_discussion",
        "action": "stop_all_spoken",
        "target_character_id": None,
        "reason": "已自然收尾",
    })

    result = run_group_router(
        [
            {"role": "user", "content": "你們討論一下"},
            {"role": "assistant", "content": "A 的觀點。", "character_id": "char-a"},
            {"role": "assistant", "content": "B 的補充。", "character_id": "char-b"},
        ],
        _chars(),
        router,
        last_speaker_id="char-b",
        honor_mentions=False,
        bot_turn_index=2,
        max_bot_turns=6,
    )

    assert result.should_respond is False
    assert result.target_character_id is None
    assert result.action == "stop_all_spoken"


def test_router_participant_profile_prefers_character_summary():
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
    participants = _extract_participants(prompt_text)
    assert "summary" not in participants[0]
    assert "routing_profile" in participants[0]
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


def test_group_router_prompt_contract_mentions_turn_intent():
    source = Path("prompts_default.json").read_text(encoding="utf-8")
    assert "turn_intent" in source
    assert "audience_response" in source
    assert "super_chat_response" in source
    assert "external_turn_context_json" in source
    assert "本輪外部觸發事件" in source


def _extract_turn_state(prompt_text: str) -> dict:
    match = re.search(r"<turn_state_json>\s*(.*?)\s*</turn_state_json>", prompt_text, re.S)
    assert match, prompt_text
    return json.loads(match.group(1))


def _extract_participants(prompt_text: str) -> list[dict]:
    match = re.search(r"<participants_json>\s*(.*?)\s*</participants_json>", prompt_text, re.S)
    assert match, prompt_text
    return json.loads(match.group(1))


def _extract_previous_context(prompt_text: str) -> str:
    match = re.search(r"<previous_context>\s*(.*?)\s*</previous_context>", prompt_text, re.S)
    assert match, prompt_text
    return match.group(1)


def _extract_external_turn_context(prompt_text: str) -> dict | None:
    match = re.search(r"<external_turn_context_json>\s*(.*?)\s*</external_turn_context_json>", prompt_text, re.S)
    assert match, prompt_text
    raw = match.group(1).strip()
    if raw == "null":
        return None
    return json.loads(raw)
