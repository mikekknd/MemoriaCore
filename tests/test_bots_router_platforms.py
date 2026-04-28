import asyncio

from api.routers import bots as bots_router


class _FakeManager:
    def __init__(self, platform):
        self.platform = platform
        self.reloaded = []
        self.stopped = []

    def get_status(self, bot_id, platform):
        return {
            "bot_id": bot_id,
            "platform": platform,
            "status": f"{platform}-status",
            "running": True,
            "last_error": None,
        }

    async def reload_bot(self, bot_id):
        self.reloaded.append(bot_id)

    async def stop_bot(self, bot_id):
        self.stopped.append(bot_id)


def test_runtime_status_uses_discord_manager(monkeypatch):
    discord_mgr = _FakeManager("discord")
    monkeypatch.setattr(bots_router, "get_discord_bot_manager", lambda: discord_mgr)

    status = bots_router._runtime_status("bot-d", "discord")

    assert status["platform"] == "discord"
    assert status["status"] == "discord-status"


def test_reload_runtime_uses_discord_manager(monkeypatch):
    discord_mgr = _FakeManager("discord")
    telegram_mgr = _FakeManager("telegram")
    monkeypatch.setattr(bots_router, "get_discord_bot_manager", lambda: discord_mgr)
    monkeypatch.setattr(bots_router, "get_telegram_bot_manager", lambda: telegram_mgr)

    asyncio.run(bots_router._reload_runtime("bot-d", "discord"))

    assert discord_mgr.reloaded == ["bot-d"]
    assert telegram_mgr.reloaded == []


def test_other_platform_status_is_unsupported():
    status = bots_router._runtime_status("bot-o", "other")

    assert status == {
        "bot_id": "bot-o",
        "platform": "other",
        "status": "unsupported",
        "running": False,
        "last_error": None,
    }
