"""Prepared live turn policy helpers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PreparedTurnPolicy:
    expected_status: str
    presentation_source: str
    may_chain: bool
    mark_audience_events_injected: bool
    dedicated_closing: bool = False


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
