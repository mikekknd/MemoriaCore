import json
import os
import uuid
from typing import Dict, Any, List
from core.prompt_manager import get_prompt_manager
from core.system_logger import SystemLogger

class CharacterManager:
    """管理動態角色設定，包含存取 characters.json 及透過 LLM 生成設定"""
    
    def __init__(self, characters_file="characters.json"):
        self.characters_file = characters_file
        self._ensure_file_exists()

    def _ensure_file_exists(self):
        if not os.path.exists(self.characters_file):
            default_chars = [
                {
                    "character_id": "default",
                    "name": "預設助理",
                    "system_prompt": "你是一個具備情境記憶與核心認知的 AI 助理。",
                    "visual_prompt": "具備情境記憶與核心認知的 AI 助理形象，乾淨、親切、專業的角色肖像。",
                    "evolved_prompt": None,
                    "metrics": ["professionalism", "friendliness"],
                    "allowed_tones": ["Neutral", "Happy", "Professional", "Friendly"],
                    "reply_rules": "Traditional Chinese. NO EMOJIS.",
                    "tts_rules": ""
                }
            ]
            self.save_characters(default_chars)

    def load_characters(self) -> List[Dict[str, Any]]:
        try:
            with open(self.characters_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

    def save_characters(self, characters: List[Dict[str, Any]]):
        with open(self.characters_file, "w", encoding="utf-8") as f:
            json.dump(characters, f, ensure_ascii=False, indent=2)

    def get_character(self, character_id: str) -> Dict[str, Any] | None:
        chars = self.load_characters()
        for c in chars:
            if c["character_id"] == character_id:
                return c
        return None

    def upsert_character(self, character_data: Dict[str, Any]):
        chars = self.load_characters()
        char_id = character_data.get("character_id")
        if not char_id:
            char_id = str(uuid.uuid4())
            character_data["character_id"] = char_id
        
        updated = False
        for i, c in enumerate(chars):
            if c["character_id"] == char_id:
                chars[i] = character_data
                updated = True
                break
        
        if not updated:
            chars.append(character_data)
            
        self.save_characters(chars)
        return char_id

    def delete_character(self, character_id: str):
        chars = self.load_characters()
        chars = [c for c in chars if c["character_id"] != character_id]
        self.save_characters(chars)

    def set_evolved_prompt(self, character_id: str, content: str, persona_face: str = "public") -> bool:
        """將 PersonaProbe 產出的演化人設寫入指定角色的 evolved_prompt 欄位（per-face）。

        evolved_prompt 儲存格式為 dict：{"public": str|None, "private": str|None}。
        舊格式（純字串）讀取時自動視為 public，寫入時一律轉為 dict。
        回傳 True 表示成功，False 表示找不到角色。
        """
        chars = self.load_characters()
        for i, c in enumerate(chars):
            if c["character_id"] == character_id:
                ep = c.get("evolved_prompt")
                if isinstance(ep, str):
                    ep = {"public": ep, "private": None}
                elif not isinstance(ep, dict):
                    ep = {"public": None, "private": None}
                ep[persona_face] = content
                chars[i]["evolved_prompt"] = ep
                self.save_characters(chars)
                return True
        return False

    def clear_evolved_prompt(self, character_id: str, persona_face: str | None = None) -> bool:
        """清除指定角色的 evolved_prompt，還原為使用原始 system_prompt。

        persona_face=None → 清除所有 face；否則只清指定 face。
        回傳 True 表示成功，False 表示找不到角色。
        """
        chars = self.load_characters()
        for i, c in enumerate(chars):
            if c["character_id"] == character_id:
                if persona_face is None:
                    chars[i]["evolved_prompt"] = None
                else:
                    ep = c.get("evolved_prompt")
                    if isinstance(ep, dict):
                        ep[persona_face] = None
                        chars[i]["evolved_prompt"] = ep
                    else:
                        chars[i]["evolved_prompt"] = None
                self.save_characters(chars)
                return True
        return False

    def get_effective_prompt(self, character: Dict[str, Any], persona_face: str = "public") -> str:
        """回傳角色實際使用的 System Prompt：優先對應 face 的 evolved_prompt，否則 system_prompt。

        相容舊版純字串格式（視為 public face）。
        """
        ep = character.get("evolved_prompt")
        if isinstance(ep, dict):
            return ep.get(persona_face) or character.get("system_prompt", "")
        # 舊格式向後相容：字串只服務 public face
        if isinstance(ep, str) and ep:
            return ep if persona_face == "public" else character.get("system_prompt", "")
        return character.get("system_prompt", "")

    def get_active_character(self, active_id: str = "default") -> Dict[str, Any]:
        """如果沒有設定，預設回傳第一個或 default"""
        chars = self.load_characters()
        for c in chars:
            if c.get("character_id") == active_id:
                return c
        return chars[0] if chars else {}

    def generate_character_profile(self, description: str, router, task_key="character_gen") -> Dict[str, Any]:
        """
        透過 LLM 生成動態角色的屬性草稿
        """
        GENERATE_SCHEMA = {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "角色的名稱"},
                "system_prompt": {"type": "string", "description": "核心人格與世界觀設定的 System Prompt"},
                "reply_rules": {
                    "type": "string",
                    "description": "回覆文字的格式與語氣規定（例如必須說繁體中文、不准用 Emoji、句尾要加喵 等），同時套用於 reply 欄位（字幕文字）"
                },
                "visual_prompt": {
                    "type": "string",
                    "description": "角色外觀專用的圖片生成提示詞。描述可視覺化元素，例如物種、髮色、眼睛、服裝、體型、年齡感、配件、整體畫風。禁止包含對話規則、人格分析或不可視覺化的抽象心理描述。"
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
            "required": ["name", "system_prompt", "reply_rules", "visual_prompt", "tts_rules", "tts_language"]
        }

        prompt = get_prompt_manager().get("character_generate").format(
            description=description
        )

        api_messages = [{"role": "user", "content": prompt}]

        try:
            parsed = router.generate_json(task_key, api_messages, schema=GENERATE_SCHEMA, temperature=0.7)
            if not parsed:
                SystemLogger.log_error("CharacterGenerate", f"LLM 回傳空 JSON。Prompt 前100字: {prompt[:100]!r}")
                return {"error": "Invalid JSON format"}
            return parsed
        except Exception as e:
            SystemLogger.log_error("CharacterGenerate", f"例外: {e} | Prompt 前100字: {prompt[:100]!r}")
            return {"error": str(e)}
