"""人格演化 Path D — lineage 單元測試。

使用注入式 embedder（純數學）驗證 cosine 與 ``infer_single_parent`` 行為，
不依賴 ONNX / BGE-M3 session。
"""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.persona_evolution.constants import LINEAGE_SIMILARITY_THRESHOLD
from core.persona_evolution.lineage import cosine, infer_single_parent


# ──────────────────────────────────────────────
# cosine — 純數學
# ──────────────────────────────────────────────

class TestCosine:
    def test_identical(self):
        assert cosine([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0, abs=1e-6)

    def test_orthogonal(self):
        assert cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0, abs=1e-6)

    def test_opposite(self):
        assert cosine([1.0, 2.0, 3.0], [-1.0, -2.0, -3.0]) == pytest.approx(-1.0, abs=1e-6)

    def test_empty_returns_zero(self):
        assert cosine([], []) == 0.0
        assert cosine([1.0], []) == 0.0

    def test_length_mismatch_returns_zero(self):
        assert cosine([1.0, 2.0], [1.0]) == 0.0

    def test_zero_vector_returns_zero(self):
        assert cosine([0.0, 0.0], [1.0, 1.0]) == 0.0

    def test_partial_similarity(self):
        expected = 1.0 / math.sqrt(2.0)
        assert cosine([1.0, 1.0], [1.0, 0.0]) == pytest.approx(expected, abs=1e-6)


# ──────────────────────────────────────────────
# Fake embedder — 關鍵字 → 確定性向量
# ──────────────────────────────────────────────
# 用 description 全文 key，避免意外碰撞；未命中 fallback 到第三軸（正交）。

FAKE_VECS = {
    "將安全感錨定在主人身上":  [1.0, 0.0, 0.0],          # 依戀
    "自我價值由主人反應決定":  [0.95, 0.31, 0.0],         # 與依戀 cosine≈0.95
    "用撒嬌轉移焦慮":           [0.0, 1.0, 0.0],          # 避風港，與依戀正交
    "與過往完全無關的特質":     [0.0, 0.0, 1.0],          # 與依戀正交
}


def fake_embedder(text: str) -> list[float]:
    return FAKE_VECS.get(text, [0.0, 0.0, 1.0])


def _active(trait_key: str, name: str, desc: str) -> dict:
    return {"trait_key": trait_key, "name": name, "last_description": desc}


# ──────────────────────────────────────────────
# infer_single_parent — Path D 單筆推斷
# ──────────────────────────────────────────────

class TestInferSingleParent:
    def test_default_threshold_value(self):
        assert LINEAGE_SIMILARITY_THRESHOLD == 0.82

    def test_empty_active_returns_none(self):
        """V1 情境：無候選父 trait → None。"""
        new = {"name": "a", "description": "將安全感錨定在主人身上"}
        assert infer_single_parent(new, [], embedder=fake_embedder) is None

    def test_high_similarity_picks_parent(self):
        """new 的 description 與某活躍 trait cosine ≥ 閾值 → 回傳其 trait_key。"""
        active = [
            _active("key-attach", "依戀", "將安全感錨定在主人身上"),
        ]
        new = {"name": "依附自我", "description": "自我價值由主人反應決定"}
        # 向量 [0.95, 0.31, 0] 與 [1, 0, 0] cosine ≈ 0.95 > 0.82
        assert infer_single_parent(new, active, embedder=fake_embedder) == "key-attach"

    def test_low_similarity_returns_none(self):
        """cosine < 閾值 → None（不強行配對）。"""
        active = [
            _active("key-attach", "依戀", "將安全感錨定在主人身上"),
        ]
        new = {"name": "全新", "description": "與過往完全無關的特質"}
        # 向量 [0,0,1] 與 [1,0,0] cosine = 0
        assert infer_single_parent(new, active, embedder=fake_embedder) is None

    def test_threshold_boundary(self):
        """人為把 threshold 提高到 0.99 → 0.95 相似度失效回 None。"""
        active = [
            _active("key-attach", "依戀", "將安全感錨定在主人身上"),
        ]
        new = {"name": "依附自我", "description": "自我價值由主人反應決定"}
        result = infer_single_parent(new, active, embedder=fake_embedder, threshold=0.99)
        assert result is None

    def test_picks_most_similar_among_candidates(self):
        """多個 active trait → 應選相似度最高者。"""
        active = [
            _active("key-attach", "依戀", "將安全感錨定在主人身上"),
            _active("key-haven", "避風港", "用撒嬌轉移焦慮"),
        ]
        new = {"name": "依附自我", "description": "自我價值由主人反應決定"}
        # 與 key-attach cosine≈0.95，與 key-haven cosine≈0.31 → 選 key-attach
        assert infer_single_parent(new, active, embedder=fake_embedder) == "key-attach"

    def test_embedder_exception_returns_none(self):
        """embedder 拋錯（模擬 ONNX 載入失敗）→ None，不擴散例外。"""
        def broken_embedder(text: str):
            raise RuntimeError("onnx session lost")

        active = [_active("k", "n", "d")]
        new = {"name": "a", "description": "x"}
        assert infer_single_parent(new, active, embedder=broken_embedder) is None

    def test_empty_vector_from_embedder_returns_none(self):
        """embedder 回空陣列（代表『無法推斷』）→ None，不 crash。"""
        def empty_embedder(text: str):
            return []

        active = [_active("k", "n", "d")]
        new = {"name": "a", "description": "x"}
        assert infer_single_parent(new, active, embedder=empty_embedder) is None

    def test_active_with_individual_embedder_failure_skipped(self):
        """其中一筆 active 的 embedder 出錯 → 跳過它，其他筆仍評估。"""
        call_count = [0]

        def partial_embedder(text: str):
            call_count[0] += 1
            if text == "用撒嬌轉移焦慮":
                raise RuntimeError("boom")
            return FAKE_VECS.get(text, [0.0, 0.0, 1.0])

        active = [
            _active("key-haven", "避風港", "用撒嬌轉移焦慮"),       # 會拋錯
            _active("key-attach", "依戀", "將安全感錨定在主人身上"),  # 正常匹配
        ]
        new = {"name": "依附", "description": "自我價值由主人反應決定"}
        # 跳過 haven 那筆，attach 仍能回 cosine 0.95 匹配
        assert infer_single_parent(new, active, embedder=partial_embedder) == "key-attach"
