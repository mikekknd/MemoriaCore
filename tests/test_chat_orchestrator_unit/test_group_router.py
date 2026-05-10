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
    assert turn_state["discussion_mode"] == "youtube_live"
    assert "<youtube_live_group_router_rules>" in prompt_text
    assert "禁止同一角色連續發言" in prompt_text
    assert "remaining_bot_turns_including_next 是硬性上限" in prompt_text


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


def test_youtube_live_router_prompt_includes_live_episode_plan_constraints():
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
    assert turn_state["live_episode_plan"]["turn_contract"]["turn_id"] == "seg_01_turn_01"
    assert turn_state["live_episode_plan"]["speaker_policy"]["allowed_character_ids"] == ["char-a", "char-b"]
    assert "char-c" not in json.dumps(turn_state["not_yet_spoken_this_turn"], ensure_ascii=False)


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
    assert turn_state["live_episode_plan"]["dialogue_policy"]["max_replies"] == 3
    assert "allowed_character_ids" not in turn_state["live_episode_plan"]["speaker_policy"]
    assert turn_state["live_episode_plan"]["speaker_policy"]["anchored_character_ids"] == ["char-b"]
    assert turn_state["live_episode_plan"]["speaker_policy"]["anchor_status"] == "first_reply_already_completed"


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
    assert "allowed_character_ids" not in turn_state["live_episode_plan"]["speaker_policy"]


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
