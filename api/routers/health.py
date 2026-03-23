"""GET /health — 活性與就緒探針"""
from fastapi import APIRouter
from api.models.responses import HealthDTO
from api.dependencies import get_memory_sys, get_uptime

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthDTO)
async def health_check():
    ms = get_memory_sys()
    onnx_loaded = ms.embed_provider is not None
    db_accessible = bool(ms.db_path)
    return HealthDTO(
        onnx_loaded=onnx_loaded,
        db_accessible=db_accessible,
        uptime_seconds=round(get_uptime(), 1),
    )
