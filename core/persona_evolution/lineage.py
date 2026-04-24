"""人格演化 Path D — 血統推斷層。

職責：
- 以 BGE-M3 ONNX（已在 ``core.llm_gateway`` 註冊）取 dense 向量。
- 計算描述相似度，在 LLM 回傳的 ``parent_key`` 無效時做 fallback 推斷。

Path D 下 LLM 會自選 ``parent_key``；本模組只在「LLM 填錯 / 未填」時才被呼叫，
作為單筆 new_trait 對活躍 trait 清單的 cosine 最相近匹配。相較於舊 6 維度
情境的整批配對（``infer_parents``），新簽章 ``infer_single_parent`` 精準到
單一條目、返回單一 ``parent_key``（或 None）。
"""
from typing import Callable, Iterable

import numpy as np

from core.persona_evolution.constants import LINEAGE_SIMILARITY_THRESHOLD


def cosine(a: Iterable[float], b: Iterable[float]) -> float:
    """純數學 cosine similarity。空向量或長度不一致回傳 0.0。"""
    va = np.asarray(list(a), dtype=np.float32)
    vb = np.asarray(list(b), dtype=np.float32)
    if va.size == 0 or vb.size == 0 or va.size != vb.size:
        return 0.0
    na = float(np.linalg.norm(va))
    nb = float(np.linalg.norm(vb))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))


def bge_m3_embed(text: str) -> list[float]:
    """取 BGE-M3 ONNX dense 向量；首次呼叫會觸發 ONNX session 初始化。

    ONNX 載入失敗時回傳空陣列（呼叫端應視為「無法推斷」而非拋錯）。
    """
    # 延遲 import — 讓 lineage 邏輯測試不被 ONNX 依賴綁住。
    try:
        from core.llm_gateway import get_bge_m3_onnx_instance
        session, tokenizer = get_bge_m3_onnx_instance()
        inputs = tokenizer(
            text or "none",
            padding="longest",
            truncation=True,
            max_length=8192,
            return_tensors="np",
        )
        ort_inputs = {
            "input_ids": inputs["input_ids"].astype(np.int64),
            "attention_mask": inputs["attention_mask"].astype(np.int64),
        }
        outputs = session.run(None, ort_inputs)
        return [float(x) for x in outputs[0][0]]
    except Exception:
        return []


def infer_single_parent(
    new_trait: dict,
    active_traits: list[dict],
    embedder: Callable[[str], list[float]] | None = None,
    threshold: float = LINEAGE_SIMILARITY_THRESHOLD,
) -> str | None:
    """針對單一 new_trait 在 ``active_traits`` 中找最相似者；回傳其 ``trait_key`` 或 None。

    Path D 用途：LLM 填的 ``parent_key`` 驗證不存在時的 fallback。不涉及「同名繼承」
    邏輯——那屬於 ``persona_traits`` 的 update 路徑（由 ``save_trait_snapshot`` 處理）。

    規則：
    - ``active_traits`` 為空 → None（V1 情境）。
    - embedder 或其依賴失敗（回空向量）→ None（寧可孤立節點也別崩 sync）。
    - 比對 ``new_trait.description`` 與 ``active_traits[*].last_description``。
    - 最高 cosine ``>= threshold`` 才採用；否則 None。

    Args:
        new_trait: ``{"name", "description", ...}``
        active_traits: ``[{"trait_key", "name", "last_description", ...}, ...]``
        embedder: 可注入假 embedder 以解耦 ONNX；預設呼叫 ``bge_m3_embed``。
        threshold: cosine 閾值，預設從 ``constants.LINEAGE_SIMILARITY_THRESHOLD``。
    """
    if not active_traits:
        return None

    embed = embedder or bge_m3_embed
    try:
        new_vec = embed(new_trait.get("description", ""))
    except Exception:
        return None
    if not new_vec:
        return None

    best_key: str | None = None
    best_score: float = -1.0
    for t in active_traits:
        try:
            pv = embed(t.get("last_description", ""))
        except Exception:
            continue
        if not pv:
            continue
        score = cosine(new_vec, pv)
        if score > best_score:
            best_key, best_score = t.get("trait_key"), score

    if best_score >= threshold and best_key:
        return best_key
    return None
