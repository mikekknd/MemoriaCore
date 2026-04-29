"""共用 Prompt 工具函式。

此模組存放跨模組共享的 prompt 組裝邏輯，避免在 coordinator / orchestration 重複定義。
"""
from datetime import datetime, timezone, timedelta

from core.prompt_manager import get_prompt_manager


def _is_su_private_weather_context(session_ctx: dict | None) -> bool:
    """判斷目前對話是否允許注入 SU 專用天氣快取。"""
    if not session_ctx:
        return False
    try:
        from core.deployment_config import get_su_user_id
        su_user_id = get_su_user_id()
    except Exception:
        su_user_id = ""

    user_id = str(session_ctx.get("user_id", "") or "")
    persona_face = session_ctx.get("persona_face", "public")
    return bool(su_user_id and user_id == str(su_user_id) and persona_face == "private")


def _build_su_weather_block(user_prefs: dict | None, session_ctx: dict | None) -> str:
    """只在 SU private face 對話中注入設定城市的天氣快取。"""
    if not _is_su_private_weather_context(session_ctx):
        return ""

    prefs = user_prefs or {}
    city = (prefs.get("weather_city") or "").strip()
    if not city:
        return ""

    try:
        from tools.weather_cache import WeatherCache
        wc = WeatherCache()
        weather_summary = wc.get_current_slot(city)
        if weather_summary:
            return f"\nWeather: {weather_summary}"

        api_key = (prefs.get("openweather_api_key") or "").strip()
        if api_key:
            wc.ensure_today(city, api_key)
            weather_summary = wc.get_current_slot(city)
            if weather_summary:
                return f"\nWeather: {weather_summary}"
    except Exception:
        pass
    return ""


def build_user_prefix(
    session_messages: list[dict],
    user_prefs: dict | None = None,
    session_ctx: dict | None = None,
) -> str:
    """組裝使用者訊息前綴：環境上下文（時間、天氣）+ 情緒軌跡（若有前輪紀錄）。
    結果為純文字，接在 api_messages 最後一則 user content 之前。
    放在 user message 前綴而非 system prompt，以保留 prefix cache。
    天氣快取只服務 SU private face，避免其他使用者城市污染 SU 的常駐 prompt cache。
    """
    pm = get_prompt_manager()
    current_time = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S CST")
    weather_block = _build_su_weather_block(user_prefs, session_ctx)

    env_block = pm.get("environment_context_block").format(
        current_time=current_time,
        weather_block=weather_block,
    )

    emo_block = ""
    for msg in reversed(session_messages):
        if msg.get("role") == "assistant" and msg.get("persona_state"):
            ps = msg["persona_state"]
            internal_thought = ps.get("internal_thought") or "—"
            emo_block = "\n" + pm.get("emotional_trajectory_block").format(
                internal_thought=internal_thought,
            )
            break

    return env_block + emo_block + "\n\n"
