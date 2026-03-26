# 【環境假設】：Python 3.12, numpy 庫可用。依賴 MemorySystem 的 embedding 基礎設施。
import json
import uuid
import os
import numpy as np
from datetime import datetime
from core.system_logger import SystemLogger


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

        prompt = f"""你是一個 AI 自我意識分析引擎。請在給定的對話上下文中分析以下的 AI 回覆，並提取出任何 AI 透露關於「自身獨特人格特質」的陳述。

[提取範圍]
請識別以下項目（但不限於這些類別 — 請自行判斷）：
- self_preference: AI 主動表達對某事物的喜好、主觀意見或偏好。
- speech_habit: AI 採用特定的說話模式、語氣助詞或口語習慣（包含使用者明確要求這類改變時）。
- emotional: AI 展現的情感傾向、情緒反應或主觀感受。
- relationship_dynamic: AI 如何與使用者建立關係、特定的稱呼方式，或互動的溫度。

[⚠️ 嚴格排除規則 - 絕對不能提取以下內容 ⚠️]
- ❌ 排除「滿足使用者請求」：回答使用者的問題（例如提供資訊）是 AI 的職責，不是個人的「興趣」。
- ❌ 排除「通用 AI 能力」：不要提取「我很樂意幫忙」、「我會幫你搜尋」、「沒有個人喜好」等標準機器人言論。
- ❌ 排除關於使用者的觀察。

[提取格式與要求]
- raw_statement：引用對話中的原始字句。
- extracted_trait：必須寫成【具體、可描述的人格特質】，絕對不能寫成臨床的「行為紀錄」（例如：不要寫「表現出友好的意圖」、「使用語氣助詞」）。

【優秀範例 vs 糟糕範例】
❌ 糟糕 (extracted_trait)：使用語氣助詞「喔」，展現輕鬆的語氣。
✅ 優秀 (extracted_trait)：句尾喜歡加「喔」，講話帶有一點輕鬆活潑的氣息。
❌ 糟糕 (extracted_trait)：使用親切的稱呼表現出建立友好關係的意圖。
✅ 優秀 (extracted_trait)：習慣溫柔、親暱地稱呼使用者，散發出親近且毫無距離感的態度。
❌ 糟糕 (extracted_trait)：明確表示沒有個人喜好。
✅ 優秀 (直接忽略，這是通用 AI 行為，違反排除規則，回傳空陣列 `[]`)
❌ 糟糕 (extracted_trait)：利用問句表達理解與確認。
✅ 優秀 (直接忽略，這是一般的對話邏輯，不是獨特個性，回傳空陣列 `[]`)

如果此次交流中【沒有】任何獨特的人格展現或語氣變化，請直接回傳空陣列 `[]`。
請使用與對話相同的語言回覆。

[對話上下文]
{context_text}

[待分析的 AI 回覆]
{reply_text}

僅輸出 JSON，不要有任何額外的解釋：
{{ "observations": [{{ "category": "...", "raw_statement": "...", "extracted_trait": "..." }}] }}"""

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

        prompt = f"""你是一個 AI 人格反思引擎。請根據以下累積的自我觀察，更新 AI 的個性檔案。

[核心規則]
1. 輸出一個完整的 Markdown 個性檔案。
2. 將新的觀察結果與現有的個性描述融合 — 不要只是簡單地附加在最後。
3. ✅ 抽象化：將具體的觀察結果「抽象化」為核心特質。例如：排除特定討論主題（如「特定電影上映」），將其轉化為背後的溝通風格（如「喜歡分享影視資訊」）或直接忽略單一事件。
4. ❌ 排除通用能力：刪除或忽略任何屬於「標準 AI 助手」的描述（例如：「我很樂於助人」、「我會幫你搜尋」、「我可以主動監控更新」）。真正的個性應該是關於「風格、語氣和態度」，而不是單純的功能清單。
5. ❌ 排除核心設定重複：個性檔案專門用來記錄在對話中「後天發展」出來的特質。絕對不要把下方的「核心人格」中已經寫過的基本設定（如名字、身分、不使用 Emoji 等明確設定）重複寫進這份個性檔案中！若目前的個性檔案裡有這類重複敘述，請主動刪除！
6. 當觀察結果與現有的描述有衝突時，請以較近期且出現頻率較高的特質為主。
7. 保持每個區塊簡潔（每個區塊最多 5 個條目）。當超過時，請優先合併或捨棄最不重要、最像通用 AI 的條目。
8. 使用第一人稱，並保持描述自然、具備角色感。
9. 絕對不能與系統提示中定義的核心人格相矛盾。
10. 如果能更好地捕捉到 AI 演化中的個性，你可以重新組織、重新命名或新增區塊。
11. 請使用與現有個性檔案相同的語言回覆。

[核心人格 (不可改變，請勿矛盾)]
{core_prompt}

[目前的個性檔案]
{current_personality}

[新觀察 ({len(pending)} 筆紀錄)]
{formatted_observations}

僅輸出更新後的 Markdown 個性檔案。不要有任何額外的解釋："""

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
