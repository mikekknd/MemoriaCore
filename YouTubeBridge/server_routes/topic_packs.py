"""Topic pack routes。"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from models import TopicPackCreateRequest, TopicPackEntryCreateRequest, TopicPackEntryUpdateRequest, TopicPackUpdateRequest
from server_presenters import sanitize_topic_pack_usage_status


router = APIRouter()
_state = None
storage = None
manager = None
summary_manager = None
chat_preview_cache = None
STATIC_ROOT = ""
UI_ASSETS_ROOT = None
E2E_CHECKPOINT_PATH = None


def configure(state):
    global _state, storage, manager, summary_manager, chat_preview_cache
    global STATIC_ROOT, UI_ASSETS_ROOT, E2E_CHECKPOINT_PATH
    _state = state
    storage = state.storage
    manager = state.manager
    summary_manager = state.summary_manager
    chat_preview_cache = state.chat_preview_cache
    STATIC_ROOT = str(state.static_root)
    UI_ASSETS_ROOT = state.ui_assets_root
    E2E_CHECKPOINT_PATH = state.e2e_checkpoint_path


def _require_state():
    if _state is None:
        raise RuntimeError("server route state is not configured")
    return _state


def _sanitize_graph_metadata(metadata: dict | None) -> dict:
    safe: dict = {}
    for key, value in (metadata or {}).items():
        key_str = str(key)
        key_lower = key_str.lower()
        if key_lower in {"prompt", "external_context", "context_text", "raw_context", "embedding", "embeddings", "vector"}:
            continue
        safe[key_str] = value
    return safe


def sanitize_topic_graph(graph: dict) -> dict:
    return {
        "pack_id": int(graph.get("pack_id") or 0),
        "nodes": [
            {
                "id": int(node.get("id") or 0),
                "pack_id": int(node.get("pack_id") or 0),
                "entry_id": node.get("entry_id"),
                "node_key": str(node.get("node_key") or ""),
                "node_type": str(node.get("node_type") or ""),
                "title": str(node.get("title") or ""),
                "summary": str(node.get("summary") or "")[:500],
                "source_name": str(node.get("source_name") or ""),
                "source_heading": str(node.get("source_heading") or ""),
                "metadata": _sanitize_graph_metadata(node.get("metadata") if isinstance(node.get("metadata"), dict) else {}),
            }
            for node in graph.get("nodes", [])
            if isinstance(node, dict)
        ],
        "edges": [
            {
                "id": int(edge.get("id") or 0),
                "pack_id": int(edge.get("pack_id") or 0),
                "source_node_id": int(edge.get("source_node_id") or 0),
                "target_node_id": int(edge.get("target_node_id") or 0),
                "source_node_key": str(edge.get("source_node_key") or ""),
                "target_node_key": str(edge.get("target_node_key") or ""),
                "edge_type": str(edge.get("edge_type") or ""),
                "weight": float(edge.get("weight") or 0.0),
                "evidence": str(edge.get("evidence") or "")[:500],
            }
            for edge in graph.get("edges", [])
            if isinstance(edge, dict)
        ],
    }


def sanitize_topic_graph_trace(trace: dict | None) -> dict | None:
    if not trace:
        return None
    preview = str(trace.get("context_text_preview") or "")
    if "<topic_pack_fact_cards" in preview:
        preview = "[hidden context]"
    return {
        "id": int(trace.get("id") or 0),
        "session_id": str(trace.get("session_id") or ""),
        "pack_id": int(trace.get("pack_id") or 0),
        "source": str(trace.get("source") or ""),
        "query_text": str(trace.get("query_text") or "")[:500],
        "entry_node_ids": list(trace.get("entry_node_ids") or []),
        "expanded_node_ids": list(trace.get("expanded_node_ids") or []),
        "selected_node_ids": list(trace.get("selected_node_ids") or []),
        "rejected_nodes": list(trace.get("rejected_nodes") or []),
        "context_text_preview": preview[:500],
        "created_at": str(trace.get("created_at") or ""),
    }


@router.get("/topic-packs")
async def list_topic_packs():
    return storage.list_topic_packs()


@router.post("/topic-packs")
async def create_topic_pack(body: TopicPackCreateRequest):
    return storage.create_topic_pack(body.model_dump())


@router.delete("/topic-packs")
async def delete_all_topic_packs():
    result = storage.delete_all_topic_packs()
    return {
        "status": "deleted",
        "pack_count": int(result.get("pack_count") or 0),
        "entry_count": int(result.get("entry_count") or 0),
    }


@router.put("/topic-packs/{pack_id}")
async def update_topic_pack(pack_id: int, body: TopicPackUpdateRequest):
    try:
        return storage.update_topic_pack(pack_id, body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=404 if "不存在" in str(exc) else 400, detail=str(exc))


@router.delete("/topic-packs/{pack_id}")
async def delete_topic_pack(pack_id: int):
    result = storage.delete_topic_pack(pack_id)
    if not result.get("deleted"):
        raise HTTPException(status_code=404, detail="topic pack not found")
    return {"status": "deleted", "pack_id": int(pack_id), "entry_count": int(result.get("entry_count") or 0)}


@router.get("/topic-packs/{pack_id}/entries")
async def list_topic_pack_entries(pack_id: int, limit: int = 100):
    if not storage.get_topic_pack(pack_id):
        raise HTTPException(status_code=404, detail="topic pack not found")
    return {
        "pack_id": pack_id,
        "entries": storage.list_topic_pack_entries(pack_id, limit=limit),
    }


@router.post("/topic-packs/{pack_id}/entries")
async def create_topic_pack_entry(pack_id: int, body: TopicPackEntryCreateRequest):
    try:
        entry = storage.create_topic_pack_entry(pack_id, body.model_dump())
        embedding = None
        try:
            embedding = await asyncio.to_thread(manager.index_topic_pack_entry, int(entry["id"]))
        except Exception as exc:
            return {**entry, "embedding_status": "failed", "embedding_error": str(exc)[:300]}
        return {**entry, "embedding_status": "indexed", "embedding": embedding}
    except ValueError as exc:
        raise HTTPException(status_code=404 if "不存在" in str(exc) else 400, detail=str(exc))


@router.put("/topic-packs/{pack_id}/entries/{entry_id}")
async def update_topic_pack_entry(pack_id: int, entry_id: int, body: TopicPackEntryUpdateRequest):
    existing = storage.get_topic_pack_entry(entry_id)
    if not existing or int(existing["pack_id"]) != int(pack_id):
        raise HTTPException(status_code=404, detail="topic pack entry not found")
    try:
        entry = storage.update_topic_pack_entry(entry_id, body.model_dump())
        try:
            await asyncio.to_thread(manager.index_topic_pack_entry, int(entry["id"]))
        except Exception as exc:
            return {**entry, "embedding_status": "failed", "embedding_error": str(exc)[:300]}
        return {**entry, "embedding_status": "indexed"}
    except ValueError as exc:
        raise HTTPException(status_code=404 if "不存在" in str(exc) else 400, detail=str(exc))


@router.delete("/topic-packs/{pack_id}/entries/{entry_id}")
async def delete_topic_pack_entry(pack_id: int, entry_id: int):
    existing = storage.get_topic_pack_entry(entry_id)
    if not existing or int(existing["pack_id"]) != int(pack_id):
        raise HTTPException(status_code=404, detail="topic pack entry not found")
    deleted = storage.delete_topic_pack_entry(entry_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="topic pack entry not found")
    return {"status": "deleted", "pack_id": int(pack_id), "entry_id": int(entry_id)}


@router.get("/sessions/{session_id}/topic-packs")
async def list_session_topic_packs(session_id: str):
    if not storage.get_session(session_id):
        raise HTTPException(status_code=404, detail="session not found")
    return {
        "session_id": session_id,
        "packs": storage.list_session_topic_packs(session_id),
        "entries": storage.list_session_topic_pack_entries(session_id),
    }


@router.delete("/sessions/{session_id}/topic-packs")
async def clear_session_topic_pack(session_id: str):
    try:
        return storage.clear_session_topic_pack(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/sessions/{session_id}/topic-packs/usage")
async def get_session_topic_pack_usage(session_id: str):
    if not storage.get_session(session_id):
        raise HTTPException(status_code=404, detail="session not found")
    return sanitize_topic_pack_usage_status(manager.get_topic_pack_usage_status(session_id))


@router.get("/sessions/{session_id}/topic-packs/search")
async def search_session_topic_packs(session_id: str, query: str, limit: int = 6):
    def _search_entries() -> dict[str, Any]:
        if not storage.get_session(session_id):
            raise ValueError("session not found")
        embedding = manager._embed_text(query, timeout_seconds=20)
        vector = embedding.get("dense") if isinstance(embedding, dict) else []
        entries = storage.search_session_topic_pack_entries(session_id, vector, limit=limit, min_score=0.0)
        storage.record_topic_pack_entry_usages(
            session_id,
            entries,
            query_text=query,
            usage_source="manual_search",
        )
        return {
            "session_id": session_id,
            "query": query,
            "embedding_model": embedding.get("model") if isinstance(embedding, dict) else "",
            "entries": entries,
        }

    try:
        return await asyncio.wait_for(asyncio.to_thread(_search_entries), timeout=30)
    except TimeoutError:
        raise HTTPException(status_code=504, detail="topic pack search timeout")
    except ValueError as exc:
        raise HTTPException(status_code=404 if "session not found" in str(exc) else 400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/topic-packs/{pack_id}/search")
async def search_topic_pack(pack_id: int, query: str, limit: int = 6):
    def _search_entries() -> dict[str, Any]:
        if not storage.get_topic_pack(pack_id):
            raise ValueError("topic pack not found")
        embedding = manager._embed_text(query, timeout_seconds=20)
        vector = embedding.get("dense") if isinstance(embedding, dict) else []
        entries = storage.search_topic_pack_entries(pack_id, vector, limit=limit, min_score=0.0)
        return {
            "pack_id": pack_id,
            "query": query,
            "embedding_model": embedding.get("model") if isinstance(embedding, dict) else "",
            "entries": entries,
        }

    try:
        return await asyncio.wait_for(asyncio.to_thread(_search_entries), timeout=30)
    except TimeoutError:
        raise HTTPException(status_code=504, detail="topic pack search timeout")
    except ValueError as exc:
        raise HTTPException(status_code=404 if "not found" in str(exc) else 400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/topic-packs/{pack_id}/embeddings/rebuild")
async def rebuild_topic_pack_embeddings(pack_id: int):
    if not storage.get_topic_pack(pack_id):
        raise HTTPException(status_code=404, detail="topic pack not found")
    try:
        return await asyncio.to_thread(manager.rebuild_topic_pack_embeddings, pack_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/topic-packs/{pack_id}/graph")
async def get_topic_pack_graph(pack_id: int):
    if not storage.get_topic_pack(pack_id):
        raise HTTPException(status_code=404, detail="topic pack not found")
    return sanitize_topic_graph(storage.get_topic_graph(pack_id))


@router.post("/topic-packs/{pack_id}/graph/rebuild")
async def rebuild_topic_pack_graph(pack_id: int):
    if not storage.get_topic_pack(pack_id):
        raise HTTPException(status_code=404, detail="topic pack not found")
    try:
        return await asyncio.to_thread(manager.rebuild_topic_graph_for_pack, pack_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/sessions/{session_id}/topic-graph/traces")
async def list_topic_graph_traces(session_id: str, limit: int = 20):
    if not storage.get_session(session_id):
        raise HTTPException(status_code=404, detail="session not found")
    traces = storage.list_topic_graph_retrieval_traces(session_id, limit=limit)
    return {
        "session_id": session_id,
        "traces": [trace for item in traces if (trace := sanitize_topic_graph_trace(item))],
    }


@router.get("/sessions/{session_id}/topic-graph/latest-trace")
async def get_latest_topic_graph_trace(session_id: str):
    if not storage.get_session(session_id):
        raise HTTPException(status_code=404, detail="session not found")
    return {
        "session_id": session_id,
        "trace": sanitize_topic_graph_trace(storage.get_latest_topic_graph_retrieval_trace(session_id)),
    }


@router.post("/sessions/{session_id}/topic-packs/{pack_id}")
async def link_topic_pack(session_id: str, pack_id: int, replace: bool = False):
    try:
        if replace:
            return storage.set_session_topic_pack(session_id, pack_id)
        return storage.link_topic_pack_to_session(session_id, pack_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
