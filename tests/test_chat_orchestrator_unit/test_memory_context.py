"""最終 chat prompt 的記憶上下文格式測試。"""

from core.chat_orchestrator.memory_context import (
    build_retrieved_memory_context,
    format_proactive_topics_prompt,
    format_static_profile_prompt,
)


def test_retrieved_memory_context_uses_flat_sections_without_nested_xml():
    result = build_retrieved_memory_context(
        core_insights=[{"insight": "使用者偏好先驗證再下結論。", "score": 0.91}],
        profile_matches=[
            {"fact_key": "favorite_food", "fact_value": "毛豆", "score": 0.88},
        ],
        blocks=[
            {
                "block_id": "block-a",
                "timestamp": "2026-05-07T08:00:00",
                "overview": "使用者討論 prompt token 壓縮。",
                "raw_dialogues": [
                    {"role": "user", "content": "這段 XML 太深了"},
                    {
                        "role": "assistant",
                        "content": "可以改成外層 XML、內層 Markdown。",
                        "character_name": "可可",
                        "character_id": "char-coco",
                    },
                ],
                "_debug_score": 0.9,
                "_debug_raw_sim": 0.8,
                "_debug_sparse_raw": 0.7,
                "_debug_recency": 0.6,
                "_debug_importance": 0.5,
            }
        ],
        force_group=True,
    )

    prompt = result.prompt
    assert "core_memory:" in prompt
    assert "insight: |" in prompt
    assert "relevant_preferences:" in prompt
    assert "- key: favorite_food" in prompt
    assert "  value: 毛豆" in prompt
    assert "episodic_memories:" in prompt
    assert "- index: 1" in prompt
    assert "  uid: block-a" in prompt
    assert "  overview: |" in prompt
    assert "  dialogue: |" in prompt
    assert "[可可|char-coco]: 可以改成外層 XML、內層 Markdown。" in prompt
    assert "<user_core_memory>" not in prompt
    assert "<user_relevant_preferences>" not in prompt
    assert "<preference" not in prompt
    assert "<episodic_memory" not in prompt
    assert "<timestamp>" not in prompt
    assert "<overview>" not in prompt
    assert "<dialogue>" not in prompt
    assert result.block_details[0]["overview"] == "使用者討論 prompt token 壓縮。"
    assert "使用者偏好先驗證再下結論。" in result.core_debug_text
    assert "favorite_food=毛豆" in result.profile_debug_text


def test_retrieved_memory_context_is_empty_without_retrieved_sections():
    result = build_retrieved_memory_context(
        core_insights=[],
        profile_matches=[],
        blocks=[],
        static_profile="",
        proactive_topics="",
    )

    assert result.prompt == ""
    assert result.block_details == []


def test_static_profile_prompt_uses_flat_sections():
    prompt = format_static_profile_prompt(
        basic_facts=[{"fact_key": "name", "fact_value": "夏雪"}],
        critical_facts=[{"fact_key": "allergy", "fact_value": "花生"}],
    )

    assert "static_user_profile:" in prompt
    assert "  basic_info:" in prompt
    assert "  critical_rules:" in prompt
    assert "- key: name" in prompt
    assert "  value: 夏雪" in prompt
    assert "- key: allergy" in prompt
    assert "  value: 花生" in prompt
    assert "<user_static_profile>" not in prompt
    assert "<basic_info>" not in prompt
    assert "<critical_rules>" not in prompt
    assert "<fact" not in prompt
    assert "<rule" not in prompt


def test_proactive_topics_prompt_uses_flat_sections():
    prompt = format_proactive_topics_prompt([
        {"interest_keyword": "茶", "summary_content": "使用者最近關注冷泡茶。"},
    ])

    assert "proactive_topics:" in prompt
    assert "instruction: |" in prompt
    assert "topics:" in prompt
    assert "- keyword: 茶" in prompt
    assert "  summary: 使用者最近關注冷泡茶。" in prompt
    assert "<proactive_topics>" not in prompt
    assert "<instruction>" not in prompt
    assert "<topic" not in prompt


def test_chat_system_suffix_merges_response_rules_into_required_output_format():
    from core.prompt_manager import get_prompt_manager

    template = get_prompt_manager().get_default("chat_system_suffix")

    assert "<retrieved_memory_context>" not in template
    assert "<response_process_rules>" not in template
    assert "</response_process_rules>" not in template
    assert "<required_output_format>" in template
    assert "<reply_content_rules>" in template
    assert '"internal_thought":' in template
    assert '"reply":' in template
    assert "{speech_instruction}" in template
    assert '"internal_thought": "分析使用者的潛在意圖' in template
    assert '"reply": "顯示給使用者看的自然語言回覆（螢幕字幕文字）"' in template
    required_output = template.split("<reply_content_rules>", 1)[0]
    assert '文字與語氣規則：{speech_instruction}' not in required_output
    assert "<internal_thought_rule>" not in template
    assert "</internal_thought_rule>" not in template


def test_build_final_chat_context_moves_retrieved_memory_to_latest_user_message():
    from core.chat_orchestrator.generation_context import build_final_chat_context

    api_messages, _clean_history, sys_prompt = build_final_chat_context(
        char_sys_prompt="角色 prompt",
        group_participants_block="",
        mem_ctx="core_memory:\n- insight: 使用者偏好先驗證。",
        reply_rules="用繁體中文回應。",
        session_messages=[{"role": "user", "content": "請整理重點。"}],
        context_window=5,
        user_prefs={},
        session_ctx={},
        force_group=False,
    )

    latest_user = api_messages[-1]["content"]
    assert "<retrieved_memory_context>" not in sys_prompt
    assert "使用者偏好先驗證" not in sys_prompt
    assert latest_user.count("<retrieved_memory_context>") == 1
    assert "core_memory:" in latest_user
    assert "使用者偏好先驗證" in latest_user
    assert latest_user.index("<retrieved_memory_context>") < latest_user.index("請整理重點。")


def test_build_final_chat_context_omits_empty_retrieved_memory_block():
    from core.chat_orchestrator.generation_context import build_final_chat_context

    api_messages, _clean_history, sys_prompt = build_final_chat_context(
        char_sys_prompt="角色 prompt",
        group_participants_block="",
        mem_ctx="",
        reply_rules="用繁體中文回應。",
        session_messages=[{"role": "user", "content": "請開場。"}],
        context_window=5,
        user_prefs={},
        session_ctx={},
        force_group=False,
    )

    latest_user = api_messages[-1]["content"]
    assert "<retrieved_memory_context>" not in sys_prompt
    assert "<retrieved_memory_context>" not in latest_user
    assert "無相關記憶" not in latest_user


def test_youtube_live_chat_system_suffix_omits_dynamic_rules_and_memory_block():
    from core.chat_orchestrator.generation_context import build_final_chat_context

    api_messages, _clean_history, sys_prompt = build_final_chat_context(
        char_sys_prompt="直播角色 prompt",
        group_participants_block="",
        mem_ctx="core_memory:\n- insight: 直播記憶。",
        reply_rules="用繁體中文回應。",
        session_messages=[{"role": "user", "content": "請開場。"}],
        context_window=5,
        user_prefs={},
        session_ctx={
            "channel": "youtube_live",
            "external_chat_context": {"source": "youtube_live_director"},
        },
        force_group=False,
    )

    assert "<system_dynamic_rules>" not in sys_prompt
    assert "<retrieved_memory_context>" not in sys_prompt
    assert "<response_process_rules>" not in sys_prompt
    assert "<required_output_format>" in sys_prompt
    assert "<reply_content_rules>" in sys_prompt
    assert "文字與語氣規則：2. `reply`" not in sys_prompt
    assert "<retrieved_memory_context>" in api_messages[-1]["content"]


def test_chat_response_schema_limits_internal_thought_length():
    from core.chat_orchestrator.generation_context import build_chat_response_schema

    schema = build_chat_response_schema()

    assert schema["properties"]["internal_thought"]["type"] == "string"
    assert schema["properties"]["internal_thought"]["maxLength"] == 40


def test_normalize_internal_thought_trims_to_40_characters():
    from core.chat_orchestrator.generation_context import normalize_internal_thought

    text = "這是一段超過四十個字的內心獨白，用來確認解析層會穩定截斷多餘內容並忽略模型多寫的部分"

    assert normalize_internal_thought(text) == text[:40]
    assert len(normalize_internal_thought(text)) == 40
    assert normalize_internal_thought(None) is None
