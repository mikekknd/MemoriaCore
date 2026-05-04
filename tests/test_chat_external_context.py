from api.models.requests import ChatSyncRequest
from api.routers.chat_rest import (
    _build_external_context_visible_event,
    _memory_write_policy_for_request,
    _resolve_chat_display_content,
    _resolve_external_context_payload,
)
from core.chat_orchestrator.dialogue_format import format_history_for_llm
from core.chat_orchestrator.dataclasses import PipelineContext


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

    assert display == "導播推進直播流程。"
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
