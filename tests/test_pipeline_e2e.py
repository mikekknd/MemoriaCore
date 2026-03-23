"""端對端測試：完整流程從 Ollama 生成對話 → 記憶管線 → 記憶招回 → 畫像提取"""
import pytest
from tests.test_config import requires_ollama
from tests.ollama_sim import generate_conversation, generate_persona_conversation


@requires_ollama
@pytest.mark.slow
class TestEndToEndPipeline:

    def _run_pipeline_and_inject(self, memory_system, analyzer, router, messages):
        """輔助：將對話通過 pipeline 並注入記憶"""
        last_block = memory_system.memory_blocks[-1] if memory_system.memory_blocks else None
        pipeline_res = analyzer.process_memory_pipeline(
            messages, last_block, router, memory_system.embed_model
        )
        if "error" in pipeline_res:
            return pipeline_res

        new_mems = pipeline_res.get("new_memories", [])
        for mem in new_mems:
            entities_str = ", ".join(mem.get("entities", []))
            summary_str = mem.get("summary", "")
            overview = f"[核心實體]: {entities_str}\n[情境摘要]: {summary_str}"
            indices = mem.get("message_indices", [])
            raw = [messages[i] for i in indices if 0 <= i < len(messages)]
            if raw:
                memory_system.add_memory_block(
                    overview, raw, router=router,
                    potential_preferences=mem.get("potential_preferences", [])
                )
        return pipeline_res

    def test_full_flow_conversation_to_recall(self, memory_system, analyzer, router):
        """完整流程：Ollama 生成對話 → pipeline → 注入記憶 → 語意招回"""
        try:
            messages = generate_conversation(router, "學習彈鋼琴的心得與技巧", turns=5)
        except RuntimeError:
            pytest.skip("Ollama 對話生成失敗")

        assert len(messages) >= 4, f"對話回合數不足: {len(messages)}"

        result = self._run_pipeline_and_inject(memory_system, analyzer, router, messages)
        assert "error" not in result, f"Pipeline 錯誤: {result.get('error')}"
        assert len(memory_system.memory_blocks) >= 1, "應至少注入一筆記憶"

        # 語意招回
        search_results = memory_system.search_blocks("鋼琴練習技巧", "", top_k=2)
        assert len(search_results) >= 1, "應能招回鋼琴相關記憶"
        assert search_results[0]["_debug_score"] >= 0.5

    def test_multi_topic_independent_recall(self, memory_system, analyzer, router):
        """兩個不同主題的對話應分別儲存，各自可獨立招回"""
        try:
            conv_a = generate_conversation(router, "Python 機器學習框架 PyTorch 的使用心得", turns=4)
            conv_b = generate_conversation(router, "週末去露營烤肉的經驗分享", turns=4)
        except RuntimeError:
            pytest.skip("Ollama 對話生成失敗")

        self._run_pipeline_and_inject(memory_system, analyzer, router, conv_a)
        blocks_after_a = len(memory_system.memory_blocks)
        assert blocks_after_a >= 1

        self._run_pipeline_and_inject(memory_system, analyzer, router, conv_b)
        blocks_after_b = len(memory_system.memory_blocks)
        assert blocks_after_b >= 2, "兩個不同主題應產生至少 2 筆記憶區塊"

        # 各自招回
        results_ml = memory_system.search_blocks("PyTorch 深度學習", "", top_k=2)
        results_camp = memory_system.search_blocks("露營烤肉 戶外活動", "", top_k=2)

        assert len(results_ml) >= 1, "應能招回 ML 相關記憶"
        assert len(results_camp) >= 1, "應能招回露營相關記憶"

        # 確保排名合理（ML 查詢不應優先回傳露營記憶）
        if len(results_ml) >= 1:
            overview = results_ml[0]["overview"]
            assert "露營" not in overview or "Python" in overview or "PyTorch" in overview or "學習" in overview, \
                "ML 查詢的首筆結果不應是露營記憶"

    def test_profile_extraction_through_pipeline(self, memory_system, analyzer, router):
        """對話中的個人資訊經完整流程後應寫入使用者畫像"""
        try:
            messages = generate_persona_conversation(
                router,
                persona_desc="使用者叫做小芳，今年 25 歲，是插畫師，住在台中，最愛喝珍珠奶茶",
                topic="聊聊工作與日常生活",
                turns=5
            )
        except RuntimeError:
            pytest.skip("Ollama 人設對話生成失敗")

        # 記憶管線
        self._run_pipeline_and_inject(memory_system, analyzer, router, messages)

        # 畫像提取
        current_profile = memory_system.storage.load_all_profiles(memory_system.db_path)
        facts = analyzer.extract_user_facts(messages, current_profile, router)

        if facts:
            memory_system.apply_profile_facts(facts, memory_system.embed_model)

        profiles = memory_system.storage.load_all_profiles(memory_system.db_path)
        assert len(profiles) >= 1, "完整流程後應至少有一筆使用者畫像"

        all_values = " ".join([p["fact_value"] for p in profiles])
        has_persona = any(k in all_values for k in ["小芳", "插畫", "台中", "珍珠", "奶茶", "25"])
        assert has_persona, f"畫像應包含人設資訊，但僅有: {[p['fact_key'] + '=' + p['fact_value'] for p in profiles]}"

    def test_repeated_topic_builds_core_insight(self, memory_system, analyzer, router):
        """同主題多次對話後，透過大腦反芻（consolidate）應累積出核心認知 Insight"""
        topic = "動畫角色設計的美學分析"
        for i in range(3):
            try:
                messages = generate_conversation(router, f"{topic}（第 {i+1} 次討論）", turns=4)
            except RuntimeError:
                pytest.skip(f"第 {i+1} 次 Ollama 對話生成失敗")

            self._run_pipeline_and_inject(memory_system, analyzer, router, messages)

        assert len(memory_system.memory_blocks) >= 1, "應至少有一筆記憶區塊"

        # 若記憶未自動合併，手動觸發大腦反芻（找相似群 → 融合）
        if len(memory_system.memory_blocks) >= 2:
            clusters = memory_system.find_pending_clusters(cluster_threshold=0.65, min_group_size=2)
            for cluster in clusters:
                memory_system.consolidate_and_fuse(cluster, router)

        # 反芻後應有核心認知
        assert len(memory_system.core_memories) >= 1, \
            "多次同主題對話經大腦反芻後，應提煉出至少一筆核心認知"
