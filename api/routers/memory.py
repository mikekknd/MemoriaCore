"""記憶區塊、核心認知、圖譜、查詢擴展端點"""
import asyncio
from fastapi import APIRouter, HTTPException, Query
from api.dependencies import (
    get_memory_sys, get_storage, get_router, get_embed_model, db_write_lock,
)
from api.models.requests import SearchRequest, CoreSearchRequest, ExpandQueryRequest, BlockUpdateRequest
from api.models.responses import (
    MemoryBlockDTO, SearchResultDTO, CoreMemoryDTO,
    GraphDTO, GraphNodeDTO, GraphEdgeDTO,
    PreferenceTagDTO, DialogueMessageDTO,
)

router = APIRouter(prefix="/memory", tags=["memory"])


# ── helpers ───────────────────────────────────────────────
def _block_to_dto(b: dict, include_vectors: bool = False) -> MemoryBlockDTO:
    prefs = []
    for p in b.get("potential_preferences", []):
        if isinstance(p, dict):
            prefs.append(PreferenceTagDTO(tag=p.get("tag", ""), intensity=float(p.get("intensity", 0.5))))
        else:
            prefs.append(PreferenceTagDTO(tag=str(p)))
    dialogues = [DialogueMessageDTO(role=m.get("role", ""), content=m.get("content", ""))
                 for m in b.get("raw_dialogues", []) if "role" in m]
    dto = MemoryBlockDTO(
        block_id=b["block_id"],
        timestamp=b.get("timestamp", ""),
        overview=b.get("overview", ""),
        is_consolidated=b.get("is_consolidated", False),
        encounter_count=float(b.get("encounter_count", 1.0)),
        potential_preferences=prefs,
        raw_dialogues=dialogues,
    )
    if include_vectors:
        dto.overview_vector = b.get("overview_vector")
        dto.sparse_vector = b.get("sparse_vector")
    return dto


# ── Memory Blocks ─────────────────────────────────────────
@router.get("/blocks", response_model=list[MemoryBlockDTO])
async def list_blocks(include_vectors: bool = Query(False)):
    ms = get_memory_sys()
    return [_block_to_dto(b, include_vectors) for b in ms.memory_blocks]


@router.get("/blocks/{block_id}", response_model=MemoryBlockDTO)
async def get_block(block_id: str, include_vectors: bool = Query(False)):
    ms = get_memory_sys()
    for b in ms.memory_blocks:
        if b["block_id"] == block_id:
            return _block_to_dto(b, include_vectors)
    raise HTTPException(404, detail=f"Block {block_id} not found")


@router.put("/blocks/{block_id}", response_model=MemoryBlockDTO)
async def update_block(block_id: str, body: BlockUpdateRequest):
    ms = get_memory_sys()
    async with db_write_lock:
        ok = await asyncio.to_thread(ms.update_memory_block, block_id, body.new_overview)
    if not ok:
        raise HTTPException(404, detail=f"Block {block_id} not found or vector engine not ready")
    for b in ms.memory_blocks:
        if b["block_id"] == block_id:
            return _block_to_dto(b)
    raise HTTPException(500, detail="Block updated but not found in memory")


@router.delete("/blocks/{block_id}")
async def delete_block(block_id: str):
    ms = get_memory_sys()
    async with db_write_lock:
        before = len(ms.memory_blocks)
        ms.memory_blocks = [b for b in ms.memory_blocks if b["block_id"] != block_id]
        if len(ms.memory_blocks) == before:
            raise HTTPException(404, detail=f"Block {block_id} not found")
        await asyncio.to_thread(ms.storage.save_db, ms.db_path, ms.memory_blocks)
    return {"status": "deleted", "block_id": block_id}


# ── Search ────────────────────────────────────────────────
@router.post("/search", response_model=list[SearchResultDTO])
async def search_blocks(body: SearchRequest):
    ms = get_memory_sys()
    results = await asyncio.to_thread(
        ms.search_blocks,
        body.query, body.combined_keywords,
        body.top_k, body.alpha, 0.5,
        body.threshold, body.hard_base,
    )
    out = []
    for b in results:
        dto = _block_to_dto(b)
        sr = SearchResultDTO(**dto.model_dump())
        sr._debug_score = b.get("_debug_score", 0)
        sr._debug_recency = b.get("_debug_recency", 0)
        sr._debug_raw_sim = b.get("_debug_raw_sim", 0)
        sr._debug_sparse_raw = b.get("_debug_sparse_raw", 0)
        sr._debug_hard_base = b.get("_debug_hard_base", 0)
        sr._debug_sparse_norm = b.get("_debug_sparse_norm", 0)
        sr._debug_importance = b.get("_debug_importance", 0)
        out.append(sr)
    return out


# ── Core Memories ─────────────────────────────────────────
@router.get("/core", response_model=list[CoreMemoryDTO])
async def list_core():
    ms = get_memory_sys()
    return [CoreMemoryDTO(
        core_id=c["core_id"], timestamp=c.get("timestamp", ""),
        insight=c.get("insight", ""), encounter_count=float(c.get("encounter_count", 1.0)),
    ) for c in ms.core_memories]


@router.post("/core/search")
async def search_core(body: CoreSearchRequest):
    ms = get_memory_sys()
    results = await asyncio.to_thread(ms.search_core_memories, body.query, body.top_k, body.threshold)
    return results


@router.delete("/core/{core_id}")
async def delete_core(core_id: str):
    ms = get_memory_sys()
    async with db_write_lock:
        before = len(ms.core_memories)
        ms.core_memories = [c for c in ms.core_memories if c["core_id"] != core_id]
        if len(ms.core_memories) == before:
            raise HTTPException(404, detail=f"Core memory {core_id} not found")
        # 從 DB 刪除
        import sqlite3
        conn = sqlite3.connect(ms.db_path)
        conn.execute("DELETE FROM core_memories WHERE core_id = ?", (core_id,))
        conn.commit()
        conn.close()
    return {"status": "deleted", "core_id": core_id}


# ── Graph（力導向圖用） ──────────────────────────────────
@router.get("/graph", response_model=GraphDTO)
async def get_graph(similarity_threshold: float = Query(0.6)):
    ms = get_memory_sys()
    nodes: list[GraphNodeDTO] = []
    edges: list[GraphEdgeDTO] = []

    blocks = ms.memory_blocks
    cores = ms.core_memories

    for b in blocks:
        label = b["overview"].split("\n")[0] if "\n" in b["overview"] else b["overview"]
        nodes.append(GraphNodeDTO(id=b["block_id"], type="block", label=label,
                                   weight=float(b.get("encounter_count", 1.0))))
    for c in cores:
        nodes.append(GraphNodeDTO(id=c["core_id"], type="core", label=c["insight"],
                                   weight=float(c.get("encounter_count", 1.0))))

    # 計算 block-block 邊
    for i in range(len(blocks)):
        for j in range(i + 1, len(blocks)):
            sim = ms.cosine_similarity(blocks[i].get("overview_vector", []),
                                       blocks[j].get("overview_vector", []))
            if sim >= similarity_threshold:
                edges.append(GraphEdgeDTO(source=blocks[i]["block_id"],
                                          target=blocks[j]["block_id"], weight=round(sim, 3)))

    # 計算 block-core 邊
    for b in blocks:
        for c in cores:
            sim = ms.cosine_similarity(b.get("overview_vector", []),
                                       c.get("insight_vector", []))
            if sim >= similarity_threshold:
                edges.append(GraphEdgeDTO(source=b["block_id"],
                                          target=c["core_id"], weight=round(sim, 3)))

    return GraphDTO(nodes=nodes, edges=edges)


# ── Query Expansion ───────────────────────────────────────
@router.post("/expand-query")
async def expand_query(body: ExpandQueryRequest):
    ms = get_memory_sys()
    rtr = get_router()
    result = await asyncio.to_thread(ms.expand_query, body.query, body.recent_history, rtr, "expand")
    return result
