"""YouTubeBridge server route helper functions。"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


def public_connector(connector: dict | None) -> dict | None:
    if not connector:
        return None
    return {
        **connector,
        "api_key": "",
        "api_key_configured": bool(connector.get("api_key")),
    }


def build_e2e_checkpoint(storage_obj: Any, session_id: str) -> dict[str, Any]:
    session = storage_obj.get_session(session_id)
    if not session:
        raise ValueError("session not found")
    packs = storage_obj.list_session_topic_packs(session_id)
    interactions = storage_obj.list_interactions(session_id, limit=100)
    events = storage_obj.list_events(session_id, limit=500)
    active_interactions = [
        item for item in interactions
        if str(item.get("status") or "") in {"queued", "running", "active"}
    ]
    usage_stats = storage_obj.get_topic_pack_usage_stats(session_id)
    director_state = storage_obj.get_director_state(session_id)
    return {
        "session_id": session_id,
        "topic_pack_id": int(packs[0]["id"]) if packs else None,
        "status": str(session.get("status") or ""),
        "started_at": str(session.get("started_at") or session.get("created_at") or ""),
        "ended_at": str(session.get("ended_at") or ""),
        "last_message_count": storage_obj.count_events(session_id),
        "last_sc_count": sum(1 for event in events if str(event.get("priority_class") or "") == "super_chat"),
        "active_interaction_count": len(active_interactions),
        "usage_stats": {
            "total_entries": int(usage_stats.get("total_entries") or 0),
            "used_entry_count": int(usage_stats.get("used_entry_count") or 0),
            "unused_entry_count": int(usage_stats.get("unused_entry_count") or 0),
            "low_unused": bool(usage_stats.get("low_unused")),
            "repeated_entry": usage_stats.get("repeated_entry") if isinstance(usage_stats.get("repeated_entry"), dict) else None,
        },
        "director_status": str(director_state.get("status") or ""),
        "checkpoint_created_at": datetime.now().isoformat(),
        "can_resume": str(session.get("status") or "") not in {"deleted"},
    }


def write_e2e_checkpoint(path: Path, checkpoint: dict[str, Any]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(checkpoint, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {"path": str(path), "checkpoint": checkpoint}


def read_e2e_checkpoint(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None
