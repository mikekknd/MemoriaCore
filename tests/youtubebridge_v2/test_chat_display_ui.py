import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
UI_ROOT = ROOT / "YouTubeBridgeV2" / "static" / "chat-display"
UI_MODULE = UI_ROOT / "chat-display.js"
UI_HTML = UI_ROOT / "index.html"
UI_CSS = UI_ROOT / "chat-display.css"


def _run_node_json(source: str):
    code = f"""
import * as ui from {json.dumps(UI_MODULE.as_uri())};
{source}
"""
    result = subprocess.run(
        ["node", "--input-type=module", "-"],
        input=code,
        text=True,
        capture_output=True,
        cwd=ROOT,
        check=True,
    )
    return json.loads(result.stdout)


def _assert_no_private_payload(value):
    text = repr(value).lower()
    for forbidden in (
        "hidden_prompt",
        "raw_prompt",
        "raw_payload",
        "raw_memoriacore_payload",
        "raw_adapter_payload",
        "raw_topic_pack",
        "topic_pack_fact_cards",
        "raw_factcard",
        "operator_controls",
        "operator_only",
        "diagnostics",
        "authorization",
        "access_token",
        "secret",
        "token",
    ):
        assert forbidden not in text


def test_chat_display_renders_audience_message():
    result = _run_node_json(
        """
const event = ui.DisplayMessageEvent.fromEvent({
  event_type: "audience_message",
  public_payload: {
    author_display_name: "Mika",
    message_text: "Hello bridge",
    timestamp: "12:34"
  }
});
console.log(JSON.stringify({event, html: event.render()}));
"""
    )

    assert result["event"]["authorDisplayName"] == "Mika"
    assert result["event"]["messageText"] == "Hello bridge"
    assert 'data-testid="audience-message"' in result["html"]
    assert "Mika" in result["html"]
    assert "Hello bridge" in result["html"]


def test_chat_display_renders_display_flags_for_audience_message():
    result = _run_node_json(
        """
const event = ui.DisplayMessageEvent.fromEvent({
  event_type: "audience_message",
  sequence: 1,
  public_payload: {
    author_display_name: "Mika",
    message_text: "Pinned member note",
    flags: {
      member: true,
      moderator: true,
      unknown_private_hint: true,
      hidden_prompt: "do not expose"
    }
  }
});
console.log(JSON.stringify({event, html: event.render()}));
"""
    )

    assert "Member" in result["html"]
    assert "Moderator" in result["html"]
    assert "unknown_private_hint" not in result["html"]
    _assert_no_private_payload(result)


def test_chat_display_renders_character_response_with_role_label():
    result = _run_node_json(
        """
const event = ui.DisplayCharacterResponseEvent.fromEvent({
  event_type: "character_response",
  public_payload: {
    character_name: "Luna",
    role_label: "Host",
    response_text: "Welcome back",
    phase: "planned_show",
    presentation: {voice_state: "speaking", visual_state: "focus"}
  }
});
console.log(JSON.stringify({event, html: event.render()}));
"""
    )

    assert result["event"]["characterName"] == "Luna"
    assert result["event"]["roleLabel"] == "Host"
    assert result["event"]["presentation"]["voiceState"] == "speaking"
    assert 'data-testid="character-response"' in result["html"]
    assert 'data-testid="role-label"' in result["html"]
    assert "Welcome back" in result["html"]


def test_chat_display_renders_super_chat_metadata():
    result = _run_node_json(
        """
const event = ui.DisplaySuperChatEvent.fromEvent({
  event_type: "super_chat",
  public_payload: {
    author_display_name: "Rin",
    message_text: "Great show",
    amount_display_string: "NT$150",
    currency: "TWD",
    acknowledgement_status: "pending"
  }
});
console.log(JSON.stringify({event, html: event.render()}));
"""
    )

    assert result["event"]["amountDisplayString"] == "NT$150"
    assert result["event"]["acknowledgementStatus"] == "pending"
    assert 'data-testid="super-chat"' in result["html"]
    assert "NT$150" in result["html"]
    assert "Great show" in result["html"]


def test_chat_display_renders_aftertalk_status_banner():
    result = _run_node_json(
        """
const event = ui.DisplaySystemStateEvent.fromEvent({
  event_type: "system_state",
  public_payload: {
    phase: "aftertalk",
    aftertalk_status: "active",
    message: "Aftertalk is live"
  }
});
console.log(JSON.stringify({event, html: event.render()}));
"""
    )

    assert result["event"]["phase"] == "aftertalk"
    assert result["event"]["statusLabel"] == "Aftertalk"
    assert 'data-testid="status-banner"' in result["html"]
    assert "Aftertalk is live" in result["html"]


def test_chat_display_renders_closing_status_banner():
    result = _run_node_json(
        """
const event = ui.DisplaySystemStateEvent.fromEvent({
  event_type: "system_state",
  public_payload: {
    phase: "closing",
    closing_status: "finalizing",
    message: "Closing sequence"
  }
});
console.log(JSON.stringify({event, html: event.render()}));
"""
    )

    assert result["event"]["phase"] == "closing"
    assert result["event"]["statusLabel"] == "Closing"
    assert 'data-phase="closing"' in result["html"]
    assert "Closing sequence" in result["html"]


def test_chat_display_renders_existing_closing_status_event_shape():
    result = _run_node_json(
        """
const html = ui.renderDisplayEvent({
  event_type: "closing_status",
  session_id: "session-1",
  status: "complete",
  message: "closing complete",
  public_summary: {
    closing_completion_status: "complete"
  },
  metadata: {
    closing_reason: "duration_reached"
  }
});
console.log(JSON.stringify({html}));
"""
    )

    assert 'data-testid="status-banner"' in result["html"]
    assert 'data-phase="closing"' in result["html"]
    assert "closing complete" in result["html"]
    assert 'data-testid="display-fallback"' not in result["html"]


def test_chat_display_orders_events_by_sequence_when_rendering_replay():
    result = _run_node_json(
        """
const html = ui.renderDisplayEvents([
  {
    event_type: "audience_message",
    sequence: 2,
    public_payload: {author_display_name: "Second", message_text: "second"}
  },
  {
    event_type: "audience_message",
    sequence: 1,
    public_payload: {author_display_name: "First", message_text: "first"}
  }
]);
console.log(JSON.stringify({html}));
"""
    )

    assert result["html"].index("First") < result["html"].index("Second")


def test_malformed_display_event_uses_safe_fallback():
    result = _run_node_json(
        """
const html = ui.renderDisplayEvent({event_type: "audience_message", public_payload: null});
console.log(JSON.stringify({html}));
"""
    )

    assert 'data-testid="display-fallback"' in result["html"]
    assert "Display event unavailable" in result["html"]


def test_render_display_events_keeps_safe_fallback_for_malformed_items():
    result = _run_node_json(
        """
const html = ui.renderDisplayEvents([
  {event_type: "audience_message", public_payload: null},
  {
    event_type: "audience_message",
    sequence: 2,
    public_payload: {author_display_name: "Mika", message_text: "still visible"}
  }
]);
console.log(JSON.stringify({html}));
"""
    )

    assert 'data-testid="display-fallback"' in result["html"]
    assert "still visible" in result["html"]


def test_render_display_events_keeps_safe_fallback_for_unsupported_items():
    result = _run_node_json(
        """
let html = "";
try {
  html = ui.renderDisplayEvents([
    {event_type: "unsupported_event", public_payload: {message_text: "bad"}},
    {
      event_type: "audience_message",
      sequence: 2,
      public_payload: {author_display_name: "Mika", message_text: "still visible"}
    }
  ]);
} catch (error) {
  html = `THREW:${error.message}`;
}
console.log(JSON.stringify({html}));
"""
    )

    assert not result["html"].startswith("THREW:")
    assert 'data-testid="display-fallback"' in result["html"]
    assert "still visible" in result["html"]


def test_display_permission_does_not_call_control_api():
    result = _run_node_json(
        """
const sources = [];
class FakeSource {
  constructor(url) {
    this.url = url;
    sources.push(this);
  }
}
const events = [];
const stale = [];
const source = ui.connectDisplayStream({
  sessionId: "session 1",
  eventSourceFactory: (url) => new FakeSource(url),
  onEvent: (event) => events.push(event),
  onStale: (state) => stale.push(state)
});
source.onmessage({
  data: JSON.stringify({
    event_type: "audience_message",
    session_id: "session 1",
    public_payload: {author_display_name: "Mika", message_text: "hi"}
  })
});
source.onerror(new Error("disconnect"));
console.log(JSON.stringify({url: source.url, events, stale}));
"""
    )

    assert result["url"] == "/v2/sessions/session%201/display-stream"
    assert result["events"][0]["messageText"] == "hi"
    assert result["stale"][0]["stream_state"] == "stale"

    source = UI_MODULE.read_text(encoding="utf-8")
    assert "/aftertalk-policy" not in source
    assert "/manual-close" not in source
    assert "/operator-stream" not in source
    assert re.search(r"method\s*:\s*['\"]POST", source) is None


def test_chat_display_missing_session_id_renders_system_banner():
    result = _run_node_json(
        """
const root = {innerHTML: ""};
ui.mountChatDisplay({root, sessionId: ""});
console.log(JSON.stringify({html: root.innerHTML}));
"""
    )

    assert 'data-testid="status-banner"' in result["html"]
    assert "Missing session_id" in result["html"]


def test_hidden_prompt_and_operator_metadata_are_not_rendered():
    result = _run_node_json(
        """
const event = ui.DisplayCharacterResponseEvent.fromEvent({
  event_type: "character_response",
  public_payload: {
    character_name: "Luna",
    role_label: "Host",
    response_text: "Visible line",
    hidden_prompt: "do not expose",
    raw_payload: {authorization: "Bearer secret"},
    operator_controls: {manual_close: true},
    operator_only_metadata: {diagnostics: "secret"},
    presentation: {
      voice_state: "speaking",
      raw_memoriacore_payload: "secret"
    }
  }
});
console.log(JSON.stringify({event, html: event.render()}));
"""
    )

    assert "Visible line" in result["html"]
    assert result["event"]["presentation"] == {
        "voiceState": "speaking",
        "visualState": "",
    }
    _assert_no_private_payload(result)


def test_chat_display_static_entrypoint_links_assets_and_i18n():
    assert UI_HTML.exists()
    assert UI_CSS.exists()
    assert UI_MODULE.exists()

    html = UI_HTML.read_text(encoding="utf-8")

    assert 'chat-display.css' in html
    assert 'chat-display.js' in html
    assert 'id="chatDisplayRoot"' in html
    assert '/static/shared/i18n.js' in html
    assert 'data-i18n="youtubebridge_v2.chat_display.loading"' in html


def test_chat_display_i18n_keys_are_registered():
    zh = json.loads((ROOT / "static" / "locales" / "zh-TW.json").read_text(encoding="utf-8"))
    en = json.loads((ROOT / "static" / "locales" / "en-US.json").read_text(encoding="utf-8"))
    source = UI_MODULE.read_text(encoding="utf-8")

    for key in (
        "youtubebridge_v2.chat_display.title",
        "youtubebridge_v2.chat_display.loading",
        "youtubebridge_v2.chat_display.audience",
        "youtubebridge_v2.chat_display.character",
        "youtubebridge_v2.chat_display.super_chat",
        "youtubebridge_v2.chat_display.aftertalk",
        "youtubebridge_v2.chat_display.closing",
        "youtubebridge_v2.chat_display.fallback",
        "youtubebridge_v2.chat_display.stream_stale",
        "youtubebridge_v2.chat_display.missing_session_id",
        "youtubebridge_v2.chat_display.flag_member",
        "youtubebridge_v2.chat_display.flag_moderator",
        "youtubebridge_v2.chat_display.flag_highlighted",
        "youtubebridge_v2.chat_display.flag_pinned",
        "youtubebridge_v2.chat_display.flag_held_for_review",
        "youtubebridge_v2.chat_display.flag_verified",
    ):
        assert key in zh
        assert key in en
    assert "MCI18N.t" in source


def test_chat_display_static_entrypoint_is_served_by_api_app():
    from fastapi.testclient import TestClient

    from api.main import app

    client = TestClient(app)
    response = client.get("/v2/static/chat-display/index.html")

    assert response.status_code == 200
    assert 'id="chatDisplayRoot"' in response.text


def test_chat_display_does_not_import_runtime_adapter_storage_or_control_api():
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (UI_HTML, UI_CSS, UI_MODULE)
        if path.exists()
    )

    forbidden_patterns = (
        r"from\s+['\"].*runtime",
        r"from\s+['\"].*adapters",
        r"from\s+['\"].*storage",
        r"YouTubeBridgeV2[\\/](runtime|adapters|storage)",
        r"/v2/(adapters|storage)/",
        r"/aftertalk-policy",
        r"/manual-close",
        r"/operator-stream",
        r"\bsqlite3\b",
        r"\baiosqlite\b",
    )
    matches = [
        pattern
        for pattern in forbidden_patterns
        if re.search(pattern, source, flags=re.IGNORECASE)
    ]
    assert matches == []
