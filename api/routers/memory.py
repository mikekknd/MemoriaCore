"""記憶區塊、核心認知、圖譜、查詢擴展端點"""
import asyncio
from fastapi import APIRouter, Depends, HTTPException, Query
from api.dependencies import (
    get_current_user, require_admin_user, get_memory_sys, get_storage,
    get_router, get_embed_model, db_write_lock,
)
from api.models.requests import SearchRequest, CoreSearchRequest, ExpandQueryRequest, BlockUpdateRequest
from api.models.responses import (
    MemoryBlockDTO, SearchResultDTO, CoreMemoryDTO,
    GraphDTO, GraphNodeDTO, GraphEdgeDTO,
    PreferenceTagDTO, DialogueMessageDTO,
)

router = APIRouter(prefix="/memory", tags=["memory"])


# ── helpers ───────────────────────────────────────────────
def _visibility_filter_for(user: dict) -> list[str]:
    return ["private", "public"] if user.get("role") == "admin" else ["public"]


def _blocks_for_user(ms, user: dict) -> list[dict]:
    blocks: list[dict] = []
    for vis in _visibility_filter_for(user):
        blocks.extend(ms._get_memory_blocks(str(user["id"]), "default", vis))
    return blocks


def _cores_for_user(ms, user: dict) -> list[dict]:
    cores: list[dict] = []
    for vis in _visibility_filter_for(user):
        cores.extend(ms._get_core_memories(str(user["id"]), "default", vis))
    return cores


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


# ── Admin Inspect（read-only, scope-aware）──────────────────
def _inspect_visibility_filter(visibility: str) -> list[str] | None:
    normalized = (visibility or "all").strip().lower()
    if normalized == "all":
        return None
    if normalized in ("public", "private"):
        return [normalized]
    raise HTTPException(422, detail="visibility must be one of: all, public, private")


def _require_memory_db(ms):
    if not ms.db_path:
        raise HTTPException(503, detail="Database not initialized")


def _runtime_scope_stats(scopes: dict) -> dict[str, dict]:
    stats: dict[str, dict] = {}

    def ensure(user_id: str) -> dict:
        if user_id not in stats:
            stats[user_id] = {
                "memory_blocks": 0,
                "core_memories": 0,
                "profiles": 0,
                "topics": 0,
            }
        return stats[user_id]

    for row in scopes.get("counts", {}).get("memory_blocks", []):
        ensure(str(row.get("user_id", "default")))["memory_blocks"] += int(row.get("count", 0))
    for row in scopes.get("counts", {}).get("core_memories", []):
        ensure(str(row.get("user_id", "default")))["core_memories"] += int(row.get("count", 0))
    for row in scopes.get("counts", {}).get("user_profile", []):
        ensure(str(row.get("user_id", "default")))["profiles"] += int(row.get("count", 0))
    for row in scopes.get("counts", {}).get("topic_cache", []):
        ensure(str(row.get("user_id", "default")))["topics"] += int(row.get("count", 0))
    return stats


@router.get("/inspect/scopes")
async def inspect_scopes(current_user: dict = Depends(require_admin_user)):
    ms = get_memory_sys()
    _require_memory_db(ms)
    storage = get_storage()
    scopes = await asyncio.to_thread(storage.inspect_memory_scopes, ms.db_path)
    users = await asyncio.to_thread(storage.list_users_basic)
    user_lookup = {str(u["id"]): u for u in users}
    runtime_stats = _runtime_scope_stats(scopes)
    scope_user_ids = set(scopes.get("user_ids", [])) | set(user_lookup.keys())
    scopes["users"] = [
        {
            "user_id": user_id,
            "username": (user_lookup.get(user_id) or {}).get("username", ""),
            "nickname": (user_lookup.get(user_id) or {}).get("nickname", ""),
            "role": (user_lookup.get(user_id) or {}).get("role", ""),
            "stats": runtime_stats.get(user_id, {}),
        }
        for user_id in sorted(scope_user_ids, key=lambda x: (not x.isdigit(), x))
    ]
    return scopes


@router.get("/inspect/blocks")
async def inspect_blocks(
    user_id: str = Query(...),
    character_id: str = Query(...),
    visibility: str = Query("all"),
    limit: int = Query(200, ge=1, le=1000),
    include_dialogues: bool = Query(False),
    current_user: dict = Depends(require_admin_user),
):
    ms = get_memory_sys()
    _require_memory_db(ms)
    storage = get_storage()
    return await asyncio.to_thread(
        storage.inspect_memory_blocks,
        ms.db_path,
        user_id,
        character_id,
        _inspect_visibility_filter(visibility),
        limit,
        include_dialogues,
    )


@router.get("/inspect/core")
async def inspect_core(
    user_id: str = Query(...),
    character_id: str = Query(...),
    visibility: str = Query("all"),
    limit: int = Query(200, ge=1, le=1000),
    current_user: dict = Depends(require_admin_user),
):
    ms = get_memory_sys()
    _require_memory_db(ms)
    storage = get_storage()
    return await asyncio.to_thread(
        storage.inspect_core_memories,
        ms.db_path,
        user_id,
        character_id,
        _inspect_visibility_filter(visibility),
        limit,
    )


@router.get("/inspect/profile")
async def inspect_profile(
    user_id: str = Query(...),
    visibility: str = Query("all"),
    include_tombstones: bool = Query(False),
    limit: int = Query(200, ge=1, le=1000),
    current_user: dict = Depends(require_admin_user),
):
    ms = get_memory_sys()
    _require_memory_db(ms)
    storage = get_storage()
    return await asyncio.to_thread(
        storage.inspect_profiles,
        ms.db_path,
        user_id,
        _inspect_visibility_filter(visibility),
        include_tombstones,
        limit,
    )


@router.get("/inspect/topics")
async def inspect_topics(
    user_id: str = Query(...),
    character_id: str = Query(...),
    visibility: str = Query("all"),
    include_global: bool = Query(False),
    only_unmentioned: bool = Query(False),
    limit: int = Query(200, ge=1, le=1000),
    current_user: dict = Depends(require_admin_user),
):
    ms = get_memory_sys()
    _require_memory_db(ms)
    storage = get_storage()
    return await asyncio.to_thread(
        storage.inspect_topics,
        ms.db_path,
        user_id,
        character_id,
        _inspect_visibility_filter(visibility),
        include_global,
        only_unmentioned,
        limit,
    )


# ── Memory Blocks ─────────────────────────────────────────
# 以下端點透過 back-compat property，僅操作 (user_id='default', visibility='public') 範圍。
# 非 default 使用者的資料不可見。如需跨 user/visibility 管理，需擴充為接受 query params。
@router.get("/blocks", response_model=list[MemoryBlockDTO])
async def list_blocks(include_vectors: bool = Query(False), current_user: dict = Depends(get_current_user)):
    ms = get_memory_sys()
    return [_block_to_dto(b, include_vectors) for b in _blocks_for_user(ms, current_user)]


@router.get("/blocks/{block_id}", response_model=MemoryBlockDTO)
async def get_block(
    block_id: str,
    include_vectors: bool = Query(False),
    current_user: dict = Depends(get_current_user),
):
    ms = get_memory_sys()
    for b in _blocks_for_user(ms, current_user):
        if b["block_id"] == block_id:
            return _block_to_dto(b, include_vectors)
    raise HTTPException(404, detail=f"Block {block_id} not found")


@router.put("/blocks/{block_id}", response_model=MemoryBlockDTO)
async def update_block(block_id: str, body: BlockUpdateRequest, current_user: dict = Depends(get_current_user)):
    ms = get_memory_sys()
    async with db_write_lock:
        ok = False
        for vis in _visibility_filter_for(current_user):
            ok = await asyncio.to_thread(
                ms.update_memory_block,
                block_id,
                body.new_overview,
                user_id=str(current_user["id"]),
                character_id="default",
                visibility=vis,
            )
            if ok:
                break
    if not ok:
        raise HTTPException(404, detail=f"Block {block_id} not found or vector engine not ready")
    for b in _blocks_for_user(ms, current_user):
        if b["block_id"] == block_id:
            return _block_to_dto(b)
    raise HTTPException(500, detail="Block updated but not found in memory")


@router.delete("/blocks/{block_id}")
async def delete_block(block_id: str, current_user: dict = Depends(get_current_user)):
    ms = get_memory_sys()
    async with db_write_lock:
        found = False
        user_id = str(current_user["id"])
        for vis in _visibility_filter_for(current_user):
            blocks = ms._get_memory_blocks(user_id, "default", vis)
            before = len(blocks)
            blocks[:] = [b for b in blocks if b["block_id"] != block_id]
            if len(blocks) < before:
                found = True
                await asyncio.to_thread(
                    ms.storage.save_db,
                    ms.db_path,
                    blocks,
                    user_id=user_id,
                    character_id="default",
                    visibility=vis,
                )
                break
        if not found:
            raise HTTPException(404, detail=f"Block {block_id} not found")
    return {"status": "deleted", "block_id": block_id}


# ── Search ────────────────────────────────────────────────
@router.post("/search", response_model=list[SearchResultDTO])
async def search_blocks(body: SearchRequest, current_user: dict = Depends(get_current_user)):
    ms = get_memory_sys()
    results = await asyncio.to_thread(
        ms.search_blocks,
        body.query, body.combined_keywords,
        body.top_k, body.alpha, 0.5,
        body.threshold, body.hard_base,
        user_id=str(current_user["id"]),
        character_id="default",
        visibility_filter=_visibility_filter_for(current_user),
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
# 同 Memory Blocks：僅限 (user_id='default', visibility='public') 範圍。
@router.get("/core", response_model=list[CoreMemoryDTO])
async def list_core(current_user: dict = Depends(get_current_user)):
    ms = get_memory_sys()
    return [CoreMemoryDTO(
        core_id=c["core_id"], timestamp=c.get("timestamp", ""),
        insight=c.get("insight", ""), encounter_count=float(c.get("encounter_count", 1.0)),
    ) for c in _cores_for_user(ms, current_user)]


@router.post("/core/search")
async def search_core(body: CoreSearchRequest, current_user: dict = Depends(get_current_user)):
    ms = get_memory_sys()
    results = await asyncio.to_thread(
        ms.search_core_memories,
        body.query,
        body.top_k,
        body.threshold,
        user_id=str(current_user["id"]),
        character_id="default",
        visibility_filter=_visibility_filter_for(current_user),
    )
    return results


@router.delete("/core/{core_id}")
async def delete_core(
    core_id: str,
    user_id: str = Query("default"),
    character_id: str = Query("default"),
    current_user: dict = Depends(get_current_user),
):
    ms = get_memory_sys()
    async with db_write_lock:
        # 從所有已快取的 visibility slot 移除
        found = False
        user_id = str(current_user["id"])
        character_id = "default"
        for vis in _visibility_filter_for(current_user):
            cache_key = (user_id, character_id, vis)
            if cache_key in ms._core_memories_cache:
                before = len(ms._core_memories_cache[cache_key])
                ms._core_memories_cache[cache_key] = [
                    c for c in ms._core_memories_cache[cache_key]
                    if c["core_id"] != core_id
                ]
                if len(ms._core_memories_cache[cache_key]) < before:
                    found = True
        if not found:
            raise HTTPException(404, detail=f"Core memory {core_id} not found")
        await asyncio.to_thread(
            ms.storage.delete_core_memory, ms.db_path, user_id, character_id, core_id
        )
    return {"status": "deleted", "core_id": core_id}


# ── Graph（力導向圖用） ──────────────────────────────────
@router.get("/graph", response_model=GraphDTO)
async def get_graph(
    similarity_threshold: float = Query(0.6),
    current_user: dict = Depends(get_current_user),
):
    ms = get_memory_sys()
    nodes: list[GraphNodeDTO] = []
    edges: list[GraphEdgeDTO] = []

    blocks = _blocks_for_user(ms, current_user)
    cores = _cores_for_user(ms, current_user)

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
