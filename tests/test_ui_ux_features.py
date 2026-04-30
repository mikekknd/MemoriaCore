"""UI/UX 修正項目的 API 與儲存層測試。"""
import asyncio
from pathlib import Path
import shutil
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.dependencies as deps
from api.middleware.auth import AuthMiddleware
from api.main import _persona_sync_candidate_character_ids, _should_log_persona_sync_skip
from api.routers import auth, chat_rest, session, system
from api.session_manager import session_manager
from core.storage_manager import StorageManager


def _tmp_dir() -> Path:
    base = Path("tests") / ".ui_ux_tmp" / uuid.uuid4().hex
    base.mkdir(parents=True, exist_ok=False)
    return base


def _storage(base: Path) -> StorageManager:
    storage = StorageManager(
        prefs_file=str(base / "prefs.json"),
        history_file=str(base / "history.json"),
    )
    storage._USERS_DB = str(base / "users.db")
    storage._CONV_DB = str(base / "conversation.db")
    return storage


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(system.router, prefix="/api/v1")
    app.include_router(session.router, prefix="/api/v1")
    app.include_router(chat_rest.router, prefix="/api/v1")
    app.add_middleware(AuthMiddleware)
    return app


def test_admin_bypass_requires_enabled_loopback_and_admin(monkeypatch):
    monkeypatch.setenv("MEMORIACORE_JWT_SECRET", "ui-ux-bypass-secret")
    base = _tmp_dir()
    storage = _storage(base)
    deps.storage = storage

    try:
        app = _app()
        loopback_client = TestClient(app, client=("127.0.0.1", 50000))
        assert loopback_client.post("/api/v1/auth/bypass").status_code == 403

        storage.save_prefs({"admin_bypass_enabled": True})
        assert loopback_client.post("/api/v1/auth/bypass").status_code == 404

        storage.create_user("owner", "hash1")
        remote_client = TestClient(app, client=("203.0.113.10", 50000))
        assert remote_client.post("/api/v1/auth/bypass").status_code == 403

        response = loopback_client.post("/api/v1/auth/bypass")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["user"]["role"] == "admin"
        assert payload["csrf_token"]
        assert "mc_auth" in response.cookies
    finally:
        deps.storage = None
        shutil.rmtree(base, ignore_errors=True)


def test_system_config_roundtrips_admin_bypass(monkeypatch):
    monkeypatch.setenv("MEMORIACORE_JWT_SECRET", "ui-ux-config-secret")
    base = _tmp_dir()
    storage = _storage(base)
    deps.storage = storage

    try:
        client = TestClient(_app(), client=("127.0.0.1", 50000))
        registered = client.post(
            "/api/v1/auth/register",
            json={
                "username": "owner",
                "password": "abc123",
                "password_confirm": "abc123",
            },
        )
        assert registered.status_code == 200, registered.text
        csrf = registered.json()["csrf_token"]

        config = client.get("/api/v1/system/config")
        assert config.status_code == 200, config.text
        assert config.json()["admin_bypass_enabled"] is False
        assert config.json()["group_chat_turn_delay_seconds"] == 2.0

        updated = client.put(
            "/api/v1/system/config",
            headers={"X-CSRF-Token": csrf},
            json={"admin_bypass_enabled": True, "group_chat_turn_delay_seconds": 0.5},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["admin_bypass_enabled"] is True
        assert updated.json()["group_chat_turn_delay_seconds"] == 0.5
        assert storage.load_prefs()["admin_bypass_enabled"] is True
        assert storage.load_prefs()["group_chat_turn_delay_seconds"] == 0.5
    finally:
        deps.storage = None
        shutil.rmtree(base, ignore_errors=True)


def test_personality_root_endpoint_removed_and_sync_requires_character_id(monkeypatch):
    monkeypatch.setenv("MEMORIACORE_JWT_SECRET", "ui-ux-personality-secret")
    base = _tmp_dir()
    storage = _storage(base)
    deps.storage = storage

    class FakePersonaSyncManager:
        def get_sync_status(self, storage=None, character_id=None, persona_face="public", prefs=None):
            return {
                "character_id": character_id,
                "persona_face": persona_face,
                "messages_since_last": 0,
            }

    deps.persona_sync_mgr = FakePersonaSyncManager()

    try:
        client = TestClient(_app(), client=("127.0.0.1", 50000))
        registered = client.post(
            "/api/v1/auth/register",
            json={
                "username": "owner",
                "password": "abc123",
                "password_confirm": "abc123",
            },
        )
        assert registered.status_code == 200, registered.text

        assert client.get("/api/v1/system/personality").status_code == 404
        assert client.get("/api/v1/system/personality/sync-status").status_code == 422

        status = client.get(
            "/api/v1/system/personality/sync-status",
            params={"character_id": "char-a", "persona_face": "public"},
        )
        assert status.status_code == 200, status.text
        assert status.json()["character_id"] == "char-a"
    finally:
        deps.storage = None
        deps.persona_sync_mgr = None
        shutil.rmtree(base, ignore_errors=True)


def test_auto_persona_sync_uses_conversation_candidates_without_default_fallback():
    class FakeStorage:
        def list_conversation_character_ids(self):
            return ["char-a", "char-b"]

        def list_recent_conversation_character_ids(self, limit=50):
            raise AssertionError("不應 fallback 到 recent helper")

    assert _persona_sync_candidate_character_ids(FakeStorage()) == ["char-a", "char-b"]

    class EmptyStorage:
        def list_conversation_character_ids(self):
            return []

    assert _persona_sync_candidate_character_ids(EmptyStorage()) == []


def test_auto_persona_sync_skip_log_suppresses_insufficient_messages():
    assert _should_log_persona_sync_skip("no_messages_yet") is False
    assert _should_log_persona_sync_skip("insufficient_messages(12/50)") is False
    assert _should_log_persona_sync_skip("not_idle(1.0min < 10min)") is True


def test_session_creation_accepts_character_id_and_rejects_unknown(monkeypatch):
    monkeypatch.setenv("MEMORIACORE_JWT_SECRET", "ui-ux-session-secret")
    base = _tmp_dir()
    storage = _storage(base)
    deps.storage = storage
    session_manager.set_storage(storage)
    session_manager._sessions.clear()

    class FakeCharacterManager:
        def get_character(self, character_id):
            if character_id == "char-b":
                return {"character_id": "char-b", "name": "角色 B"}
            return None

    deps.character_mgr = FakeCharacterManager()

    try:
        client = TestClient(_app(), client=("127.0.0.1", 50000))
        registered = client.post(
            "/api/v1/auth/register",
            json={
                "username": "owner",
                "password": "abc123",
                "password_confirm": "abc123",
            },
        )
        csrf = registered.json()["csrf_token"]

        created = client.post(
            "/api/v1/session",
            headers={"X-CSRF-Token": csrf},
            json={"channel": "dashboard", "character_id": "char-b"},
        )
        assert created.status_code == 200, created.text
        assert created.json()["character_id"] == "char-b"

        invalid = client.post(
            "/api/v1/session",
            headers={"X-CSRF-Token": csrf},
            json={"channel": "dashboard", "character_id": "missing"},
        )
        assert invalid.status_code == 404
    finally:
        session_manager._sessions.clear()
        session_manager.set_storage(None)
        deps.storage = None
        deps.character_mgr = None
        shutil.rmtree(base, ignore_errors=True)


def test_conversation_message_persists_character_name():
    base = _tmp_dir()
    storage = _storage(base)

    try:
        storage.create_conversation_session("sid-a", user_id="1", character_id="char-a")
        storage.save_conversation_message(
            "sid-a",
            "assistant",
            "hello",
            character_name="角色 A",
            character_id="char-a",
        )

        messages = storage.load_conversation_messages("sid-a")
        assert messages[0]["character_name"] == "角色 A"
        assert messages[0]["character_id"] == "char-a"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_group_session_creation_dedupes_and_persists_participants(monkeypatch):
    monkeypatch.setenv("MEMORIACORE_JWT_SECRET", "ui-ux-group-secret")
    base = _tmp_dir()
    storage = _storage(base)
    deps.storage = storage
    session_manager.set_storage(storage)
    session_manager._sessions.clear()

    class FakeCharacterManager:
        def get_character(self, character_id):
            if character_id in {"char-a", "char-b"}:
                return {"character_id": character_id, "name": character_id}
            return None

    deps.character_mgr = FakeCharacterManager()

    try:
        client = TestClient(_app(), client=("127.0.0.1", 50000))
        registered = client.post(
            "/api/v1/auth/register",
            json={
                "username": "owner",
                "password": "abc123",
                "password_confirm": "abc123",
            },
        )
        csrf = registered.json()["csrf_token"]

        created = client.post(
            "/api/v1/session",
            headers={"X-CSRF-Token": csrf},
            json={
                "channel": "dashboard",
                "character_ids": ["char-a", "char-b", "char-a"],
                "group_name": "測試群組",
            },
        )
        assert created.status_code == 200, created.text
        payload = created.json()
        assert payload["session_mode"] == "group"
        assert payload["character_ids"] == ["char-a", "char-b"]

        info = storage.get_session_info(payload["session_id"])
        assert info["session_mode"] == "group"
        assert info["group_name"] == "測試群組"
        assert info["character_ids"] == ["char-a", "char-b"]
    finally:
        session_manager._sessions.clear()
        session_manager.set_storage(None)
        deps.storage = None
        deps.character_mgr = None
        shutil.rmtree(base, ignore_errors=True)


def test_session_roster_update_persists_single_event_and_active_participants():
    base = _tmp_dir()
    storage = _storage(base)
    session_manager.set_storage(storage)
    session_manager._sessions.clear()

    try:
        session = asyncio.run(session_manager.create(
            channel="dashboard",
            user_id="owner",
            character_id="char-a",
            character_ids=["char-a"],
        ))

        event = asyncio.run(session_manager.update_roster(
            session.session_id,
            ["char-a", "char-b"],
            character_names={"char-a": "角色 A", "char-b": "角色 B"},
        ))
        assert event["type"] == "roster_changed"
        assert event["active_character_ids"] == ["char-a", "char-b"]
        assert "加入 角色 B" in event["content"]

        duplicate = asyncio.run(session_manager.update_roster(
            session.session_id,
            ["char-b", "char-a"],
            character_names={"char-a": "角色 A", "char-b": "角色 B"},
        ))
        assert duplicate is None

        messages = storage.load_conversation_messages(session.session_id)
        roster_events = [m for m in messages if m["role"] == "system_event"]
        assert len(roster_events) == 1
        assert roster_events[0]["debug_info"]["event_type"] == "roster_changed"

        info = storage.get_session_info(session.session_id)
        assert info["session_mode"] == "group"
        assert info["character_ids"] == ["char-a", "char-b"]
    finally:
        session_manager._sessions.clear()
        session_manager.set_storage(None)
        shutil.rmtree(base, ignore_errors=True)


def test_stream_sync_emits_roster_changed_before_group_done(monkeypatch):
    monkeypatch.setenv("MEMORIACORE_JWT_SECRET", "ui-ux-roster-stream-secret")
    base = _tmp_dir()
    storage = _storage(base)
    deps.storage = storage
    session_manager.set_storage(storage)
    session_manager._sessions.clear()

    class FakeCharacterManager:
        def get_character(self, character_id):
            if character_id in {"char-a", "char-b"}:
                return {
                    "character_id": character_id,
                    "name": {"char-a": "角色 A", "char-b": "角色 B"}[character_id],
                    "tts_language": "",
                    "tts_rules": "",
                }
            return None

    async def fake_group_loop(**kwargs):
        return []

    deps.character_mgr = FakeCharacterManager()
    monkeypatch.setattr(chat_rest, "run_group_chat_loop", fake_group_loop)

    try:
        client = TestClient(_app(), client=("127.0.0.1", 50000))
        registered = client.post(
            "/api/v1/auth/register",
            json={
                "username": "owner",
                "password": "abc123",
                "password_confirm": "abc123",
            },
        )
        csrf = registered.json()["csrf_token"]

        created = client.post(
            "/api/v1/session",
            headers={"X-CSRF-Token": csrf},
            json={"channel": "dashboard", "character_ids": ["char-a"]},
        )
        session_id = created.json()["session_id"]

        response = client.post(
            "/api/v1/chat/stream-sync",
            headers={"X-CSRF-Token": csrf},
            json={
                "session_id": session_id,
                "content": "hello",
                "character_ids": ["char-a", "char-b"],
            },
        )
        assert response.status_code == 200, response.text
        body = response.text
        assert body.index('"type": "roster_changed"') < body.index('"type": "group_done"')

        messages = storage.load_conversation_messages(session_id)
        assert [m["role"] for m in messages[:2]] == ["system_event", "user"]
    finally:
        session_manager._sessions.clear()
        session_manager.set_storage(None)
        deps.storage = None
        deps.character_mgr = None
        shutil.rmtree(base, ignore_errors=True)


def test_chat_sync_returns_and_persists_character_name(monkeypatch):
    monkeypatch.setenv("MEMORIACORE_JWT_SECRET", "ui-ux-chat-secret")
    base = _tmp_dir()
    storage = _storage(base)
    deps.storage = storage
    session_manager.set_storage(storage)
    session_manager._sessions.clear()

    class FakeCharacterManager:
        def get_character(self, character_id):
            if character_id == "char-b":
                return {
                    "character_id": "char-b",
                    "name": "角色 B",
                    "tts_language": "",
                    "tts_rules": "",
                }
            return None

    def fake_orchestration(*args, **kwargs):
        return (
            "測試回覆",
            [],
            {},
            False,
            None,
            "內在想法",
            None,
            None,
            "測試回覆",
            "",
            [],
        )

    deps.character_mgr = FakeCharacterManager()
    monkeypatch.setattr(chat_rest, "_select_orchestration", lambda prefs: fake_orchestration)

    try:
        client = TestClient(_app(), client=("127.0.0.1", 50000))
        registered = client.post(
            "/api/v1/auth/register",
            json={
                "username": "owner",
                "password": "abc123",
                "password_confirm": "abc123",
            },
        )
        csrf = registered.json()["csrf_token"]

        created = client.post(
            "/api/v1/session",
            headers={"X-CSRF-Token": csrf},
            json={"channel": "dashboard", "character_id": "char-b"},
        )
        session_id = created.json()["session_id"]

        response = client.post(
            "/api/v1/chat/sync",
            headers={"X-CSRF-Token": csrf},
            json={"session_id": session_id, "content": "hello"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["character_name"] == "角色 B"
        assert response.json()["character_id"] == "char-b"

        messages = storage.load_conversation_messages(session_id)
        assistant = [m for m in messages if m["role"] == "assistant"][0]
        assert assistant["character_name"] == "角色 B"
        assert assistant["character_id"] == "char-b"
    finally:
        session_manager._sessions.clear()
        session_manager.set_storage(None)
        deps.storage = None
        deps.character_mgr = None
        shutil.rmtree(base, ignore_errors=True)


def test_chat_sync_returns_roster_event_when_roster_changes(monkeypatch):
    monkeypatch.setenv("MEMORIACORE_JWT_SECRET", "ui-ux-chat-roster-secret")
    base = _tmp_dir()
    storage = _storage(base)
    deps.storage = storage
    session_manager.set_storage(storage)
    session_manager._sessions.clear()

    class FakeCharacterManager:
        def get_character(self, character_id):
            if character_id in {"char-a", "char-b"}:
                return {
                    "character_id": character_id,
                    "name": {"char-a": "角色 A", "char-b": "角色 B"}[character_id],
                    "tts_language": "",
                    "tts_rules": "",
                }
            return None

    def fake_orchestration(*args, **kwargs):
        return ("ok", [], {}, False, None, "", None, None, "ok", "", [])

    async def fake_group_loop(**kwargs):
        return [{
            "reply": "ok",
            "extracted_entities": [],
            "retrieval_context": {},
            "cited_memory_uids": [],
            "internal_thought": "",
            "speech": "ok",
            "thinking_speech": "",
            "character_id": "char-a",
            "character_name": "角色 A",
            "turn_index": 0,
            "is_final": True,
        }]

    deps.character_mgr = FakeCharacterManager()
    monkeypatch.setattr(chat_rest, "_select_orchestration", lambda prefs: fake_orchestration)
    monkeypatch.setattr(chat_rest, "run_group_chat_loop", fake_group_loop)

    try:
        client = TestClient(_app(), client=("127.0.0.1", 50000))
        registered = client.post(
            "/api/v1/auth/register",
            json={"username": "owner", "password": "abc123", "password_confirm": "abc123"},
        )
        csrf = registered.json()["csrf_token"]

        created = client.post(
            "/api/v1/session",
            headers={"X-CSRF-Token": csrf},
            json={"channel": "dashboard", "character_ids": ["char-a"]},
        )
        session_id = created.json()["session_id"]

        # 同樣 roster：不應產生 roster_event
        unchanged = client.post(
            "/api/v1/chat/sync",
            headers={"X-CSRF-Token": csrf},
            json={"session_id": session_id, "content": "hi", "character_ids": ["char-a"]},
        )
        assert unchanged.status_code == 200, unchanged.text
        assert unchanged.json().get("roster_event") is None

        # 變更 roster：應在回應中帶 roster_event
        changed = client.post(
            "/api/v1/chat/sync",
            headers={"X-CSRF-Token": csrf},
            json={"session_id": session_id, "content": "hi again", "character_ids": ["char-a", "char-b"]},
        )
        assert changed.status_code == 200, changed.text
        roster_event = changed.json().get("roster_event")
        assert roster_event is not None
        assert roster_event["type"] == "roster_changed"
        assert roster_event["active_character_ids"] == ["char-a", "char-b"]
        assert "char-b" in roster_event["added_character_ids"]
    finally:
        session_manager._sessions.clear()
        session_manager.set_storage(None)
        deps.storage = None
        deps.character_mgr = None
        shutil.rmtree(base, ignore_errors=True)
