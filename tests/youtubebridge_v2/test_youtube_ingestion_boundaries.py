from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from tests.youtubebridge_v2.fakes import (
    FakeAftertalkRunner,
    FakeClosingRunner,
    FakePlannedShowRunner,
    InMemoryV2StorageManager,
)
from YouTubeBridgeV2.app import create_v2_app
from YouTubeBridgeV2.composition import create_v2_composition


ROOT = Path(__file__).resolve().parents[2]
STARTED_AT = datetime(2026, 5, 12, 8, 0, tzinfo=timezone.utc)


def _assert_no_private_payload(value: object) -> None:
    text = repr(value).lower()
    for forbidden in (
        "hidden_prompt",
        "raw_payload",
        "raw_youtube_payload",
        "rawtopicpack",
        "raw_topic_pack",
        "access_token",
        "authorization",
        "secret-value",
        "token",
        "must not leak",
    ):
        assert forbidden not in text


def _composition():
    storage = InMemoryV2StorageManager()
    planned_show = FakePlannedShowRunner(storage)
    composition = create_v2_composition(
        storage_manager=storage,
        planned_show_runner=planned_show,
        aftertalk_runner=FakeAftertalkRunner(storage),
        closing_runner=FakeClosingRunner(storage),
    )
    return composition, storage, planned_show


def _client():
    composition, storage, planned_show = _composition()
    client = TestClient(create_v2_app(composition, now_provider=lambda: STARTED_AT))
    return client, storage, planned_show


def _create_session(client: TestClient, session_id: str) -> None:
    response = client.post(
        "/v2/sessions",
        json={
            "command_id": f"{session_id}-create",
            "session_id": session_id,
            "aftertalk_policy": "auto",
        },
    )
    assert response.status_code == 200


def test_fake_backed_youtube_super_chat_ingestion_is_public_safe_across_reads():
    client, _storage, planned_show = _client()
    _create_session(client, "session-youtube-boundary")

    response = client.post(
        "/v2/sessions/session-youtube-boundary/youtube-events",
        json={
            "command_id": "cmd-super-chat-boundary",
            "youtube_event": {
                "id": "sc-boundary-1",
                "snippet": {
                    "type": "superChatEvent",
                    "publishedAt": "2026-05-12T08:20:00Z",
                    "displayMessage": "Great stream",
                    "superChatDetails": {
                        "amountMicros": 150000000,
                        "currency": "TWD",
                        "amountDisplayString": "NT$150",
                        "userComment": "Great stream",
                        "tier": 3,
                    },
                },
                "authorDetails": {
                    "displayName": "Rin",
                    "channelId": "channel-rin",
                    "isChatSponsor": True,
                },
                "raw_youtube_payload": {
                    "access_token": "must not leak",
                    "authorization": "Bearer secret-value",
                },
            },
        },
    )
    events_response = client.get("/v2/sessions/session-youtube-boundary/events?limit=20")
    with client.stream(
        "GET",
        "/v2/sessions/session-youtube-boundary/operator-stream",
    ) as operator_stream:
        operator_stream.read()
        operator_text = operator_stream.text
    with client.stream(
        "GET",
        "/v2/sessions/session-youtube-boundary/display-stream",
    ) as display_stream:
        display_stream.read()
        display_text = display_stream.text

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    events = events_response.json()["events"]
    super_chat_event = next(
        event for event in events if event["event_id"] == "sc-boundary-1"
    )
    assert super_chat_event["event_type"] == "youtube_super_chat"
    public_payload = super_chat_event["public_payload"]["public_payload"]
    assert public_payload["author_display_name"] == "Rin"
    assert public_payload["super_chat"]["amount_display_string"] == "NT$150"
    assert public_payload["super_chat"]["acknowledgement_status"] == "pending"
    assert len(planned_show.calls) == 1
    _assert_no_private_payload(response.json())
    _assert_no_private_payload(events_response.json())
    _assert_no_private_payload(operator_text)
    _assert_no_private_payload(display_text)


def test_api_ingestion_uses_persisted_cursor_to_skip_duplicate_event_id():
    client, _storage, planned_show = _client()
    _create_session(client, "session-youtube-duplicate")
    raw_event = {
        "id": "yt-duplicate-1",
        "snippet": {
            "type": "textMessageEvent",
            "publishedAt": "2026-05-12T08:21:00Z",
            "displayMessage": "First",
            "textMessageDetails": {"messageText": "First"},
        },
        "authorDetails": {"displayName": "Mika", "channelId": "channel-mika"},
    }

    first = client.post(
        "/v2/sessions/session-youtube-duplicate/youtube-events",
        json={
            "command_id": "cmd-youtube-first",
            "youtube_event": raw_event,
            "polling_cursor": {
                "live_chat_id": "live-chat-1",
                "next_page_token": "page-1",
                "polling_interval_millis": 1500,
                "seen_event_ids": [],
            },
            "page_info": {
                "next_page_token": "page-2",
                "polling_interval_millis": 2500,
            },
        },
    )
    duplicate = client.post(
        "/v2/sessions/session-youtube-duplicate/youtube-events",
        json={
            "command_id": "cmd-youtube-duplicate",
            "youtube_event": raw_event,
        },
    )
    events = client.get("/v2/sessions/session-youtube-duplicate/events?limit=50").json()[
        "events"
    ]

    assert first.status_code == 200
    assert duplicate.status_code == 200
    assert duplicate.json()["events"][0]["event_type"] == "youtube_event_ignored"
    assert duplicate.json()["events"][0]["payload"] == {
        "youtube_event": "duplicate",
        "event_id": "yt-duplicate-1",
    }
    assert len(planned_show.calls) == 1
    assert any(
        event["event_type"] == "youtube_text_message"
        and event["public_payload"]["should_dispatch"] is False
        for event in events
    )
    _assert_no_private_payload(first.json())
    _assert_no_private_payload(duplicate.json())
    _assert_no_private_payload(events)


def _python_files_under_v2() -> list[Path]:
    return sorted((ROOT / "YouTubeBridgeV2").rglob("*.py"))


def _imported_modules(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.append(node.module or "")
    return modules


def test_youtube_ingestion_v2_source_has_no_external_transport_or_legacy_imports():
    forbidden_prefixes = (
        "YouTubeBridge",
        "googleapiclient",
        "google.oauth",
        "requests",
        "sqlite3",
        "aiosqlite",
    )
    violations: list[tuple[str, str]] = []

    for path in _python_files_under_v2():
        for module in _imported_modules(path):
            if any(
                module == forbidden or module.startswith(f"{forbidden}.")
                for forbidden in forbidden_prefixes
            ):
                violations.append((str(path.relative_to(ROOT)), module))

    assert violations == []


def test_youtube_ingestion_route_does_not_import_adapters_or_storage():
    route_path = ROOT / "YouTubeBridgeV2" / "server" / "routes.py"
    violations = [
        module
        for module in _imported_modules(route_path)
        if module.startswith("YouTubeBridgeV2.adapters")
        or module.startswith("YouTubeBridgeV2.storage")
        or module in {"sqlite3", "aiosqlite"}
    ]

    assert violations == []
