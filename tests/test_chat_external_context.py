import json
from pathlib import Path

from api.models.requests import ChatSyncRequest
from api.routers.chat_rest import (
    _build_external_context_visible_event,
    _chat_user_display_name,
    _external_context_group_turn_limit,
    _live_session_scope_for_external_context,
    _memory_write_policy_for_request,
    _resolve_chat_display_content,
    _resolve_external_context_payload,
    _transient_user_content_for_external_context,
)
from core.chat_orchestrator.dialogue_format import format_history_for_llm
from core.chat_orchestrator.dataclasses import PipelineContext
from core.chat_orchestrator.group_followup import build_group_followup_instruction


def test_external_context_payload_is_generic_and_capped():
    body = ChatSyncRequest(
        content="hello",
        external_context={
            "source": "youtube live!",
            "source_session_id": "yt-session",
            "context_text": "x" * 1500,
            "max_chars": 1000,
            "event_ids": [3, 2, 1],
            "summary": {"event_count": 3},
        },
    )

    context, summary = _resolve_external_context_payload(body)

    assert context is not None
    assert context["source"] == "youtube_live_"
    assert len(context["context_text"]) == 1000
    assert summary["source_session_id"] == "yt-session"
    assert summary["event_count"] == 3
    assert summary["event_ids"] == ["3", "2", "1"]
    assert summary["truncated"] is True


def test_external_context_payload_ignores_empty_context():
    body = ChatSyncRequest(
        content="hello",
        external_context={"source": "youtube_live", "context_text": "  "},
    )

    context, summary = _resolve_external_context_payload(body)

    assert context is None
    assert summary == {}


def test_external_context_visible_event_is_not_llm_visible():
    body = ChatSyncRequest(
        content="hello",
        external_context={
            "source": "youtube_live",
            "context_text": "\n".join(f"- viewer{i}: message{i}" for i in range(10)),
            "visible_events": [
                {
                    "event_id": i,
                    "author_display_name": f"viewer{i}",
                    "author_channel_id": f"UC{i:02d}abcdefghij",
                    "message_text": f"message{i}",
                }
                for i in range(10)
            ],
            "summary": {"event_count": 10},
        },
    )
    context, summary = _resolve_external_context_payload(body)

    event = _build_external_context_visible_event(context, summary)

    assert event is not None
    content, debug_info = event
    assert content.startswith("YouTube Live 留言注入：10 則")
    assert "viewer0: message0" in content
    assert "UC00abcdefghij" not in content
    assert "UC00ab...efghij" not in content
    assert "textMessageEvent" not in content
    assert "另有 7 則未顯示。" in content
    assert debug_info["event_type"] == "youtube_live_chat_batch"
    assert debug_info["llm_visible"] is False
    assert debug_info.get("hide_in_chat") is not True

    formatted = format_history_for_llm([
        {"role": "system_event", "content": content, "debug_info": debug_info},
        {"role": "user", "content": "hello"},
    ])
    assert formatted == [{"role": "user", "content": "hello"}]


def test_external_context_display_content_uses_only_visible_chat_lines():
    body = ChatSyncRequest(
        content="請根據已帶入的 YouTube 直播留言上下文回應。不要開啟瀏覽器或搜尋網頁。",
        external_context={
            "source": "youtube_live",
            "context_text": (
                "- 2026-05-02T15:53:17.8658+00:00 @viewer (textMessageEvent): 被看到大型debug現場\n"
                "<topic_pack_fact_cards>\n"
                "四月新番 fact card 內容\n"
                "</topic_pack_fact_cards>"
            ),
            "visible_events": [
                {
                    "event_id": 1,
                    "author_display_name": "@viewer",
                    "author_channel_id": "UCFakeChannelId",
                    "message_text": "被看到大型debug現場",
                },
                {
                    "event_id": 2,
                    "author_display_name": "SC觀眾",
                    "author_channel_id": "UCSecret",
                    "message_text": "支持一下",
                    "amount_display_string": "NT$150",
                    "priority_class": "super_chat",
                },
            ],
            "summary": {"event_count": 2},
        },
    )
    context, _summary = _resolve_external_context_payload(body)

    display = _resolve_chat_display_content(body, context)

    assert display == "@viewer: 被看到大型debug現場\n[SC NT$150] SC觀眾: 支持一下"
    assert "請根據已帶入" not in display
    assert "topic_pack_fact_cards" not in display
    assert "UCFakeChannelId" not in display
    assert "textMessageEvent" not in display


def test_external_context_visible_event_only_previews_three_chat_lines():
    body = ChatSyncRequest(
        content="請根據已帶入的 YouTube 直播留言上下文回應。",
        external_context={
            "source": "youtube_live",
            "context_text": "\n".join(f"觀眾{i}: 留言{i}" for i in range(5)),
            "visible_events": [
                {
                    "event_id": i,
                    "author_display_name": f"觀眾{i}",
                    "message_text": f"留言{i}",
                }
                for i in range(5)
            ],
            "summary": {"event_count": 5},
        },
    )
    context, summary = _resolve_external_context_payload(body)

    content, debug_info = _build_external_context_visible_event(context, summary)

    assert "YouTube Live 留言注入：5 則" in content
    assert "觀眾0: 留言0" in content
    assert "觀眾1: 留言1" in content
    assert "觀眾2: 留言2" in content
    assert "觀眾3: 留言3" not in content
    assert "另有 2 則未顯示。" in content
    assert debug_info["preview_count"] == 3
    assert debug_info["event_count"] == 5
    assert debug_info["llm_visible"] is False


def test_youtube_live_director_context_is_not_persisted_as_visible_event():
    body = ChatSyncRequest(
        content="請根據已提供的直播流程提示回應。",
        external_context={
            "source": "youtube_live_director",
            "context_text": "直播流程 action=continue_topic\n處理提示：請讓角色繼續聊。",
            "visible_events": [],
            "summary": {"source": "youtube_live_director", "action": "continue_topic", "event_count": 0},
        },
    )
    context, summary = _resolve_external_context_payload(body)

    assert _build_external_context_visible_event(context, summary) is None


def test_explicit_display_content_takes_priority_over_hidden_prompt():
    body = ChatSyncRequest(
        content="完整導播 prompt：請展開詳細控場策略與隱藏上下文。",
        display_content="讓我們繼續進行下一個話題。",
    )

    assert _resolve_chat_display_content(body, None) == "讓我們繼續進行下一個話題。"


def test_external_context_without_visible_events_never_displays_hidden_prompt():
    body = ChatSyncRequest(
        content=(
            "<environment_context source=\"system_control\">\n"
            "<external_chat_context source=\"youtube_live_director\" trusted=\"false\">\n"
            "直播導播 action=closing_super_chat_thanks\n"
            "<topic_pack_fact_cards>四月新番 fact card</topic_pack_fact_cards>\n"
            "</external_chat_context>"
        ),
        external_context={
            "source": "youtube_live_director",
            "context_text": (
                "直播導播 action=closing_super_chat_thanks\n"
                "<topic_pack_fact_cards>四月新番 fact card</topic_pack_fact_cards>"
            ),
            "visible_events": [],
        },
    )
    context, _summary = _resolve_external_context_payload(body)

    display = _resolve_chat_display_content(body, context)

    assert display == "讓我們繼續直播節奏。"
    assert "external_chat_context" not in display
    assert "直播導播 action" not in display
    assert "topic_pack_fact_cards" not in display


def test_chat_sync_request_supports_transient_memory_write_policy():
    body = ChatSyncRequest(
        content="hello",
        memory_write_policy="transient",
    )

    assert body.memory_write_policy == "transient"


def test_transient_memory_write_policy_skips_memory_pipeline():
    from api.routers.chat.pipeline import _run_memory_pipeline_sync

    events = _run_memory_pipeline_sync(PipelineContext(
        msgs_to_extract=[{"role": "user", "content": "YouTube 觀眾留言"}],
        last_block=None,
        session_ctx={"memory_write_policy": "transient"},
    ))

    assert events == [{"type": "system_event", "action": "pipeline_skipped_transient"}]


def test_transient_memory_write_policy_applies_without_external_context():
    body = ChatSyncRequest(content="hello", memory_write_policy="transient")

    assert _memory_write_policy_for_request(body, None) == "transient"


def test_external_context_forces_transient_memory_write_policy():
    body = ChatSyncRequest(content="hello", memory_write_policy="normal")

    assert _memory_write_policy_for_request(body, {"source": "youtube_live"}) == "transient"


def test_youtube_live_external_context_uses_public_live_scope():
    body = ChatSyncRequest(
        content="hello",
        user_id="1",
        channel_class="private",
        persona_face="private",
        external_context={
            "source": "youtube_live",
            "source_session_id": "yt-live-a",
            "context_text": "觀眾: hi",
        },
    )
    context, _summary = _resolve_external_context_payload(body)

    scope = _live_session_scope_for_external_context(body, context)

    assert scope == {
        "channel": "youtube_live",
        "channel_uid": "yt-live-a",
        "user_id": "__youtube_live__",
        "channel_class": "public",
        "persona_face": "public",
    }


def test_youtube_live_external_context_hides_admin_display_name():
    current_user = {"id": 1, "username": "mikekknd", "nickname": "夏雪", "role": "admin"}

    assert _chat_user_display_name(current_user, {"source": "youtube_live_director"}) == ""
    assert _chat_user_display_name(current_user, None) == "夏雪"


class _SessionStub:
    def __init__(self, character_ids: list[str]):
        self.active_character_ids = character_ids
        self.character_id = character_ids[0] if character_ids else "default"


def test_youtube_live_director_external_context_uses_explicit_group_turn_limit():
    session = _SessionStub(["char-a", "char-b"])

    limit = _external_context_group_turn_limit(
        session,
        {"source": "youtube_live_director", "group_turn_limit": 5},
    )

    assert limit == 5


def test_youtube_live_director_context_payload_preserves_group_turn_limit():
    session = _SessionStub(["char-a", "char-b"])
    body = ChatSyncRequest(
        content="請自然延續直播。",
        external_context={
            "source": "youtube_live_director",
            "context_text": "直播流程 action=continue_topic",
            "group_turn_limit": 10,
            "summary": {"source": "youtube_live_director", "group_turn_limit": 10},
        },
    )

    context, summary = _resolve_external_context_payload(body)

    assert context is not None
    assert context["group_turn_limit"] == 10
    assert summary["group_turn_limit"] == 10
    assert _external_context_group_turn_limit(session, context) == 10


def test_youtube_live_director_context_payload_preserves_episode_plan_summary_metadata():
    body = ChatSyncRequest(
        content="請自然延續直播。",
        external_context={
            "source": "youtube_live_director",
            "context_text": "直播流程 action=continue_topic",
            "summary": {
                "source": "youtube_live_director",
                "episode_plan_id": "plan-general-panel",
                "episode_plan_turn_id": "seg_01_turn_01",
                "episode_plan_mode": "planned_turn",
            },
        },
    )

    context, summary = _resolve_external_context_payload(body)

    assert context is not None
    assert summary["episode_plan_id"] == "plan-general-panel"
    assert summary["episode_plan_turn_id"] == "seg_01_turn_01"
    assert summary["episode_plan_mode"] == "planned_turn"
    assert context["summary"]["episode_plan_id"] == "plan-general-panel"
    assert context["summary"]["episode_plan_turn_id"] == "seg_01_turn_01"
    assert context["summary"]["episode_plan_mode"] == "planned_turn"


def test_youtube_live_director_context_payload_preserves_safe_live_episode_plan_for_bridge_scope():
    body = ChatSyncRequest(
        content="請自然延續直播。",
        channel="youtube_live",
        user_id="__youtube_live__",
        channel_class="public",
        persona_face="public",
        external_context={
            "source": "youtube_live_director",
            "context_text": "直播流程 action=continue_topic",
            "live_episode_plan": {
                "plan_id": "plan-general-panel",
                "title": "一般議題直播",
                "mode": "planned_turn",
                "segment_id": "seg_01",
                "turn_id": "seg_01_turn_01",
                "turn_type": "hook",
                "max_turns_override": 1,
                "unsafe_blob": {"full_plan": "drop me"},
                "dialogue_policy": {
                    "min_replies": 2,
                    "max_replies": 3,
                    "autonomy": "guided",
                    "preferred_flow": ["host frames", "analyst adds"],
                    "unsafe": "drop me",
                },
                "turn_contract": {
                    "turn_id": "seg_01_turn_01",
                    "turn_type": "hook",
                    "intent": "用具體事件開場",
                    "speaker_policy": {
                        "selection_mode": "fixed",
                        "allowed_participant_ids": ["char-a"],
                        "allowed_character_ids": ["char-a"],
                        "preferred_role_functions": ["host"],
                        "avoid_repeat_speaker": True,
                        "extra": "drop me",
                    },
                },
                "output_requirements": {
                    "max_sentences": 2,
                    "must_end_with_question": False,
                    "allow_audience_question": False,
                    "unsafe": "drop me",
                },
                "evidence_policy": {
                    "queries": ["公開週榜", "社群口碑"],
                    "required_entities": ["作品A"],
                    "max_cards": 1,
                    "allow_unverified_claims": False,
                    "unsafe": "drop me",
                },
                "interrupt_state": {"status": "planned", "secret": "drop me"},
            },
        },
    )

    context, summary = _resolve_external_context_payload(body)

    assert context is not None
    plan = context["live_episode_plan"]
    assert plan == {
        "plan_id": "plan-general-panel",
        "title": "一般議題直播",
        "mode": "planned_turn",
        "segment_id": "seg_01",
        "turn_id": "seg_01_turn_01",
        "turn_type": "hook",
        "max_turns_override": 1,
        "dialogue_policy": {
            "min_replies": 2,
            "max_replies": 3,
            "autonomy": "guided",
            "preferred_flow": ["host frames", "analyst adds"],
        },
        "turn_contract": {
            "turn_id": "seg_01_turn_01",
            "turn_type": "hook",
            "intent": "用具體事件開場",
            "speaker_policy": {
                "selection_mode": "fixed",
                "allowed_character_ids": ["char-a"],
                "preferred_role_functions": ["host"],
                "avoid_repeat_speaker": True,
            },
        },
        "speaker_policy": {
            "selection_mode": "fixed",
            "allowed_character_ids": ["char-a"],
            "preferred_role_functions": ["host"],
            "avoid_repeat_speaker": True,
        },
        "output_requirements": {
            "max_sentences": 2,
            "must_end_with_question": False,
            "allow_audience_question": False,
        },
        "evidence_policy": {
            "queries": ["公開週榜", "社群口碑"],
            "required_entities": ["作品A"],
            "max_cards": 1,
            "allow_unverified_claims": False,
        },
        "interrupt_state": {"status": "planned"},
    }
    assert "live_episode_plan" not in summary


def test_youtube_live_episode_plan_does_not_treat_participant_ids_as_character_ids():
    body = ChatSyncRequest(
        content="請自然延續直播。",
        channel="youtube_live",
        user_id="__youtube_live__",
        channel_class="public",
        persona_face="public",
        external_context={
            "source": "youtube_live_director",
            "context_text": "直播流程 action=continue_topic",
            "live_episode_plan": {
                "plan_id": "plan-general-panel",
                "mode": "planned_turn",
                "turn_id": "seg_01_turn_01",
                "speaker_policy": {
                    "selection_mode": "fixed",
                    "allowed_participant_ids": ["koko"],
                },
            },
        },
    )

    context, _summary = _resolve_external_context_payload(body)

    assert context is not None
    assert "allowed_character_ids" not in context["live_episode_plan"]["speaker_policy"]


def test_youtube_live_context_preserves_prompt_overrides_only_for_bridge_scope():
    body = ChatSyncRequest(
        content="請自然延續直播。",
        channel="youtube_live",
        user_id="__youtube_live__",
        channel_class="public",
        persona_face="public",
        external_context={
            "source": "youtube_live_director",
            "context_text": "直播流程 action=continue_topic",
            "character_prompt_overrides": {
                "coco": {
                    "enabled": True,
                    "mode": "replace",
                    "system_prompt": "直播專用可可 prompt",
                    "self_address": "本小姐",
                    "opening_intro": "本小姐是可可。",
                    "addressing": {"bailian": "白蓮大人"},
                    "reply_rules": "只在直播中使用。",
                }
            },
        },
    )

    context, summary = _resolve_external_context_payload(body)

    assert context is not None
    assert context["character_prompt_overrides"]["coco"]["system_prompt"] == "直播專用可可 prompt"
    assert context["character_prompt_overrides"]["coco"]["addressing"] == {"bailian": "白蓮大人"}
    assert "character_prompt_overrides" not in summary


def test_youtube_live_context_preserves_hosting_only_for_bridge_scope():
    body = ChatSyncRequest(
        content="請根據直播流程提示回應。",
        channel="youtube_live",
        user_id="__youtube_live__",
        channel_class="public",
        persona_face="public",
        external_context={
            "source": "youtube_live_director",
            "context_text": "直播流程 action=continue_topic",
            "live_hosting": {
                "host_interaction_rules": "可可提出觀眾視角；白蓮負責分析收束。",
                "program_segment_turns": 3,
                "segment_state": {
                    "topic": "魔法帽的工作室",
                    "topic_entry_id": 7,
                    "current_step": {"step_id": "step_02", "name": "核心分析", "description": "拆解背後因素。"},
                    "completed_steps": [{"step_id": "step_01", "name": "事件 Hook"}],
                    "remaining_steps": [{"step_id": "step_03", "name": "反方觀點", "description": "提醒不能過度解讀。"}],
                    "turns_in_step": 1,
                    "last_transition_reason": "step_hold",
                },
            },
        },
    )

    context, summary = _resolve_external_context_payload(body)

    assert context["live_hosting"]["host_interaction_rules"] == "可可提出觀眾視角；白蓮負責分析收束。"
    assert context["live_hosting"]["program_segment_turns"] == 3
    assert "program_segment_plan" not in context["live_hosting"]
    assert context["live_hosting"]["segment_state"] == {
        "topic": "魔法帽的工作室",
        "topic_entry_id": 7,
        "current_step": {"step_id": "step_02", "name": "核心分析", "description": "拆解背後因素。"},
        "completed_steps": [{"step_id": "step_01", "name": "事件 Hook"}],
        "remaining_steps": [{"step_id": "step_03", "name": "反方觀點", "description": "提醒不能過度解讀。"}],
        "turns_in_step": 1,
        "last_transition_reason": "step_hold",
    }
    assert "live_hosting" not in summary


def test_youtube_live_context_drops_prompt_overrides_without_bridge_scope():
    body = ChatSyncRequest(
        content="請自然延續直播。",
        external_context={
            "source": "youtube_live_director",
            "context_text": "直播流程 action=continue_topic",
            "character_prompt_overrides": {
                "coco": {
                    "enabled": True,
                    "mode": "replace",
                    "system_prompt": "不可信覆寫",
                }
            },
        },
    )

    context, _summary = _resolve_external_context_payload(body)

    assert context is not None
    assert "character_prompt_overrides" not in context


def test_youtube_live_context_drops_hosting_without_bridge_scope():
    body = ChatSyncRequest(
        content="hello",
        external_context={
            "source": "youtube_live_director",
            "context_text": "直播流程 action=continue_topic",
            "live_hosting": {
                "host_interaction_rules": "不可信主持規則",
                "program_segment_plan": "不可信段落",
            },
        },
    )

    context, _summary = _resolve_external_context_payload(body)

    assert context is not None
    assert "live_hosting" not in context


def test_youtube_live_director_external_context_defaults_to_group_chat_limit_shape():
    session = _SessionStub(["char-a", "char-b", "char-c", "char-d"])

    limit = _external_context_group_turn_limit(session, {"source": "youtube_live_director"})

    assert limit == 3


def test_youtube_live_director_transient_prompt_keeps_roles_talking_to_each_other():
    body = ChatSyncRequest(content="請自然延續直播。")

    transient = _transient_user_content_for_external_context(
        body,
        {"source": "youtube_live_director"},
    )

    assert "角色彼此" in transient
    assert "不要把問題丟回觀眾" in transient
    assert "回應留言" in transient


def test_youtube_live_director_transient_prompt_respects_disabled_dialogue_expansion():
    body = ChatSyncRequest(content="請自然延續直播。")

    transient = _transient_user_content_for_external_context(
        body,
        {
            "source": "youtube_live_director",
            "director_dialogue_expansion_enabled": False,
        },
    )

    assert "角色彼此" not in transient
    assert "不要要求其他角色接話" in transient
    assert "不要把問題丟回觀眾" in transient


def test_youtube_live_director_transient_prompt_includes_public_turn_instruction():
    body = ChatSyncRequest(
        content=(
            "直播開場任務：請先完成固定開場白與自我介紹。\n"
            "固定開場自我介紹：\n"
            "- 可可：本小姐是今天的直播主持可可。"
        ),
    )

    transient = _transient_user_content_for_external_context(
        body,
        {"source": "youtube_live_director"},
    )

    assert "直播開場任務" in transient
    assert "固定開場自我介紹" in transient
    assert "本小姐是今天的直播主持可可" in transient


def test_group_followup_prompt_has_youtube_live_no_audience_handoff_exception():
    prompts = json.loads(Path("prompts_default.json").read_text(encoding="utf-8"))
    template = prompts["group_followup_user"]["template"]

    assert "直播自主推進" in template
    assert "不要把問題丟回觀眾" in template
    assert "不可把問題丟回觀眾" in template


def test_youtube_live_chat_system_suffix_contains_style_desync_rule():
    prompts = json.loads(Path("prompts_default.json").read_text(encoding="utf-8"))
    template = prompts["chat_system_suffix_youtube_live"]["template"]

    assert "直播語言規則" in template
    assert "reply 欄位必須使用繁體中文（zh-TW）" in template
    assert "禁止使用簡體字" in template
    assert "句型去同步規則" in template
    assert "只參考前文的意思與事實" in template
    assert "不要模仿前文的標點、用詞、節奏、句型或修辭骨架" in template
    assert "表層格式、稱呼或句式" in template
    assert "——" not in template
    assert "諸位" not in template


def test_youtube_live_chat_system_suffix_keeps_reply_rules_outside_json_example():
    prompts = json.loads(Path("prompts_default.json").read_text(encoding="utf-8"))
    template = prompts["chat_system_suffix_youtube_live"]["template"]

    assert "<required_output_format>" in template
    assert "<reply_content_rules>" in template
    assert '"reply": "顯示給使用者看的自然語言回覆（螢幕字幕文字）"' in template
    required_output = template.split("<reply_content_rules>", 1)[0]
    assert '文字與語氣規則：{speech_instruction}' not in required_output


def test_group_followup_prompt_contains_primary_target_style_desync_rule():
    prompts = json.loads(Path("prompts_default.json").read_text(encoding="utf-8"))
    template = prompts["group_followup_user"]["template"]

    assert "內容可承接，表層句型不可承接" in template
    assert "不要模仿 primary_reply_target.content 的標點、用詞" in template
    assert "必要專有名詞、對象名稱、人物或角色名與已驗證事實可以保留" in template
    assert "——" not in template
    assert "諸位" not in template


def test_group_followup_prompt_uses_generic_hard_duplicate_rules():
    prompts = json.loads(Path("prompts_default.json").read_text(encoding="utf-8"))
    template = prompts["group_followup_user"]["template"]

    assert "你正在接續同一段直播討論" in template
    assert "不得重述 primary_reply_target.content 的主張、結論或理由" in template
    assert "不得使用同一資料卡或同一 evidence entry 的同一個事實點再次展開" in template
    assert "不得把上一位角色的句子改寫成自己的語氣" in template
    assert "若無新資訊，只能三選一" in template
    assert "簡短反應" in template
    assert "轉譯成觀眾視角" in template
    assert "推進到下一段" in template
    assert "判定為重複的情況" in template
    assert "使用相同原因解釋同一事件、數據、排名、趨勢或結論變化" in template
    assert "再次說明同一對象的同一屬性、背景、優勢、限制、受眾基本盤或表現結果" in template
    assert "再次使用同功能比喻" in template
    assert "再次總結相同結論" in template
    assert "若 primary_reply_target.content 已經完成本輪目標，請不要補分析" in template
    assert "動畫" not in template
    assert "作品的作畫、世界觀" not in template


def test_youtube_live_group_followup_instruction_includes_live_rules_block():
    instruction = build_group_followup_instruction(
        {
            "user_prompt_original": "請自然延續直播。",
            "last_character_name": "可可",
            "last_reply": "大家最在意第 4 話的節奏吧？",
            "conversation_intent": "continue_group_discussion",
            "routing_action": "repeat_speaker_reply_to_ai",
        },
        "請自然延續直播。",
        {"external_chat_context": {"source": "youtube_live_director"}},
    )

    assert "youtube_live_group_context:" in instruction
    assert "直播基礎規則" in instruction
    assert "不要把問題丟回觀眾" in instruction
    assert "不要提到 prompt" in instruction


def test_youtube_live_group_followup_instruction_includes_reply_task_block():
    instruction = build_group_followup_instruction(
        {
            "user_prompt_original": "請自然延續直播。",
            "last_character_name": "可可",
            "last_reply": "Anime Corner 週榜到底該怎麼用比較好？",
            "conversation_intent": "continue_group_discussion",
            "routing_action": "new_speaker_reply_to_ai",
            "live_episode_reply_task": {
                "stage": "reaction_translate_or_new_angle",
                "turn_reply_index": 2,
                "max_role_replies": 2,
                "previous_claims": ["Anime Corner 週榜只是即時快照"],
                "previous_speaker_name": "可可",
                "previous_reply": "Anime Corner 週榜到底該怎麼用比較好？",
            },
        },
        "請自然延續直播。",
        {"external_chat_context": {"source": "youtube_live_director"}},
    )

    assert "live_episode_reply_task:" in instruction
    assert "本次發言任務" in instruction
    assert "第 2 位角色只能反應、轉譯、補一個新角度或推進" in instruction
    assert "不得完整覆蓋整個段落目標" in instruction
    assert "不得重述上一位角色的主觀點" in instruction
    assert "Anime Corner 週榜只是即時快照" in instruction


def test_youtube_live_group_followup_instruction_omits_duplicate_hosting_rules():
    instruction = build_group_followup_instruction(
        {
            "last_character_name": "可可",
            "last_reply": "這段作畫為什麼被大家討論？",
            "user_prompt_original": "請自然延續直播。",
            "conversation_intent": "continue_group_discussion",
            "routing_action": "repeat_speaker_reply_to_ai",
        },
        "請自然延續直播。",
        {
            "external_chat_context": {
                "source": "youtube_live_director",
                "live_hosting": {
                    "host_interaction_rules": "可可提出觀眾視角；白蓮負責分析收束。",
                    "program_segment_plan": "事件 Hook\n核心分析",
                    "program_segment_turns": 3,
                    "current_segment": {"index": 1, "name": "核心分析"},
                },
            },
        },
    )

    assert "youtube_live_hosting_context:" not in instruction
    assert "可可提出觀眾視角" not in instruction
    assert "目前節目段落：核心分析" not in instruction
    assert "youtube_live_group_context:" in instruction


def test_youtube_live_chat_external_context_keeps_short_batch_round_limit():
    session = _SessionStub(["char-a", "char-b", "char-c", "char-d"])

    limit = _external_context_group_turn_limit(session, {"source": "youtube_live"})

    assert limit == 3
