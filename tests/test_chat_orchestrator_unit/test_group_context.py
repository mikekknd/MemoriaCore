"""群組 prompt/log context 測試。"""

import core.chat_orchestrator.group_context as group_context


class _PromptManager:
    def get(self, key: str) -> str:
        assert key == "group_participants_block"
        return "群組={group_name}; current={current_character_id}\n{participants_text}"


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

    assert "群組=測試群組; current=char-a" in block
    assert "角色A (char-a)" not in block
    assert "角色 A 簡介" not in block
    assert "角色B (char-b): 角色 B fallback 人格" in block
    assert "角色 A 完整人格" not in block


def test_group_participants_block_omits_current_when_it_is_only_participant(monkeypatch):
    monkeypatch.setattr(group_context, "get_prompt_manager", lambda: _PromptManager())
    ctx = {
        "session_mode": "group",
        "group_name": "測試群組",
        "active_character_ids": ["char-a"],
    }

    block = group_context.build_group_participants_block(ctx, _CharacterManager(), "char-a")

    assert "群組=測試群組; current=char-a" in block
    assert "角色A (char-a)" not in block
    assert "目前沒有其他 AI 成員資料" in block


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
    assert log_context["participants"][0]["character_summary"] == "角色 A 簡介"
