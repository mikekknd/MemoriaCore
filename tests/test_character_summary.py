"""角色簡介 character_summary 測試。"""

from pathlib import Path
import shutil
import uuid

import pytest
from fastapi import BackgroundTasks

import api.routers.character as character_router
import core.character_engine as character_engine
from core.character_engine import CharacterManager
from core.persona_evolution.snapshot_store import PersonaSnapshotStore
from core.storage_manager import StorageManager


class _PromptManager:
    def get(self, key: str) -> str:
        assert key == "character_generate"
        return "請生成角色：{description}"


class _Router:
    def __init__(self, payload):
        self.payload = payload
        self.schema = None
        self.call_count = 0
        self.task_keys: list[str] = []

    def generate_json(self, task_key, messages, schema=None, temperature=0.7):
        self.schema = schema
        self.call_count += 1
        self.task_keys.append(task_key)
        return dict(self.payload)


def _tmp_dir() -> Path:
    base = Path(".pyTestTemp") / "character_summary_tmp" / uuid.uuid4().hex
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


@pytest.mark.asyncio
async def test_upsert_new_character_seeds_initial_persona_snapshots(monkeypatch):
    """新角色 upsert 應透過背景任務補上 public/private 兩個 face 的初始 snapshot，
    且兩個 face 共用同一次 LLM 萃取（避免重複呼叫）。
    """
    base = _tmp_dir()
    try:
        mgr = CharacterManager(str(base / "characters.json"))
        storage = StorageManager(
            prefs_file=str(base / "prefs.json"),
            history_file=str(base / "history.json"),
            persona_snapshot_db_path=str(base / "persona.db"),
        )
        store = PersonaSnapshotStore(storage)
        router = _Router({
            "new_traits": [
                {"name": "關係錨定", "description": "我會優先維持與使用者的情感連續性。", "confidence": "high"},
                {"name": "回應克制", "description": "我會用穩定節奏回應，不把語氣推到過度戲劇化。", "confidence": "medium"},
                {"name": "設定自覺", "description": "我會遵守建立時賦予的身份與互動邊界。", "confidence": "medium"},
            ]
        })
        monkeypatch.setattr(character_router, "get_character_manager", lambda: mgr)
        monkeypatch.setattr(character_router, "get_persona_snapshot_store", lambda: store)
        monkeypatch.setattr(character_router, "get_router", lambda: router)

        background_tasks = BackgroundTasks()
        result = await character_router.upsert_character(
            character_router.CharacterProfileDTO(
                name="新角色",
                character_summary="初始角色簡介",
                system_prompt="初始完整人格設定",
                visual_prompt="外觀",
                reply_rules="繁體中文",
                tts_rules="",
                tts_language="",
            ),
            background_tasks,
        )

        char_id = result["character_id"]
        assert result["initial_snapshots_pending"] is True
        # response 立即返回時 snapshot 還沒寫入（背景任務還沒跑）
        assert store.get_latest_tree(char_id, persona_face="public") is None
        assert router.call_count == 0

        # 模擬 FastAPI 在 response 之後執行 BackgroundTasks
        await background_tasks()

        public_tree = store.get_latest_tree(char_id, persona_face="public")
        private_tree = store.get_latest_tree(char_id, persona_face="private")
        assert public_tree is not None and private_tree is not None
        assert len(public_tree["nodes"]) == 3
        assert {n["name"] for n in public_tree["nodes"]} == {"關係錨定", "回應克制", "設定自覺"}
        assert len(private_tree["nodes"]) == 3
        # 雙 face 共用同一次萃取：LLM 只能被呼叫一次
        assert router.call_count == 1
        assert router.task_keys == ["persona_seed"]
        assert router.schema["required"] == ["new_traits"]
    finally:
        shutil.rmtree(base, ignore_errors=True)


@pytest.mark.asyncio
async def test_upsert_existing_character_does_not_seed(monkeypatch):
    """更新既有角色不應觸發初始 seeding。"""
    base = _tmp_dir()
    try:
        mgr = CharacterManager(str(base / "characters.json"))
        storage = StorageManager(
            prefs_file=str(base / "prefs.json"),
            history_file=str(base / "history.json"),
            persona_snapshot_db_path=str(base / "persona.db"),
        )
        store = PersonaSnapshotStore(storage)
        router = _Router({"new_traits": []})
        monkeypatch.setattr(character_router, "get_character_manager", lambda: mgr)
        monkeypatch.setattr(character_router, "get_persona_snapshot_store", lambda: store)
        monkeypatch.setattr(character_router, "get_router", lambda: router)

        existing_id = mgr.upsert_character({
            "character_id": "char-existing",
            "name": "既有角色",
            "system_prompt": "原本人格",
        })

        bt = BackgroundTasks()
        result = await character_router.upsert_character(
            character_router.CharacterProfileDTO(
                character_id=existing_id,
                name="既有角色",
                system_prompt="更新後人格",
            ),
            bt,
        )
        assert result["initial_snapshots_pending"] is False
        await bt()
        assert router.call_count == 0
        assert store.get_latest_tree(existing_id, persona_face="public") is None
    finally:
        shutil.rmtree(base, ignore_errors=True)
