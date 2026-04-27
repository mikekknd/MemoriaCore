"""對話完成後的記憶管線（話題偏移時觸發）。

記憶管線在話題偏移後於背景執行緒中跑，包含：
1. 記憶管線 LLM（提取 entities/summary、healed_entities）
2. 區塊寫入（add_memory_block / update_memory_block）
3. 使用者畫像提取（extract_user_facts → apply_profile_facts）
4. 偏好聚合（PreferenceAggregator.aggregate → write_to_profile）

完成後將事件透過 ws_manager 推送給 WebSocket 客戶端。
"""
import asyncio
import time

from api.dependencies import get_memory_sys, get_router, get_analyzer, get_embed_model
from api.routers.chat.ws_manager import ws_manager
from core.chat_orchestrator.dataclasses import PipelineContext  # re-export，保持向後相容

__all__ = ["PipelineContext"]


# ════════════════════════════════════════════════════════════
# SECTION: 同步管線執行（背景執行緒）
# ════════════════════════════════════════════════════════════

def _run_memory_pipeline_sync(ctx: PipelineContext) -> list[dict]:
    """
    同步執行記憶管線全流程（在背景執行緒中跑）。
    包含：記憶管線 LLM → 區塊寫入 → 畫像提取 → 偏好聚合。
    回傳 pipeline_events 列表。
    """
    ms = get_memory_sys()
    analyzer = get_analyzer()
    rtr = get_router()
    embed_model = get_embed_model()
    pipeline_events = []

    msgs_to_extract = ctx.msgs_to_extract
    last_block = ctx.last_block
    sctx = ctx.session_ctx
    user_id = sctx.get("user_id", "default")
    character_id = sctx.get("character_id", "default")
    write_visibility = sctx.get("persona_face", "public")  # persona_face == write_visibility

    t_start = time.perf_counter()

    # ─── 記憶管線 LLM ───
    try:
        pipeline_res = analyzer.process_memory_pipeline(
            msgs_to_extract, last_block, rtr, embed_model, task_key="pipeline",
        )
    except Exception as e:
        pipeline_res = {"error": str(e)}

    if "error" not in pipeline_res:
        healed_list = pipeline_res.get("healed_entities")
        if healed_list and last_block:
            old_overview = last_block["overview"]
            summary_part = old_overview.split("\n[情境摘要]: ")[-1] if "\n[情境摘要]: " in old_overview else old_overview
            new_overview = f"[核心實體]: {', '.join(healed_list)}\n[情境摘要]: {summary_part}"
            ms.update_memory_block(last_block["block_id"], new_overview,
                                   user_id=user_id, character_id=character_id, visibility=write_visibility)

        for block in pipeline_res.get("new_memories", []):
            entities_str = ", ".join(block.get("entities", []))
            summary_str = block.get("summary", "無摘要")
            indices = block.get("message_indices", [])
            prefs = block.get("potential_preferences", [])
            overview = f"[核心實體]: {entities_str}\n[情境摘要]: {summary_str}"
            raw_dialogues = [msgs_to_extract[idx] for idx in indices if 0 <= idx < len(msgs_to_extract)]
            if raw_dialogues:
                ms.add_memory_block(overview, raw_dialogues, router=rtr, potential_preferences=prefs,
                                    user_id=user_id, character_id=character_id, visibility=write_visibility)

    pipeline_events.append({"type": "system_event", "action": "pipeline_complete",
                            "new_blocks": len(pipeline_res.get("new_memories", []))})

    # ─── 使用者畫像提取 ───
    try:
        current_profile = ms.storage.load_all_profiles(ms.db_path, user_id=user_id) if ms.db_path else []
        profile_facts = analyzer.extract_user_facts(msgs_to_extract, current_profile, rtr, task_key="profile")
        if profile_facts:
            ms.apply_profile_facts(profile_facts, embed_model,
                                   user_id=user_id, visibility=write_visibility)
            pipeline_events.append({"type": "system_event", "action": "profile_updated",
                                    "facts_count": len(profile_facts)})
    except Exception:
        pass

    # ─── 偏好聚合 ───
    try:
        from preference_aggregator import PreferenceAggregator
        pref_agg = PreferenceAggregator(ms)
        promoted = pref_agg.aggregate(
            score_threshold=3.0,
            user_id=user_id, character_id=character_id, visibility=write_visibility,
        )
        if promoted:
            pref_agg.write_to_profile(promoted, user_id=user_id, visibility=write_visibility)
            pipeline_events.append({"type": "system_event", "action": "preferences_aggregated",
                                    "promoted_count": len(promoted)})
    except Exception:
        pass

    pipeline_events.append({"type": "system_event", "action": "graph_updated", "entity": "memory_blocks"})

    elapsed_ms = (time.perf_counter() - t_start) * 1000
    pipeline_events.append({"type": "system_event", "action": "pipeline_timing",
                            "elapsed_ms": round(elapsed_ms, 1)})
    return pipeline_events


# ════════════════════════════════════════════════════════════
# SECTION: 非同步背景包裝器（推送 WS 事件）
# ════════════════════════════════════════════════════════════

async def _run_memory_pipeline_bg(session_id: str, ctx: PipelineContext) -> None:
    """
    非同步包裝器：在背景執行緒中跑記憶管線，完成後推送事件給 WebSocket 客戶端。
    """
    try:
        events = await asyncio.to_thread(_run_memory_pipeline_sync, ctx)
        for evt in events:
            await ws_manager.send_json(session_id, evt)
    except Exception:
        pass  # 背景管線失敗不影響已完成的對話回覆
