from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from core.storage_manager import StorageManager


@pytest.mark.asyncio
async def test_lifespan_shutdown_awaits_all_cancelled_background_tasks():
    import api.main as api_main

    finalized: list[str] = []

    async def worker(name: str) -> None:
        try:
            await asyncio.Event().wait()
        finally:
            finalized.append(name)

    cleanup_task = asyncio.create_task(worker("cleanup"))
    persona_task = asyncio.create_task(worker("persona"))
    await asyncio.sleep(0)

    await api_main._cancel_lifespan_tasks(cleanup_task, None, persona_task)

    assert cleanup_task.done()
    assert persona_task.done()
    assert set(finalized) == {"cleanup", "persona"}


class _FakeBotManager:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def sync_from_registry(self) -> None:
        self.calls.append("sync")

    async def stop_all(self) -> None:
        self.calls.append("stop")


class _FakePersonaSyncManager:
    async def should_run(self, *args, **kwargs):
        return False, "not_due"

    async def run_sync(self, *args, **kwargs):
        raise AssertionError("persona sync should not run during lifecycle smoke")


def _storage_manager(tmp_path) -> StorageManager:
    return StorageManager(
        prefs_file=str(tmp_path / "prefs.json"),
        history_file=str(tmp_path / "history.json"),
        persona_snapshot_db_path=str(tmp_path / "persona_snapshots.db"),
        youtube_bridge_v2_db_path=str(tmp_path / "youtubebridge_v2.db"),
    )


def _install_lifecycle_fakes(monkeypatch, tmp_path, prefs: dict[str, object] | None = None):
    import api.main as api_main

    storage = _storage_manager(tmp_path)
    storage.save_prefs(prefs or {})
    startup_calls: list[str] = []
    telegram = _FakeBotManager()
    discord = _FakeBotManager()

    async def forbidden_background_gather(*args, **kwargs):
        raise AssertionError("background gather should not start without tavily_api_key")

    monkeypatch.setattr(api_main, "init_all", lambda: startup_calls.append("init_all"))
    monkeypatch.setattr(api_main, "get_storage", lambda: storage)
    monkeypatch.setattr(api_main, "get_memory_sys", lambda: SimpleNamespace(db_path=""))
    monkeypatch.setattr(api_main, "get_router", lambda: object())
    monkeypatch.setattr(api_main, "get_persona_sync_manager", lambda: _FakePersonaSyncManager())
    monkeypatch.setattr(api_main, "get_telegram_bot_manager", lambda: telegram)
    monkeypatch.setattr(api_main, "get_discord_bot_manager", lambda: discord)
    monkeypatch.setattr(api_main, "is_db_maintenance_mode", lambda: False)
    monkeypatch.setattr(api_main, "start_background_gather_loop", forbidden_background_gather)
    monkeypatch.setattr(api_main, "_v2_composition_cache", None, raising=False)
    monkeypatch.setattr(api_main, "_v2_composition_storage_id", None, raising=False)
    return api_main, storage, startup_calls, telegram, discord


def test_main_app_lifespan_starts_v2_routes_and_shuts_down_managers(tmp_path, monkeypatch):
    api_main, storage, startup_calls, telegram, discord = _install_lifecycle_fakes(
        monkeypatch,
        tmp_path,
    )

    with TestClient(api_main.app, client=("127.0.0.1", 50000)) as client:
        static_response = client.get("/v2/static/operator-console/index.html")
        create_response = client.post(
            "/v2/sessions",
            json={
                "command_id": "lifecycle-create",
                "session_id": "lifecycle-session",
                "aftertalk_policy": "auto",
            },
        )

    assert static_response.status_code == 200
    assert 'id="operatorConsoleRoot"' in static_response.text
    assert create_response.status_code == 200
    assert create_response.json()["phase"] == "planned_show"
    assert storage.get_v2_session("lifecycle-session")["current_phase"] == "planned_show"
    assert startup_calls == ["init_all"]
    assert telegram.calls == ["sync", "stop"]
    assert discord.calls == ["sync", "stop"]
