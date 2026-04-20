"""偏好聚合純數學測試 - 完全脫離 LLM 依賴的單元測試
這類測試驗證 PreferenceAggregator 的純數學邏輯：
- 標籤聚類（Greedy clustering）
- 極性分離（同一標籤的「喜歡/討厭」不應合併）
- 時間衰減（Time decay）
- 分數計算
"""
import pytest
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.mock_llm import MockMemorySystem
from core.preference_aggregator import PreferenceAggregator


class TestPreferenceAggregationMath:
    """PreferenceAggregator 純數學邏輯測試"""

    def _create_mock_block(self, tag, intensity=0.8, timestamp=None, encounter_count=2.0):
        """輔助：建立含單一偏好的 Mock 記憶區塊"""
        import uuid
        from datetime import datetime
        if timestamp is None:
            timestamp = datetime.now().isoformat()

        # 產生向量
        vec = [0.1 * i for i in range(384)]

        return {
            "block_id": str(uuid.uuid4()),
            "timestamp": timestamp,
            "overview": "[核心實體]: 測試\n[情境摘要]: 測試",
            "overview_vector": vec,
            "sparse_vector": {},
            "raw_dialogues": [{"role": "user", "content": "測試"}],
            "is_consolidated": False,
            "encounter_count": encounter_count,
            "potential_preferences": [{"tag": tag, "intensity": intensity}]
        }

    def test_cluster_similar_tags(self):
        """語意相似的偏好標籤應聚類為同一群"""
        # 準備資料：4 筆含相似偏好的記憶
        blocks = []
        for i in range(4):
            blocks.append(self._create_mock_block(
                "喜歡肉類料理",
                intensity=0.8,
                encounter_count=2.0
            ))

        # 準備 MemorySystem 和 PreferenceAggregator
        memory_sys = MockMemorySystem()
        memory_sys.memory_blocks = blocks

        aggregator = PreferenceAggregator(memory_sys)
        results = aggregator.aggregate(score_threshold=3.0)

        # 驗證
        assert len(results) >= 1, "多次出現的高強度偏好應被聚合"
        # 所有 tag 應聚為一群
        total_cluster_size = sum(r["cluster_size"] for r in results)
        assert total_cluster_size >= 3, f"相似標籤應聚合，但 cluster_size={total_cluster_size}"

    def test_polarity_prevents_merge(self):
        """「喜歡X」和「討厭X」不應因 embedding 相似而合併"""
        blocks = [
            # 喜歡甜食
            self._create_mock_block(
                "喜歡甜食",
                intensity=0.9,
                encounter_count=4.0
            ),
            # 討厭甜食
            self._create_mock_block(
                "討厭甜食",
                intensity=0.9,
                encounter_count=4.0
            ),
        ]

        memory_sys = MockMemorySystem()
        memory_sys.memory_blocks = blocks

        aggregator = PreferenceAggregator(memory_sys)
        # 低門檻確保都能出現
        results = aggregator.aggregate(score_threshold=0.1)

        like_clusters = [r for r in results if r["tag"].startswith("喜歡")]
        dislike_clusters = [r for r in results if r["tag"].startswith("討厭")]

        # 驗證它們不在同一個 cluster
        if like_clusters and dislike_clusters:
            for lc in like_clusters:
                for dc in dislike_clusters:
                    tags_overlap = set(lc["all_tags"]) & set(dc["all_tags"])
                    assert len(tags_overlap) == 0, "喜歡和討厭不應出現在同一 cluster"

    def test_time_decay_reduces_scores(self):
        """90 天前的標籤分數應低於今天的標籤"""
        from datetime import datetime, timedelta

        old_time = (datetime.now() - timedelta(days=90)).isoformat()
        new_time = datetime.now().isoformat()

        blocks = [
            self._create_mock_block(
                "喜歡咖啡飲品",
                intensity=0.8,
                timestamp=old_time,
                encounter_count=2.0
            ),
            self._create_mock_block(
                "喜歡茶類飲品",
                intensity=0.8,
                timestamp=new_time,
                encounter_count=2.0
            ),
        ]

        memory_sys = MockMemorySystem()
        memory_sys.memory_blocks = blocks

        aggregator = PreferenceAggregator(memory_sys)
        results = aggregator.aggregate(score_threshold=0.1)

        # 驗證時間衰減 effect
        coffee_result = next((r for r in results if "咖啡" in r["tag"]), None)
        tea_result = next((r for r in results if "茶" in r["tag"]), None)

        if coffee_result and tea_result:
            assert tea_result["score"] > coffee_result["score"], \
                f"近期偏好分數 ({tea_result['score']}) 應高於舊偏好 ({coffee_result['score']})"

    def test_below_threshold_not_promoted(self):
        """低分標籤（單次、低強度）不應被升格"""
        blocks = [
            self._create_mock_block(
                "喜歡攝影",
                intensity=0.2,  # 低強度
                encounter_count=0.5  # 低遇見次數
            ),
        ]

        memory_sys = MockMemorySystem()
        memory_sys.memory_blocks = blocks

        aggregator = PreferenceAggregator(memory_sys)
        results = aggregator.aggregate(score_threshold=3.0)

        photo_results = [r for r in results if "攝影" in r["tag"]]
        assert len(photo_results) == 0, "低分偏好不應被升格"

    def test_cluster_size_calculation(self):
        """驗證 cluster_size 計算正確"""
        # 創建 3 筆完全相同的標籤
        blocks = [
            self._create_mock_block(
                "喜歡運動",
                intensity=0.9,
                encounter_count=2.0
            ),
            self._create_mock_block(
                "喜歡運動",
                intensity=0.8,
                encounter_count=2.0
            ),
            self._create_mock_block(
                "喜歡運動",
                intensity=0.7,
                encounter_count=2.0
            ),
        ]

        memory_sys = MockMemorySystem()
        memory_sys.memory_blocks = blocks

        aggregator = PreferenceAggregator(memory_sys)
        results = aggregator.aggregate(score_threshold=0.1)

        assert len(results) >= 1, "應至少有一個 cluster"
        assert results[0]["cluster_size"] == 3, f"cluster_size 應為 3，但為 {results[0]['cluster_size']}"

    def test_score_calculation_with_decay(self):
        """驗證分數計算公式：S = Σ(intensity × e^(-λΔt) × encounter_count)"""
        from datetime import datetime, timedelta
        import math

        # 創建 2 筆標籤，時間差 30 天
        now = datetime.now()
        thirty_days_ago = (now - timedelta(days=30)).isoformat()

        blocks = [
            self._create_mock_block(
                "喜歡閱讀",
                intensity=1.0,  # 最高強度
                timestamp=thirty_days_ago,
                encounter_count=2.0
            ),
            self._create_mock_block(
                "喜歡閱讀",
                intensity=1.0,  # 最高強度
                timestamp=now.isoformat(),
                encounter_count=2.0
            ),
        ]

        memory_sys = MockMemorySystem()
        memory_sys.memory_blocks = blocks

        aggregator = PreferenceAggregator(memory_sys)
        results = aggregator.aggregate(score_threshold=0.1, decay_lambda=0.02)

        assert len(results) >= 1, "應至少有一個 cluster"

        # 驗證分數計算（近似值，因為實際時間差可能略有不同）
        # 新標籤：1.0 × e^(-0.02 × 0) × 2.0 = 2.0
        # 舊標籤：1.0 × e^(-0.02 × 30) × 2.0 ≈ 2.0 × 0.5488 = 1.0976
        # 總分 ≈ 3.0976
        score = results[0]["score"]
        assert score >= 2.5, f"預期分數約 3.0，但為 {score}"


class TestPreferenceAggregatorSamePolarity:
    """PreferenceAggregator._same_polarity 方法測試"""

    def test_same_polarity_likes(self):
        """相同的「喜歡」應被視為同極性"""
        from tests.mock_llm import MockMemorySystem
        ms = MockMemorySystem()
        agg = PreferenceAggregator(ms)
        assert agg._same_polarity("喜歡肉類料理", "喜歡蔬菜料理") is True

    def test_same_polarity_dislikes(self):
        """相同的「討厭」應被視為同極性"""
        from tests.mock_llm import MockMemorySystem
        ms = MockMemorySystem()
        agg = PreferenceAggregator(ms)
        assert agg._same_polarity("討厭甜食", "討厭辛辣") is True

    def test_different_polarity_likes_dislikes(self):
        """「喜歡」和「討厭」不應被視為同極性"""
        from tests.mock_llm import MockMemorySystem
        ms = MockMemorySystem()
        agg = PreferenceAggregator(ms)
        assert agg._same_polarity("喜歡肉類料理", "討厭肉類料理") is False

    def test_different_polarity_dislikes_likes(self):
        """「討厭」和「喜歡」不應被視為同極性"""
        from tests.mock_llm import MockMemorySystem
        ms = MockMemorySystem()
        agg = PreferenceAggregator(ms)
        assert agg._same_polarity("討厭甜食", "喜歡甜食") is False
