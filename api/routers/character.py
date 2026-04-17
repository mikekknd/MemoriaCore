from fastapi import APIRouter
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from api.dependencies import get_character_manager, get_router
import asyncio

router = APIRouter(prefix="/character", tags=["character"])

# Requests / Responses
class CharacterProfileDTO(BaseModel):
    character_id: Optional[str] = None
    name: str
    system_prompt: str
    evolved_prompt: Optional[str] = None
    metrics: List[str]
    allowed_tones: List[str]
    reply_rules: str = ""
    tts_rules: str = ""
    tts_language: Optional[str] = None

class GenerateProfileRequest(BaseModel):
    description: str

@router.get("")
async def list_characters() -> List[Dict[str, Any]]:
    mgr = get_character_manager()
    return mgr.load_characters()

@router.get("/{character_id}")
async def get_character(character_id: str) -> Dict[str, Any]:
    mgr = get_character_manager()
    char = mgr.get_character(character_id)
    if not char:
        return {"error": "Character not found"}
    return char

@router.post("")
async def upsert_character(profile: CharacterProfileDTO):
    mgr = get_character_manager()
    char_id = mgr.upsert_character(profile.model_dump(exclude_none=True))
    return {"status": "success", "character_id": char_id}

@router.delete("/{character_id}")
async def delete_character(character_id: str):
    mgr = get_character_manager()
    mgr.delete_character(character_id)
    return {"status": "success"}


@router.delete("/{character_id}/evolved-prompt")
async def clear_evolved_prompt(character_id: str):
    """清除指定角色的 evolved_prompt，還原為使用原始 system_prompt。"""
    mgr = get_character_manager()
    found = mgr.clear_evolved_prompt(character_id)
    if not found:
        return {"status": "error", "message": "找不到指定角色"}
    return {"status": "cleared"}

@router.post("/generate")
async def generate_character(req: GenerateProfileRequest):
    mgr = get_character_manager()
    llm_router = get_router()
    # Call the LLM in the background thread since it's synchronous block
    res = await asyncio.to_thread(mgr.generate_character_profile, req.description, llm_router)
    if "error" in res:
         return {"error": res["error"]}
    return res
