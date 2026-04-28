from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from api.dependencies import get_bot_registry, get_character_manager, get_router, get_storage
from core.system_logger import SystemLogger
from probe_engine import FAST_PERSONA_BEHAVIORAL_TEMPLATE
import asyncio
import json

router = APIRouter(prefix="/character", tags=["character"])

# Requests / Responses
class CharacterProfileDTO(BaseModel):
    character_id: Optional[str] = None
    name: str
    system_prompt: str
    visual_prompt: str = ""
    evolved_prompt: Optional[str | Dict[str, Optional[str]]] = None
    reply_rules: str = ""
    tts_rules: str = ""
    tts_language: Optional[str] = None

class GenerateProfileRequest(BaseModel):
    description: str

class GenerateFromSeedRequest(BaseModel):
    description: str
    existing_persona: str = ""

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
    refs = get_bot_registry().configs_using_character(character_id, get_storage().load_prefs())
    if refs:
        bot_ids = ", ".join(c.get("bot_id", "") for c in refs)
        raise HTTPException(
            status_code=400,
            detail=f"此角色仍被 Bot 設定使用，請先修改或刪除 Bot：{bot_ids}",
        )
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
    try:
        res = await asyncio.to_thread(mgr.generate_character_profile, req.description, llm_router)
    except Exception as e:
        SystemLogger.log_error("CharacterGenerate", f"執行失敗: {e}")
        return {"error": f"執行失敗: {e}"}
    if "error" in res:
         return {"error": res["error"]}
    return res


@router.post("/generate-from-seed")
async def generate_from_existing_persona(req: GenerateFromSeedRequest):
    """
    使用現有角色資料（system_prompt / evolved_prompt）當作人格種子，
    透過 PersonaProbe 快速人格生成流程（build_fast_persona_complete_prompt）
    產生新的角色草稿。
    """
    llm_router = get_router()

    # ── PersonaProbe 快速人格生成 system prompt ──
    base_system = (
        "你是一個人格設計師，專門為語音輸出（TTS）的 LLM 角色扮演設計高品質的行為規格書。\n\n"
        "根據下方的原始人格種子和校準問答，填寫指定的行為模板。\n\n"
        "【最重要：所有行為描述必須是「語言行為」，不可是「身體動作」】\n"
        "這份規格書的輸出將透過語音播放，聽眾只能聽到說出來的話，看不到任何動作。\n"
        "因此，所有情緒狀態和性格特質必須轉化為「話語本身的結構變化」來承載，\n"
        "而不是描述身體動作（如「身體僵住」、「尾巴搖擺」、「縮小身體」）。\n\n"
        "「語言行為」的具體分類（用這些維度描述，不用身體動作）：\n"
        "  • 句式轉換：陳述句 → 疑問句、長句 → 短句、完整句 → 片段\n"
        "  • 停頓模式：回應前停頓多久、在哪種情境下沉默後才開口\n"
        "  • 話題主動性：主動追問對方 / 被動等待 / 突然把話題岔走\n"
        "  • 轉移策略：不舒服時用問題轉移 / 引入一個具體的新話題 / 把話說得很短就停\n"
        "  • 回應密度：展開說 / 只給一兩個字 / 反問代替回答\n\n"
        "填寫規則：\n"
        "1. 基本設定原文保留：原始種子的具體身份、外觀、物種、特殊限制不可抽象化。\n"
        "2. 感知框架一句話：描述這個角色接收外來刺激的本能傾向，用行為傾向表達，\n"
        "   不可寫「用溫柔的心看世界」這類空洞的世界觀陳述。\n"
        "3. 核心矛盾一句話：描述矛盾如何透過語言習慣無意識顯露，\n"
        "   例如「越在意越故意輕描淡寫」，不可寫抽象的「想被愛又怕受傷」。\n"
        "4. 禁止身體動作描述：不可出現「身體僵住」、「眼神迴避」、「尾巴垂下」\n"
        "   等任何視覺性動作。把相同的情緒意圖轉換成語言行為。\n"
        "5. 禁止詞彙清單：不要列「習慣用的詞」或「口頭禪」，描述語言結構而非詞彙。\n"
        "6. 每個欄位必須角色專屬：填完後問自己「換個角色名字這段還成立嗎？」\n"
        "   如果成立就必須重寫。\n"
        "7. 強度校準描述語言節奏的變化幅度，不描述身體狀態。\n"
        "8. 硬性禁止針對這個角色最容易被語言堆疊過度展演的特質。\n\n"
        "輸出格式：只輸出合法 JSON 物件，不要有任何解釋、前言或 Markdown 外框。\n"
        "JSON 欄位必須符合下方 schema：\n"
        "- name：根據人格種子推導出的角色名稱。\n"
        "- system_prompt：填寫完整的 PersonaProbe 行為模板內容；請把完整模板文字放在這個字串欄位內。\n"
        "- visual_prompt：角色外觀專用的圖片生成提示詞，只描述可視覺化元素，不寫對話規則或抽象心理分析。\n"
        "- reply_rules：字幕文字的格式與語氣規定。\n"
        "- tts_rules：TTS 發音專用指引，無特殊需求請填空字串。\n"
        "- tts_language：若需要特定發音語言請填寫，否則填空字串。\n"
        "語言：繁體中文。"
    )

    user_content = (
        "【原始人格種子（使用者提供，必須完整保留在基本設定中）】\n"
        f"{req.existing_persona.strip()}\n\n"
        "請填寫以下行為模板。每個方括號內的說明文字都必須替換為針對上方這個具體角色的內容：\n\n"
        f"{FAST_PERSONA_BEHAVIORAL_TEMPLATE}"
    )

    GENERATE_SCHEMA = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "角色的名稱"},
            "system_prompt": {"type": "string", "description": "核心人格與世界觀設定的 System Prompt"},
            "visual_prompt": {
                "type": "string",
                "description": "角色外觀專用的圖片生成提示詞。描述物種、髮色、眼睛、服裝、配件、體型、年齡感與畫風等可視覺化元素。"
            },
            "reply_rules": {
                "type": "string",
                "description": "回覆文字的格式與語氣規定（例如必須說繁體中文、不准用 Emoji、句尾要加喵 等），同時套用於 reply 欄位（字幕文字）"
            },
            "tts_rules": {
                "type": "string",
                "description": "TTS 發音專用指引（例如發音腔調、停頓節奏、特定詞彙的讀音），僅注入 speech 欄位的生成提示。無特殊需求請留空字串。"
            },
            "tts_language": {
                "type": "string",
                "description": "如果角色發音語言與字幕不同，請填寫此欄位（例如 '日文', '英文'）。若無需雙語分離則留空字串。"
            }
        },
        "required": ["name", "system_prompt", "visual_prompt", "reply_rules", "tts_rules", "tts_language"]
    }

    api_messages = [
        {"role": "system", "content": base_system},
        {"role": "user", "content": user_content},
    ]

    try:
        parsed = await asyncio.to_thread(
            llm_router.generate_json,
            "character_gen",
            api_messages,
            GENERATE_SCHEMA,
            0.7,
        )
        if not parsed:
            SystemLogger.log_error("GenerateFromSeed", f"LLM 回傳空 JSON。Seed 前50字: {req.existing_persona[:50]!r}")
            return {"error": "LLM 回傳格式無效，請稍後再試"}
        return parsed
    except Exception as e:
        SystemLogger.log_error("GenerateFromSeed", f"例外: {e}")
        return {"error": str(e)}
