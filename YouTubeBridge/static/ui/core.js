if (new URLSearchParams(window.location.search).get("embedded") === "control") {
  document.body.classList.add("embedded-control");
}
export const SINGLE_CONNECTOR_ID = "youtube-main";

export const state = {
  sessions: [],
  connectors: [],
  connector: null,
  characters: [],
  maxSessionCharacters: 6,
  topicPacks: [],
  topicEntries: [],
  currentTopicEntryId: 0,
  topicEntryEditorBusy: false,
  factCardImportBusy: false,
  events: [],
  eventSource: null,
  chatPreviewRefreshTimer: null,
  selectedEventIds: new Set(),
};

export const $ = (id) => document.getElementById(id);
export const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  "\"": "&quot;",
  "'": "&#39;",
}[ch]));
export const log = (message, data) => {
  const time = new Date().toLocaleTimeString();
  const safeData = sanitizeLogData(data);
  const line = safeData === undefined ? `[${time}] ${message}` : `[${time}] ${message}\n${JSON.stringify(safeData, null, 2)}`;
  $("log").textContent = `${line}\n\n${$("log").textContent}`.slice(0, 12000);
};
export const clearLog = () => {
  $("log").textContent = "";
};

export function sanitizeLogData(value, depth = 0) {
  if (value === undefined) return undefined;
  if (value === null) return null;
  if (Array.isArray(value)) {
    if (value.length > 16 && value.every((item) => typeof item === "number")) {
      return `[embedding ${value.length} dims]`;
    }
    return value.slice(0, 24).map((item) => sanitizeLogData(item, depth + 1));
  }
  if (typeof value === "object") {
    const output = {};
    for (const [key, raw] of Object.entries(value)) {
      if (["embedding", "embeddings", "embedding_vector", "embedding_blob", "vector"].includes(key)) {
        output[key] = Array.isArray(raw) ? `[embedding ${raw.length} dims]` : "[hidden embedding]";
        continue;
      }
      if (["prompt", "decision_prompt", "hidden_context", "external_context", "context_text"].includes(key)) {
        output[key] = "[hidden]";
        continue;
      }
      if (["events", "event_ids", "super_chats"].includes(key) && Array.isArray(raw)) {
        output[key] = { count: raw.length };
        continue;
      }
      if (key === "decision" && raw && typeof raw === "object") {
        output[key] = {
          action: raw.action,
          reason: raw.reason,
          current_topic: raw.current_topic,
        };
        continue;
      }
      output[key] = depth >= 3 ? "[nested]" : sanitizeLogData(raw, depth + 1);
    }
    return output;
  }
  if (typeof value === "string" && value.length > 800) {
    return `${value.slice(0, 240)}... [truncated ${value.length} chars]`;
  }
  return value;
}

export function summarizeSsePayload(payload) {
  const eventCount = payload.event_count
    ?? payload.events?.length
    ?? payload.event_ids?.length
    ?? payload.count
    ?? payload.generated_count;
  return {
    type: payload.type,
    session_id: payload.session_id,
    job_id: payload.job_id || payload.interaction?.job_id,
    status: payload.status || payload.interaction?.status,
    source: payload.source || payload.interaction?.source,
    event_count: eventCount,
    pack_id: payload.pack_id || payload.entry?.pack_id,
    entry_count: payload.entry_count || payload.entries?.length,
  };
}

export function installTestIds() {
  [
    "sessionId", "videoId", "characterSelect", "characterLimitState",
    "injectInterval", "injectMinIntervalSeconds", "plannedDuration", "scInterruptCooldown", "sessionTopicPackSelect", "autoInject", "autoFinalize",
    "autoScThanksOnFinalize", "researchEnabled", "toggleSession", "updateSession",
    "testEventMinSeconds", "testEventMaxSeconds", "testEventCountPerTick",
    "testSuperChatCountPerTick", "autoTestEvents", "generateTestEvents", "eventsList",
    "directorGuidance", "directorIdle", "topicPackSelect",
    "topicEntrySelect", "updateTopicPack", "deleteTopicPack", "deleteAllTopicPacks", "updateTopicEntry", "cancelTopicEntryEdit",
    "topicEntryPanel", "importFactCardsFolder", "factCardImportOverlay", "factCardImportMessage",
    "topicFactCardLiveLockNotice", "liveSessionPane", "eventsPane", "summaryPane", "topicPackPane", "systemSettingsPane",
    "runtimeRulesPane", "reloadRuntimeRules", "runtimeRulesContent",
    "log"
  ].forEach((id) => {
    const element = $(id);
    if (element && !element.dataset.testid) element.dataset.testid = id;
  });
}

export function positionHelpTooltip(tip) {
  if (!tip) return;
  tip.classList.remove("tooltip-left");
  const rect = tip.getBoundingClientRect();
  const tooltipWidth = Math.min(280, window.innerWidth * 0.72);
  const margin = 16;
  const hasRightRoom = window.innerWidth - rect.right >= tooltipWidth + margin;
  const hasLeftRoom = rect.left >= tooltipWidth + margin;
  if (!hasRightRoom && hasLeftRoom) {
    tip.classList.add("tooltip-left");
  }
}

export function installTooltipPositioning(root = document) {
  root.querySelectorAll(".help-tip").forEach((tip) => {
    tip.addEventListener("mouseenter", () => positionHelpTooltip(tip));
    tip.addEventListener("focus", () => positionHelpTooltip(tip));
    tip.addEventListener("mouseleave", () => tip.classList.remove("tooltip-left"));
    tip.addEventListener("blur", () => tip.classList.remove("tooltip-left"));
  });
}

let _bridgeKey = "";

export async function initBridgeKey() {
  try {
    const cfg = await fetch("/ui-config").then((r) => r.json());
    _bridgeKey = cfg.bridge_key || "";
  } catch {
    _bridgeKey = "";
  }
}

export async function api(path, options = {}) {
  const keyHeaders = _bridgeKey ? { "X-Bridge-Key": _bridgeKey } : {};
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...keyHeaders,
      ...(options.headers || {}),
    },
  });
  const text = await response.text();
  let data = {};
  try { data = text ? JSON.parse(text) : {}; } catch { data = { raw: text }; }
  if (!response.ok) throw new Error(data.detail ? JSON.stringify(data.detail) : text || response.statusText);
  return data;
}
