import sys
from pathlib import Path

BRIDGE_ROOT = Path(__file__).resolve().parents[1]
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))

from turn_pipeline import prepared_turn_policy_for_interaction


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
