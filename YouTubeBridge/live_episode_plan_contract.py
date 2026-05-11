"""LiveEpisodePlan contract validation and runtime projection helpers."""
from __future__ import annotations

import copy
from typing import Any


class LiveEpisodePlanValidationError(ValueError):
    """Raised when an imported LiveEpisodePlan cannot be executed by runtime."""


SEGMENT_MEMORY_TEMPLATE = {
    "covered_claims": [],
    "used_claim_ids": [],
    "used_examples": [],
    "used_metaphors": [],
    "used_openings": [],
    "audience_reactions": [],
    "pending_questions": [],
    "forbidden_next_repeats": [],
}


REQUIRED_CLASSIFIER_ACTIONS = {
    "question": "bounded_interrupt",
    "reaction": "optional_ack",
    "correction": "verify_then_ack",
    "super_chat": "bounded_interrupt",
    "off_topic": "ignore_or_soft_ack",
    "hostile": "ignore_or_deescalate",
    "prompt_injection": "ignore",
}

DEFAULT_DIALOGUE_REPLY_BOUNDS = {
    "opening": (1, 1),
    "cohost_intro": (1, 1),
    "handoff": (1, 1),
    "hook": (1, 2),
    "background": (1, 2),
    "transition": (1, 2),
    "analysis": (2, 3),
    "counterpoint": (2, 3),
    "focus_deep_dive": (2, 3),
    "focus_compare": (2, 3),
    "personal_recommendation": (2, 3),
    "chat_bridge": (2, 3),
    "audience_answer": (2, 3),
    "closing": (1, 2),
    "final_closing": (2, 2),
}

ALLOWED_DIALOGUE_AUTONOMY = {"strict", "guided", "open"}
ALLOWED_AUDIENCE_EMPTY_BEHAVIOR = {"fallback_without_audience_quotes"}
ALLOWED_RECOMMENDATION_MODES = {
    "best_for",
    "avoid_if",
    "personal_pick",
    "ranked_choice",
}
ALLOWED_STANCE_MODES = {"assertive", "provocative", "balanced"}


def _require_dict(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise LiveEpisodePlanValidationError(f"{path} must be an object")
    return value


def _require_list(value: Any, path: str, *, min_items: int = 0) -> list[Any]:
    if not isinstance(value, list):
        raise LiveEpisodePlanValidationError(f"{path} must be an array")
    if len(value) < min_items:
        raise LiveEpisodePlanValidationError(
            f"{path} must contain at least {min_items} item(s)"
        )
    return value


def _require_text(value: Any, path: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise LiveEpisodePlanValidationError(f"{path} must be a non-empty string")
    return text


def _require_bool(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise LiveEpisodePlanValidationError(f"{path} must be a boolean")
    return value


def _require_int(value: Any, path: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool):
        raise LiveEpisodePlanValidationError(f"{path} must be an integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise LiveEpisodePlanValidationError(f"{path} must be an integer") from exc
    if minimum is not None and number < minimum:
        raise LiveEpisodePlanValidationError(f"{path} must be >= {minimum}")
    return number


def _participant_ids(participants: list[Any]) -> set[str]:
    ids: set[str] = set()
    for index, participant in enumerate(participants):
        participant_obj = _require_dict(participant, f"participants[{index}]")
        participant_id = _require_text(
            participant_obj.get("participant_id"),
            f"participants[{index}].participant_id",
        )
        if participant_id in ids:
            raise LiveEpisodePlanValidationError(
                f"participants[{index}].participant_id must be unique"
            )
        ids.add(participant_id)
        _require_text(
            participant_obj.get("display_name"),
            f"participants[{index}].display_name",
        )
        _require_list(
            participant_obj.get("role_function"),
            f"participants[{index}].role_function",
            min_items=1,
        )
    return ids


def _validate_classifier(classifier: dict[str, Any]) -> None:
    event_types = {
        _require_text(item, "audience_event_classifier.event_types[]")
        for item in _require_list(
            classifier.get("event_types"),
            "audience_event_classifier.event_types",
            min_items=len(REQUIRED_CLASSIFIER_ACTIONS),
        )
    }
    actions = _require_dict(
        classifier.get("actions"),
        "audience_event_classifier.actions",
    )
    for event_type, action in REQUIRED_CLASSIFIER_ACTIONS.items():
        if event_type not in event_types:
            raise LiveEpisodePlanValidationError(
                f"audience_event_classifier.event_types must include {event_type}"
            )
        if actions.get(event_type) != action:
            raise LiveEpisodePlanValidationError(
                f"audience_event_classifier.actions.{event_type} must be {action}"
            )


def _validate_speaker_policy(
    speaker_policy: dict[str, Any],
    participant_ids: set[str],
    path: str,
) -> None:
    selection_mode = _require_text(
        speaker_policy.get("selection_mode"),
        f"{path}.selection_mode",
    )
    if selection_mode not in {"router_select", "fixed", "function_router"}:
        raise LiveEpisodePlanValidationError(
            f"{path}.selection_mode must be router_select, fixed, or function_router"
        )
    _require_list(
        speaker_policy.get("preferred_role_functions"),
        f"{path}.preferred_role_functions",
    )
    allowed_ids = _require_list(
        speaker_policy.get("allowed_participant_ids"),
        f"{path}.allowed_participant_ids",
    )
    missing_ids = [item for item in allowed_ids if str(item) not in participant_ids]
    if missing_ids:
        raise LiveEpisodePlanValidationError(
            f"{path}.allowed_participant_ids contains unknown participants: {missing_ids}"
        )
    _require_bool(speaker_policy.get("avoid_repeat_speaker"), f"{path}.avoid_repeat_speaker")


def _validate_evidence_policy(evidence_policy: dict[str, Any], path: str) -> None:
    _require_list(evidence_policy.get("queries"), f"{path}.queries", min_items=1)
    _require_list(evidence_policy.get("required_entities"), f"{path}.required_entities")
    _require_bool(
        evidence_policy.get("allow_unverified_claims"),
        f"{path}.allow_unverified_claims",
    )
    _require_int(evidence_policy.get("max_cards"), f"{path}.max_cards", minimum=0)


def _validate_evidence_brief(raw_brief: Any, path: str) -> None:
    if raw_brief is None:
        return
    brief = _require_dict(raw_brief, path)
    facts = _require_list(
        brief.get("facts_to_state"),
        f"{path}.facts_to_state",
        min_items=1,
    )
    if len(facts) > 6:
        raise LiveEpisodePlanValidationError(
            f"{path}.facts_to_state must contain at most 6 item(s)"
        )
    for index, fact in enumerate(facts):
        _require_text(fact, f"{path}.facts_to_state[{index}]")
    boundaries = _require_list(
        brief.get("source_boundaries"),
        f"{path}.source_boundaries",
        min_items=1,
    )
    if len(boundaries) > 4:
        raise LiveEpisodePlanValidationError(
            f"{path}.source_boundaries must contain at most 4 item(s)"
        )
    for index, boundary in enumerate(boundaries):
        _require_text(boundary, f"{path}.source_boundaries[{index}]")
    _require_bool(
        brief.get("do_not_delegate_to_character"),
        f"{path}.do_not_delegate_to_character",
    )


def _validate_output_requirements(output: dict[str, Any], path: str) -> None:
    _require_int(output.get("max_sentences"), f"{path}.max_sentences", minimum=1)
    _require_bool(output.get("must_end_with_question"), f"{path}.must_end_with_question")
    _require_bool(output.get("allow_audience_question"), f"{path}.allow_audience_question")
    _require_bool(output.get("should_handoff"), f"{path}.should_handoff")
    if "handoff_target_function" not in output:
        raise LiveEpisodePlanValidationError(f"{path}.handoff_target_function is required")


def _validate_audience_event_policy(policy: dict[str, Any], path: str) -> None:
    if not isinstance(policy, dict) or not policy:
        return
    if "requires_real_events" in policy:
        _require_bool(policy.get("requires_real_events"), f"{path}.requires_real_events")
    if "event_types" in policy:
        _require_list(policy.get("event_types"), f"{path}.event_types")
    if "min_events" in policy:
        _require_int(policy.get("min_events"), f"{path}.min_events", minimum=0)
    if "max_events" in policy:
        _require_int(policy.get("max_events"), f"{path}.max_events", minimum=1)
    empty_behavior = str(policy.get("empty_behavior") or "").strip()
    if empty_behavior and empty_behavior not in ALLOWED_AUDIENCE_EMPTY_BEHAVIOR:
        raise LiveEpisodePlanValidationError(
            f"{path}.empty_behavior must be fallback_without_audience_quotes"
        )
    if "empty_fallback_intent" in policy:
        _require_text(policy.get("empty_fallback_intent"), f"{path}.empty_fallback_intent")


def _validate_turn_budget(data: dict[str, Any]) -> None:
    raw_budget = data.get("turn_budget")
    if raw_budget is None:
        return
    budget = _require_dict(raw_budget, "turn_budget")
    if "target_planned_turns" in budget:
        _require_int(budget.get("target_planned_turns"), "turn_budget.target_planned_turns", minimum=1)
    if "opening_turns" in budget:
        _require_int(budget.get("opening_turns"), "turn_budget.opening_turns", minimum=0)
    if "content_turns" in budget:
        _require_int(budget.get("content_turns"), "turn_budget.content_turns", minimum=0)
    if "planning_note" in budget:
        _require_text(budget.get("planning_note"), "turn_budget.planning_note")


def _focus_target_ids(data: dict[str, Any]) -> set[str]:
    raw_targets = data.get("focus_targets")
    if raw_targets is None:
        return set()
    targets = _require_list(raw_targets, "focus_targets", min_items=2)
    if len(targets) > 4:
        raise LiveEpisodePlanValidationError("focus_targets must contain at most 4 item(s)")
    target_ids: set[str] = set()
    for index, raw_target in enumerate(targets):
        target = _require_dict(raw_target, f"focus_targets[{index}]")
        target_id = _require_text(
            target.get("target_id"),
            f"focus_targets[{index}].target_id",
        )
        if target_id in target_ids:
            raise LiveEpisodePlanValidationError(
                f"focus_targets[{index}].target_id must be unique"
            )
        target_ids.add(target_id)
        _require_text(target.get("label"), f"focus_targets[{index}].label")
        _require_text(target.get("target_type"), f"focus_targets[{index}].target_type")
        _require_text(
            target.get("selection_reason"),
            f"focus_targets[{index}].selection_reason",
        )
        for angle_index, angle in enumerate(
            _require_list(
                target.get("analysis_angles"),
                f"focus_targets[{index}].analysis_angles",
                min_items=1,
            )
        ):
            _require_text(angle, f"focus_targets[{index}].analysis_angles[{angle_index}]")
        for axis_index, axis in enumerate(
            _require_list(
                target.get("recommendation_axes"),
                f"focus_targets[{index}].recommendation_axes",
                min_items=1,
            )
        ):
            _require_text(axis, f"focus_targets[{index}].recommendation_axes[{axis_index}]")
    return target_ids


def _claim_ledger_ids(data: dict[str, Any]) -> set[str]:
    raw_ledger = data.get("claim_ledger")
    if raw_ledger is None:
        return set()
    ledger = _require_dict(raw_ledger, "claim_ledger")
    raw_claims = ledger.get("semantic_claims", ledger.get("used_claims", []))
    claims = _require_list(raw_claims, "claim_ledger.semantic_claims")
    claim_ids: set[str] = set()
    for index, raw_claim in enumerate(claims):
        claim = _require_dict(raw_claim, f"claim_ledger.semantic_claims[{index}]")
        claim_id = _require_text(
            claim.get("claim_id"),
            f"claim_ledger.semantic_claims[{index}].claim_id",
        )
        if claim_id in claim_ids:
            raise LiveEpisodePlanValidationError(
                f"claim_ledger.semantic_claims[{index}].claim_id must be unique"
            )
        claim_ids.add(claim_id)
        _require_text(
            claim.get("meaning"),
            f"claim_ledger.semantic_claims[{index}].meaning",
        )
        if "ban_paraphrase" in claim:
            _require_bool(
                claim.get("ban_paraphrase"),
                f"claim_ledger.semantic_claims[{index}].ban_paraphrase",
            )
    return claim_ids


def _validate_claim_policy(policy: dict[str, Any], known_claim_ids: set[str], path: str) -> None:
    if not isinstance(policy, dict) or not policy:
        return
    if not known_claim_ids:
        raise LiveEpisodePlanValidationError(
            f"{path} requires claim_ledger.semantic_claims"
        )
    for key in ("new_claim_ids", "forbidden_claim_ids"):
        values = [
            _require_text(item, f"{path}.{key}[]")
            for item in _require_list(policy.get(key, []), f"{path}.{key}")
        ]
        missing_ids = sorted({item for item in values if item not in known_claim_ids})
        if missing_ids:
            raise LiveEpisodePlanValidationError(
                f"{path}.{key} contains unknown claim ids: {missing_ids}"
            )
    if "must_not_paraphrase_used_claims" in policy:
        _require_bool(
            policy.get("must_not_paraphrase_used_claims"),
            f"{path}.must_not_paraphrase_used_claims",
        )


def _validate_focus_policy(
    policy: dict[str, Any],
    known_target_ids: set[str],
    turn_type: str,
    path: str,
) -> None:
    if not isinstance(policy, dict) or not policy:
        if turn_type in {"focus_deep_dive", "focus_compare", "personal_recommendation"}:
            raise LiveEpisodePlanValidationError(f"{path} is required for {turn_type}")
        return
    if not known_target_ids:
        raise LiveEpisodePlanValidationError(f"{path} requires focus_targets")
    target_ids = [
        _require_text(item, f"{path}.target_ids[]")
        for item in _require_list(policy.get("target_ids"), f"{path}.target_ids", min_items=1)
    ]
    missing_ids = sorted({item for item in target_ids if item not in known_target_ids})
    if missing_ids:
        raise LiveEpisodePlanValidationError(
            f"{path}.target_ids contains unknown focus target ids: {missing_ids}"
        )
    _require_text(policy.get("depth_goal"), f"{path}.depth_goal")
    min_must_cover = 3 if turn_type in {"focus_deep_dive", "personal_recommendation"} else 2
    must_cover = _require_list(
        policy.get("must_cover"),
        f"{path}.must_cover",
        min_items=min_must_cover,
    )
    if len(must_cover) > 4:
        raise LiveEpisodePlanValidationError(f"{path}.must_cover must contain at most 4 item(s)")
    for index, item in enumerate(must_cover):
        _require_text(item, f"{path}.must_cover[{index}]")
    _require_bool(policy.get("avoid_generic_reframe"), f"{path}.avoid_generic_reframe")
    recommendation_mode = _require_text(
        policy.get("recommendation_mode"),
        f"{path}.recommendation_mode",
    )
    if recommendation_mode not in ALLOWED_RECOMMENDATION_MODES:
        allowed = ", ".join(sorted(ALLOWED_RECOMMENDATION_MODES))
        raise LiveEpisodePlanValidationError(
            f"{path}.recommendation_mode must be one of: {allowed}"
        )


def _validate_recommendation_policy(
    policy: dict[str, Any],
    focus_policy: dict[str, Any],
    known_target_ids: set[str],
    turn_type: str,
    path: str,
) -> None:
    if not isinstance(policy, dict) or not policy:
        if turn_type == "personal_recommendation":
            raise LiveEpisodePlanValidationError(
                f"{path} is required for personal_recommendation"
            )
        return
    _require_text(policy.get("recommendation_style"), f"{path}.recommendation_style")
    recommendations = _require_list(
        policy.get("recommendations"),
        f"{path}.recommendations",
        min_items=1,
    )
    recommended_ids: set[str] = set()
    for index, raw_recommendation in enumerate(recommendations):
        recommendation = _require_dict(raw_recommendation, f"{path}.recommendations[{index}]")
        target_id = _require_text(
            recommendation.get("target_id"),
            f"{path}.recommendations[{index}].target_id",
        )
        if target_id not in known_target_ids:
            raise LiveEpisodePlanValidationError(
                f"{path}.recommendations[{index}].target_id contains unknown focus target id: {target_id}"
            )
        recommended_ids.add(target_id)
        _require_text(
            recommendation.get("best_for"),
            f"{path}.recommendations[{index}].best_for",
        )
        _require_text(
            recommendation.get("why"),
            f"{path}.recommendations[{index}].why",
        )
        _require_text(
            recommendation.get("avoid_if"),
            f"{path}.recommendations[{index}].avoid_if",
        )
        if "personal_bias" in recommendation:
            _require_text(
                recommendation.get("personal_bias"),
                f"{path}.recommendations[{index}].personal_bias",
            )
    ranked_order = [
        _require_text(item, f"{path}.ranked_order[]")
        for item in _require_list(policy.get("ranked_order", []), f"{path}.ranked_order")
    ]
    missing_ranked_ids = sorted({item for item in ranked_order if item not in known_target_ids})
    if missing_ranked_ids:
        raise LiveEpisodePlanValidationError(
            f"{path}.ranked_order contains unknown focus target ids: {missing_ranked_ids}"
        )
    if turn_type == "personal_recommendation":
        focus_target_ids = {
            _require_text(item, f"{path}.focus_policy.target_ids[]")
            for item in _require_list(
                focus_policy.get("target_ids", []),
                f"{path}.focus_policy.target_ids",
                min_items=1,
            )
        }
        missing_recommendations = sorted(focus_target_ids - recommended_ids)
        if missing_recommendations:
            raise LiveEpisodePlanValidationError(
                f"{path}.recommendations missing focus target ids: {missing_recommendations}"
            )


def _validate_stance_policy(policy: dict[str, Any], turn_type: str, path: str) -> None:
    if not isinstance(policy, dict) or not policy:
        if turn_type == "personal_recommendation":
            raise LiveEpisodePlanValidationError(
                f"{path} is required for personal_recommendation"
            )
        return
    stance_mode = _require_text(policy.get("stance_mode"), f"{path}.stance_mode")
    if stance_mode not in ALLOWED_STANCE_MODES:
        allowed = ", ".join(sorted(ALLOWED_STANCE_MODES))
        raise LiveEpisodePlanValidationError(f"{path}.stance_mode must be one of: {allowed}")
    must_take_side = _require_bool(policy.get("must_take_side"), f"{path}.must_take_side")
    disclaimer_budget = _require_int(
        policy.get("disclaimer_budget"),
        f"{path}.disclaimer_budget",
        minimum=0,
    )
    if disclaimer_budget > 1:
        raise LiveEpisodePlanValidationError(f"{path}.disclaimer_budget must be <= 1")
    for index, phrase in enumerate(
        _require_list(
            policy.get("avoid_disclaimer_phrases"),
            f"{path}.avoid_disclaimer_phrases",
            min_items=1,
        )
    ):
        _require_text(phrase, f"{path}.avoid_disclaimer_phrases[{index}]")
    _require_text(policy.get("edge_instruction"), f"{path}.edge_instruction")
    if turn_type == "personal_recommendation":
        if not must_take_side:
            raise LiveEpisodePlanValidationError(
                f"{path}.must_take_side must be true for personal_recommendation"
            )
        if disclaimer_budget != 0:
            raise LiveEpisodePlanValidationError(
                f"{path}.disclaimer_budget must be 0 for personal_recommendation"
            )


def _validate_segment_rhythm_control(raw_control: Any, path: str) -> None:
    if raw_control is None:
        return
    control = _require_dict(raw_control, path)
    _require_text(control.get("discussion_goal"), f"{path}.discussion_goal")
    for index, item in enumerate(
        _require_list(control.get("data_points"), f"{path}.data_points", min_items=1)
    ):
        _require_text(item, f"{path}.data_points[{index}]")
    _require_text(
        control.get("audience_understanding"),
        f"{path}.audience_understanding",
    )
    for index, item in enumerate(
        _require_list(control.get("close_when"), f"{path}.close_when", min_items=1)
    ):
        _require_text(item, f"{path}.close_when[{index}]")


def dialogue_policy_for_turn(turn: dict[str, Any]) -> dict[str, Any]:
    turn_type = str(turn.get("turn_type") or "").strip()
    min_default, max_default = DEFAULT_DIALOGUE_REPLY_BOUNDS.get(turn_type, (1, 2))
    raw = turn.get("dialogue_policy")
    if raw is None:
        return {
            "min_replies": min_default,
            "max_replies": max_default,
            "autonomy": "guided",
            "preferred_flow": [],
        }
    policy = _require_dict(raw, "dialogue_policy")
    min_replies = _require_int(
        policy.get("min_replies", min_default),
        "dialogue_policy.min_replies",
        minimum=1,
    )
    max_replies = _require_int(
        policy.get("max_replies", max_default),
        "dialogue_policy.max_replies",
        minimum=1,
    )
    if max_replies < min_replies:
        raise LiveEpisodePlanValidationError("dialogue reply bounds are invalid")
    if max_replies > 4:
        raise LiveEpisodePlanValidationError("dialogue_policy.max_replies must be <= 4")
    autonomy = str(policy.get("autonomy") or "guided").strip()
    if autonomy not in ALLOWED_DIALOGUE_AUTONOMY:
        raise LiveEpisodePlanValidationError("dialogue_policy.autonomy must be strict, guided, or open")
    preferred_flow = [
        str(item).strip()
        for item in _require_list(policy.get("preferred_flow", []), "dialogue_policy.preferred_flow")
        if str(item).strip()
    ]
    return {
        "min_replies": min_replies,
        "max_replies": max_replies,
        "autonomy": autonomy,
        "preferred_flow": preferred_flow[:6],
    }


def _validate_completion_conditions(
    completion: dict[str, Any],
    turn_types: set[str],
    path: str,
) -> None:
    if "required_takeaways" in completion:
        raise LiveEpisodePlanValidationError(
            f"{path}.required_takeaways is not a runtime condition"
        )
    min_turns = _require_int(completion.get("min_planned_turns"), f"{path}.min_planned_turns", minimum=1)
    max_turns = _require_int(completion.get("max_planned_turns"), f"{path}.max_planned_turns", minimum=1)
    if max_turns < min_turns:
        raise LiveEpisodePlanValidationError(f"{path} planned turn bounds are invalid")
    required_types = {
        _require_text(item, f"{path}.required_turn_types[]")
        for item in _require_list(
            completion.get("required_turn_types"),
            f"{path}.required_turn_types",
            min_items=1,
        )
    }
    _require_list(
        completion.get("optional_turn_types"),
        f"{path}.optional_turn_types",
    )
    missing_required = sorted(required_types - turn_types)
    if missing_required:
        raise LiveEpisodePlanValidationError(
            f"{path}.required_turn_types missing turn contracts: {missing_required}"
        )


def validate_live_episode_plan(plan: dict[str, Any]) -> dict[str, Any]:
    data = copy.deepcopy(_require_dict(plan, "plan"))
    if data.get("schema_version") != "live_episode_plan.v1":
        raise LiveEpisodePlanValidationError("schema_version must be live_episode_plan.v1")

    _require_text(data.get("plan_id"), "plan_id")
    _require_text(data.get("title"), "title")

    show_format = _require_dict(data.get("show_format"), "show_format")
    _require_text(show_format.get("primary"), "show_format.primary")
    _require_text(show_format.get("format_notes"), "show_format.format_notes")

    flow_policy = _require_dict(data.get("flow_policy"), "flow_policy")
    if flow_policy.get("segment_order") != "locked":
        raise LiveEpisodePlanValidationError("flow_policy.segment_order must be locked")
    if flow_policy.get("audience_can_change_segment_order") is not False:
        raise LiveEpisodePlanValidationError(
            "flow_policy.audience_can_change_segment_order must be false"
        )

    _validate_classifier(
        _require_dict(data.get("audience_event_classifier"), "audience_event_classifier")
    )
    _validate_turn_budget(data)
    focus_target_ids = _focus_target_ids(data)
    claim_ids = _claim_ledger_ids(data)
    participant_ids = _participant_ids(
        _require_list(data.get("participants"), "participants", min_items=1)
    )

    segments = _require_list(data.get("segments"), "segments", min_items=1)
    for segment_index, segment in enumerate(segments):
        segment_path = f"segments[{segment_index}]"
        segment_obj = _require_dict(segment, segment_path)
        _require_text(segment_obj.get("segment_id"), f"{segment_path}.segment_id")
        _validate_segment_rhythm_control(
            segment_obj.get("rhythm_control"),
            f"{segment_path}.rhythm_control",
        )
        turns = _require_list(
            segment_obj.get("planned_turn_contracts"),
            f"{segment_path}.planned_turn_contracts",
            min_items=1,
        )
        turn_types: set[str] = set()
        for turn_index, turn in enumerate(turns):
            turn_path = f"{segment_path}.planned_turn_contracts[{turn_index}]"
            turn_obj = _require_dict(turn, turn_path)
            _require_text(turn_obj.get("turn_id"), f"{turn_path}.turn_id")
            turn_type = _require_text(turn_obj.get("turn_type"), f"{turn_path}.turn_type")
            turn_types.add(turn_type)
            _require_text(turn_obj.get("intent"), f"{turn_path}.intent")
            try:
                turn_obj["dialogue_policy"] = dialogue_policy_for_turn(turn_obj)
            except LiveEpisodePlanValidationError as exc:
                raise LiveEpisodePlanValidationError(f"{turn_path}.{exc}") from exc
            _validate_speaker_policy(
                _require_dict(turn_obj.get("speaker_policy"), f"{turn_path}.speaker_policy"),
                participant_ids,
                f"{turn_path}.speaker_policy",
            )
            _validate_evidence_policy(
                _require_dict(turn_obj.get("evidence_policy"), f"{turn_path}.evidence_policy"),
                f"{turn_path}.evidence_policy",
            )
            _validate_evidence_brief(
                turn_obj.get("evidence_brief"),
                f"{turn_path}.evidence_brief",
            )
            _require_dict(
                turn_obj.get("forbidden_repetition"),
                f"{turn_path}.forbidden_repetition",
            )
            _validate_output_requirements(
                _require_dict(
                    turn_obj.get("output_requirements"),
                    f"{turn_path}.output_requirements",
                ),
                f"{turn_path}.output_requirements",
            )
            _validate_audience_event_policy(
                turn_obj.get("audience_event_policy") if isinstance(turn_obj.get("audience_event_policy"), dict) else {},
                f"{turn_path}.audience_event_policy",
            )
            _validate_claim_policy(
                turn_obj.get("claim_policy") if isinstance(turn_obj.get("claim_policy"), dict) else {},
                claim_ids,
                f"{turn_path}.claim_policy",
            )
            _validate_focus_policy(
                turn_obj.get("focus_policy") if isinstance(turn_obj.get("focus_policy"), dict) else {},
                focus_target_ids,
                turn_type,
                f"{turn_path}.focus_policy",
            )
            _validate_recommendation_policy(
                turn_obj.get("recommendation_policy") if isinstance(turn_obj.get("recommendation_policy"), dict) else {},
                turn_obj.get("focus_policy") if isinstance(turn_obj.get("focus_policy"), dict) else {},
                focus_target_ids,
                turn_type,
                f"{turn_path}.recommendation_policy",
            )
            _validate_stance_policy(
                turn_obj.get("stance_policy") if isinstance(turn_obj.get("stance_policy"), dict) else {},
                turn_type,
                f"{turn_path}.stance_policy",
            )
        _validate_completion_conditions(
            _require_dict(
                segment_obj.get("completion_conditions"),
                f"{segment_path}.completion_conditions",
            ),
            turn_types,
            f"{segment_path}.completion_conditions",
        )
    return data


def initial_planned_state(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "plan_id": _require_text(plan.get("plan_id"), "plan_id"),
        "plan_status": "running",
        "current_segment_index": 0,
        "current_turn_index": 0,
        "completed_segment_ids": [],
        "completed_turn_ids": [],
        "completed_turn_types": [],
        "segment_memory": copy.deepcopy(SEGMENT_MEMORY_TEMPLATE),
    }


def initial_segment_memory() -> dict[str, Any]:
    return copy.deepcopy(SEGMENT_MEMORY_TEMPLATE)


def current_segment(
    plan: dict[str, Any],
    planned_state: dict[str, Any],
) -> dict[str, Any] | None:
    segments = plan.get("segments")
    if not isinstance(segments, list):
        return None
    segment_index = int(planned_state.get("current_segment_index") or 0)
    if segment_index < 0 or segment_index >= len(segments):
        return None
    segment = segments[segment_index]
    return segment if isinstance(segment, dict) else None


def current_turn_contract(
    plan: dict[str, Any],
    planned_state: dict[str, Any],
) -> dict[str, Any] | None:
    if str(planned_state.get("plan_status") or "") == "completed":
        return None
    segment = current_segment(plan, planned_state)
    if not segment:
        return None
    turn_index = int(planned_state.get("current_turn_index") or 0)
    turns = segment.get("planned_turn_contracts")
    if not isinstance(turns, list) or turn_index < 0 or turn_index >= len(turns):
        return None
    turn = turns[turn_index]
    return turn if isinstance(turn, dict) else None
