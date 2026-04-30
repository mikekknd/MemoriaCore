import os
import shutil
import uuid
from pathlib import Path

import pytest

from core.bot_registry import BotRegistry, BotRegistryError
from core.persona_sync import PersonaSyncManager
from core.storage_manager import StorageManager
from api.telegram_bot import _account_display_name, _is_telegram_id_query


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


def test_bot_registry_rejects_enabled_discord_without_token():
    tmp_dir = _tmp_dir()
    try:
        registry = BotRegistry(str(tmp_dir / "bot_configs.json"))
        config = {
            "bot_id": "discord-a",
            "platform": "discord",
            "display_name": "Discord A",
            "character_id": "char-a",
            "token": "",
            "enabled": True,
        }

        with pytest.raises(BotRegistryError, match="Discord bot 必須提供 token"):
            registry.validate_config(config, character_ids={"char-a"})
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_bot_registry_rejects_duplicate_enabled_discord_tokens():
    tmp_dir = _tmp_dir()
    try:
        registry = BotRegistry(str(tmp_dir / "bot_configs.json"))
        configs = [
            {
                "bot_id": "discord-a",
                "platform": "discord",
                "display_name": "A",
                "character_id": "char-a",
                "token": "same-discord-token",
                "enabled": True,
            },
            {
                "bot_id": "discord-b",
                "platform": "discord",
                "display_name": "B",
                "character_id": "char-b",
                "token": "same-discord-token",
                "enabled": True,
            },
        ]

        with pytest.raises(BotRegistryError, match="Discord token 不可重複"):
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


def test_storage_finds_user_by_telegram_uid_and_prefers_local_nickname():
    tmp_dir = _tmp_dir()
    storage = StorageManager(
        prefs_file=str(tmp_dir / "prefs.json"),
        history_file=str(tmp_dir / "history.json"),
    )
    storage._USERS_DB = str(tmp_dir / "users.db")

    class TelegramUser:
        id = 123456
        full_name = "Telegram Name"
        username = "telegram_user"

    class Message:
        from_user = TelegramUser()

    try:
        user = storage.create_user(
            "local-user",
            "hash",
            nickname="本機暱稱",
            telegram_uid="123456",
        )

        matched = storage.get_user_by_telegram_uid(123456)

        assert matched["id"] == user["id"]
        assert _account_display_name(matched, Message()) == "本機暱稱"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_storage_finds_user_by_discord_uid():
    tmp_dir = _tmp_dir()
    storage = StorageManager(
        prefs_file=str(tmp_dir / "prefs.json"),
        history_file=str(tmp_dir / "history.json"),
    )
    storage._USERS_DB = str(tmp_dir / "users.db")

    try:
        user = storage.create_user(
            "discord-user",
            "hash",
            nickname="DC 暱稱",
            discord_uid="987654",
        )

        matched = storage.get_user_by_discord_uid(987654)

        assert matched["id"] == user["id"]
        assert matched["nickname"] == "DC 暱稱"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_telegram_id_query_detection():
    assert _is_telegram_id_query("我的 Telegram ID 是多少？")
    assert _is_telegram_id_query("what is my telegram id")
    assert not _is_telegram_id_query("今天想聊 Telegram bot 設定")


@pytest.mark.asyncio
async def test_persona_sync_requires_explicit_character_id():
    manager = PersonaSyncManager()

    should, reason = await manager.should_run(
        storage=None,
        prefs={"active_character_id": "char-a", "persona_sync_enabled": True},
        persona_face="public",
        character_id=None,
    )

    assert should is False
    assert reason == "missing_character_id"

    status = manager.get_sync_status(
        storage=None,
        prefs={"active_character_id": "char-a"},
        persona_face="public",
        character_id=None,
    )
    assert status["character_id"] == ""
    assert status["error"] == "missing_character_id"
