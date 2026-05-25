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


def test_operator_console_renders_durable_session_identity_and_automation_state():
    result = _run_node_json(
        """
const view = ui.OperatorSessionStatusView.fromStatus({
  session_id: "session-1",
  phase: "planned_show",
  public_summary: {
    title: "May Showcase",
    plan_id: "plan-1",
    raw_payload: {token: "must not leak"}
  },
  automation_control: {
    enabled: true,
    paused: true,
    reason: "operator pause",
    raw_payload: {authorization: "Bearer secret"}
  }
});
const html = ui.renderOperatorConsole(view);
console.log(JSON.stringify({view, html}));
"""
    )

    assert result["view"]["statusTitle"] == "May Showcase"
    assert result["view"]["sessionId"] == "session-1"
    assert result["view"]["automationControl"] == {
        "enabled": True,
        "paused": True,
        "reason": "operator pause",
    }
    assert result["view"]["automationStateLabel"] == "paused"
    assert 'data-testid="status-title"' in result["html"]
    assert 'data-testid="session-id"' in result["html"]
    assert 'data-testid="automation-state"' in result["html"]
    assert "May Showcase" in result["html"]
    assert "session-1" in result["html"]
    assert "operator pause" in result["html"]
    _assert_no_private_payload(result)


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


def test_operator_console_renders_runtime_control_inputs_for_operator():
    result = _run_node_json(
        """
const view = ui.OperatorSessionStatusView.fromStatus({
  session_id: "session-1",
  phase: "planned_show",
  permission_group: "operator"
});
const html = ui.renderOperatorConsole(view);
console.log(JSON.stringify({view, html}));
"""
    )

    assert 'data-testid="plan-json-input"' in result["html"]
    assert 'data-testid="bind-plan-button"' in result["html"]
    assert 'data-testid="tick-button"' in result["html"]
    assert 'data-testid="manual-close-button"' in result["html"]


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


def test_aftertalk_toggle_reloads_durable_status_after_update():
    result = _run_node_json(
        """
const calls = [];
let changeHandler = null;
const elements = {
  aftertalk: {
    checked: false,
    addEventListener: (_event, handler) => { changeHandler = handler; }
  },
  tick: null,
  bindPlan: null,
  close: null
};
const root = {
  innerHTML: "",
  querySelector: (selector) => {
    if (selector === "[data-testid='aftertalk-toggle']") return elements.aftertalk;
    if (selector === "[data-testid='tick-button']") return elements.tick;
    if (selector === "[data-testid='bind-plan-button']") return elements.bindPlan;
    if (selector === "[data-testid='manual-close-button']") return elements.close;
    return null;
  }
};
const fetchImpl = async (url, options = {}) => {
  calls.push({url, method: options.method || "GET", body: options.body ? JSON.parse(options.body) : null});
  if (options.method === "POST") {
    return {ok: true, json: async () => ({status: "ok"})};
  }
  return {
    ok: true,
    json: async () => ({
      session_id: "session-1",
      phase: "planned_show",
      permission_group: "operator",
      aftertalk_policy: "disabled",
      public_summary: {title: "Reloaded Policy"}
    })
  };
};
ui.mountOperatorConsole({
  root,
  sessionId: "session-1",
  fetchImpl,
  eventSourceFactory: () => null,
  initialStatus: {
    session_id: "session-1",
    phase: "planned_show",
    permission_group: "operator",
    aftertalk_policy: "auto"
  }
});
await changeHandler();
console.log(JSON.stringify({calls, html: root.innerHTML}));
"""
    )

    assert result["calls"][0]["url"] == "/v2/sessions/session-1/aftertalk-policy"
    assert result["calls"][0]["method"] == "POST"
    assert result["calls"][0]["body"]["aftertalk_policy"] == "disabled"
    assert result["calls"][1] == {
        "url": "/v2/sessions/session-1",
        "method": "GET",
        "body": None,
    }
    assert "Reloaded Policy" in result["html"]
    assert "disabled" in result["html"]


def test_create_session_command_sends_create_request():
    result = _run_node_json(
        """
const calls = [];
const fetchImpl = async (url, options) => {
  calls.push({url, method: options.method, body: JSON.parse(options.body)});
  return {ok: true, json: async () => ({status: "ok", session_id: "session-1"})};
};
const response = await ui.CreateSessionCommand.send({
  sessionId: "session-1",
  aftertalkPolicy: "auto",
  fetchImpl,
  commandIdFactory: () => "cmd-create"
});
console.log(JSON.stringify({calls, response}));
"""
    )

    assert result["calls"] == [
        {
            "url": "/v2/sessions",
            "method": "POST",
            "body": {
                "command_id": "cmd-create",
                "session_id": "session-1",
                "aftertalk_policy": "auto",
            },
        }
    ]


def test_bind_plan_command_sends_plan_request_without_private_payload():
    result = _run_node_json(
        """
const calls = [];
const fetchImpl = async (url, options) => {
  calls.push({url, method: options.method, body: JSON.parse(options.body)});
  return {ok: true, json: async () => ({status: "ok"})};
};
const response = await ui.BindPlanCommand.send({
  sessionId: "session-1",
  plan: {
    plan_id: "plan-1",
    title: "Operator Plan",
    raw_payload: {token: "must not leak"},
    turns: []
  },
  fetchImpl,
  commandIdFactory: () => "cmd-bind"
});
console.log(JSON.stringify({calls, response}));
"""
    )

    assert result["calls"] == [
        {
            "url": "/v2/sessions/session-1/plan",
            "method": "POST",
            "body": {
                "command_id": "cmd-bind",
                "plan": {
                    "plan_id": "plan-1",
                    "title": "Operator Plan",
                    "turns": [],
                },
            },
        }
    ]
    _assert_no_private_payload(result)


def test_episode_plan_list_command_fetches_plan_packages_without_private_payload():
    result = _run_node_json(
        """
const calls = [];
const fetchImpl = async (url, options = {}) => {
  calls.push({url, method: options.method || "GET"});
  return {
    ok: true,
    json: async () => ({
      episode_plans: [{
        id: "Alpha",
        plan_id: "plan-alpha",
        title: "Alpha Show",
        folder: "Alpha",
        filename: "episode-plan.json",
        plan: {
          plan_id: "plan-alpha",
          title: "Alpha Show",
          turns: [],
          raw_payload: {token: "must not leak"}
        }
      }]
    })
  };
};
const plans = await ui.EpisodePlanListCommand.send({fetchImpl});
console.log(JSON.stringify({calls, plans}));
"""
    )

    assert result["calls"] == [{"url": "/v2/episode-plans", "method": "GET"}]
    assert result["plans"] == [
        {
            "id": "Alpha",
            "planId": "plan-alpha",
            "title": "Alpha Show",
            "folder": "Alpha",
            "filename": "episode-plan.json",
            "plan": {
                "plan_id": "plan-alpha",
                "title": "Alpha Show",
                "turns": [],
            },
        }
    ]
    _assert_no_private_payload(result)


def test_operator_console_renders_episode_plan_picker_for_operator():
    result = _run_node_json(
        """
const view = ui.OperatorSessionStatusView.fromStatus({
  session_id: "session-1",
  phase: "planned_show",
  permission_group: "operator",
  episode_plans: [{
    id: "Alpha",
    plan_id: "plan-alpha",
    title: "Alpha Show",
    folder: "Alpha",
    filename: "episode-plan.json",
    plan: {plan_id: "plan-alpha", title: "Alpha Show", turns: []}
  }]
});
const html = ui.renderOperatorConsole(view);
console.log(JSON.stringify({view, html}));
"""
    )

    assert result["view"]["episodePlans"][0]["planId"] == "plan-alpha"
    assert 'data-testid="episode-plan-select"' in result["html"]
    assert 'data-testid="episode-plan-refresh-button"' in result["html"]
    assert 'data-testid="episode-plan-load-button"' in result["html"]
    assert "Alpha Show" in result["html"]
    _assert_no_private_payload(result)


def test_episode_plan_picker_loads_selected_plan_into_textarea():
    result = _run_node_json(
        """
let loadHandler = null;
let refreshHandler = null;
const elements = {
  select: {value: "plan-beta"},
  loadButton: {addEventListener: (_event, handler) => { loadHandler = handler; }},
  refreshButton: {addEventListener: (_event, handler) => { refreshHandler = handler; }},
  textarea: {value: ""}
};
const root = {
  innerHTML: "",
  querySelector: (selector) => {
    if (selector === "[data-testid='episode-plan-select']") return elements.select;
    if (selector === "[data-testid='episode-plan-load-button']") return elements.loadButton;
    if (selector === "[data-testid='episode-plan-refresh-button']") return elements.refreshButton;
    if (selector === "[data-testid='plan-json-input']") return elements.textarea;
    if (selector === "[data-testid='aftertalk-toggle']") return null;
    if (selector === "[data-testid='tick-button']") return null;
    if (selector === "[data-testid='bind-plan-button']") return null;
    if (selector === "[data-testid='manual-close-button']") return null;
    if (selector === "[data-testid='api-key-input']") return null;
    if (selector === "[data-testid='api-key-permission-select']") return null;
    if (selector === "[data-testid='api-key-create-button']") return null;
    if (selector === "[data-testid='api-key-refresh-button']") return null;
    return null;
  },
  querySelectorAll: () => []
};
const fetchImpl = async () => {
  throw new Error("load should not call API");
};
ui.mountOperatorConsole({
  root,
  sessionId: "session-1",
  fetchImpl,
  eventSourceFactory: () => null,
  initialStatus: {
    session_id: "session-1",
    phase: "planned_show",
    permission_group: "operator",
    episode_plans: [
      {
        id: "plan-alpha",
        plan_id: "plan-alpha",
        title: "Alpha Show",
        folder: "Alpha",
        filename: "episode-plan.json",
        plan: {plan_id: "plan-alpha", title: "Alpha Show", turns: []}
      },
      {
        id: "plan-beta",
        plan_id: "plan-beta",
        title: "Beta Show",
        folder: "Beta",
        filename: "episode-plan.json",
        plan: {plan_id: "plan-beta", title: "Beta Show", turns: [{id: "turn-1"}]}
      }
    ]
  }
});
await loadHandler();
console.log(JSON.stringify({
  textareaValue: elements.textarea.value,
  refreshBound: Boolean(refreshHandler),
}));
"""
    )

    loaded = json.loads(result["textareaValue"])
    assert loaded == {
        "plan_id": "plan-beta",
        "title": "Beta Show",
        "turns": [{"id": "turn-1"}],
    }
    assert result["refreshBound"] is True


def test_mount_operator_console_loads_episode_plan_packages_for_operator():
    result = _run_node_json(
        """
const calls = [];
const root = {
  innerHTML: "",
  querySelector: (selector) => selector === "[data-testid='episode-plan-select']" ? {} : null,
  querySelectorAll: () => []
};
const fetchImpl = async (url) => {
  calls.push(url);
  return {
    ok: true,
    json: async () => ({
      episode_plans: [{
        id: "Gamma",
        plan_id: "plan-gamma",
        title: "Gamma Show",
        folder: "Gamma",
        filename: "episode-plan.json",
        plan: {plan_id: "plan-gamma", title: "Gamma Show", turns: []}
      }]
    })
  };
};
ui.mountOperatorConsole({
  root,
  sessionId: "session-1",
  fetchImpl,
  eventSourceFactory: () => null,
  initialStatus: {
    session_id: "session-1",
    phase: "planned_show",
    permission_group: "operator"
  }
});
await new Promise((resolve) => setTimeout(resolve, 0));
console.log(JSON.stringify({calls, html: root.innerHTML}));
"""
    )

    assert result["calls"] == ["/v2/episode-plans"]
    assert "Gamma Show" in result["html"]


def test_operator_console_preserves_plan_picker_state_across_stream_rerender():
    result = _run_node_json(
        """
let source = null;
const makeElements = (html) => {
  const hasPlanOptions = html.includes('value="plan-alpha"') && html.includes('value="plan-beta"');
  return {
    episodePlanSelect: hasPlanOptions ? {value: "plan-alpha"} : null,
    planJsonInput: html.includes('data-testid="plan-json-input"') ? {value: ""} : null,
  };
};
const root = {
  _html: "",
  _elements: {},
  set innerHTML(value) {
    this._html = value;
    this._elements = makeElements(value);
  },
  get innerHTML() {
    return this._html;
  },
  querySelector(selector) {
    if (selector === "[data-testid='episode-plan-select']") return this._elements.episodePlanSelect;
    if (selector === "[data-testid='plan-json-input']") return this._elements.planJsonInput;
    if (selector === "[data-testid='episode-plan-load-button']") return {addEventListener: () => {}};
    if (selector === "[data-testid='episode-plan-refresh-button']") return {addEventListener: () => {}};
    if (selector === "[data-testid='aftertalk-toggle']") return null;
    if (selector === "[data-testid='tick-button']") return null;
    if (selector === "[data-testid='bind-plan-button']") return null;
    if (selector === "[data-testid='manual-close-button']") return null;
    if (selector === "[data-testid='api-key-input']") return null;
    if (selector === "[data-testid='api-key-permission-select']") return null;
    if (selector === "[data-testid='api-key-create-button']") return null;
    if (selector === "[data-testid='api-key-refresh-button']") return null;
    return null;
  },
  querySelectorAll: () => []
};
class FakeSource {
  constructor(url) {
    this.url = url;
    source = this;
  }
}
ui.mountOperatorConsole({
  root,
  sessionId: "session-1",
  fetchImpl: async () => ({ok: true, json: async () => ({})}),
  eventSourceFactory: (url) => new FakeSource(url),
  initialStatus: {
    session_id: "session-1",
    phase: "planned_show",
    permission_group: "operator",
    episode_plans: [
      {
        id: "plan-alpha",
        plan_id: "plan-alpha",
        title: "Alpha Show",
        folder: "Alpha",
        filename: "episode-plan.json",
        plan: {plan_id: "plan-alpha", title: "Alpha Show", turns: []}
      },
      {
        id: "plan-beta",
        plan_id: "plan-beta",
        title: "Beta Show",
        folder: "Beta",
        filename: "episode-plan.json",
        plan: {plan_id: "plan-beta", title: "Beta Show", turns: []}
      }
    ]
  }
});
root.querySelector("[data-testid='episode-plan-select']").value = "plan-beta";
root.querySelector("[data-testid='plan-json-input']").value = '{"plan_id":"draft"}';
source.onmessage({
  data: JSON.stringify({
    event_type: "operator_status",
    session_id: "session-1",
    payload: {phase: "aftertalk", permission_group: "operator"}
  })
});
console.log(JSON.stringify({
  selected: root.querySelector("[data-testid='episode-plan-select']").value,
  planJson: root.querySelector("[data-testid='plan-json-input']").value,
}));
"""
    )

    assert result == {
        "selected": "plan-beta",
        "planJson": '{"plan_id":"draft"}',
    }


def test_operator_console_ignores_duplicate_stream_status_without_rebuilding_controls():
    result = _run_node_json(
        """
let source = null;
let renderCount = 0;
const makeElements = (html) => {
  const hasPlanOptions = html.includes('value="plan-alpha"') && html.includes('value="plan-beta"');
  return {
    episodePlanSelect: hasPlanOptions ? {value: "plan-alpha"} : null,
    planJsonInput: html.includes('data-testid="plan-json-input"') ? {value: ""} : null,
  };
};
const root = {
  _html: "",
  _elements: {},
  set innerHTML(value) {
    renderCount += 1;
    this._html = value;
    this._elements = makeElements(value);
  },
  get innerHTML() {
    return this._html;
  },
  querySelector(selector) {
    if (selector === "[data-testid='episode-plan-select']") return this._elements.episodePlanSelect;
    if (selector === "[data-testid='plan-json-input']") return this._elements.planJsonInput;
    if (selector === "[data-testid='episode-plan-load-button']") return {addEventListener: () => {}};
    if (selector === "[data-testid='episode-plan-refresh-button']") return {addEventListener: () => {}};
    if (selector === "[data-testid='aftertalk-toggle']") return null;
    if (selector === "[data-testid='tick-button']") return null;
    if (selector === "[data-testid='bind-plan-button']") return null;
    if (selector === "[data-testid='manual-close-button']") return null;
    if (selector === "[data-testid='api-key-input']") return null;
    if (selector === "[data-testid='api-key-permission-select']") return null;
    if (selector === "[data-testid='api-key-create-button']") return null;
    if (selector === "[data-testid='api-key-refresh-button']") return null;
    return null;
  },
  querySelectorAll: () => []
};
class FakeSource {
  constructor(url) {
    this.url = url;
    source = this;
  }
}
ui.mountOperatorConsole({
  root,
  sessionId: "session-1",
  fetchImpl: async () => ({ok: true, json: async () => ({})}),
  eventSourceFactory: (url) => new FakeSource(url),
  initialStatus: {
    session_id: "session-1",
    phase: "planned_show",
    permission_group: "operator",
    episode_plans: [
      {
        id: "plan-alpha",
        plan_id: "plan-alpha",
        title: "Alpha Show",
        folder: "Alpha",
        filename: "episode-plan.json",
        plan: {plan_id: "plan-alpha", title: "Alpha Show", turns: []}
      },
      {
        id: "plan-beta",
        plan_id: "plan-beta",
        title: "Beta Show",
        folder: "Beta",
        filename: "episode-plan.json",
        plan: {plan_id: "plan-beta", title: "Beta Show", turns: []}
      }
    ]
  }
});
root.querySelector("[data-testid='episode-plan-select']").value = "plan-beta";
source.onmessage({
  data: JSON.stringify({
    event_type: "operator_status",
    session_id: "session-1",
    payload: {
      session_id: "session-1",
      phase: "planned_show",
      permission_group: "operator"
    }
  })
});
console.log(JSON.stringify({
  renderCount,
  selected: root.querySelector("[data-testid='episode-plan-select']").value,
}));
"""
    )

    assert result == {
        "renderCount": 1,
        "selected": "plan-beta",
    }


def test_tick_session_command_sends_tick_request():
    result = _run_node_json(
        """
const calls = [];
const fetchImpl = async (url, options) => {
  calls.push({url, method: options.method, body: JSON.parse(options.body)});
  return {ok: true, json: async () => ({status: "ok", phase: "aftertalk"})};
};
const response = await ui.TickSessionCommand.send({
  sessionId: "session-1",
  fetchImpl,
  commandIdFactory: () => "cmd-tick"
});
console.log(JSON.stringify({calls, response}));
"""
    )

    assert result["calls"] == [
        {
            "url": "/v2/sessions/session-1/tick",
            "method": "POST",
            "body": {"command_id": "cmd-tick"},
        }
    ]


def test_load_operator_status_fetches_durable_session_status_api():
    result = _run_node_json(
        """
const calls = [];
const fetchImpl = async (url) => {
  calls.push(url);
  return {
    ok: true,
    json: async () => ({
      session_id: "session-1",
      phase: "planned_show",
      public_summary: {title: "Durable Status"},
      automation_control: {enabled: false, paused: false, reason: "maintenance"}
    })
  };
};
const view = await ui.loadOperatorStatus({sessionId: "session 1", fetchImpl});
console.log(JSON.stringify({calls, view}));
"""
    )

    assert result["calls"] == ["/v2/sessions/session%201"]
    assert result["view"]["statusTitle"] == "Durable Status"
    assert result["view"]["automationStateLabel"] == "disabled"


def test_mount_operator_console_preserves_loaded_durable_status_fields():
    result = _run_node_json(
        """
const root = {
  innerHTML: "",
  querySelector: () => null
};
const fetchImpl = async () => ({
  ok: true,
  json: async () => ({
    session_id: "session-1",
    phase: "planned_show",
    permission_group: "operator",
    public_summary: {title: "Loaded Durable Title"},
    automation_control: {enabled: true, paused: true, reason: "hold"}
  })
});
ui.mountOperatorConsole({
  root,
  sessionId: "session-1",
  fetchImpl,
  eventSourceFactory: () => null
});
await new Promise((resolve) => setTimeout(resolve, 0));
console.log(JSON.stringify({html: root.innerHTML}));
"""
    )

    assert "Loaded Durable Title" in result["html"]
    assert 'data-testid="automation-state"' in result["html"]
    assert 'data-testid="operator-controls"' in result["html"]
    assert "hold" in result["html"]


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


def test_api_key_commands_use_management_endpoints_without_echoing_secret():
    result = _run_node_json(
        """
const calls = [];
const fetchImpl = async (url, options = {}) => {
  calls.push({url, method: options.method || "GET", body: options.body ? JSON.parse(options.body) : null});
  return {
    ok: true,
    json: async () => ({
      status: "ok",
      api_key: {key_fingerprint: "abc123def456", key_prefix: "abc123def456", permission_group: "display"},
      api_keys: [{key_fingerprint: "abc123def456", key_prefix: "abc123def456", permission_group: "display"}]
    })
  };
};
const created = await ui.ApiKeyCreateCommand.send({
  key: "new-display-secret",
  permissionGroup: "display",
  fetchImpl
});
const listed = await ui.ApiKeyListCommand.send({fetchImpl});
const deleted = await ui.ApiKeyDeleteCommand.send({
  keyFingerprint: "abc123def456",
  fetchImpl
});
console.log(JSON.stringify({calls, created, listed, deleted}));
"""
    )

    assert result["calls"][0] == {
        "url": "/v2/api-keys",
        "method": "POST",
        "body": {"key": "new-display-secret", "permission_group": "display"},
    }
    assert result["calls"][1] == {"url": "/v2/api-keys", "method": "GET", "body": None}
    assert result["calls"][2]["url"] == "/v2/api-keys/abc123def456"
    assert result["calls"][2]["method"] == "DELETE"
    _assert_no_private_payload(
        {
            "created": result["created"],
            "listed": result["listed"],
            "deleted": result["deleted"],
        }
    )


def test_operator_console_renders_api_key_management_for_operator():
    result = _run_node_json(
        """
const view = ui.OperatorSessionStatusView.fromStatus({
  session_id: "session-1",
  phase: "planned_show",
  permission_group: "operator"
});
const html = ui.renderOperatorConsole(view);
console.log(JSON.stringify({view, html}));
"""
    )

    assert 'data-testid="api-key-panel"' in result["html"]
    assert 'data-testid="api-key-input"' in result["html"]
    assert 'data-testid="api-key-permission-select"' in result["html"]
    assert 'data-testid="api-key-create-button"' in result["html"]
    assert 'data-testid="api-key-refresh-button"' in result["html"]


def test_api_key_panel_creates_refreshes_and_deletes_without_rendering_secret():
    result = _run_node_json(
        """
const calls = [];
let createHandler = null;
let refreshHandler = null;
let deleteHandler = null;
const elements = {
  apiKeyInput: {value: "new-display-secret"},
  permissionSelect: {value: "display"},
  createButton: {addEventListener: (_event, handler) => { createHandler = handler; }},
  refreshButton: {addEventListener: (_event, handler) => { refreshHandler = handler; }},
  deleteButton: {dataset: {keyFingerprint: "abc123def456"}, addEventListener: (_event, handler) => { deleteHandler = handler; }},
};
const root = {
  innerHTML: "",
  querySelector: (selector) => {
    if (selector === "[data-testid='aftertalk-toggle']") return null;
    if (selector === "[data-testid='tick-button']") return null;
    if (selector === "[data-testid='bind-plan-button']") return null;
    if (selector === "[data-testid='manual-close-button']") return null;
    if (selector === "[data-testid='api-key-input']") return elements.apiKeyInput;
    if (selector === "[data-testid='api-key-permission-select']") return elements.permissionSelect;
    if (selector === "[data-testid='api-key-create-button']") return elements.createButton;
    if (selector === "[data-testid='api-key-refresh-button']") return elements.refreshButton;
    return null;
  },
  querySelectorAll: (selector) => selector === "[data-testid='api-key-delete-button']"
    ? [elements.deleteButton]
    : []
};
const fetchImpl = async (url, options = {}) => {
  calls.push({url, method: options.method || "GET", body: options.body ? JSON.parse(options.body) : null});
  return {
    ok: true,
    json: async () => ({
      status: "ok",
      api_keys: [{key_fingerprint: "abc123def456", key_prefix: "abc123def456", permission_group: "display"}],
      api_key: {key_fingerprint: "abc123def456", key_prefix: "abc123def456", permission_group: "display"}
    })
  };
};
ui.mountOperatorConsole({
  root,
  sessionId: "session-1",
  fetchImpl,
  eventSourceFactory: () => null,
  initialStatus: {session_id: "session-1", phase: "planned_show", permission_group: "operator"}
});
await createHandler();
await refreshHandler();
await deleteHandler();
console.log(JSON.stringify({calls, html: root.innerHTML, inputValue: elements.apiKeyInput.value}));
"""
    )

    assert result["calls"][0]["url"] == "/v2/api-keys"
    assert result["calls"][0]["method"] == "POST"
    assert result["calls"][1]["url"] == "/v2/api-keys"
    assert result["calls"][1]["method"] == "GET"
    assert result["calls"][2]["url"] == "/v2/api-keys"
    assert result["calls"][2]["method"] == "GET"
    assert result["calls"][3]["url"] == "/v2/api-keys/abc123def456"
    assert result["calls"][3]["method"] == "DELETE"
    assert result["inputValue"] == ""
    assert "abc123def456" in result["html"]
    assert "new-display-secret" not in result["html"]
    _assert_no_private_payload(result["html"])


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
    assert result["view"]["controls"]["tickDisabled"] is True
    assert result["view"]["controls"]["bindPlanDisabled"] is True
    assert 'data-testid="tick-button" disabled' in result["html"]
    assert 'data-testid="bind-plan-button" disabled' in result["html"]
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


def test_operator_stream_ignores_event_history_messages_for_status_updates():
    result = _run_node_json(
        """
class FakeSource {
  constructor(url) {
    this.url = url;
  }
}
const statuses = [];
const source = ui.connectOperatorStream({
  sessionId: "session 1",
  eventSourceFactory: (url) => new FakeSource(url),
  onStatus: (status) => statuses.push(status),
});
source.onmessage({
  data: JSON.stringify({
    event_id: "evt-1",
    event_type: "phase_update",
    public_payload: {phase: "aftertalk"}
  })
});
console.log(JSON.stringify({statuses}));
"""
    )

    assert result["statuses"] == []


def test_missing_session_id_renders_create_session_controls():
    result = _run_node_json(
        """
const root = {innerHTML: "", querySelector: () => null};
ui.mountOperatorConsole({root});
console.log(JSON.stringify({html: root.innerHTML}));
"""
    )

    assert 'data-testid="create-session-form"' in result["html"]
    assert 'data-testid="create-session-id-input"' in result["html"]
    assert 'data-testid="create-session-button"' in result["html"]
    assert "session" in result["html"].lower()


def test_parse_plan_json_for_operator_rejects_invalid_json_safely():
    result = _run_node_json(
        """
try {
  ui.parsePlanJsonForOperator("{bad json");
} catch (error) {
  console.log(JSON.stringify({message: error.message, error}));
}
"""
    )

    assert result["message"] == "invalid plan JSON"
    _assert_no_private_payload(result)


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


def test_operator_console_page_init_mounts_without_session_id():
    source = UI_MODULE.read_text(encoding="utf-8")

    assert "if (root && sessionId)" not in source
    assert "mountOperatorConsole({root, sessionId});" in source


def test_operator_console_i18n_keys_are_registered():
    zh = json.loads((ROOT / "static" / "locales" / "zh-TW.json").read_text(encoding="utf-8"))
    en = json.loads((ROOT / "static" / "locales" / "en-US.json").read_text(encoding="utf-8"))
    source = UI_MODULE.read_text(encoding="utf-8")

    for key in (
        "youtubebridge_v2.operator_console.title",
        "youtubebridge_v2.operator_console.session",
        "youtubebridge_v2.operator_console.phase",
        "youtubebridge_v2.operator_console.phase_unknown",
        "youtubebridge_v2.operator_console.automation",
        "youtubebridge_v2.operator_console.automation_running",
        "youtubebridge_v2.operator_console.automation_paused",
        "youtubebridge_v2.operator_console.automation_disabled",
        "youtubebridge_v2.operator_console.create_session",
        "youtubebridge_v2.operator_console.bind_plan",
        "youtubebridge_v2.operator_console.tick",
        "youtubebridge_v2.operator_console.plan_json",
        "youtubebridge_v2.operator_console.episode_plans",
        "youtubebridge_v2.operator_console.load_episode_plan",
        "youtubebridge_v2.operator_console.episode_plans_empty",
        "youtubebridge_v2.operator_console.invalid_plan_json",
        "youtubebridge_v2.operator_console.manual_close",
        "youtubebridge_v2.operator_console.read_only",
        "youtubebridge_v2.operator_console.stream_connected",
        "youtubebridge_v2.operator_console.stream_stale",
        "youtubebridge_v2.operator_console.diagnostics_unavailable",
        "youtubebridge_v2.operator_console.missing_session_id",
        "youtubebridge_v2.operator_console.api_keys",
        "youtubebridge_v2.operator_console.refresh",
        "youtubebridge_v2.operator_console.api_key_placeholder",
        "youtubebridge_v2.operator_console.create_api_key",
        "youtubebridge_v2.operator_console.delete_api_key",
        "youtubebridge_v2.operator_console.api_keys_empty",
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

    status_response = client.get("/v2/sessions/session-1")
    assert status_response.status_code == 200
    status = status_response.json()
    assert status["session_id"] == "session-1"
    assert status["phase"] == "planned_show"
    assert status["permission_group"] == "operator"
    assert "public_summary" in status
    assert status["automation_control"] == {
        "enabled": True,
        "paused": False,
        "reason": "",
    }
    assert "v2_runtime_not_configured" not in repr(status)

    policy_response = client.post(
        "/v2/sessions/session-1/aftertalk-policy",
        json={"command_id": "cmd-policy", "aftertalk_policy": "disabled"},
    )
    assert policy_response.status_code == 200

    policy_status = client.get("/v2/sessions/session-1").json()
    assert policy_status["aftertalk_policy"] == "disabled"
    assert policy_status["permission_group"] == "operator"

    bind_response = client.post(
        "/v2/sessions/session-1/plan",
        json={
            "command_id": "cmd-bind",
            "plan": {
                "plan_id": "plan-ui",
                "title": "Operator UI Plan",
                "turns": [
                    {
                        "id": "turn-1",
                        "purpose": "Verify operator UI plan bind route.",
                        "topic_cue": "UI route smoke.",
                        "speaker_policy": {
                            "type": "fixed",
                            "speaker_ids": ["host"],
                        },
                        "audience_insertion": {
                            "enabled": False,
                            "allow_super_chats": False,
                        },
                    }
                ],
            },
        },
    )
    assert bind_response.status_code == 200

    tick_response = client.post(
        "/v2/sessions/session-1/tick",
        json={"command_id": "cmd-tick"},
    )
    assert tick_response.status_code == 200

    manual_close_response = client.post(
        "/v2/sessions/session-1/manual-close",
        json={"command_id": "cmd-close", "reason": "operator"},
    )
    assert manual_close_response.status_code == 200

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
