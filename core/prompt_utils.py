"""共用 Prompt 工具函式。

此模組存放跨模組共享的 prompt 組裝邏輯，避免在 coordinator / orchestration 重複定義。
"""
from datetime import datetime, timezone, timedelta

from core.prompt_manager import get_prompt_manager


def build_user_prefix(session_messages: list[dict]) -> str:
    """組裝使用者訊息前綴：環境上下文（時間、天氣）+ 情緒軌跡（若有前輪紀錄）。
    結果為純文字，接在 api_messages 最後一則 user content 之前。
    放在 user message 前綴而非 system prompt，以保留 prefix cache。
    """
    pm = get_prompt_manager()
    current_time = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S CST")

    weather_block = ""
    try:
        from tools.weather_cache import WeatherCache
        weather_summary = WeatherCache().get_current_slot()
        if weather_summary:
            weather_block = f"\nWeather: {weather_summary}"
    except Exception:
        pass

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
