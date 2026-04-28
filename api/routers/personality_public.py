"""一般使用者可讀的 public face 人格演化 API。"""
from fastapi import APIRouter, HTTPException, Query

from api.dependencies import get_character_manager, get_persona_snapshot_store, get_storage
from api.models.persona_evolution import PersonaSnapshotSummaryDTO, PersonaTreeDTO
from api.models.responses import PublicCharacterDTO
from api.routers.persona_evolution import _tree_to_dto

router = APIRouter(prefix="/personality-public", tags=["personality-public"])


def _reject_private_face(persona_face: str | None) -> None:
    if persona_face and persona_face != "public":
        raise HTTPException(status_code=403, detail="僅允許讀取 public face")


@router.get("/characters", response_model=list[PublicCharacterDTO])
async def list_public_characters():
    mgr = get_character_manager()
    characters = []
    for char in mgr.load_characters():
        character_id = char.get("character_id")
        name = char.get("name")
        if character_id and name:
            characters.append(PublicCharacterDTO(character_id=character_id, name=name))
    return characters


@router.get("/snapshots", response_model=list[PersonaSnapshotSummaryDTO])
async def list_public_snapshots(
    character_id: str = Query(..., description="角色 ID"),
    persona_face: str | None = Query(None, description="僅允許 public"),
):
    _reject_private_face(persona_face)
    sto = get_storage()
    rows = sto.list_persona_snapshots(character_id, persona_face="public")
    return [PersonaSnapshotSummaryDTO(**r) for r in rows]


@router.get("/snapshots/latest/tree", response_model=PersonaTreeDTO)
async def get_latest_public_snapshot_tree(
    character_id: str = Query(..., description="角色 ID"),
    persona_face: str | None = Query(None, description="僅允許 public"),
):
    _reject_private_face(persona_face)
    store = get_persona_snapshot_store()
    tree = store.get_latest_tree(character_id, persona_face="public")
    if tree is None:
        raise HTTPException(status_code=404, detail="no public snapshot for character")
    return _tree_to_dto(tree)


@router.get("/snapshots/{version}/tree", response_model=PersonaTreeDTO)
async def get_public_snapshot_tree(
    version: int,
    character_id: str = Query(..., description="角色 ID"),
    persona_face: str | None = Query(None, description="僅允許 public"),
):
    _reject_private_face(persona_face)
    store = get_persona_snapshot_store()
    tree = store.get_tree(character_id, version, persona_face="public")
    if tree is None:
        raise HTTPException(status_code=404, detail="public snapshot not found")
    return _tree_to_dto(tree)
