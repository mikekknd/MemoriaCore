"""Ollama 對話模擬器：透過本地 LLM 生成測試用的模擬對話"""
import json


# 複用 tools_synthetic.py 的 JSON Schema 模式
CONVERSATION_SCHEMA = {
    "type": "object",
    "properties": {
        "conversation": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "role": {"type": "string", "enum": ["user", "assistant"]},
                    "content": {"type": "string"}
                },
                "required": ["role", "content"]
            }
        }
    },
    "required": ["conversation"]
}


def _parse_conversation(raw_text):
    """從 LLM 原始輸出解析對話陣列"""
    _start = raw_text.find('{')
    if _start == -1:
        raise RuntimeError(f"LLM 輸出無合法 JSON: {raw_text[:200]}")

    parsed_obj, _ = json.JSONDecoder().raw_decode(raw_text, _start)
    parsed_array = parsed_obj.get("conversation", [])

    messages = []
    for item in parsed_array:
        if isinstance(item, dict):
            r = item.get("role", "").lower()
            c = item.get("content", "").strip()
            if r in ["user", "assistant"] and c:
                messages.append({"role": r, "content": c})

    if not messages:
        raise RuntimeError(f"JSON 解析成功但無有效對話: {raw_text[:200]}")

    return messages


def generate_conversation(router, topic, turns=6):
    """生成一段關於指定主題的模擬對話

    Args:
        router: LLMRouter 實例
        topic: 對話主題
        turns: 預期回合數

    Returns:
        list[dict]: [{"role": "user"/"assistant", "content": "..."}]

    Raises:
        RuntimeError: 生成或解析失敗
    """
    prompt = f"""請模擬一段關於「{topic}」的深度自然對話，包含 User 和 Assistant 的來回討論。
【長度與深度要求】：約 {turns} 回合，最大不超過 20 回合。包含提問、解答、延伸討論。
【語言】：繁體中文。
【強制輸出格式】：嚴禁任何開場白、結語或解釋。請直接依照提供的 JSON Schema 結構輸出。"""

    api_messages = [{"role": "user", "content": prompt}]
    raw_text = router.generate("chat", api_messages, temperature=0.6, response_format=CONVERSATION_SCHEMA)
    return _parse_conversation(raw_text)


def generate_persona_conversation(router, persona_desc, topic, turns=6):
    """生成含有特定使用者人設的模擬對話（用於畫像提取測試）

    Args:
        router: LLMRouter 實例
        persona_desc: 使用者人設描述（如「使用者叫 Alice，住在台北，對花生過敏」）
        topic: 對話主題
        turns: 預期回合數

    Returns:
        list[dict]: 模擬對話
    """
    prompt = f"""請模擬一段自然對話，其中 User 扮演具有以下特質的角色：
【使用者人設】：{persona_desc}
【對話主題】：{topic}
【要求】：
1. 使用者必須在對話中「自然地」以第一人稱揭露上述人設中的個人資訊（姓名、偏好、禁忌等）。
2. 不要刻意或生硬地提及，要融入對話脈絡中。
3. 約 {turns} 回合，包含 User 和 Assistant 的來回討論。
4. 語言：繁體中文。
【強制輸出格式】：嚴禁任何開場白、結語或解釋。請直接依照提供的 JSON Schema 結構輸出。"""

    api_messages = [{"role": "user", "content": prompt}]
    raw_text = router.generate("chat", api_messages, temperature=0.6, response_format=CONVERSATION_SCHEMA)
    return _parse_conversation(raw_text)


def generate_preference_conversation(router, preferences, topic, turns=6):
    """生成含有明確偏好表達的模擬對話（用於偏好聚合測試）

    Args:
        router: LLMRouter 實例
        preferences: 偏好清單（如 ["喜歡肉類料理", "討厭甜食"]）
        topic: 對話主題
        turns: 預期回合數

    Returns:
        list[dict]: 模擬對話
    """
    pref_list = "\n".join([f"- {p}" for p in preferences])
    prompt = f"""請模擬一段自然對話，其中 User 在討論過程中明確表達以下偏好：
【使用者偏好】：
{pref_list}
【對話主題】：{topic}
【要求】：
1. 使用者必須在對話中以第一人稱強烈且明確地表達上述偏好（例如「我超愛...」「我最討厭...」）。
2. 每個偏好至少被提及或暗示一次。
3. 約 {turns} 回合，包含 User 和 Assistant 的來回討論。
4. 語言：繁體中文。
【強制輸出格式】：嚴禁任何開場白、結語或解釋。請直接依照提供的 JSON Schema 結構輸出。"""

    api_messages = [{"role": "user", "content": prompt}]
    raw_text = router.generate("chat", api_messages, temperature=0.6, response_format=CONVERSATION_SCHEMA)
    return _parse_conversation(raw_text)
