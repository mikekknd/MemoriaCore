import sys
import asyncio
from pathlib import Path

BRIDGE_ROOT = Path(__file__).resolve().parents[1]
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))

from turn_pipeline import (
    PreparedTurnPolicy,
    PreparedTurnConsumeOptions,
    PreparedTurnPayload,
    consume_prepared_turn,
    prepared_turn_policy_for_interaction,
)


def _interaction(source, status, action=""):
    metadata = {}
    if action:
        metadata["decision"] = {"action": action}
    return {
        "job_id": "job-1",
        "source": source,
        "status": status,
        "metadata": metadata,
    }


def test_director_prefetch_policy_allows_chain_for_normal_turn():
    policy = prepared_turn_policy_for_interaction(
        _interaction("director_prefetch", "prefetched", "planned_turn")
    )

    assert policy.expected_status == "prefetched"
    assert policy.presentation_source == "director"
    assert policy.may_chain is True
    assert policy.mark_audience_events_injected is False
    assert policy.dedicated_closing is False


def test_audience_prepare_policy_marks_events_without_general_prefetch_chain():
    policy = prepared_turn_policy_for_interaction(
        _interaction("director_audience_prepare", "prepared", "audience_gap_prepare")
    )

    assert policy.expected_status == "prepared"
    assert policy.presentation_source == "director_audience_gap"
    assert policy.may_chain is False
    assert policy.mark_audience_events_injected is True
    assert policy.dedicated_closing is False


def test_final_closing_policy_is_prefetched_without_chain():
    policy = prepared_turn_policy_for_interaction(
        _interaction("director_prefetch", "prefetched", "final_closing")
    )

    assert policy.expected_status == "prefetched"
    assert policy.presentation_source == "director_closing"
    assert policy.may_chain is False
    assert policy.dedicated_closing is True


def test_closing_super_chat_policy_is_prefetched_without_chain():
    policy = prepared_turn_policy_for_interaction(
        _interaction("director_prefetch", "prefetched", "closing_super_chat_thanks")
    )

    assert policy.expected_status == "prefetched"
    assert policy.presentation_source == "director_super_chat"
    assert policy.may_chain is False
    assert policy.dedicated_closing is True


def test_unknown_source_has_no_policy():
    assert prepared_turn_policy_for_interaction(
        _interaction("director", "completed", "planned_turn")
    ) is None


class FakePreparedTurnAdapter:
    def __init__(
        self,
        interaction,
        prepared_results,
        *,
        claim_status="presenting",
        complete_status="completed",
        complete_returns_none=False,
    ):
        self.interaction = dict(interaction)
        self.prepared_results = list(prepared_results)
        self.events = []
        self.marked_event_ids = []
        self.followup_calls = []
        self.prepared_results_calls = 0
        self.claim_status = claim_status
        self.complete_status = complete_status
        self.complete_returns_none = complete_returns_none

    def get_interaction(self, job_id):
        if job_id == self.interaction["job_id"]:
            return dict(self.interaction)
        return None

    def prepared_results_for_interaction(self, interaction, *, require_complete):
        self.prepared_results_calls += 1
        return list(self.prepared_results)

    def claim_interaction(self, job_id, expected_status):
        assert job_id == self.interaction["job_id"]
        assert self.interaction["status"] == expected_status
        self.interaction["status"] = self.claim_status
        return dict(self.interaction)

    async def broadcast(self, payload):
        self.events.append(payload["type"])

    async def present_prepared_results(self, prepared_results, *, source, interaction_job_id):
        self.events.append(f"present:{source}")
        return []

    def visible_prepared_results(self, prepared_results):
        return list(prepared_results)

    def prepared_result_item_count(self, prepared_results):
        return sum(len(prepared.get("items") or []) for prepared in prepared_results)

    def mark_audience_events_injected(self, interaction):
        ids = [int(event_id) for event_id in interaction.get("event_ids") or []]
        self.marked_event_ids.extend(ids)
        return len(ids)

    def complete_interaction(self, job_id, *, reply_text, metadata):
        assert job_id == self.interaction["job_id"]
        if self.complete_returns_none:
            return None
        self.interaction["status"] = self.complete_status
        self.interaction["reply_text"] = reply_text
        self.interaction.setdefault("metadata", {}).update(metadata)
        return dict(self.interaction)

    async def schedule_followup_prefetch(self, payload, *, allow_audience):
        self.events.append(f"schedule:{allow_audience}")
        self.followup_calls.append({"payload": payload, "allow_audience": allow_audience})
        return "followup-task"


def _payload(interaction, *, decision=None, base_state=None, prepared_results=None):
    return PreparedTurnPayload(
        interaction=interaction,
        memoria_result={"session_id": "mem-main", "reply": "reply text"},
        prepared_results=prepared_results
        or [{"message": {"content": "reply text"}, "items": [{"item_id": "item-1"}]}],
        decision=decision
        or {"action": "continue_topic", "episode_plan": {"mode": "planned_turn"}},
        base_state=base_state or {"status": "running"},
    )


def test_consume_prepared_turn_claims_presents_completes_and_chains():
    interaction = {
        "job_id": "prefetch-1",
        "source": "director_prefetch",
        "status": "prefetched",
        "metadata": {"decision": {"action": "continue_topic"}},
    }
    payload = _payload(interaction)
    adapter = FakePreparedTurnAdapter(interaction, payload.prepared_results)

    result = asyncio.run(
        consume_prepared_turn(
            adapter,
            payload,
            PreparedTurnConsumeOptions(
                session_id="live-a",
                allow_followup_prefetch=True,
                followup_allow_audience=True,
                completion_metadata_key="prefetch_consumed",
                started_event_type="interaction_started",
                completed_event_type="interaction_completed",
            ),
        )
    )

    assert result.consumed is True
    assert result.interaction["status"] == "completed"
    assert result.interaction["metadata"]["prefetch_consumed"] is True
    assert result.interaction["metadata"]["played_item_count"] == 1
    assert result.interaction["metadata"]["marked_injected"] == 0
    assert result.after_memoria_task == "followup-task"
    assert result.played_item_count == 1
    assert result.marked_injected == 0
    assert adapter.events == [
        "interaction_started",
        "schedule:True",
        "present:director",
        "interaction_completed",
    ]
    assert len(adapter.followup_calls) == 1
    assert adapter.followup_calls[0]["allow_audience"] is True
    assert adapter.followup_calls[0]["payload"].interaction["status"] == "presenting"


def test_consume_prepared_turn_schedules_followup_before_presenting():
    interaction = {
        "job_id": "prefetch-order",
        "source": "director_prefetch",
        "status": "prefetched",
        "metadata": {"decision": {"action": "continue_topic"}},
    }
    payload = _payload(interaction)
    adapter = FakePreparedTurnAdapter(interaction, payload.prepared_results)

    result = asyncio.run(
        consume_prepared_turn(
            adapter,
            payload,
            PreparedTurnConsumeOptions(
                session_id="live-a",
                allow_followup_prefetch=True,
                followup_allow_audience=True,
            ),
        )
    )

    assert result.after_memoria_task == "followup-task"
    assert adapter.events.index("schedule:True") < adapter.events.index("present:director")


def test_consume_prepared_turn_policy_may_chain_false_blocks_non_audience_followup(monkeypatch):
    interaction = {
        "job_id": "prefetch-no-chain",
        "source": "sentinel_no_chain",
        "status": "sentinel_ready",
        "metadata": {"decision": {"action": "continue_topic"}},
    }
    payload = _payload(interaction)
    adapter = FakePreparedTurnAdapter(interaction, payload.prepared_results)
    monkeypatch.setattr(
        "turn_pipeline.prepared_turn_policy_for_interaction",
        lambda _interaction: PreparedTurnPolicy(
            expected_status="sentinel_ready",
            presentation_source="sentinel_no_chain",
            may_chain=False,
            mark_audience_events_injected=False,
        ),
    )

    result = asyncio.run(
        consume_prepared_turn(
            adapter,
            payload,
            PreparedTurnConsumeOptions(
                session_id="live-a",
                allow_followup_prefetch=True,
                followup_allow_audience=True,
            ),
        )
    )

    assert result.consumed is True
    assert result.after_memoria_task is None
    assert "schedule:True" not in adapter.events
    assert adapter.followup_calls == []


def test_consume_prepared_turn_uses_payload_prepared_results_before_adapter_lookup():
    interaction = {
        "job_id": "prefetch-payload",
        "source": "director_prefetch",
        "status": "prefetched",
        "metadata": {"decision": {"action": "continue_topic"}},
    }
    payload = _payload(
        interaction,
        prepared_results=[
            {
                "message": {"content": "payload reply"},
                "items": [{"item_id": "payload-1"}, {"item_id": "payload-2"}],
            }
        ],
    )
    adapter = FakePreparedTurnAdapter(
        interaction,
        [{"message": {"content": "adapter reply"}, "items": []}],
    )

    result = asyncio.run(
        consume_prepared_turn(
            adapter,
            payload,
            PreparedTurnConsumeOptions(
                session_id="live-a",
                completion_metadata_key="prefetch_consumed",
                started_event_type="interaction_started",
                completed_event_type="interaction_completed",
            ),
        )
    )

    assert result.consumed is True
    assert adapter.prepared_results_calls == 0
    assert result.played_item_count == 2
    assert result.interaction["reply_text"] == "reply text"


def test_consume_prepared_turn_refuses_claim_with_wrong_presenting_status():
    interaction = {
        "job_id": "prefetch-claim",
        "source": "director_prefetch",
        "status": "prefetched",
        "metadata": {"decision": {"action": "continue_topic"}},
    }
    payload = _payload(interaction)
    adapter = FakePreparedTurnAdapter(
        interaction,
        payload.prepared_results,
        claim_status="prefetched",
    )

    result = asyncio.run(
        consume_prepared_turn(
            adapter,
            payload,
            PreparedTurnConsumeOptions(
                session_id="live-a",
                completion_metadata_key="prefetch_consumed",
                started_event_type="interaction_started",
                completed_event_type="interaction_completed",
            ),
        )
    )

    assert result.consumed is False
    assert result.reason == "presenting_claim_failed"
    assert result.interaction["status"] == "prefetched"
    assert adapter.events == []


def test_consume_prepared_turn_refuses_complete_with_wrong_status():
    interaction = {
        "job_id": "prefetch-complete-status",
        "source": "director_prefetch",
        "status": "prefetched",
        "metadata": {"decision": {"action": "continue_topic"}},
    }
    payload = _payload(interaction)
    adapter = FakePreparedTurnAdapter(
        interaction,
        payload.prepared_results,
        complete_status="presenting",
    )

    result = asyncio.run(
        consume_prepared_turn(
            adapter,
            payload,
            PreparedTurnConsumeOptions(
                session_id="live-a",
                completion_metadata_key="prefetch_consumed",
                started_event_type="interaction_started",
                completed_event_type="interaction_completed",
            ),
        )
    )

    assert result.consumed is False
    assert result.reason == "complete_failed"
    assert result.interaction["status"] == "presenting"
    assert adapter.events == ["interaction_started", "present:director"]


def test_consume_prepared_turn_refuses_complete_returning_none():
    interaction = {
        "job_id": "prefetch-complete-none",
        "source": "director_prefetch",
        "status": "prefetched",
        "metadata": {"decision": {"action": "continue_topic"}},
    }
    payload = _payload(interaction)
    adapter = FakePreparedTurnAdapter(
        interaction,
        payload.prepared_results,
        complete_returns_none=True,
    )

    result = asyncio.run(
        consume_prepared_turn(
            adapter,
            payload,
            PreparedTurnConsumeOptions(
                session_id="live-a",
                completion_metadata_key="prefetch_consumed",
                started_event_type="interaction_started",
                completed_event_type="interaction_completed",
            ),
        )
    )

    assert result.consumed is False
    assert result.reason == "complete_failed"
    assert result.interaction is None
    assert adapter.events == ["interaction_started", "present:director"]


def test_consume_prepared_turn_marks_audience_events_without_general_chain():
    interaction = {
        "job_id": "audience-1",
        "source": "director_audience_prepare",
        "status": "prepared",
        "event_ids": [101, 102],
        "metadata": {"decision": {"action": "reply_chat_batch"}},
    }
    payload = _payload(interaction)
    adapter = FakePreparedTurnAdapter(interaction, payload.prepared_results)

    result = asyncio.run(
        consume_prepared_turn(
            adapter,
            payload,
            PreparedTurnConsumeOptions(
                session_id="live-a",
                allow_followup_prefetch=False,
                completion_metadata_key="audience_prepare_consumed",
                started_event_type="interaction_started",
                completed_event_type="interaction_completed",
            ),
        )
    )

    assert result.consumed is True
    assert result.after_memoria_task is None
    assert result.marked_injected == 2
    assert result.interaction["metadata"]["audience_prepare_consumed"] is True
    assert result.interaction["metadata"]["played_item_count"] == 1
    assert result.interaction["metadata"]["marked_injected"] == 2
    assert adapter.marked_event_ids == [101, 102]
    assert adapter.followup_calls == []


def test_consume_prepared_turn_forces_audience_followup_to_planned_only():
    interaction = {
        "job_id": "audience-2",
        "source": "director_audience_prepare",
        "status": "prepared",
        "event_ids": [201],
        "metadata": {"decision": {"action": "reply_chat_batch"}},
    }
    payload = _payload(interaction)
    adapter = FakePreparedTurnAdapter(interaction, payload.prepared_results)

    result = asyncio.run(
        consume_prepared_turn(
            adapter,
            payload,
            PreparedTurnConsumeOptions(
                session_id="live-a",
                allow_followup_prefetch=True,
                followup_allow_audience=True,
                completion_metadata_key="audience_prepare_consumed",
                started_event_type="interaction_started",
                completed_event_type="interaction_completed",
            ),
        )
    )

    assert result.consumed is True
    assert result.after_memoria_task == "followup-task"
    assert len(adapter.followup_calls) == 1
    assert adapter.followup_calls[0]["allow_audience"] is False
    assert adapter.followup_calls[0]["payload"].interaction["status"] == "presenting"


def test_consume_prepared_turn_refuses_final_closing_when_not_expected():
    interaction = {
        "job_id": "closing-1",
        "source": "director_prefetch",
        "status": "prefetched",
        "metadata": {"decision": {"action": "final_closing"}},
    }
    payload = _payload(interaction)
    adapter = FakePreparedTurnAdapter(interaction, payload.prepared_results)

    result = asyncio.run(
        consume_prepared_turn(
            adapter,
            payload,
            PreparedTurnConsumeOptions(
                session_id="live-a",
                expected_dedicated_closing=False,
                completion_metadata_key="final_closing_prefetch_consumed",
                started_event_type="interaction_started",
                completed_event_type="interaction_completed",
            ),
        )
    )

    assert result.consumed is False
    assert result.reason == "dedicated_closing_unexpected"
    assert result.interaction["status"] == "prefetched"
    assert adapter.events == []
    assert adapter.followup_calls == []
