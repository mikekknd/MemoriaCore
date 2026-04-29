"""對話歷史格式化工具。"""


def format_history_for_llm(messages: list[dict]) -> list[dict]:
    """將群組對話的 assistant 訊息加上 speaker 標籤，讓上下文可辨識發話角色。
    單 AI session（僅一個 character_id）不加前綴，避免 LLM 自我污染。
    """
    assistant_char_ids = {
        m.get("character_id") for m in messages
        if m.get("role") == "assistant" and m.get("character_id")
    }
    is_group = len(assistant_char_ids) > 1

    formatted: list[dict] = []
    for m in messages:
        role = m.get("role", "")
        content = m.get("content", "")
        if is_group and role == "assistant":
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
