import inspect

from YouTubeBridgeV2.live_episode_plan.runner import (
    LiveEpisodePlanContract,
    LiveEpisodePlanState,
    PlanCompletionSignal,
    PlanExecutionStatus,
    PlannedTurnIntent,
    PlannedTurnResult,
    next_planned_turn,
    record_planned_turn_result,
    validate_episode_plan_contract,
)


RAW_TOPIC_PACK_TEXT = "FULL_RAW_TOPIC_PACK_SHOULD_NOT_LEAK"


def _valid_plan():
    return {
        "plan_id": "plan-1",
        "title": "V2 smoke episode",
        "topic_pack_fact_cards": RAW_TOPIC_PACK_TEXT,
        "raw_fact_cards": ["FACT_CARD_RAW_SHOULD_NOT_LEAK"],
        "turns": [
            {
                "id": "opening",
                "purpose": "Open with the main question.",
                "topic_cue": "Why V2 needs a planned runtime.",
                "speaker_policy": {
                    "type": "fixed",
                    "speaker_ids": ["host"],
                },
                "audience_insertion": {
                    "enabled": False,
                    "allow_super_chats": False,
                },
            },
            {
                "id": "cohost-reaction",
                "purpose": "Let cohosts react to the opening.",
                "topic_cue": "Runtime chain reaction.",
                "speaker_policy": {
                    "type": "fixed",
                    "speaker_ids": ["cohost-a", "cohost-b"],
                },
                "audience_insertion": {
                    "enabled": True,
                    "allow_super_chats": True,
                },
            },
        ],
    }


def _state(cursor=0, completed_turn_ids=()):
    contract = validate_episode_plan_contract(_valid_plan())
    return LiveEpisodePlanState(
        contract=contract,
        cursor=cursor,
        completed_turn_ids=completed_turn_ids,
    )


def _assert_no_raw_topic_pack(value):
    text = repr(value)
    assert RAW_TOPIC_PACK_TEXT not in text
    assert "FACT_CARD_RAW_SHOULD_NOT_LEAK" not in text
    assert "topic_pack_fact_cards" not in text
    assert "raw_fact_cards" not in text


def test_valid_episode_plan_contract_is_accepted():
    contract = validate_episode_plan_contract(_valid_plan())

    assert isinstance(contract, LiveEpisodePlanContract)
    assert contract.status == PlanExecutionStatus.RUNNING
    assert contract.plan_id == "plan-1"
    assert contract.title == "V2 smoke episode"
    assert len(contract.turns) == 2
    assert contract.validation_errors == ()
    _assert_no_raw_topic_pack(contract.public_summary)


def test_missing_required_episode_plan_field_is_invalid():
    contract = validate_episode_plan_contract({"plan_id": "plan-1", "turns": []})

    assert contract.status == PlanExecutionStatus.INVALID
    assert "title" in " ".join(contract.validation_errors)
    assert "turns" in " ".join(contract.validation_errors)


def test_cursor_outside_plan_turn_range_is_invalid():
    negative = next_planned_turn(_state(cursor=-1))
    too_far = next_planned_turn(_state(cursor=3))

    for result in (negative, too_far):
        assert result.status == PlanExecutionStatus.INVALID
        assert result.intent is None
        assert result.completion_signal.completed is False
        assert "cursor" in " ".join(result.validation_errors)


def test_first_turn_produces_planned_turn_intent():
    result = next_planned_turn(_state())

    assert isinstance(result, PlannedTurnResult)
    assert result.status == PlanExecutionStatus.RUNNING
    assert isinstance(result.intent, PlannedTurnIntent)
    assert result.intent.turn_id == "opening"
    assert result.intent.turn_index == 0
    assert result.intent.purpose == "Open with the main question."
    assert isinstance(result.completion_signal, PlanCompletionSignal)
    assert result.completion_signal.completed is False


def test_fixed_speaker_policy_is_preserved():
    result = next_planned_turn(_state(cursor=1))

    assert result.intent.speaker_policy == "fixed"
    assert result.intent.speaker_ids == ("cohost-a", "cohost-b")


def test_audience_event_is_excluded_when_turn_policy_disallows_it():
    result = next_planned_turn(
        _state(cursor=0),
        audience_event_summary={
            "type": "message",
            "display_text": "Can I interrupt?",
            "raw_payload": {"secret": "do-not-emit"},
        },
    )

    assert result.intent.audience_summary is None
    assert result.intent.audience_handling_hint == "audience_insertion_disabled"
    assert result.skipped_audience_reason == "turn_policy_disallows_audience"
    assert "raw_payload" not in repr(result)


def test_super_chat_summary_is_allowed_only_when_turn_policy_allows_it():
    allowed = next_planned_turn(
        _state(cursor=1),
        audience_event_summary={
            "type": "super_chat",
            "display_text": "Thanks for the show",
            "amount_micros": 5000000,
            "currency": "TWD",
            "raw_payload": {"token": "secret"},
        },
    )
    blocked = next_planned_turn(
        _state(cursor=0),
        audience_event_summary={
            "type": "super_chat",
            "display_text": "Thanks for the show",
            "amount_micros": 5000000,
            "currency": "TWD",
            "raw_payload": {"token": "secret"},
        },
    )

    assert allowed.intent.audience_summary == {
        "type": "super_chat",
        "display_text": "Thanks for the show",
        "amount_micros": 5000000,
        "currency": "TWD",
    }
    assert allowed.intent.audience_handling_hint == "audience_summary_allowed"
    assert "raw_payload" not in repr(allowed)

    assert blocked.intent.audience_summary is None
    assert blocked.skipped_audience_reason == "turn_policy_disallows_audience"


def test_turn_result_advances_cursor():
    state = _state()
    current = next_planned_turn(state)

    recorded = record_planned_turn_result(state, current)

    assert recorded.next_state.cursor == 1
    assert recorded.next_state.completed_turn_ids == ("opening",)
    assert recorded.completion_signal.completed is False
    assert recorded.redacted_turn_summary == {
        "turn_id": "opening",
        "turn_index": 0,
        "completed": True,
    }


def test_last_turn_result_emits_completion_signal():
    state = _state(cursor=1, completed_turn_ids=("opening",))
    current = next_planned_turn(state)

    recorded = record_planned_turn_result(state, current)

    assert recorded.status == PlanExecutionStatus.COMPLETED
    assert recorded.next_state.cursor == 2
    assert recorded.completion_signal.completed is True
    assert recorded.completion_signal.completed_turn_ids == (
        "opening",
        "cohost-reaction",
    )


def test_raw_topic_pack_text_is_not_emitted_in_turn_intent():
    result = next_planned_turn(_state())

    _assert_no_raw_topic_pack(result.intent)
    _assert_no_raw_topic_pack(result.redacted_turn_summary)


def test_runner_does_not_call_memoria_youtube_storage_or_ui():
    assert list(inspect.signature(next_planned_turn).parameters) == [
        "plan_state",
        "audience_event_summary",
    ]
    assert list(inspect.signature(record_planned_turn_result).parameters) == [
        "plan_state",
        "turn_result",
    ]
