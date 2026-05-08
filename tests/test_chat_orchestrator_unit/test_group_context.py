"""群組 prompt/log context 測試。"""

import core.chat_orchestrator.group_context as group_context


class _PromptManager:
    def get(self, key: str) -> str:
        assert key == "group_participants_block"
        return "{group_context_line}\n目前扮演={current_character_name}{self_address_clause}; id={current_character_id}; raw_group={group_name}\n{participants_text}"


class _CharacterManager:
    def __init__(self):
        self.characters = {
            "char-a": {
                "character_id": "char-a",
                "name": "角色A",
                "character_summary": "角色 A 簡介",
                "system_prompt": "角色 A 完整人格",
            },
            "char-b": {
                "character_id": "char-b",
                "name": "角色B",
                "character_summary": "",
                "system_prompt": "角色 B fallback 人格",
            },
        }

    def get_character(self, character_id):
        return self.characters.get(character_id)


def test_group_participants_block_uses_summary_and_omits_current(monkeypatch):
    monkeypatch.setattr(group_context, "get_prompt_manager", lambda: _PromptManager())
    ctx = {
        "session_mode": "group",
        "group_name": "測試群組",
        "active_character_ids": ["char-a", "char-b"],
    }

    block = group_context.build_group_participants_block(ctx, _CharacterManager(), "char-a")

    assert "你正在多 AI 群組「測試群組」中對話。" in block
    assert "目前扮演=角色A; id=char-a; raw_group=測試群組" in block
    assert "角色A (char-a)" not in block
    assert "角色 A 簡介" not in block
    assert "- name: 角色B" in block
    assert "  character_id: char-b" in block
    assert "  role: 角色 B fallback 人格" in block
    assert "<participant" not in block
    assert "<summary>" not in block
    assert "角色 A 完整人格" not in block


def test_group_participants_block_omits_current_when_it_is_only_participant(monkeypatch):
    monkeypatch.setattr(group_context, "get_prompt_manager", lambda: _PromptManager())
    ctx = {
        "session_mode": "group",
        "group_name": "測試群組",
        "active_character_ids": ["char-a"],
    }

    block = group_context.build_group_participants_block(ctx, _CharacterManager(), "char-a")

    assert "你正在多 AI 群組「測試群組」中對話。" in block
    assert "目前扮演=角色A; id=char-a; raw_group=測試群組" in block
    assert "角色A (char-a)" not in block
    assert "- none" in block
    assert "<no_other_participants" not in block


def test_group_participants_block_omits_blank_group_name_from_prompt(monkeypatch):
    monkeypatch.setattr(group_context, "get_prompt_manager", lambda: _PromptManager())
    ctx = {
        "session_mode": "group",
        "group_name": "",
        "active_character_ids": ["char-a", "char-b"],
    }

    block = group_context.build_group_participants_block(ctx, _CharacterManager(), "char-a")

    assert "你正在多 AI 群組對話中。" in block
    assert "未命名群組" not in block
    assert "raw_group=\n" in block


def test_default_group_participants_template_keeps_only_outer_xml():
    template = group_context.get_prompt_manager().get_default("group_participants_block")

    assert template.lstrip().startswith("<group_context>")
    assert template.rstrip().endswith("</group_context>")
    assert "current_character:" in template
    assert "rules:" in template
    assert "participants:" in template
    assert "{self_address_clause}" in template
    assert "group_name:" not in template
    assert "<context>" not in template
    assert "<current_character>" not in template
    assert "<group_output_rules>" not in template
    assert "<other_ai_participants>" not in template


def test_llm_log_context_lists_user_current_ai_and_participants():
    ctx = {
        "session_id": "sid-a",
        "session_mode": "group",
        "group_name": "測試群組",
        "user_id": "user-a",
        "user_name": "使用者A",
        "active_character_ids": ["char-a", "char-b"],
    }

    log_context = group_context.build_llm_log_context(ctx, _CharacterManager(), "char-a")

    assert log_context["session_id"] == "sid-a"
    assert log_context["session_mode"] == "group"
    assert log_context["user_name"] == "使用者A"
    assert log_context["current_character_name"] == "角色A"
    assert log_context["participants"][0] == {"character_id": "char-a", "name": "角色A"}
    assert "character_summary" not in log_context["participants"][0]
