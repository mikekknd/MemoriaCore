"""dialogue_format 測試：訊息清洗、群組標籤、citation metadata 讀取。"""
from core.chat_orchestrator.dialogue_format import (
    sanitize_message_for_llm,
    collect_cited_uids,
    speaker_label,
    format_history_for_llm,
    format_dialogue_for_analysis,
)


class TestSanitizeMessageForLLM:
    def test_strips_ref_tag(self):
        assert sanitize_message_for_llm("你好嗎 [Ref: abc123]") == "你好嗎"

    def test_strips_multiple_ref_tags(self):
        cleaned = sanitize_message_for_llm("[Ref: a1] 早安 [Ref: b2, c3]")
        assert "[Ref:" not in cleaned
        assert "早安" in cleaned

    def test_strips_environment_block(self):
        text = "<environment_context>Current Time: 2026-04-30</environment_context>使用者好"
        assert sanitize_message_for_llm(text) == "使用者好"

    def test_strips_emotional_block(self):
        text = "<emotional_trajectory>[上一輪]「猶豫」</emotional_trajectory>嗨"
        assert sanitize_message_for_llm(text) == "嗨"

    def test_followup_message_returns_empty(self):
        text = "【群組接力指令】\n使用者原始話題：xx\n上一位..."
        assert sanitize_message_for_llm(text) == ""

    def test_empty_input(self):
        assert sanitize_message_for_llm("") == ""
        assert sanitize_message_for_llm(None) == ""

    def test_plain_content_unchanged(self):
        assert sanitize_message_for_llm("普通對話") == "普通對話"


class TestCollectCitedUids:
    def test_reads_from_debug_info(self):
        msg = {
            "role": "assistant",
            "content": "回覆內容",
            "debug_info": {"cited_uids": ["uid-a", "uid-b"]},
        }
        assert collect_cited_uids(msg) == ["uid-a", "uid-b"]

    def test_fallback_regex_when_no_metadata(self):
        msg = {"role": "assistant", "content": "舊資料 [Ref: legacy-uid]"}
        assert collect_cited_uids(msg) == ["legacy-uid"]

    def test_fallback_handles_multi_uid(self):
        msg = {"role": "assistant", "content": "x [Ref: a1, b2,c3]"}
        assert collect_cited_uids(msg) == ["a1", "b2", "c3"]

    def test_metadata_takes_precedence_over_regex(self):
        msg = {
            "role": "assistant",
            "content": "x [Ref: legacy]",
            "debug_info": {"cited_uids": ["new-uid"]},
        }
        # debug_info 為主，regex 不應被觸發
        assert collect_cited_uids(msg) == ["new-uid"]

    def test_empty_uids_returns_empty(self):
        assert collect_cited_uids({"role": "assistant", "content": ""}) == []

    def test_debug_info_empty_list_falls_back_to_regex(self):
        msg = {
            "role": "assistant",
            "content": "舊資料 [Ref: legacy]",
            "debug_info": {"cited_uids": []},
        }
        # 空 list 視為缺值，仍走 regex fallback
        assert collect_cited_uids(msg) == ["legacy"]


class TestSpeakerLabel:
    def test_full_label(self):
        msg = {"character_name": "白蓮", "character_id": "char_lotus"}
        assert speaker_label(msg) == "白蓮|char_lotus"

    def test_only_name(self):
        assert speaker_label({"character_name": "白蓮"}) == "白蓮"

    def test_only_id(self):
        assert speaker_label({"character_id": "char_lotus"}) == "char_lotus"

    def test_empty(self):
        assert speaker_label({}) == ""


class TestFormatHistoryForLLM:
    def test_single_character_no_label(self):
        msgs = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好嗎",
             "character_name": "助理", "character_id": "char_default"},
        ]
        out = format_history_for_llm(msgs)
        assert out == [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好嗎"},
        ]

    def test_group_session_adds_label(self):
        msgs = [
            {"role": "user", "content": "嗨"},
            {"role": "assistant", "content": "你好",
             "character_name": "白蓮", "character_id": "char_lotus"},
            {"role": "assistant", "content": "嗨嗨",
             "character_name": "可可", "character_id": "char_coco"},
        ]
        out = format_history_for_llm(msgs)
        assert out[1]["content"] == "[白蓮|char_lotus]: 你好"
        assert out[2]["content"] == "[可可|char_coco]: 嗨嗨"

    def test_force_group_labels_single_assistant(self):
        msgs = [
            {"role": "user", "content": "嗨"},
            {"role": "assistant", "content": "你好",
             "character_name": "白蓮", "character_id": "char_lotus"},
        ]
        out = format_history_for_llm(msgs, force_group=True)
        assert out[1]["content"] == "[白蓮|char_lotus]: 你好"

    def test_strips_ref_in_assistant_content(self):
        msgs = [
            {"role": "assistant", "content": "答覆 [Ref: uid1]",
             "character_name": "白蓮", "character_id": "char_lotus"},
            {"role": "assistant", "content": "另一答 [Ref: uid2]",
             "character_name": "可可", "character_id": "char_coco"},
        ]
        out = format_history_for_llm(msgs)
        assert "[Ref:" not in out[0]["content"]
        assert "[Ref:" not in out[1]["content"]
        assert "[白蓮|char_lotus]: 答覆" == out[0]["content"]

    def test_skips_followup_instruction_message(self):
        msgs = [
            {"role": "user", "content": "正常 user 訊息"},
            {"role": "user", "content": "【群組接力指令】\n使用者原始話題..."},
            {"role": "assistant", "content": "回覆"},
        ]
        out = format_history_for_llm(msgs)
        # 接力訊息整則被跳過
        assert len(out) == 2
        assert out[0]["content"] == "正常 user 訊息"


class TestFormatDialogueForAnalysis:
    def test_single_character_uses_role_prefix(self):
        msgs = [
            {"role": "user", "content": "嗨"},
            {"role": "assistant", "content": "你好",
             "character_name": "助理", "character_id": "char_default"},
        ]
        out = format_dialogue_for_analysis(msgs)
        assert "user: 嗨" in out
        assert "assistant: 你好" in out
        assert "[" not in out  # 無群組標籤

    def test_group_uses_speaker_label(self):
        msgs = [
            {"role": "user", "content": "嗨"},
            {"role": "assistant", "content": "你好",
             "character_name": "白蓮", "character_id": "char_lotus"},
            {"role": "assistant", "content": "嗨嗨",
             "character_name": "可可", "character_id": "char_coco"},
        ]
        out = format_dialogue_for_analysis(msgs)
        assert "[白蓮|char_lotus]: 你好" in out
        assert "[可可|char_coco]: 嗨嗨" in out

    def test_force_group_uses_speaker_label_for_single_assistant(self):
        msgs = [
            {"role": "user", "content": "嗨"},
            {"role": "assistant", "content": "你好",
             "character_name": "白蓮", "character_id": "char_lotus"},
        ]
        out = format_dialogue_for_analysis(msgs, force_group=True)
        assert "[白蓮|char_lotus]: 你好" in out

    def test_sanitizes_internal_tags(self):
        msgs = [
            {"role": "user", "content": "<environment_context>x</environment_context>嗨"},
            {"role": "assistant", "content": "回覆 [Ref: u1]",
             "character_name": "白蓮", "character_id": "char_lotus"},
            {"role": "assistant", "content": "回覆2 [Ref: u2]",
             "character_name": "可可", "character_id": "char_coco"},
        ]
        out = format_dialogue_for_analysis(msgs)
        assert "<environment_context>" not in out
        assert "[Ref:" not in out

    def test_skips_followup_message(self):
        msgs = [
            {"role": "user", "content": "原始問題"},
            {"role": "user", "content": "【群組接力指令】內容"},
        ]
        out = format_dialogue_for_analysis(msgs)
        assert "群組接力指令" not in out
        assert "原始問題" in out
