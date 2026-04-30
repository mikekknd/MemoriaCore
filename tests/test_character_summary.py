"""角色簡介 character_summary 測試。"""

from pathlib import Path
import shutil
import uuid

import pytest

import api.routers.character as character_router
import core.character_engine as character_engine
from core.character_engine import CharacterManager


class _PromptManager:
    def get(self, key: str) -> str:
        assert key == "character_generate"
        return "請生成角色：{description}"


class _Router:
    def __init__(self, payload):
        self.payload = payload
        self.schema = None

    def generate_json(self, task_key, messages, schema=None, temperature=0.7):
        self.schema = schema
        return dict(self.payload)


def _tmp_dir() -> Path:
    base = Path("tests") / ".character_summary_tmp" / uuid.uuid4().hex
    base.mkdir(parents=True, exist_ok=False)
    return base


def _profile(summary: str) -> dict:
    return {
        "name": "測試角色",
        "character_summary": summary,
        "system_prompt": "完整人格",
        "visual_prompt": "外觀",
        "reply_rules": "繁體中文",
        "tts_rules": "",
        "tts_language": "",
    }


def test_character_manager_saves_summary_and_backfills_missing():
    base = _tmp_dir()
    try:
        mgr = CharacterManager(str(base / "characters.json"))
        long_summary = "角色簡介" * 80
        mgr.upsert_character({
            "character_id": "char-a",
            "name": "角色 A",
            "character_summary": long_summary,
            "system_prompt": "完整人格 A",
        })
        mgr.upsert_character({
            "character_id": "char-b",
            "name": "角色 B",
            "system_prompt": "完整人格 B",
        })

        char_a = mgr.get_character("char-a")
        char_b = mgr.get_character("char-b")
        assert len(char_a["character_summary"]) <= 240
        assert char_b["character_summary"] == ""
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_generate_character_profile_requires_and_returns_summary(monkeypatch):
    monkeypatch.setattr(character_engine, "get_prompt_manager", lambda: _PromptManager())
    router = _Router(_profile("短版角色簡介"))
    base = _tmp_dir()
    try:
        mgr = CharacterManager(str(base / "characters.json"))

        result = mgr.generate_character_profile("建立一個角色", router)

        assert "character_summary" in router.schema["required"]
        assert result["character_summary"] == "短版角色簡介"
    finally:
        shutil.rmtree(base, ignore_errors=True)


@pytest.mark.asyncio
async def test_generate_from_seed_requires_and_returns_summary(monkeypatch):
    router = _Router(_profile("種子生成角色簡介"))
    monkeypatch.setattr(character_router, "get_router", lambda: router)
    base = _tmp_dir()
    mgr = CharacterManager(str(base / "seed_characters.json"))

    class _CharacterManager:
        def _normalize_character(self, payload):
            return mgr._normalize_character(payload)

    monkeypatch.setattr(character_router, "get_character_manager", lambda: _CharacterManager())

    try:
        result = await character_router.generate_from_existing_persona(
            character_router.GenerateFromSeedRequest(
                description="角色描述",
                existing_persona="完整人格種子",
            )
        )

        assert "character_summary" in router.schema["required"]
        assert result["character_summary"] == "種子生成角色簡介"
    finally:
        shutil.rmtree(base, ignore_errors=True)
