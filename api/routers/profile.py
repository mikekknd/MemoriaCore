"""使用者畫像 CRUD + 語意搜尋端點"""
import asyncio
from fastapi import APIRouter, HTTPException, Query
from api.dependencies import get_memory_sys, get_storage, get_embed_model, db_write_lock
from api.models.requests import ProfileUpsertRequest, ProfileSearchRequest
from api.models.responses import ProfileFactDTO, ProfileSearchResultDTO

router = APIRouter(prefix="/profile", tags=["profile"])


@router.get("", response_model=list[ProfileFactDTO])
async def list_profiles(include_tombstones: bool = Query(False)):
    ms = get_memory_sys()
    sto = get_storage()
    if not ms.db_path:
        return []
    profiles = await asyncio.to_thread(sto.load_all_profiles, ms.db_path, include_tombstones)
    return [ProfileFactDTO(**p) for p in profiles]


@router.get("/static-prompt")
async def static_prompt():
    ms = get_memory_sys()
    text = await asyncio.to_thread(ms.get_static_profile_prompt)
    return {"prompt": text}


@router.get("/{fact_key}", response_model=ProfileFactDTO)
async def get_profile(fact_key: str):
    ms = get_memory_sys()
    sto = get_storage()
    if not ms.db_path:
        raise HTTPException(404, detail="Database not initialized")
    row = await asyncio.to_thread(sto.get_profile_by_key, ms.db_path, fact_key)
    if not row:
        raise HTTPException(404, detail=f"Profile fact '{fact_key}' not found")
    return ProfileFactDTO(**row)


@router.put("/{fact_key}", response_model=ProfileFactDTO)
async def upsert_profile(fact_key: str, body: ProfileUpsertRequest):
    ms = get_memory_sys()
    sto = get_storage()
    if not ms.db_path:
        raise HTTPException(503, detail="Database not initialized")
    async with db_write_lock:
        await asyncio.to_thread(
            sto.upsert_profile, ms.db_path, fact_key,
            body.fact_value, body.category, body.source_context, body.confidence,
        )
    # 重新載入快取
    await asyncio.to_thread(ms.load_user_profile)
    row = await asyncio.to_thread(sto.get_profile_by_key, ms.db_path, fact_key)
    return ProfileFactDTO(**row)


@router.delete("/{fact_key}")
async def delete_profile(fact_key: str):
    ms = get_memory_sys()
    sto = get_storage()
    if not ms.db_path:
        raise HTTPException(503, detail="Database not initialized")
    async with db_write_lock:
        await asyncio.to_thread(sto.delete_profile, ms.db_path, fact_key)
    await asyncio.to_thread(ms.load_user_profile)
    return {"status": "deleted", "fact_key": fact_key}


@router.post("/search", response_model=list[ProfileSearchResultDTO])
async def search_profiles(body: ProfileSearchRequest):
    ms = get_memory_sys()
    results = await asyncio.to_thread(
        ms.search_profile_by_query, body.query, body.top_k, body.threshold,
    )
    return [ProfileSearchResultDTO(**r) for r in results]
