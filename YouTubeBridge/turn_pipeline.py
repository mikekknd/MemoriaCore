"""Prepared live turn policy helpers."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Protocol


@dataclass(frozen=True)
class PreparedTurnPolicy:
    expected_status: str
    presentation_source: str
    may_chain: bool
    mark_audience_events_injected: bool
    dedicated_closing: bool = False


@dataclass(frozen=True)
class PreparedTurnPayload:
    interaction: dict[str, Any]
    memoria_result: dict[str, Any]
    prepared_results: list[dict[str, Any]]
    decision: dict[str, Any]
    base_state: dict[str, Any]


@dataclass(frozen=True)
class PreparedTurnFollowupGate:
    requested: bool = False
    runtime_stopping: bool = False
    graceful_closing: bool = False
    prefetch_in_flight: bool = False


@dataclass(frozen=True)
class PreparedTurnConsumeOptions:
    session_id: str
    allow_followup_prefetch: bool = False
    followup_allow_audience: bool = False
    followup_gate: PreparedTurnFollowupGate | None = None
    expected_dedicated_closing: bool = False
    require_complete_prepared_items: bool = True
    completion_metadata_key: str = "prepared_turn_consumed"
    started_event_type: str = "interaction_started"
    completed_event_type: str = "interaction_completed"


@dataclass(frozen=True)
class PreparedTurnConsumeResult:
    consumed: bool
    reason: str
    payload: PreparedTurnPayload
    interaction: dict[str, Any] | None
    after_memoria_task: Any = None
    played_item_count: int = 0
    marked_injected: int = 0
    followup_skip_reason: str = "not_requested"


def prepared_turn_followup_skip_reason(
    *,
    requested: bool,
    has_decision: bool,
    has_base_state: bool,
    runtime_stopping: bool,
    graceful_closing: bool,
    prefetch_in_flight: bool,
) -> str:
    if not requested:
        return "not_requested"
    if not has_decision:
        return "missing_decision"
    if not has_base_state:
        return "missing_base_state"
    if runtime_stopping:
        return "runtime_stopping"
    if graceful_closing:
        return "graceful_closing"
    if prefetch_in_flight:
        return "prefetch_in_flight"
    return ""


class PreparedTurnConsumeAdapter(Protocol):
    def get_interaction(self, job_id: str) -> dict[str, Any] | None:
        ...

    def prepared_results_for_interaction(
        self,
        interaction: dict[str, Any],
        *,
        require_complete: bool,
    ) -> list[dict[str, Any]]:
        ...

    def claim_interaction(
        self,
        job_id: str,
        expected_status: str,
    ) -> dict[str, Any] | None:
        ...

    async def broadcast(self, payload: dict[str, Any]) -> None:
        ...

    async def present_prepared_results(
        self,
        prepared_results: list[dict[str, Any]],
        *,
        source: str,
        interaction_job_id: str,
    ) -> Any:
        ...

    def visible_prepared_results(
        self,
        prepared_results: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        ...

    def prepared_result_item_count(self, prepared_results: list[dict[str, Any]]) -> int:
        ...

    def mark_audience_events_injected(self, interaction: dict[str, Any]) -> int:
        ...

    def complete_interaction(
        self,
        job_id: str,
        *,
        reply_text: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any] | None:
        ...

    async def schedule_followup_prefetch(
        self,
        payload: PreparedTurnPayload,
        *,
        allow_audience: bool,
    ) -> Any:
        ...


def _decision_action(interaction: dict[str, Any]) -> str:
    metadata = interaction.get("metadata") if isinstance(interaction.get("metadata"), dict) else {}
    decision = metadata.get("decision") if isinstance(metadata.get("decision"), dict) else {}
    return str(decision.get("action") or "")


def prepared_turn_policy_for_interaction(
    interaction: dict[str, Any] | None,
) -> PreparedTurnPolicy | None:
    if not isinstance(interaction, dict):
        return None
    source = str(interaction.get("source") or "")
    action = _decision_action(interaction)
    if source == "director_audience_prepare":
        return PreparedTurnPolicy(
            expected_status="prepared",
            presentation_source="director_audience_gap",
            may_chain=False,
            mark_audience_events_injected=True,
        )
    if source != "director_prefetch":
        return None
    if action == "final_closing":
        return PreparedTurnPolicy(
            expected_status="prefetched",
            presentation_source="director_closing",
            may_chain=False,
            mark_audience_events_injected=False,
            dedicated_closing=True,
        )
    if action == "closing_super_chat_thanks":
        return PreparedTurnPolicy(
            expected_status="prefetched",
            presentation_source="director_super_chat",
            may_chain=False,
            mark_audience_events_injected=False,
            dedicated_closing=True,
        )
    return PreparedTurnPolicy(
        expected_status="prefetched",
        presentation_source="director",
        may_chain=True,
        mark_audience_events_injected=False,
    )


def _consume_refused(
    reason: str,
    payload: PreparedTurnPayload,
    interaction: dict[str, Any] | None,
) -> PreparedTurnConsumeResult:
    return PreparedTurnConsumeResult(
        consumed=False,
        reason=reason,
        payload=payload,
        interaction=interaction,
    )


def _reply_text_from_payload(payload: PreparedTurnPayload) -> str:
    reply = payload.memoria_result.get("reply")
    if reply is not None:
        return str(reply)
    for prepared in payload.prepared_results:
        message = prepared.get("message") if isinstance(prepared.get("message"), dict) else {}
        content = message.get("content")
        if content is not None:
            return str(content)
    return ""


def _followup_skip_reason_for_consume(
    *,
    options: PreparedTurnConsumeOptions,
    payload: PreparedTurnPayload,
    policy: PreparedTurnPolicy,
) -> str:
    if options.followup_gate is not None:
        requested = options.followup_gate.requested
        runtime_stopping = options.followup_gate.runtime_stopping
        graceful_closing = options.followup_gate.graceful_closing
        prefetch_in_flight = options.followup_gate.prefetch_in_flight
    else:
        requested = options.allow_followup_prefetch
        runtime_stopping = False
        graceful_closing = False
        prefetch_in_flight = False
    reason = prepared_turn_followup_skip_reason(
        requested=requested,
        has_decision=bool(payload.decision),
        has_base_state=bool(payload.base_state),
        runtime_stopping=runtime_stopping,
        graceful_closing=graceful_closing,
        prefetch_in_flight=prefetch_in_flight,
    )
    if reason:
        return reason
    if not (policy.may_chain or policy.mark_audience_events_injected):
        return "policy_disallows_followup"
    return ""


async def consume_prepared_turn(
    adapter: PreparedTurnConsumeAdapter,
    payload: PreparedTurnPayload,
    options: PreparedTurnConsumeOptions,
) -> PreparedTurnConsumeResult:
    job_id = str(payload.interaction.get("job_id") or "")
    if not job_id:
        return _consume_refused("missing_job_id", payload, None)

    current_interaction = adapter.get_interaction(job_id)
    if current_interaction is None:
        return _consume_refused("interaction_missing", payload, None)

    policy = prepared_turn_policy_for_interaction(current_interaction)
    if policy is None:
        return _consume_refused("policy_missing", payload, current_interaction)
    if policy.dedicated_closing and not options.expected_dedicated_closing:
        return _consume_refused("dedicated_closing_unexpected", payload, current_interaction)
    if options.expected_dedicated_closing and not policy.dedicated_closing:
        return _consume_refused("dedicated_closing_expected", payload, current_interaction)
    if str(current_interaction.get("status") or "") != policy.expected_status:
        return _consume_refused("unexpected_status", payload, current_interaction)

    prepared_results = list(payload.prepared_results or [])
    if not prepared_results:
        prepared_results = adapter.prepared_results_for_interaction(
            current_interaction,
            require_complete=options.require_complete_prepared_items,
        )
    if not prepared_results:
        return _consume_refused("missing_prepared_results", payload, current_interaction)

    claimed_interaction = adapter.claim_interaction(job_id, policy.expected_status)
    if claimed_interaction is None or str(claimed_interaction.get("status") or "") != "presenting":
        return _consume_refused(
            "presenting_claim_failed",
            payload,
            claimed_interaction,
        )

    claimed_payload = replace(
        payload,
        interaction=claimed_interaction,
        prepared_results=prepared_results,
    )
    await adapter.broadcast(
        {
            "type": options.started_event_type,
            "session_id": options.session_id,
            "interaction": claimed_interaction,
        }
    )

    after_memoria_task = None
    followup_skip_reason = _followup_skip_reason_for_consume(
        options=options,
        payload=claimed_payload,
        policy=policy,
    )
    if not followup_skip_reason:
        followup_allow_audience = (
            False
            if policy.mark_audience_events_injected
            else options.followup_allow_audience
        )
        after_memoria_task = await adapter.schedule_followup_prefetch(
            claimed_payload,
            allow_audience=followup_allow_audience,
        )

    await adapter.present_prepared_results(
        prepared_results,
        source=policy.presentation_source,
        interaction_job_id=job_id,
    )

    visible_results = adapter.visible_prepared_results(prepared_results)
    played_item_count = adapter.prepared_result_item_count(visible_results)
    marked_injected = 0
    if policy.mark_audience_events_injected and played_item_count > 0:
        marked_injected = adapter.mark_audience_events_injected(claimed_interaction)

    metadata = dict(
        claimed_interaction.get("metadata")
        if isinstance(claimed_interaction.get("metadata"), dict)
        else {}
    )
    if options.completion_metadata_key:
        metadata[options.completion_metadata_key] = True
    metadata["played_item_count"] = played_item_count
    metadata["marked_injected"] = marked_injected
    completed_interaction = adapter.complete_interaction(
        job_id,
        reply_text=_reply_text_from_payload(claimed_payload),
        metadata=metadata,
    )
    if completed_interaction is None or str(completed_interaction.get("status") or "") != "completed":
        return _consume_refused(
            "complete_failed",
            claimed_payload,
            completed_interaction,
        )

    completed_payload = replace(
        claimed_payload,
        interaction=completed_interaction,
    )

    await adapter.broadcast(
        {
            "type": options.completed_event_type,
            "session_id": options.session_id,
            "interaction": completed_interaction,
            "played_item_count": played_item_count,
            "marked_injected": marked_injected,
        }
    )
    return PreparedTurnConsumeResult(
        consumed=True,
        reason="consumed",
        payload=completed_payload,
        interaction=completed_interaction,
        after_memoria_task=after_memoria_task,
        played_item_count=played_item_count,
        marked_injected=marked_injected,
        followup_skip_reason=followup_skip_reason,
    )
