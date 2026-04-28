"""使用者畫像 CRUD + 語意搜尋端點"""
import asyncio
from fastapi import APIRouter, Depends, HTTPException, Query
from api.dependencies import get_current_user, get_memory_sys, get_storage, get_embed_model, db_write_lock
from api.models.requests import ProfileUpsertRequest, ProfileSearchRequest
from api.models.responses import ProfileFactDTO, ProfileSearchResultDTO

router = APIRouter(prefix="/profile", tags=["profile"])


def _visibility_filter_for(user: dict) -> list[str]:
    return ["private", "public"] if user.get("role") == "admin" else ["public"]


@router.get("", response_model=list[ProfileFactDTO])
async def list_profiles(
    include_tombstones: bool = Query(False),
    current_user: dict = Depends(get_current_user),
):
    ms = get_memory_sys()
    sto = get_storage()
    if not ms.db_path:
        return []
    profiles = await asyncio.to_thread(
        sto.load_all_profiles,
        ms.db_path,
        include_tombstones,
        user_id=str(current_user["id"]),
        visibility_filter=_visibility_filter_for(current_user),
    )
    return [ProfileFactDTO(**p) for p in profiles]


@router.get("/static-prompt")
async def static_prompt(current_user: dict = Depends(get_current_user)):
    ms = get_memory_sys()
    text = await asyncio.to_thread(
        ms.get_static_profile_prompt,
        user_id=str(current_user["id"]),
        visibility_filter=_visibility_filter_for(current_user),
    )
    return {"prompt": text}


@router.get("/{fact_key}", response_model=list[ProfileFactDTO])
async def get_profile(fact_key: str, current_user: dict = Depends(get_current_user)):
    ms = get_memory_sys()
    sto = get_storage()
    if not ms.db_path:
        raise HTTPException(404, detail="Database not initialized")
    rows = await asyncio.to_thread(sto.get_profile_by_key, ms.db_path, fact_key, user_id=str(current_user["id"]))
    if not rows:
        raise HTTPException(404, detail=f"Profile fact '{fact_key}' not found")
    return [ProfileFactDTO(**r) for r in rows]


@router.put("/{fact_key}", response_model=ProfileFactDTO)
async def upsert_profile(
    fact_key: str,
    body: ProfileUpsertRequest,
    current_user: dict = Depends(get_current_user),
):
    ms = get_memory_sys()
    sto = get_storage()
    if not ms.db_path:
        raise HTTPException(503, detail="Database not initialized")
    async with db_write_lock:
        await asyncio.to_thread(
            sto.upsert_profile, ms.db_path, fact_key,
            body.fact_value, body.category, body.source_context, body.confidence,
            user_id=str(current_user["id"]),
            visibility="private" if current_user.get("role") == "admin" else "public",
        )
    # 重新載入快取
    await asyncio.to_thread(ms.load_user_profile)
    row = await asyncio.to_thread(
        sto.get_profile_by_key,
        ms.db_path,
        fact_key,
        body.fact_value,
        user_id=str(current_user["id"]),
    )
    return ProfileFactDTO(**row)


@router.delete("/{fact_key}")
async def delete_profile(fact_key: str, current_user: dict = Depends(get_current_user)):
    ms = get_memory_sys()
    sto = get_storage()
    if not ms.db_path:
        raise HTTPException(503, detail="Database not initialized")
    async with db_write_lock:
        await asyncio.to_thread(sto.delete_profile, ms.db_path, fact_key, user_id=str(current_user["id"]))
    await asyncio.to_thread(ms.load_user_profile)
    return {"status": "deleted", "fact_key": fact_key}


@router.post("/search", response_model=list[ProfileSearchResultDTO])
async def search_profiles(body: ProfileSearchRequest, current_user: dict = Depends(get_current_user)):
    ms = get_memory_sys()
    results = await asyncio.to_thread(
        ms.search_profile_by_query,
        body.query,
        body.top_k,
        body.threshold,
        user_id=str(current_user["id"]),
        visibility_filter=_visibility_filter_for(current_user),
    )
    return [ProfileSearchResultDTO(**r) for r in results]
