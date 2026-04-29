"""對話歷史格式化工具。"""


def format_history_for_llm(messages: list[dict]) -> list[dict]:
    """將 assistant 訊息加上 speaker 標籤，讓群組對話上下文可辨識發話角色。"""
    formatted: list[dict] = []
    for m in messages:
        role = m.get("role", "")
        content = m.get("content", "")
        if role == "assistant":
            label = _speaker_label(m)
            if label:
                content = f"[{label}]: {content}"
        formatted.append({"role": role, "content": content})
    return formatted


def _speaker_label(message: dict) -> str:
    name = str(message.get("character_name") or "").strip()
    character_id = str(message.get("character_id") or "").strip()
    if name and character_id:
        return f"{name}|{character_id}"
    return name or character_id
