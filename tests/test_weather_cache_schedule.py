from datetime import datetime

import pytest

from api import main as api_main
from core import deployment_config
from tools import weather_cache


def _slot(temp):
    return {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "weather": "多雲",
        "temp": temp,
        "humidity": 70,
        "wind": 1.5,
        "pop": 20,
    }


def test_weather_cache_refresh_schedule_uses_fixed_eight_hour_slots():
    next_refresh = getattr(api_main, "_next_weather_cache_refresh_at", None)
    assert next_refresh is not None, "weather cache must expose a testable fixed-slot scheduler"

    assert next_refresh(datetime(2026, 5, 26, 7, 59, 59)) == datetime(2026, 5, 26, 8, 0, 0)
    assert next_refresh(datetime(2026, 5, 26, 8, 0, 0)) == datetime(2026, 5, 26, 16, 0, 0)
    assert next_refresh(datetime(2026, 5, 26, 16, 0, 1)) == datetime(2026, 5, 27, 0, 0, 0)


def test_weather_cache_force_refresh_fetches_even_when_today_cache_exists(tmp_path, monkeypatch):
    wc = weather_cache.WeatherCache(str(tmp_path / "weather_cache.json"))
    fetch_temps = iter([25.0, 26.0])
    fetch_calls = []

    def fake_fetch(city, api_key):
        fetch_calls.append((city, api_key))
        return [_slot(next(fetch_temps))], "TW"

    monkeypatch.setattr(wc, "_fetch_today_forecast", fake_fetch)

    assert wc.ensure_today("Taichung", "key") is True
    assert wc.ensure_today("Taichung", "key", force=True) is True

    assert fetch_calls == [("Taichung", "key"), ("Taichung", "key")]
    assert wc.get_full_today("Taichung")[0]["temp"] == 26.0


@pytest.mark.asyncio
async def test_scheduled_weather_refresh_uses_latest_su_weather_preferences(monkeypatch):
    refresh_once = getattr(api_main, "_refresh_su_weather_cache_once", None)
    assert refresh_once is not None, "scheduled refresh must reuse the current stored weather preferences"

    calls = []

    class FakeStorage:
        def load_prefs(self):
            return {
                "su_user_id": "su-1",
                "weather_city": "Taichung",
                "openweather_api_key": "openweather-key",
            }

    class FakeWeatherCache:
        def ensure_today(self, city, api_key, force=False):
            calls.append((city, api_key, force))
            return True

    monkeypatch.setattr(api_main, "get_storage", lambda: FakeStorage())
    monkeypatch.setattr(deployment_config, "get_su_user_id", lambda: "fallback-su")
    monkeypatch.setattr(weather_cache, "WeatherCache", FakeWeatherCache)

    assert await refresh_once() is True
    assert calls == [("Taichung", "openweather-key", False)]


@pytest.mark.asyncio
async def test_scheduled_weather_refresh_forces_fetch_at_fixed_slots(monkeypatch):
    refresh_once = getattr(api_main, "_refresh_su_weather_cache_once", None)
    assert refresh_once is not None, "scheduled refresh must be able to force a fresh API fetch"

    calls = []

    class FakeStorage:
        def load_prefs(self):
            return {
                "su_user_id": "su-1",
                "weather_city": "Taichung",
                "openweather_api_key": "openweather-key",
            }

    class FakeWeatherCache:
        def ensure_today(self, city, api_key, force=False):
            calls.append((city, api_key, force))
            return True

    monkeypatch.setattr(api_main, "get_storage", lambda: FakeStorage())
    monkeypatch.setattr(deployment_config, "get_su_user_id", lambda: "fallback-su")
    monkeypatch.setattr(weather_cache, "WeatherCache", FakeWeatherCache)

    assert await refresh_once(force=True) is True
    assert calls == [("Taichung", "openweather-key", True)]
