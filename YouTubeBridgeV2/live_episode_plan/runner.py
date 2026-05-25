"""Pure LiveEpisodePlan runner contracts for YouTubeBridgeV2.

此模組只負責驗證 LiveEpisodePlan、建立 planned turn intent、推進
cursor 與輸出 completion signal。它不產生 prompt，不呼叫 adapter，
也不寫入 storage。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any


class PlanExecutionStatus(str, Enum):
    """LiveEpisodePlan runner 對目前計畫狀態的判斷."""

    NOT_STARTED = "not_started"
    RUNNING = "running"
    COMPLETED = "completed"
    INVALID = "invalid"


@dataclass(frozen=True)
class LiveEpisodePlanContract:
    """已驗證或驗證失敗的 LiveEpisodePlan contract summary."""

    plan_id: str
    title: str
    turns: tuple[dict[str, object], ...]
    status: PlanExecutionStatus
    validation_errors: tuple[str, ...] = ()
    public_summary: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class LiveEpisodePlanState:
    """Runner cursor state，由 application/storage 層保存與提交."""

    contract: LiveEpisodePlanContract
    cursor: int = 0
    completed_turn_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlannedTurnIntent:
    """交給後續 adapter 的 planned show 執行意圖."""

    plan_id: str
    turn_id: str
    turn_index: int
    purpose: str
    speaker_policy: str
    speaker_ids: tuple[str, ...]
    topic_cue: str
    audience_summary: dict[str, object] | None = None
    audience_handling_hint: str = "audience_not_provided"
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class PlanCompletionSignal:
    """交給 Runtime Phase 的 completion input."""

    completed: bool
    completed_turn_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlannedTurnResult:
    """單一 planned turn 的 intent/result summary."""

    status: PlanExecutionStatus
    intent: PlannedTurnIntent | None
    next_state: LiveEpisodePlanState
    completion_signal: PlanCompletionSignal
    redacted_turn_summary: dict[str, object] = field(default_factory=dict)
    validation_errors: tuple[str, ...] = ()
    skipped_audience_reason: str | None = None


def validate_episode_plan_contract(plan: dict[str, object]) -> LiveEpisodePlanContract:
    """驗證 LiveEpisodePlan dict 並回傳不含 raw Topic Pack 的 contract summary."""

    errors: list[str] = []
    plan_id = _string_field(plan, "plan_id")
    title = _string_field(plan, "title")
    raw_turns = plan.get("turns")

    if not plan_id:
        errors.append("plan_id is required")
    if not title:
        errors.append("title is required")
    if not isinstance(raw_turns, list) or not raw_turns:
        errors.append("turns must be a non-empty list")

    turns: list[dict[str, object]] = []
    if isinstance(raw_turns, list):
        for index, raw_turn in enumerate(raw_turns):
            if not isinstance(raw_turn, dict):
                errors.append(f"turns[{index}] must be an object")
                continue
            normalized = _normalize_turn(raw_turn, index, errors)
            if normalized is not None:
                turns.append(normalized)

    status = PlanExecutionStatus.INVALID if errors else PlanExecutionStatus.RUNNING
    return LiveEpisodePlanContract(
        plan_id=plan_id,
        title=title,
        turns=tuple(turns),
        status=status,
        validation_errors=tuple(errors),
        public_summary={
            "plan_id": plan_id,
            "title": title,
            "turn_count": len(turns),
        },
    )


def next_planned_turn(
    plan_state: LiveEpisodePlanState,
    audience_event_summary: dict[str, object] | None = None,
) -> PlannedTurnResult:
    """依目前 cursor 產生下一個 planned turn intent."""

    contract = plan_state.contract
    if contract.status is PlanExecutionStatus.INVALID:
        return _invalid_result(plan_state, contract.validation_errors)

    cursor_error = _cursor_validation_error(plan_state.cursor, len(contract.turns))
    if cursor_error is not None:
        return _invalid_result(plan_state, (cursor_error,))

    if plan_state.cursor == len(contract.turns):
        completed_ids = _completed_turn_ids(plan_state)
        return PlannedTurnResult(
            status=PlanExecutionStatus.COMPLETED,
            intent=None,
            next_state=plan_state,
            completion_signal=PlanCompletionSignal(
                completed=True,
                completed_turn_ids=completed_ids,
            ),
        )

    turn = contract.turns[plan_state.cursor]
    audience_summary, audience_hint, skipped_reason = _audience_output(
        turn,
        audience_event_summary,
    )
    intent = PlannedTurnIntent(
        plan_id=contract.plan_id,
        turn_id=str(turn["id"]),
        turn_index=plan_state.cursor,
        purpose=str(turn["purpose"]),
        speaker_policy=str(turn["speaker_policy"]),
        speaker_ids=tuple(str(speaker_id) for speaker_id in turn["speaker_ids"]),
        topic_cue=str(turn["topic_cue"]),
        audience_summary=audience_summary,
        audience_handling_hint=audience_hint,
        metadata={
            "turn_count": len(contract.turns),
            "audience_insertion_enabled": bool(turn["audience_insertion_enabled"]),
        },
    )
    return PlannedTurnResult(
        status=PlanExecutionStatus.RUNNING,
        intent=intent,
        next_state=plan_state,
        completion_signal=PlanCompletionSignal(
            completed=False,
            completed_turn_ids=plan_state.completed_turn_ids,
        ),
        redacted_turn_summary=_turn_summary(intent, completed=False),
        skipped_audience_reason=skipped_reason,
    )


def record_planned_turn_result(
    plan_state: LiveEpisodePlanState,
    turn_result: PlannedTurnResult,
) -> PlannedTurnResult:
    """記錄 planned turn 完成結果並推進 cursor."""

    if turn_result.intent is None:
        return turn_result

    turn_id = turn_result.intent.turn_id
    completed_turn_ids = _append_once(plan_state.completed_turn_ids, turn_id)
    next_cursor = plan_state.cursor + 1
    completed = next_cursor >= len(plan_state.contract.turns)
    next_state = replace(
        plan_state,
        cursor=next_cursor,
        completed_turn_ids=completed_turn_ids,
    )
    status = PlanExecutionStatus.COMPLETED if completed else PlanExecutionStatus.RUNNING
    return replace(
        turn_result,
        status=status,
        next_state=next_state,
        completion_signal=PlanCompletionSignal(
            completed=completed,
            completed_turn_ids=completed_turn_ids,
        ),
        redacted_turn_summary=_turn_summary(turn_result.intent, completed=True),
    )


def _normalize_turn(
    raw_turn: dict[str, object],
    index: int,
    errors: list[str],
) -> dict[str, object] | None:
    turn_id = _string_field(raw_turn, "id")
    purpose = _string_field(raw_turn, "purpose")
    topic_cue = _string_field(raw_turn, "topic_cue")
    speaker_policy = raw_turn.get("speaker_policy")
    audience_policy = raw_turn.get("audience_insertion", {})

    if not turn_id:
        errors.append(f"turns[{index}].id is required")
    if not purpose:
        errors.append(f"turns[{index}].purpose is required")
    if not topic_cue:
        errors.append(f"turns[{index}].topic_cue is required")
    if not isinstance(speaker_policy, dict):
        errors.append(f"turns[{index}].speaker_policy is required")
        return None

    speaker_policy_type = _string_field(speaker_policy, "type") or "fixed"
    speaker_ids = speaker_policy.get("speaker_ids")
    if not isinstance(speaker_ids, list) or not speaker_ids:
        errors.append(f"turns[{index}].speaker_policy.speaker_ids is required")
        return None

    if audience_policy is None:
        audience_policy = {}
    if not isinstance(audience_policy, dict):
        errors.append(f"turns[{index}].audience_insertion must be an object")
        return None

    if not turn_id or not purpose or not topic_cue:
        return None

    return {
        "id": turn_id,
        "purpose": purpose,
        "topic_cue": topic_cue,
        "speaker_policy": speaker_policy_type,
        "speaker_ids": tuple(str(speaker_id) for speaker_id in speaker_ids),
        "audience_insertion_enabled": bool(audience_policy.get("enabled", False)),
        "allow_super_chats": bool(audience_policy.get("allow_super_chats", False)),
    }


def _audience_output(
    turn: dict[str, object],
    audience_event_summary: dict[str, object] | None,
) -> tuple[dict[str, object] | None, str, str | None]:
    if not audience_event_summary:
        return None, "audience_not_provided", None

    if not bool(turn["audience_insertion_enabled"]):
        return None, "audience_insertion_disabled", "turn_policy_disallows_audience"

    event_type = str(audience_event_summary.get("type", "message"))
    if event_type == "super_chat" and not bool(turn["allow_super_chats"]):
        return None, "super_chat_not_allowed", "turn_policy_disallows_super_chat"

    return (
        _sanitize_audience_summary(audience_event_summary),
        "audience_summary_allowed",
        None,
    )


def _sanitize_audience_summary(summary: dict[str, object]) -> dict[str, object]:
    allowed_keys = {
        "type",
        "display_text",
        "amount_micros",
        "currency",
        "author_display_name",
    }
    return {key: value for key, value in summary.items() if key in allowed_keys}


def _turn_summary(intent: PlannedTurnIntent, *, completed: bool) -> dict[str, object]:
    return {
        "turn_id": intent.turn_id,
        "turn_index": intent.turn_index,
        "completed": completed,
    }


def _invalid_result(
    plan_state: LiveEpisodePlanState,
    validation_errors: tuple[str, ...],
) -> PlannedTurnResult:
    return PlannedTurnResult(
        status=PlanExecutionStatus.INVALID,
        intent=None,
        next_state=plan_state,
        completion_signal=PlanCompletionSignal(completed=False),
        validation_errors=validation_errors,
    )


def _completed_turn_ids(plan_state: LiveEpisodePlanState) -> tuple[str, ...]:
    if plan_state.completed_turn_ids:
        return plan_state.completed_turn_ids
    return tuple(str(turn["id"]) for turn in plan_state.contract.turns)


def _append_once(items: tuple[str, ...], item: str) -> tuple[str, ...]:
    if item in items:
        return items
    return (*items, item)


def _cursor_validation_error(cursor: int, turn_count: int) -> str | None:
    if not isinstance(cursor, int) or isinstance(cursor, bool):
        return "cursor must be an integer"
    if cursor < 0 or cursor > turn_count:
        return f"cursor out of range: {cursor}; expected 0..{turn_count}"
    return None


def _string_field(mapping: dict[str, object], key: str) -> str:
    value = mapping.get(key)
    if value is None:
        return ""
    return str(value).strip()


__all__ = [
    "LiveEpisodePlanContract",
    "LiveEpisodePlanState",
    "PlanCompletionSignal",
    "PlanExecutionStatus",
    "PlannedTurnIntent",
    "PlannedTurnResult",
    "next_planned_turn",
    "record_planned_turn_result",
    "validate_episode_plan_contract",
]
