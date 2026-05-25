from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping

import pytest

from YouTubeBridgeV2.adapters.memoria import (
    MemoriaAdapterError,
    build_memoria_request,
    normalize_memoria_response,
)
from YouTubeBridgeV2.adapters.memoria_http import (
    MemoriaHttpTransportConfig,
    MemoriaSyncHttpTransport,
)
from YouTubeBridgeV2.live_episode_plan.runner import PlannedTurnIntent


_TRUE_VALUES = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class MemoriaRealIntegrationSettings:
    enabled: bool
    base_url: str | None
    api_key: str | None = field(default=None, repr=False)
    character_id: str | None = None
    user_id: str = "__youtube_live_integration__"
    session_id: str = "yb2-integration-session"
    timeout_seconds: float = 10.0
    max_attempts: int = 1

    def transport_config(self) -> MemoriaHttpTransportConfig:
        if self.base_url is None:
            raise ValueError("YB2_MEMORIA_BASE_URL is required")
        return MemoriaHttpTransportConfig(
            base_url=self.base_url,
            api_key=self.api_key,
            timeout_seconds=self.timeout_seconds,
            max_attempts=self.max_attempts,
        )


def _settings_from_env(env: Mapping[str, str]) -> MemoriaRealIntegrationSettings:
    return MemoriaRealIntegrationSettings(
        enabled=_enabled(env.get("YB2_MEMORIA_INTEGRATION")),
        base_url=_optional_env(env.get("YB2_MEMORIA_BASE_URL")),
        api_key=_optional_env(env.get("YB2_MEMORIA_API_KEY")),
        character_id=_optional_env(env.get("YB2_MEMORIA_CHARACTER_ID")),
        user_id=_optional_env(env.get("YB2_MEMORIA_USER_ID"))
        or "__youtube_live_integration__",
        session_id=_optional_env(env.get("YB2_MEMORIA_SESSION_ID"))
        or "yb2-integration-session",
        timeout_seconds=_float_env(env.get("YB2_MEMORIA_TIMEOUT_SECONDS"), 10.0),
        max_attempts=_int_env(env.get("YB2_MEMORIA_MAX_ATTEMPTS"), 1),
    )


def _require_enabled_settings(
    settings: MemoriaRealIntegrationSettings,
) -> MemoriaRealIntegrationSettings:
    if not settings.enabled:
        pytest.skip("set YB2_MEMORIA_INTEGRATION=1 to run real MemoriaCore integration")
    if settings.base_url is None:
        pytest.skip("set YB2_MEMORIA_BASE_URL to run real MemoriaCore integration")
    if settings.character_id is None:
        pytest.skip("set YB2_MEMORIA_CHARACTER_ID to run real MemoriaCore integration")
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


def _integration_planned_turn(character_id: str) -> PlannedTurnIntent:
    return PlannedTurnIntent(
        plan_id="yb2-real-memoria-integration",
        turn_id="real-memoria-smoke",
        turn_index=0,
        purpose="Reply briefly to confirm the YouTubeBridgeV2 MemoriaCore transport works.",
        speaker_policy="fixed",
        speaker_ids=(character_id,),
        topic_cue="MemoriaCore integration harness smoke test.",
        audience_summary=None,
        audience_handling_hint="no_audience_event",
        metadata={"test_scope": "youtubebridge_v2_memoria_integration"},
    )


def _integration_context(settings: MemoriaRealIntegrationSettings) -> dict[str, object]:
    return {
        "v2_session_id": "yb2-real-memoria-integration",
        "memoria_session_id": settings.session_id,
        "user_id": settings.user_id,
        "character_id": settings.character_id,
        "correlation_id": "yb2-real-memoria-correlation",
        "request_id": "yb2-real-memoria-request",
    }


def _assert_no_secret_payload(value: object) -> None:
    text = repr(value).lower()
    for forbidden in (
        "secret-token",
        "authorization",
        "bearer",
        "raw_payload",
        "access_token",
        "hidden_prompt",
    ):
        assert forbidden not in text


def test_memoria_real_integration_settings_default_is_disabled():
    settings = _settings_from_env({})

    assert settings.enabled is False
    assert settings.base_url is None
    assert settings.character_id is None
    assert settings.user_id == "__youtube_live_integration__"
    assert settings.session_id == "yb2-integration-session"
    assert settings.timeout_seconds == 10.0
    assert settings.max_attempts == 1


def test_memoria_real_integration_settings_parse_explicit_env_without_secret_repr():
    settings = _settings_from_env(
        {
            "YB2_MEMORIA_INTEGRATION": "1",
            "YB2_MEMORIA_BASE_URL": "http://127.0.0.1:8088",
            "YB2_MEMORIA_API_KEY": "secret-token",
            "YB2_MEMORIA_CHARACTER_ID": "host-character",
            "YB2_MEMORIA_USER_ID": "integration-user",
            "YB2_MEMORIA_SESSION_ID": "integration-session",
            "YB2_MEMORIA_TIMEOUT_SECONDS": "3.5",
            "YB2_MEMORIA_MAX_ATTEMPTS": "2",
        }
    )

    assert settings.enabled is True
    assert settings.base_url == "http://127.0.0.1:8088"
    assert settings.api_key == "secret-token"
    assert settings.character_id == "host-character"
    assert settings.user_id == "integration-user"
    assert settings.session_id == "integration-session"
    assert settings.timeout_seconds == 3.5
    assert settings.max_attempts == 2
    assert "secret-token" not in repr(settings.transport_config())
    assert settings.transport_config().public_summary() == {
        "base_url": "http://127.0.0.1:8088",
        "timeout_seconds": 3.5,
        "max_attempts": 2,
        "has_api_key": True,
    }


def test_memoria_real_integration_requires_opt_in_before_external_call():
    settings = _settings_from_env({})

    with pytest.raises(pytest.skip.Exception, match="YB2_MEMORIA_INTEGRATION=1"):
        _require_enabled_settings(settings)


def test_memoria_real_integration_requires_base_url_and_character_id():
    settings = _settings_from_env({"YB2_MEMORIA_INTEGRATION": "1"})

    with pytest.raises(pytest.skip.Exception, match="YB2_MEMORIA_BASE_URL"):
        _require_enabled_settings(settings)

    settings = _settings_from_env(
        {
            "YB2_MEMORIA_INTEGRATION": "1",
            "YB2_MEMORIA_BASE_URL": "http://127.0.0.1:8088",
        }
    )

    with pytest.raises(pytest.skip.Exception, match="YB2_MEMORIA_CHARACTER_ID"):
        _require_enabled_settings(settings)


@pytest.mark.memoria_integration
def test_real_memoria_sync_transport_round_trips_planned_turn():
    settings = _require_enabled_settings(_settings_from_env(os.environ))
    request = build_memoria_request(
        _integration_planned_turn(settings.character_id or ""),
        _integration_context(settings),
    )
    transport = MemoriaSyncHttpTransport(settings.transport_config())

    response_payload = transport.send(request)
    normalized = normalize_memoria_response(response_payload, request.correlation)

    assert not isinstance(normalized, MemoriaAdapterError)
    assert normalized.messages
    assert normalized.public_summary["message_count"] >= 1
    assert normalized.correlation.correlation_id == "yb2-real-memoria-correlation"
    _assert_no_secret_payload(request.public_summary)
    _assert_no_secret_payload(normalized.public_summary)
