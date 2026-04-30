"""Prompt 前綴組裝測試。"""

import core.deployment_config as deployment_config
import core.prompt_utils as prompt_utils
import tools.weather_cache as weather_cache


class _FakePromptManager:
    def get(self, key: str) -> str:
        return {
            "environment_context_block": "<environment_context>\nCurrent Time: {current_time}{weather_block}\n</environment_context>",
            "user_identity_block": (
                "<user_identity><display_name>{user_name}</display_name>"
                "<local_user_id>{user_id}</local_user_id>"
                "<telegram_user_id>{telegram_user_id}</telegram_user_id>"
                "<discord_user_id>{discord_user_id}</discord_user_id></user_identity>"
            ),
            "emotional_trajectory_block": "<emotional_trajectory>{internal_thought}</emotional_trajectory>",
        }.get(key, "")


def test_weather_prefix_skips_non_su(monkeypatch):
    monkeypatch.setattr(prompt_utils, "get_prompt_manager", lambda: _FakePromptManager())
    monkeypatch.setattr(deployment_config, "get_su_user_id", lambda: "su-1")

    class RaisingWeatherCache:
        def __init__(self):
            raise AssertionError("非 SU 不應讀取 WeatherCache")

    monkeypatch.setattr(weather_cache, "WeatherCache", RaisingWeatherCache)

    prefix = prompt_utils.build_user_prefix(
        [{"role": "user", "content": "你好"}],
        user_prefs={"weather_city": "Taipei", "openweather_api_key": "key"},
        session_ctx={"user_id": "user-2", "persona_face": "public"},
    )

    assert "Weather:" not in prefix


def test_weather_prefix_skips_su_public_face(monkeypatch):
    monkeypatch.setattr(prompt_utils, "get_prompt_manager", lambda: _FakePromptManager())
    monkeypatch.setattr(deployment_config, "get_su_user_id", lambda: "su-1")

    class RaisingWeatherCache:
        def __init__(self):
            raise AssertionError("SU public face 不應讀取 WeatherCache")

    monkeypatch.setattr(weather_cache, "WeatherCache", RaisingWeatherCache)

    prefix = prompt_utils.build_user_prefix(
        [{"role": "user", "content": "你好"}],
        user_prefs={"weather_city": "Taipei", "openweather_api_key": "key"},
        session_ctx={"user_id": "su-1", "persona_face": "public"},
    )

    assert "Weather:" not in prefix


def test_weather_prefix_uses_su_city_and_refreshes_on_miss(monkeypatch):
    monkeypatch.setattr(prompt_utils, "get_prompt_manager", lambda: _FakePromptManager())
    monkeypatch.setattr(deployment_config, "get_su_user_id", lambda: "su-1")

    calls = {"slots": [], "ensure": []}

    class FakeWeatherCache:
        refreshed = False

        def get_current_slot(self, city=None):
            calls["slots"].append(city)
            if self.refreshed:
                return f"{city} 天氣摘要"
            return None

        def ensure_today(self, city, api_key):
            calls["ensure"].append((city, api_key))
            self.refreshed = True
            return True

    monkeypatch.setattr(weather_cache, "WeatherCache", FakeWeatherCache)

    prefix = prompt_utils.build_user_prefix(
        [{"role": "user", "content": "你好"}],
        user_prefs={"weather_city": "Taipei", "openweather_api_key": "key"},
        session_ctx={"user_id": "su-1", "persona_face": "private"},
    )

    assert "<weather>" in prefix
    assert "Taipei 天氣摘要" in prefix
    assert calls["slots"] == ["Taipei", "Taipei"]
    assert calls["ensure"] == [("Taipei", "key")]


def test_weather_prefix_does_not_refresh_when_cache_hits(monkeypatch):
    monkeypatch.setattr(prompt_utils, "get_prompt_manager", lambda: _FakePromptManager())
    monkeypatch.setattr(deployment_config, "get_su_user_id", lambda: "su-1")

    calls = {"ensure": 0}

    class FakeWeatherCache:
        def get_current_slot(self, city=None):
            return f"{city} cached"

        def ensure_today(self, city, api_key):
            calls["ensure"] += 1
            return True

    monkeypatch.setattr(weather_cache, "WeatherCache", FakeWeatherCache)

    prefix = prompt_utils.build_user_prefix(
        [{"role": "user", "content": "你好"}],
        user_prefs={"weather_city": "Taipei", "openweather_api_key": "key"},
        session_ctx={"user_id": "su-1", "persona_face": "private"},
    )

    assert "<weather>" in prefix
    assert "Taipei cached" in prefix
    assert calls["ensure"] == 0


def test_emotional_trajectory_uses_same_character_only(monkeypatch):
    monkeypatch.setattr(prompt_utils, "get_prompt_manager", lambda: _FakePromptManager())

    prefix = prompt_utils.build_user_prefix(
        [
            {
                "role": "assistant",
                "content": "A 回覆",
                "character_id": "char-a",
                "persona_state": {"internal_thought": "A 的內在思考"},
            },
            {
                "role": "assistant",
                "content": "B 回覆",
                "character_id": "char-b",
                "persona_state": {"internal_thought": "B 的內在思考"},
            },
        ],
        session_ctx={"character_id": "char-a"},
    )

    assert "A 的內在思考" in prefix
    assert "B 的內在思考" not in prefix


def test_user_prefix_includes_display_name(monkeypatch):
    monkeypatch.setattr(prompt_utils, "get_prompt_manager", lambda: _FakePromptManager())

    prefix = prompt_utils.build_user_prefix(
        [{"role": "user", "content": "你知道我的名字嗎？"}],
        session_ctx={
            "user_id": "42",
            "user_name": "本機暱稱",
            "telegram_user_id": "123456",
            "discord_user_id": "987654",
        },
    )

    assert "<user_identity>" in prefix
    assert "<display_name>本機暱稱</display_name>" in prefix
    assert "<local_user_id>42</local_user_id>" in prefix
    assert "<telegram_user_id>123456</telegram_user_id>" in prefix
    assert "<discord_user_id>987654</discord_user_id>" in prefix


def test_emotional_trajectory_omits_when_group_character_has_no_prior_thought(monkeypatch):
    monkeypatch.setattr(prompt_utils, "get_prompt_manager", lambda: _FakePromptManager())

    prefix = prompt_utils.build_user_prefix(
        [
            {
                "role": "assistant",
                "content": "B 回覆",
                "character_id": "char-b",
                "persona_state": {"internal_thought": "B 的內在思考"},
            },
        ],
        session_ctx={"character_id": "char-a", "session_mode": "group"},
    )

    assert "<emotional_trajectory>" not in prefix


def test_latest_user_message_wraps_group_human_speaker():
    wrapped = prompt_utils.format_latest_user_message_for_llm(
        "嗚嗚，可可都無視我拉!",
        session_ctx={
            "session_mode": "group",
            "active_character_ids": ["char-a", "char-b"],
            "user_id": "user-1",
            "user_name": "mikekknd",
        },
    )

    assert '<latest_user_message speaker="human_user" user_name="mikekknd" user_id="user-1">' in wrapped
    assert "嗚嗚，可可都無視我拉!" in wrapped
    assert wrapped.strip().endswith("</latest_user_message>")


def test_latest_user_message_single_session_stays_plain():
    content = "嗚嗚，可可都無視我拉!"

    wrapped = prompt_utils.format_latest_user_message_for_llm(
        content,
        session_ctx={"session_mode": "single", "active_character_ids": ["char-a"]},
    )

    assert wrapped == content
