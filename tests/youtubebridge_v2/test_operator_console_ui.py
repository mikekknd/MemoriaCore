import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
UI_ROOT = ROOT / "YouTubeBridgeV2" / "static" / "operator-console"
UI_MODULE = UI_ROOT / "operator-console.js"
UI_HTML = UI_ROOT / "index.html"
UI_CSS = UI_ROOT / "operator-console.css"


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
        "authorization",
        "access_token",
        "secret",
        "token",
    ):
        assert forbidden not in text


def test_operator_console_renders_current_phase():
    result = _run_node_json(
        """
const view = ui.OperatorSessionStatusView.fromStatus({
  session_id: "session-1",
  phase: "aftertalk",
  aftertalk_policy: "auto",
  closing_completion_status: "not_started"
});
const html = ui.renderOperatorConsole(view);
console.log(JSON.stringify({view, html}));
"""
    )

    assert result["view"]["phase"] == "aftertalk"
    assert result["view"]["phaseLabel"] == "Aftertalk"
    assert 'data-testid="phase-value"' in result["html"]
    assert 'data-phase="aftertalk"' in result["html"]


def test_missing_permission_group_defaults_to_display_only():
    result = _run_node_json(
        """
const view = ui.OperatorSessionStatusView.fromStatus({
  session_id: "session-1",
  phase: "aftertalk",
  aftertalk_policy: "auto"
});
console.log(JSON.stringify({view, html: ui.renderOperatorConsole(view)}));
"""
    )

    assert result["view"]["permissionGroup"] == "display"
    assert result["view"]["canControl"] is False
    assert 'data-testid="operator-controls"' not in result["html"]
    assert 'data-testid="read-only-permission"' in result["html"]


def test_operator_console_renders_live_episode_plan_progress():
    result = _run_node_json(
        """
const view = ui.OperatorSessionStatusView.fromStatus({
  session_id: "session-1",
  phase: "planned_show",
  live_episode_plan: {
    plan_id: "plan-1",
    current_turn_index: 2,
    total_turns: 5,
    current_turn_title: "Opening recap",
    status: "running"
  }
});
console.log(JSON.stringify({view, html: ui.renderOperatorConsole(view)}));
"""
    )

    assert result["view"]["planProgress"]["label"] == "2 / 5"
    assert result["view"]["planProgress"]["percent"] == 40
    assert "Opening recap" in result["html"]
    assert 'data-testid="plan-progress"' in result["html"]


def test_operator_console_renders_aftertalk_policy_status():
    result = _run_node_json(
        """
const view = ui.OperatorSessionStatusView.fromStatus({
  session_id: "session-1",
  phase: "aftertalk",
  aftertalk_policy: "auto",
  permission_group: "operator"
});
console.log(JSON.stringify({view, html: ui.renderOperatorConsole(view)}));
"""
    )

    assert result["view"]["aftertalkPolicy"] == "auto"
    assert 'data-testid="aftertalk-policy"' in result["html"]
    assert 'data-testid="aftertalk-state"' in result["html"]


def test_aftertalk_toggle_sends_policy_update():
    result = _run_node_json(
        """
const calls = [];
const fetchImpl = async (url, options) => {
  calls.push({url, method: options.method, body: JSON.parse(options.body)});
  return {ok: true, json: async () => ({status: "ok"})};
};
const response = await ui.AftertalkPolicyControl.send({
  sessionId: "session-1",
  policy: "disabled",
  fetchImpl,
  commandIdFactory: () => "cmd-policy"
});
console.log(JSON.stringify({calls, response}));
"""
    )

    assert result["calls"] == [
        {
            "url": "/v2/sessions/session-1/aftertalk-policy",
            "method": "POST",
            "body": {
                "command_id": "cmd-policy",
                "aftertalk_policy": "disabled",
            },
        }
    ]


def test_remaining_time_is_displayed_from_phase_status():
    result = _run_node_json(
        """
const view = ui.OperatorSessionStatusView.fromStatus({
  session_id: "session-1",
  phase: "aftertalk",
  duration_summary: {remaining_time_seconds: 185},
  closing_completion_status: "not_started"
});
console.log(JSON.stringify({view, html: ui.renderOperatorConsole(view)}));
"""
    )

    assert result["view"]["remainingTimeLabel"] == "03:05"
    assert 'data-testid="remaining-time"' in result["html"]
    assert "03:05" in result["html"]


def test_manual_close_button_sends_manual_close_command():
    result = _run_node_json(
        """
const calls = [];
const fetchImpl = async (url, options) => {
  calls.push({url, method: options.method, body: JSON.parse(options.body)});
  return {ok: true, json: async () => ({phase: "closing"})};
};
const response = await ui.ManualCloseCommand.send({
  sessionId: "session-1",
  reason: "operator",
  fetchImpl,
  commandIdFactory: () => "cmd-close"
});
console.log(JSON.stringify({calls, response}));
"""
    )

    assert result["calls"] == [
        {
            "url": "/v2/sessions/session-1/manual-close",
            "method": "POST",
            "body": {
                "command_id": "cmd-close",
                "reason": "operator",
            },
        }
    ]


def test_controls_disable_while_action_is_in_flight():
    result = _run_node_json(
        """
const view = ui.OperatorSessionStatusView.fromStatus({
  session_id: "session-1",
  phase: "aftertalk",
  aftertalk_policy: "auto",
  closing_completion_status: "not_started",
  permission_group: "operator"
}, {inFlightAction: "manual_close"});
console.log(JSON.stringify({view, html: ui.renderOperatorConsole(view)}));
"""
    )

    assert result["view"]["controls"]["manualCloseDisabled"] is True
    assert result["view"]["controls"]["aftertalkDisabled"] is True
    assert 'data-testid="manual-close-button" disabled' in result["html"]


def test_error_banner_renders_sanitized_error():
    result = _run_node_json(
        """
const banner = ui.OperatorDiagnosticBanner.fromError({
  message: "hidden_prompt raw_payload token must not leak",
  raw_payload: {authorization: "Bearer secret"}
});
console.log(JSON.stringify({banner, html: banner.render()}));
"""
    )

    assert result["banner"]["message"] == "request failed"
    assert 'data-testid="error-banner"' in result["html"]
    _assert_no_private_payload(result)


def test_diagnostics_render_sanitized_banner():
    result = _run_node_json(
        """
const view = ui.OperatorSessionStatusView.fromStatus({
  session_id: "session-1",
  phase: "planned_show",
  diagnostics: {
    message: "operator stream disconnected",
    raw_payload: {hidden_prompt: "must not leak"}
  }
});
console.log(JSON.stringify({view, html: ui.renderOperatorConsole(view)}));
"""
    )

    assert result["view"]["errorBanner"]["message"] == "operator stream disconnected"
    assert 'data-testid="error-banner"' in result["html"]
    _assert_no_private_payload(result)


def test_display_only_permission_hides_operator_controls():
    result = _run_node_json(
        """
const view = ui.OperatorSessionStatusView.fromStatus({
  session_id: "session-1",
  phase: "aftertalk",
  aftertalk_policy: "auto",
  permission_group: "display"
});
console.log(JSON.stringify({view, html: ui.renderOperatorConsole(view)}));
"""
    )

    assert result["view"]["canControl"] is False
    assert 'data-testid="operator-controls"' not in result["html"]
    assert 'data-testid="read-only-permission"' in result["html"]


def test_operator_stream_updates_status_and_reports_stale_state():
    result = _run_node_json(
        """
const sources = [];
class FakeSource {
  constructor(url) {
    this.url = url;
    sources.push(this);
  }
}
const statuses = [];
const stale = [];
const source = ui.connectOperatorStream({
  sessionId: "session 1",
  eventSourceFactory: (url) => new FakeSource(url),
  onStatus: (status) => statuses.push(status),
  onStale: (status) => stale.push(status),
});
source.onmessage({
  data: JSON.stringify({
    event_type: "operator_status",
    session_id: "session 1",
    payload: {phase: "aftertalk", permission_group: "operator"}
  })
});
source.onerror(new Error("disconnect"));
console.log(JSON.stringify({url: source.url, statuses, stale}));
"""
    )

    assert result["url"] == "/v2/sessions/session%201/operator-stream"
    assert result["statuses"][0]["phase"] == "aftertalk"
    assert result["statuses"][0]["permission_group"] == "operator"
    assert result["stale"][0]["stream_state"] == "stale"


def test_missing_session_id_renders_diagnostic():
    result = _run_node_json(
        """
const root = {innerHTML: ""};
ui.mountOperatorConsole({root});
console.log(JSON.stringify({html: root.innerHTML}));
"""
    )

    assert 'data-testid="error-banner"' in result["html"]
    assert "session" in result["html"].lower()


def test_operator_console_static_entrypoint_links_assets():
    assert UI_HTML.exists()
    assert UI_CSS.exists()
    assert UI_MODULE.exists()

    html = UI_HTML.read_text(encoding="utf-8")

    assert 'operator-console.css' in html
    assert 'operator-console.js' in html
    assert 'id="operatorConsoleRoot"' in html
    assert '/static/shared/i18n.js' in html
    assert 'data-i18n="youtubebridge_v2.operator_console.loading"' in html


def test_operator_console_i18n_keys_are_registered():
    zh = json.loads((ROOT / "static" / "locales" / "zh-TW.json").read_text(encoding="utf-8"))
    en = json.loads((ROOT / "static" / "locales" / "en-US.json").read_text(encoding="utf-8"))
    source = UI_MODULE.read_text(encoding="utf-8")

    for key in (
        "youtubebridge_v2.operator_console.title",
        "youtubebridge_v2.operator_console.phase",
        "youtubebridge_v2.operator_console.phase_unknown",
        "youtubebridge_v2.operator_console.manual_close",
        "youtubebridge_v2.operator_console.read_only",
        "youtubebridge_v2.operator_console.stream_connected",
        "youtubebridge_v2.operator_console.stream_stale",
        "youtubebridge_v2.operator_console.diagnostics_unavailable",
        "youtubebridge_v2.operator_console.missing_session_id",
    ):
        assert key in zh
        assert key in en
    assert "MCI18N.t" in source


def test_operator_console_static_entrypoint_is_served_by_api_app():
    from fastapi.testclient import TestClient

    from api.main import app

    client = TestClient(app)
    response = client.get("/v2/static/operator-console/index.html")

    assert response.status_code == 200
    assert 'id="operatorConsoleRoot"' in response.text


def test_operator_console_api_dependencies_are_served_by_main_app(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from api.main import app
    import api.main as api_main
    from core.storage_manager import StorageManager

    storage = StorageManager(
        prefs_file=str(tmp_path / "prefs.json"),
        history_file=str(tmp_path / "history.json"),
        persona_snapshot_db_path=str(tmp_path / "persona_snapshots.db"),
        youtube_bridge_v2_db_path=str(tmp_path / "youtubebridge_v2.db"),
    )
    monkeypatch.setattr(api_main, "get_storage", lambda: storage)
    monkeypatch.setattr(api_main, "_v2_composition_cache", None, raising=False)
    monkeypatch.setattr(api_main, "_v2_composition_storage_id", None, raising=False)
    client = TestClient(app, client=("127.0.0.1", 50000))

    create_response = client.post(
        "/v2/sessions",
        json={
            "command_id": "cmd-create",
            "session_id": "session-1",
            "aftertalk_policy": "auto",
        },
    )
    assert create_response.status_code == 200

    phase_response = client.get("/v2/sessions/session-1/phase")
    assert phase_response.status_code == 200
    assert phase_response.json()["session_id"] == "session-1"
    assert phase_response.json()["phase"] == "planned_show"
    assert "v2_runtime_not_configured" not in repr(phase_response.json())

    with client.stream("GET", "/v2/sessions/session-1/operator-stream") as stream_response:
        stream_response.read()
        text = stream_response.text

    assert stream_response.status_code == 200
    assert "operator_status" in text
    assert "planned_show" in text
    assert "runtime service is not configured" not in text
    _assert_no_private_payload(text)


def test_operator_console_does_not_import_runtime_adapter_or_storage():
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
        r"\bsqlite3\b",
        r"\baiosqlite\b",
    )
    matches = [
        pattern
        for pattern in forbidden_patterns
        if re.search(pattern, source, flags=re.IGNORECASE)
    ]
    assert matches == []
