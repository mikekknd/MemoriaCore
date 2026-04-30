"""共用 Prompt 工具函式。

此模組存放跨模組共享的 prompt 組裝邏輯，避免在 coordinator / orchestration 重複定義。
"""
from datetime import datetime, timezone, timedelta

from core.prompt_manager import get_prompt_manager
from core.xml_prompt import xml_attr, xml_block


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
            return "\n" + xml_block("weather", weather_summary)

        api_key = (prefs.get("openweather_api_key") or "").strip()
        if api_key:
            wc.ensure_today(city, api_key)
            weather_summary = wc.get_current_slot(city)
            if weather_summary:
                return "\n" + xml_block("weather", weather_summary)
    except Exception:
        pass
    return ""


def _build_user_identity_block(session_ctx: dict | None) -> str:
    """注入目前真人使用者的顯示身份，供模型回答稱呼類問題。"""
    if not session_ctx:
        return ""
    user_name = str(session_ctx.get("user_name") or "").strip()
    user_id = str(session_ctx.get("user_id") or "").strip()
    telegram_user_id = str(session_ctx.get("telegram_user_id") or "").strip()
    discord_user_id = str(session_ctx.get("discord_user_id") or "").strip()
    if not any([user_name, user_id, telegram_user_id, discord_user_id]):
        return ""

    return get_prompt_manager().get("user_identity_block").format(
        user_name=xml_attr(user_name),
        user_id=xml_attr(user_id),
        telegram_user_id=xml_attr(telegram_user_id),
        discord_user_id=xml_attr(discord_user_id),
    )


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
    user_identity_block = _build_user_identity_block(session_ctx)

    env_block = pm.get("environment_context_block").format(
        current_time=current_time,
        weather_block=weather_block,
    )

    emo_block = ""
    current_character_id = str((session_ctx or {}).get("character_id") or "").strip()
    for msg in reversed(session_messages):
        if msg.get("role") == "assistant" and msg.get("persona_state"):
            msg_character_id = str(msg.get("character_id") or "").strip()
            if current_character_id and msg_character_id and msg_character_id != current_character_id:
                continue
            ps = msg["persona_state"]
            internal_thought = ps.get("internal_thought") or "—"
            emo_block = "\n" + pm.get("emotional_trajectory_block").format(
                internal_thought=internal_thought,
            )
            break

    return env_block + user_identity_block + emo_block + "\n\n"


def format_latest_user_message_for_llm(content: str, session_ctx: dict | None = None) -> str:
    """群組模式中明確標示最後一則訊息來自真人使用者。

    Chat API 的 role=user 對多數模型已足夠，但群組模式同時存在多個 AI speaker
    label 與 user-role 控制區塊時，部分模型會把「我」誤連到前一位 AI。這裡只
    包裝送進 LLM 的暫態內容，不改寫 DB 中的原始使用者訊息。
    """
    if not _is_group_prompt_context(session_ctx):
        return content

    ctx = session_ctx or {}
    return xml_block(
        "latest_user_message",
        content,
        attrs={
            "speaker": "human_user",
            "user_name": ctx.get("user_name") or "",
            "user_id": ctx.get("user_id") or "",
        },
    )


def _is_group_prompt_context(session_ctx: dict | None) -> bool:
    if not session_ctx:
        return False
    active_ids = session_ctx.get("active_character_ids") or []
    return session_ctx.get("session_mode") == "group" or len(active_ids) > 1
