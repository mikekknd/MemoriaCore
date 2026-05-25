const PHASE_LABELS = {
  planned_show: "Planned Show",
  aftertalk: "Aftertalk",
  closing: "Closing",
  ended: "Ended",
};
const I18N_PREFIX = "youtubebridge_v2.operator_console";

const PRIVATE_KEYS = new Set([
  "access_token",
  "authorization",
  "hidden_prompt",
  "raw_adapter_payload",
  "raw_fact_card",
  "raw_fact_cards",
  "raw_factcard",
  "raw_memoriacore_payload",
  "raw_payload",
  "raw_prompt",
  "raw_topic_pack",
  "secret",
  "token",
  "topic_pack_fact_cards",
]);

const PRIVATE_TEXT = Array.from(PRIVATE_KEYS);

export class OperatorSessionStatusView {
  constructor(values) {
    Object.assign(this, values);
  }

  static fromStatus(status = {}, options = {}) {
    const phase = String(status.phase || "unknown");
    const permissionGroup = String(
      options.permissionGroup || status.permission_group || status.permissionGroup || "display",
    );
    const canControl = permissionGroup === "operator";
    const closingState = String(status.closing_completion_status || "not_started");
    const inFlightAction = options.inFlightAction || "";
    const controlsDisabled = Boolean(inFlightAction) || phase === "closing" || phase === "ended";
    const aftertalkPolicy = String(status.aftertalkPolicy || status.aftertalk_policy || "disabled");
    const streamState = String(status.stream_state || options.streamState || "connected");
    const diagnostics = sanitizePublicValue(status.diagnostics || {});
    const sessionId = String(options.sessionId || status.session_id || "");
    const publicSummary = normalizePublicSummary(
      status.public_summary || status.publicSummary || {title: status.statusTitle},
      sessionId,
    );
    const automationControl = normalizeAutomationControl(
      status.automation_control || status.automationControl || {},
    );

    return new OperatorSessionStatusView({
      sessionId,
      statusTitle: publicSummary.title,
      publicSummary,
      phase,
      phaseLabel: localizedPhaseLabel(phase),
      automationControl,
      automationStateLabel: localizedAutomationState(automationControl),
      aftertalkPolicy,
      aftertalkPolicyLabel: localizedAftertalkPolicy(aftertalkPolicy),
      aftertalkStateLabel: phase === "aftertalk"
        ? translate("aftertalk_active", "active")
        : translate("aftertalk_idle", "idle"),
      canControl,
      permissionGroup,
      closingState,
      remainingTimeLabel: formatRemainingTime(
        status.duration_summary?.remaining_time_seconds,
      ),
      planProgress: normalizePlanProgress(status.live_episode_plan || status.plan_progress),
      episodePlans: normalizeEpisodePlanList(status.episode_plans || status.episodePlans || []),
      apiKeys: normalizeApiKeyList(status.api_keys || status.apiKeys || []),
      diagnostics,
      errorBanner: status.error
        ? OperatorDiagnosticBanner.fromError(status.error)
        : diagnosticBannerFromMetadata(diagnostics),
      controls: {
        aftertalkDisabled: !canControl || controlsDisabled,
        bindPlanDisabled: !canControl || controlsDisabled,
        episodePlanDisabled: !canControl || controlsDisabled,
        apiKeyDisabled: !canControl || controlsDisabled,
        manualCloseDisabled: !canControl || controlsDisabled,
        tickDisabled: !canControl || controlsDisabled,
      },
      streamState,
      streamStateLabel: localizedStreamState(streamState),
    });
  }
}

export class OperatorControlAction {
  constructor({sessionId, endpoint, body, method = "POST"}) {
    this.sessionId = String(sessionId || "");
    this.endpoint = endpoint;
    this.method = method;
    this.body = sanitizePublicValue(body || {});
  }

  async send(fetchImpl = globalThis.fetch) {
    const response = await fetchImpl(this.endpoint, {
      method: this.method,
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(this.body),
    });
    const payload = await safeJson(response);
    if (!response.ok) {
      throw OperatorDiagnosticBanner.fromError(payload);
    }
    return payload;
  }
}

export class AftertalkPolicyControl {
  static action({sessionId, policy, commandIdFactory = defaultCommandId}) {
    return new OperatorControlAction({
      sessionId,
      endpoint: `/v2/sessions/${encodeURIComponent(sessionId)}/aftertalk-policy`,
      body: {
        command_id: commandIdFactory("aftertalk-policy"),
        aftertalk_policy: policy,
      },
    });
  }

  static send({sessionId, policy, fetchImpl = globalThis.fetch, commandIdFactory}) {
    return AftertalkPolicyControl.action({
      sessionId,
      policy,
      commandIdFactory,
    }).send(fetchImpl);
  }
}

export class CreateSessionCommand {
  static action({
    sessionId,
    aftertalkPolicy = "auto",
    commandIdFactory = defaultCommandId,
  }) {
    return new OperatorControlAction({
      sessionId,
      endpoint: "/v2/sessions",
      body: {
        command_id: commandIdFactory("create-session"),
        session_id: sessionId,
        aftertalk_policy: aftertalkPolicy,
      },
    });
  }

  static send({
    sessionId,
    aftertalkPolicy = "auto",
    fetchImpl = globalThis.fetch,
    commandIdFactory,
  }) {
    return CreateSessionCommand.action({
      sessionId,
      aftertalkPolicy,
      commandIdFactory,
    }).send(fetchImpl);
  }
}

export class BindPlanCommand {
  static action({sessionId, plan, commandIdFactory = defaultCommandId}) {
    return new OperatorControlAction({
      sessionId,
      endpoint: `/v2/sessions/${encodeURIComponent(sessionId)}/plan`,
      body: {
        command_id: commandIdFactory("bind-plan"),
        plan,
      },
    });
  }

  static send({sessionId, plan, fetchImpl = globalThis.fetch, commandIdFactory}) {
    return BindPlanCommand.action({
      sessionId,
      plan,
      commandIdFactory,
    }).send(fetchImpl);
  }
}

export class EpisodePlanListCommand {
  static async send({fetchImpl = globalThis.fetch} = {}) {
    const response = await fetchImpl("/v2/episode-plans");
    const payload = await safeJson(response);
    if (!response.ok) {
      throw OperatorDiagnosticBanner.fromError(payload);
    }
    return normalizeEpisodePlanList(payload.episode_plans || payload.episodePlans || payload);
  }
}

export class TickSessionCommand {
  static action({sessionId, commandIdFactory = defaultCommandId}) {
    return new OperatorControlAction({
      sessionId,
      endpoint: `/v2/sessions/${encodeURIComponent(sessionId)}/tick`,
      body: {
        command_id: commandIdFactory("tick"),
      },
    });
  }

  static send({sessionId, fetchImpl = globalThis.fetch, commandIdFactory}) {
    return TickSessionCommand.action({
      sessionId,
      commandIdFactory,
    }).send(fetchImpl);
  }
}

export class ManualCloseCommand {
  static action({sessionId, reason = "operator", commandIdFactory = defaultCommandId}) {
    return new OperatorControlAction({
      sessionId,
      endpoint: `/v2/sessions/${encodeURIComponent(sessionId)}/manual-close`,
      body: {
        command_id: commandIdFactory("manual-close"),
        reason,
      },
    });
  }

  static send({
    sessionId,
    reason = "operator",
    fetchImpl = globalThis.fetch,
    commandIdFactory,
  }) {
    return ManualCloseCommand.action({
      sessionId,
      reason,
      commandIdFactory,
    }).send(fetchImpl);
  }
}

export class ApiKeyListCommand {
  static async send({fetchImpl = globalThis.fetch} = {}) {
    const response = await fetchImpl("/v2/api-keys");
    const payload = await safeJson(response);
    if (!response.ok) {
      throw OperatorDiagnosticBanner.fromError(payload);
    }
    return normalizeApiKeyList(payload.api_keys || payload.apiKeys || payload);
  }
}

export class ApiKeyCreateCommand {
  static async send({
    key,
    permissionGroup,
    fetchImpl = globalThis.fetch,
  } = {}) {
    const response = await fetchImpl("/v2/api-keys", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        key,
        permission_group: permissionGroup,
      }),
    });
    const payload = await safeJson(response);
    if (!response.ok) {
      throw OperatorDiagnosticBanner.fromError(payload);
    }
    return sanitizePublicValue(payload);
  }
}

export class ApiKeyDeleteCommand {
  static async send({
    keyFingerprint,
    fetchImpl = globalThis.fetch,
  } = {}) {
    const response = await fetchImpl(`/v2/api-keys/${encodeURIComponent(keyFingerprint)}`, {
      method: "DELETE",
    });
    const payload = await safeJson(response);
    if (!response.ok) {
      throw OperatorDiagnosticBanner.fromError(payload);
    }
    return sanitizePublicValue(payload);
  }
}

export class OperatorDiagnosticBanner {
  constructor({severity = "error", message = translate("request_failed", "request failed"), metadata = {}} = {}) {
    this.severity = severity;
    this.message = sanitizeMessage(message);
    this.metadata = sanitizePublicValue(metadata);
  }

  static fromError(error = {}) {
    const rawMessage = error.error?.message || error.message || error.detail || "";
    return new OperatorDiagnosticBanner({
      severity: "error",
      message: sanitizeMessage(rawMessage) || translate("request_failed", "request failed"),
      metadata: error.error || error,
    });
  }

  render() {
    return `<div class="error-banner" data-testid="error-banner" role="alert">${escapeHtml(this.message)}</div>`;
  }
}

export function renderOperatorConsole(viewLike) {
  const view = viewLike instanceof OperatorSessionStatusView
    ? viewLike
    : OperatorSessionStatusView.fromStatus(viewLike);
  const errorHtml = view.errorBanner ? view.errorBanner.render() : "";
  const controlsHtml = view.canControl
    ? renderOperatorControls(view)
    : `<div class="read-only" data-testid="read-only-permission">${escapeHtml(translate("read_only", "Display-only access"))}</div>`;

  return `
    <section class="operator-console" data-phase="${escapeHtml(view.phase)}">
      ${errorHtml}
      <header class="console-header">
        <div>
          <span class="eyeline">YouTubeBridgeV2</span>
          <h1>${escapeHtml(translate("title", "Operator Console"))}</h1>
          <div class="header-meta">
            <strong data-testid="status-title">${escapeHtml(view.statusTitle)}</strong>
            <span data-testid="session-id">${escapeHtml(translate("session", "Session"))}: ${escapeHtml(view.sessionId)}</span>
          </div>
        </div>
        <span class="stream-state" data-state="${escapeHtml(view.streamState)}">${escapeHtml(view.streamStateLabel)}</span>
      </header>
      <main class="console-grid">
        <section class="panel phase-panel">
          <span class="label">${escapeHtml(translate("phase", "Phase"))}</span>
          <strong data-testid="phase-value">${escapeHtml(view.phaseLabel)}</strong>
          <span class="muted">${escapeHtml(view.phase)}</span>
        </section>
        <section class="panel">
          <span class="label">${escapeHtml(translate("remaining_time", "Remaining Time"))}</span>
          <strong data-testid="remaining-time">${escapeHtml(view.remainingTimeLabel)}</strong>
        </section>
        <section class="panel" data-automation-state="${escapeHtml(automationStateName(view.automationControl))}">
          <span class="label">${escapeHtml(translate("automation", "Automation"))}</span>
          <strong data-testid="automation-state">${escapeHtml(view.automationStateLabel)}</strong>
          <span class="muted">${escapeHtml(view.automationControl.reason || "")}</span>
        </section>
        <section class="panel">
          <span class="label">${escapeHtml(translate("aftertalk", "Aftertalk"))}</span>
          <strong data-testid="aftertalk-policy">${escapeHtml(view.aftertalkPolicyLabel)}</strong>
          <span class="muted" data-testid="aftertalk-state">${escapeHtml(view.aftertalkStateLabel)}</span>
        </section>
        <section class="panel">
          <span class="label">${escapeHtml(translate("closing", "Closing"))}</span>
          <strong data-testid="closing-state">${escapeHtml(view.closingState)}</strong>
        </section>
        ${renderPlanProgress(view.planProgress)}
      </main>
      ${controlsHtml}
    </section>
  `;
}

export async function loadOperatorStatus({
  sessionId,
  fetchImpl = globalThis.fetch,
} = {}) {
  const response = await fetchImpl(`/v2/sessions/${encodeURIComponent(sessionId)}`);
  const payload = await safeJson(response);
  if (!response.ok) {
    throw OperatorDiagnosticBanner.fromError(payload);
  }
  return OperatorSessionStatusView.fromStatus(payload, {sessionId});
}

export function connectOperatorStream({
  sessionId,
  eventSourceFactory = defaultEventSourceFactory,
  onStatus = () => {},
  onStale = () => {},
} = {}) {
  if (!sessionId || typeof eventSourceFactory !== "function") return null;
  const endpoint = `/v2/sessions/${encodeURIComponent(sessionId)}/operator-stream`;
  const source = eventSourceFactory(endpoint);
  if (!source) return null;

  source.onmessage = (event) => {
    const payload = parseStreamPayload(event?.data);
    if (!payload) return;
    if (payload.event_type && payload.event_type !== "operator_status") return;
    onStatus(operatorStatusFromEvent(payload));
  };
  source.onerror = () => {
    onStale({
      stream_state: "stale",
      diagnostics: {
        message: translate("diagnostics_unavailable", "operator stream disconnected"),
        operator_stream: "disconnected",
      },
    });
  };
  return source;
}

export async function initOperatorConsoleI18n(i18n = globalThis.MCI18N) {
  if (i18n?.init) {
    await i18n.init();
  }
  if (i18n?.apply && typeof document !== "undefined") {
    i18n.apply(document);
  }
}

export function mountOperatorConsole({
  root,
  sessionId,
  fetchImpl = globalThis.fetch,
  eventSourceFactory = defaultEventSourceFactory,
  initialStatus = null,
} = {}) {
  const target = root || document.getElementById("operatorConsoleRoot");
  if (!target) return;
  if (!sessionId) {
    target.innerHTML = renderSessionCreatePanel();
    bindSessionCreateControls(target, {fetchImpl});
    return;
  }
  let latestStatus = initialStatus || null;
  let stream = null;
  let episodePlansRequested = false;

  const render = (status, options = {}) => {
    if (
      options.skipIfUnchanged
      && latestStatus
      && !statusPatchChangesCurrent(latestStatus, status)
    ) {
      return;
    }
    const controlState = readOperatorControlState(target);
    latestStatus = {...(latestStatus || {}), ...status};
    target.innerHTML = renderOperatorConsole(
      OperatorSessionStatusView.fromStatus(latestStatus, {sessionId, ...options}),
    );
    restoreOperatorControlState(target, controlState);
    bindOperatorControls(target, {sessionId, fetchImpl, render, status: latestStatus});
  };
  const loadEpisodePlansOnce = () => {
    if (episodePlansRequested) return;
    const view = OperatorSessionStatusView.fromStatus(latestStatus || {}, {sessionId});
    if (!view.canControl || view.episodePlans.length > 0) return;
    if (
      typeof target.querySelector === "function"
      && !target.querySelector("[data-testid='episode-plan-select']")
    ) {
      return;
    }
    episodePlansRequested = true;
    EpisodePlanListCommand.send({fetchImpl})
      .then((episodePlans) => render({episode_plans: episodePlans}))
      .catch((error) => render({error}));
  };
  const ensureStream = () => {
    if (stream) return;
    stream = connectOperatorStream({
      sessionId,
      eventSourceFactory,
      onStatus: (status) => render(
        {...status, stream_state: "connected"},
        {skipIfUnchanged: true},
      ),
      onStale: (status) => render(status),
    });
  };

  if (initialStatus) {
    render(initialStatus);
    loadEpisodePlansOnce();
    ensureStream();
    return;
  }

  loadOperatorStatus({sessionId, fetchImpl})
    .then((view) => {
      render(view);
      loadEpisodePlansOnce();
      ensureStream();
    })
    .catch((error) => {
      target.innerHTML = new OperatorDiagnosticBanner({
        message: error.message || translate("request_failed", "request failed"),
      }).render();
    });
}

function bindOperatorControls(root, {sessionId, fetchImpl, render, status}) {
  const refreshEpisodePlans = async () => {
    try {
      const episodePlans = await EpisodePlanListCommand.send({fetchImpl});
      render({...status, episode_plans: episodePlans});
    } catch (error) {
      render({...status, error});
    }
  };

  const refreshApiKeys = async () => {
    try {
      const apiKeys = await ApiKeyListCommand.send({fetchImpl});
      render({...status, api_keys: apiKeys});
    } catch (error) {
      render({...status, error});
    }
  };

  const aftertalk = root.querySelector("[data-testid='aftertalk-toggle']");
  if (aftertalk) {
    aftertalk.addEventListener("change", async () => {
      render(status, {inFlightAction: "aftertalk_policy"});
      const policy = aftertalk.checked ? "auto" : "disabled";
      try {
        await AftertalkPolicyControl.send({sessionId, policy, fetchImpl});
        const nextStatus = await loadOperatorStatus({sessionId, fetchImpl});
        render(nextStatus);
      } catch (error) {
        render({...status, error});
      }
    });
  }

  const tick = root.querySelector("[data-testid='tick-button']");
  if (tick) {
    tick.addEventListener("click", async () => {
      render(status, {inFlightAction: "tick"});
      try {
        const next = await TickSessionCommand.send({sessionId, fetchImpl});
        render({...status, ...next});
      } catch (error) {
        render({...status, error});
      }
    });
  }

  const bindPlan = root.querySelector("[data-testid='bind-plan-button']");
  if (bindPlan) {
    bindPlan.addEventListener("click", async () => {
      const input = root.querySelector("[data-testid='plan-json-input']");
      let plan;
      try {
        plan = parsePlanJsonForOperator(input?.value || "");
      } catch (error) {
        render({...status, error});
        return;
      }
      render(status, {inFlightAction: "bind_plan"});
      try {
        await BindPlanCommand.send({sessionId, plan, fetchImpl});
        const nextStatus = await loadOperatorStatus({sessionId, fetchImpl});
        render(nextStatus);
      } catch (error) {
        render({...status, error});
      }
    });
  }

  const episodePlanRefresh = root.querySelector("[data-testid='episode-plan-refresh-button']");
  if (episodePlanRefresh) {
    episodePlanRefresh.addEventListener("click", refreshEpisodePlans);
  }

  const episodePlanLoad = root.querySelector("[data-testid='episode-plan-load-button']");
  if (episodePlanLoad) {
    episodePlanLoad.addEventListener("click", async () => {
      const select = root.querySelector("[data-testid='episode-plan-select']");
      const input = root.querySelector("[data-testid='plan-json-input']");
      const plans = normalizeEpisodePlanList(status.episode_plans || status.episodePlans || []);
      const selected = String(select?.value || "");
      const entry = plans.find((plan) => plan.id === selected || plan.planId === selected) || plans[0];
      if (!entry || !input) return;
      input.value = JSON.stringify(entry.plan, null, 2);
    });
  }

  const close = root.querySelector("[data-testid='manual-close-button']");
  if (close) {
    close.addEventListener("click", async () => {
      render(status, {inFlightAction: "manual_close"});
      try {
        const next = await ManualCloseCommand.send({sessionId, fetchImpl});
        render({...status, ...next});
      } catch (error) {
        render({...status, error});
      }
    });
  }

  const apiKeyCreate = root.querySelector("[data-testid='api-key-create-button']");
  if (apiKeyCreate) {
    apiKeyCreate.addEventListener("click", async () => {
      const input = root.querySelector("[data-testid='api-key-input']");
      const permission = root.querySelector("[data-testid='api-key-permission-select']");
      const key = String(input?.value || "").trim();
      if (!key) return;
      render(status, {inFlightAction: "api_key"});
      try {
        await ApiKeyCreateCommand.send({
          key,
          permissionGroup: String(permission?.value || "observer"),
          fetchImpl,
        });
        if (input) input.value = "";
        await refreshApiKeys();
      } catch (error) {
        render({...status, error});
      }
    });
  }

  const apiKeyRefresh = root.querySelector("[data-testid='api-key-refresh-button']");
  if (apiKeyRefresh) {
    apiKeyRefresh.addEventListener("click", refreshApiKeys);
  }

  const apiKeyDeleteButtons = typeof root.querySelectorAll === "function"
    ? root.querySelectorAll("[data-testid='api-key-delete-button']")
    : [];
  for (const button of apiKeyDeleteButtons) {
    button.addEventListener("click", async () => {
      const keyFingerprint = button.dataset.keyFingerprint || "";
      if (!keyFingerprint) return;
      render(status, {inFlightAction: "api_key"});
      try {
        await ApiKeyDeleteCommand.send({keyFingerprint, fetchImpl});
        await refreshApiKeys();
      } catch (error) {
        render({...status, error});
      }
    });
  }
}

function readOperatorControlState(root) {
  if (!root || typeof root.querySelector !== "function") {
    return {};
  }
  return {
    episodePlanId: String(
      root.querySelector("[data-testid='episode-plan-select']")?.value || "",
    ),
    planJson: String(
      root.querySelector("[data-testid='plan-json-input']")?.value || "",
    ),
  };
}

function restoreOperatorControlState(root, state = {}) {
  if (!root || typeof root.querySelector !== "function") {
    return;
  }
  const select = root.querySelector("[data-testid='episode-plan-select']");
  if (select && state.episodePlanId) {
    select.value = state.episodePlanId;
  }
  const input = root.querySelector("[data-testid='plan-json-input']");
  if (input && state.planJson) {
    input.value = state.planJson;
  }
}

function statusPatchChangesCurrent(current = {}, patch = {}) {
  for (const [key, value] of Object.entries(patch || {})) {
    const currentValue = current[key];
    if (key === "stream_state" && !currentValue && value === "connected") {
      continue;
    }
    if (JSON.stringify(currentValue ?? null) !== JSON.stringify(value ?? null)) {
      return true;
    }
  }
  return false;
}

function renderOperatorControls(view) {
  const aftertalkChecked = view.aftertalkPolicy === "auto" ? " checked" : "";
  const aftertalkDisabled = view.controls.aftertalkDisabled ? " disabled" : "";
  const bindDisabled = view.controls.bindPlanDisabled ? " disabled" : "";
  const episodePlanDisabled = view.controls.episodePlanDisabled ? " disabled" : "";
  const apiKeyDisabled = view.controls.apiKeyDisabled ? " disabled" : "";
  const manualDisabled = view.controls.manualCloseDisabled ? " disabled" : "";
  const tickDisabled = view.controls.tickDisabled ? " disabled" : "";
  const noEpisodePlans = episodePlanSelectDisabled(view.episodePlans);
  return `
    <section class="operator-controls" data-testid="operator-controls">
      <div class="control-row">
        <label class="toggle">
          <input data-testid="aftertalk-toggle" type="checkbox"${aftertalkChecked}${aftertalkDisabled}>
          <span>${escapeHtml(translate("aftertalk", "Aftertalk"))}</span>
        </label>
        <button data-testid="tick-button"${tickDisabled}>${escapeHtml(translate("tick", "Tick"))}</button>
        <button data-testid="manual-close-button"${manualDisabled}>${escapeHtml(translate("manual_close", "Manual Close"))}</button>
      </div>
      <div class="plan-bind-control">
        <label for="operatorPlanJson">${escapeHtml(translate("plan_json", "Plan JSON"))}</label>
        <div class="episode-plan-picker" data-testid="episode-plan-picker">
          <label for="operatorEpisodePlanSelect">${escapeHtml(translate("episode_plans", "Episode Plans"))}</label>
          <div class="episode-plan-picker-row">
            <select id="operatorEpisodePlanSelect" data-testid="episode-plan-select"${episodePlanDisabled || noEpisodePlans}>
              ${renderEpisodePlanOptions(view.episodePlans || [])}
            </select>
            <button data-testid="episode-plan-refresh-button" type="button"${episodePlanDisabled}>${escapeHtml(translate("refresh", "Refresh"))}</button>
            <button data-testid="episode-plan-load-button" type="button"${episodePlanDisabled || noEpisodePlans}>${escapeHtml(translate("load_episode_plan", "Load Plan"))}</button>
          </div>
        </div>
        <textarea id="operatorPlanJson" data-testid="plan-json-input" rows="6" spellcheck="false"></textarea>
        <button data-testid="bind-plan-button"${bindDisabled}>${escapeHtml(translate("bind_plan", "Bind Plan"))}</button>
      </div>
      <div class="api-key-control" data-testid="api-key-panel">
        <div class="control-heading">
          <strong>${escapeHtml(translate("api_keys", "API Keys"))}</strong>
          <button data-testid="api-key-refresh-button" type="button"${apiKeyDisabled}>${escapeHtml(translate("refresh", "Refresh"))}</button>
        </div>
        <div class="api-key-create-row">
          <input data-testid="api-key-input" type="password" autocomplete="off" placeholder="${escapeHtml(translate("api_key_placeholder", "New API key"))}"${apiKeyDisabled}>
          <select data-testid="api-key-permission-select"${apiKeyDisabled}>
            <option value="operator">operator</option>
            <option value="display">display</option>
            <option value="observer">observer</option>
          </select>
          <button data-testid="api-key-create-button" type="button"${apiKeyDisabled}>${escapeHtml(translate("create_api_key", "Create Key"))}</button>
        </div>
        <div class="api-key-list" data-testid="api-key-list">${renderApiKeyList(view.apiKeys || [])}</div>
      </div>
    </section>
  `;
}

function episodePlanSelectDisabled(episodePlans) {
  return normalizeEpisodePlanList(episodePlans).length === 0 ? " disabled" : "";
}

function renderEpisodePlanOptions(episodePlans) {
  const entries = normalizeEpisodePlanList(episodePlans);
  if (entries.length === 0) {
    return `<option value="">${escapeHtml(translate("episode_plans_empty", "No episode plans"))}</option>`;
  }
  return entries.map((entry) => {
    const label = entry.folder
      ? `${entry.title} (${entry.folder})`
      : entry.title;
    return `<option value="${escapeHtml(entry.id)}">${escapeHtml(label)}</option>`;
  }).join("");
}

function renderSessionCreatePanel(errorBanner = null) {
  const errorHtml = errorBanner
    ? errorBanner.render()
    : new OperatorDiagnosticBanner({
      message: translate("missing_session_id", "Missing session_id"),
    }).render();
  return `
    <section class="operator-console setup-console">
      ${errorHtml}
      <header class="console-header">
        <div>
          <span class="eyeline">YouTubeBridgeV2</span>
          <h1>${escapeHtml(translate("title", "Operator Console"))}</h1>
        </div>
      </header>
      <form class="operator-controls setup-form" data-testid="create-session-form">
        <label>
          <span>${escapeHtml(translate("session", "Session"))}</span>
          <input data-testid="create-session-id-input" name="session_id" autocomplete="off">
        </label>
        <label>
          <span>${escapeHtml(translate("aftertalk", "Aftertalk"))}</span>
          <select data-testid="create-aftertalk-policy" name="aftertalk_policy">
            <option value="auto">auto</option>
            <option value="disabled">disabled</option>
          </select>
        </label>
        <button data-testid="create-session-button" type="submit">${escapeHtml(translate("create_session", "Create Session"))}</button>
      </form>
    </section>
  `;
}

function bindSessionCreateControls(root, {fetchImpl}) {
  const form = root.querySelector("[data-testid='create-session-form']");
  if (!form) return;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = root.querySelector("[data-testid='create-session-id-input']");
    const policy = root.querySelector("[data-testid='create-aftertalk-policy']");
    const nextSessionId = String(input?.value || "").trim();
    if (!nextSessionId) {
      root.innerHTML = renderSessionCreatePanel();
      bindSessionCreateControls(root, {fetchImpl});
      return;
    }
    try {
      await CreateSessionCommand.send({
        sessionId: nextSessionId,
        aftertalkPolicy: String(policy?.value || "auto"),
        fetchImpl,
      });
      const status = await loadOperatorStatus({sessionId: nextSessionId, fetchImpl});
      if (typeof history !== "undefined" && typeof location !== "undefined" && history.replaceState) {
        const url = new URL(location.href);
        url.searchParams.set("session_id", nextSessionId);
        history.replaceState({}, "", url);
      }
      mountOperatorConsole({root, sessionId: nextSessionId, fetchImpl, initialStatus: status});
    } catch (error) {
      root.innerHTML = renderSessionCreatePanel(
        new OperatorDiagnosticBanner({
          message: error.message || translate("request_failed", "request failed"),
        }),
      );
      bindSessionCreateControls(root, {fetchImpl});
    }
  });
}

function renderPlanProgress(progress) {
  return `
    <section class="panel plan-panel" data-testid="plan-progress">
      <div class="panel-row">
        <span class="label">${escapeHtml(translate("plan", "LiveEpisodePlan"))}</span>
        <strong>${escapeHtml(progress.label)}</strong>
      </div>
      <div class="progress-track" aria-label="${escapeHtml(translate("plan_progress", "LiveEpisodePlan progress"))}">
        <span style="width:${progress.percent}%"></span>
      </div>
      <p>${escapeHtml(progress.currentTurnTitle)}</p>
    </section>
  `;
}

function renderApiKeyList(apiKeys) {
  const entries = normalizeApiKeyList(apiKeys);
  if (entries.length === 0) {
    return `<p class="muted">${escapeHtml(translate("api_keys_empty", "No API keys"))}</p>`;
  }
  return entries.map((entry) => `
    <div class="api-key-entry">
      <span>${escapeHtml(entry.keyPrefix)}</span>
      <strong>${escapeHtml(entry.permissionGroup)}</strong>
      <button data-testid="api-key-delete-button" type="button" data-key-fingerprint="${escapeHtml(entry.keyFingerprint)}">${escapeHtml(translate("delete_api_key", "Revoke"))}</button>
    </div>
  `).join("");
}

export function parsePlanJsonForOperator(raw) {
  try {
    const parsed = JSON.parse(String(raw || ""));
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new Error("invalid");
    }
    return sanitizePublicValue(parsed);
  } catch {
    throw new OperatorDiagnosticBanner({
      message: translate("invalid_plan_json", "invalid plan JSON"),
    });
  }
}

function normalizePublicSummary(summary = {}, sessionId = "") {
  const safe = sanitizePublicValue(summary || {});
  const title = String(
    safe.title
    || safe.plan_title
    || safe.plan_id
    || sessionId
    || translate("untitled_session", "Untitled session"),
  );
  return {
    title,
    planId: String(safe.plan_id || ""),
  };
}

function normalizeAutomationControl(control = {}) {
  const safe = sanitizePublicValue(control || {});
  return {
    enabled: safe.enabled === undefined ? true : Boolean(safe.enabled),
    paused: Boolean(safe.paused),
    reason: String(safe.reason || ""),
  };
}

function localizedAutomationState(control) {
  if (!control.enabled) {
    return translate("automation_disabled", "disabled");
  }
  if (control.paused) {
    return translate("automation_paused", "paused");
  }
  return translate("automation_running", "running");
}

function automationStateName(control) {
  if (!control.enabled) return "disabled";
  if (control.paused) return "paused";
  return "running";
}

function normalizePlanProgress(plan = {}) {
  const current = clampNumber(
    plan.current_turn_index ?? plan.current_turn ?? plan.completed_turns ?? 0,
    0,
    Number.MAX_SAFE_INTEGER,
  );
  const total = Math.max(0, Number(plan.total_turns ?? plan.turn_count ?? 0) || 0);
  const percent = total > 0 ? Math.round((Math.min(current, total) / total) * 100) : 0;
  return {
    planId: String(plan.plan_id || ""),
    status: String(plan.status || "not_loaded"),
    currentTurnIndex: current,
    totalTurns: total,
    currentTurnTitle: String(plan.current_turn_title || plan.current_turn_id || translate("no_active_turn", "No active turn")),
    label: total > 0 ? `${current} / ${total}` : "0 / 0",
    percent,
  };
}

function normalizeApiKeyList(payload = []) {
  const entries = Array.isArray(payload)
    ? payload
    : Array.isArray(payload.api_keys)
      ? payload.api_keys
      : Array.isArray(payload.apiKeys)
        ? payload.apiKeys
        : [];
  return entries.map((entry) => {
    const safe = sanitizePublicValue(entry || {});
    const keyFingerprint = String(safe.key_fingerprint || safe.keyFingerprint || "");
    const keyPrefix = String(safe.key_prefix || safe.keyPrefix || keyFingerprint.slice(0, 12));
    const permissionGroup = String(safe.permission_group || safe.permissionGroup || "observer");
    return {
      keyFingerprint,
      keyPrefix,
      permissionGroup,
    };
  }).filter((entry) => entry.keyFingerprint);
}

function normalizeEpisodePlanList(payload = []) {
  const entries = Array.isArray(payload)
    ? payload
    : Array.isArray(payload.episode_plans)
      ? payload.episode_plans
      : Array.isArray(payload.episodePlans)
        ? payload.episodePlans
        : [];
  return entries.map((entry) => {
    const safe = sanitizePublicValue(entry || {});
    const plan = sanitizePublicValue(safe.plan || {});
    const planId = String(safe.plan_id || safe.planId || plan.plan_id || "");
    const id = String(safe.id || planId || safe.folder || "");
    const title = String(safe.title || plan.title || plan.plan_title || planId || id);
    return {
      id,
      planId,
      title,
      folder: String(safe.folder || ""),
      filename: String(safe.filename || "episode-plan.json"),
      plan,
    };
  }).filter((entry) => entry.id && entry.plan && typeof entry.plan === "object");
}

function formatRemainingTime(seconds) {
  if (seconds === null || seconds === undefined) return translate("remaining_unlimited", "unlimited");
  const value = Math.max(0, Number(seconds) || 0);
  const minutes = Math.floor(value / 60);
  const remainder = Math.floor(value % 60);
  return `${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`;
}

function sanitizePublicValue(value) {
  if (Array.isArray(value)) {
    return value.map((item) => sanitizePublicValue(item));
  }
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value)
        .filter(([key]) => !PRIVATE_KEYS.has(String(key).toLowerCase()))
        .map(([key, inner]) => [key, sanitizePublicValue(inner)]),
    );
  }
  if (typeof value === "string") {
    return sanitizeMessage(value);
  }
  return value;
}

function sanitizeMessage(message) {
  const text = String(message || "");
  const lowered = text.toLowerCase();
  if (!text || PRIVATE_TEXT.some((key) => lowered.includes(key))) {
    return translate("request_failed", "request failed");
  }
  return text;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function defaultCommandId(prefix) {
  return `operator-${prefix}-${Date.now()}`;
}

async function safeJson(response) {
  try {
    return await response.json();
  } catch {
    return {};
  }
}

function clampNumber(value, min, max) {
  const number = Number(value) || 0;
  return Math.max(min, Math.min(number, max));
}

function localizedPhaseLabel(phase) {
  if (!PHASE_LABELS[phase]) {
    return translate("phase_unknown", "Unknown");
  }
  return translate(`phase_${phase}`, PHASE_LABELS[phase]);
}

function localizedAftertalkPolicy(policy) {
  return translate(`aftertalk_policy_${policy}`, policy);
}

function localizedStreamState(streamState) {
  if (streamState === "connected") {
    return translate("stream_connected", "connected");
  }
  if (streamState === "stale") {
    return translate("stream_stale", "stale");
  }
  return streamState;
}

function diagnosticBannerFromMetadata(diagnostics) {
  if (!diagnostics || typeof diagnostics !== "object" || Object.keys(diagnostics).length === 0) {
    return null;
  }
  return new OperatorDiagnosticBanner({
    severity: "warning",
    message: diagnostics.message || translate("diagnostics_unavailable", "diagnostic unavailable"),
    metadata: diagnostics,
  });
}

function translate(key, fallback, params = {}) {
  const fullKey = `${I18N_PREFIX}.${key}`;
  if (globalThis.MCI18N?.t) {
    return globalThis.MCI18N.t(fullKey, params, fallback);
  }
  return formatText(fallback, params);
}

function formatText(template, params = {}) {
  return String(template).replace(/\{([A-Za-z0-9_]+)\}/g, (match, key) => (
    Object.prototype.hasOwnProperty.call(params, key) ? params[key] : match
  ));
}

function defaultEventSourceFactory(endpoint) {
  if (typeof globalThis.EventSource !== "function") return null;
  return new globalThis.EventSource(endpoint);
}

function parseStreamPayload(raw) {
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function operatorStatusFromEvent(event) {
  const payload = event.public_payload || event.payload || event;
  return {
    ...payload,
    session_id: event.session_id || payload.session_id || "",
  };
}

if (typeof document !== "undefined") {
  const initPage = async () => {
    await initOperatorConsoleI18n();
    const root = document.getElementById("operatorConsoleRoot");
    const sessionId = root?.dataset.sessionId || new URLSearchParams(location.search).get("session_id");
    if (root) {
      mountOperatorConsole({root, sessionId});
    }
  };
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initPage);
  } else {
    initPage();
  }
}
