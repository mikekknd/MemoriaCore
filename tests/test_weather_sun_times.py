import json
from datetime import datetime

from tools import weather
from tools.weather_cache import WeatherCache


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _forecast_item(ts):
    return {
        "dt": ts,
        "weather": [{"description": "晴"}],
        "main": {"temp": 28.0, "humidity": 65},
        "wind": {"speed": 2.5},
        "pop": 0.2,
    }


def test_current_weather_summary_includes_local_sunrise_and_sunset(monkeypatch):
    def fake_get(_url, params, timeout):
        assert timeout == 10
        return FakeResponse({
            "name": "Taichung",
            "timezone": 28800,
            "sys": {"country": "TW", "sunrise": 0, "sunset": 3600},
            "weather": [{"description": "晴"}],
            "main": {"temp": 30.0, "feels_like": 32.0, "humidity": 70},
            "wind": {"speed": 3.0},
        })

    monkeypatch.setattr(weather.requests, "get", fake_get)

    payload = json.loads(weather._fetch_current("Taichung", "key"))

    assert "日出 08:00" in payload["weather_data"]
    assert "日落 09:00" in payload["weather_data"]


def test_forecast_summary_includes_local_sunrise_and_sunset(monkeypatch):
    def fake_get(_url, params, timeout):
        assert timeout == 10
        return FakeResponse({
            "city": {
                "name": "Taichung",
                "country": "TW",
                "timezone": 28800,
                "sunrise": 0,
                "sunset": 3600,
            },
            "list": [_forecast_item(0)],
        })

    monkeypatch.setattr(weather.requests, "get", fake_get)

    payload = json.loads(weather._fetch_forecast("Taichung", "key"))

    assert "日出 08:00" in payload["weather_forecast"]
    assert "日落 09:00" in payload["weather_forecast"]


def test_weather_cache_summary_includes_local_sunrise_and_sunset(tmp_path, monkeypatch):
    wc = WeatherCache(str(tmp_path / "weather_cache.json"))
    now_ts = int(datetime.now().timestamp())

    def fake_get(_url, params, timeout):
        assert timeout == 15
        return FakeResponse({
            "city": {
                "country": "TW",
                "timezone": 28800,
                "sunrise": 0,
                "sunset": 3600,
            },
            "list": [_forecast_item(now_ts)],
        })

    monkeypatch.setattr("tools.weather_cache.requests.get", fake_get)

    assert wc.ensure_today("Taichung", "key") is True
    summary = wc.get_current_slot("Taichung")

    assert summary is not None
    assert "日出 08:00" in summary
    assert "日落 09:00" in summary
