import json

from core.chat_orchestrator.group_context import build_group_participants_block
from core.chat_orchestrator.live_persona import (
    apply_live_persona_to_participants,
    resolve_live_persona_prompt,
)


class _PromptManager:
    def get(self, key: str) -> str:
        templates = {
            "youtube_live_persona_override_block": (
                "<live_character_prompt>\n{system_prompt}\n</live_character_prompt>\n"
                "\n"
                "直播約束：\n- 請和其他角色互相接話、補充、反駁或提出下一個切入點。"
            ),
            "group_participants_block": (
                "group={group_name}; current={current_character_name}\n"
                "rules: 你目前扮演的是「{current_character_name}」{self_address_clause}；自己的角色設定以上方角色 prompt 為準。\n"
                "{participants_text}"
            ),
        }
        return templates[key]


class _CharacterManager:
    def __init__(self, characters):
        self.characters = characters

    def get_character(self, character_id):
        return self.characters.get(character_id)


def _trusted_ctx():
    return {
        "user_id": "__youtube_live__",
        "channel": "youtube_live",
        "persona_face": "public",
        "external_chat_context": {
            "source": "youtube_live_director",
            "character_prompt_overrides": {
                "coco": {
                    "enabled": True,
                    "mode": "replace",
                    "system_prompt": "直播可可專用 prompt。原始人設不得混入。",
                    "self_address": "本小姐",
                    "opening_intro": "本小姐是今天的直播主持可可。",
                    "reply_rules": "每次接話都要自然接住前一位角色。",
                    "addressing": {"bailian": "白蓮大人"},
                }
            },
        },
    }


def test_trusted_youtube_live_persona_replaces_base_prompt(monkeypatch):
    monkeypatch.setattr("core.chat_orchestrator.live_persona.get_prompt_manager", lambda: _PromptManager())

    prompt, reply_rules = resolve_live_persona_prompt(
        character_id="coco",
        base_prompt="原始可可 prompt 不應保留。",
        base_reply_rules="原始 reply rules。",
        session_ctx=_trusted_ctx(),
    )

    assert "直播可可專用 prompt" in prompt
    assert "原始可可 prompt" not in prompt
    assert "固定自稱：本小姐" not in prompt
    assert "白蓮大人" not in prompt
    assert "<live_speech_identity>" not in prompt
    assert "對其他角色的固定稱呼" not in prompt
    assert "今天的直播主持可可" not in prompt
    assert "<live_reply_rules>" not in prompt
    assert "每次接話都要自然接住前一位角色。" not in prompt
    assert "自然接住前一位角色" in reply_rules


def test_youtube_live_persona_template_does_not_embed_reply_rules_block():
    prompts = json.loads(open("prompts_default.json", encoding="utf-8").read())
    block = prompts["youtube_live_persona_override_block"]

    assert "{reply_rules_block}" not in block["template"]
    assert "{reply_rules_block}" not in block["placeholders"]
    assert "<live_reply_rules>" not in block["template"]
    assert "不要把問題丟回觀眾" not in block["template"]
    assert "請和其他角色互相接話、補充、反駁或提出下一個切入點" in block["template"]


def test_live_persona_prompt_omits_runtime_scope_and_opening_execution_rules():
    prompt, _reply_rules = resolve_live_persona_prompt(
        character_id="coco",
        base_prompt="原始可可 prompt。",
        base_reply_rules="原始 reply rules。",
        session_ctx=_trusted_ctx(),
    )

    assert "不代表一般聊天人格永久變更" not in prompt
    assert "開場時請自然完成固定開場白" not in prompt
    assert "<youtube_live_persona_override>" not in prompt
    assert "mode: replace" not in prompt
    assert "開場自我介紹" not in prompt
    assert "固定自稱" not in prompt
    assert "<live_speech_identity>" not in prompt
    assert "今天的直播主持可可" not in prompt


def test_live_persona_prompt_omits_empty_reply_rules_block(monkeypatch):
    monkeypatch.setattr("core.chat_orchestrator.live_persona.get_prompt_manager", lambda: _PromptManager())
    ctx = _trusted_ctx()
    ctx["external_chat_context"]["character_prompt_overrides"]["coco"]["reply_rules"] = ""

    prompt, reply_rules = resolve_live_persona_prompt(
        character_id="coco",
        base_prompt="原始可可 prompt。",
        base_reply_rules="原始 reply rules。",
        session_ctx=ctx,
    )

    assert "<live_reply_rules>" not in prompt
    assert "（沿用一般回覆規則）" not in prompt
    assert reply_rules == "原始 reply rules。"


def test_untrusted_context_ignores_live_persona_override(monkeypatch):
    monkeypatch.setattr("core.chat_orchestrator.live_persona.get_prompt_manager", lambda: _PromptManager())
    ctx = _trusted_ctx()
    ctx["channel"] = "dashboard"
    ctx["user_id"] = "normal-user"

    prompt, reply_rules = resolve_live_persona_prompt(
        character_id="coco",
        base_prompt="原始可可 prompt。",
        base_reply_rules="原始 reply rules。",
        session_ctx=ctx,
    )

    assert prompt == "原始可可 prompt。"
    assert reply_rules == "原始 reply rules。"


def test_group_participant_block_uses_live_addressing_rules(monkeypatch):
    monkeypatch.setattr("core.chat_orchestrator.live_persona.get_prompt_manager", lambda: _PromptManager())
    monkeypatch.setattr("core.chat_orchestrator.group_context.get_prompt_manager", lambda: _PromptManager())
    ctx = _trusted_ctx() | {
        "session_mode": "group",
        "active_character_ids": ["coco", "bailian"],
        "group_name": "YouTube Live",
    }
    chars = {
        "coco": {"character_id": "coco", "name": "可可", "character_summary": "原本可可摘要"},
        "bailian": {"character_id": "bailian", "name": "白蓮", "character_summary": "原本白蓮摘要"},
    }

    participants = apply_live_persona_to_participants(list(chars.values()), ctx)
    block = build_group_participants_block(ctx, _CharacterManager({c["character_id"]: c for c in participants}), "coco")

    coco = next(c for c in participants if c["character_id"] == "coco")
    assert coco["character_summary"] == "原本可可摘要"
    assert "routing_profile" not in coco
    assert "今天的直播主持可可" not in json.dumps(participants, ensure_ascii=False)
    assert "每次接話都要自然接住前一位角色" not in json.dumps(participants, ensure_ascii=False)
    assert "白蓮大人" in block
    assert "直播稱呼" in block
    assert "固定自稱：本小姐" in block
    assert "bailian" in block
