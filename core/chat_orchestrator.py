"""
異步雙層 Agent 語音互動架構 — 核心模組。

將對話編排拆分為三個獨立模組：
  Module A: Router Agent（意圖路由層）
  Module B: Async Middleware（非同步中介層）
  Module C: Persona Synthesis Agent（角色渲染層）
"""
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable

from core.system_logger import SystemLogger


# ── 資料結構 ──────────────────────────────────────────────

@dataclass
class RouterResult:
    """Module A 的輸出。"""
    needs_tools: bool
    tool_calls: list[dict] = field(default_factory=list)
    thinking_speech: str = ""


@dataclass
class ToolContext:
    """Module B 的輸出。"""
    tool_results: list[dict] = field(default_factory=list)   # [{"tool_name": str, "result": str}]
    tool_results_formatted: str = ""                          # 格式化文字，注入 Module C
    thinking_speech_sent: str = ""                            # 已推播給前端的過渡語


@dataclass
class PersonaResult:
    """Module C 的輸出。"""
    reply_text: str = ""
    new_entities: list[str] = field(default_factory=list)
    inner_thought: str | None = None
    status_metrics: dict | None = None
    tone: str | None = None
    speech: str | None = None


# ── Module A: Router Agent ────────────────────────────────

ROUTER_SYSTEM_PROMPT_TEMPLATE = """你是意圖路由代理。你的唯一任務是判斷使用者的問題是否需要呼叫外部工具。
角色語氣提示：{char_hint}

規則：
1. 若使用者問題需要即時資訊（天氣、新聞、事實查證、網路搜尋），你必須呼叫對應工具。
2. 呼叫工具時，必須同時用角色語氣說一句簡短等待語（20字以內），放在回覆文字中。
3. 若不需要工具，不要呼叫任何工具，也不要輸出任何文字。
4. 你不負責回答使用者的問題，只負責路由判斷。"""


def run_router_agent(
    user_prompt: str,
    char_hint: str,
    tools_list: list[dict],
    router,
    temperature: float = 0.7,
) -> RouterResult:
    """
    Module A — 輕量 LLM 判斷是否需要工具，並產生過渡語音。

    Args:
        user_prompt: 使用者的原始輸入。
        char_hint: 角色語氣的一行描述（例如「傲嬌女僕，語氣帶點不耐煩但其實很認真」）。
        tools_list: 所有可用的 Tool Schema 列表。
        router: LLMRouter 實例。
        temperature: LLM 溫度參數。

    Returns:
        RouterResult — needs_tools / tool_calls / thinking_speech。
    """
    sys_prompt = ROUTER_SYSTEM_PROMPT_TEMPLATE.format(char_hint=char_hint)
    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_prompt},
    ]

    try:
        content, tool_calls = router.generate_with_tools(
            "router", messages, tools=tools_list, temperature=temperature,
        )
    except Exception as e:
        SystemLogger.log_error("RouterAgent", f"{type(e).__name__}: {e}")
        return RouterResult(needs_tools=False)

    if tool_calls:
        thinking_speech = (content or "").strip()
        return RouterResult(
            needs_tools=True,
            tool_calls=tool_calls,
            thinking_speech=thinking_speech,
        )

    return RouterResult(needs_tools=False)


# ── Module B: Async Middleware ────────────────────────────

def run_middleware(
    router_result: RouterResult,
    on_thinking_speech: Callable[[str], None] | None = None,
    on_tool_status: Callable[[dict], None] | None = None,
) -> ToolContext:
    """
    Module B — 同步中介層：立即推播過渡語音，並行執行工具呼叫。

    Args:
        router_result: Module A 的輸出。
        on_thinking_speech: 回呼，立即將過渡語推播給前端 TTS。
        on_tool_status: 回呼，推送工具呼叫狀態事件。

    Returns:
        ToolContext — 工具結果 + 已推播的過渡語。
    """
    from tools_tavily import execute_tool_call

    thinking_speech = router_result.thinking_speech

    # 1) 立即推播過渡語音
    if thinking_speech and on_thinking_speech:
        on_thinking_speech(thinking_speech)

    # 2) 通知前端每個工具的呼叫狀態
    tool_calls = router_result.tool_calls
    if on_tool_status:
        for tc in tool_calls:
            tool_name = tc.get("function", {}).get("name", "unknown")
            query = tc.get("function", {}).get("arguments", {}).get("query", "")
            on_tool_status({
                "type": "tool_status",
                "action": "calling",
                "tool_name": tool_name,
                "message": f"正在搜尋：{query}" if query else f"正在呼叫工具：{tool_name}",
            })

    # 3) ThreadPoolExecutor 並行執行所有工具
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=len(tool_calls) or 1) as executor:
        future_to_tc = {
            executor.submit(execute_tool_call, tc): tc
            for tc in tool_calls
        }
        for future in as_completed(future_to_tc):
            tc = future_to_tc[future]
            tool_name = tc.get("function", {}).get("name", "unknown")
            try:
                result_text = future.result()
            except Exception as e:
                result_text = json.dumps({"error": f"工具執行失敗: {e}"}, ensure_ascii=False)
            results.append({"tool_name": tool_name, "result": result_text})

    # 4) 通知前端工具執行完成
    if on_tool_status:
        on_tool_status({
            "type": "tool_status",
            "action": "complete",
            "message": "搜尋完成，正在整理回覆...",
        })

    # 5) 格式化工具結果
    formatted_parts = []
    for r in results:
        formatted_parts.append(f"【{r['tool_name']} 查詢結果】\n{r['result']}")
    formatted_text = (
        "[系統自動查詢結果 — 以下資料由外部工具回傳，非使用者輸入]\n"
        + "\n".join(formatted_parts)
        + "\n[查詢結果結束]"
    )

    return ToolContext(
        tool_results=results,
        tool_results_formatted=formatted_text,
        thinking_speech_sent=thinking_speech,
    )


# ── Module C: Persona Synthesis Agent ─────────────────────

def run_persona_agent(
    user_prompt: str,
    api_messages: list[dict],
    tool_context: ToolContext | None,
    chat_schema: dict,
    router,
    temperature: float = 0.7,
) -> tuple[str | None, PersonaResult | None]:
    """
    Module C — 角色渲染層：載入完整角色設定，生成結構化 JSON 回覆。

    Returns:
        (raw_llm_response, error_result) — 成功時回傳 (str, None)，
        失敗時回傳 (None, PersonaResult)。呼叫端負責解析及計時。
    """
    # 組裝最終 messages：注入 thinking_speech 和工具結果
    final_messages = list(api_messages)  # shallow copy

    if tool_context:
        # 在最後一條 user message 之前注入 assistant 的 thinking_speech
        # 讓 Module C 知道它已經說過等待語
        if tool_context.thinking_speech_sent:
            # 找到最後一條 user message 的位置
            insert_idx = len(final_messages) - 1
            for i in range(len(final_messages) - 1, -1, -1):
                if final_messages[i].get("role") == "user":
                    insert_idx = i
                    break
            final_messages.insert(insert_idx, {
                "role": "assistant",
                "content": tool_context.thinking_speech_sent,
            })

        # 在 user message 之後追加工具結果（獨立的 user 訊息，明確標記為系統工具回傳）
        final_messages.append({
            "role": "user",
            "content": (
                f"[系統通知：以下是根據你的工具查詢自動回傳的外部數據，請依據此數據回答使用者的問題]\n"
                f"{tool_context.tool_results_formatted}"
            ),
        })

    # 呼叫 LLM — 不帶 tools，帶 response_format
    try:
        full_res = router.generate(
            "chat", final_messages, temperature=temperature,
            response_format=chat_schema,
        )
    except Exception as e:
        SystemLogger.log_error("PersonaAgent", f"{type(e).__name__}: {e}")
        return None, PersonaResult(reply_text=f"生成錯誤: {e}")

    # 回傳原始 LLM 回應，讓呼叫端可以分別計時解析步驟
    return full_res, None


def _parse_persona_response(full_res: str | None) -> PersonaResult:
    """從 LLM 原始回應中解析結構化 JSON。"""
    if not full_res:
        return PersonaResult(reply_text="（無回應）")

    start = full_res.find('{')
    if start == -1:
        return PersonaResult(reply_text=full_res)

    try:
        parsed, _ = json.JSONDecoder().raw_decode(full_res, start)
        return PersonaResult(
            reply_text=parsed.get("reply", "解析錯誤"),
            new_entities=parsed.get("extracted_entities", []),
            inner_thought=parsed.get("internal_thought"),
            status_metrics=parsed.get("status_metrics"),
            tone=parsed.get("tone"),
            speech=parsed.get("speech"),
        )
    except Exception:
        return PersonaResult(reply_text=full_res)


# ── 頂層協調函式 ──────────────────────────────────────────

def run_dual_layer_orchestration(
    session_messages: list[dict],
    last_entities: list[str],
    user_prompt: str,
    user_prefs: dict,
    on_event: Callable[[dict], None] | None = None,
):
    """
    異步雙層 Agent 對話編排 — 取代 _run_chat_orchestration()。

    維持與原函式相同的參數簽名。
    回傳 10-tuple：原 9 元素 + thinking_speech。
    """
    from api.dependencies import (
        get_memory_sys, get_storage, get_router, get_analyzer,
        get_embed_model, get_personality_engine, get_character_manager,
    )
    from api.routers.chat_ws import StepTimer

    ms = get_memory_sys()
    analyzer = get_analyzer()
    rtr = get_router()
    storage = get_storage()
    embed_model = get_embed_model()

    shift_threshold = user_prefs.get("shift_threshold", 0.55)
    ui_alpha = user_prefs.get("ui_alpha", 0.6)
    memory_hard_base = user_prefs.get("memory_hard_base", 0.55)
    memory_threshold = user_prefs.get("memory_threshold", 0.5)
    temperature = user_prefs.get("temperature", 0.7)

    topic_shifted = False
    pipeline_data = None
    timer = StepTimer()
    thinking_speech = ""

    # ─── Phase 0: 話題偏移偵測（與原邏輯相同） ───
    with timer.step("話題偏移偵測 (Topic Shift Detection)"):
        is_shift, cohesion_score = analyzer.detect_topic_shift(
            session_messages, embed_model, threshold=shift_threshold,
        )

    if is_shift:
        topic_shifted = True
        import copy
        msgs_to_extract = [{"role": m["role"], "content": m["content"]}
                           for m in session_messages[:-1]]
        last_block = copy.deepcopy(ms.memory_blocks[-1]) if ms.memory_blocks else None
        pipeline_data = (msgs_to_extract, last_block)

    # ─── Phase 0: 雙軌檢索（與原邏輯相同） ───
    with timer.step("查詢擴展 (Query Expansion LLM)"):
        expand_res = ms.expand_query(user_prompt, session_messages, rtr, task_key="expand")
    inherited_str = " ".join(last_entities)
    combined_keywords = f"{expand_res['expanded_keywords']} {inherited_str}".strip()

    f_alpha = ui_alpha
    f_base = max(0.50, memory_hard_base - (0.05 * expand_res["entity_confidence"]))

    with timer.step("情境記憶檢索 (Memory Block Search)"):
        blocks = ms.search_blocks(user_prompt, combined_keywords, 2, f_alpha, 0.5, memory_threshold, f_base)

    with timer.step("核心認知檢索 (Core Memory Search)"):
        core_insights = ms.search_core_memories(user_prompt, top_k=1, threshold=0.45)

    core_ctx = ""
    core_debug_text = "未觸發核心認知。"
    if core_insights:
        core_ctx = f"【使用者核心資訊】：{core_insights[0]['insight']}\n"
        core_debug_text = f"觸發認知: {core_insights[0]['insight']} (Score: {core_insights[0]['score']:.3f})"

    with timer.step("使用者偏好檢索 (Profile Search)"):
        profile_matches = ms.search_profile_by_query(user_prompt, top_k=3, threshold=0.5)
    profile_ctx = ""
    profile_debug_text = "未觸發使用者偏好。"
    if profile_matches:
        profile_lines = [f"- {pm['fact_key']}: {pm['fact_value']}" for pm in profile_matches]
        profile_ctx = f"【使用者相關偏好】\n" + "\n".join(profile_lines) + "\n"
        profile_debug_text = f"觸發 {len(profile_matches)} 筆偏好: " + ", ".join(
            [f"{pm['fact_key']}={pm['fact_value']} ({pm['score']:.3f})" for pm in profile_matches])

    block_details = []
    mem_ctx = "無相關記憶。"
    if blocks:
        formatted_blocks = []
        for i, block in enumerate(blocks):
            raw_text = "\n".join([f"  - {m['role']}: {m['content']}" for m in block["raw_dialogues"] if "role" in m])
            formatted_blocks.append(
                f"【情境回憶 {i + 1}】\n[時間]: {block['timestamp']}\n[概覽]: {block['overview']}\n[當時的詳細對話]:\n{raw_text}")
            overview_header = block['overview'].split('\n')[0] if '\n' in block['overview'] else block['overview']
            block_details.append({
                "id": i + 1, "overview": overview_header,
                "hybrid": block.get("_debug_score", 0),
                "dense": block.get("_debug_raw_sim", 0),
                "sparse": block.get("_debug_sparse_raw", 0),
                "recency": block.get("_debug_recency", 0),
                "importance": block.get("_debug_importance", 0),
            })
        mem_ctx = "\n\n".join(formatted_blocks)

    # ─── Phase 0: System Prompt 組裝（與原邏輯相同） ───
    static_profile = ms.get_static_profile_prompt()
    static_profile_block = f"\n{static_profile}\n" if static_profile else ""

    pe = get_personality_engine()
    personality_ctx = pe.get_personality_prompt()
    personality_block = f"\n【AI個性記憶】\n{personality_ctx}\n" if personality_ctx else ""

    proactive_topics_block = ms.get_proactive_topics_prompt(limit=1)
    if proactive_topics_block:
        proactive_topics_block = f"\n{proactive_topics_block}\n"

    weather_block = ""
    try:
        from weather_cache import WeatherCache
        weather_summary = WeatherCache().get_current_slot()
        if weather_summary:
            weather_block = f"\n【即時天氣資訊】\n{weather_summary}\n"
    except Exception:
        pass

    char_mgr = get_character_manager()
    active_char_id = user_prefs.get("active_character_id", "default")
    active_char = char_mgr.get_active_character(active_char_id)
    metrics = active_char.get("metrics", ["professionalism"])
    allowed_tones = active_char.get("allowed_tones", ["Neutral", "Happy", "Professional"])
    speech_rules = active_char.get("speech_rules", "Traditional Chinese. NO EMOJIS.")
    char_tts_lang = active_char.get("tts_language", "")
    char_sys_prompt = active_char.get("system_prompt", storage.load_system_prompt())
    char_name = active_char.get("name", "助理")

    metrics_str = ", ".join(metrics)
    tones_str = "/".join(allowed_tones)

    speech_instruction = ""
    speech_json = ""
    if char_tts_lang:
        speech_instruction = f"4. `speech`: 這是專門提供給語音合成 (TTS) 的發音文本，請嚴格遵守以下角色發音規則：[{char_tts_lang}] {speech_rules}\n5. `reply`: 這是顯示給使用者看的自然語言回覆（字幕），需對應 `speech` 的語意，可根據需要輸出翻譯視角（例如 speech 為 {char_tts_lang} 發音，reply 則為中文翻譯字幕）。"
        speech_json = f'  "speech": "符合 {char_tts_lang} 的發音文本",\n'
    else:
        speech_instruction = f"4. `reply`: 這是顯示給使用者看的自然語言回覆（螢幕字幕文字）。文字與語氣規則：{speech_rules}"
        speech_json = ""

    sys_prompt = f"""{char_sys_prompt}
{personality_block}{static_profile_block}{weather_block}
{core_ctx}{profile_ctx}{proactive_topics_block}
【系統動態規則】
1. 實體釐清：若包含指代不明的實體，請自然發問釐清。但若使用者已明確表示「忘記、不知道或不想討論」，請立即停止追問並順應話題。
2. 泛化工具調用：本次對話中，你不需要自行呼叫任何工具。如果有外部工具查詢結果，系統會自動提供給你。

【系統核心指令】：綜合以下情境記憶區塊來回答使用者。
[情境記憶區]
{mem_ctx}

【心理活動與狀態評估規則】
在給出最終回答(`reply`)之前，你必須先進行內部心理狀態的計算，這將決定你的說話語氣與態度：
1. `internal_thought`: (最多 40 字) 請分析使用者的潛在意圖，並結合你目前的人格設定寫下你的內心獨白或心理衝突。
2. `status_metrics`: 根據當下情境為你的心理指標打分 (0-100)。目前的追蹤指標有：[{metrics_str}]。
3. `tone`: 從 [{tones_str}] 中選出一個最符合當下心境的語氣。
{speech_instruction}

【強制輸出格式】：你的回覆必須是合法的 JSON，禁止輸出任何額外說明或 Markdown：
{{
  "internal_thought": "...",
  "status_metrics": {{ "{metrics[0] if metrics else 'score'}": 50 }},
  "tone": "{allowed_tones[0] if allowed_tones else 'Neutral'}",
{speech_json}  "reply": "你的自然語言回覆（螢幕字幕文字）",
  "extracted_entities": ["核心實體1"]
}}"""

    retrieval_ctx = {
        "original_query": user_prompt,
        "expanded_keywords": expand_res['expanded_keywords'],
        "inherited_tags": last_entities,
        "has_memory": bool(blocks),
        "block_count": len(blocks),
        "threshold": memory_threshold,
        "hard_base": f_base,
        "confidence": expand_res["entity_confidence"],
        "block_details": block_details,
        "core_debug_text": core_debug_text,
        "profile_debug_text": profile_debug_text,
        "dynamic_prompt": sys_prompt,
    }

    # ─── JSON Schema ───
    metrics_props = {m: {"type": "integer", "minimum": 0, "maximum": 100} for m in metrics}
    chat_schema = {
        "type": "object",
        "properties": {
            "internal_thought": {"type": "string"},
            "status_metrics": {
                "type": "object",
                "properties": metrics_props,
                "required": metrics
            },
            "tone": {"type": "string"},
            "speech": {"type": "string"},
            "reply": {"type": "string"},
            "extracted_entities": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["internal_thought", "status_metrics", "tone", "speech", "reply", "extracted_entities"],
    }

    # ─── 上下文組裝 ───
    with timer.step("上下文組裝 (Context Assembly)"):
        api_messages = [{"role": "system", "content": sys_prompt}]
        clean_history = [{"role": m["role"], "content": m["content"]} for m in session_messages[-4:]]
        api_messages.extend(clean_history)

    # ─── 判斷可用工具 ───
    tools_list = []
    try:
        from tools_tavily import TAVILY_SEARCH_SCHEMA
        if user_prefs.get("tavily_api_key"):
            tools_list.append(TAVILY_SEARCH_SCHEMA)
    except ImportError:
        pass
    try:
        from tools_weather import WEATHER_SCHEMA
        if user_prefs.get("openweather_api_key"):
            tools_list.append(WEATHER_SCHEMA)
    except ImportError:
        pass

    tool_context = None
    if tools_list:
        # ─── Module A: Router Agent（LLM 呼叫） ───
        char_hint = f"{char_name}（{speech_rules}）"
        with timer.step("意圖路由判斷 (Router Agent LLM)"):
            router_result = run_router_agent(
                user_prompt=user_prompt,
                char_hint=char_hint,
                tools_list=tools_list,
                router=rtr,
                temperature=temperature,
            )

        # ─── Module B: Middleware（僅在需要工具時執行） ───
        if router_result.needs_tools:
            # 過渡語音推播（幾乎不耗時，但記錄以利除錯）
            with timer.step("過渡語音推播 (Thinking Speech Dispatch)"):
                if router_result.thinking_speech and on_event:
                    on_event({"type": "thinking_speech", "content": router_result.thinking_speech})

            # 工具並行執行（主要耗時區段）
            with timer.step("工具並行執行 (Tool Execution)"):
                tool_context = run_middleware(
                    router_result=router_result,
                    on_thinking_speech=None,  # 已在上方推播
                    on_tool_status=on_event,
                )
                thinking_speech = tool_context.thinking_speech_sent

    # ─── Module C: Persona Synthesis Agent ───
    with timer.step("角色渲染生成 (Persona Agent LLM)"):
        raw_res, error_result = run_persona_agent(
            user_prompt=user_prompt,
            api_messages=api_messages,
            tool_context=tool_context,
            chat_schema=chat_schema,
            router=rtr,
            temperature=temperature,
        )

    if error_result is not None:
        persona_result = error_result
    else:
        with timer.step("回應解析 (Response Parsing)"):
            persona_result = _parse_persona_response(raw_res)

    reply_text = persona_result.reply_text
    new_entities = persona_result.new_entities
    inner_thought = persona_result.inner_thought
    status_metrics = persona_result.status_metrics
    tone = persona_result.tone
    speech = persona_result.speech

    # 將效能計時結果注入 retrieval_ctx
    retrieval_ctx["perf_timing"] = timer.summary()

    return (
        reply_text, new_entities, retrieval_ctx, topic_shifted, pipeline_data,
        inner_thought, status_metrics, tone, speech, thinking_speech,
    )
