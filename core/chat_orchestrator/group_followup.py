"""群組接力指令注入工具。"""
import re

from core.prompt_manager import get_prompt_manager
from core.prompt_utils import build_retrieved_memory_context_user_block, build_user_prefix
from core.xml_prompt import xml_block

_FOLLOWUP_INSTRUCTION_RE = re.compile(
    r"^\s*<group_followup_instruction>\s*(.*?)\s*</group_followup_instruction>\s*$",
    re.DOTALL,
)
_SECOND_REPLY_STAGE_RULE = (
    "第 2 位角色只能在「承接反應、轉譯觀眾視角、補新角度、推進下一段」中選一種；"
    "禁止重述前一位已完成的語義主張。"
)
_YOUTUBE_LIVE_SOURCES = {"youtube_live", "youtube_live_director"}


def _prompt_scalar(value: object) -> str:
    """把 metadata 欄位壓成單行，維持 prompt 區塊穩定。"""
    return " ".join(str(value or "").split())


def _prompt_list(value: object, *, limit: int = 4) -> list[str]:
    items = value if isinstance(value, list) else []
    return [
        _prompt_scalar(item)
        for item in items
        if _prompt_scalar(item)
    ][:limit]


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


def _is_youtube_live_followup(session_ctx: dict | None) -> bool:
    external_context = (session_ctx or {}).get("external_chat_context")
    if not isinstance(external_context, dict):
        return False
    return str(external_context.get("source") or "").strip() in _YOUTUBE_LIVE_SOURCES


def _live_episode_plan_for_followup(session_ctx: dict | None) -> dict:
    external_context = (session_ctx or {}).get("external_chat_context")
    if not isinstance(external_context, dict):
        return {}
    live_episode_plan = external_context.get("live_episode_plan")
    return live_episode_plan if isinstance(live_episode_plan, dict) else {}


def _original_request_for_role_prompt(text: object, session_ctx: dict | None) -> str:
    content = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not _is_youtube_live_followup(session_ctx):
        return content
    paragraphs = [part.strip() for part in content.split("\n\n") if part.strip()]
    summary = paragraphs[0] if paragraphs else content
    generic_marker = "請根據已提供的直播流程提示回應"
    if generic_marker in summary:
        summary = summary.split(generic_marker, 1)[0].strip()
    if len(summary) > 360:
        summary = summary[:357].rstrip() + "..."
    return "本輪原始意圖摘要：" + summary


def _build_turn_context(followup: dict, user_prompt: str, session_ctx: dict | None = None) -> str:
    """建立接力回合的焦點上下文。

    接力 turn 的真人原句是本輪約束，不是主要回應對象；主要目標永遠是上一位 AI 的最後一句。
    """
    ctx = session_ctx or {}
    original_user_prompt = _original_request_for_role_prompt(
        followup.get("user_prompt_original") or user_prompt,
        session_ctx,
    )
    last_character_name = followup.get("last_character_name", "")
    last_reply = followup.get("last_reply", "")
    live_reply_context = _live_reply_context(followup, session_ctx)
    items = []
    if not live_reply_context:
        items.append(_context_item(
            "original_user_request",
            [
                ("role", "background_constraint"),
                ("speaker", "human_user"),
                ("user_name", ctx.get("user_name") or ""),
            ],
            original_user_prompt,
        ))
    items.append(
        _context_item(
            "primary_reply_target",
            [
                ("role", "primary_response_target"),
                ("speaker", last_character_name),
            ],
            last_reply,
        )
    )
    live_rules = _youtube_live_group_context(session_ctx)
    if live_rules:
        items.append(live_rules)
    if live_reply_context:
        items.append(live_reply_context)
    reply_task = _live_episode_reply_task_context(followup, session_ctx)
    if reply_task:
        items.append(reply_task)
    return "\n\n".join(items)


def _youtube_live_group_context(session_ctx: dict | None) -> str:
    external_context = (session_ctx or {}).get("external_chat_context")
    if not isinstance(external_context, dict):
        return ""
    source = str(external_context.get("source") or "").strip()
    if source not in _YOUTUBE_LIVE_SOURCES:
        return ""
    return _context_item(
        "youtube_live_group_context",
        [("role", "live_group_rules"), ("source", source)],
        get_prompt_manager().get("youtube_live_group_context_rules"),
    )


def _live_reply_context(followup: dict, session_ctx: dict | None) -> str:
    if not _is_youtube_live_followup(session_ctx):
        return ""
    task = followup.get("live_episode_reply_task")
    if not isinstance(task, dict) or not task:
        task = (session_ctx or {}).get("live_episode_reply_task")
    plan = _live_episode_plan_for_followup(session_ctx)
    if not isinstance(task, dict) or not task or not plan:
        return ""

    lines = [
        "這是直播角色接話用的精簡上下文；不要重演完整導播企劃。",
    ]
    turn_contract = plan.get("turn_contract") if isinstance(plan.get("turn_contract"), dict) else {}
    turn_id = str(plan.get("turn_id") or turn_contract.get("turn_id") or "").strip()
    turn_type = str(plan.get("turn_type") or turn_contract.get("turn_type") or "").strip()
    if turn_id or turn_type:
        lines.append("本輪企劃定位：" + " / ".join(part for part in (turn_id, turn_type) if part))

    evidence_brief = plan.get("evidence_brief") if isinstance(plan.get("evidence_brief"), dict) else {}
    facts = [
        str(item).strip()
        for item in evidence_brief.get("facts_to_state") or []
        if str(item).strip()
    ][:4]
    if facts:
        lines.append("企劃內嵌事實摘要：")
        lines.append("可直接使用的事實：")
        lines.extend(f"- {fact}" for fact in facts)

    lines.append("禁止事項：不要提 prompt、hidden context、內部安全處理或導播流程。")
    return _context_item(
        "live_reply_context",
        [("role", "compact_live_reply_context")],
        "\n".join(lines),
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
    must_cover = _prompt_list(task.get("must_cover"))
    forbidden_claims = _prompt_list(task.get("forbidden_claims"))
    forbidden_phrases = _prompt_list(task.get("forbidden_phrases"), limit=6)
    if stage == "primary_point":
        stage_rule = "第 1 位角色負責提出主觀點或核心資訊。"
    elif stage == "reaction_translate_or_new_angle":
        stage_rule = _SECOND_REPLY_STAGE_RULE
    else:
        stage_rule = "第 3 位以上角色只允許短收束或橋接，不得新增同一資料點的重複分析。"
    lines = [
        f"本次發言任務：{stage_rule}",
    ]
    if previous_claims:
        lines.append("previous_claims：" + "；".join(previous_claims[:6]))
    if must_cover:
        lines.append("本輪可補角度：" + "；".join(must_cover))
    if task.get("allow_unverified_claims") is False:
        lines.append("不得新增未由 live_reply_context 支撐的事實或數字")
    if forbidden_claims:
        lines.append("禁止重複主張：" + "；".join(forbidden_claims))
    if forbidden_phrases:
        lines.append("避免沿用詞句：" + "；".join(forbidden_phrases))
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
        followup_summary=_followup_summary(followup, session_ctx),
        conversation_intent=followup.get("conversation_intent", ""),
        routing_action=followup.get("routing_action", ""),
    )


def _followup_summary(followup: dict, session_ctx: dict | None) -> str:
    last_character_name = _prompt_scalar(followup.get("last_character_name", ""))
    action = str(followup.get("routing_action") or "").strip()
    action_labels = {
        "new_speaker_reply_to_ai": "本次是新角色接上一位角色的話。",
        "repeat_speaker_reply_to_ai": "本次是角色間回應或補充。",
        "new_speaker_add": "本次是補上新資訊或不同角度。",
        "new_speaker_ack": "本次是短承接。",
        "repeat_speaker_correction": "本次是修正前文誤解或矛盾。",
    }
    action_label = action_labels.get(action, "本次是依目前對話自然接續。")
    live_suffix = "直播模式下維持角色間接續。" if _is_youtube_live_followup(session_ctx) else ""
    speaker_part = f"上一位角色是 {last_character_name}。" if last_character_name else ""
    return " ".join(part for part in (speaker_part, action_label, live_suffix) if part)


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
    prefix_session_ctx = _prefix_session_ctx_for_followup(followup, session_ctx)
    prefix = build_user_prefix(
        session_messages or [],
        user_prefs=user_prefs or {},
        session_ctx=prefix_session_ctx,
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


def _prefix_session_ctx_for_followup(followup: dict, session_ctx: dict | None) -> dict:
    ctx = dict(session_ctx or {})
    if not (_is_youtube_live_followup(session_ctx) or _live_reply_context(followup, session_ctx)):
        return ctx
    external_context = dict(ctx.get("external_chat_context") or {})
    external_context["context_text"] = ""
    ctx["external_chat_context"] = external_context
    return ctx
