import os
import shutil
import uuid
from pathlib import Path

import pytest

from core.bot_registry import BotRegistry, BotRegistryError
from core.persona_sync import PersonaSyncManager
from core.storage_manager import StorageManager


def _tmp_dir() -> Path:
    path = Path(".pyTestTemp") / "multibot" / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=False)
    return path


def test_bot_registry_migrates_legacy_telegram_token():
    tmp_dir = _tmp_dir()
    try:
        registry = BotRegistry(str(tmp_dir / "bot_configs.json"))

        configs = registry.load_configs({
            "telegram_bot_token": "123:abc",
            "active_character_id": "char-a",
        })

        assert configs == [{
            "bot_id": "legacy-telegram",
            "platform": "telegram",
            "display_name": "Legacy Telegram",
            "character_id": "char-a",
            "token": "123:abc",
            "enabled": True,
        }]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_bot_registry_rejects_duplicate_enabled_telegram_tokens():
    tmp_dir = _tmp_dir()
    try:
        registry = BotRegistry(str(tmp_dir / "bot_configs.json"))
        configs = [
            {
                "bot_id": "bot-a",
                "platform": "telegram",
                "display_name": "A",
                "character_id": "char-a",
                "token": "same-token",
                "enabled": True,
            },
            {
                "bot_id": "bot-b",
                "platform": "telegram",
                "display_name": "B",
                "character_id": "char-b",
                "token": "same-token",
                "enabled": True,
            },
        ]

        with pytest.raises(BotRegistryError, match="token 不可重複"):
            registry.validate_configs(configs, character_ids={"char-a", "char-b"})
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_conversation_session_persists_bot_and_character():
    tmp_dir = _tmp_dir()
    storage = StorageManager(
        prefs_file=str(tmp_dir / "prefs.json"),
        history_file=str(tmp_dir / "history.json"),
    )
    storage._CONV_DB = str(tmp_dir / "conversation.db")

    try:
        storage.create_conversation_session(
            "sid-a",
            channel="telegram",
            channel_uid="tg-user",
            bot_id="bot-a",
            user_id="42",
            character_id="char-a",
            channel_class="public",
            persona_face="public",
        )
        storage.save_conversation_message("sid-a", "user", "hello")

        info = storage.get_session_info("sid-a")
        assert info["bot_id"] == "bot-a"
        assert info["character_id"] == "char-a"
        assert info["message_count"] == 1
        assert storage.load_conversation_messages("sid-a")[0]["content"] == "hello"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_persona_sync_state_is_per_character():
    tmp_dir = _tmp_dir()
    original_cwd = os.getcwd()
    try:
        os.chdir(tmp_dir)
        manager = PersonaSyncManager()
        prefs = {"active_character_id": "char-a"}

        manager._save_face_state(
            "char-a",
            "public",
            {"last_reflection_at": "2026-01-01T00:00:00", "today_date": "2026-01-01", "today_run_count": 1},
            prefs,
        )

        a_state = manager._load_face_state("char-a", "public", prefs)
        b_state = manager._load_face_state("char-b", "public", prefs)

        assert a_state["today_run_count"] == 1
        assert b_state["today_run_count"] == 0
    finally:
        os.chdir(original_cwd)
        shutil.rmtree(tmp_dir, ignore_errors=True)
