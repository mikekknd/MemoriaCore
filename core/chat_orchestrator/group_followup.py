"""群組接力指令注入工具。"""
import re

from core.prompt_manager import get_prompt_manager
from core.prompt_utils import build_user_prefix, format_latest_user_message_for_llm
from core.xml_prompt import xml_block

_FOLLOWUP_INSTRUCTION_RE = re.compile(
    r"^\s*<group_followup_instruction>\s*(.*?)\s*</group_followup_instruction>\s*$",
    re.DOTALL,
)


def build_group_followup_instruction(followup: dict, user_prompt: str) -> str:
    """依目前 prompt template 組出群組接力指令。"""
    return get_prompt_manager().get("group_followup_user").format(
        user_prompt=followup.get("user_prompt_original", user_prompt),
        last_character_name=followup.get("last_character_name", ""),
        last_reply=followup.get("last_reply", ""),
    )


def _followup_instruction_body(followup_text: str) -> str:
    """取出既有 template 的內文，避免產生同名巢狀 XML-like 標籤。"""
    match = _FOLLOWUP_INSTRUCTION_RE.match(followup_text or "")
    if match:
        return match.group(1).strip()
    return (followup_text or "").strip()


def inject_group_followup_instruction(
    api_messages: list[dict],
    followup: dict | None,
    user_prompt: str,
    session_messages: list[dict] | None = None,
    user_prefs: dict | None = None,
    session_ctx: dict | None = None,
) -> None:
    """將群組接力指令注入最終 LLM messages。

    追加最後一則 user control message，避免接力回合以 assistant message 結尾。
    這則 control 同時帶入本輪真人訊息與環境/使用者名稱前綴，避免後續 AI 只看見
    接力規則而失去「這一輪使用者實際說了什麼」的明確標記。
    """
    if not followup or not api_messages:
        return

    followup_text = build_group_followup_instruction(followup, user_prompt)
    original_user_prompt = followup.get("user_prompt_original") or user_prompt
    prefix = build_user_prefix(
        session_messages or [],
        user_prefs=user_prefs or {},
        session_ctx=session_ctx or {},
    )
    latest_user = format_latest_user_message_for_llm(original_user_prompt, session_ctx or {})
    followup_control = xml_block(
        "group_followup_instruction",
        _followup_instruction_body(followup_text),
        attrs={"source": "system_control"},
    )
    api_messages.append({
        "role": "user",
        "content": prefix + latest_user + "\n\n" + followup_control,
    })
