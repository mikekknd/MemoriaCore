"""YouTubeBridge API public response sanitizer。"""
from __future__ import annotations

from typing import Any


_RUNTIME_STATUS_PUBLIC_KEYS = (
    "session_id",
    "status",
    "running",
    "mode",
    "last_error",
    "auto_inject_running",
    "last_auto_inject_at",
    "last_auto_inject_error",
    "auto_test_events_running",
    "last_auto_test_event_at",
    "last_auto_test_event_error",
)

_DIRECTOR_STATE_PUBLIC_KEYS = (
    "session_id",
    "director_enabled",
    "status",
    "current_topic",
    "last_director_action_at",
    "consecutive_ai_turns",
    "created_at",
    "updated_at",
)


def sanitize_chat_preview_message(message: dict) -> dict:
    if not isinstance(message, dict):
        return {}
    return {
        "message_id": message.get("message_id"),
        "role": str(message.get("role") or ""),
        "content": str(message.get("content") or ""),
        "created_at": message.get("created_at") or message.get("timestamp") or "",
        "timestamp": message.get("timestamp") or message.get("created_at") or "",
        "character_id": message.get("character_id"),
        "character_name": message.get("character_name"),
    }


def sanitize_chat_preview_session(session: dict | None) -> dict | None:
    if not isinstance(session, dict):
        return None
    allowed = (
        "session_id",
        "channel",
        "channel_uid",
        "character_id",
        "character_ids",
        "session_mode",
        "group_name",
        "last_active",
        "is_active",
        "message_count",
    )
    return {key: session.get(key) for key in allowed if key in session}


def sanitize_public_text(value: Any, *, max_chars: int = 800) -> str:
    text = str(value or "")
    hidden_markers = (
        "<external_chat_context",
        "<topic_pack_fact_cards",
        "hidden external context",
        "完整 SC 清單",
    )
    if any(marker in text for marker in hidden_markers):
        return "[hidden context]"
    if len(text) > max_chars:
        return f"{text[:max_chars]}... [truncated {len(text)} chars]"
    return text


def sanitize_interaction_metadata(value: Any, *, depth: int = 0) -> Any:
    if value is None:
        return None
    if isinstance(value, list):
        if len(value) > 16 and all(isinstance(item, (int, float)) for item in value):
            return f"[embedding {len(value)} dims]"
        return [sanitize_interaction_metadata(item, depth=depth + 1) for item in value[:24]]
    if not isinstance(value, dict):
        if isinstance(value, str):
            return sanitize_public_text(value)
        return value

    output: dict[str, Any] = {}
    for key, raw in value.items():
        key_str = str(key)
        key_lower = key_str.lower()
        if key_lower in {"embedding", "embeddings", "embedding_vector", "embedding_blob", "vector"}:
            output[key_str] = (
                f"[embedding {len(raw)} dims]" if isinstance(raw, list) else "[hidden embedding]"
            )
            continue
        if (
            "prompt" in key_lower
            or "hidden" in key_lower
            or "raw" in key_lower
            or key_lower in {
                "hidden_context",
                "external_context",
                "context_text",
                "raw_context",
                "episode_plan_completed_state",
                "planned_state",
                "turn_contract",
                "planned_turn_contracts",
            }
        ):
            output[key_str] = "[hidden]"
            continue
        if key_lower in {"events", "event_ids", "super_chats", "comments"} and isinstance(raw, list):
            output[key_str] = {"count": len(raw)}
            continue
        if key_lower in {"opening_decision", "last_decision", "decision"} and isinstance(raw, dict):
            output[key_str] = {
                "action": raw.get("action"),
                "reason": raw.get("reason"),
                "current_topic": raw.get("current_topic"),
            }
            continue
        if key_lower == "summary" and isinstance(raw, dict):
            allowed_summary = (
                "source",
                "source_session_id",
                "connector_id",
                "video_id",
                "live_chat_id",
                "event_count",
                "dropped_count",
            )
            output[key_str] = {
                summary_key: raw.get(summary_key)
                for summary_key in allowed_summary
                if summary_key in raw
            }
            continue
        output[key_str] = (
            "[nested]"
            if depth >= 3
            else sanitize_interaction_metadata(raw, depth=depth + 1)
        )
    return output


def sanitize_interaction(interaction: dict | None) -> dict | None:
    if not isinstance(interaction, dict):
        return None
    sanitized = dict(interaction)
    for key in ("content", "reply_text", "closure_text", "request_text", "response_text"):
        if key in sanitized:
            sanitized[key] = sanitize_public_text(sanitized.get(key))
    sanitized["metadata"] = sanitize_interaction_metadata(sanitized.get("metadata") or {})
    return sanitized


def sanitize_director_state(director: dict | None) -> dict | None:
    if not isinstance(director, dict):
        return None
    output = {
        key: director.get(key)
        for key in _DIRECTOR_STATE_PUBLIC_KEYS
        if key in director
    }
    if isinstance(director.get("metadata"), dict):
        output["metadata"] = sanitize_interaction_metadata(director.get("metadata") or {})
    else:
        output["metadata"] = {}
    return output


def sanitize_runtime_status(status: Any) -> Any:
    if not isinstance(status, dict):
        return status
    output = {
        key: status.get(key)
        for key in _RUNTIME_STATUS_PUBLIC_KEYS
        if key in status
    }
    if isinstance(status.get("active_interaction"), dict):
        output["active_interaction"] = sanitize_interaction(status.get("active_interaction"))
    if isinstance(status.get("director"), dict):
        output["director"] = sanitize_director_state(status.get("director"))
    return output


def _event_count_from_result(value: Any) -> int:
    if not isinstance(value, dict):
        return 0
    for candidate in (
        value.get("event_ids"),
        (value.get("summary") or {}).get("event_ids") if isinstance(value.get("summary"), dict) else None,
        (value.get("interaction") or {}).get("event_ids") if isinstance(value.get("interaction"), dict) else None,
        ((value.get("result") or {}).get("summary") or {}).get("event_ids")
        if isinstance(value.get("result"), dict) and isinstance((value.get("result") or {}).get("summary"), dict)
        else None,
        ((value.get("result") or {}).get("interaction") or {}).get("event_ids")
        if isinstance(value.get("result"), dict) and isinstance((value.get("result") or {}).get("interaction"), dict)
        else None,
    ):
        if isinstance(candidate, list):
            return len(candidate)
    return 0


def sanitize_closing_result_summary(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    output: dict[str, Any] = {}
    for key in (
        "status",
        "reason",
        "super_chat_count",
        "candidate_super_chat_count",
        "marked",
        "initial_pending_count",
        "classified_count",
        "failed_count",
        "fallback_count",
        "batch_count",
    ):
        if key in result:
            output[key] = result.get(key)
    output["event_count"] = _event_count_from_result(result)
    return output


def sanitize_phase_pipeline_response(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    output: dict[str, Any] = {}
    for key in ("phase", "status", "session_id", "reason", "injected_at"):
        if key in payload:
            output[key] = payload.get(key)
    if "runtime_status" in payload:
        output["runtime_status"] = sanitize_runtime_status(payload.get("runtime_status"))
    if isinstance(payload.get("director"), dict):
        output["director"] = sanitize_director_state(payload.get("director"))
    if isinstance(payload.get("interaction"), dict):
        output["interaction"] = sanitize_interaction(payload.get("interaction"))
    if isinstance(payload.get("closing"), dict):
        closing = payload["closing"]
        summary = sanitize_closing_result_summary(closing)
        output["closing"] = {
            "status": str(summary.get("status") or ""),
            "reason": str(summary.get("reason") or ""),
            "super_chat_count": int(summary.get("super_chat_count") or 0),
            "candidate_super_chat_count": int(summary.get("candidate_super_chat_count") or 0),
            "marked": int(summary.get("marked") or 0),
            "event_count": int(summary.get("event_count") or 0),
        }
    if isinstance(payload.get("finalized"), dict):
        finalized = payload["finalized"]
        output["finalized"] = {
            "session_id": finalized.get("session_id"),
            "status": finalized.get("status"),
            "runtime_status": sanitize_runtime_status(finalized.get("runtime_status")),
            "closing_super_chat_thanks": sanitize_closing_result_summary(
                finalized.get("closing_super_chat_thanks")
            ),
            "closing_safety_resolution": sanitize_closing_result_summary(
                finalized.get("closing_safety_resolution")
            ),
        }
    return output


def sanitize_topic_pack_usage_status(status: dict[str, Any]) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for item in status.get("entries") or []:
        if not isinstance(item, dict):
            continue
        entries.append({
            "entry_id": int(item.get("entry_id") or 0),
            "pack_id": int(item.get("pack_id") or 0),
            "title": str(item.get("title") or "")[:200],
            "source_type": str(item.get("source_type") or "")[:80],
            "usage_count": int(item.get("usage_count") or 0),
            "avg_similarity": float(item.get("avg_similarity") or 0.0),
            "last_used_at": str(item.get("last_used_at") or ""),
            "usage_sources": [
                str(source)[:80]
                for source in (item.get("usage_sources") if isinstance(item.get("usage_sources"), list) else [])
            ],
        })
    repeated = status.get("repeated_entry") if isinstance(status.get("repeated_entry"), dict) else None
    research_gate_raw = status.get("research_gate") if isinstance(status.get("research_gate"), dict) else {}
    research_statuses = research_gate_raw.get("statuses") if isinstance(research_gate_raw.get("statuses"), dict) else {}
    research_gate = {
        "total_count": int(research_gate_raw.get("total_count") or 0),
        "success_count": int(research_gate_raw.get("success_count") or 0),
        "degraded_count": int(research_gate_raw.get("degraded_count") or 0),
        "statuses": {
            str(key)[:80]: int(value or 0)
            for key, value in research_statuses.items()
            if str(key).strip()
        },
    }
    return {
        "session_id": str(status.get("session_id") or ""),
        "total_entries": int(status.get("total_entries") or 0),
        "used_entry_count": int(status.get("used_entry_count") or 0),
        "unused_entry_count": int(status.get("unused_entry_count") or 0),
        "low_unused": bool(status.get("low_unused")),
        "repeated_entry": {
            "entry_id": int(repeated.get("entry_id") or 0),
            "recent_count": int(repeated.get("recent_count") or 0),
            "title": str(repeated.get("title") or "")[:200],
        } if repeated else None,
        "last_replenished_at": str(status.get("last_replenished_at") or ""),
        "last_replenish_reason": str(status.get("last_replenish_reason") or ""),
        "last_replenish_status": str(status.get("last_replenish_status") or ""),
        "worker_status": str(status.get("worker_status") or ""),
        "last_replenish_fallback_mode": str(status.get("last_replenish_fallback_mode") or ""),
        "last_replenish_error": str(status.get("last_replenish_error") or "")[:300],
        "replenishment_in_progress": bool(status.get("replenishment_in_progress")),
        "research_gate": research_gate,
        "entries": entries,
        "recent_usage_count": len(status.get("recent_usage") or []),
    }
