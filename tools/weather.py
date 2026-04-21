# 環境假設：Python 3.10+, Requests 庫, 已配置 StorageManager 與 SystemLogger
# 功能對齊：維持原有 OpenWeather API 呼叫邏輯，僅優化 WEATHER_SCHEMA 以提升 LLM 參數提取準確度。

import os
import requests
import json
from core.system_logger import SystemLogger

def _get_openweather_key():
    try:
        from core.storage_manager import StorageManager
        prefs = StorageManager().load_prefs()
        key = prefs.get("openweather_api_key")
        if key:
            return key
    except Exception:
        pass
    return os.environ.get("OPENWEATHER_API_KEY", "")

WEATHER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "【功能】查詢指定城市的即時天氣或未來天氣預報。回傳真實氣象數據。\n【觸發時機】當使用者詢問天氣、氣溫、降雨機率、穿搭建議等依賴真實環境數據的問題時，必須優先呼叫此工具，禁止改用 search_web 代替，絕對禁止憑空捏造。",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "目標城市名稱。必須轉換為標準英文城市名（例如：使用者說「台北」，此處填入 \"Taipei\"；「東京」填入 \"Tokyo\"）。"
                },
                "mode": {
                    "type": "string",
                    "enum": ["current", "forecast"],
                    "description": "查詢模式。預設使用 'current'（即時天氣）。僅當使用者明確詢問未來時間（如明天、週末、未來幾天）時，才使用 'forecast'（未來 5 天預報）。"
                }
            },
            "required": ["city"]
        }
    }
}

def get_weather(city: str, mode: str = "current") -> str:
    """
    呼叫 OpenWeather API 查詢天氣。
    mode: 'current' → Current Weather API
          'forecast' → 5 Day / 3 Hour Forecast API（摘要前 8 筆 = 未來 24 小時）
    """
    api_key = _get_openweather_key()
    if not api_key:
        SystemLogger.log_error("OpenWeather", "尚未設定 OPENWEATHER_API_KEY")
        return json.dumps({"error": "系統尚未設定 OpenWeather API Key，請前往設定介面填寫後再試。"}, ensure_ascii=False)

    try:
        if mode == "forecast":
            return _fetch_forecast(city, api_key)
        else:
            return _fetch_current(city, api_key)
    except Exception as e:
        SystemLogger.log_error("OpenWeather", f"天氣查詢過程中發生錯誤: {e}")
        return json.dumps({"error": f"天氣查詢過程中發生錯誤: {e}"}, ensure_ascii=False)

def _fetch_current(city: str, api_key: str) -> str:
    url = "https://api.openweathermap.org/data/2.5/weather"
    params = {
        "q": city,
        "appid": api_key,
        "units": "metric",
        "lang": "zh_tw",
    }
    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    d = resp.json()

    result = {
        "city": d.get("name", city),
        "country": d.get("sys", {}).get("country", ""),
        "weather": d.get("weather", [{}])[0].get("description", ""),
        "temp": d.get("main", {}).get("temp"),
        "feels_like": d.get("main", {}).get("feels_like"),
        "humidity": d.get("main", {}).get("humidity"),
        "wind_speed": d.get("wind", {}).get("speed"),
        "visibility_m": d.get("visibility"),
    }
    summary = (
        f"{result['city']} ({result['country']}) 即時天氣：{result['weather']}，"
        f"氣溫 {result['temp']}°C（體感 {result['feels_like']}°C），"
        f"濕度 {result['humidity']}%，風速 {result['wind_speed']} m/s"
    )
    if result["visibility_m"] is not None:
        summary += f"，能見度 {result['visibility_m']}m"

    return json.dumps({"weather_data": summary}, ensure_ascii=False)

def _fetch_forecast(city: str, api_key: str) -> str:
    url = "https://api.openweathermap.org/data/2.5/forecast"
    params = {
        "q": city,
        "appid": api_key,
        "units": "metric",
        "lang": "zh_tw",
        "cnt": 8,  # 未來 24 小時（每 3 小時一筆）
    }
    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    d = resp.json()

    city_name = d.get("city", {}).get("name", city)
    country = d.get("city", {}).get("country", "")
    entries = []
    for item in d.get("list", []):
        dt_txt = item.get("dt_txt", "")
        weather_desc = item.get("weather", [{}])[0].get("description", "")
        temp = item.get("main", {}).get("temp")
        humidity = item.get("main", {}).get("humidity")
        wind = item.get("wind", {}).get("speed")
        pop = item.get("pop", 0)  # 降雨機率 (0~1)
        entries.append(
            f"  {dt_txt}：{weather_desc}，{temp}°C，濕度 {humidity}%，風速 {wind} m/s，降雨機率 {int(pop * 100)}%"
        )

    summary = f"{city_name} ({country}) 未來 24 小時預報：\n" + "\n".join(entries)
    return json.dumps({"weather_forecast": summary}, ensure_ascii=False)