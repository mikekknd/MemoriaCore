"""人格演化 Path D — ``TraitDiff`` / ``TraitUpdate`` / ``NewTrait`` 資料結構。

本模組僅定義 LLM 輸出解析後的中間格式，與 SQL 解耦。``StorageManager.save_trait_snapshot``
接受的 ``updates`` / ``new_traits`` 以 plain dict 傳遞，以便 PersonaSyncManager 可在
trait_key fallback（cosine 推斷 parent）後再丟進 DB。

欄位命名：
- ``trait_key`` — 跨版本唯一 UUID（hex，32 字元），由 V1 / Vn 的後端生成器指定。
- ``parent_key`` — 指向 ``persona_traits.trait_key``；LLM 自選或後端 fallback 決定。
- ``confidence`` — 仍採 4 級字串（high/medium/low/none），與既有 ``CONFIDENCE_MAP`` 對齊。
"""
from typing import Literal

from pydantic import BaseModel, Field


ConfidenceLabel = Literal["high", "medium", "low", "none"]


class TraitUpdate(BaseModel):
    """對既有 trait 的 confidence 調整；LLM 不可在此改 name/description（後端忽略）。"""
    trait_key: str
    confidence: ConfidenceLabel


class NewTrait(BaseModel):
    """本版新建 trait；``parent_key`` 可指向任何歷史 trait_key 或 None（root）。"""
    name: str
    description: str
    confidence: ConfidenceLabel
    parent_key: str | None = None


class TraitDiff(BaseModel):
    """Vn LLM 輸出的完整 diff；V1 情境下 ``updates`` 為空。"""
    updates: list[TraitUpdate] = Field(default_factory=list)
    new_traits: list[NewTrait] = Field(default_factory=list)
