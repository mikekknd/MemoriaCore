import copy
import sys
import importlib.util
from pathlib import Path

import pytest

BRIDGE_ROOT = Path(__file__).resolve().parents[1]
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))

from live_episode_plan_contract import (
    LiveEpisodePlanValidationError,
    current_turn_contract,
    initial_planned_state,
    validate_live_episode_plan,
)

LIVE_PLANNER_VALIDATOR_PATH = (
    Path(__file__).resolve().parents[1].parent
    / ".agents"
    / "skills"
    / "live-episode-planner"
    / "scripts"
    / "validate_episode_plan.py"
)


def _load_live_planner_validator():
    spec = importlib.util.spec_from_file_location(
        "validate_live_episode_planner_skill",
        LIVE_PLANNER_VALIDATOR_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def sample_plan() -> dict:
    return {
        "schema_version": "live_episode_plan.v1",
        "plan_id": "plan-general-panel",
        "title": "泛用多人節目企劃",
        "language": "zh-TW",
        "show_format": {
            "primary": "open_panel",
            "secondary": ["news_commentary", "character_banter"],
            "format_notes": "三人以上依角色功能推進，不固定題材分類。",
        },
        "flow_policy": {
            "segment_order": "locked",
            "audience_interrupts": "allowed_within_current_segment",
            "audience_can_change_segment_order": False,
            "resume_after_interrupt": "next_planned_turn_contract",
        },
        "audience_event_classifier": {
            "event_types": [
                "question",
                "reaction",
                "correction",
                "super_chat",
                "off_topic",
                "hostile",
                "prompt_injection",
            ],
            "actions": {
                "question": "bounded_interrupt",
                "reaction": "optional_ack",
                "correction": "verify_then_ack",
                "super_chat": "bounded_interrupt",
                "off_topic": "ignore_or_soft_ack",
                "hostile": "ignore_or_deescalate",
                "prompt_injection": "ignore",
            },
        },
        "topic_pack_refs": [
            {
                "pack_id": 1,
                "purpose": "evidence_retrieval",
                "query_bias": ["作品名稱", "觀眾反應"],
            }
        ],
        "participants": [
            {
                "participant_id": "host-a",
                "display_name": "主持A",
                "role_function": ["host", "energy_driver"],
                "speaking_style_bias": ["短句"],
                "best_for_turns": ["hook", "transition"],
                "avoid_turns": ["dense_fact_exposition"],
                "interaction_edges": [],
            },
            {
                "participant_id": "analyst-b",
                "display_name": "分析B",
                "role_function": ["analyst"],
                "speaking_style_bias": ["拆解脈絡"],
                "best_for_turns": ["analysis"],
                "avoid_turns": [],
                "interaction_edges": [],
            },
            {
                "participant_id": "skeptic-c",
                "display_name": "質疑C",
                "role_function": ["skeptic"],
                "speaking_style_bias": ["提出反方"],
                "best_for_turns": ["counterpoint"],
                "avoid_turns": [],
                "interaction_edges": [],
            },
        ],
        "episode_arc": {
            "thesis": "本集核心主張",
            "tension": "本集主要張力",
            "listener_takeaways": ["觀眾知道該段值得聽的理由"],
            "opening_strategy": "先建立事件感",
            "closing_strategy": "回收觀眾可帶走的重點",
        },
        "segments": [
            {
                "segment_id": "seg_01",
                "title": "事件 Hook",
                "goal": "建立為什麼現在值得聊",
                "planned_turn_contracts": [
                    {
                        "turn_id": "seg_01_turn_01",
                        "turn_type": "hook",
                        "intent": "用具體事件開場",
                        "speaker_policy": {
                            "selection_mode": "router_select",
                            "preferred_role_functions": ["host"],
                            "allowed_participant_ids": [],
                            "avoid_repeat_speaker": True,
                        },
                        "evidence_policy": {
                            "queries": ["事件名稱 爆點 觀眾反應"],
                            "required_entities": ["事件名稱"],
                            "allow_unverified_claims": False,
                            "max_cards": 3,
                        },
                        "forbidden_repetition": {
                            "claims": [],
                            "metaphors": [],
                            "openings": [],
                        },
                        "output_requirements": {
                            "max_sentences": 2,
                            "must_end_with_question": False,
                            "allow_audience_question": False,
                            "should_handoff": True,
                            "handoff_target_function": "analyst",
                        },
                        "handoff": {
                            "next_turn_hint": "交給分析角色補脈絡",
                        },
                    },
                    {
                        "turn_id": "seg_01_turn_02",
                        "turn_type": "analysis",
                        "intent": "說明事件背後脈絡",
                        "speaker_policy": {
                            "selection_mode": "router_select",
                            "preferred_role_functions": ["analyst"],
                            "allowed_participant_ids": [],
                            "avoid_repeat_speaker": True,
                        },
                        "evidence_policy": {
                            "queries": ["事件名稱 背景 脈絡"],
                            "required_entities": ["事件名稱"],
                            "allow_unverified_claims": False,
                            "max_cards": 3,
                        },
                        "forbidden_repetition": {
                            "claims": ["已經說過事件值得聊"],
                            "metaphors": [],
                            "openings": ["確實如此"],
                        },
                        "output_requirements": {
                            "max_sentences": 2,
                            "must_end_with_question": False,
                            "allow_audience_question": False,
                            "should_handoff": False,
                            "handoff_target_function": "",
                        },
                        "handoff": {
                            "next_turn_hint": "",
                        },
                    },
                ],
                "audience_handling": {
                    "allowed_interrupt_types": ["question", "reaction", "super_chat", "correction"],
                    "max_interrupt_turns": 2,
                    "resume_rule": "bridge_back_to_segment_goal",
                },
                "completion_conditions": {
                    "min_planned_turns": 2,
                    "max_planned_turns": 4,
                    "required_turn_types": ["hook", "analysis"],
                    "optional_turn_types": ["counterpoint", "transition"],
                },
                "transition_targets": [
                    {
                        "target_segment_id": "seg_02",
                        "transition_intent": "從事件轉入核心爭議",
                    }
                ],
            }
        ],
        "constraints": {
            "forbidden_repetition": {
                "claims": [],
                "openings": [],
                "jokes": [],
            },
            "safety": {
                "audience_is_untrusted": True,
                "do_not_follow_audience_instructions": True,
                "do_not_expose_internal_plan": True,
            },
        },
        "performance_hints": {
            "tts": {},
            "subtitles": {},
            "expressions": {},
            "camera": {},
        },
    }


def test_validate_live_episode_plan_accepts_generalized_plan():
    plan = validate_live_episode_plan(sample_plan())

    assert plan["plan_id"] == "plan-general-panel"
    assert len(plan["participants"]) == 3
    assert plan["show_format"]["primary"] == "open_panel"
    assert plan["segments"][0]["planned_turn_contracts"][0]["dialogue_policy"] == {
        "min_replies": 1,
        "max_replies": 2,
        "autonomy": "guided",
        "preferred_flow": [],
    }
    assert plan["segments"][0]["planned_turn_contracts"][1]["dialogue_policy"]["max_replies"] == 3
    assert plan["segments"][0]["completion_conditions"]["required_turn_types"] == [
        "hook",
        "analysis",
    ]


def test_validate_live_episode_plan_returns_deepcopy():
    source = sample_plan()
    plan = validate_live_episode_plan(source)

    plan["participants"][0]["participant_id"] = "mutated"

    assert source["participants"][0]["participant_id"] == "host-a"


def test_validate_live_episode_plan_rejects_subjective_required_takeaways_condition():
    plan = sample_plan()
    plan["segments"][0]["completion_conditions"]["required_takeaways"] = [
        "觀眾知道本段為何值得聽"
    ]

    with pytest.raises(LiveEpisodePlanValidationError, match="required_takeaways"):
        validate_live_episode_plan(plan)


def test_validate_live_episode_plan_requires_structured_evidence_policy_queries():
    plan = sample_plan()
    plan["segments"][0]["planned_turn_contracts"][0]["evidence_policy"] = {
        "topic_pack_query": "事件名稱 + 爆點 + 觀眾反應"
    }

    with pytest.raises(LiveEpisodePlanValidationError, match="evidence_policy.queries"):
        validate_live_episode_plan(plan)


def test_validate_live_episode_plan_accepts_turn_evidence_brief():
    plan = sample_plan()
    turn = plan["segments"][0]["planned_turn_contracts"][0]
    turn["evidence_brief"] = {
        "facts_to_state": [
            "事件 A 在 2026-05-10 公開更新，這是本輪可以直接說出的事實。",
            "公開來源只支援事件已更新，不支援推論事件已成為市場第一。",
        ],
        "source_boundaries": [
            "FactCards 與 sources.md 是企劃層查證工件，不是角色要朗讀的話題卡。",
            "沒有來源支撐的成因或排名推論不可自行補完。",
        ],
        "do_not_delegate_to_character": True,
    }

    validated = validate_live_episode_plan(plan)

    brief = validated["segments"][0]["planned_turn_contracts"][0]["evidence_brief"]
    assert brief["facts_to_state"][0].startswith("事件 A")
    assert brief["source_boundaries"][0].startswith("FactCards")
    assert brief["do_not_delegate_to_character"] is True


def test_validate_live_episode_plan_rejects_invalid_turn_evidence_brief():
    plan = sample_plan()
    turn = plan["segments"][0]["planned_turn_contracts"][0]
    turn["evidence_brief"] = {
        "facts_to_state": [],
        "source_boundaries": ["缺少可直接播出的事實時，這不是完整企劃。"],
        "do_not_delegate_to_character": True,
    }

    with pytest.raises(LiveEpisodePlanValidationError, match="evidence_brief.facts_to_state"):
        validate_live_episode_plan(plan)


def test_live_episode_planner_skill_validator_requires_evidence_brief_for_source_backed_turns():
    validator = _load_live_planner_validator()
    plan = sample_plan()

    with pytest.raises(validator.EpisodePlanSkillValidationError, match="evidence_brief"):
        validator._require_runtime_evidence_briefs(plan)


def test_validate_live_episode_plan_rejects_speaker_policy_outside_participants():
    plan = sample_plan()
    plan["segments"][0]["planned_turn_contracts"][0]["speaker_policy"][
        "allowed_participant_ids"
    ] = ["missing-speaker"]

    with pytest.raises(LiveEpisodePlanValidationError, match="allowed_participant_ids"):
        validate_live_episode_plan(plan)


def test_validate_live_episode_plan_rejects_invalid_dialogue_policy_bounds():
    plan = sample_plan()
    plan["segments"][0]["planned_turn_contracts"][0]["dialogue_policy"] = {
        "min_replies": 3,
        "max_replies": 2,
        "autonomy": "guided",
        "preferred_flow": [],
    }

    with pytest.raises(LiveEpisodePlanValidationError, match="dialogue reply bounds"):
        validate_live_episode_plan(plan)


def test_validate_live_episode_plan_accepts_audience_event_policy():
    plan = sample_plan()
    turn = plan["segments"][0]["planned_turn_contracts"][0]
    turn["turn_type"] = "chat_bridge"
    turn["audience_event_policy"] = {
        "requires_real_events": True,
        "event_types": ["reaction", "super_chat"],
        "min_events": 1,
        "max_events": 2,
        "empty_behavior": "fallback_without_audience_quotes",
        "empty_fallback_intent": "沒有真實留言時，不要引用聊天室，改用一般偏好差異收束。",
    }
    plan["segments"][0]["completion_conditions"]["required_turn_types"] = ["chat_bridge"]

    validated = validate_live_episode_plan(plan)

    assert validated["segments"][0]["planned_turn_contracts"][0]["audience_event_policy"]["requires_real_events"] is True


def test_validate_live_episode_plan_accepts_focus_targets_and_focus_policy():
    plan = sample_plan()
    plan["focus_targets"] = [
        {
            "target_id": "tool-alpha",
            "label": "Alpha Notebook",
            "target_type": "productivity_tool",
            "selection_reason": "代表低摩擦、快速上手的個人整理工具。",
            "analysis_angles": ["上手成本", "整理彈性", "團隊交接限制"],
            "recommendation_axes": ["適合誰", "不適合誰", "個人首選理由"],
        },
        {
            "target_id": "workflow-beta",
            "label": "Beta Review Loop",
            "target_type": "team_workflow",
            "selection_reason": "代表流程約束較強、但交接品質穩定的團隊方案。",
            "analysis_angles": ["審核節奏", "責任分工", "擴張成本"],
            "recommendation_axes": ["最佳使用情境", "避雷條件", "角色偏好排序"],
        },
    ]
    first_turn = plan["segments"][0]["planned_turn_contracts"][0]
    first_turn["turn_type"] = "focus_deep_dive"
    first_turn["focus_policy"] = {
        "target_ids": ["tool-alpha"],
        "depth_goal": "停在 Alpha Notebook 的使用情境與限制，不回到抽象選工具原則。",
        "must_cover": ["上手成本", "整理彈性", "團隊交接限制"],
        "avoid_generic_reframe": True,
        "recommendation_mode": "best_for",
    }
    second_turn = plan["segments"][0]["planned_turn_contracts"][1]
    second_turn["turn_type"] = "personal_recommendation"
    second_turn["focus_policy"] = {
        "target_ids": ["tool-alpha", "workflow-beta"],
        "depth_goal": "以角色偏好給出具體選擇，不偽裝成中立總結。",
        "must_cover": ["推薦給誰", "推薦理由", "避雷條件"],
        "avoid_generic_reframe": True,
        "recommendation_mode": "personal_pick",
    }
    second_turn["recommendation_policy"] = {
        "recommendation_style": "角色可以偏好明確，但必須給出可採用的選擇條件。",
        "recommendations": [
            {
                "target_id": "tool-alpha",
                "best_for": "個人先整理、還沒有團隊交接壓力的使用者。",
                "why": "上手快，能把零散想法先收進同一處。",
                "avoid_if": "需要多人審核、權責追蹤或長期知識庫一致性。",
                "personal_bias": "主持偏好把它當第一週試用入口。",
            },
            {
                "target_id": "workflow-beta",
                "best_for": "已經有多人協作與交付責任的團隊。",
                "why": "流程約束比較重，但能減少交接時的解讀落差。",
                "avoid_if": "團隊現在只需要個人速度，還沒有固定審核節奏。",
                "personal_bias": "分析角色偏好把它當正式上線方案。",
            },
        ],
        "ranked_order": ["tool-alpha", "workflow-beta"],
    }
    second_turn["stance_policy"] = {
        "stance_mode": "assertive",
        "must_take_side": True,
        "disclaimer_budget": 0,
        "avoid_disclaimer_phrases": [
            "每個人喜好不同",
            "僅供參考",
            "榜單只是參考",
        ],
        "edge_instruction": "直接給角色偏好的排序與取捨，不用先替所有人留退路。",
    }
    plan["segments"][0]["completion_conditions"]["required_turn_types"] = [
        "focus_deep_dive",
        "personal_recommendation",
    ]

    validated = validate_live_episode_plan(plan)

    turns = validated["segments"][0]["planned_turn_contracts"]
    assert validated["focus_targets"][0]["target_id"] == "tool-alpha"
    assert turns[0]["dialogue_policy"]["max_replies"] == 3
    assert turns[1]["dialogue_policy"]["max_replies"] == 3
    assert turns[1]["focus_policy"]["recommendation_mode"] == "personal_pick"
    assert turns[1]["recommendation_policy"]["recommendations"][0]["best_for"].startswith("個人先整理")
    assert turns[1]["stance_policy"]["disclaimer_budget"] == 0


def test_validate_live_episode_plan_rejects_unknown_focus_policy_target_id():
    plan = sample_plan()
    plan["focus_targets"] = [
        {
            "target_id": "known-target",
            "label": "Known Target",
            "target_type": "generic_option",
            "selection_reason": "用來驗證 focus_policy target id 檢查。",
            "analysis_angles": ["角度一", "角度二"],
            "recommendation_axes": ["適合誰", "避雷條件"],
        },
        {
            "target_id": "comparison-target",
            "label": "Comparison Target",
            "target_type": "generic_option",
            "selection_reason": "讓焦點對象清單符合多對象企劃容量。",
            "analysis_angles": ["角度一", "角度二"],
            "recommendation_axes": ["適合誰", "避雷條件"],
        }
    ]
    plan["segments"][0]["planned_turn_contracts"][0]["focus_policy"] = {
        "target_ids": ["missing-target"],
        "depth_goal": "驗證不存在的焦點對象會被拒絕。",
        "must_cover": ["角度一", "角度二"],
        "avoid_generic_reframe": True,
        "recommendation_mode": "best_for",
    }

    with pytest.raises(LiveEpisodePlanValidationError, match="focus_policy.target_ids"):
        validate_live_episode_plan(plan)


def test_validate_live_episode_plan_rejects_focus_deep_dive_without_enough_angles():
    plan = sample_plan()
    plan["focus_targets"] = [
        {
            "target_id": "known-target",
            "label": "Known Target",
            "target_type": "generic_option",
            "selection_reason": "用來驗證深挖角度數量。",
            "analysis_angles": ["角度一", "角度二", "角度三"],
            "recommendation_axes": ["適合誰", "推薦理由", "避雷條件"],
        },
        {
            "target_id": "comparison-target",
            "label": "Comparison Target",
            "target_type": "generic_option",
            "selection_reason": "讓焦點對象清單符合多對象企劃容量。",
            "analysis_angles": ["角度一", "角度二", "角度三"],
            "recommendation_axes": ["適合誰", "推薦理由", "避雷條件"],
        },
    ]
    turn = plan["segments"][0]["planned_turn_contracts"][0]
    turn["turn_type"] = "focus_deep_dive"
    turn["focus_policy"] = {
        "target_ids": ["known-target"],
        "depth_goal": "驗證深挖 turn 不能只放兩個標籤式角度。",
        "must_cover": ["角度一", "角度二"],
        "avoid_generic_reframe": True,
        "recommendation_mode": "best_for",
    }
    plan["segments"][0]["completion_conditions"]["required_turn_types"] = [
        "focus_deep_dive",
        "analysis",
    ]

    with pytest.raises(LiveEpisodePlanValidationError, match="focus_policy.must_cover"):
        validate_live_episode_plan(plan)


def test_validate_live_episode_plan_rejects_personal_recommendation_without_details():
    plan = sample_plan()
    plan["focus_targets"] = [
        {
            "target_id": "known-target",
            "label": "Known Target",
            "target_type": "generic_option",
            "selection_reason": "用來驗證推薦明細。",
            "analysis_angles": ["角度一", "角度二", "角度三"],
            "recommendation_axes": ["適合誰", "推薦理由", "避雷條件"],
        },
        {
            "target_id": "comparison-target",
            "label": "Comparison Target",
            "target_type": "generic_option",
            "selection_reason": "讓焦點對象清單符合多對象企劃容量。",
            "analysis_angles": ["角度一", "角度二", "角度三"],
            "recommendation_axes": ["適合誰", "推薦理由", "避雷條件"],
        },
    ]
    turn = plan["segments"][0]["planned_turn_contracts"][0]
    turn["turn_type"] = "personal_recommendation"
    turn["focus_policy"] = {
        "target_ids": ["known-target", "comparison-target"],
        "depth_goal": "推薦 turn 必須有每個焦點對象的具體推薦明細。",
        "must_cover": ["推薦給誰", "推薦理由", "避雷條件"],
        "avoid_generic_reframe": True,
        "recommendation_mode": "personal_pick",
    }
    plan["segments"][0]["completion_conditions"]["required_turn_types"] = [
        "personal_recommendation",
        "analysis",
    ]

    with pytest.raises(LiveEpisodePlanValidationError, match="recommendation_policy"):
        validate_live_episode_plan(plan)


def test_validate_live_episode_plan_rejects_recommendation_policy_unknown_target_id():
    plan = sample_plan()
    plan["focus_targets"] = [
        {
            "target_id": "known-target",
            "label": "Known Target",
            "target_type": "generic_option",
            "selection_reason": "用來驗證推薦 target id。",
            "analysis_angles": ["角度一", "角度二", "角度三"],
            "recommendation_axes": ["適合誰", "推薦理由", "避雷條件"],
        },
        {
            "target_id": "comparison-target",
            "label": "Comparison Target",
            "target_type": "generic_option",
            "selection_reason": "讓焦點對象清單符合多對象企劃容量。",
            "analysis_angles": ["角度一", "角度二", "角度三"],
            "recommendation_axes": ["適合誰", "推薦理由", "避雷條件"],
        },
    ]
    turn = plan["segments"][0]["planned_turn_contracts"][0]
    turn["turn_type"] = "personal_recommendation"
    turn["focus_policy"] = {
        "target_ids": ["known-target"],
        "depth_goal": "驗證推薦明細 target id 會被交叉檢查。",
        "must_cover": ["推薦給誰", "推薦理由", "避雷條件"],
        "avoid_generic_reframe": True,
        "recommendation_mode": "personal_pick",
    }
    turn["recommendation_policy"] = {
        "recommendation_style": "角色主觀推薦。",
        "recommendations": [
            {
                "target_id": "missing-target",
                "best_for": "測試對象。",
                "why": "測試理由。",
                "avoid_if": "測試避雷。",
            }
        ],
    }
    plan["segments"][0]["completion_conditions"]["required_turn_types"] = [
        "personal_recommendation",
        "analysis",
    ]

    with pytest.raises(LiveEpisodePlanValidationError, match="recommendation_policy.recommendations"):
        validate_live_episode_plan(plan)


def test_validate_live_episode_plan_rejects_personal_recommendation_without_stance_policy():
    plan = sample_plan()
    plan["focus_targets"] = [
        {
            "target_id": "known-target",
            "label": "Known Target",
            "target_type": "generic_option",
            "selection_reason": "用來驗證個人推薦必須站邊。",
            "analysis_angles": ["角度一", "角度二", "角度三"],
            "recommendation_axes": ["適合誰", "推薦理由", "避雷條件"],
        },
        {
            "target_id": "comparison-target",
            "label": "Comparison Target",
            "target_type": "generic_option",
            "selection_reason": "讓焦點對象清單符合多對象企劃容量。",
            "analysis_angles": ["角度一", "角度二", "角度三"],
            "recommendation_axes": ["適合誰", "推薦理由", "避雷條件"],
        },
    ]
    turn = plan["segments"][0]["planned_turn_contracts"][0]
    turn["turn_type"] = "personal_recommendation"
    turn["focus_policy"] = {
        "target_ids": ["known-target"],
        "depth_goal": "推薦 turn 必須避免安全退路。",
        "must_cover": ["推薦給誰", "推薦理由", "避雷條件"],
        "avoid_generic_reframe": True,
        "recommendation_mode": "personal_pick",
    }
    turn["recommendation_policy"] = {
        "recommendation_style": "角色主觀推薦。",
        "recommendations": [
            {
                "target_id": "known-target",
                "best_for": "測試對象。",
                "why": "測試理由。",
                "avoid_if": "測試避雷。",
            }
        ],
    }
    plan["segments"][0]["completion_conditions"]["required_turn_types"] = [
        "personal_recommendation",
        "analysis",
    ]

    with pytest.raises(LiveEpisodePlanValidationError, match="stance_policy"):
        validate_live_episode_plan(plan)


def test_validate_live_episode_plan_rejects_personal_recommendation_with_disclaimer_budget():
    plan = sample_plan()
    plan["focus_targets"] = [
        {
            "target_id": "known-target",
            "label": "Known Target",
            "target_type": "generic_option",
            "selection_reason": "用來驗證免責聲明預算。",
            "analysis_angles": ["角度一", "角度二", "角度三"],
            "recommendation_axes": ["適合誰", "推薦理由", "避雷條件"],
        },
        {
            "target_id": "comparison-target",
            "label": "Comparison Target",
            "target_type": "generic_option",
            "selection_reason": "讓焦點對象清單符合多對象企劃容量。",
            "analysis_angles": ["角度一", "角度二", "角度三"],
            "recommendation_axes": ["適合誰", "推薦理由", "避雷條件"],
        },
    ]
    turn = plan["segments"][0]["planned_turn_contracts"][0]
    turn["turn_type"] = "personal_recommendation"
    turn["focus_policy"] = {
        "target_ids": ["known-target"],
        "depth_goal": "推薦 turn 不能把免責聲明當固定開場。",
        "must_cover": ["推薦給誰", "推薦理由", "避雷條件"],
        "avoid_generic_reframe": True,
        "recommendation_mode": "personal_pick",
    }
    turn["recommendation_policy"] = {
        "recommendation_style": "角色主觀推薦。",
        "recommendations": [
            {
                "target_id": "known-target",
                "best_for": "測試對象。",
                "why": "測試理由。",
                "avoid_if": "測試避雷。",
            }
        ],
    }
    turn["stance_policy"] = {
        "stance_mode": "assertive",
        "must_take_side": True,
        "disclaimer_budget": 1,
        "avoid_disclaimer_phrases": ["每個人喜好不同"],
        "edge_instruction": "直接站邊。",
    }
    plan["segments"][0]["completion_conditions"]["required_turn_types"] = [
        "personal_recommendation",
        "analysis",
    ]

    with pytest.raises(LiveEpisodePlanValidationError, match="disclaimer_budget"):
        validate_live_episode_plan(plan)


def test_validate_live_episode_plan_accepts_turn_budget_and_claim_policy():
    plan = sample_plan()
    plan["turn_budget"] = {
        "target_planned_turns": 4,
        "opening_turns": 1,
        "content_turns": 3,
        "planning_note": "短測試節目只驗證一個主軸。",
    }
    plan["claim_ledger"] = {
        "semantic_claims": [
            {
                "claim_id": "ranking_shift",
                "meaning": "近期榜單名次發生變化，形成新的討論入口。",
                "ban_paraphrase": True,
            },
            {
                "claim_id": "ranking_is_entry",
                "meaning": "排行榜可以作為找作品入口，但不能替觀眾做最終判決。",
                "ban_paraphrase": True,
            },
        ]
    }
    first_turn = plan["segments"][0]["planned_turn_contracts"][0]
    first_turn["claim_policy"] = {
        "new_claim_ids": ["ranking_shift"],
        "forbidden_claim_ids": [],
        "must_not_paraphrase_used_claims": True,
    }
    second_turn = plan["segments"][0]["planned_turn_contracts"][1]
    second_turn["claim_policy"] = {
        "new_claim_ids": ["ranking_is_entry"],
        "forbidden_claim_ids": ["ranking_shift"],
        "must_not_paraphrase_used_claims": True,
    }

    validated = validate_live_episode_plan(plan)

    assert validated["turn_budget"]["target_planned_turns"] == 4
    assert validated["claim_ledger"]["semantic_claims"][0]["claim_id"] == "ranking_shift"
    assert validated["segments"][0]["planned_turn_contracts"][1]["claim_policy"][
        "forbidden_claim_ids"
    ] == ["ranking_shift"]


def test_validate_live_episode_plan_rejects_unknown_claim_policy_id():
    plan = sample_plan()
    plan["claim_ledger"] = {
        "semantic_claims": [
            {
                "claim_id": "known_claim",
                "meaning": "已知語義主張。",
                "ban_paraphrase": True,
            }
        ]
    }
    plan["segments"][0]["planned_turn_contracts"][0]["claim_policy"] = {
        "new_claim_ids": ["missing_claim"],
        "forbidden_claim_ids": [],
        "must_not_paraphrase_used_claims": True,
    }

    with pytest.raises(LiveEpisodePlanValidationError, match="claim_policy.new_claim_ids"):
        validate_live_episode_plan(plan)


def test_validate_live_episode_plan_rejects_invalid_segment_rhythm_control():
    plan = sample_plan()
    plan["segments"][0]["rhythm_control"] = {
        "discussion_goal": "",
        "data_points": [],
        "audience_understanding": "",
        "close_when": [],
    }

    with pytest.raises(LiveEpisodePlanValidationError, match="rhythm_control.discussion_goal"):
        validate_live_episode_plan(plan)


def test_validate_live_episode_plan_rejects_invalid_audience_empty_behavior():
    plan = sample_plan()
    turn = plan["segments"][0]["planned_turn_contracts"][0]
    turn["audience_event_policy"] = {
        "requires_real_events": True,
        "empty_behavior": "simulate_chat",
    }

    with pytest.raises(LiveEpisodePlanValidationError, match="empty_behavior"):
        validate_live_episode_plan(plan)


def test_initial_planned_state_targets_first_turn_contract():
    plan = validate_live_episode_plan(sample_plan())
    state = initial_planned_state(plan)
    turn = current_turn_contract(plan, state)

    assert state["plan_id"] == "plan-general-panel"
    assert state["current_segment_index"] == 0
    assert state["current_turn_index"] == 0
    assert turn["turn_id"] == "seg_01_turn_01"
    assert state["segment_memory"]["covered_claims"] == []


def test_current_turn_contract_returns_none_after_plan_complete():
    plan = validate_live_episode_plan(sample_plan())
    state = initial_planned_state(plan)
    state["plan_status"] = "completed"
    state["current_turn_index"] = 1

    assert current_turn_contract(plan, state) is None


def test_validate_live_episode_plan_does_not_mutate_input():
    plan = sample_plan()
    original = copy.deepcopy(plan)

    validate_live_episode_plan(plan)

    assert plan == original
