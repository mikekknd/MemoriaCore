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
    "chat_bridge": (2, 3),
    "audience_answer": (2, 3),
    "closing": (1, 2),
    "final_closing": (2, 2),
}

ALLOWED_DIALOGUE_AUTONOMY = {"strict", "guided", "open"}
ALLOWED_AUDIENCE_EMPTY_BEHAVIOR = {"fallback_without_audience_quotes"}


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
    claim_ids = _claim_ledger_ids(data)
    participant_ids = _participant_ids(
        _require_list(data.get("participants"), "participants", min_items=1)
    )

    segments = _require_list(data.get("segments"), "segments", min_items=1)
    for segment_index, segment in enumerate(segments):
        segment_path = f"segments[{segment_index}]"
        segment_obj = _require_dict(segment, segment_path)
        _require_text(segment_obj.get("segment_id"), f"{segment_path}.segment_id")
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
            turn_types.add(_require_text(turn_obj.get("turn_type"), f"{turn_path}.turn_type"))
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
