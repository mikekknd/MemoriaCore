"""Pydantic DTO — 人格演化 Snapshot 對外資料契約。

用於 ``api/routers/persona_evolution.py`` 的 response_model。

Path D 欄位備註：
- ``dimension_key`` 保留原欄位名（前端穩定識別碼），但**語意已改為 trait_key**
  （UUID hex，跨版本穩定）。新增 ``trait_key`` 別名欄位與之同值，方便過渡期
  前端選擇要用哪一個。
- ``parent_key`` 反映 ``persona_traits.parent_key`` — 跨版本真實血統，不受
  單版 ``parent_name`` denormalised cache 影響。
- ``is_active`` 來自 ``persona_traits.is_active`` — B' 休眠規則下會動態變動。
"""
from typing import Optional

from pydantic import BaseModel


class PersonaDimensionDTO(BaseModel):
    dimension_key: str                    # UUID (= trait_key)；保留欄位名以相容前端
    trait_key: str                        # 別名，與 dimension_key 同值
    name: str
    confidence: float
    confidence_label: Optional[str] = None
    description: str
    parent_name: Optional[str] = None     # denormalised cache（顯示用）
    parent_key: Optional[str] = None      # 真父子關係（from persona_traits）
    is_active: bool = True


class PersonaSnapshotSummaryDTO(BaseModel):
    """清單用：不含 dimensions，只含 dimensions_count。"""

    id: int
    version: int
    timestamp: str
    summary: Optional[str] = None
    dimensions_count: int


class PersonaSnapshotDTO(BaseModel):
    """單一版本完整資料（含 dimensions）。"""

    id: int
    character_id: str
    version: int
    timestamp: str
    summary: Optional[str] = None
    evolved_prompt: Optional[str] = None
    dimensions: list[PersonaDimensionDTO] = []


class PersonaTreeNodeDTO(BaseModel):
    """前端 Force-Directed Graph 節點；比 ``PersonaDimensionDTO`` 多帶穩定 ``id``。

    ``id`` 等於 ``dimension_key``（= ``trait_key``），供 D3.js data join 使用，
    避免節點在版本切換時被重建。
    """

    id: str
    dimension_key: str
    trait_key: str
    name: str
    confidence: float
    confidence_label: Optional[str] = None
    description: str
    parent_name: Optional[str] = None
    parent_key: Optional[str] = None
    is_active: bool = True


class PersonaTreeLinkDTO(BaseModel):
    """父子邊；``source``/``target`` 皆為 node 的 ``id``（= ``dimension_key``）。"""

    source: str
    target: str


class PersonaTreeDTO(BaseModel):
    """Unity / 前端 Force-Directed Graph 專用結構。

    後端在組裝時，已由 ``parent_key`` 推導出 ``links`` 陣列，前端可直接餵給 D3。
    """

    version: int
    timestamp: str
    summary: Optional[str] = None
    nodes: list[PersonaTreeNodeDTO] = []
    links: list[PersonaTreeLinkDTO] = []


class TraitTimelinePointDTO(BaseModel):
    """Trait 折線圖單一資料點（某版的 confidence）。"""

    version: int
    timestamp: str
    confidence: float
    confidence_label: Optional[str] = None


class TraitTimelineDTO(BaseModel):
    """指定 trait 在所有版本的 confidence 變化序列。

    confidence == "none" 的版本因不寫 ``persona_dimensions`` row，在此序列中
    缺席（該版 LLM 仍注意到此 trait，但未表現到需要記錄的程度）。
    """

    character_id: str
    trait_key: str
    points: list[TraitTimelinePointDTO] = []


class TraitListItemDTO(BaseModel):
    """``GET /traits`` 端點回傳結構（debug / 前端清單用）。"""

    trait_key: str
    name: str
    last_description: str
    created_version: int
    last_active_version: int
    parent_key: Optional[str] = None
    is_active: bool = True
