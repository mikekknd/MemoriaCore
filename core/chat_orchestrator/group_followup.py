"""群組接力指令注入工具。"""
import re

from core.prompt_manager import get_prompt_manager
from core.prompt_utils import build_retrieved_memory_context_user_block, build_user_prefix
from core.xml_prompt import xml_block

_FOLLOWUP_INSTRUCTION_RE = re.compile(
    r"^\s*<group_followup_instruction>\s*(.*?)\s*</group_followup_instruction>\s*$",
    re.DOTALL,
)


def _prompt_scalar(value: object) -> str:
    """把 metadata 欄位壓成單行，維持 prompt 區塊穩定。"""
    return " ".join(str(value or "").split())


def _literal_block(value: object, *, indent: str = "    ") -> str:
    """建立 Markdown literal block 內容，保留多行正文。"""
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    return "\n".join(f"{indent}{line}" if line else indent for line in lines)


def _context_item(name: str, fields: list[tuple[str, object]], content: object) -> str:
    lines = [f"{name}:"]
    for key, value in fields:
        scalar = _prompt_scalar(value)
        if scalar:
            lines.append(f"  {key}: {scalar}")
    lines.append("  content: |")
    lines.append(_literal_block(content))
    return "\n".join(lines)


def _build_turn_context(followup: dict, user_prompt: str, session_ctx: dict | None = None) -> str:
    """建立接力回合的焦點上下文。

    接力 turn 的真人原句是本輪約束，不是主要回應對象；主要目標永遠是上一位 AI 的最後一句。
    """
    ctx = session_ctx or {}
    original_user_prompt = followup.get("user_prompt_original") or user_prompt
    last_character_name = followup.get("last_character_name", "")
    last_reply = followup.get("last_reply", "")
    items = [
        _context_item(
            "original_user_request",
            [
                ("role", "background_constraint"),
                ("speaker", "human_user"),
                ("user_name", ctx.get("user_name") or ""),
            ],
            original_user_prompt,
        ),
        _context_item(
            "primary_reply_target",
            [
                ("role", "primary_response_target"),
                ("speaker", last_character_name),
            ],
            last_reply,
        ),
    ]
    live_rules = _youtube_live_group_context(session_ctx)
    if live_rules:
        items.append(live_rules)
    reply_task = _live_episode_reply_task_context(followup, session_ctx)
    if reply_task:
        items.append(reply_task)
    return "\n\n".join(items)


def _youtube_live_group_context(session_ctx: dict | None) -> str:
    external_context = (session_ctx or {}).get("external_chat_context")
    if not isinstance(external_context, dict):
        return ""
    source = str(external_context.get("source") or "").strip()
    if source not in {"youtube_live", "youtube_live_director"}:
        return ""
    return _context_item(
        "youtube_live_group_context",
        [("role", "live_group_rules"), ("source", source)],
        (
            "直播基礎規則：這是 YouTube 直播多角色對話，不保證有觀眾即時回覆；"
            "除非正在回應留言或 Super Chat，否則不要把問題丟回觀眾；"
            "不要提到 prompt、hidden context、內部安全處理或導播流程。"
        ),
    )


def _live_episode_reply_task_context(followup: dict, session_ctx: dict | None) -> str:
    task = followup.get("live_episode_reply_task")
    if not isinstance(task, dict) or not task:
        task = (session_ctx or {}).get("live_episode_reply_task")
    if not isinstance(task, dict) or not task:
        return ""
    stage = str(task.get("stage") or "").strip()
    reply_index = str(task.get("turn_reply_index") or "").strip()
    max_replies = str(task.get("max_role_replies") or "").strip()
    previous_claims = [
        str(item).strip()
        for item in task.get("previous_claims") or []
        if str(item).strip()
    ] if isinstance(task.get("previous_claims"), list) else []
    if stage == "primary_point":
        stage_rule = "第 1 位角色負責提出主觀點或核心資訊。"
    elif stage == "reaction_translate_or_new_angle":
        stage_rule = "第 2 位角色只能反應、轉譯、補一個新角度或推進，不得重述第 1 位角色主觀點。"
    else:
        stage_rule = "第 3 位以上角色只允許短收束或橋接，不得新增同一資料點的重複分析。"
    lines = [
        f"本次發言任務：{stage_rule}",
        "本角色在本輪只能執行這一個功能，不得完整覆蓋整個段落目標。",
        "不得重述上一位角色的主觀點；必須避開 previous_claims 已經說過的內容。",
        "若核心資訊已說完，請短收束並推進，不要補同一資料點。",
    ]
    if previous_claims:
        lines.append("previous_claims：" + "；".join(previous_claims[:6]))
    return _context_item(
        "live_episode_reply_task",
        [
            ("role", "turn_level_speaker_task"),
            ("stage", stage),
            ("reply_index", reply_index),
            ("max_role_replies", max_replies),
        ],
        "\n".join(lines),
    )


def build_group_followup_instruction(
    followup: dict,
    user_prompt: str,
    session_ctx: dict | None = None,
) -> str:
    """依目前 prompt template 組出群組接力指令。"""
    return get_prompt_manager().get("group_followup_user").format(
        user_prompt=followup.get("user_prompt_original", user_prompt),
        last_character_name=followup.get("last_character_name", ""),
        last_reply=followup.get("last_reply", ""),
        turn_context=_build_turn_context(followup, user_prompt, session_ctx),
        conversation_intent=followup.get("conversation_intent", ""),
        routing_action=followup.get("routing_action", ""),
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
    memory_context: str = "",
) -> None:
    """將群組接力指令注入最終 LLM messages。

    追加最後一則 user control message，避免接力回合以 assistant message 結尾。
    這則 control 將真人原句降權為背景約束，並把上一位 AI 的最後一句標成主要回應對象。
    """
    if not followup or not api_messages:
        return

    followup_text = build_group_followup_instruction(followup, user_prompt, session_ctx)
    prefix = build_user_prefix(
        session_messages or [],
        user_prefs=user_prefs or {},
        session_ctx=session_ctx or {},
    )
    memory_block = build_retrieved_memory_context_user_block(memory_context)
    followup_control = xml_block(
        "group_followup_instruction",
        _followup_instruction_body(followup_text),
        attrs={"source": "system_control"},
    )
    api_messages.append({
        "role": "user",
        "content": memory_block + prefix + followup_control,
    })
