"""LiveEpisodePlan contract validation and runtime projection helpers."""
from __future__ import annotations

import copy
from typing import Any


class LiveEpisodePlanValidationError(ValueError):
    """Raised when an imported LiveEpisodePlan cannot be executed by runtime."""


SEGMENT_MEMORY_TEMPLATE = {
    "covered_claims": [],
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
        "current_segment_index": 0,
        "current_turn_index": 0,
        "completed_segment_ids": [],
        "completed_turn_ids": [],
        "segment_memory": copy.deepcopy(SEGMENT_MEMORY_TEMPLATE),
    }


def current_turn_contract(
    plan: dict[str, Any],
    planned_state: dict[str, Any],
) -> dict[str, Any] | None:
    segments = plan.get("segments")
    if not isinstance(segments, list):
        return None
    segment_index = int(planned_state.get("current_segment_index") or 0)
    turn_index = int(planned_state.get("current_turn_index") or 0)
    if segment_index < 0 or segment_index >= len(segments):
        return None
    segment = segments[segment_index]
    if not isinstance(segment, dict):
        return None
    turns = segment.get("planned_turn_contracts")
    if not isinstance(turns, list) or turn_index < 0 or turn_index >= len(turns):
        return None
    turn = turns[turn_index]
    return turn if isinstance(turn, dict) else None
