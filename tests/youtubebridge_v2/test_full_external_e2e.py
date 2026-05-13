from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Mapping

import pytest
from fastapi.testclient import TestClient

from core.storage_manager import StorageManager
from YouTubeBridgeV2.adapters.memoria_http import (
    MemoriaHttpTransportConfig,
    MemoriaSyncHttpTransport,
)
from YouTubeBridgeV2.app import create_v2_app
from YouTubeBridgeV2.composition import create_v2_composition
from YouTubeBridgeV2.runtime.memoria_runners import MemoriaPlannedShowRunner


_TRUE_VALUES = {"1", "true", "yes", "on"}
STARTED_AT = datetime(2026, 5, 12, 8, 0, tzinfo=timezone.utc)


@dataclass(frozen=True)
class FullExternalE2ESettings:
    enabled: bool
    memoria_base_url: str | None
    memoria_api_key: str | None = field(default=None, repr=False)
    character_id: str | None = None
    session_id: str = "yb2-full-external-e2e"
    user_id: str = "__youtube_live_external_e2e__"
    timeout_seconds: float = 10.0
    max_attempts: int = 1

    def transport_config(self) -> MemoriaHttpTransportConfig:
        if self.memoria_base_url is None:
            raise ValueError("YB2_EXTERNAL_MEMORIA_BASE_URL is required")
        return MemoriaHttpTransportConfig(
            base_url=self.memoria_base_url,
            api_key=self.memoria_api_key,
            timeout_seconds=self.timeout_seconds,
            max_attempts=self.max_attempts,
        )


def _settings_from_env(env: Mapping[str, str]) -> FullExternalE2ESettings:
    return FullExternalE2ESettings(
        enabled=_enabled(env.get("YB2_FULL_EXTERNAL_E2E")),
        memoria_base_url=_optional_env(
            env.get("YB2_EXTERNAL_MEMORIA_BASE_URL")
            or env.get("YB2_MEMORIA_BASE_URL")
        ),
        memoria_api_key=_optional_env(
            env.get("YB2_EXTERNAL_MEMORIA_API_KEY") or env.get("YB2_MEMORIA_API_KEY")
        ),
        character_id=_optional_env(
            env.get("YB2_EXTERNAL_MEMORIA_CHARACTER_ID")
            or env.get("YB2_MEMORIA_CHARACTER_ID")
        ),
        session_id=_optional_env(env.get("YB2_FULL_EXTERNAL_SESSION_ID"))
        or "yb2-full-external-e2e",
        user_id=_optional_env(env.get("YB2_FULL_EXTERNAL_USER_ID"))
        or "__youtube_live_external_e2e__",
        timeout_seconds=_float_env(env.get("YB2_FULL_EXTERNAL_TIMEOUT_SECONDS"), 10.0),
        max_attempts=_int_env(env.get("YB2_FULL_EXTERNAL_MAX_ATTEMPTS"), 1),
    )


def _require_enabled_settings(settings: FullExternalE2ESettings) -> FullExternalE2ESettings:
    if not settings.enabled:
        pytest.skip("set YB2_FULL_EXTERNAL_E2E=1 to run full external V2 E2E")
    if settings.memoria_base_url is None:
        pytest.skip("set YB2_EXTERNAL_MEMORIA_BASE_URL or YB2_MEMORIA_BASE_URL")
    if settings.character_id is None:
        pytest.skip("set YB2_EXTERNAL_MEMORIA_CHARACTER_ID or YB2_MEMORIA_CHARACTER_ID")
    return settings


def _enabled(value: str | None) -> bool:
    return str(value or "").strip().lower() in _TRUE_VALUES


def _optional_env(value: str | None) -> str | None:
    text = str(value or "").strip()
    return text or None


def _float_env(value: str | None, default: float) -> float:
    if _optional_env(value) is None:
        return default
    return float(str(value))


def _int_env(value: str | None, default: int) -> int:
    if _optional_env(value) is None:
        return default
    return int(str(value))


def _storage_manager(tmp_path):
    return StorageManager(
        prefs_file=str(tmp_path / "prefs.json"),
        history_file=str(tmp_path / "history.json"),
        persona_snapshot_db_path=str(tmp_path / "persona_snapshots.db"),
        youtube_bridge_v2_db_path=str(tmp_path / "youtubebridge_v2.db"),
    )


def _create_session_payload(settings: FullExternalE2ESettings) -> dict[str, object]:
    return {
        "command_id": f"{settings.session_id}-create",
        "session_id": settings.session_id,
        "aftertalk_policy": "auto",
        "metadata": {
            "duration_policy": {
                "planned_duration_seconds": 3600,
                "auto_finalize_on_duration": True,
                "aftertalk_requires_remaining_time": True,
            },
            "tts_policy": {
                "enabled": True,
                "provider": "external-e2e",
                "default_voice_id": "external-e2e-fallback",
            },
            "hidden_prompt": "must not leak",
        },
    }


def _plan_payload(settings: FullExternalE2ESettings) -> dict[str, object]:
    return {
        "command_id": f"{settings.session_id}-bind",
        "plan": {
            "plan_id": "plan-full-external-e2e",
            "title": "Full External E2E",
            "raw_topic_pack": "must not leak",
            "turns": [
                {
                    "id": "external-smoke",
                    "purpose": (
                        "Reply briefly to confirm YouTubeBridgeV2 full external "
                        "MemoriaCore transport works."
                    ),
                    "topic_cue": "Full external E2E smoke test.",
                    "speaker_policy": {
                        "type": "fixed",
                        "speaker_ids": [settings.character_id],
                    },
                    "audience_insertion": {
                        "enabled": False,
                        "allow_super_chats": False,
                    },
                    "metadata": {"test_scope": "full_external_e2e"},
                }
            ],
        },
    }


def _sse_payloads(text: str) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    for line in text.splitlines():
        if line.startswith("data: "):
            payloads.append(json.loads(line.removeprefix("data: ")))
    return payloads


def _assert_no_secret_payload(value: object) -> None:
    text = repr(value).lower()
    for forbidden in (
        "secret-token",
        "authorization",
        "bearer",
        "raw_payload",
        "access_token",
        "hidden_prompt",
        "raw_topic_pack",
    ):
        assert forbidden not in text


def test_full_external_e2e_settings_default_is_disabled():
    settings = _settings_from_env({})

    assert settings.enabled is False
    assert settings.memoria_base_url is None
    assert settings.character_id is None
    assert settings.session_id == "yb2-full-external-e2e"
    assert settings.user_id == "__youtube_live_external_e2e__"
    assert settings.timeout_seconds == 10.0
    assert settings.max_attempts == 1


def test_full_external_e2e_settings_parse_env_without_secret_repr():
    settings = _settings_from_env(
        {
            "YB2_FULL_EXTERNAL_E2E": "1",
            "YB2_EXTERNAL_MEMORIA_BASE_URL": "http://127.0.0.1:8088",
            "YB2_EXTERNAL_MEMORIA_API_KEY": "secret-token",
            "YB2_EXTERNAL_MEMORIA_CHARACTER_ID": "host-character",
            "YB2_FULL_EXTERNAL_SESSION_ID": "external-session",
            "YB2_FULL_EXTERNAL_USER_ID": "external-user",
            "YB2_FULL_EXTERNAL_TIMEOUT_SECONDS": "3.5",
            "YB2_FULL_EXTERNAL_MAX_ATTEMPTS": "2",
        }
    )

    assert settings.enabled is True
    assert settings.memoria_base_url == "http://127.0.0.1:8088"
    assert settings.memoria_api_key == "secret-token"
    assert settings.character_id == "host-character"
    assert settings.session_id == "external-session"
    assert settings.user_id == "external-user"
    assert settings.timeout_seconds == 3.5
    assert settings.max_attempts == 2
    assert "secret-token" not in repr(settings)
    assert "secret-token" not in repr(settings.transport_config())


def test_full_external_e2e_requires_explicit_opt_in():
    settings = _settings_from_env({})

    with pytest.raises(pytest.skip.Exception, match="YB2_FULL_EXTERNAL_E2E=1"):
        _require_enabled_settings(settings)


def test_full_external_e2e_requires_memoria_endpoint_and_character():
    settings = _settings_from_env({"YB2_FULL_EXTERNAL_E2E": "1"})

    with pytest.raises(pytest.skip.Exception, match="MEMORIA_BASE_URL"):
        _require_enabled_settings(settings)

    settings = _settings_from_env(
        {
            "YB2_FULL_EXTERNAL_E2E": "1",
            "YB2_EXTERNAL_MEMORIA_BASE_URL": "http://127.0.0.1:8088",
        }
    )
    with pytest.raises(pytest.skip.Exception, match="MEMORIA_CHARACTER_ID"):
        _require_enabled_settings(settings)


@pytest.mark.memoria_integration
def test_full_external_v2_memoria_display_tts_round_trip(tmp_path):
    settings = _require_enabled_settings(_settings_from_env(os.environ))
    storage = _storage_manager(tmp_path)
    transport = MemoriaSyncHttpTransport(settings.transport_config())
    composition = create_v2_composition(
        storage_manager=storage,
        planned_show_runner=MemoriaPlannedShowRunner(storage, transport),
    )
    client = TestClient(create_v2_app(composition, now_provider=lambda: STARTED_AT))

    create_response = client.post("/v2/sessions", json=_create_session_payload(settings))
    bind_response = client.post(
        f"/v2/sessions/{settings.session_id}/plan",
        json=_plan_payload(settings),
    )
    tick_response = client.post(
        f"/v2/sessions/{settings.session_id}/tick",
        json={"command_id": f"{settings.session_id}-tick"},
    )
    events_response = client.get(f"/v2/sessions/{settings.session_id}/events?limit=50")
    queue_response = client.get(f"/v2/sessions/{settings.session_id}/tts-queue")
    with client.stream("GET", f"/v2/sessions/{settings.session_id}/display-stream") as stream:
        stream.read()
        display_events = _sse_payloads(stream.text)

    assert create_response.status_code == 200
    assert bind_response.status_code == 200
    assert tick_response.status_code == 200
    assert tick_response.json()["dispatch"]["status"] == "ok"
    assert events_response.status_code == 200
    assert queue_response.status_code == 200

    character_events = [
        event for event in display_events if event.get("event_type") == "character_response"
    ]
    assert character_events
    response_text = character_events[0]["public_payload"]["response_text"]
    assert isinstance(response_text, str)
    assert response_text.strip()
    queued = queue_response.json()["tts_queue"]
    assert queued
    assert queued[0]["text"] == response_text
    assert queued[0]["status"] == "pending"

    delivery_id = queued[0]["delivery_id"]
    phase_before_ack = client.get(f"/v2/sessions/{settings.session_id}/phase").json()["phase"]
    ack_response = client.post(
        f"/v2/sessions/{settings.session_id}/tts-deliveries/{delivery_id}/ack",
        json={"command_id": f"{settings.session_id}-ack"},
    )
    timeout_response = client.post(
        f"/v2/sessions/{settings.session_id}/tts-deliveries/{delivery_id}/timeout",
        json={"command_id": f"{settings.session_id}-timeout", "timeout_seconds": 30},
    )
    phase_after_timeout = client.get(f"/v2/sessions/{settings.session_id}/phase").json()["phase"]

    assert ack_response.status_code == 200
    assert ack_response.json()["status"] == "delivered"
    assert ack_response.json()["phase_transition_requested"] is False
    assert timeout_response.status_code == 200
    assert timeout_response.json()["phase_transition_requested"] is False
    assert phase_after_timeout == phase_before_ack
    _assert_no_secret_payload(
        (
            create_response.json(),
            bind_response.json(),
            tick_response.json(),
            events_response.json(),
            display_events,
            queue_response.json(),
            ack_response.json(),
            timeout_response.json(),
        )
    )
