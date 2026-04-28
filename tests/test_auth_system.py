"""Auth 系統的低階單元測試。"""
from datetime import datetime
from pathlib import Path
import shutil
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.dependencies as deps
from api.auth_utils import create_jwt, decode_jwt, issue_token_payload
from api.middleware.auth import AuthMiddleware
from api.routers import admin_users, auth, character, personality_public, profile
from core.storage_manager import StorageManager


@pytest.fixture
def auth_tmp_dir():
    base = Path("tests") / ".auth_tmp" / uuid.uuid4().hex
    base.mkdir(parents=True, exist_ok=False)
    try:
        yield base
    finally:
        shutil.rmtree(base, ignore_errors=True)


def _storage(base):
    s = StorageManager(
        prefs_file=str(base / "prefs.json"),
        history_file=str(base / "history.json"),
    )
    s._USERS_DB = str(base / "users.db")
    return s


def test_first_registered_user_is_admin(auth_tmp_dir):
    storage = _storage(auth_tmp_dir)

    first = storage.create_user("owner", "hash1")
    second = storage.create_user("guest", "hash2")

    assert first["role"] == "admin"
    assert second["role"] == "user"
    assert storage.count_users() == 2


def test_token_version_revokes_existing_payload(auth_tmp_dir, monkeypatch):
    monkeypatch.setenv("MEMORIACORE_JWT_SECRET", "test-secret")
    storage = _storage(auth_tmp_dir)
    user = storage.create_user("owner", "hash1")
    payload, csrf = issue_token_payload(user)
    token = create_jwt(payload)

    decoded = decode_jwt(token)
    assert decoded["sub"] == str(user["id"])
    assert decoded["csrf"] == csrf
    assert decoded["ver"] == 0

    updated = storage.increment_user_token_version(user["id"])
    assert updated["token_version"] == 1
    assert decoded["ver"] != updated["token_version"]


def test_auth_attempt_locking_is_persistent(auth_tmp_dir):
    storage = _storage(auth_tmp_dir)
    username = "owner"
    ip = "127.0.0.1"

    for _ in range(5):
        storage.record_auth_attempt(username, ip, limit=5, lock_minutes=15)

    assert storage.is_auth_locked(username, ip)
    attempt = storage.get_auth_attempt(username, ip)
    assert attempt["failed_count"] == 5
    assert datetime.fromisoformat(attempt["locked_until"]) > datetime.now()

    storage.reset_auth_attempts(username, ip)
    assert not storage.is_auth_locked(username, ip)


def test_auth_endpoints_issue_cookie_and_enforce_csrf(auth_tmp_dir, monkeypatch):
    monkeypatch.setenv("MEMORIACORE_JWT_SECRET", "endpoint-test-secret")
    storage = _storage(auth_tmp_dir)
    deps.storage = storage

    app = FastAPI()
    app.include_router(auth.router, prefix="/api/v1")
    app.add_middleware(AuthMiddleware)
    client = TestClient(app)

    try:
        response = client.post(
            "/api/v1/auth/register",
            json={
                "username": "owner",
                "password": "correct-horse-1",
                "password_confirm": "correct-horse-1",
            },
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["user"]["role"] == "admin"
        assert "mc_auth" in response.cookies

        assert client.get("/api/v1/auth/me").status_code == 200
        assert client.post("/api/v1/auth/logout").status_code == 403
        assert client.post(
            "/api/v1/auth/logout",
            headers={"X-CSRF-Token": payload["csrf_token"]},
        ).status_code == 200
        assert client.get("/api/v1/auth/me").status_code == 401
    finally:
        deps.storage = None


def test_password_minimum_length_is_six_characters(auth_tmp_dir, monkeypatch):
    monkeypatch.setenv("MEMORIACORE_JWT_SECRET", "password-length-test-secret")
    storage = _storage(auth_tmp_dir)
    deps.storage = storage

    app = FastAPI()
    app.include_router(auth.router, prefix="/api/v1")
    app.add_middleware(AuthMiddleware)
    client = TestClient(app)

    try:
        too_short = client.post(
            "/api/v1/auth/register",
            json={
                "username": "short-pass",
                "password": "abc12",
                "password_confirm": "abc12",
            },
        )
        assert too_short.status_code == 422

        accepted = client.post(
            "/api/v1/auth/register",
            json={
                "username": "six-pass",
                "password": "abc123",
                "password_confirm": "abc123",
            },
        )
        assert accepted.status_code == 200, accepted.text
    finally:
        deps.storage = None


def test_registration_disabled_still_allows_first_admin_bootstrap(auth_tmp_dir, monkeypatch):
    monkeypatch.setenv("MEMORIACORE_JWT_SECRET", "bootstrap-test-secret")
    storage = _storage(auth_tmp_dir)
    storage.save_prefs({"registration_enabled": False})
    deps.storage = storage

    app = FastAPI()
    app.include_router(auth.router, prefix="/api/v1")
    app.add_middleware(AuthMiddleware)
    client = TestClient(app)

    try:
        first = client.post(
            "/api/v1/auth/register",
            json={
                "username": "owner",
                "password": "abc123",
                "password_confirm": "abc123",
            },
        )
        assert first.status_code == 200, first.text
        assert first.json()["user"]["role"] == "admin"

        second = client.post(
            "/api/v1/auth/register",
            json={
                "username": "viewer",
                "password": "abc123",
                "password_confirm": "abc123",
            },
        )
        assert second.status_code == 403
    finally:
        deps.storage = None


def test_public_personality_api_is_available_to_regular_users(auth_tmp_dir, monkeypatch):
    monkeypatch.setenv("MEMORIACORE_JWT_SECRET", "public-personality-test-secret")
    storage = _storage(auth_tmp_dir)
    deps.storage = storage

    class FakeCharacterManager:
        def load_characters(self):
            return [
                {
                    "character_id": "catgirl-fragment",
                    "name": "Catgirl",
                    "system_prompt": "private prompt",
                }
            ]

    deps.character_mgr = FakeCharacterManager()

    app = FastAPI()
    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(character.router, prefix="/api/v1")
    app.include_router(personality_public.router, prefix="/api/v1")
    app.add_middleware(AuthMiddleware)
    client = TestClient(app)

    try:
        client.post(
            "/api/v1/auth/register",
            json={
                "username": "owner",
                "password": "abc123",
                "password_confirm": "abc123",
            },
        )
        user_response = client.post(
            "/api/v1/auth/register",
            json={
                "username": "viewer",
                "password": "abc123",
                "password_confirm": "abc123",
            },
        )
        assert user_response.status_code == 200, user_response.text
        assert user_response.json()["user"]["role"] == "user"

        assert client.get("/api/v1/character").status_code == 403

        public_response = client.get("/api/v1/personality-public/characters")
        assert public_response.status_code == 200, public_response.text
        assert public_response.json() == [
            {"character_id": "catgirl-fragment", "name": "Catgirl"}
        ]

        private_response = client.get(
            "/api/v1/personality-public/snapshots/latest/tree"
            "?character_id=catgirl-fragment&persona_face=private"
        )
        assert private_response.status_code == 403
    finally:
        deps.storage = None
        deps.character_mgr = None


def test_admin_users_api_manages_and_deletes_test_user_data(auth_tmp_dir, monkeypatch):
    monkeypatch.setenv("MEMORIACORE_JWT_SECRET", "admin-users-test-secret")
    storage = _storage(auth_tmp_dir)
    storage._CONV_DB = str(auth_tmp_dir / "conversation.db")
    deps.storage = storage

    app = FastAPI()
    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(admin_users.router, prefix="/api/v1")
    app.add_middleware(AuthMiddleware)
    client = TestClient(app)

    try:
        admin = client.post(
            "/api/v1/auth/register",
            json={
                "username": "owner",
                "password": "abc123",
                "password_confirm": "abc123",
            },
        )
        assert admin.status_code == 200, admin.text
        admin_csrf = admin.json()["csrf_token"]

        user = client.post(
            "/api/v1/auth/register",
            json={
                "username": "test-user",
                "password": "abc123",
                "password_confirm": "abc123",
            },
        )
        assert user.status_code == 200, user.text
        user_id = user.json()["user"]["id"]
        user_csrf = user.json()["csrf_token"]

        assert client.get("/api/v1/admin/users").status_code == 403

        storage.create_conversation_session("sid-test", user_id=str(user_id))
        storage.save_conversation_message("sid-test", "user", "hello")
        memory_db = str(auth_tmp_dir / "memory_db_test.db")
        storage.upsert_profile(memory_db, "pref", "tea", "preference", user_id=str(user_id))
        storage.save_core_memory(memory_db, "core-test", "2026-04-28T00:00:00", "insight", [0.1], user_id=str(user_id))
        storage.insert_topic_cache(memory_db, "topic-test", "tea", "summary", user_id=str(user_id))

        login_admin = client.post(
            "/api/v1/auth/login",
            json={"username": "owner", "password": "abc123"},
        )
        assert login_admin.status_code == 200, login_admin.text
        admin_csrf = login_admin.json()["csrf_token"]

        users = client.get("/api/v1/admin/users")
        assert users.status_code == 200, users.text
        target = next(u for u in users.json() if u["username"] == "test-user")
        assert target["stats"]["sessions"] == 1
        assert target["stats"]["messages"] == 1
        assert target["stats"]["profiles"] == 1
        assert target["stats"]["core_memories"] == 1
        assert target["stats"]["topics"] == 1

        revoke = client.post(f"/api/v1/admin/users/{user_id}/revoke", headers={"X-CSRF-Token": admin_csrf})
        assert revoke.status_code == 200, revoke.text

        reset = client.post(
            f"/api/v1/admin/users/{user_id}/password",
            headers={"X-CSRF-Token": admin_csrf},
            json={"new_password": "newpass1"},
        )
        assert reset.status_code == 200, reset.text
        old_login = client.post(
            "/api/v1/auth/login",
            json={"username": "test-user", "password": "abc123"},
        )
        assert old_login.status_code == 401

        bad_delete = client.request(
            "DELETE",
            f"/api/v1/admin/users/{user_id}",
            headers={"X-CSRF-Token": admin_csrf},
            json={"confirm_username": "wrong"},
        )
        assert bad_delete.status_code == 400

        delete_self = client.request(
            "DELETE",
            "/api/v1/admin/users/1",
            headers={"X-CSRF-Token": admin_csrf},
            json={"confirm_username": "owner"},
        )
        assert delete_self.status_code == 400

        deleted = client.request(
            "DELETE",
            f"/api/v1/admin/users/{user_id}",
            headers={"X-CSRF-Token": admin_csrf},
            json={"confirm_username": "test-user"},
        )
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["deleted_counts"]["sessions"] == 1
        assert storage.get_user_by_id(user_id) is None
        assert storage.load_conversation_sessions(user_id=str(user_id)) == []
        assert storage.load_all_profiles(memory_db, user_id=str(user_id)) == []
        assert client.post(
            "/api/v1/auth/login",
            json={"username": "test-user", "password": "newpass1"},
        ).status_code == 401
    finally:
        deps.storage = None


def test_profile_list_route_is_registered():
    routes = {
        (route.path, tuple(sorted(route.methods)), route.endpoint.__name__)
        for route in profile.router.routes
    }
    assert ("/profile", ("GET",), "list_profiles") in routes
    assert ("/profile", ("GET",), "_visibility_filter_for") not in routes


def test_client_ip_ignores_forwarded_header_by_default(monkeypatch):
    class Client:
        host = "10.0.0.1"

    class Request:
        headers = {"x-forwarded-for": "203.0.113.9"}
        client = Client()

    monkeypatch.delenv("MEMORIACORE_TRUST_PROXY_HEADERS", raising=False)
    assert auth._client_ip(Request()) == "10.0.0.1"


def test_client_ip_uses_forwarded_header_when_trusted(monkeypatch):
    class Client:
        host = "10.0.0.1"

    class Request:
        headers = {"x-forwarded-for": "203.0.113.9, 10.0.0.2"}
        client = Client()

    monkeypatch.setenv("MEMORIACORE_TRUST_PROXY_HEADERS", "1")
    assert auth._client_ip(Request()) == "203.0.113.9"


def test_password_change_rejects_username_as_new_password(auth_tmp_dir, monkeypatch):
    monkeypatch.setenv("MEMORIACORE_JWT_SECRET", "password-change-test-secret")
    storage = _storage(auth_tmp_dir)
    deps.storage = storage

    app = FastAPI()
    app.include_router(auth.router, prefix="/api/v1")
    app.add_middleware(AuthMiddleware)
    client = TestClient(app)

    try:
        response = client.post(
            "/api/v1/auth/register",
            json={
                "username": "owner-name",
                "password": "correct-horse-1",
                "password_confirm": "correct-horse-1",
            },
        )
        assert response.status_code == 200, response.text
        csrf = response.json()["csrf_token"]

        response = client.put(
            "/api/v1/auth/password",
            headers={"X-CSRF-Token": csrf},
            json={
                "old_password": "correct-horse-1",
                "new_password": "owner-name",
            },
        )
        assert response.status_code == 422
    finally:
        deps.storage = None
