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


def test_chat_system_suffix_response_process_rules_are_flat():
    from core.prompt_manager import get_prompt_manager

    template = get_prompt_manager().get_default("chat_system_suffix")

    assert "<response_process_rules>" in template
    assert "</response_process_rules>" in template
    assert "internal_thought:" in template
    assert "reply:" in template
    assert "{speech_instruction}" in template
    assert "<internal_thought_rule>" not in template
    assert "</internal_thought_rule>" not in template
