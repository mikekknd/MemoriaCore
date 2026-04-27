"""人格演化快照 REST API。

掛在 ``/system/personality`` 之下：
- ``GET /snapshots``                      — 版本清單摘要
- ``GET /snapshots/latest``               — 最新版完整內容
- ``GET /snapshots/latest/tree``          — 最新版 Force-Directed Graph 結構
- ``GET /snapshots/{version}``            — 指定版本完整內容
- ``GET /snapshots/{version}/tree``       — 指定版本 Force-Directed Graph 結構
- ``GET /traits``                         — 該角色 trait 清單（debug / 除錯用）
- ``GET /traits/timeline``                — 指定 trait_key 的 confidence 折線資料

Snapshot 寫入統一由 ``core.persona_sync.PersonaSyncManager.run_sync`` 觸發，
本 router 只做讀取，不提供 POST / PUT。

Path D 備註：前版 ``/dimensions/timeline`` 端點已移除——名字可能跨版本改寫，
真正穩定的識別碼是 ``trait_key``（UUID），故改以 trait_key 查詢。
"""
from fastapi import APIRouter, HTTPException, Query

from api.dependencies import get_persona_snapshot_store, get_storage
from api.models.persona_evolution import (
    PersonaDimensionDTO,
    PersonaSnapshotDTO,
    PersonaSnapshotSummaryDTO,
    PersonaTreeDTO,
    PersonaTreeLinkDTO,
    PersonaTreeNodeDTO,
    TraitListItemDTO,
    TraitTimelineDTO,
    TraitTimelinePointDTO,
)

router = APIRouter(prefix="/system/personality", tags=["personality"])


def _dim_dto(raw: dict) -> PersonaDimensionDTO:
    """把 ``_load_dimensions_for`` 回傳的 dict 轉 DTO，補上 ``trait_key`` 別名。"""
    return PersonaDimensionDTO(
        dimension_key=raw["dimension_key"],
        trait_key=raw["dimension_key"],  # 同值別名
        name=raw["name"],
        confidence=raw["confidence"],
        confidence_label=raw.get("confidence_label"),
        description=raw["description"],
        parent_name=raw.get("parent_name"),
        parent_key=raw.get("parent_key"),
        is_active=raw.get("is_active", True),
    )


def _node_dto(raw: dict) -> PersonaTreeNodeDTO:
    return PersonaTreeNodeDTO(
        id=raw["dimension_key"],
        dimension_key=raw["dimension_key"],
        trait_key=raw["dimension_key"],
        name=raw["name"],
        confidence=raw["confidence"],
        confidence_label=raw.get("confidence_label"),
        description=raw["description"],
        parent_name=raw.get("parent_name"),
        parent_key=raw.get("parent_key"),
        is_active=raw.get("is_active", True),
    )


def _to_snapshot_dto(raw: dict) -> PersonaSnapshotDTO:
    return PersonaSnapshotDTO(
        id=raw["id"],
        character_id=raw["character_id"],
        version=raw["version"],
        timestamp=raw["timestamp"],
        summary=raw.get("summary"),
        evolved_prompt=raw.get("evolved_prompt"),
        dimensions=[_dim_dto(d) for d in raw.get("dimensions", [])],
    )


def _tree_to_dto(tree: dict) -> PersonaTreeDTO:
    """把 store 回傳的 tree dict 包裝成 DTO，並由 ``parent_key`` 推導 links。

    Path D 下 ``parent_key`` 是真父子關係（``persona_traits`` 表）。只在父節點
    也出現在同一張快照時才加 link，避免指向目前版本之外的節點產生孤立邊。
    """
    raw_nodes = tree.get("nodes", [])
    node_keys = {n["dimension_key"] for n in raw_nodes}
    nodes = [_node_dto(n) for n in raw_nodes]
    links: list[PersonaTreeLinkDTO] = []
    for n in raw_nodes:
        parent_key = n.get("parent_key")
        if not parent_key or parent_key not in node_keys:
            continue
        links.append(PersonaTreeLinkDTO(source=parent_key, target=n["dimension_key"]))
    return PersonaTreeDTO(
        version=tree["version"],
        timestamp=tree["timestamp"],
        summary=tree.get("summary"),
        nodes=nodes,
        links=links,
    )


@router.get("/snapshots", response_model=list[PersonaSnapshotSummaryDTO])
async def list_snapshots(
    character_id: str = Query(..., description="角色 ID"),
    persona_face: str = Query("public", description="human face（public / private）"),
):
    """回傳該角色所有 snapshot 摘要（不含 dimensions 內容），版本遞增排序。"""
    sto = get_storage()
    rows = sto.list_persona_snapshots(character_id, persona_face=persona_face)
    return [PersonaSnapshotSummaryDTO(**r) for r in rows]


@router.get("/snapshots/latest", response_model=PersonaSnapshotDTO)
async def get_latest_snapshot(
    character_id: str = Query(..., description="角色 ID"),
    persona_face: str = Query("public", description="human face（public / private）"),
):
    """回傳該角色最新版 snapshot（含所有 dimensions）；無紀錄時回 404。"""
    sto = get_storage()
    raw = sto.get_latest_persona_snapshot(character_id, persona_face=persona_face)
    if raw is None:
        raise HTTPException(status_code=404, detail="no snapshot for character")
    return _to_snapshot_dto(raw)


@router.get("/snapshots/latest/tree", response_model=PersonaTreeDTO)
async def get_latest_snapshot_tree(
    character_id: str = Query(..., description="角色 ID"),
    persona_face: str = Query("public", description="human face（public / private）"),
):
    """回傳最新版 snapshot 的 Force-Directed Graph 結構。

    ⚠️ 路由順序：此路由必須在 ``/snapshots/{version}/tree`` 之前註冊，否則
    FastAPI 會把 ``latest`` 當成 ``{version}: int`` 導致 422。
    """
    store = get_persona_snapshot_store()
    tree = store.get_latest_tree(character_id, persona_face=persona_face)
    if tree is None:
        raise HTTPException(status_code=404, detail="no snapshot for character")
    return _tree_to_dto(tree)


@router.get("/snapshots/{version}", response_model=PersonaSnapshotDTO)
async def get_snapshot(
    version: int,
    character_id: str = Query(..., description="角色 ID"),
    persona_face: str = Query("public", description="human face（public / private）"),
):
    """回傳指定版本的 snapshot（含 dimensions）；不存在回 404。"""
    sto = get_storage()
    raw = sto.get_persona_snapshot(character_id, version, persona_face=persona_face)
    if raw is None:
        raise HTTPException(status_code=404, detail="snapshot not found")
    return _to_snapshot_dto(raw)


@router.get("/snapshots/{version}/tree", response_model=PersonaTreeDTO)
async def get_snapshot_tree(
    version: int,
    character_id: str = Query(..., description="角色 ID"),
    persona_face: str = Query("public", description="human face（public / private）"),
):
    """回傳指定版本的 Force-Directed Graph 結構。"""
    store = get_persona_snapshot_store()
    tree = store.get_tree(character_id, version, persona_face=persona_face)
    if tree is None:
        raise HTTPException(status_code=404, detail="snapshot not found")
    return _tree_to_dto(tree)


@router.get("/traits", response_model=list[TraitListItemDTO])
async def list_traits(
    character_id: str = Query(..., description="角色 ID"),
    active_only: bool = Query(True, description="是否只回傳活躍 trait（預設 True）"),
    persona_face: str = Query("public", description="human face（public / private）"),
):
    """回傳該角色的 trait 清單（按 ``last_active_version DESC``）。

    ``active_only=True``（預設）：只含 ``is_active = 1``；``False`` 則含所有
    歷史 trait（含已 B' 休眠）。供前端 debug / 管理介面使用。
    """
    sto = get_storage()
    rows = (
        sto.get_active_traits(character_id, persona_face=persona_face)
        if active_only
        else sto.get_all_traits(character_id, persona_face=persona_face)
    )
    return [TraitListItemDTO(**r) for r in rows]


@router.get("/traits/timeline", response_model=TraitTimelineDTO)
async def get_trait_timeline(
    character_id: str = Query(..., description="角色 ID"),
    trait_key: str = Query(..., description="trait_key（UUID hex，跨版本穩定）"),
    persona_face: str = Query("public", description="human face（public / private）"),
):
    """回傳指定 trait 在所有版本的 confidence 變化；無紀錄時 points 為空陣列。

    confidence == "none" 的版本不會出現在 points 中（該版 LLM 仍注意到此 trait
    但未表現到要寫入 dim row 的程度）。
    """
    sto = get_storage()
    points = sto.get_trait_timeline(character_id, trait_key, persona_face=persona_face)
    return TraitTimelineDTO(
        character_id=character_id,
        trait_key=trait_key,
        points=[TraitTimelinePointDTO(**p) for p in points],
    )
