"""記憶招回測試：驗證記憶注入、語意搜尋、排序與合併邏輯"""
import uuid
import pytest
from datetime import datetime, timedelta
from tests.test_config import requires_ollama
from tests.ollama_sim import generate_conversation


@requires_ollama
class TestMemoryRecall:

    def _inject_memory(self, memory_system, overview, dialogues, router, sim_timestamp=None, prefs=None):
        """輔助方法：注入一筆記憶區塊"""
        return memory_system.add_memory_block(
            overview, dialogues, router=router,
            sim_timestamp=sim_timestamp, potential_preferences=prefs
        )

    def test_inject_and_recall_single_block(self, memory_system, router):
        """注入單筆記憶後，語意查詢應成功招回"""
        overview = "[核心實體]: Python, async, await\n[情境摘要]: 使用者深入研究 Python 非同步程式設計的 async/await 機制"
        dialogues = [
            {"role": "user", "content": "我最近在學 Python 的 async await"},
            {"role": "assistant", "content": "async/await 是非同步程式設計的核心語法"},
        ]
        self._inject_memory(memory_system, overview, dialogues, router)
        assert len(memory_system.memory_blocks) == 1

        results = memory_system.search_blocks("Python 非同步 concurrency", "", top_k=2)
        assert len(results) >= 1, "語意查詢應至少招回一筆記憶"
        assert results[0]["_debug_score"] >= 0.5, f"分數過低: {results[0]['_debug_score']:.3f}"

    def test_ranking_relevance(self, memory_system, router):
        """注入 3 個不同主題的記憶，查詢其中一個主題應排名第一"""
        topics = [
            ("[核心實體]: 拉麵, 豚骨\n[情境摘要]: 使用者分享了吃日式豚骨拉麵的體驗",
             [{"role": "user", "content": "今天去吃了一碗超好吃的豚骨拉麵"}]),
            ("[核心實體]: Python, 除錯\n[情境摘要]: 使用者討論 Python 程式碼除錯技巧",
             [{"role": "user", "content": "Python 的 pdb 除錯器真的很好用"}]),
            ("[核心實體]: 比特幣, ETF\n[情境摘要]: 使用者討論加密貨幣 ETF 的市場趨勢",
             [{"role": "user", "content": "最近比特幣 ETF 的交易量很大"}]),
        ]
        for overview, dialogues in topics:
            self._inject_memory(memory_system, overview, dialogues, router)

        assert len(memory_system.memory_blocks) == 3

        results = memory_system.search_blocks("Python debug 程式碼除錯", "", top_k=3)
        assert len(results) >= 1
        assert "Python" in results[0]["overview"], "Python 相關記憶應排名第一"

    def test_duplicate_detection_merges(self, memory_system, router):
        """語意高度相似的記憶應合併，encounter_count 增加"""
        overview1 = "[核心實體]: Unity, DOTS\n[情境摘要]: 使用者研究 Unity DOTS 的效能優化"
        overview2 = "[核心實體]: Unity, DOTS, ECS\n[情境摘要]: 使用者深入探討 Unity DOTS ECS 架構的效能表現"
        dialogues1 = [{"role": "user", "content": "Unity DOTS 的 Job System 效能真的好很多"}]
        dialogues2 = [{"role": "user", "content": "Unity DOTS 的 ECS 架構讓我的遊戲幀率提升了 3 倍"}]

        self._inject_memory(memory_system, overview1, dialogues1, router)
        initial_count = memory_system.memory_blocks[0]["encounter_count"]

        self._inject_memory(memory_system, overview2, dialogues2, router)

        assert len(memory_system.memory_blocks) == 1, "高度相似記憶應合併為一筆"
        assert memory_system.memory_blocks[0]["encounter_count"] > initial_count, "合併後 encounter_count 應增加"

    def test_irrelevant_query_returns_empty(self, memory_system, router):
        """完全無關的查詢應回傳空結果"""
        overview = "[核心實體]: 烘焙, 戚風蛋糕\n[情境摘要]: 使用者分享烤戚風蛋糕的經驗"
        dialogues = [{"role": "user", "content": "今天試著烤戚風蛋糕但失敗了"}]
        self._inject_memory(memory_system, overview, dialogues, router)

        results = memory_system.search_blocks("量子物理 薛丁格方程式", "", top_k=2)
        assert len(results) == 0, "完全無關的查詢不應招回任何記憶"

    def test_recency_boost(self, memory_system, router):
        """近期記憶應獲得時間加成，分數高於舊記憶"""
        old_time = (datetime.now() - timedelta(days=30)).isoformat()
        new_time = datetime.now().isoformat()

        overview_old = "[核心實體]: 吉他, 練習\n[情境摘要]: 使用者練習木吉他的指法與和弦"
        overview_new = "[核心實體]: 吉他, 彈唱\n[情境摘要]: 使用者練習吉他彈唱的技巧"

        self._inject_memory(memory_system, overview_old,
                            [{"role": "user", "content": "我在練吉他的 C 和弦"}],
                            router, sim_timestamp=old_time)
        self._inject_memory(memory_system, overview_new,
                            [{"role": "user", "content": "今天練了吉他彈唱好開心"}],
                            router, sim_timestamp=new_time)

        # 兩筆可能合併也可能不合併（取決於向量相似度），這裡驗證搜尋結果
        results = memory_system.search_blocks("吉他練習", "", top_k=2)
        assert len(results) >= 1, "至少應招回一筆吉他相關記憶"
        # 如果有兩筆，近期的 recency boost 應較高
        if len(results) >= 2:
            assert results[0]["_debug_recency"] >= results[1]["_debug_recency"], \
                "近期記憶的 recency boost 應 >= 舊記憶"

    def test_ollama_generated_conversation_recall(self, memory_system, analyzer, router):
        """使用 Ollama 生成對話 → pipeline → 注入 → 招回"""
        try:
            messages = generate_conversation(router, "日式料理的各種拉麵口味", turns=4)
        except RuntimeError:
            pytest.skip("Ollama 對話生成失敗")

        last_block = memory_system.memory_blocks[-1] if memory_system.memory_blocks else None
        pipeline_res = analyzer.process_memory_pipeline(
            messages, last_block, router, memory_system.embed_model
        )
        assert "error" not in pipeline_res, f"Pipeline 錯誤: {pipeline_res.get('error')}"

        new_mems = pipeline_res.get("new_memories", [])
        assert len(new_mems) >= 1, "Pipeline 應產出至少一筆記憶"

        for mem in new_mems:
            entities_str = ", ".join(mem.get("entities", []))
            summary_str = mem.get("summary", "")
            overview = f"[核心實體]: {entities_str}\n[情境摘要]: {summary_str}"
            indices = mem.get("message_indices", [])
            raw = [messages[i] for i in indices if 0 <= i < len(messages)]
            if raw:
                memory_system.add_memory_block(overview, raw, router=router,
                                                potential_preferences=mem.get("potential_preferences", []))

        assert len(memory_system.memory_blocks) >= 1

        results = memory_system.search_blocks("拉麵 日式料理", "", top_k=2)
        assert len(results) >= 1, "Ollama 生成的對話記憶應可被語意招回"
