# 【環境假設】：Python 3.12, numpy 庫可用。依賴 MemorySystem 的 embedding 基礎設施。
import json
import uuid
import os
import numpy as np
from datetime import datetime
from core.system_logger import SystemLogger
from core.prompt_manager import get_prompt_manager


class PersonalityEngine:
    """
    AI 雙層人格演化系統 — 核心引擎。
    負責：
    1. 讀取/寫入 ai_personality.md（可演化個性檔案）
    2. 從對話中提取 AI 自我觀察（LLM 驅動，無硬編碼規則）
    3. 觀察去重（embedding 語意比對）
    4. 反思觸發與合成（LLM 驅動個性檔案更新）
    """

    PERSONALITY_FILE = "ai_personality.md"
    OBS_DEDUP_THRESHOLD = 0.85  # 觀察去重的語意相似度閾值

    def __init__(self, memory_sys, storage):
        """
        Args:
            memory_sys: MemorySystem 實例（提供 embedding + cosine_similarity）
            storage: StorageManager 實例（提供 DB CRUD）
        """
        self.memory_sys = memory_sys
        self.storage = storage

    @property
    def db_path(self):
        return self.memory_sys.db_path

    # ==========================================
    # 個性檔案讀寫
    # ==========================================
    def get_personality_prompt(self):
        """讀取 ai_personality.md 內容，供系統提示注入"""
        if os.path.exists(self.PERSONALITY_FILE):
            try:
                with open(self.PERSONALITY_FILE, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    # 若檔案全是初始佔位符則不注入
                    if content and "尚未發展" not in content and "尚未觀察" not in content and "關係尚在建立" not in content:
                        return content
                    # 即使是初始狀態也回傳，讓 AI 知道自己有個性檔案
                    return content
            except Exception:
                pass
        return ""

    def save_personality(self, content):
        """手動編輯用，寫入 ai_personality.md"""
        with open(self.PERSONALITY_FILE, "w", encoding="utf-8") as f:
            f.write(content)

    def load_personality_raw(self):
        """讀取原始個性檔案內容"""
        if os.path.exists(self.PERSONALITY_FILE):
            with open(self.PERSONALITY_FILE, "r", encoding="utf-8") as f:
                return f.read()
        return ""

    # ==========================================
    # AI 自我觀察提取
    # ==========================================
    def extract_self_observations(self, reply_text, context_msgs, router, task_key="ai_observe"):
        """
        從 AI 回覆 + 對話上下文中提取 AI 的自我陳述。
        完全由 LLM 判斷，無預過濾，無硬編碼語言模式。

        Args:
            reply_text: AI 的回覆文本
            context_msgs: 最近的對話訊息列表 [{"role": ..., "content": ...}]
            router: LLMRouter 實例
            task_key: LLM 路由任務鍵

        Returns:
            list[dict]: 提取的觀察列表，每個觀察含 category, raw_statement, extracted_trait
        """
        if not reply_text or not reply_text.strip():
            return []

        context_text = "\n".join([f"{m['role']}: {m['content']}" for m in (context_msgs or [])[-6:]])

        OBSERVE_SCHEMA = {
            "type": "object",
            "properties": {
                "observations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "category": {"type": "string"},
                            "raw_statement": {"type": "string"},
                            "extracted_trait": {"type": "string"}
                        },
                        "required": ["category", "raw_statement", "extracted_trait"]
                    }
                }
            },
            "required": ["observations"]
        }

        prompt = get_prompt_manager().get("ai_self_observe").format(
            context_text=context_text, reply_text=reply_text
        )

        try:
            api_messages = [{"role": "user", "content": prompt}]
            try:
                raw_text = router.generate(task_key, api_messages, temperature=0.1, response_format=OBSERVE_SCHEMA)
            except Exception:
                raw_text = router.generate(task_key, api_messages, temperature=0.1)

            _start = raw_text.find('{')
            if _start == -1:
                return []
            parsed, _ = json.JSONDecoder().raw_decode(raw_text, _start)
            observations = parsed.get("observations", [])

            # 基礎驗證：確保必要欄位存在且非空
            valid = []
            for obs in observations:
                if (obs.get("category") and obs.get("raw_statement") and obs.get("extracted_trait")):
                    valid.append(obs)
            return valid

        except Exception as e:
            SystemLogger.log_error("personality_observe", str(e))
            return []

    # ==========================================
    # 觀察儲存（含 embedding 去重）
    # ==========================================
    def store_observation(self, obs, source_context=""):
        """
        將觀察存入 DB，含語意去重邏輯。
        若同類別中有語意相似的未反思觀察 → 合併（+encounter_count）。

        Args:
            obs: dict with category, raw_statement, extracted_trait
            source_context: 來源上下文摘要
        """
        if not self.db_path or not self.memory_sys.embed_provider:
            return

        trait_text = obs["extracted_trait"]
        category = obs["category"]

        # 生成 embedding
        features = self.memory_sys.embed_provider.get_embedding(text=trait_text, model=self.memory_sys.embed_model)
        new_dense = features.get("dense", [])
        if not new_dense:
            return

        # 檢查同類別未反思觀察的語意重複
        existing = self.storage.load_pending_observations(self.db_path)
        for ex in existing:
            if ex["category"] != category:
                continue
            ex_dense = ex.get("trait_vector", [])
            if not ex_dense:
                continue
            sim = self.memory_sys.cosine_similarity(new_dense, ex_dense)
            if sim >= self.OBS_DEDUP_THRESHOLD:
                # 語意重複 → 合併
                self.storage.increment_observation_count(self.db_path, ex["obs_id"])
                SystemLogger.log_system_event("personality_obs_merged", {
                    "existing_id": ex["obs_id"], "new_trait": trait_text,
                    "similarity": round(sim, 4), "new_count": ex["encounter_count"] + 1
                })
                return

        # 新觀察 → 插入
        obs_id = str(uuid.uuid4())
        self.storage.insert_ai_observation(
            self.db_path, obs_id, category,
            obs["raw_statement"], trait_text, new_dense, source_context
        )
        SystemLogger.log_system_event("personality_obs_added", {
            "obs_id": obs_id, "category": category, "trait": trait_text
        })

    # ==========================================
    # 反思觸發檢查
    # ==========================================
    def should_reflect(self, threshold=None):
        """
        檢查是否應觸發反思。

        Args:
            threshold: 反思閾值，若不指定則從 user_prefs 讀取

        Returns:
            bool
        """
        if not self.db_path:
            return False
        if threshold is None:
            prefs = self.storage.load_prefs()
            threshold = prefs.get("reflection_threshold", 5)
        pending_count = self.storage.count_pending_observations(self.db_path)
        return pending_count >= threshold

    # ==========================================
    # 反思執行
    # ==========================================
    def run_reflection(self, router, task_key="ai_reflect"):
        """
        執行 AI 人格反思：讀取待反思觀察 + 當前個性檔 → LLM 合成 → 更新檔案。

        Args:
            router: LLMRouter 實例
            task_key: LLM 路由任務鍵

        Returns:
            bool: 是否成功更新個性檔案
        """
        if not self.db_path:
            return False

        # 載入待反思觀察
        pending = self.storage.load_pending_observations(self.db_path)
        if not pending:
            return False

        # 載入當前狀態
        current_personality = self.load_personality_raw()
        core_prompt = self.storage.load_system_prompt()

        # 格式化觀察紀錄
        obs_lines = []
        for i, obs in enumerate(pending, 1):
            count_str = f" (x{int(obs['encounter_count'])})" if obs['encounter_count'] > 1 else ""
            obs_lines.append(f"{i}. [{obs['category']}] {obs['extracted_trait']}{count_str}")
        formatted_observations = "\n".join(obs_lines)

        prompt = get_prompt_manager().get("ai_reflect").format(
            core_prompt=core_prompt, current_personality=current_personality,
            pending_count=len(pending), formatted_observations=formatted_observations
        )

        try:
            api_messages = [{"role": "user", "content": prompt}]
            result = router.generate(task_key, api_messages, temperature=0.3)

            if not result or not result.strip():
                return False

            # 基礎驗證：確保輸出看起來像 Markdown
            result = result.strip()
            if not result.startswith("#"):
                # 嘗試找到第一個 # 開頭
                idx = result.find("\n#")
                if idx != -1:
                    result = result[idx + 1:]
                elif result.find("#") != -1:
                    result = result[result.find("#"):]
                else:
                    SystemLogger.log_error("personality_reflect", "Reflection output does not contain Markdown headers")
                    return False

            # 寫入個性檔案
            self.save_personality(result)

            # 標記觀察為已反思
            obs_ids = [obs["obs_id"] for obs in pending]
            self.storage.mark_observations_reflected(self.db_path, obs_ids)

            SystemLogger.log_system_event("personality_reflection_complete", {
                "observations_consumed": len(pending),
                "personality_length": len(result)
            })
            return True

        except Exception as e:
            SystemLogger.log_error("personality_reflect", str(e))
            return False
