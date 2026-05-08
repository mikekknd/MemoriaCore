import copy
import sys
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


def test_validate_live_episode_plan_rejects_speaker_policy_outside_participants():
    plan = sample_plan()
    plan["segments"][0]["planned_turn_contracts"][0]["speaker_policy"][
        "allowed_participant_ids"
    ] = ["missing-speaker"]

    with pytest.raises(LiveEpisodePlanValidationError, match="allowed_participant_ids"):
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
    state["current_segment_index"] = 9

    assert current_turn_contract(plan, state) is None


def test_validate_live_episode_plan_does_not_mutate_input():
    plan = sample_plan()
    original = copy.deepcopy(plan)

    validate_live_episode_plan(plan)

    assert plan == original
