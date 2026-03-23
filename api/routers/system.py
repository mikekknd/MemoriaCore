"""系統設定、大腦反芻、偏好聚合、合成資料端點"""
import asyncio
from fastapi import APIRouter, BackgroundTasks
from api.dependencies import (
    get_memory_sys, get_storage, get_router, get_embed_model,
    get_personality_engine, reload_router, db_write_lock,
)
from api.models.requests import (
    ConfigUpdateRequest, ConsolidateRequest,
    PreferenceAggregateRequest, SyntheticRequest,
)
from api.models.responses import SystemConfigDTO
from api.session_manager import session_manager

router = APIRouter(prefix="/system", tags=["system"])


# ── 系統設定 ──────────────────────────────────────────────
@router.get("/config", response_model=SystemConfigDTO)
async def get_config():
    sto = get_storage()
    prefs = sto.load_prefs()
    return SystemConfigDTO(
        routing_config=prefs.get("routing_config", {}),
        temperature=prefs.get("temperature", 0.7),
        ui_alpha=prefs.get("ui_alpha", 0.6),
        memory_threshold=prefs.get("memory_threshold", 0.5),
        memory_hard_base=prefs.get("memory_hard_base", 0.55),
        shift_threshold=prefs.get("shift_threshold", 0.55),
        cluster_threshold=prefs.get("cluster_threshold", 0.75),
        embed_model=prefs.get("embed_model", "bge-m3:latest"),
        openai_key=prefs.get("openai_key", ""),
        or_key=prefs.get("or_key", ""),
        llamacpp_url=prefs.get("llamacpp_url", "http://localhost:8080"),
        ai_observe_enabled=prefs.get("ai_observe_enabled", True),
        reflection_threshold=prefs.get("reflection_threshold", 5),
        telegram_bot_token=prefs.get("telegram_bot_token", ""),
    )


@router.put("/config", response_model=SystemConfigDTO)
async def update_config(body: ConfigUpdateRequest):
    sto = get_storage()
    prefs = sto.load_prefs()
    update = body.model_dump(exclude_none=True)
    prefs.update(update)
    sto.save_prefs(prefs)
    # 熱重載路由
    await asyncio.to_thread(reload_router)
    return await get_config()


# ── System Prompt ─────────────────────────────────────────
@router.get("/prompt")
async def get_prompt():
    sto = get_storage()
    text = sto.load_system_prompt()
    return {"prompt": text}


@router.put("/prompt")
async def update_prompt(body: dict):
    sto = get_storage()
    sto.save_system_prompt(body.get("prompt", ""))
    return {"status": "saved"}


# ── 大腦反芻 ──────────────────────────────────────────────
def _run_consolidation(cluster_threshold: float, min_group_size: int):
    """同步：在背景執行緒中執行"""
    ms = get_memory_sys()
    rtr = get_router()
    clusters = ms.find_pending_clusters(cluster_threshold, min_group_size)
    results = []
    for cluster in clusters:
        res = ms.consolidate_and_fuse(cluster, rtr, task_key="compress")
        results.append(res)
    return results


@router.post("/consolidate")
async def consolidate(body: ConsolidateRequest, bg: BackgroundTasks):
    ms = get_memory_sys()
    clusters = ms.find_pending_clusters(body.cluster_threshold, body.min_group_size)
    if not clusters:
        return {"status": "no_clusters", "message": "沒有需要反芻的話題群組。"}
    # 在背景執行
    bg.add_task(asyncio.to_thread, _run_consolidation, body.cluster_threshold, body.min_group_size)
    return {"status": "started", "cluster_count": len(clusters)}


# ── 偏好聚合 ──────────────────────────────────────────────
def _run_preference_aggregation(score_threshold: float):
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from preference_aggregator import PreferenceAggregator
    ms = get_memory_sys()
    pref_agg = PreferenceAggregator(ms)
    promoted = pref_agg.aggregate(score_threshold=score_threshold)
    written = 0
    if promoted:
        written = pref_agg.write_to_profile(promoted)
    return {"promoted_count": len(promoted) if promoted else 0, "written": written}


@router.post("/preference-aggregate")
async def preference_aggregate(body: PreferenceAggregateRequest):
    result = await asyncio.to_thread(_run_preference_aggregation, body.score_threshold)
    return result


# ── AI 個性管理 ──────────────────────────────────────────
@router.get("/personality")
async def get_personality():
    pe = get_personality_engine()
    content = pe.load_personality_raw()
    return {"content": content}


@router.put("/personality")
async def update_personality(body: dict):
    pe = get_personality_engine()
    pe.save_personality(body.get("content", ""))
    return {"status": "saved"}


@router.post("/personality/reflect")
async def trigger_reflection():
    pe = get_personality_engine()
    rtr = get_router()
    success = await asyncio.to_thread(pe.run_reflection, rtr)
    if success:
        return {"status": "success", "message": "個性檔案已更新"}
    return {"status": "no_change", "message": "無待反思觀察或反思失敗"}


@router.get("/personality/observations")
async def get_observations():
    pe = get_personality_engine()
    ms = get_memory_sys()
    if not ms.db_path:
        return {"observations": [], "pending_count": 0}
    sto = get_storage()
    all_obs = sto.load_all_observations(ms.db_path)
    pending_count = sto.count_pending_observations(ms.db_path)
    return {"observations": all_obs, "pending_count": pending_count}


# ── 合成測試資料 ──────────────────────────────────────────
@router.post("/synthetic")
async def synthetic_data(body: SyntheticRequest):
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from tools_synthetic import generate_synthetic_data
    ms = get_memory_sys()
    ana = get_analyzer()
    rtr = get_router()
    success, overview, data = await asyncio.to_thread(
        generate_synthetic_data, body.topic, body.turns, ms, ana, rtr, body.sim_timestamp,
    )
    if success:
        return {"status": "success", "overview": overview}
    else:
        return {"status": "failed", "error": overview}
