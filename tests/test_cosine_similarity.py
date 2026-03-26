"""純數學單元測試：cosine_similarity 與 sparse_cosine_similarity（不需 Ollama）"""
import math
import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.core_memory import MemorySystem


@pytest.fixture
def ms():
    """輕量 MemorySystem，僅用於呼叫數學方法"""
    return MemorySystem()


# ==========================================
# Dense Cosine Similarity
# ==========================================

class TestDenseCosineSimilarity:
    def test_identical_vectors(self, ms):
        v = [1.0, 2.0, 3.0, 4.0]
        assert ms.cosine_similarity(v, v) == pytest.approx(1.0, abs=1e-6)

    def test_orthogonal_vectors(self, ms):
        v1 = [1.0, 0.0, 0.0]
        v2 = [0.0, 1.0, 0.0]
        assert ms.cosine_similarity(v1, v2) == pytest.approx(0.0, abs=1e-6)

    def test_opposite_vectors(self, ms):
        v1 = [1.0, 2.0, 3.0]
        v2 = [-1.0, -2.0, -3.0]
        assert ms.cosine_similarity(v1, v2) == pytest.approx(-1.0, abs=1e-6)

    def test_dict_input_with_dense_key(self, ms):
        d1 = {"dense": [1.0, 0.0, 0.0]}
        d2 = {"dense": [1.0, 0.0, 0.0]}
        assert ms.cosine_similarity(d1, d2) == pytest.approx(1.0, abs=1e-6)

    def test_mixed_dict_and_list(self, ms):
        d = {"dense": [1.0, 0.0]}
        v = [1.0, 0.0]
        assert ms.cosine_similarity(d, v) == pytest.approx(1.0, abs=1e-6)

    def test_empty_vectors_return_zero(self, ms):
        assert ms.cosine_similarity([], []) == 0.0

    def test_mismatched_lengths_return_zero(self, ms):
        assert ms.cosine_similarity([1.0, 2.0], [1.0]) == 0.0

    def test_zero_vector_return_zero(self, ms):
        assert ms.cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0

    def test_partial_similarity(self, ms):
        v1 = [1.0, 1.0, 0.0]
        v2 = [1.0, 0.0, 0.0]
        expected = 1.0 / math.sqrt(2.0)
        assert ms.cosine_similarity(v1, v2) == pytest.approx(expected, abs=1e-6)


# ==========================================
# Sparse Cosine Similarity
# ==========================================

class TestSparseCosineSimilarity:
    def test_identical_sparse(self, ms):
        d = {"1": 0.5, "2": 0.8, "3": 0.3}
        sim = ms.sparse_cosine_similarity(d, d)
        assert sim == pytest.approx(1.0, abs=1e-6)

    def test_partial_overlap(self, ms):
        d1 = {"1": 1.0, "2": 0.5, "3": 0.3}
        d2 = {"2": 0.8, "3": 0.6, "4": 0.9}
        sim = ms.sparse_cosine_similarity(d1, d2)
        assert 0.0 < sim < 1.0

    def test_no_overlap_return_zero(self, ms):
        d1 = {"1": 1.0, "2": 0.5}
        d2 = {"3": 0.8, "4": 0.6}
        assert ms.sparse_cosine_similarity(d1, d2) == 0.0

    def test_empty_dict_return_zero(self, ms):
        assert ms.sparse_cosine_similarity({}, {"1": 0.5}) == 0.0
        assert ms.sparse_cosine_similarity({"1": 0.5}, {}) == 0.0
        assert ms.sparse_cosine_similarity({}, {}) == 0.0

    def test_single_key_overlap(self, ms):
        d1 = {"5": 0.7}
        d2 = {"5": 0.7, "6": 0.3}
        sim = ms.sparse_cosine_similarity(d1, d2)
        assert sim > 0.0

    def test_none_input_return_zero(self, ms):
        assert ms.sparse_cosine_similarity(None, {"1": 0.5}) == 0.0
        assert ms.sparse_cosine_similarity({"1": 0.5}, None) == 0.0
