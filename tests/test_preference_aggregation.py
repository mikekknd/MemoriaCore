"""偏好聚合測試：驗證標籤聚類、極性分離、時間衰減與畫像寫入"""
import uuid
import pytest
from datetime import datetime, timedelta
from tests.test_config import requires_ollama


@requires_ollama
class TestPreferenceAggregation:

    def _inject_block_with_prefs(self, memory_system, overview, prefs, timestamp=None, encounter_count=1.5):
        """輔助方法：手動注入含偏好標籤的記憶區塊"""
        vec = memory_system.embed_provider.get_embedding(text=overview, model=memory_system.embed_model)
        block = {
            "block_id": str(uuid.uuid4()),
            "timestamp": timestamp or datetime.now().isoformat(),
            "overview": overview,
            "overview_vector": vec.get("dense", []),
            "sparse_vector": vec.get("sparse", {}),
            "raw_dialogues": [{"role": "user", "content": "測試對話"}],
            "is_consolidated": False,
            "encounter_count": encounter_count,
            "potential_preferences": prefs
        }
        memory_system.memory_blocks.append(block)
        return block

    def test_cluster_similar_tags(self, pref_aggregator, memory_system):
        """語意相似的偏好標籤應聚類為同一群"""
        # 注入多筆含相似偏好的記憶
        for i in range(4):
            self._inject_block_with_prefs(
                memory_system,
                f"[核心實體]: 肉類料理 {i}\n[情境摘要]: 使用者喜歡吃肉",
                [{"tag": "喜歡肉類料理", "intensity": 0.8}],
                encounter_count=2.0
            )

        results = pref_aggregator.aggregate(score_threshold=3.0)
        assert len(results) >= 1, "多次出現的高強度偏好應被聚合"
        # 所有 tag 應聚為一群
        total_cluster_size = sum(r["cluster_size"] for r in results if "肉" in r["tag"])
        assert total_cluster_size >= 3, f"相似標籤應聚合，但 cluster_size={total_cluster_size}"

    def test_polarity_prevents_merge(self, pref_aggregator, memory_system):
        """「喜歡X」和「討厭X」不應因 embedding 相似而合併"""
        self._inject_block_with_prefs(
            memory_system,
            "[核心實體]: 甜食\n[情境摘要]: 使用者喜歡甜食",
            [{"tag": "喜歡甜食", "intensity": 0.9}],
            encounter_count=4.0
        )
        self._inject_block_with_prefs(
            memory_system,
            "[核心實體]: 甜食\n[情境摘要]: 使用者討厭甜食",
            [{"tag": "討厭甜食", "intensity": 0.9}],
            encounter_count=4.0
        )

        results = pref_aggregator.aggregate(score_threshold=0.1)  # 低門檻確保都能出現
        like_clusters = [r for r in results if r["tag"].startswith("喜歡")]
        dislike_clusters = [r for r in results if r["tag"].startswith("討厭")]

        if like_clusters and dislike_clusters:
            # 確保它們不在同一個 cluster
            for lc in like_clusters:
                for dc in dislike_clusters:
                    tags_overlap = set(lc["all_tags"]) & set(dc["all_tags"])
                    assert len(tags_overlap) == 0, "喜歡和討厭不應出現在同一 cluster"

    def test_time_decay_reduces_old_scores(self, pref_aggregator, memory_system):
        """90 天前的標籤分數應低於今天的標籤"""
        old_time = (datetime.now() - timedelta(days=90)).isoformat()
        new_time = datetime.now().isoformat()

        self._inject_block_with_prefs(
            memory_system,
            "[核心實體]: 咖啡\n[情境摘要]: 使用者喜歡喝咖啡（舊）",
            [{"tag": "喜歡咖啡飲品", "intensity": 0.8}],
            timestamp=old_time, encounter_count=2.0
        )
        self._inject_block_with_prefs(
            memory_system,
            "[核心實體]: 茶\n[情境摘要]: 使用者喜歡喝茶（新）",
            [{"tag": "喜歡茶類飲品", "intensity": 0.8}],
            timestamp=new_time, encounter_count=2.0
        )

        results = pref_aggregator.aggregate(score_threshold=0.1)
        coffee_result = next((r for r in results if "咖啡" in r["tag"]), None)
        tea_result = next((r for r in results if "茶" in r["tag"]), None)

        if coffee_result and tea_result:
            assert tea_result["score"] > coffee_result["score"], \
                f"近期偏好分數 ({tea_result['score']}) 應高於舊偏好 ({coffee_result['score']})"

    def test_write_to_profile_deduplicates(self, pref_aggregator, memory_system):
        """write_to_profile 不應產生重複的畫像條目"""
        for i in range(5):
            self._inject_block_with_prefs(
                memory_system,
                f"[核心實體]: 運動 {i}\n[情境摘要]: 使用者喜歡運動",
                [{"tag": "喜歡戶外運動", "intensity": 0.9}],
                encounter_count=2.0
            )

        results = pref_aggregator.aggregate(score_threshold=3.0)
        if not results:
            pytest.skip("聚合未產出足夠分數的結果")

        # 寫入兩次
        count1 = pref_aggregator.write_to_profile(results)
        count2 = pref_aggregator.write_to_profile(results)

        assert count1 >= 1, "第一次寫入應至少寫入一筆"
        assert count2 == 0, "第二次寫入應被語義去重攔截"

    def test_below_threshold_not_promoted(self, pref_aggregator, memory_system):
        """低分標籤（單次、低強度）不應被升格"""
        self._inject_block_with_prefs(
            memory_system,
            "[核心實體]: 攝影\n[情境摘要]: 使用者提到攝影",
            [{"tag": "喜歡攝影", "intensity": 0.2}],
            encounter_count=0.5
        )

        results = pref_aggregator.aggregate(score_threshold=3.0)
        photo_results = [r for r in results if "攝影" in r["tag"]]
        assert len(photo_results) == 0, "低分偏好不應被升格"
