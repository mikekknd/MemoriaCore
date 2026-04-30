"""群組接力指令注入工具。"""
import re

from core.prompt_manager import get_prompt_manager
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
) -> None:
    """將群組接力指令注入最終 LLM messages。

    只追加最後一則 user control message，避免接力回合以 assistant message 結尾，
    同時保持 system prompt 穩定，降低破壞 prefix cache 的機率。
    """
    if not followup or not api_messages:
        return

    followup_text = build_group_followup_instruction(followup, user_prompt)
    api_messages.append({
        "role": "user",
        "content": xml_block(
            "group_followup_instruction",
            _followup_instruction_body(followup_text),
            attrs={"source": "system_control"},
        ),
    })
