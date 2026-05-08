"""YouTube Live 專用角色 prompt overlay。

這裡只處理 MemoriaCore 收到的受信任 Bridge live scope。一般 dashboard /
REST 對話即使帶入同名 external_context，也不應套用直播覆寫。
"""
from __future__ import annotations

from typing import Any

from core.prompt_manager import get_prompt_manager


YOUTUBE_LIVE_USER_ID = "__youtube_live__"
YOUTUBE_LIVE_EXTERNAL_SOURCES = {"youtube_live", "youtube_live_director"}
SUPPORTED_MODES = {"replace", "append"}


def _clean_text(value: Any, *, limit: int = 6000) -> str:
    return str(value or "").replace("\r", "\n").strip()[:limit]


def _compact_text(value: Any, *, limit: int = 240) -> str:
    return " ".join(str(value or "").split())[:limit]


def _trusted_live_context(session_ctx: dict | None) -> dict | None:
    ctx = session_ctx or {}
    external = ctx.get("external_chat_context")
    if not isinstance(external, dict):
        return None
    if str(ctx.get("channel") or "").strip() != "youtube_live":
        return None
    if str(ctx.get("user_id") or "").strip() != YOUTUBE_LIVE_USER_ID:
        return None
    if str(ctx.get("persona_face") or "").strip() != "public":
        return None
    if str(external.get("source") or "").strip() not in YOUTUBE_LIVE_EXTERNAL_SOURCES:
        return None
    return external


def live_persona_override_for_character(session_ctx: dict | None, character_id: str) -> dict[str, Any] | None:
    """取得目前角色的 trusted live persona override。"""
    external = _trusted_live_context(session_ctx)
    if not external:
        return None
    overrides = external.get("character_prompt_overrides")
    if not isinstance(overrides, dict):
        return None
    raw = overrides.get(str(character_id or "").strip())
    if not isinstance(raw, dict):
        return None
    if raw.get("enabled") is False:
        return None
    system_prompt = _clean_text(raw.get("system_prompt"), limit=8000)
    if not system_prompt:
        return None
    addressing = raw.get("addressing") if isinstance(raw.get("addressing"), dict) else {}
    return {
        "mode": str(raw.get("mode") or "replace").strip() if str(raw.get("mode") or "replace").strip() in SUPPORTED_MODES else "replace",
        "system_prompt": system_prompt,
        "self_address": _compact_text(raw.get("self_address"), limit=120),
        "opening_intro": _clean_text(raw.get("opening_intro"), limit=1200),
        "reply_rules": _clean_text(raw.get("reply_rules"), limit=2000),
        "addressing": {
            _compact_text(key, limit=120): _compact_text(value, limit=120)
            for key, value in addressing.items()
            if _compact_text(key, limit=120) and _compact_text(value, limit=120)
        },
    }


def _addressing_text(addressing: dict[str, str]) -> str:
    if not addressing:
        return "（未設定）"
    return "\n".join(f"- {target_id}: {address}" for target_id, address in addressing.items())


def resolve_live_persona_prompt(
    *,
    character_id: str,
    base_prompt: str,
    base_reply_rules: str,
    session_ctx: dict | None,
) -> tuple[str, str]:
    """依 trusted live persona overlay 覆寫目前角色 prompt 與 reply rules。"""
    override = live_persona_override_for_character(session_ctx, character_id)
    if not override:
        return base_prompt, base_reply_rules

    pm = get_prompt_manager()
    reply_rules_block = ""
    if override["reply_rules"]:
        reply_rules_block = (
            "<live_reply_rules>\n"
            f"{override['reply_rules']}\n"
            "</live_reply_rules>\n\n"
        )
    overlay_block = pm.get("youtube_live_persona_override_block").format(
        mode=override["mode"],
        system_prompt=override["system_prompt"],
        self_address=override["self_address"] or "（未設定）",
        opening_intro=override["opening_intro"] or "（未設定）",
        addressing_text=_addressing_text(override["addressing"]),
        reply_rules=override["reply_rules"],
        reply_rules_block=reply_rules_block,
    )
    if override["mode"] == "append":
        next_prompt = f"{base_prompt}\n\n{overlay_block}".strip()
    else:
        next_prompt = overlay_block
    next_reply_rules = override["reply_rules"] or base_reply_rules
    return next_prompt, next_reply_rules


def live_persona_participant_note(
    session_ctx: dict | None,
    *,
    current_character_id: str,
    participant_id: str,
) -> str:
    """給群聊名單 / router 的直播稱呼補充。"""
    override = live_persona_override_for_character(session_ctx, current_character_id)
    if not override:
        return ""
    address = override.get("addressing", {}).get(str(participant_id or "").strip())
    if not address:
        return ""
    return f"直播稱呼：目前角色應稱呼此角色為「{address}」。"


def live_persona_self_address_clause(
    session_ctx: dict | None,
    *,
    current_character_id: str,
) -> str:
    """給 group rules 使用的直播固定自稱片段。"""
    override = live_persona_override_for_character(session_ctx, current_character_id)
    if not override:
        return ""
    self_address = str(override.get("self_address") or "").strip()
    if not self_address:
        return ""
    return f"；固定自稱：{self_address}"


def apply_live_persona_to_participants(
    participants: list[dict[str, Any]],
    session_ctx: dict | None,
) -> list[dict[str, Any]]:
    """讓 group router 也能看到直播 overlay 的角色摘要。"""
    ctx = session_ctx or {}
    output: list[dict[str, Any]] = []
    for character in participants:
        item = dict(character)
        cid = str(item.get("character_id") or item.get("id") or "").strip()
        override = live_persona_override_for_character(ctx | {"character_id": cid}, cid)
        if override:
            summary_parts = [
                _compact_text(override.get("system_prompt"), limit=180),
                _compact_text(override.get("opening_intro"), limit=120),
                _compact_text(override.get("reply_rules"), limit=120),
            ]
            summary = "；".join(part for part in summary_parts if part)
            if summary:
                item["character_summary"] = summary
        output.append(item)
    return output
