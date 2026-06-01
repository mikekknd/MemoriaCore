"""最終 chat prompt 的記憶上下文格式化。"""
from dataclasses import dataclass

from core.chat_orchestrator.dialogue_format import format_dialogue_for_analysis


@dataclass
class RetrievedMemoryContext:
    prompt: str
    block_details: list[dict]
    core_debug_text: str
    profile_debug_text: str


def _scalar(value: object, *, limit: int = 240) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def _literal_block(value: object, *, indent: str = "  ") -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n") or [""]
    return "\n".join(f"{indent}{line}" if line else indent for line in lines)


def _score(value: object) -> str:
    try:
        return f"{float(value):.3f}"
    except (TypeError, ValueError):
        return "0.000"


def _fact_items(facts: list[dict], *, indent: str = "") -> list[str]:
    lines: list[str] = []
    child_indent = indent + "  "
    for fact in facts:
        lines.append(f"{indent}- key: {_scalar(fact.get('fact_key'), limit=120)}")
        lines.append(f"{child_indent}value: {_scalar(fact.get('fact_value'))}")
    return lines


def format_static_profile_prompt(
    basic_facts: list[dict],
    critical_facts: list[dict],
) -> str:
    """格式化長期靜態使用者資料，供 chat system prompt 注入。"""
    if not basic_facts and not critical_facts:
        return ""

    lines = ["static_user_profile:"]
    if basic_facts:
        lines.append("  basic_info:")
        lines.extend(_fact_items(basic_facts, indent="  "))
    if critical_facts:
        lines.append("  critical_rules:")
        lines.extend(_fact_items(critical_facts, indent="  "))
    return "\n".join(lines)


def format_proactive_topics_prompt(topics: list[dict]) -> str:
    """格式化背景蒐集話題，供 chat system prompt 注入。"""
    if not topics:
        return ""

    lines = [
        "proactive_topics:",
        "  instruction: |",
        "    以下是系統背景蒐集到、使用者可能感興趣的資訊。請視上下文自然融合，不要說「我查到了」或「根據背景資訊」。",
        "  topics:",
    ]
    for topic in topics:
        lines.append(f"  - keyword: {_scalar(topic.get('interest_keyword'), limit=120)}")
        lines.append(f"    summary: {_scalar(topic.get('summary_content'))}")
    return "\n".join(lines)


def _format_core_memory(core_insights: list[dict]) -> tuple[str, str]:
    if not core_insights:
        return "", "未觸發核心認知。"

    insight = str(core_insights[0].get("insight", "") or "")
    score = core_insights[0].get("score", 0)
    lines = [
        "core_memory:",
        f"  score: {_score(score)}",
        "  insight: |",
        _literal_block(insight, indent="    "),
    ]
    return "\n".join(lines), f"觸發認知: {insight} (Score: {_score(score)})"


def _format_relevant_preferences(profile_matches: list[dict]) -> tuple[str, str]:
    if not profile_matches:
        return "", "未觸發使用者偏好。"

    lines = ["relevant_preferences:"]
    for match in profile_matches:
        lines.append(f"- key: {_scalar(match.get('fact_key'), limit=120)}")
        lines.append(f"  value: {_scalar(match.get('fact_value'))}")
        lines.append(f"  score: {_score(match.get('score', 0))}")

    debug = "觸發 {count} 筆偏好: {items}".format(
        count=len(profile_matches),
        items=", ".join(
            f"{m.get('fact_key')}={m.get('fact_value')} ({_score(m.get('score', 0))})"
            for m in profile_matches
        ),
    )
    return "\n".join(lines), debug


def _format_episodic_memories(
    blocks: list[dict],
    *,
    force_group: bool = False,
) -> tuple[str, list[dict]]:
    if not blocks:
        return "", []

    lines = ["episodic_memories:"]
    block_details: list[dict] = []
    for i, block in enumerate(blocks):
        overview = str(block.get("overview", "") or "")
        raw_text = format_dialogue_for_analysis(
            block.get("raw_dialogues", []),
            force_group=force_group,
            prefer_named_assistant=True,
        )
        lines.extend([
            f"- index: {i + 1}",
            f"  uid: {_scalar(block.get('block_id', 'unknown'), limit=120)}",
            f"  timestamp: {_scalar(block.get('timestamp'), limit=120)}",
            "  overview: |",
            _literal_block(overview, indent="    "),
            "  dialogue: |",
            _literal_block(raw_text, indent="    "),
        ])

        overview_header = overview.split("\n", 1)[0] if "\n" in overview else overview
        block_details.append({
            "id": i + 1,
            "overview": overview_header,
            "hybrid": block.get("_debug_score", 0),
            "dense": block.get("_debug_raw_sim", 0),
            "sparse": block.get("_debug_sparse_raw", 0),
            "recency": block.get("_debug_recency", 0),
            "importance": block.get("_debug_importance", 0),
        })

    return "\n".join(lines), block_details


def build_retrieved_memory_context(
    *,
    core_insights: list[dict],
    profile_matches: list[dict],
    blocks: list[dict],
    static_profile: str = "",
    proactive_topics: str = "",
    force_group: bool = False,
) -> RetrievedMemoryContext:
    """建立 `<retrieved_memory_context>` 內層文字。"""
    sections: list[str] = []
    if static_profile:
        sections.append(static_profile.strip())

    core_text, core_debug_text = _format_core_memory(core_insights)
    if core_text:
        sections.append(core_text)

    profile_text, profile_debug_text = _format_relevant_preferences(profile_matches)
    if profile_text:
        sections.append(profile_text)

    if proactive_topics:
        sections.append(proactive_topics.strip())

    episodic_text, block_details = _format_episodic_memories(blocks, force_group=force_group)
    if episodic_text:
        sections.append(episodic_text)

    return RetrievedMemoryContext(
        prompt="\n\n".join(sections),
        block_details=block_details,
        core_debug_text=core_debug_text,
        profile_debug_text=profile_debug_text,
    )
