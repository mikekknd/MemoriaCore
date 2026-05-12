const I18N_PREFIX = "youtubebridge_v2.chat_display";

const PRIVATE_KEYS = new Set([
  "access_token",
  "authorization",
  "diagnostics",
  "hidden_prompt",
  "operator_controls",
  "operator_only",
  "operator_only_metadata",
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

const DISPLAY_FLAG_LABELS = {
  held_for_review: ["flag_held_for_review", "Held"],
  highlighted: ["flag_highlighted", "Highlighted"],
  member: ["flag_member", "Member"],
  moderator: ["flag_moderator", "Moderator"],
  paid_member: ["flag_member", "Member"],
  pinned: ["flag_pinned", "Pinned"],
  verified: ["flag_verified", "Verified"],
};

export class DisplayPresentationMetadata {
  constructor(values = {}) {
    this.voiceState = cleanText(values.voiceState || values.voice_state || "");
    this.visualState = cleanText(values.visualState || values.visual_state || "");
  }

  static fromPayload(payload = {}) {
    return new DisplayPresentationMetadata(sanitizePublicValue(payload));
  }

  render() {
    const states = [this.voiceState, this.visualState].filter(Boolean).join(" / ");
    if (!states) return "";
    return `<span class="presentation-state" data-testid="presentation-metadata">${escapeHtml(states)}</span>`;
  }
}

export class DisplayMessageEvent {
  constructor(values = {}) {
    assignDisplayOrder(this, values);
    this.eventType = "audience_message";
    this.authorDisplayName = cleanText(values.authorDisplayName || values.author_display_name || translate("audience", "Audience"));
    this.messageText = cleanText(values.messageText || values.message_text || values.text || "");
    this.timestamp = cleanText(values.timestamp || values.published_at || "");
    this.flags = sanitizePublicValue(values.flags || values.display_flags || {});
  }

  static fromEvent(event = {}) {
    return new DisplayMessageEvent(requiredPayload(event));
  }

  render() {
    if (!this.messageText) return renderFallback();
    return `
      <article class="chat-row audience-message" data-testid="audience-message">
        <div class="row-meta">
          <span class="role-chip">${escapeHtml(translate("audience", "Audience"))}</span>
          <strong>${escapeHtml(this.authorDisplayName)}</strong>
          <time>${escapeHtml(this.timestamp)}</time>
          ${renderDisplayFlags(this.flags)}
        </div>
        <p>${escapeHtml(this.messageText)}</p>
      </article>
    `;
  }
}

export class DisplayCharacterResponseEvent {
  constructor(values = {}) {
    assignDisplayOrder(this, values);
    this.eventType = "character_response";
    this.characterName = cleanText(values.characterName || values.character_name || values.speaker_name || translate("character", "Character"));
    this.roleLabel = cleanText(values.roleLabel || values.role_label || values.role || translate("character", "Character"));
    this.responseText = cleanText(values.responseText || values.response_text || values.message_text || values.text || "");
    this.phase = cleanText(values.phase || "");
    this.presentation = DisplayPresentationMetadata.fromPayload(values.presentation || values.presentation_metadata || {});
  }

  static fromEvent(event = {}) {
    return new DisplayCharacterResponseEvent(requiredPayload(event));
  }

  render() {
    if (!this.responseText) return renderFallback();
    return `
      <article class="chat-row character-response" data-testid="character-response" data-phase="${escapeHtml(this.phase)}">
        <div class="row-meta">
          <span class="role-chip character" data-testid="role-label">${escapeHtml(this.roleLabel)}</span>
          <strong>${escapeHtml(this.characterName)}</strong>
          ${this.phase ? `<span class="phase-badge">${escapeHtml(this.phase)}</span>` : ""}
          ${this.presentation.render()}
        </div>
        <p>${escapeHtml(this.responseText)}</p>
      </article>
    `;
  }
}

export class DisplaySuperChatEvent {
  constructor(values = {}) {
    assignDisplayOrder(this, values);
    this.eventType = "super_chat";
    this.authorDisplayName = cleanText(values.authorDisplayName || values.author_display_name || translate("audience", "Audience"));
    this.messageText = cleanText(values.messageText || values.message_text || values.text || "");
    this.amountDisplayString = cleanText(values.amountDisplayString || values.amount_display_string || values.amount || "");
    this.currency = cleanText(values.currency || "");
    this.acknowledgementStatus = cleanText(values.acknowledgementStatus || values.acknowledgement_status || "");
  }

  static fromEvent(event = {}) {
    return new DisplaySuperChatEvent(requiredPayload(event));
  }

  render() {
    return `
      <article class="chat-row super-chat" data-testid="super-chat">
        <div class="row-meta">
          <span class="role-chip super">${escapeHtml(translate("super_chat", "Super Chat"))}</span>
          <strong>${escapeHtml(this.authorDisplayName)}</strong>
          <span class="amount">${escapeHtml(this.amountDisplayString || this.currency)}</span>
          ${this.acknowledgementStatus ? `<span class="ack">${escapeHtml(this.acknowledgementStatus)}</span>` : ""}
        </div>
        <p>${escapeHtml(this.messageText)}</p>
      </article>
    `;
  }
}

export class DisplaySystemStateEvent {
  constructor(values = {}) {
    assignDisplayOrder(this, values);
    const publicSummary = objectValue(values.public_summary || values.publicSummary);
    const eventType = cleanText(values.eventType || values.event_type || "");
    const impliedPhase = eventType === "closing_status"
      ? "closing"
      : eventType === "aftertalk_status"
        ? "aftertalk"
        : "";
    this.eventType = "system_state";
    this.phase = cleanText(values.phase || publicSummary.phase || impliedPhase || "unknown");
    this.aftertalkStatus = cleanText(
      values.aftertalkStatus
      || values.aftertalk_status
      || publicSummary.aftertalk_status
      || (this.phase === "aftertalk" ? values.status || publicSummary.status : "")
    );
    this.closingStatus = cleanText(
      values.closingStatus
      || values.closing_status
      || values.finalization_status
      || publicSummary.closing_completion_status
      || (this.phase === "closing" ? values.status || publicSummary.status : "")
    );
    this.message = cleanText(values.message || publicSummary.message || "");
    this.statusLabel = this.phase === "closing"
      ? translate("closing", "Closing")
      : this.phase === "aftertalk"
        ? translate("aftertalk", "Aftertalk")
        : translate("system", "System");
  }

  static fromEvent(event = {}) {
    return new DisplaySystemStateEvent(requiredPayload(event));
  }

  render() {
    const status = this.closingStatus || this.aftertalkStatus || this.phase;
    const message = this.message || `${this.statusLabel}: ${status}`;
    return `
      <aside class="status-banner" data-testid="status-banner" data-phase="${escapeHtml(this.phase)}">
        <strong>${escapeHtml(this.statusLabel)}</strong>
        <span>${escapeHtml(message)}</span>
      </aside>
    `;
  }
}

export function renderDisplayEvent(event = {}) {
  try {
    return displayRenderable(event).render();
  } catch {
    return renderFallback();
  }
}

export function renderDisplayEvents(events = []) {
  return events
    .map((event, index) => ({event: safeDisplayRenderable(event), index}))
    .sort(compareDisplayEvents)
    .map(({event}) => event.render())
    .join("");
}

export function connectDisplayStream({
  sessionId,
  eventSourceFactory = defaultEventSourceFactory,
  onEvent = () => {},
  onStale = () => {},
} = {}) {
  if (!sessionId || typeof eventSourceFactory !== "function") return null;
  const endpoint = `/v2/sessions/${encodeURIComponent(sessionId)}/display-stream`;
  const source = eventSourceFactory(endpoint);
  if (!source) return null;

  source.onmessage = (event) => {
    const payload = parseStreamPayload(event?.data);
    if (!payload) return;
    try {
      onEvent(normalizeDisplayEvent(payload));
    } catch {
      onEvent({eventType: "fallback", render: renderFallback});
    }
  };
  source.onerror = () => {
    onStale({
      stream_state: "stale",
      message: translate("stream_stale", "Display stream is stale"),
    });
  };
  return source;
}

export async function initChatDisplayI18n(i18n = globalThis.MCI18N) {
  if (i18n?.init) {
    await i18n.init();
  }
  if (i18n?.apply && typeof document !== "undefined") {
    i18n.apply(document);
  }
}

export function mountChatDisplay({
  root,
  sessionId,
  eventSourceFactory = defaultEventSourceFactory,
  initialEvents = [],
} = {}) {
  const target = root || document.getElementById("chatDisplayRoot");
  if (!target) return;
  if (!sessionId) {
    target.innerHTML = DisplaySystemStateEvent.fromEvent({
      event_type: "system_state",
      public_payload: {
        phase: "unknown",
        message: translate("missing_session_id", "Missing session_id"),
      },
    }).render();
    return;
  }
  const events = [];

  const render = () => {
    target.innerHTML = renderDisplayEvents(events);
  };

  for (const event of initialEvents) {
    events.push(normalizeDisplayEvent(event));
  }
  render();

  connectDisplayStream({
    sessionId,
    eventSourceFactory,
    onEvent: (event) => {
      events.push(event);
      render();
    },
    onStale: (state) => {
      events.push(DisplaySystemStateEvent.fromEvent({
        event_type: "system_state",
        public_payload: {
          phase: "unknown",
          message: state.message,
        },
      }));
      render();
    },
  });
}

function normalizeDisplayEvent(event = {}) {
  const type = String(event.event_type || event.type || "").toLowerCase();
  if (type === "audience_message" || type === "display_message") {
    return DisplayMessageEvent.fromEvent(event);
  }
  if (type === "character_response" || type === "display_character_response") {
    return DisplayCharacterResponseEvent.fromEvent(event);
  }
  if (type === "super_chat" || type === "display_super_chat") {
    return DisplaySuperChatEvent.fromEvent(event);
  }
  if (
    type === "system_state"
    || type === "phase_update"
    || type === "display_system_state"
    || type === "closing_status"
    || type === "aftertalk_status"
  ) {
    return DisplaySystemStateEvent.fromEvent(event);
  }
  throw new Error("unsupported display event");
}

function requiredPayload(event) {
  const payload = event.public_payload ?? event.payload ?? event;
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new Error("malformed display event");
  }
  const safePayload = sanitizePublicValue(payload);
  return {
    ...eventDisplayMetadata(event, safePayload),
    ...safePayload,
  };
}

function eventDisplayMetadata(event, safePayload) {
  return sanitizePublicValue({
    created_at: event.created_at ?? event.createdAt ?? safePayload.created_at,
    display_sequence: event.display_sequence ?? safePayload.display_sequence,
    event_id: event.event_id ?? event.id ?? safePayload.event_id,
    event_type: event.event_type ?? event.type ?? safePayload.event_type,
    message: event.message ?? safePayload.message,
    metadata: event.metadata ?? safePayload.metadata,
    public_summary: event.public_summary ?? safePayload.public_summary,
    sequence: event.sequence ?? safePayload.sequence,
    status: event.status ?? safePayload.status,
    timestamp: event.timestamp ?? safePayload.timestamp,
  });
}

function sanitizePublicValue(value) {
  if (Array.isArray(value)) {
    return value.map((item) => sanitizePublicValue(item));
  }
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value)
        .filter(([key]) => !isPrivateKey(key))
        .map(([key, inner]) => [key, sanitizePublicValue(inner)]),
    );
  }
  if (typeof value === "string") {
    return cleanText(value);
  }
  return value;
}

function isPrivateKey(key) {
  const lowered = String(key).toLowerCase();
  return PRIVATE_KEYS.has(lowered) || lowered.includes("operator_only");
}

function cleanText(value) {
  const text = String(value || "");
  const lowered = text.toLowerCase();
  if (PRIVATE_TEXT.some((key) => lowered.includes(key))) {
    return "";
  }
  return text;
}

function displayRenderable(event = {}) {
  if (event && typeof event.render === "function") return event;
  return normalizeDisplayEvent(event);
}

function safeDisplayRenderable(event = {}) {
  try {
    return displayRenderable(event);
  } catch {
    return {sequence: null, createdAt: "", eventId: "", render: renderFallback};
  }
}

function compareDisplayEvents(left, right) {
  const leftKey = displaySortKey(left.event, left.index);
  const rightKey = displaySortKey(right.event, right.index);
  for (let index = 0; index < leftKey.length; index += 1) {
    if (leftKey[index] < rightKey[index]) return -1;
    if (leftKey[index] > rightKey[index]) return 1;
  }
  return 0;
}

function displaySortKey(event, index) {
  const sequence = toFiniteNumber(event.sequence);
  if (sequence !== null) return [0, sequence, index];
  const timestamp = Date.parse(event.createdAt || event.timestamp || "");
  if (Number.isFinite(timestamp)) return [1, timestamp, index];
  return [2, index];
}

function assignDisplayOrder(target, values = {}) {
  target.sequence = toFiniteNumber(values.sequence ?? values.display_sequence ?? values.order);
  target.createdAt = cleanText(values.createdAt || values.created_at || "");
  target.eventId = cleanText(values.eventId || values.event_id || values.id || "");
}

function toFiniteNumber(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function objectValue(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function renderDisplayFlags(flags = {}) {
  const labels = normalizeDisplayFlags(flags);
  if (!labels.length) return "";
  return labels
    .map((label) => `<span class="display-flag" data-testid="display-flag">${escapeHtml(label)}</span>`)
    .join("");
}

function normalizeDisplayFlags(flags = {}) {
  const keys = Array.isArray(flags)
    ? flags
    : Object.entries(objectValue(flags))
      .filter(([, value]) => value === true || value === "true" || value === 1)
      .map(([key]) => key);
  const labels = [];
  const seen = new Set();
  for (const key of keys) {
    const normalized = normalizeDisplayFlagKey(key);
    if (seen.has(normalized) || !DISPLAY_FLAG_LABELS[normalized]) continue;
    seen.add(normalized);
    const [i18nKey, fallback] = DISPLAY_FLAG_LABELS[normalized];
    labels.push(translate(i18nKey, fallback));
  }
  return labels;
}

function normalizeDisplayFlagKey(key) {
  return cleanText(key).toLowerCase().replace(/[\s-]+/g, "_");
}

function renderFallback() {
  return `
    <article class="chat-row fallback" data-testid="display-fallback">
      <span class="role-chip">${escapeHtml(translate("system", "System"))}</span>
      <p>${escapeHtml(translate("fallback", "Display event unavailable"))}</p>
    </article>
  `;
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

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
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

if (typeof document !== "undefined") {
  const initPage = async () => {
    await initChatDisplayI18n();
    const root = document.getElementById("chatDisplayRoot");
    const sessionId = root?.dataset.sessionId || new URLSearchParams(location.search).get("session_id");
    if (root) {
      mountChatDisplay({root, sessionId});
    }
  };
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initPage);
  } else {
    initPage();
  }
}
