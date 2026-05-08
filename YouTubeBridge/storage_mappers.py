"""YouTubeBridge SQLite row mapper 與序列化 helper。"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import struct
from typing import Any


def json_dump(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False)


def json_load(value: str, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except Exception:
        return fallback


def topic_entry_content_hash(entry: dict[str, Any]) -> str:
    text = f"{entry.get('title') or ''}\n{entry.get('body') or ''}"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def vector_to_blob(vector: list[float]) -> bytes:
    values = [float(value) for value in vector]
    if not values:
        return b""
    return struct.pack(f"<{len(values)}f", *values)


def blob_to_vector(blob: bytes | memoryview | None, dim: int) -> list[float]:
    if not blob or dim <= 0:
        return []
    data = bytes(blob)
    expected = dim * 4
    if len(data) != expected:
        return []
    return [float(value) for value in struct.unpack(f"<{dim}f", data)]


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm <= 0 or right_norm <= 0:
        return 0.0
    return dot / (left_norm * right_norm)


def row_value(row: sqlite3.Row, key: str, fallback: Any = None) -> Any:
    return row[key] if key in row.keys() else fallback


def int_or_default(value: Any, fallback: int) -> int:
    if value is None or value == "":
        return int(fallback)
    return int(value)


def row_to_connector(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return {
        "connector_id": row["connector_id"],
        "display_name": row["display_name"] or "",
        "api_key": row["api_key"] or "",
        "enabled": bool(row["enabled"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def row_to_session(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return {
        "session_id": row["session_id"],
        "connector_id": row["connector_id"],
        "display_name": row["display_name"] or "",
        "video_id": row["video_id"] or "",
        "live_chat_id": row["live_chat_id"] or "",
        "target_memoria_session_id": row["target_memoria_session_id"] or "",
        "episode_plan_id": row_value(row, "episode_plan_id", "") or "",
        "character_ids": json_load(row["character_ids_json"], []),
        "status": row["status"] or "stopped",
        "auto_connect": bool(row["auto_connect"]),
        "auto_inject": bool(row["auto_inject"]),
        "inject_interval_seconds": int(row["inject_interval_seconds"] or 30),
        "inject_min_interval_seconds": int(row_value(row, "inject_min_interval_seconds", 10) or 10),
        "inject_min_interval_ratio": float(row_value(row, "inject_min_interval_ratio", 0.32) or 0.32),
        "min_pending_events": int(row["min_pending_events"] or 1),
        "max_pending_events": int(row_value(row, "max_pending_events", 12) or 12),
        "dynamic_inject_enabled": bool(row_value(row, "dynamic_inject_enabled", 1)),
        "max_context_messages": int(row["max_context_messages"] or 50),
        "max_context_chars": int(row["max_context_chars"] or 8000),
        "retention_days": int(row["retention_days"] or 30),
        "planned_duration_minutes": int_or_default(row_value(row, "planned_duration_minutes", 30), 30),
        "auto_finalize_on_duration": bool(row_value(row, "auto_finalize_on_duration", 1)),
        "auto_delete_after_processed": bool(row_value(row, "auto_delete_after_processed", 1)),
        "director_guidance": row_value(row, "director_guidance", "") or "",
        "host_interaction_rules": row_value(row, "host_interaction_rules", "") or "",
        "program_segment_plan": row_value(row, "program_segment_plan", "") or "",
        "program_segment_turns": int(row_value(row, "program_segment_turns", 3) or 3),
        "auto_test_events_enabled": bool(row_value(row, "auto_test_events_enabled", 0)),
        "test_event_min_seconds": int(row_value(row, "test_event_min_seconds", 20) or 20),
        "test_event_max_seconds": int(row_value(row, "test_event_max_seconds", 45) or 45),
        "test_event_count_per_tick": int(row_value(row, "test_event_count_per_tick", 3) or 3),
        "test_event_use_llm": bool(row_value(row, "test_event_use_llm", 1)),
        "test_super_chat_count_per_tick": int(row_value(row, "test_super_chat_count_per_tick", 0) or 0),
        "test_malicious_sc_enabled": bool(row_value(row, "test_malicious_sc_enabled", 0)),
        "test_sc_burst_mode": bool(row_value(row, "test_sc_burst_mode", 0)),
        "sc_interrupt_cooldown_seconds": int(row_value(row, "sc_interrupt_cooldown_seconds", 30) or 30),
        "max_sc_per_batch": int(row_value(row, "max_sc_per_batch", 5) or 5),
        "director_anchor_every_turns": int(row_value(row, "director_anchor_every_turns", 2) or 2),
        "director_group_turn_limit": int(row_value(row, "director_group_turn_limit", 3) or 3),
        "director_max_chat_batches_before_anchor": int(row_value(row, "director_max_chat_batches_before_anchor", 2) or 2),
        "director_offtopic_policy": row_value(row, "director_offtopic_policy", "defer") or "defer",
        "director_sc_burst_policy": row_value(row, "director_sc_burst_policy", "summarize_batch") or "summarize_batch",
        "research_enabled": bool(row_value(row, "research_enabled", 0)),
        "research_cooldown_seconds": int(row_value(row, "research_cooldown_seconds", 300) or 300),
        "research_max_per_session": int(row_value(row, "research_max_per_session", 12) or 12),
        "auto_sc_thanks_on_finalize": bool(row_value(row, "auto_sc_thanks_on_finalize", 1)),
        "started_at": row_value(row, "started_at", "") or "",
        "finalized_at": row_value(row, "finalized_at", "") or "",
        "summary_status": row_value(row, "summary_status", "pending") or "pending",
        "summary_id": row_value(row, "summary_id", None),
        "summary_error": row_value(row, "summary_error", "") or "",
        "summary_updated_at": row_value(row, "summary_updated_at", "") or "",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def row_to_episode_plan(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return {
        "plan_id": row["plan_id"],
        "schema_version": row["schema_version"],
        "title": row["title"],
        "language": row["language"] or "zh-TW",
        "show_format": json_load(row["show_format_json"], {}),
        "plan_json": json_load(row["plan_json"], {}),
        "source_path": row["source_path"] or "",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def row_to_event(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return {
        "id": int(row["id"]),
        "bridge_session_id": row["bridge_session_id"],
        "connector_id": row["connector_id"],
        "video_id": row["video_id"] or "",
        "live_chat_id": row["live_chat_id"] or "",
        "youtube_message_id": row["youtube_message_id"] or "",
        "message_type": row["message_type"] or "",
        "author_channel_id": row["author_channel_id"] or "",
        "author_display_name": row["author_display_name"] or "",
        "author_profile_image_url": row["author_profile_image_url"] or "",
        "message_text": row["message_text"] or "",
        "published_at": row["published_at"] or "",
        "received_at": row["received_at"] or "",
        "status": row["status"] or "active",
        "amount_display_string": row["amount_display_string"] or "",
        "currency": row["currency"] or "",
        "amount_micros": int(row_value(row, "amount_micros", 0) or 0),
        "sc_tier": int(row_value(row, "sc_tier", 0) or 0),
        "priority_class": row_value(row, "priority_class", "normal") or "normal",
        "safety_label": row_value(row, "safety_label", "unclassified") or "unclassified",
        "safety_status": row_value(row, "safety_status", "pending") or "pending",
        "safe_message_text": row_value(row, "safe_message_text", "") or "",
        "safety_summary": row_value(row, "safety_summary", "") or "",
        "safety_reason": row_value(row, "safety_reason", "") or "",
        "safety_confidence": float(row_value(row, "safety_confidence", 0) or 0),
        "safety_checked_at": row_value(row, "safety_checked_at", "") or "",
        "handled_in_closing_at": row_value(row, "handled_in_closing_at", "") or "",
        "injected_at": row["injected_at"] or "",
        "injection_count": int(row["injection_count"] or 0),
        "metadata": json_load(row["metadata_json"], {}),
    }


def row_to_summary(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return {
        "id": int(row["id"]),
        "session_id": row["session_id"],
        "connector_id": row["connector_id"],
        "video_id": row["video_id"] or "",
        "live_chat_id": row["live_chat_id"] or "",
        "character_ids": json_load(row["character_ids_json"], []),
        "title": row["title"] or "",
        "summary_text": row["summary_text"] or "",
        "topic_tags": json_load(row["topic_tags_json"], []),
        "key_points": json_load(row["key_points_json"], []),
        "qa_pairs": json_load(row["qa_pairs_json"], []),
        "audience_mood": row["audience_mood"] or "",
        "memory_text": row["memory_text"] or "",
        "event_count": int(row["event_count"] or 0),
        "source_started_at": row["source_started_at"] or "",
        "source_ended_at": row["source_ended_at"] or "",
        "status": row["status"] or "completed",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "metadata": json_load(row["metadata_json"], {}),
    }


def row_to_interaction(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return {
        "id": int(row["id"]),
        "job_id": row["job_id"],
        "session_id": row["session_id"],
        "source": row["source"] or "youtube_injection",
        "priority": int(row["priority"] or 100),
        "status": row["status"] or "queued",
        "reason": row["reason"] or "",
        "event_ids": json_load(row["event_ids_json"], []),
        "memoria_session_id": row["memoria_session_id"] or "",
        "character_ids": json_load(row["character_ids_json"], []),
        "content": row["content"] or "",
        "reply_text": row["reply_text"] or "",
        "closure_text": row["closure_text"] or "",
        "created_at": row["created_at"],
        "started_at": row["started_at"] or "",
        "completed_at": row["completed_at"] or "",
        "interrupted_at": row["interrupted_at"] or "",
        "metadata": json_load(row["metadata_json"], {}),
    }


def row_to_director_state(row: sqlite3.Row | None, session_id: str) -> dict:
    if row is None:
        return {
            "session_id": session_id,
            "director_enabled": False,
            "idle_seconds": 60,
            "last_director_action_at": "",
            "current_topic": "",
            "consecutive_ai_turns": 0,
            "last_seen_event_id": 0,
            "status": "stopped",
            "updated_at": "",
            "metadata": {},
        }
    return {
        "session_id": row["session_id"],
        "director_enabled": bool(row["director_enabled"]),
        "idle_seconds": int(row["idle_seconds"] or 60),
        "last_director_action_at": row["last_director_action_at"] or "",
        "current_topic": row["current_topic"] or "",
        "consecutive_ai_turns": int(row["consecutive_ai_turns"] or 0),
        "last_seen_event_id": int(row["last_seen_event_id"] or 0),
        "status": row["status"] or "stopped",
        "updated_at": row["updated_at"] or "",
        "metadata": json_load(row["metadata_json"], {}),
    }


def row_to_topic_pack(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return {
        "id": int(row["id"]),
        "title": row["title"] or "",
        "description": row["description"] or "",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def row_to_topic_pack_entry(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return {
        "id": int(row["id"]),
        "pack_id": int(row["pack_id"]),
        "pack_title": row_value(row, "pack_title", "") or "",
        "title": row["title"] or "",
        "body": row["body"] or "",
        "source_url": row["source_url"] or "",
        "source_type": row["source_type"] or "manual",
        "tags": json_load(row["tags_json"], []),
        "created_at": row["created_at"],
    }


def row_to_topic_pack_entry_embedding(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    dim = int(row["embedding_dim"] or 0)
    return {
        "entry_id": int(row["entry_id"]),
        "pack_id": int(row["pack_id"]),
        "embedding_model": row["embedding_model"] or "",
        "embedding_dim": dim,
        "embedding": blob_to_vector(row["embedding_blob"], dim),
        "content_hash": row["content_hash"] or "",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def row_to_topic_graph_node(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return {
        "id": int(row["id"]),
        "pack_id": int(row["pack_id"]),
        "entry_id": int(row["entry_id"]) if row["entry_id"] is not None else None,
        "node_key": row["node_key"] or "",
        "node_type": row["node_type"] or "",
        "title": row["title"] or "",
        "summary": row["summary"] or "",
        "source_name": row["source_name"] or "",
        "source_heading": row["source_heading"] or "",
        "metadata": json_load(row["metadata_json"], {}),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def row_to_topic_graph_edge(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return {
        "id": int(row["id"]),
        "pack_id": int(row["pack_id"]),
        "source_node_id": int(row["source_node_id"]),
        "target_node_id": int(row["target_node_id"]),
        "source_node_key": row_value(row, "source_node_key", "") or "",
        "target_node_key": row_value(row, "target_node_key", "") or "",
        "edge_type": row["edge_type"] or "",
        "weight": float(row["weight"] or 0.0),
        "evidence": row["evidence"] or "",
        "created_at": row["created_at"],
    }


def row_to_topic_graph_retrieval_trace(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return {
        "id": int(row["id"]),
        "session_id": row["session_id"] or "",
        "pack_id": int(row["pack_id"]),
        "source": row["source"] or "",
        "query_text": row["query_text"] or "",
        "entry_node_ids": json_load(row["entry_node_ids_json"], []),
        "expanded_node_ids": json_load(row["expanded_node_ids_json"], []),
        "selected_node_ids": json_load(row["selected_node_ids_json"], []),
        "rejected_nodes": json_load(row["rejected_nodes_json"], []),
        "context_text_preview": row["context_text_preview"] or "",
        "created_at": row["created_at"],
    }
