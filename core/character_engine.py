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
                    "metrics": ["professionalism", "friendliness"],
                    "allowed_tones": ["Neutral", "Happy", "Professional", "Friendly"],
                    "speech_rules": "Traditional Chinese. NO EMOJIS."
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
                "metrics": {
                    "type": "array", 
                    "items": {"type": "string"}, 
                    "description": "2到4個英文變數名稱，代表這個角色隨時變動的心理指標，例如 'shyness', 'affection'"
                },
                "allowed_tones": {
                    "type": "array", 
                    "items": {
                        "type": "string",
                        "enum": ["Neutral", "Happy", "Sad", "Angry", "Fear", "Surprise", "Disgust", "Shame", "Tense"]
                    },
                    "description": "這個角色可能會切換的情緒或語氣，必須嚴格從以下標籤挑選：Neutral, Happy, Sad, Angry, Fear, Surprise, Disgust, Shame, Tense"
                },
                "speech_rules": {
                    "type": "string", 
                    "description": "針對這個角色講話的強制規定或口癖（例如必須說繁體中文、不准用 Emoji、句尾要加喵 等）"
                },
                "tts_language": {
                    "type": "string",
                    "description": "如果角色發音語言與字幕不同，請填寫此欄位（例如 '日文', '英文'）。若無需雙語分離則留空字串。"
                }
            },
            "required": ["name", "system_prompt", "metrics", "allowed_tones", "speech_rules", "tts_language"]
        }

        prompt = get_prompt_manager().get("character_generate").format(
            description=description
        )

        api_messages = [{"role": "user", "content": prompt}]

        try:
            parsed = router.generate_json(task_key, api_messages, schema=GENERATE_SCHEMA, temperature=0.7)
            if not parsed:
                return {"error": "Invalid JSON format"}
            return parsed
        except Exception as e:
            SystemLogger.log_error("CharacterGenerate", str(e))
            return {"error": str(e)}
