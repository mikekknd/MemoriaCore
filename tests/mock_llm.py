"""Mock LLM 基礎設施 - 提供穩定、可預測的 LLM 模擬，脫離對實際 Ollama 連線的依賴"""
from unittest.mock import MagicMock, patch
from datetime import datetime
import json
import pytest


# ════════════════════════════════════════════════════════════
# SECTION: Mock Router - 模擬 LLMRouter 行為
# ════════════════════════════════════════════════════════════

class MockRouter:
    """模擬 LLMRouter，提供可預測的結構化回應"""

    def __init__(self):
        self.generate_calls = []  # 追蹤呼叫歷史
        self.generate_json_calls = []
        self.routes = {}
        self._tool_calls = []  # 可配置的 tool_calls（支援工具呼叫流程測試）

        # 預設回應（可由測試覆蓋）
        self._default_response = '{"internal_thought": "測試回應", "status_metrics": {"professionalism": 50}, "tone": "Neutral", "reply": "測試回應內容", "extracted_entities": []}'
        self._default_json_response = {"facts": []}

    def register_route(self, task_key, provider, model_name):
        """模擬路由註冊"""
        self.routes[task_key] = {"provider": provider, "model": model_name}

    def generate(self, task_key, messages, temperature=0.0, response_format=None):
        """模擬 LLM generate 呼叫，返回預定義的 JSON 字串"""
        self.generate_calls.append({
            "task_key": task_key,
            "messages": messages,
            "temperature": temperature,
            "response_format": response_format
        })
        return self._default_response

    def generate_with_tools(self, task_key, messages, tools=None, temperature=0.0, tool_choice="auto", response_format=None):
        """模擬 LLM generate_with_tools 呼叫"""
        self.generate_calls.append({
            "task_key": task_key,
            "messages": messages,
            "tools": tools,
            "temperature": temperature,
            "tool_choice": tool_choice,
            "response_format": response_format
        })
        return self._default_response, self._tool_calls

    def generate_json(self, task_key, messages, schema=None, temperature=0.1):
        """模擬 LLM generate_json 呼叫，返回預定義的 dict"""
        self.generate_json_calls.append({
            "task_key": task_key,
            "messages": messages,
            "schema": schema,
            "temperature": temperature
        })
        return self._default_json_response

    # --- 用於測試的輔助方法 ---

    def set_chat_response(self, response_dict):
        """設定 LLM 對話回應的 JSON dict"""
        self._default_response = json.dumps(response_dict, ensure_ascii=False)
        return self

    def set_facts_response(self, facts_list):
        """設定 extract_user_facts 的回應"""
        self._default_json_response = {"facts": facts_list}
        return self

    def set_router_result(self, needs_tools=False, thinking_speech=None):
        """設定 Router Agent 的結果"""
        from core.chat_orchestrator.router_agent import RouterResult
        return RouterResult(
            needs_tools=needs_tools,
            thinking_speech=thinking_speech,
            tools_to_call=[],
            tool_call_results=[]
        )

    def set_tool_calls(self, tool_calls):
        """設定 LLM generate_with_tools 回傳的 tool_calls"""
        self._tool_calls = tool_calls
        return self


# ════════════════════════════════════════════════════════════
# SECTION: Mock Memory System - 模擬 MemorySystem 行為
# ════════════════════════════════════════════════════════════

class MockMemorySystem:
    """模擬 MemorySystem，提供可預測的向量與搜尋行為"""

    def __init__(self):
        self.memory_blocks = []
        self.core_memories = []
        self.user_profiles = []
        self.embed_provider = MockEmbedProvider()
        self.embed_model = "bge-m3:latest"
        self.db_path = None

        # 搜尋模擬參數
        self._search_results = []
        self._core_search_results = []

    def cosine_similarity(self, vec_a, vec_b):
        """模擬餘弦相似度計算（使用全部維度）"""
        if not vec_a or not vec_b:
            return 0.0

        # 使用全部維度計算相似度
        dot_product = sum(a * b for a, b in zip(vec_a, vec_b))
        norm_a = (sum(a * a for a in vec_a)) ** 0.5
        norm_b = (sum(b * b for b in vec_b)) ** 0.5

        if norm_a == 0 or norm_b == 0:
            return 0.0

        similarity = dot_product / (norm_a * norm_b)
        # 確保結果在 [0, 1] 之間（餘弦相似度範圍是 [-1, 1]，但我們主要用正相似度）
        return max(0.0, similarity)

    def search_blocks(self, query, keywords, top_k, alpha, beta, threshold, base):
        """模擬記憶區塊搜尋"""
        return self._search_results[:top_k] if self._search_results else []

    def search_core_memories(self, query, top_k=1, threshold=0.45):
        """模擬核心記憶搜尋"""
        return self._core_search_results[:top_k] if self._core_search_results else []

    def search_profile_by_query(self, query, top_k=3, threshold=0.5):
        """模擬使用者畫像搜尋"""
        return []

    def expand_query(self, query, messages, router, task_key="expand"):
        """模擬查詢擴展"""
        return {"expanded_keywords": "", "entity_confidence": 0.5}

    def add_memory_block(self, overview, dialogues, router=None, sim_timestamp=None, potential_preferences=None):
        """模擬新增記憶區塊"""
        import uuid
        block = {
            "block_id": str(uuid.uuid4()),
            "timestamp": sim_timestamp or datetime.now().isoformat(),
            "overview": overview,
            "raw_dialogues": dialogues,
            "is_consolidated": False,
            "encounter_count": 1.0,
            "potential_preferences": potential_preferences or []
        }
        self.memory_blocks.append(block)
        return block

    def apply_profile_facts(self, facts, embed_model):
        """模擬套用使用者事實"""
        pass

    def load_user_profile(self):
        """模擬載入使用者畫像"""
        pass

    def get_static_profile_prompt(self):
        """模擬載入靜態畫像提示"""
        return ""

    def get_proactive_topics_prompt(self, limit=1):
        """模擬載入主動話題提示"""
        return ""

    # --- 用於測試的輔助方法 ---

    def set_search_results(self, results):
        """設定搜尋結果（用於測試）"""
        self._search_results = results
        return self

    def set_core_search_results(self, results):
        """設定核心記憶搜尋結果（用於測試）"""
        self._core_search_results = results
        return self

    def add_mock_memory_block(self, overview, dialogues, score=0.8):
        """快速新增 Mock 記憶區塊（用於測試）"""
        import uuid
        # 產生簡單的向量
        vec = [0.1 * i for i in range(384)]
        block = {
            "block_id": str(uuid.uuid4()),
            "timestamp": datetime.now().isoformat(),
            "overview": overview,
            "overview_vector": vec,
            "sparse_vector": {},
            "raw_dialogues": dialogues,
            "is_consolidated": False,
            "encounter_count": 1.0,
            "potential_preferences": [],
            "_debug_score": score,
            "_debug_raw_sim": score,
            "_debug_sparse_raw": score * 0.9,
            "_debug_recency": 1.0,
            "_debug_importance": 0.5
        }
        self.memory_blocks.append(block)
        self._search_results.append(block)
        return block


# ════════════════════════════════════════════════════════════
# SECTION: Mock Embed Provider - 模擬嵌入向量產生
# ════════════════════════════════════════════════════════════

class MockEmbedProvider:
    """模擬嵌入向量產生器（脫離 ONNX/LLM 依賴）

    特性：
    - 相同文字 => 相同向量（可重現）
    - 相似關鍵詞 => 高相似度向量（可用於語意比較）
    - 不同主題 => 低相似度向量（可用於話題偏移測試）
    """

    def __init__(self, vector_dim=384):
        self.vector_dim = vector_dim
        self.get_embedding_calls = []

    def _extract_keywords(self, text):
        """提取文字中的關鍵詞（簡單版）"""
        import re
        # 提取中文詞語（2-4個字符）
        chinese_words = re.findall(r'[\u4e00-\u9fff]{2,4}', text)
        # 提取英文單詞
        english_words = re.findall(r'\b[a-zA-Z]{3,}\b', text)
        return chinese_words + english_words

    def _get_keyword_vector(self, keywords):
        """根據關鍵詞集合產生向量（具有語意可比較性）"""
        # 定義主題到向量的映射
        topic_vectors = {
            # 程式設計相關 - 共享高維度空間
            "python": [0.8, 0.6, 0.4, 0.2, 0.1, 0.3, 0.5, 0.7] + [0] * (self.vector_dim - 8),
            "程式設計": [0.75, 0.55, 0.35, 0.25, 0.15, 0.35, 0.45, 0.65] + [0] * (self.vector_dim - 8),
            "自動化": [0.7, 0.5, 0.4, 0.3, 0.2, 0.4, 0.5, 0.6] + [0] * (self.vector_dim - 8),
            "腳本": [0.72, 0.52, 0.42, 0.28, 0.18, 0.38, 0.48, 0.62] + [0] * (self.vector_dim - 8),
            "處理": [0.68, 0.48, 0.38, 0.32, 0.22, 0.42, 0.52, 0.58] + [0] * (self.vector_dim - 8),
            "資料": [0.65, 0.45, 0.35, 0.35, 0.25, 0.45, 0.55, 0.55] + [0] * (self.vector_dim - 8),
            "套件": [0.6, 0.4, 0.3, 0.4, 0.3, 0.5, 0.6, 0.5] + [0] * (self.vector_dim - 8),
            "框架": [0.55, 0.35, 0.25, 0.45, 0.35, 0.55, 0.65, 0.45] + [0] * (self.vector_dim - 8),
            # 食物相關
            "拉麵": [0.2, 0.3, 0.7, 0.8, 0.6, 0.4, 0.2, 0.3] + [0] * (self.vector_dim - 8),
            "豚骨": [0.18, 0.28, 0.68, 0.78, 0.62, 0.42, 0.18, 0.28] + [0] * (self.vector_dim - 8),
            "湯頭": [0.15, 0.25, 0.65, 0.75, 0.65, 0.55, 0.15, 0.25] + [0] * (self.vector_dim - 8),
            "壽司": [0.15, 0.25, 0.65, 0.75, 0.65, 0.55, 0.15, 0.25] + [0] * (self.vector_dim - 8),
            "咖啡": [0.3, 0.4, 0.6, 0.7, 0.5, 0.3, 0.3, 0.4] + [0] * (self.vector_dim - 8),
            "蛋糕": [0.1, 0.2, 0.5, 0.6, 0.7, 0.6, 0.1, 0.2] + [0] * (self.vector_dim - 8),
            # 財經相關
            "比特幣": [0.5, 0.4, 0.3, 0.2, 0.7, 0.8, 0.6, 0.5] + [0] * (self.vector_dim - 8),
            "投資": [0.45, 0.35, 0.25, 0.15, 0.75, 0.85, 0.55, 0.45] + [0] * (self.vector_dim - 8),
            "交易": [0.4, 0.3, 0.2, 0.1, 0.65, 0.75, 0.6, 0.5] + [0] * (self.vector_dim - 8),
            "入場": [0.38, 0.28, 0.18, 0.08, 0.68, 0.78, 0.58, 0.48] + [0] * (self.vector_dim - 8),
            # 音樂相關
            "吉他": [0.35, 0.25, 0.45, 0.55, 0.4, 0.3, 0.6, 0.7] + [0] * (self.vector_dim - 8),
            "彈奏": [0.3, 0.2, 0.4, 0.5, 0.35, 0.25, 0.55, 0.65] + [0] * (self.vector_dim - 8),
            "民謠": [0.32, 0.22, 0.42, 0.52, 0.38, 0.28, 0.58, 0.68] + [0] * (self.vector_dim - 8),
            "搖滾": [0.28, 0.18, 0.38, 0.48, 0.32, 0.22, 0.52, 0.62] + [0] * (self.vector_dim - 8),
            # 一般對話
            "天氣": [0.5, 0.3, 0.4, 0.3, 0.4, 0.5, 0.3, 0.4] + [0] * (self.vector_dim - 8),
            "出去": [0.48, 0.28, 0.38, 0.28, 0.38, 0.48, 0.28, 0.38] + [0] * (self.vector_dim - 8),
            "跑步": [0.4, 0.2, 0.3, 0.25, 0.35, 0.45, 0.25, 0.35] + [0] * (self.vector_dim - 8),
            "運動": [0.42, 0.22, 0.32, 0.27, 0.37, 0.47, 0.27, 0.37] + [0] * (self.vector_dim - 8),
            "熱身": [0.38, 0.18, 0.28, 0.23, 0.33, 0.43, 0.23, 0.33] + [0] * (self.vector_dim - 8),
            # QA 對話
            "學習": [0.6, 0.5, 0.4, 0.3, 0.5, 0.6, 0.4, 0.5] + [0] * (self.vector_dim - 8),
            "不知道": [0.18, 0.08, 0.28, 0.18, 0.18, 0.28, 0.18, 0.18] + [0] * (self.vector_dim - 8),
            "好啊": [0.15, 0.05, 0.25, 0.15, 0.15, 0.25, 0.15, 0.15] + [0] * (self.vector_dim - 8),
            "是啊": [0.18, 0.08, 0.28, 0.18, 0.18, 0.28, 0.18, 0.18] + [0] * (self.vector_dim - 8),
        }

        # 匯集所有關鍵詞的向量
        vector = [0.0] * self.vector_dim
        for kw in keywords:
            if kw in topic_vectors:
                for i in range(min(8, self.vector_dim)):
                    vector[i] += topic_vectors[kw][i]

        # 正規化向量
        norm = sum(v * v for v in vector) ** 0.5
        if norm > 0:
            vector = [v / norm for v in vector]
        else:
            import hashlib
            hash_val = hashlib.md5("default".encode()).hexdigest()
            for i in range(self.vector_dim):
                sub_hash = hash_val[i % 32:(i % 32) + 8]
                vector[i] = (int(sub_hash, 16) / 0xffffffff) * 2 - 1

        return vector

    def get_embedding(self, text, model):
        """產生可預測的向量（基於關鍵詞的語意模擬）"""
        self.get_embedding_calls.append({"text": text, "model": model})

        keywords = self._extract_keywords(text)
        vector = self._get_keyword_vector(keywords)

        return {"dense": vector, "sparse": {}}


# ════════════════════════════════════════════════════════════
# SECTION: Fixture 工具函式 - 用於 conftest.py
# ════════════════════════════════════════════════════════════

def create_mock_router():
    """建立 Mock Router 的便捷函式"""
    return MockRouter()


def create_mock_memory_system():
    """建立 Mock MemorySystem 的便捷函式"""
    return MockMemorySystem()


def create_mock_embed_provider():
    """建立 Mock EmbedProvider 的便捷函式"""
    return MockEmbedProvider()


# ════════════════════════════════════════════════════════════
# SECTION: Pytest Fixtures - 自動注入的 Mock 物件
# ════════════════════════════════════════════════════════════

@pytest.fixture
def mock_router():
    """提供 Mock Router fixture"""
    return MockRouter()


@pytest.fixture
def mock_memory_system():
    """提供 Mock MemorySystem fixture"""
    return MockMemorySystem()


@pytest.fixture
def mock_embed_provider():
    """提供 Mock EmbedProvider fixture"""
    return MockEmbedProvider()
