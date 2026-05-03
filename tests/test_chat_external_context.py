from api.models.requests import ChatSyncRequest
from api.routers.chat_rest import _build_external_context_visible_event, _resolve_external_context_payload
from core.chat_orchestrator.dialogue_format import format_history_for_llm


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
    assert "另有 2 則未顯示。" in content
    assert debug_info["event_type"] == "youtube_live_batch"
    assert debug_info["llm_visible"] is False

    formatted = format_history_for_llm([
        {"role": "system_event", "content": content, "debug_info": debug_info},
        {"role": "user", "content": "hello"},
    ])
    assert formatted == [{"role": "user", "content": "hello"}]
