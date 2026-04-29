"""
天氣快取模組 — 每日自動快取指定城市的天氣預報，供對話注入使用。

快取儲存於 weather_cache.json，新格式：
{
  "version": 2,
  "cities": {
    "Taipei": {
      "date": "2026-03-26",
      "city": "Taipei",
      "country": "TW",
      "fetched_at": "2026-03-26T08:12:00",
      "slots": [
        {"time": "2026-03-26 09:00", "weather": "多雲", "temp": 22.5, "humidity": 75, "wind": 3.2, "pop": 10}
      ]
    }
  }
}
"""
import json
import os
import requests
from datetime import datetime, timedelta
from core.system_logger import SystemLogger


class WeatherCache:
    def __init__(self, cache_file="weather_cache.json"):
        self._cache_file = cache_file

    # ── 公開方法 ──────────────────────────────────────────

    def ensure_today(self, city: str, api_key: str) -> bool:
        """
        確保今天 + 此城市的快取已存在。
        若快取有效則直接回傳 True，否則呼叫 API 抓取並寫入。
        """
        city = (city or "").strip()
        if not city:
            return False

        today_str = datetime.now().strftime("%Y-%m-%d")
        cache = self._load_cache() or self._empty_cache()
        entry = self.get_cache(city, cache=cache)
        if entry and entry.get("date") == today_str:
            SystemLogger.log_system_event("WeatherCache", f"天氣快取命中：{city} ({today_str})，跳過 API 呼叫。")
            return True

        SystemLogger.log_system_event("WeatherCache", f"天氣快取未命中或已過期，正在為 {city} 抓取今日天氣...")
        try:
            slots, country = self._fetch_today_forecast(city, api_key)
            if not slots:
                SystemLogger.log_error("WeatherCache", f"API 回傳空結果，城市: {city}")
                return False

            self.update_cache(city, country, slots, cache=cache, date=today_str)
            SystemLogger.log_system_event("WeatherCache", f"天氣快取已更新：{city} ({country})，共 {len(slots)} 筆時段。")
            return True

        except Exception as e:
            SystemLogger.log_error("WeatherCache", f"抓取天氣失敗: {e}")
            return False

    def get_cache(self, city: str | None = None, cache: dict | None = None) -> dict | None:
        """取得指定城市快取；未指定城市時使用設定中的 weather_city。"""
        cache = cache or self._load_cache()
        if not cache:
            return None

        target_city = (city or self._default_city()).strip()
        cities = cache.get("cities", {})
        if not target_city and len(cities) == 1:
            return next(iter(cities.values()))
        if not target_city:
            return None

        for key, entry in cities.items():
            if key.lower() == target_city.lower() or str(entry.get("city", "")).lower() == target_city.lower():
                return entry
        return None

    def update_cache(
        self,
        city: str,
        country: str,
        slots: list[dict],
        cache: dict | None = None,
        date: str | None = None,
    ) -> dict:
        """更新指定城市快取並寫回檔案。"""
        cache = cache or self._load_cache() or self._empty_cache()
        city = (city or "").strip()
        date = date or datetime.now().strftime("%Y-%m-%d")
        key = self._city_key(cache, city)
        cache.setdefault("cities", {})[key] = {
            "date": date,
            "city": city,
            "country": country,
            "fetched_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "slots": slots,
        }
        self._save_cache(cache)
        return cache["cities"][key]

    def get_current_slot(self, city: str | None = None) -> str | None:
        """回傳最接近目前時間的天氣摘要字串，快取無效時回傳 None。"""
        cache = self._load_cache()
        entry = self.get_cache(city, cache=cache)
        if not entry:
            return None

        today_str = datetime.now().strftime("%Y-%m-%d")
        if entry.get("date") != today_str:
            return None

        slots = entry.get("slots", [])
        if not slots:
            return None

        now = datetime.now()
        best = min(slots, key=lambda s: abs((datetime.strptime(s["time"], "%Y-%m-%d %H:%M") - now).total_seconds()))

        city_name = entry.get("city", "")
        country = entry.get("country", "")
        return (
            f"{city_name} ({country}) {best['time']} 天氣：{best['weather']}，"
            f"{best['temp']}°C，濕度 {best['humidity']}%，"
            f"風速 {best['wind']} m/s，降雨機率 {best['pop']}%"
        )

    def get_full_today(self, city: str | None = None) -> list[dict] | None:
        """回傳今天所有時段的快取資料，快取無效時回傳 None。"""
        cache = self._load_cache()
        entry = self.get_cache(city, cache=cache)
        if not entry:
            return None

        today_str = datetime.now().strftime("%Y-%m-%d")
        if entry.get("date") != today_str:
            return None

        return entry.get("slots", [])

    # ── 內部方法 ──────────────────────────────────────────

    def _fetch_today_forecast(self, city: str, api_key: str) -> tuple[list[dict], str]:
        """
        呼叫 OpenWeather forecast API（限制 8 筆 = 當日份量），
        篩選出本地日期為今天的所有時段。
        回傳 (slots_list, country_code)。
        """
        url = "https://api.openweathermap.org/data/2.5/forecast"
        params = {
            "q": city,
            "appid": api_key,
            "units": "metric",
            "lang": "zh_tw",
            "cnt": 8,
        }
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        country = data.get("city", {}).get("country", "")
        today_str = datetime.now().strftime("%Y-%m-%d")
        tomorrow_str = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

        slots = []
        for item in data.get("list", []):
            # 用 Unix timestamp 轉本地時間，避免 UTC 跨日問題。
            dt_local = datetime.fromtimestamp(item["dt"])
            # 若今日 slots 為空（深夜/跨日邊界），一併列入明日的預報。
            if dt_local.strftime("%Y-%m-%d") not in (today_str, tomorrow_str):
                continue

            slots.append({
                "time": dt_local.strftime("%Y-%m-%d %H:%M"),
                "weather": item.get("weather", [{}])[0].get("description", ""),
                "temp": item.get("main", {}).get("temp"),
                "humidity": item.get("main", {}).get("humidity"),
                "wind": item.get("wind", {}).get("speed"),
                "pop": int(item.get("pop", 0) * 100),
            })

        return slots, country

    def _load_cache(self) -> dict | None:
        if not os.path.exists(self._cache_file):
            return None
        try:
            with open(self._cache_file, "r", encoding="utf-8") as f:
                return self._normalize_cache(json.load(f))
        except Exception:
            return None

    def _save_cache(self, data: dict):
        with open(self._cache_file, "w", encoding="utf-8") as f:
            json.dump(self._normalize_cache(data), f, ensure_ascii=False, indent=2)

    def _normalize_cache(self, data) -> dict:
        """讀取舊版單城市格式或舊陣列格式時轉成 v2 結構。"""
        if isinstance(data, dict) and isinstance(data.get("cities"), dict):
            data.setdefault("version", 2)
            return data

        if isinstance(data, dict) and isinstance(data.get("slots"), list):
            city = data.get("city") or self._default_city() or "default"
            return {
                "version": 2,
                "cities": {city: data},
            }

        if isinstance(data, list):
            city = self._default_city() or "default"
            return {
                "version": 2,
                "cities": {
                    city: {
                        "date": datetime.now().strftime("%Y-%m-%d"),
                        "city": city,
                        "country": "",
                        "fetched_at": "",
                        "slots": data,
                    }
                },
            }

        return self._empty_cache()

    def _empty_cache(self) -> dict:
        return {"version": 2, "cities": {}}

    def _city_key(self, cache: dict, city: str) -> str:
        for key in cache.get("cities", {}):
            if key.lower() == city.lower():
                return key
        return city

    def _default_city(self) -> str:
        try:
            from core.storage_manager import StorageManager
            return (StorageManager().load_prefs().get("weather_city") or "").strip()
        except Exception:
            return ""
