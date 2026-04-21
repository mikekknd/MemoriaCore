"""系統設定、大腦反芻、偏好聚合、合成資料端點"""
import asyncio
from fastapi import APIRouter, BackgroundTasks
from api.dependencies import (
    get_memory_sys, get_storage, get_router, get_embed_model,
    get_persona_sync_manager, get_character_manager, reload_router, reload_tts, db_write_lock,
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
        persona_sync_enabled=prefs.get("persona_sync_enabled", True),
        persona_sync_min_messages=prefs.get("persona_sync_min_messages", 50),
        persona_sync_max_per_day=prefs.get("persona_sync_max_per_day", 2),
        persona_sync_idle_minutes=prefs.get("persona_sync_idle_minutes", 10),
        persona_probe_url=prefs.get("persona_probe_url", "http://localhost:8089"),
        persona_sync_fragment_limit=prefs.get("persona_sync_fragment_limit", 400),
        telegram_bot_token=prefs.get("telegram_bot_token", ""),
        tavily_api_key=prefs.get("tavily_api_key", ""),
        openweather_api_key=prefs.get("openweather_api_key", ""),
        weather_city=prefs.get("weather_city", ""),
        bg_gather_interval=int(prefs.get("bg_gather_interval", 14400)),
        active_character_id=prefs.get("active_character_id", "default"),
        dual_layer_enabled=prefs.get("dual_layer_enabled", False),
        tts_enabled=prefs.get("tts_enabled", False),
        minimax_api_key=prefs.get("minimax_api_key", ""),
        minimax_voice_id=prefs.get("minimax_voice_id", "moss_audio_7c2b39d9-1006-11f1-b9c4-4ea5324904c7"),
        minimax_model=prefs.get("minimax_model", "speech-2.8-hd"),
        minimax_speed=prefs.get("minimax_speed", 1.0),
        minimax_vol=prefs.get("minimax_vol", 1.0),
        minimax_pitch=prefs.get("minimax_pitch", 0),
        browser_agent_enabled=prefs.get("browser_agent_enabled", False),
        bash_tool_enabled=prefs.get("bash_tool_enabled", False),
        bash_tool_allowed_commands=prefs.get("bash_tool_allowed_commands", []),
    )


@router.put("/config", response_model=SystemConfigDTO)
async def update_config(body: ConfigUpdateRequest):
    sto = get_storage()
    prefs = sto.load_prefs()
    update = body.model_dump(exclude_none=True)
    prefs.update(update)
    sto.save_prefs(prefs)
    # 熱重載路由 + TTS
    await asyncio.to_thread(reload_router)
    await asyncio.to_thread(reload_tts, prefs)
    return await get_config()


# ── System Prompt ─────────────────────────────────────────
@router.get("/prompt")
async def get_prompt():
    sto = get_storage()
    text = sto.load_system_prompt()
    return {"prompt": text}

@router.post("/gather_now")
async def trigger_gather_now():
    """手動觸發背景話題搜尋，並重設後續的排程時間"""
    from core.background_gatherer import force_gather_now
    
    # 調用剛才寫好的中斷重設函式
    force_gather_now()
    return {"status": "success", "message": "已觸發背景蒐集信號，系統將在接下來 10 秒內啟動搜尋。"}


@router.get("/weather-cache")
async def get_weather_cache():
    """取得今天的天氣快取"""
    from tools.weather_cache import WeatherCache
    wc = WeatherCache()
    slots = wc.get_full_today()
    current = wc.get_current_slot()
    if slots is None:
        return {"status": "no_cache", "current": None, "slots": []}
    return {"status": "ok", "current": current, "slots": slots}


@router.post("/weather-cache/refresh")
async def refresh_weather_cache():
    """強制刷新天氣快取"""
    sto = get_storage()
    prefs = sto.load_prefs()
    city = prefs.get("weather_city", "")
    api_key = prefs.get("openweather_api_key", "")
    if not city or not api_key:
        return {"status": "error", "message": "未設定 weather_city 或 openweather_api_key"}
    from tools.weather_cache import WeatherCache
    wc = WeatherCache()
    success = await asyncio.to_thread(wc.ensure_today, city, api_key)
    if success:
        return {"status": "ok", "message": f"已刷新 {city} 天氣快取"}
    return {"status": "error", "message": "刷新失敗，請檢查 API Key 與城市名稱"}


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


# ── AI 個性管理（操作 active character 的 evolved_prompt）──
@router.get("/personality")
async def get_personality():
    """回傳目前 active character 的有效人設內容與演化狀態。"""
    sto = get_storage()
    char_mgr = get_character_manager()
    prefs = sto.load_prefs()
    active_id = prefs.get("active_character_id", "default")
    char = char_mgr.get_active_character(active_id)
    evolved = char.get("evolved_prompt") or ""
    original = char.get("system_prompt", "")
    has_evolved = bool(evolved)
    return {
        "content": evolved if has_evolved else original,
        "has_evolved": has_evolved,
        "character_id": char.get("character_id"),
        "character_name": char.get("name"),
    }


@router.put("/personality")
async def update_personality(body: dict):
    """手動覆寫 active character 的 evolved_prompt。"""
    sto = get_storage()
    char_mgr = get_character_manager()
    prefs = sto.load_prefs()
    active_id = prefs.get("active_character_id", "default")
    char_mgr.set_evolved_prompt(active_id, body.get("content", ""))
    return {"status": "saved"}


@router.get("/personality/sync-status")
async def get_persona_sync_status():
    """查詢 PersonaSync 目前狀態（上次執行時間、今日次數、距上次反思訊息數）"""
    psm = get_persona_sync_manager()
    sto = get_storage()
    return psm.get_sync_status(storage=sto)


@router.post("/personality/sync-now")
async def trigger_persona_sync_now():
    """手動觸發一次 PersonaProbe 同步。
    跳過所有自動觸發條件（閒置時間、訊息累積數、每日上限），
    僅保留 persona_sync_enabled 全局開關。
    """
    psm = get_persona_sync_manager()
    sto = get_storage()
    prefs = sto.load_prefs()

    if not prefs.get("persona_sync_enabled", True):
        return {"status": "skipped", "message": "persona_sync_enabled 為 False"}

    success = await psm.run_sync(sto, prefs, count_toward_daily=False)
    if success:
        return {"status": "success", "message": "PersonaProbe 同步完成，evolved_prompt 已更新"}
    return {"status": "failed", "message": "同步失敗，請查看系統 Log 以了解詳細原因"}


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
