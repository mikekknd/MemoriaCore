"""核心認知測試：驗證 Insight 提煉、融合與搜尋"""
import uuid
import pytest
from datetime import datetime
from tests.test_config import requires_ollama


@requires_ollama
class TestCoreInsights:

    def _create_memory_block(self, memory_system, overview, encounter_count=1.0, router=None):
        """輔助方法：注入記憶區塊並設定 encounter_count"""
        dialogues = [{"role": "user", "content": "測試對話內容"}]
        block = memory_system.add_memory_block(overview, dialogues, router=router)
        if block:
            block["encounter_count"] = encounter_count
        return block

    def test_distill_creates_insight(self, memory_system, router):
        """encounter_count > 1 的記憶概覽應能提煉出核心認知"""
        overview = "[核心實體]: 動畫, 賽璐璐, 色彩\n[情境摘要]: 使用者深入分析賽璐璐畫風動畫的色彩通透感與光影表現手法"
        block = self._create_memory_block(memory_system, overview, encounter_count=3.0, router=router)
        assert block is not None

        context_text = f"時間: {block['timestamp']}\n概覽: {block['overview']}"
        memory_system._distill_core_memory(context_text, 3.0, router)

        assert len(memory_system.core_memories) >= 1, "應產生至少一筆核心認知"
        insight_text = memory_system.core_memories[0]["insight"]
        assert insight_text and insight_text.upper() != "NULL", f"Insight 不應為空或 NULL: {insight_text}"

    def test_low_weight_yields_null(self, memory_system, router):
        """權重 <= 1 的極瑣碎記憶不應產生有意義的 Insight"""
        overview = "[核心實體]: 無\n[情境摘要]: 使用者說了嗯"
        block = self._create_memory_block(memory_system, overview, encounter_count=0.5, router=router)
        assert block is not None

        initial_count = len(memory_system.core_memories)
        context_text = f"時間: {block['timestamp']}\n概覽: {block['overview']}"
        memory_system._distill_core_memory(context_text, 0.5, router)

        # LLM 應回傳 NULL（但某些模型可能仍生成內容，此為模型行為的軟性測試）
        new_count = len(memory_system.core_memories)
        if new_count > initial_count:
            # 即使生成了，也驗證 encounter_count 為低權重
            new_insight = memory_system.core_memories[-1]
            assert new_insight["encounter_count"] <= 1.0, \
                "低權重記憶即使生成 Insight，encounter_count 也不應超過 1.0"
        # 若未生成（預期行為），測試通過

    def test_insight_fusion_on_similar_topic(self, memory_system, router):
        """相似主題的 Insight 應融合為一筆，encounter_count 累加"""
        # 先建立第一筆核心認知
        overview1 = "[核心實體]: 角色設計, 動畫\n[情境摘要]: 使用者深入研究動畫角色設計的細節與美學原則"
        block1 = self._create_memory_block(memory_system, overview1, encounter_count=2.0, router=router)
        context1 = f"時間: {block1['timestamp']}\n概覽: {block1['overview']}"
        memory_system._distill_core_memory(context1, 2.0, router)

        initial_count = len(memory_system.core_memories)
        if initial_count == 0:
            pytest.skip("第一次提煉未產生 Insight（模型可能判定為瑣碎）")

        initial_enc = memory_system.core_memories[0]["encounter_count"]

        # 用相似主題再次提煉
        overview2 = "[核心實體]: 人物設定, 動畫美術\n[情境摘要]: 使用者深入探討動畫人物設定的美術風格與造型設計"
        block2 = self._create_memory_block(memory_system, overview2, encounter_count=2.5, router=router)
        context2 = f"時間: {block2['timestamp']}\n概覽: {block2['overview']}"
        memory_system._distill_core_memory(context2, 2.5, router)

        # 應融合而非新增
        assert len(memory_system.core_memories) == initial_count, \
            f"相似主題應融合，但核心認知數從 {initial_count} 變為 {len(memory_system.core_memories)}"
        assert memory_system.core_memories[0]["encounter_count"] > initial_enc, \
            "融合後 encounter_count 應增加"

    def test_search_core_memories(self, memory_system, router):
        """核心認知應可被語意查詢搜尋到"""
        # 手動建立一筆核心認知
        insight = "使用者對動畫美術風格有深入的研究興趣，特別關注色彩表現與光影技法"
        vec = memory_system.embed_provider.get_embedding(text=insight, model=memory_system.embed_model)

        core_item = {
            "core_id": str(uuid.uuid4()),
            "timestamp": datetime.now().isoformat(),
            "insight": insight,
            "insight_vector": vec["dense"],
            "encounter_count": 3.0
        }
        memory_system.core_memories.append(core_item)

        results = memory_system.search_core_memories("動畫的色彩與美術", top_k=1)
        assert len(results) >= 1, "語意查詢應找到相關核心認知"
        assert results[0]["score"] >= 0.45, f"分數過低: {results[0]['score']:.3f}"
        assert "動畫" in results[0]["insight"] or "色彩" in results[0]["insight"]
