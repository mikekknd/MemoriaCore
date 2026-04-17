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

    def set_evolved_prompt(self, character_id: str, content: str) -> bool:
        """將 PersonaProbe 產出的演化人設寫入指定角色的 evolved_prompt 欄位。
        回傳 True 表示成功，False 表示找不到角色。
        """
        chars = self.load_characters()
        for i, c in enumerate(chars):
            if c["character_id"] == character_id:
                chars[i]["evolved_prompt"] = content
                self.save_characters(chars)
                return True
        return False

    def clear_evolved_prompt(self, character_id: str) -> bool:
        """清除指定角色的 evolved_prompt，還原為使用原始 system_prompt。
        回傳 True 表示成功，False 表示找不到角色。
        """
        chars = self.load_characters()
        for i, c in enumerate(chars):
            if c["character_id"] == character_id:
                chars[i]["evolved_prompt"] = None
                self.save_characters(chars)
                return True
        return False

    def get_effective_prompt(self, character: Dict[str, Any]) -> str:
        """回傳角色實際使用的 System Prompt：優先 evolved_prompt，否則 system_prompt。"""
        return character.get("evolved_prompt") or character.get("system_prompt", "")

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
            "required": ["name", "system_prompt", "metrics", "allowed_tones", "reply_rules", "tts_rules", "tts_language"]
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
