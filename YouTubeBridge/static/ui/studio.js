const $ = (id) => document.getElementById(id);
const CHAT_PREVIEW_VISIBLE_LIMIT = 120;
const MAIN_THREAD_PROBE_INTERVAL_MS = 250;

const state = {
  live: false,
  sessionId: "",
  currentSession: null,
  eventSource: null,
  chatRefreshTimer: null,
  visibleMessages: new Map(),
  visibleLiveEvents: new Map(),
  localLiveEventSerial: 0,
  startedAt: null,
  elapsedTimer: null,
  autoCommentTimer: null,
  autoCommentQueue: [],
  autoCommentTotal: 0,
  autoCommentSent: 0,
  autoCommentInFlight: false,
  sourceStatus: "idle",
  detectedVideoId: "",
  detectedLiveChatId: "",
  messageCount: 0,
  bridgeKey: "",
  saveTimers: {},
  loadingSettings: false,
  episodePlans: [],
  loadingEpisodePlans: false,
  planCharacters: [],
  roleLoadError: "",
  personaOverlays: [],
  ttsProfiles: [],
  avatarAssets: [],
  freeTalkTopicPacks: [],
  freeTalkSidecar: { found: false, topic_count: 0, warnings: [] },
  selectedFreeTalkTopicPackIds: [],
  savedFreeTalkTopicPackIds: null,
  freeTalkTopicSelectionInitialized: false,
  presentationQueue: [],
  presentationPlaying: false,
  currentPresentationItem: null,
  currentAudio: null,
  audioUnlockRequired: false,
  presentationAckInFlight: false,
  presentationAudioCache: new Map(),
  summaryLoading: false,
  mainThreadProbeTimer: null,
  mainThreadProbeExpectedAt: 0,
  mainThreadLastLagMs: 0,
  mainThreadMaxLagMs: 0,
};

const normalCommentSamples = [
  "這個工具適合團隊共用嗎？",
  "可以補充實際使用情境嗎？",
  "想知道免費版限制會不會很嚴格。",
  "這段整理很清楚，想聽更多自動化案例。",
  "如果跟 Notion 搭配會怎麼設計？",
];

const superChatSamples = [
  "SC 支持！想問新手要先學哪個工具？",
  "感謝分享，這段可以之後剪成精華嗎？",
  "想看你們示範一個完整工作流。",
];

const ttsSourcePresets = {
  "runtime/YouTubeBridge/TTSSource/sakura/default.wav": {
    promptText: "這是直播角色的範例語音。",
    promptLang: "zh",
  },
  "runtime/YouTubeBridge/TTSSource/alan/default.wav": {
    promptText: "這是直播角色的範例語音。",
    promptLang: "zh",
  },
};

const rolePersonaDrafts = {};
const maxAvatarBytes = 2 * 1024 * 1024;
const avatarMimeTypes = new Set(["image/png", "image/jpeg", "image/webp", "image/gif"]);

const clock = $("studioClock");
const planSelect = $("planSelect");
const planStatusBadge = $("planStatusBadge");
const planStateText = $("planStateText");
const subtitle = $("conversationSubtitle");
const startButton = $("startButton");
const stopButton = $("stopButton");
const liveBadge = $("liveBadge");
const leftStatusBadge = $("leftStatusBadge");
const sessionDot = $("sessionDot");
const sessionStateText = $("sessionStateText");
const startedAtText = $("startedAtText");
const durationBadge = $("durationBadge");
const feed = $("conversationFeed");
const debugLog = $("debugLog");
const sourceDetectionState = $("sourceDetectionState");
const detectedVideoId = $("detectedVideoId");
const detectedLiveChatId = $("detectedLiveChatId");
const detectSourceButton = $("detectSourceButton");
const manualVideoInput = $("manualVideoInput");
const roleBindingState = $("roleBindingState");
const roleSummaryList = $("roleSummaryList");
const refreshRolesButton = $("refreshRolesButton");
const openRoleSettingsButton = $("openRoleSettingsButton");
const livePersonaAddressingFields = $("livePersonaAddressingFields");
const preflightPlan = $("preflightPlan");
const preflightSource = $("preflightSource");
const preflightRoles = $("preflightRoles");
const preflightSettings = $("preflightSettings");
const startBlockReason = $("startBlockReason");
const testMessage = $("testMessage");
const testCount = $("testCount");
const testResult = $("testResult");
const summaryPreview = $("summaryPreview");
const testAutoSaveState = $("testAutoSaveState");
const autoCommentEnabled = $("autoCommentEnabled");
const normalCommentCount = $("normalCommentCount");
const superChatCount = $("superChatCount");
const maliciousCommentEnabled = $("maliciousCommentEnabled");
const commentFrequencySeconds = $("commentFrequencySeconds");
const autoCommentStatus = $("autoCommentStatus");
const liveSettingsSummary = $("liveSettingsSummary");
const connectorStatusBadge = $("connectorStatusBadge");
const connectorApiKeyInput = $("connectorApiKeyInput");
const memoriaAuthState = $("memoriaAuthState");
const memoriaBaseUrl = $("memoriaBaseUrl");
const memoriaUsername = $("memoriaUsername");
const memoriaPassword = $("memoriaPassword");
const systemAutoSaveState = $("systemAutoSaveState");
const livePersonaCharacterSelect = $("livePersonaCharacterSelect");
const livePersonaSaveState = $("livePersonaSaveState");
const livePersonaAvatarSelect = $("livePersonaAvatarSelect");
const livePersonaAvatarFile = $("livePersonaAvatarFile");
const uploadAvatarButton = $("uploadAvatarButton");
const reloadFreeTalkTopicsButton = $("reloadFreeTalkTopicsButton");
const freeTalkTopicStats = $("freeTalkTopicStats");
const freeTalkSidecarState = $("freeTalkSidecarState");
const freeTalkTopicChecklist = $("freeTalkTopicChecklist");
const startFreeTalkTestButton = $("startFreeTalkTestButton");
const freeTalkTestState = $("freeTalkTestState");
const skipMainToFreeTalkButton = $("skipMainToFreeTalkButton");
const skipMainToFreeTalkState = $("skipMainToFreeTalkState");

const liveSettingControls = [
  "injectIntervalSeconds",
  "injectMinIntervalSeconds",
  "minPendingComments",
  "pendingForceLimit",
  "autoInjectPendingEnabled",
  "plannedDurationMinutes",
  "autoFinalizeAtLimit",
  "thankUnhandledSuperChats",
  "clearRuntimeSessionAfterSummary",
  "postPlanFreeTalkEnabled",
  "postPlanFreeTalkMinutes",
  "freeTalkClosingTargetBatches",
  "freeTalkClosingMinBatchSize",
  "freeTalkClosingMaxBatchSize",
  "freeTalkClosingTimeLimitSeconds",
  "superChatCooldownSeconds",
  "superChatBatchLimit",
  "safeSearchEnabled",
  "showLiveEventsEnabled",
  "presentationQueueEnabled",
  "ttsEnabled",
].map((id) => $(id));

async function initStudioApi() {
  try {
    const response = await fetch("/ui-config");
    const config = await response.json();
    state.bridgeKey = config.bridge_key || "";
  } catch {
    state.bridgeKey = "";
  }
}

async function api(path, options = {}) {
  const keyHeaders = state.bridgeKey ? { "X-Bridge-Key": state.bridgeKey } : {};
  const body = options.body && typeof options.body !== "string"
    ? JSON.stringify(options.body)
    : options.body;
  const response = await fetch(path, {
    ...options,
    body,
    headers: {
      "Content-Type": "application/json",
      ...keyHeaders,
      ...(options.headers || {}),
    },
  });
  const text = await response.text();
  let data = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = { raw: text };
  }
  if (!response.ok) {
    throw new Error(data.detail ? String(data.detail) : text || response.statusText);
  }
  return data;
}

function debounceAutoSave(key, callback, delay = 500) {
  if (state.saveTimers[key]) clearTimeout(state.saveTimers[key]);
  state.saveTimers[key] = setTimeout(() => {
    delete state.saveTimers[key];
    callback();
  }, delay);
}

function nowTime() {
  return new Date().toLocaleTimeString("zh-TW", { hour12: false });
}

function updateClock() {
  clock.textContent = nowTime();
}

function formatDuration(ms) {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

function updateDuration() {
  if (!state.live || !state.startedAt) {
    durationBadge.textContent = "直播時間 00:00:00";
    return;
  }
  durationBadge.textContent = `直播時間 ${formatDuration(Date.now() - state.startedAt.getTime())}`;
}

function selectedEpisodePlan() {
  const planId = planSelect.value;
  return state.episodePlans.find((plan) => plan.plan_id === planId) || null;
}

function episodePlanTurnCount(plan) {
  const segments = plan?.plan_json?.segments;
  if (!Array.isArray(segments)) return 0;
  return segments.reduce((count, segment) => (
    count + (Array.isArray(segment?.planned_turn_contracts) ? segment.planned_turn_contracts.length : 0)
  ), 0);
}

function episodePlanLabel(plan) {
  const title = String(plan?.title || plan?.plan_id || "").trim();
  const planId = String(plan?.plan_id || "").trim();
  return title && planId && title !== planId ? `${planId}｜${title}` : title || planId;
}

function renderEpisodePlanOptions(plans) {
  const previous = planSelect.value;
  planSelect.innerHTML = "";
  if (!plans.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "尚無可用 LiveEpisodePlan";
    planSelect.append(option);
    planSelect.disabled = true;
    subtitle.textContent = "尚未選擇 LiveEpisodePlan";
    updatePlanState();
    return;
  }
  plans.forEach((plan) => {
    const option = document.createElement("option");
    option.value = plan.plan_id || "";
    option.textContent = episodePlanLabel(plan);
    planSelect.append(option);
  });
  planSelect.disabled = false;
  if (previous && plans.some((plan) => plan.plan_id === previous)) {
    planSelect.value = previous;
  }
  subtitle.textContent = planSelect.options[planSelect.selectedIndex]?.textContent || "";
  updatePlanState();
}

async function loadEpisodePlans() {
  state.loadingEpisodePlans = true;
  planStatusBadge.textContent = "載入中";
  planStatusBadge.className = "state-badge warn";
  planStateText.textContent = "正在同步本地 LiveEpisodePlan";
  try {
    await api("/episode-plans/sync-local?max_files=200", { method: "POST" });
    const plans = await api("/episode-plans?limit=100");
    state.episodePlans = Array.isArray(plans) ? plans : [];
    renderEpisodePlanOptions(state.episodePlans);
    await loadEpisodePlanCharacters(planSelect.value);
    await loadFreeTalkTopics({ quiet: true });
    appendLog("INFO", `LiveEpisodePlan 已載入：${state.episodePlans.length} 筆`);
  } catch (error) {
    state.episodePlans = [];
    renderEpisodePlanOptions([]);
    await loadEpisodePlanCharacters("");
    await loadFreeTalkTopics({ quiet: true });
    appendLog("WARN", `LiveEpisodePlan 載入失敗：${error.message || error}`);
  } finally {
    state.loadingEpisodePlans = false;
  }
}

function shouldShowLiveEvents() {
  return $("showLiveEventsEnabled").checked;
}

function activeRoleCount() {
  return state.planCharacters.length;
}

function settingsAreValid() {
  return liveSettingControls.every((control) => {
    if (!control || control.type !== "number") return true;
    const value = Number(control.value);
    const min = control.min === "" ? -Infinity : Number(control.min);
    const max = control.max === "" ? Infinity : Number(control.max);
    return Number.isFinite(value) && value >= min && value <= max;
  });
}

function startReadiness() {
  const hasPlan = Boolean(planSelect.value);
  const rolesReady = activeRoleCount() > 0;
  const settingsReady = settingsAreValid();
  const idle = !state.live
    && state.sourceStatus !== "starting"
    && state.sourceStatus !== "closing"
    && state.sourceStatus !== "closing_failed";
  return {
    hasPlan,
    rolesReady,
    settingsReady,
    idle,
    canStart: hasPlan && rolesReady && settingsReady && idle,
  };
}

function updateCheckItem(item, status, title, detail) {
  item.className = `check-item ${status}`;
  item.querySelector("strong").textContent = title;
  item.querySelector("small").textContent = detail;
}

function updatePlanState() {
  const hasPlan = Boolean(planSelect.value);
  const plan = selectedEpisodePlan();
  const turnCount = episodePlanTurnCount(plan);
  planStatusBadge.textContent = hasPlan ? "企劃已載入" : "未選擇企劃";
  planStatusBadge.className = hasPlan ? "state-badge good" : "state-badge warn";
  planStateText.textContent = hasPlan
    ? `已選擇 ${plan?.plan_id || planSelect.value} · ${turnCount} 個 planned turns`
    : "請先建立或同步 LiveEpisodePlan";
  updatePreflightChecklist();
}

function selectedFreeTalkTopicPackIds() {
  const available = new Set(state.freeTalkTopicPacks.map((pack) => pack.pack_id).filter(Boolean));
  return state.selectedFreeTalkTopicPackIds.filter((packId) => available.has(packId));
}

function setSelectedFreeTalkTopicPackIds(packIds) {
  const available = new Set(state.freeTalkTopicPacks.map((pack) => pack.pack_id).filter(Boolean));
  state.selectedFreeTalkTopicPackIds = Array.from(new Set(packIds.filter((packId) => available.has(packId))));
}

function updateFreeTalkAllCheckbox(allCheckbox) {
  const selectedCount = selectedFreeTalkTopicPackIds().length;
  allCheckbox.checked = state.freeTalkTopicPacks.length > 0 && selectedCount === state.freeTalkTopicPacks.length;
  allCheckbox.indeterminate = selectedCount > 0 && selectedCount < state.freeTalkTopicPacks.length;
}

function freeTalkSidecarLabel(sidecar = state.freeTalkSidecar) {
  if (sidecar?.found) {
    return `目前企劃 sidecar：已找到 ${sidecar.topic_count || 0} 則話題`;
  }
  return "目前企劃 sidecar：未找到 free-talk-topics.json";
}

function renderFreeTalkTopicChecklist(result = {}) {
  state.freeTalkTopicPacks = Array.isArray(result.packs) ? result.packs : [];
  state.freeTalkSidecar = result.sidecar || { found: false, topic_count: 0, warnings: [] };
  const allPackIds = state.freeTalkTopicPacks.map((pack) => pack.pack_id).filter(Boolean);
  if (state.savedFreeTalkTopicPackIds !== null) {
    setSelectedFreeTalkTopicPackIds(state.savedFreeTalkTopicPackIds);
    state.freeTalkTopicSelectionInitialized = true;
  } else if (!state.freeTalkTopicSelectionInitialized) {
    state.selectedFreeTalkTopicPackIds = [...allPackIds];
    state.freeTalkTopicSelectionInitialized = true;
  } else {
    setSelectedFreeTalkTopicPackIds(state.selectedFreeTalkTopicPackIds);
  }

  freeTalkTopicStats.textContent = `全域話題庫 ${state.freeTalkTopicPacks.length} 組，總話題 ${result.total_topic_count || 0} 則`;
  freeTalkSidecarState.textContent = freeTalkSidecarLabel(state.freeTalkSidecar);
  freeTalkTopicChecklist.replaceChildren();

  const allRow = document.createElement("label");
  allRow.className = "check-row setting-check";
  const allCheckbox = document.createElement("input");
  allCheckbox.id = "freeTalkTopicAll";
  allCheckbox.type = "checkbox";
  const allText = document.createElement("span");
  allText.textContent = "全部話題庫";
  allRow.append(allCheckbox, allText);
  freeTalkTopicChecklist.append(allRow);

  allCheckbox.addEventListener("change", () => {
    state.selectedFreeTalkTopicPackIds = allCheckbox.checked ? [...allPackIds] : [];
    state.savedFreeTalkTopicPackIds = [...state.selectedFreeTalkTopicPackIds];
    renderFreeTalkTopicChecklist({ ...result, packs: state.freeTalkTopicPacks, sidecar: state.freeTalkSidecar });
    applySystemAutoSaveState("雜談話題庫");
  });

  state.freeTalkTopicPacks.forEach((pack) => {
    const row = document.createElement("label");
    row.className = "check-row setting-check";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = pack.pack_id || "";
    input.checked = selectedFreeTalkTopicPackIds().includes(pack.pack_id);
    const text = document.createElement("span");
    text.textContent = `${pack.display_name || pack.pack_id}（${pack.topic_count || 0}）`;
    input.addEventListener("change", () => {
      const selected = new Set(selectedFreeTalkTopicPackIds());
      if (input.checked) selected.add(pack.pack_id);
      else selected.delete(pack.pack_id);
      setSelectedFreeTalkTopicPackIds(Array.from(selected));
      state.savedFreeTalkTopicPackIds = selectedFreeTalkTopicPackIds();
      updateFreeTalkAllCheckbox(allCheckbox);
      applySystemAutoSaveState("雜談話題庫");
    });
    row.append(input, text);
    freeTalkTopicChecklist.append(row);
  });

  updateFreeTalkAllCheckbox(allCheckbox);
}

async function loadFreeTalkTopics({ quiet = false } = {}) {
  try {
    const result = await api(`/studio/free-talk-topics?episode_plan_id=${encodeURIComponent(planSelect.value || "")}`);
    renderFreeTalkTopicChecklist(result);
    if (!quiet) {
      appendLog("INFO", `雜談話題庫已載入：${state.freeTalkTopicPacks.length} 組`);
    }
  } catch (error) {
    freeTalkTopicStats.textContent = "話題庫載入失敗";
    freeTalkSidecarState.textContent = "目前企劃 sidecar：檢查失敗";
    if (!quiet) {
      appendLog("WARN", `雜談話題庫載入失敗：${error.message || error}`);
    }
  }
}

function roleName(character) {
  return character?.name || character?.participant_display_name || character?.character_id || "未命名角色";
}

function roleFunctions(character) {
  return Array.isArray(character?.role_function)
    ? character.role_function.map((item) => String(item || "").trim()).filter(Boolean)
    : [];
}

function roleFunctionLabel(character) {
  const roles = roleFunctions(character);
  if (roles.includes("host")) return "主持";
  if (roles.includes("cohost")) return "共同主持";
  if (roles.includes("analyst")) return "分析";
  if (roles.includes("skeptic")) return "質疑";
  if (roles.includes("closer")) return "收束";
  return roles[0] || "角色";
}

function defaultAddressingForRole(roleId) {
  const currentRoleId = String(roleId || "").trim();
  if (!currentRoleId) return {};
  return Object.fromEntries(
    state.planCharacters
      .filter((character) => character.character_id && character.character_id !== currentRoleId)
      .map((character) => [character.character_id, roleName(character)])
  );
}

function defaultPersonaDraftForCharacter(_character = {}) {
  const palette = rolePalette(_character.character_id || roleName(_character));
  return {
    selfAddress: "我",
    avatarUrl: _character.avatar_url || "",
    chatBackgroundColor: _character.chat_background_color || palette.bg,
    chatAccentColor: _character.chat_accent_color || palette.accent,
    systemPrompt: "",
    openingIntro: "",
    addressing: defaultAddressingForRole(_character.character_id),
    replyRules: "",
    tts: {
      enabled: false,
      sourcePreset: "",
      refAudioPath: "",
      promptText: "",
      textLang: "zh",
      promptLang: "zh",
      speedFactor: "1",
      mediaType: "wav",
    },
  };
}

function ensureRolePersonaDraft(character) {
  const roleId = String(character?.character_id || "").trim();
  if (!roleId) return null;
  if (!rolePersonaDrafts[roleId]) {
    rolePersonaDrafts[roleId] = defaultPersonaDraftForCharacter(character);
  }
  return rolePersonaDrafts[roleId];
}

function appendEmptyRoleState(message) {
  roleSummaryList.replaceChildren();
  livePersonaCharacterSelect.replaceChildren();
  const summary = document.createElement("span");
  summary.textContent = message;
  roleSummaryList.append(summary);
  const option = document.createElement("option");
  option.value = "";
  option.textContent = "尚未載入角色";
  livePersonaCharacterSelect.append(option);
}

function renderPlanCharacters(characters = state.planCharacters) {
  const previousRoleId = selectedRoleId();
  roleSummaryList.replaceChildren();
  livePersonaCharacterSelect.replaceChildren();

  if (!characters.length) {
    appendEmptyRoleState(state.roleLoadError || "請先選擇 LiveEpisodePlan");
    fillLivePersonaFormForSelectedRole();
    updateRoleBindingState();
    return;
  }

  characters.forEach((character, index) => {
    const roleId = String(character.character_id || "").trim();
    if (!roleId) return;
    ensureRolePersonaDraft(character);
    const name = roleName(character);
    const roleLabel = roleFunctionLabel(character);

    const summary = document.createElement("span");
    summary.textContent = `${roleLabel}：${name}`;
    roleSummaryList.append(summary);

    const option = document.createElement("option");
    option.value = roleId;
    option.textContent = `${name}（${roleLabel}）`;
    livePersonaCharacterSelect.append(option);
  });

  if (previousRoleId && characters.some((character) => character.character_id === previousRoleId)) {
    livePersonaCharacterSelect.value = previousRoleId;
  }
  fillLivePersonaFormForSelectedRole();
  updateRoleBindingState();
}

async function loadEpisodePlanCharacters(planId) {
  state.roleLoadError = "";
  if (!planId) {
    state.planCharacters = [];
    renderPlanCharacters([]);
    return;
  }
  roleBindingState.textContent = "正在讀取企劃角色";
  try {
    const data = await api(`/episode-plans/${encodeURIComponent(planId)}/characters`);
    state.planCharacters = Array.isArray(data.characters) ? data.characters : [];
    state.roleLoadError = state.planCharacters.length ? "" : "企劃沒有可用直播角色";
    state.personaOverlays.forEach((overlay) => ensureRolePersonaDraft({ character_id: overlay.character_id }));
    state.ttsProfiles.forEach((profile) => ensureRolePersonaDraft({ character_id: profile.character_id }));
    applyPersonaSettings(state.personaOverlays, state.ttsProfiles);
    renderPlanCharacters(state.planCharacters);
    appendLog("INFO", `企劃角色已載入：${state.planCharacters.length} 位`);
  } catch (error) {
    state.planCharacters = [];
    state.roleLoadError = error.message || String(error);
    renderPlanCharacters([]);
    appendLog("WARN", `企劃角色載入失敗：${state.roleLoadError}`);
  }
}

function updateRoleBindingState() {
  const active = activeRoleCount();
  const blocked = !planSelect.value || Boolean(state.roleLoadError) || active === 0;
  let text = "請先選擇 LiveEpisodePlan";
  if (planSelect.value && state.roleLoadError) {
    text = `企劃角色對應失敗：${state.roleLoadError}`;
  } else if (active > 0) {
    text = `企劃角色已對應 · ${active} 位角色`;
  } else if (planSelect.value) {
    text = "企劃尚未解析出直播角色";
  }
  roleBindingState.textContent = text;
  roleBindingState.classList.toggle("is-blocked", blocked);
  updatePreflightChecklist();
}

function selectedRoleId() {
  return livePersonaCharacterSelect.value || "";
}

function selectedRoleDraft() {
  const roleId = selectedRoleId();
  if (!roleId) return defaultPersonaDraftForCharacter();
  const character = state.planCharacters.find((item) => item.character_id === roleId) || { character_id: roleId };
  return ensureRolePersonaDraft(character);
}

function setInputValue(id, value) {
  const control = $(id);
  if (control) control.value = value ?? "";
}

function setInputChecked(id, checked) {
  const control = $(id);
  if (control) control.checked = Boolean(checked);
}

function avatarAssetLabel(asset) {
  const size = Number(asset.size_bytes || 0);
  const sizeLabel = size >= 1024 ? `${Math.round(size / 1024)} KB` : `${size} B`;
  return `${asset.name || "local-avatar"} · ${sizeLabel}`;
}

function renderAvatarAssetOptions(selectedUrl = $("livePersonaAvatarUrl")?.value || "") {
  if (!livePersonaAvatarSelect) return;
  livePersonaAvatarSelect.replaceChildren();
  const empty = document.createElement("option");
  empty.value = "";
  empty.textContent = state.avatarAssets.length ? "未選取本地頭像" : "尚未上傳本地頭像";
  livePersonaAvatarSelect.append(empty);
  state.avatarAssets.forEach((asset) => {
    if (!asset?.url) return;
    const option = document.createElement("option");
    option.value = asset.url;
    option.textContent = avatarAssetLabel(asset);
    livePersonaAvatarSelect.append(option);
  });
  livePersonaAvatarSelect.value = state.avatarAssets.some((asset) => asset.url === selectedUrl) ? selectedUrl : "";
}

async function loadAvatarAssets(selectedUrl = $("livePersonaAvatarUrl")?.value || "") {
  try {
    const data = await api("/studio/avatar-assets");
    state.avatarAssets = Array.isArray(data.avatars) ? data.avatars : [];
    renderAvatarAssetOptions(selectedUrl);
  } catch (error) {
    appendLog("WARN", `本地頭像清單載入失敗：${error.message || error}`);
  }
}

function readAvatarFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(new Error("頭像檔案讀取失敗"));
    reader.readAsDataURL(file);
  });
}

async function saveLivePersonaOverlayNow(roleId, draft, source = "角色設定") {
  if (!roleId || state.loadingSettings) return;
  markSavingState(livePersonaSaveState, source);
  try {
    await api(`/persona-overlays/${encodeURIComponent(roleId)}`, {
      method: "POST",
      body: livePersonaOverlayPayload(draft),
    });
    markAutoSaveState(livePersonaSaveState, source);
  } catch (error) {
    markSaveError(livePersonaSaveState, source, error);
    appendLog("WARN", `角色頭像儲存失敗：${error.message || error}`);
  }
}

function applyAvatarUrl(url, source = "角色頭像") {
  const avatarUrl = String(url || "").trim();
  if (!avatarUrl) return;
  setInputValue("livePersonaAvatarUrl", avatarUrl);
  renderAvatarAssetOptions(avatarUrl);
  const roleId = selectedRoleId();
  if (!roleId) return;
  const draft = {
    ...selectedRoleDraft(),
    avatarUrl,
  };
  rolePersonaDrafts[roleId] = draft;
  saveLivePersonaOverlayNow(roleId, draft, source);
}

async function uploadLocalAvatar() {
  const file = livePersonaAvatarFile?.files?.[0];
  if (!file) {
    setAutoSaveState(livePersonaSaveState, "error", "請先選取本地頭像檔案");
    return;
  }
  if (!avatarMimeTypes.has(file.type)) {
    setAutoSaveState(livePersonaSaveState, "error", "頭像只支援 PNG/JPEG/WebP/GIF");
    return;
  }
  if (file.size > maxAvatarBytes) {
    setAutoSaveState(livePersonaSaveState, "error", "頭像檔案不可超過 2MB");
    return;
  }
  markSavingState(livePersonaSaveState, "角色頭像");
  try {
    const dataUrl = await readAvatarFileAsDataUrl(file);
    const selected = await api("/studio/avatar-assets", {
      method: "POST",
      body: {
        filename: file.name,
        data_url: dataUrl,
      },
    });
    await loadAvatarAssets(selected.url);
    applyAvatarUrl(selected.url, "角色頭像");
    appendLog("INFO", `本地頭像已上傳：${selected.name || selected.url}`);
  } catch (error) {
    markSaveError(livePersonaSaveState, "角色頭像", error);
    appendLog("WARN", `本地頭像上傳失敗：${error.message || error}`);
  } finally {
    if (livePersonaAvatarFile) livePersonaAvatarFile.value = "";
  }
}

const roleFormControlIds = [
  "livePersonaSelfAddress",
  "livePersonaAvatarUrl",
  "livePersonaAvatarSelect",
  "livePersonaChatBackgroundColor",
  "livePersonaChatAccentColor",
  "livePersonaSystemPrompt",
  "livePersonaOpeningIntro",
  "livePersonaReplyRules",
  "liveTtsSourcePreset",
  "liveTtsEnabled",
  "liveTtsRefAudioPath",
  "liveTtsPromptText",
  "liveTtsTextLang",
  "liveTtsPromptLang",
  "liveTtsSpeedFactor",
  "liveTtsMediaType",
];

function setRoleFormDisabled(disabled) {
  roleFormControlIds.forEach((id) => {
    const control = $(id);
    if (control) control.disabled = disabled;
  });
  if (livePersonaAvatarFile) livePersonaAvatarFile.disabled = disabled;
  if (uploadAvatarButton) uploadAvatarButton.disabled = disabled;
}

function setAutoSaveState(element, status, message) {
  if (!element) return;
  element.textContent = message;
  element.className = `inline-status is-${status}`;
}

function fillLivePersonaFormForSelectedRole() {
  const roleId = selectedRoleId();
  if (!roleId) {
    setRoleFormDisabled(true);
    livePersonaAddressingFields.replaceChildren();
    setInputValue("livePersonaSelfAddress", "");
    setInputValue("livePersonaAvatarUrl", "");
    renderAvatarAssetOptions("");
    setInputValue("livePersonaChatBackgroundColor", "#f2fbff");
    setInputValue("livePersonaChatAccentColor", "#0d9488");
    setInputValue("livePersonaSystemPrompt", "");
    setInputValue("livePersonaOpeningIntro", "");
    setInputValue("livePersonaReplyRules", "");
    setInputChecked("liveTtsEnabled", false);
    setInputValue("liveTtsSourcePreset", "");
    setInputValue("liveTtsRefAudioPath", "");
    setInputValue("liveTtsPromptText", "");
    setInputValue("liveTtsTextLang", "zh");
    setInputValue("liveTtsPromptLang", "zh");
    setInputValue("liveTtsSpeedFactor", "1");
    setInputValue("liveTtsMediaType", "wav");
    setAutoSaveState(livePersonaSaveState, "idle", "請先選擇有角色的 LiveEpisodePlan");
    return;
  }
  setRoleFormDisabled(false);
  const draft = selectedRoleDraft();
  const tts = draft.tts || {};
  setInputValue("livePersonaSelfAddress", draft.selfAddress || "");
  setInputValue("livePersonaAvatarUrl", draft.avatarUrl || "");
  renderAvatarAssetOptions(draft.avatarUrl || "");
  setInputValue("livePersonaChatBackgroundColor", draft.chatBackgroundColor || rolePalette(roleId).bg);
  setInputValue("livePersonaChatAccentColor", draft.chatAccentColor || rolePalette(roleId).accent);
  setInputValue("livePersonaSystemPrompt", draft.systemPrompt || "");
  setInputValue("livePersonaOpeningIntro", draft.openingIntro || "");
  renderLivePersonaAddressingFields(draft.addressing || defaultAddressingForRole(roleId));
  setInputValue("livePersonaReplyRules", draft.replyRules || "");
  setInputChecked("liveTtsEnabled", tts.enabled);
  setInputValue("liveTtsSourcePreset", tts.sourcePreset || "");
  setInputValue("liveTtsRefAudioPath", tts.refAudioPath || "");
  setInputValue("liveTtsPromptText", tts.promptText || "");
  setInputValue("liveTtsTextLang", tts.textLang || "zh");
  setInputValue("liveTtsPromptLang", tts.promptLang || "zh");
  setInputValue("liveTtsSpeedFactor", tts.speedFactor || "1");
  setInputValue("liveTtsMediaType", tts.mediaType || "wav");
  setAutoSaveState(livePersonaSaveState, "idle", "角色設定已載入；變更會自動儲存");
}

function renderLivePersonaAddressingFields(addressing = {}) {
  const roleId = selectedRoleId();
  const targets = state.planCharacters.filter((character) => (
    character.character_id && character.character_id !== roleId
  ));
  livePersonaAddressingFields.replaceChildren();
  if (!targets.length) {
    const empty = document.createElement("div");
    empty.className = "empty-inline";
    empty.textContent = "沒有其他角色需要設定互稱";
    livePersonaAddressingFields.append(empty);
    return;
  }
  targets.forEach((target) => {
    const row = document.createElement("label");
    row.className = "addressing-row";
    const label = document.createElement("span");
    label.textContent = `稱呼 ${roleName(target)}`;
    const input = document.createElement("input");
    input.className = "addressing-input";
    input.type = "text";
    input.maxLength = 120;
    input.autocomplete = "off";
    input.dataset.targetCharacterId = target.character_id;
    input.value = addressing[target.character_id] || roleName(target);
    input.addEventListener("input", () => autoSaveLivePersonaSettings("角色互稱"));
    input.addEventListener("change", () => autoSaveLivePersonaSettings("角色互稱"));
    row.append(label, input);
    livePersonaAddressingFields.append(row);
  });
}

function readLivePersonaAddressingFields() {
  return Object.fromEntries(
    Array.from(livePersonaAddressingFields.querySelectorAll(".addressing-input"))
      .map((input) => [
        input.dataset.targetCharacterId,
        input.value.trim(),
      ])
      .filter(([targetId, address]) => targetId && address)
  );
}

function readLivePersonaForm() {
  return {
    selfAddress: $("livePersonaSelfAddress").value.trim(),
    avatarUrl: $("livePersonaAvatarUrl").value.trim(),
    chatBackgroundColor: $("livePersonaChatBackgroundColor").value || "",
    chatAccentColor: $("livePersonaChatAccentColor").value || "",
    systemPrompt: $("livePersonaSystemPrompt").value.trim(),
    openingIntro: $("livePersonaOpeningIntro").value.trim(),
    addressing: readLivePersonaAddressingFields(),
    replyRules: $("livePersonaReplyRules").value.trim(),
    tts: {
      enabled: $("liveTtsEnabled").checked,
      sourcePreset: $("liveTtsSourcePreset").value,
      refAudioPath: $("liveTtsRefAudioPath").value.trim(),
      promptText: $("liveTtsPromptText").value.trim(),
      textLang: $("liveTtsTextLang").value.trim() || "zh",
      promptLang: $("liveTtsPromptLang").value.trim() || "zh",
      speedFactor: $("liveTtsSpeedFactor").value || "1",
      mediaType: $("liveTtsMediaType").value || "wav",
    },
  };
}

function livePersonaOverlayPayload(draft) {
  return {
    enabled: true,
    mode: "replace",
    system_prompt: draft.systemPrompt,
    self_address: draft.selfAddress,
    avatar_url: draft.avatarUrl,
    chat_background_color: draft.chatBackgroundColor,
    chat_accent_color: draft.chatAccentColor,
    addressing: draft.addressing || {},
    opening_intro: draft.openingIntro,
    reply_rules: draft.replyRules,
  };
}

function liveTtsProfilePayload(draft) {
  return {
    enabled: draft.tts.enabled,
    ref_audio_path: draft.tts.refAudioPath,
    prompt_text: draft.tts.promptText,
    text_lang: draft.tts.textLang,
    prompt_lang: draft.tts.promptLang,
    speed_factor: Number(draft.tts.speedFactor || 1),
    media_type: draft.tts.mediaType,
  };
}

function markAutoSaveState(element, source) {
  setAutoSaveState(element, "saved", `${source}已自動儲存`);
}

function markSavingState(element, source) {
  setAutoSaveState(element, "saving", `${source}儲存中`);
}

function markSaveError(element, source, error) {
  setAutoSaveState(element, "error", `${source}儲存失敗：${error.message || error}`);
}

function switchRoleEditorSection(nextSection) {
  document.querySelectorAll(".role-section-tab").forEach((button) => {
    const active = button.dataset.roleSection === nextSection;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
  });
  document.querySelectorAll(".role-editor-panel").forEach((panel) => {
    const active = panel.id === `role${nextSection[0].toUpperCase()}${nextSection.slice(1)}Panel`;
    panel.classList.toggle("active", active);
    panel.hidden = !active;
  });
}

function applyLiveTtsSourcePreset() {
  const selected = $("liveTtsSourcePreset").value;
  if (!selected) {
    autoSaveLivePersonaSettings("聲音 TTS");
    return;
  }
  const preset = ttsSourcePresets[selected] || {};
  setInputValue("liveTtsRefAudioPath", selected);
  setInputValue("liveTtsPromptText", preset.promptText || "");
  setInputValue("liveTtsPromptLang", preset.promptLang || "zh");
  autoSaveLivePersonaSettings("聲音 TTS");
}

function scheduleLivePersonaDraftSave(roleId, draft, source = "角色設定") {
  if (!roleId || state.loadingSettings) return;
  if (draft.tts.enabled && (!draft.tts.refAudioPath || !draft.tts.promptText)) {
    setAutoSaveState(livePersonaSaveState, "error", "TTS 啟用時需要範例語音路徑與 transcript");
    return;
  }
  markSavingState(livePersonaSaveState, source);
  debounceAutoSave(`role-settings-${roleId}`, async () => {
    try {
      await api(`/persona-overlays/${encodeURIComponent(roleId)}`, {
        method: "POST",
        body: livePersonaOverlayPayload(draft),
      });
      await api(`/persona-overlays/${encodeURIComponent(roleId)}/tts-profile`, {
        method: "POST",
        body: liveTtsProfilePayload(draft),
      });
      markAutoSaveState(livePersonaSaveState, source);
    } catch (error) {
      markSaveError(livePersonaSaveState, source, error);
      appendLog("WARN", `角色設定儲存失敗：${error.message || error}`);
    }
  });
}

function autoSaveLivePersonaSettings(source = "角色設定") {
  if (state.loadingSettings) return;
  const roleId = selectedRoleId();
  if (!roleId) return;
  const draft = readLivePersonaForm();
  rolePersonaDrafts[roleId] = draft;
  scheduleLivePersonaDraftSave(roleId, draft, source);
}

function parseManualVideoId() {
  const value = manualVideoInput.value.trim();
  if (!value) return "";
  try {
    const url = new URL(value);
    return url.searchParams.get("v") || url.pathname.split("/").filter(Boolean).pop() || value;
  } catch {
    return value;
  }
}

function updateSourceDetectionState() {
  const session = state.currentSession || {};
  const hasRealSource = Boolean(state.detectedVideoId || state.detectedLiveChatId);
  const manualVideoId = parseManualVideoId();
  if (state.sourceStatus === "starting") {
    sourceDetectionState.textContent = "啟動中";
    sourceDetectionState.className = "state-badge warn";
  } else if (state.sourceStatus === "closing") {
    sourceDetectionState.textContent = "收尾中";
    sourceDetectionState.className = "state-badge warn";
  } else if (state.sourceStatus === "closing_failed") {
    sourceDetectionState.textContent = "收尾失敗";
    sourceDetectionState.className = "state-badge warn";
  } else if (state.live && hasRealSource) {
    sourceDetectionState.textContent = "正式直播";
    sourceDetectionState.className = "state-badge good";
  } else if (state.live) {
    sourceDetectionState.textContent = "測試模式";
    sourceDetectionState.className = "state-badge good";
  } else if (manualVideoId) {
    sourceDetectionState.textContent = "手動指定";
    sourceDetectionState.className = "state-badge warn";
  } else {
    sourceDetectionState.textContent = "手動/測試模式";
    sourceDetectionState.className = "state-badge neutral";
  }
  detectedVideoId.textContent = state.detectedVideoId || (state.live ? "測試模式" : "尚未指定");
  detectedLiveChatId.textContent = state.detectedLiveChatId || (state.live ? "測試模式" : "尚未解析");
  detectSourceButton.disabled = state.sourceStatus === "starting";
  if (session.session_id && session.status === "stopped") {
    sourceDetectionState.textContent = "已停止";
    sourceDetectionState.className = "state-badge neutral";
  }
  updatePreflightChecklist();
}

function applyStartButtonState() {
  const readiness = startReadiness();
  if (state.live) {
    startButton.disabled = true;
    startButton.textContent = "直播中";
  } else if (state.sourceStatus === "closing") {
    startButton.disabled = true;
    startButton.textContent = "收尾中...";
  } else if (state.sourceStatus === "closing_failed") {
    startButton.disabled = true;
    startButton.textContent = "收尾失敗";
  } else if (state.sourceStatus === "starting") {
    startButton.disabled = true;
    startButton.textContent = "啟動直播中...";
  } else {
    startButton.disabled = !readiness.canStart;
    startButton.textContent = "開始直播";
  }
  stopButton.disabled = (!state.live && state.sourceStatus !== "closing_failed") || state.sourceStatus === "closing";
  stopButton.textContent = state.sourceStatus === "closing_failed" ? "重試收尾" : "收尾";
}

function updatePreflightChecklist() {
  const readiness = startReadiness();
  updateCheckItem(
    preflightPlan,
    readiness.hasPlan ? "ready" : "blocked",
    readiness.hasPlan ? "企劃已選擇" : "尚未選擇企劃",
    readiness.hasPlan ? "LiveEpisodePlan 已載入" : "請先選擇目前要直播的企劃",
  );

  if (state.sourceStatus === "detected") {
    updateCheckItem(preflightSource, "ready", "直播來源已偵測", `${state.detectedVideoId} · ${state.detectedLiveChatId}`);
  } else if (state.sourceStatus === "starting") {
    updateCheckItem(preflightSource, "waiting", "正在啟動直播", "正在建立 Live Session 並等待後端回傳來源狀態");
  } else if (state.live && (state.detectedVideoId || state.detectedLiveChatId)) {
    updateCheckItem(preflightSource, "ready", "正式直播來源已就緒", `${state.detectedVideoId || "video_id 待補"} · ${state.detectedLiveChatId || "live_chat_id 待補"}`);
  } else if (state.live) {
    updateCheckItem(preflightSource, "ready", "測試模式直播中", "手動來源留空，後端以 test mode 啟動");
  } else if (parseManualVideoId()) {
    updateCheckItem(preflightSource, "ready", "手動來源已填入", "開始直播後交由後端解析 video_id / live_chat_id");
  } else {
    updateCheckItem(preflightSource, "waiting", "測試模式待命", "手動來源留空時會建立 test session");
  }

  updateCheckItem(
    preflightRoles,
    readiness.rolesReady ? "ready" : "blocked",
    readiness.rolesReady ? "企劃角色已對應" : "企劃角色未就緒",
    readiness.rolesReady ? `目前 ${activeRoleCount()} 位企劃角色可用` : "請確認 LiveEpisodePlan 角色能對應到 MemoriaCore 角色",
  );
  updateCheckItem(
    preflightSettings,
    readiness.settingsReady ? "ready" : "blocked",
    readiness.settingsReady ? "直播設定有效" : "直播設定需要修正",
    readiness.settingsReady ? "注入、SC、收尾設定可用" : "請檢查右側直播設定數值範圍",
  );

  if (!readiness.hasPlan) {
    startBlockReason.textContent = "請先選擇直播企劃。";
  } else if (!readiness.rolesReady) {
    startBlockReason.textContent = state.roleLoadError
      ? `企劃角色對應失敗：${state.roleLoadError}`
      : "請確認 LiveEpisodePlan 角色能對應到 MemoriaCore 角色。";
  } else if (!readiness.settingsReady) {
    startBlockReason.textContent = "請修正右側直播設定的數值範圍。";
  } else if (state.sourceStatus === "starting") {
    startBlockReason.textContent = "正在建立 Live Session。";
  } else if (parseManualVideoId()) {
    startBlockReason.textContent = "可以開始直播；手動來源會交由後端解析。";
  } else {
    startBlockReason.textContent = "可以開始直播；手動來源留空會使用測試模式。";
  }
  applyStartButtonState();
}

function appendLog(level, message) {
  debugLog.querySelector(".log-empty")?.remove();
  const item = document.createElement("li");
  const time = document.createElement("time");
  const levelNode = document.createElement("span");
  const messageNode = document.createElement("strong");
  time.textContent = nowTime();
  levelNode.textContent = `[${level}]`;
  messageNode.textContent = message;
  item.append(time, levelNode, messageNode);
  debugLog.prepend(item);
  while (debugLog.children.length > 8) {
    debugLog.lastElementChild?.remove();
  }
}

const presentationDebugLabels = {
  item_created: "建立句子",
  item_synthesizing: "開始合成",
  item_prefetch_ready: "預載完成，等待導播交付",
  item_ready: "合成完成",
  item_presenting: "送往播放器",
  ack_wait_start: "等待 ACK",
  ack_received: "收到 ACK",
  ack_timeout: "ACK 逾時",
  item_skipped: "跳過句子",
  client_playback_timeline: "前端播放時間線",
  audio_play_blocked: "瀏覽器阻擋播放",
  audio_retry_blocked: "重試播放被阻擋",
};

function appendPresentationDebugLog(event) {
  if (!event || typeof event !== "object") return;
  const phase = String(event.phase || "unknown");
  const label = presentationDebugLabels[phase] || phase;
  const itemId = event.item_id ? `：${event.item_id}` : "";
  const status = event.status ? ` / ${event.status}` : "";
  const wait = event.timeout_seconds ? ` / ${event.timeout_seconds}s` : "";
  const error = event.error ? ` / ${event.error}` : "";
  const level = phase.includes("timeout") || phase.includes("blocked") || phase.includes("failed")
    ? "WARN"
    : "DEBUG";
  appendLog(level, `TTS Queue ${label}${itemId}${status}${wait}${error}`);
}

function reportPresentationClientDebug(phase, item, details = {}) {
  if (!state.sessionId) return Promise.resolve(null);
  return api(`/sessions/${encodeURIComponent(state.sessionId)}/presentation/debug`, {
    method: "POST",
    body: {
      phase,
      item_id: item?.item_id || "",
      status: item?.status || "",
      details,
    },
  }).catch((error) => {
    appendLog("WARN", `TTS Debug 回報失敗：${error.message || error}`);
    return null;
  });
}

function audioTimingSnapshot(audio) {
  if (!audio) return {};
  return {
    audio_ready_state: audio.readyState,
    audio_network_state: audio.networkState,
    audio_current_time: Number.isFinite(audio.currentTime) ? Number(audio.currentTime.toFixed(3)) : null,
    audio_duration: Number.isFinite(audio.duration) ? Number(audio.duration.toFixed(3)) : null,
    audio_paused: Boolean(audio.paused),
  };
}

function startMainThreadProbe() {
  if (state.mainThreadProbeTimer) return;
  state.mainThreadProbeExpectedAt = performance.now() + MAIN_THREAD_PROBE_INTERVAL_MS;
  state.mainThreadProbeTimer = setInterval(() => {
    const now = performance.now();
    const lagMs = Math.max(0, now - state.mainThreadProbeExpectedAt);
    state.mainThreadLastLagMs = Number(lagMs.toFixed(3));
    state.mainThreadMaxLagMs = Math.max(state.mainThreadMaxLagMs, state.mainThreadLastLagMs);
    state.mainThreadProbeExpectedAt = now + MAIN_THREAD_PROBE_INTERVAL_MS;
  }, MAIN_THREAD_PROBE_INTERVAL_MS);
}

function clientRuntimeSnapshot() {
  const now = performance.now();
  const pendingLagMs = state.mainThreadProbeExpectedAt
    ? Math.max(0, now - state.mainThreadProbeExpectedAt)
    : 0;
  const currentLagMs = Math.max(state.mainThreadLastLagMs || 0, pendingLagMs);
  const maxLagMs = Math.max(state.mainThreadMaxLagMs || 0, currentLagMs);
  state.mainThreadMaxLagMs = 0;
  return {
    document_visibility: document.visibilityState || "",
    document_has_focus: typeof document.hasFocus === "function" ? document.hasFocus() : null,
    main_thread_last_lag_ms: Number(currentLagMs.toFixed(3)),
    main_thread_max_lag_ms_since_last_presentation_sse: Number(maxLagMs.toFixed(3)),
  };
}

function recordPresentationClientTiming(phase, item, details = {}) {
  if (!item) return;
  if (!Array.isArray(item._clientTiming)) item._clientTiming = [];
  item._clientTiming.push({
    phase,
    client_time_ms: Number(performance.now().toFixed(3)),
    client_wall_time: new Date().toISOString(),
    queue_length: state.presentationQueue.length,
    current_item_id: state.currentPresentationItem?.item_id || "",
    ...details,
  });
}

function flushPresentationClientTiming(item, details = {}) {
  if (!item || !Array.isArray(item._clientTiming) || item._clientTiming.length === 0) return;
  const timeline = item._clientTiming.slice(-40);
  item._clientTiming = [];
  reportPresentationClientDebug("client_playback_timeline", item, {
    timeline,
    timeline_count: timeline.length,
    performance_time_origin: Number(performance.timeOrigin.toFixed(3)),
    ...details,
  });
}

function attachPresentationSseTiming(item, payload) {
  if (!item || !payload) return item;
  item._sseTiming = {
    event_received_time_ms: Number(performance.now().toFixed(3)),
    event_received_wall_time: new Date().toISOString(),
    server_broadcast_at: payload._broadcast_at || "",
    server_sse_yield_at: payload._sse_yield_at || "",
    sse_type: payload.type || "",
    ...clientRuntimeSnapshot(),
  };
  return item;
}

function appendMessage(kind, name, role, text) {
  state.messageCount += 1;
  const row = document.createElement("article");
  row.className = `chat-line ${kind}`;

  const time = document.createElement("time");
  time.className = "chat-time";
  time.textContent = nowTime();

  const mark = document.createElement("div");
  mark.className = "chat-avatar";
  mark.textContent = name.slice(0, 1);

  const copy = document.createElement("div");
  copy.className = "chat-copy";
  const title = document.createElement("strong");
  title.textContent = role ? `${name}（${role}）` : name;
  const body = document.createElement("p");
  body.textContent = text;
  copy.append(title, body, time);
  applyChatRoleVisuals(row, mark, { character_name: name, author_display_name: name }, kind);

  row.append(mark, copy);
  feed.prepend(row);
  feed.scrollTop = 0;
}

function appendLiveEventGroup(title, items) {
  if (!items.length) return;
  if (!shouldShowLiveEvents()) {
    appendLog("INFO", "直播事件顯示已關閉，留言僅保留於測試狀態");
    return;
  }
  const fallbackTime = new Date().toISOString();
  items.forEach((item) => {
    state.localLiveEventSerial += 1;
    rememberVisibleLiveEvent({
      ...item,
      event_id: item.event_id || item.id || item.youtube_message_id || `local-${state.localLiveEventSerial}`,
      published_at: item.published_at || item.received_at || item.created_at || item.timestamp || fallbackTime,
      summary: title,
    });
  });
  renderConversationTimeline();
}

function appendLiveEventItem(item, { prepend = true } = {}) {
  if (!item?.text) return;
  const eventId = liveEventKey(item);
  if (eventId && feed.querySelector(`[data-live-event-id="${CSS.escape(eventId)}"]`)) return;
  state.messageCount += 1;
  const row = document.createElement("article");
  row.className = "chat-line event";
  row.dataset.liveEventId = eventId;

  const time = document.createElement("time");
  time.className = "chat-time";
  time.textContent = chatPreviewTime({
    created_at: item.published_at || item.received_at || item.created_at || item.timestamp,
  });

  const mark = document.createElement("div");
  mark.className = "event-mark";
  mark.textContent = "EV";

  const copy = document.createElement("div");
  copy.className = "chat-copy event-copy";
  const heading = document.createElement("strong");
  heading.textContent = "直播事件";
  const summary = document.createElement("p");
  summary.textContent = item.summary || "YouTube Live 留言注入：1 則";
  const list = document.createElement("ul");
  list.className = "event-list";
  const line = document.createElement("li");
  const prefix = item.kind === "super" ? `[SC ${item.amount || "NT$75"}] ` : "";
  line.textContent = `${prefix}${item.name}: ${item.text}`;
  list.append(line);
  copy.append(heading, summary, list, time);

  row.append(mark, copy);
  if (prepend) feed.prepend(row);
  else feed.append(row);
  feed.scrollTop = 0;
}

function clearConversationFeed() {
  feed.innerHTML = "";
}

function renderConversationEmpty(message = "尚未開始直播；開始後會顯示後端 Live Session 的 AI 對話。") {
  clearConversationFeed();
  const empty = document.createElement("div");
  empty.className = "conversation-empty";
  empty.textContent = message;
  feed.append(empty);
}

function updatePresentationStatus(statusText = "語音待機", level = "neutral") {
  const status = $("presentationAudioStatus");
  if (!status) return;
  status.textContent = statusText;
  status.className = `state-badge ${level}`;
}

function setPresentationControls({ audioUnlock = false, canSkip = false } = {}) {
  const enableButton = $("enablePresentationAudio");
  const skipButton = $("skipPresentation");
  if (enableButton) enableButton.classList.toggle("hidden", !audioUnlock);
  if (skipButton) skipButton.disabled = !canSkip;
}

function stopAudioElement(audio) {
  if (!audio) return;
  audio.pause();
  audio.removeAttribute("src");
  audio.load();
}

function clearPresentationAudioCache() {
  state.presentationAudioCache.forEach((audio) => stopAudioElement(audio));
  state.presentationAudioCache.clear();
}

function stopCurrentPresentationAudio() {
  stopAudioElement(state.currentAudio);
  state.currentAudio = null;
}

function resetPresentationPlayer({ statusText = "語音待機" } = {}) {
  stopCurrentPresentationAudio();
  clearPresentationAudioCache();
  state.presentationQueue = [];
  state.presentationPlaying = false;
  state.currentPresentationItem = null;
  state.audioUnlockRequired = false;
  state.presentationAckInFlight = false;
  updatePresentationStatus(statusText, "neutral");
  setPresentationControls();
}

function presentationItemToMessage(item) {
  const now = new Date().toISOString();
  return {
    message_id: item.message_id || item.item_id,
    role: "assistant",
    content: item.text || "",
    created_at: now,
    timestamp: now,
    character_id: item.character_id || "",
    character_name: item.character_name || "AI",
    source: "presentation",
  };
}

function cachePresentationAudio(item) {
  const itemId = String(item?.item_id || "");
  const audioUrl = String(item?.audio_url || "");
  if (!itemId || !audioUrl || state.presentationAudioCache.has(itemId)) return;
  const audio = new Audio(audioUrl);
  audio.preload = "auto";
  recordPresentationClientTiming("preload_start", item, {
    audio_url_present: true,
    cache_size: state.presentationAudioCache.size,
    ...(item._sseTiming || {}),
    ...audioTimingSnapshot(audio),
  });
  audio.addEventListener("loadedmetadata", () => {
    recordPresentationClientTiming("preload_loadedmetadata", item, audioTimingSnapshot(audio));
  }, { once: true });
  audio.addEventListener("canplay", () => {
    recordPresentationClientTiming("preload_canplay", item, audioTimingSnapshot(audio));
  }, { once: true });
  audio.addEventListener("canplaythrough", () => {
    recordPresentationClientTiming("preload_canplaythrough", item, audioTimingSnapshot(audio));
  }, { once: true });
  audio.addEventListener("error", () => {
    recordPresentationClientTiming("preload_error", item, {
      ...audioTimingSnapshot(audio),
      error: audio.error?.message || String(audio.error?.code || "audio preload error"),
    });
  }, { once: true });
  state.presentationAudioCache.set(itemId, audio);
  appendLog("DEBUG", `已預載 TTS 音訊：${itemId}`);
}

function audioForPresentationItem(item) {
  const itemId = String(item?.item_id || "");
  const cached = itemId ? state.presentationAudioCache.get(itemId) : null;
  if (cached) {
    state.presentationAudioCache.delete(itemId);
    recordPresentationClientTiming("audio_cache_hit", item, audioTimingSnapshot(cached));
    return cached;
  }
  const audio = new Audio(item.audio_url || "");
  audio.preload = "auto";
  recordPresentationClientTiming("audio_cache_miss", item, {
    audio_url_present: Boolean(item.audio_url),
    ...audioTimingSnapshot(audio),
  });
  return audio;
}

async function ackPresentationItem(item) {
  if (!item?.item_id || !state.sessionId || state.presentationAckInFlight) return false;
  state.presentationAckInFlight = true;
  const ackStartedAt = performance.now();
  recordPresentationClientTiming("ack_start", item);
  try {
    await api(`/sessions/${encodeURIComponent(state.sessionId)}/presentation/${encodeURIComponent(item.item_id)}/ack`, {
      method: "POST",
    });
    recordPresentationClientTiming("ack_done", item, {
      ack_roundtrip_ms: Number((performance.now() - ackStartedAt).toFixed(3)),
    });
    flushPresentationClientTiming(item, {
      ack_ok: true,
    });
    appendLog("DEBUG", `TTS 播放完成並 ACK：${item.item_id}`);
    return true;
  } catch (error) {
    recordPresentationClientTiming("ack_failed", item, {
      ack_roundtrip_ms: Number((performance.now() - ackStartedAt).toFixed(3)),
      error: error?.message || String(error || "ack failed"),
    });
    flushPresentationClientTiming(item, {
      ack_ok: false,
      error: error?.message || String(error || "ack failed"),
    });
    appendLog("WARN", `TTS ACK 失敗：${error.message || error}`);
    await refreshStudioSession();
    return false;
  } finally {
    state.presentationAckInFlight = false;
  }
}

function isCurrentPresentationItem(item) {
  return Boolean(
    item?.item_id
    && state.currentPresentationItem?.item_id
    && item.item_id === state.currentPresentationItem.item_id
  );
}

async function finishPresentationItem(item, reason = "ended") {
  if (!isCurrentPresentationItem(item)) return;
  if (state.presentationAckInFlight) return;
  recordPresentationClientTiming("finish_start", item, { reason });
  state.presentationPlaying = true;
  state.audioUnlockRequired = false;
  setPresentationControls({ canSkip: false });
  updatePresentationStatus(reason === "error" ? "語音錯誤，送出文字" : "送出 ACK", reason === "error" ? "warn" : "neutral");
  const acked = await ackPresentationItem(item);
  if (!isCurrentPresentationItem(item)) return;
  stopCurrentPresentationAudio();
  state.currentPresentationItem = null;
  state.presentationPlaying = false;
  if (acked) {
    updatePresentationStatus("語音待機", "neutral");
    playPresentationItem();
  } else {
    updatePresentationStatus("ACK 失敗", "warn");
    setPresentationControls();
  }
}

function playPresentationItem() {
  if (state.presentationPlaying || state.audioUnlockRequired || state.currentPresentationItem) return;
  const item = state.presentationQueue.shift();
  if (!item?.item_id) {
    updatePresentationStatus("語音待機", "neutral");
    setPresentationControls();
    return;
  }
  state.presentationPlaying = true;
  state.currentPresentationItem = item;
  state.audioUnlockRequired = false;
  feed.querySelector(".conversation-empty")?.remove();
  appendChatPreviewMessage(presentationItemToMessage(item), { prepend: true });
  updatePresentationStatus("播放中", "good");
  setPresentationControls({ canSkip: true });

  if (!item.audio_url) {
    appendLog("WARN", `TTS 音訊未產生，改以文字送出：${item.item_id}`);
    recordPresentationClientTiming("text_fallback", item);
    finishPresentationItem(item, "text_fallback").catch((error) => {
      appendLog("WARN", `文字 fallback ACK 失敗：${error.message || error}`);
    });
    return;
  }

  const audio = audioForPresentationItem(item);
  state.currentAudio = audio;
  audio.addEventListener("playing", () => {
    if (!isCurrentPresentationItem(item) || state.currentAudio !== audio) return;
    recordPresentationClientTiming("audio_playing", item, audioTimingSnapshot(audio));
  }, { once: true });
  audio.addEventListener("waiting", () => {
    if (!isCurrentPresentationItem(item) || state.currentAudio !== audio) return;
    recordPresentationClientTiming("audio_waiting", item, audioTimingSnapshot(audio));
  });
  audio.addEventListener("ended", () => {
    if (!isCurrentPresentationItem(item) || state.currentAudio !== audio) return;
    recordPresentationClientTiming("audio_ended", item, audioTimingSnapshot(audio));
    finishPresentationItem(item, "ended").catch((error) => {
      appendLog("WARN", `TTS 完播處理失敗：${error.message || error}`);
    });
  }, { once: true });
  audio.addEventListener("error", () => {
    if (!isCurrentPresentationItem(item) || state.currentAudio !== audio) return;
    recordPresentationClientTiming("audio_error", item, {
      ...audioTimingSnapshot(audio),
      error: audio.error?.message || String(audio.error?.code || "audio error"),
    });
    finishPresentationItem(item, "error").catch((error) => {
      appendLog("WARN", `TTS 錯誤處理失敗：${error.message || error}`);
    });
  }, { once: true });
  const playStartedAt = performance.now();
  recordPresentationClientTiming("play_invoked", item, audioTimingSnapshot(audio));
  audio.play().then(() => {
    recordPresentationClientTiming("play_resolved", item, {
      play_promise_ms: Number((performance.now() - playStartedAt).toFixed(3)),
      ...audioTimingSnapshot(audio),
    });
  }).catch((error) => {
    if (!isCurrentPresentationItem(item) || state.currentAudio !== audio) return;
    recordPresentationClientTiming("play_blocked", item, {
      play_promise_ms: Number((performance.now() - playStartedAt).toFixed(3)),
      error: error?.message || String(error || "audio.play() rejected"),
      ...audioTimingSnapshot(audio),
    });
    flushPresentationClientTiming(item, {
      ack_ok: false,
      error: error?.message || String(error || "audio.play() rejected"),
    });
    state.presentationPlaying = false;
    state.audioUnlockRequired = true;
    updatePresentationStatus("等待啟用聲音", "warn");
    setPresentationControls({ audioUnlock: true, canSkip: true });
    appendLog("WARN", `TTS 播放被瀏覽器阻擋，等待啟用聲音：${item.item_id}`);
    reportPresentationClientDebug("audio_play_blocked", item, {
      phase: "audio_play_blocked",
      error: error?.message || String(error || "audio.play() rejected"),
      audio_url_present: Boolean(item.audio_url),
      queue_length: state.presentationQueue.length,
    });
  });
}

function enqueuePresentationItem(item) {
  if (!item?.item_id) return;
  recordPresentationClientTiming("ready_received", item, {
    queue_length_before_enqueue: state.presentationQueue.length,
    audio_url_present: Boolean(item.audio_url),
    ...(item._sseTiming || {}),
  });
  cachePresentationAudio(item);
  state.presentationQueue.push(item);
  appendLog("DEBUG", `收到 TTS 句子：${item.item_id}`);
  playPresentationItem();
}

async function retryCurrentPresentationAudio() {
  if (!state.currentPresentationItem || !state.currentAudio) return;
  const item = state.currentPresentationItem;
  const audio = state.currentAudio;
  state.audioUnlockRequired = false;
  state.presentationPlaying = true;
  updatePresentationStatus("播放中", "good");
  setPresentationControls({ canSkip: true });
  try {
    const playStartedAt = performance.now();
    recordPresentationClientTiming("retry_play_invoked", item, audioTimingSnapshot(audio));
    await audio.play();
    recordPresentationClientTiming("retry_play_resolved", item, {
      play_promise_ms: Number((performance.now() - playStartedAt).toFixed(3)),
      ...audioTimingSnapshot(audio),
    });
  } catch (error) {
    if (!isCurrentPresentationItem(item) || state.currentAudio !== audio) return;
    recordPresentationClientTiming("retry_play_blocked", item, {
      error: error?.message || String(error || "audio.play() rejected"),
      ...audioTimingSnapshot(audio),
    });
    flushPresentationClientTiming(item, {
      ack_ok: false,
      error: error?.message || String(error || "audio.play() rejected"),
    });
    state.presentationPlaying = false;
    state.audioUnlockRequired = true;
    updatePresentationStatus("等待啟用聲音", "warn");
    setPresentationControls({ audioUnlock: true, canSkip: true });
    appendLog("WARN", `TTS 重試播放仍被阻擋：${item.item_id}`);
    reportPresentationClientDebug("audio_retry_blocked", item, {
      phase: "audio_retry_blocked",
      error: error?.message || String(error || "audio.play() rejected"),
    });
  }
}

async function skipCurrentPresentation() {
  const currentItem = state.currentPresentationItem;
  const hadCurrent = Boolean(currentItem);
  stopCurrentPresentationAudio();
  state.presentationPlaying = false;
  state.audioUnlockRequired = false;
  updatePresentationStatus("跳過目前句子", "neutral");
  setPresentationControls();
  if (!hadCurrent) {
    playPresentationItem();
    return;
  }
  if (!state.sessionId) {
    state.currentPresentationItem = null;
    playPresentationItem();
    return;
  }
  try {
    await api(`/sessions/${encodeURIComponent(state.sessionId)}/presentation/current/skip`, {
      method: "POST",
    });
    appendLog("INFO", "已跳過目前 TTS 句子");
    if (state.currentPresentationItem === currentItem) {
      state.currentPresentationItem = null;
    }
    playPresentationItem();
  } catch (error) {
    state.currentPresentationItem = currentItem;
    updatePresentationStatus("跳過失敗，請重試", "warn");
    setPresentationControls({ canSkip: true });
    appendLog("WARN", `跳過 TTS 句子失敗：${error.message || error}`);
    await refreshStudioSession();
  }
}

async function handlePresentationInterrupt(payload = {}) {
  const currentItem = state.currentPresentationItem;
  const hadCurrent = Boolean(currentItem);
  stopCurrentPresentationAudio();
  clearPresentationAudioCache();
  state.presentationQueue = [];
  state.presentationPlaying = false;
  state.audioUnlockRequired = false;
  updatePresentationStatus("直播互動打斷", "warn");
  setPresentationControls();
  appendLog("INFO", `TTS 播放已被打斷：${payload.reason || payload.closure_text || "interaction"}`);
  if (hadCurrent && state.sessionId) {
    try {
      await api(`/sessions/${encodeURIComponent(state.sessionId)}/presentation/current/skip`, {
        method: "POST",
      });
      if (state.currentPresentationItem === currentItem) {
        state.currentPresentationItem = null;
      }
    } catch (error) {
      state.currentPresentationItem = currentItem;
      updatePresentationStatus("打斷解除失敗，請重試", "warn");
      setPresentationControls({ canSkip: true });
      appendLog("WARN", `打斷後解除 TTS 等待失敗：${error.message || error}`);
      await refreshStudioSession();
    }
  } else {
    state.currentPresentationItem = null;
  }
  scheduleConversationRefresh("直播打斷");
}

function summaryFromPayload(payload = {}) {
  if (payload.summary && typeof payload.summary === "object") return payload.summary;
  return payload;
}

function appendSummaryList(parent, title, items = [], formatter = (item) => String(item || "")) {
  const values = Array.isArray(items) ? items.map(formatter).filter((item) => String(item || "").trim()) : [];
  if (!values.length) return;
  const section = document.createElement("section");
  section.className = "summary-section";
  const heading = document.createElement("strong");
  heading.textContent = title;
  const list = document.createElement("ul");
  values.forEach((value) => {
    const item = document.createElement("li");
    item.textContent = value;
    list.append(item);
  });
  section.append(heading, list);
  parent.append(section);
}

function renderSummaryPreview(summary, emptyMessage = "尚未產生摘要；停止直播後可重新生成。") {
  summaryPreview.replaceChildren();
  if (!summary || !Object.keys(summary).length) {
    const empty = document.createElement("p");
    empty.className = "summary-empty";
    empty.textContent = emptyMessage;
    summaryPreview.append(empty);
    return;
  }

  const title = document.createElement("strong");
  title.className = "summary-title";
  title.textContent = summary.title || "YouTube Live 摘要";
  const overview = document.createElement("p");
  overview.textContent = summary.summary_text || summary.overview || "摘要未提供概述。";
  summaryPreview.append(title, overview);

  const topics = Array.isArray(summary.topic_tags) ? summary.topic_tags : summary.topics;
  appendSummaryList(summaryPreview, "主題", topics);
  appendSummaryList(summaryPreview, "重點", summary.key_points);
  appendSummaryList(summaryPreview, "問答", summary.qa_pairs, (pair) => {
    if (!pair || typeof pair !== "object") return "";
    const question = String(pair.question || "").trim();
    const answer = String(pair.answer || "").trim();
    if (!question && !answer) return "";
    return answer ? `${question}：${answer}` : question;
  });

  if (summary.audience_mood) {
    const mood = document.createElement("p");
    mood.className = "summary-meta";
    mood.textContent = `觀眾氛圍：${summary.audience_mood}`;
    summaryPreview.append(mood);
  }
  if (summary.metadata?.memory_text_requires_review) {
    const warning = document.createElement("p");
    warning.className = "summary-warning";
    warning.textContent = "記憶文字需要人工確認後再寫入 Shared Memory。";
    summaryPreview.append(warning);
  }
}

function updateSummaryControls() {
  const button = $("regenerateSummary");
  if (!button) return;
  button.textContent = state.summaryLoading ? "生成中" : "重新生成";
  if (state.summaryLoading) {
    button.disabled = true;
    return;
  }
  button.disabled = !state.sessionId || state.live;
  button.title = !state.sessionId
    ? "尚未建立 Live Session"
    : (state.live ? "請先停止直播再生成摘要" : "");
}

async function loadSessionSummary({ quiet = true } = {}) {
  if (!state.sessionId) {
    renderSummaryPreview(null);
    updateSummaryControls();
    return null;
  }
  try {
    const summary = await api(`/sessions/${encodeURIComponent(state.sessionId)}/summary`);
    renderSummaryPreview(summaryFromPayload(summary));
    if (!quiet) appendLog("INFO", "已載入既有 Summary");
    return summary;
  } catch (error) {
    const message = String(error.message || error);
    if (!message.includes("summary not found")) {
      renderSummaryPreview(null, `Summary 讀取失敗：${message}`);
      if (!quiet) appendLog("WARN", `Summary 讀取失敗：${message}`);
    } else if (state.live) {
      renderSummaryPreview(null, "直播中不產生摘要；停止直播後可重新生成。");
    } else {
      renderSummaryPreview(null);
    }
    updateSummaryControls();
    return null;
  }
}

function unsubscribeSessionEvents() {
  if (state.eventSource) {
    state.eventSource.close();
    state.eventSource = null;
  }
}

function resetConversationForNewSession() {
  unsubscribeSessionEvents();
  resetPresentationPlayer({ statusText: "建立新場次" });
  if (state.chatRefreshTimer) {
    clearTimeout(state.chatRefreshTimer);
    state.chatRefreshTimer = null;
  }
  state.currentSession = null;
  state.sessionId = "";
  state.messageCount = 0;
  state.visibleMessages.clear();
  state.visibleLiveEvents.clear();
  renderConversationEmpty("正在建立新的 Live Session，等待後端產生 AI 對話。");
}

function chatPreviewKind(message) {
  const characterId = String(message?.character_id || "");
  const name = String(message?.character_name || "");
  if (characterId.includes("cohost") || name.includes("艾倫")) return "cohost";
  if (String(message?.source || "") === "presentation") return "cohost";
  return "host";
}

const chatRolePalettes = [
  { bg: "#f2fbff", accent: "#0d9488" },
  { bg: "#f7f5ff", accent: "#3154b8" },
  { bg: "#fff7ed", accent: "#d97706" },
  { bg: "#f4fbf5", accent: "#16a34a" },
  { bg: "#fff5f7", accent: "#be3455" },
  { bg: "#f5f7fb", accent: "#64748b" },
];

function hashString(value) {
  return Array.from(String(value || "")).reduce((hash, char) => ((hash * 31) + char.charCodeAt(0)) >>> 0, 0);
}

function rolePalette(roleKey) {
  return chatRolePalettes[hashString(roleKey) % chatRolePalettes.length];
}

function characterRecordForMessage(message) {
  const characterId = String(message?.character_id || "").trim();
  const name = String(message?.character_name || message?.author_display_name || "").trim();
  return state.planCharacters.find((character) => (
    (characterId && character.character_id === characterId)
    || (name && (roleName(character) === name || character.participant_display_name === name))
  )) || {};
}

function avatarImageUrl(source = {}) {
  for (const key of ["avatar_url", "avatar", "image_url", "profile_image_url", "author_profile_image_url", "icon_url", "picture_url"]) {
    const value = String(source?.[key] || "").trim();
    if (value) return value;
  }
  return "";
}

function manualPaletteForMessage(source = {}, character = {}) {
  const roleId = String(source.character_id || character.character_id || "").trim();
  return {
    bg: rolePersonaDrafts[roleId]?.chatBackgroundColor || source.chat_background_color || character.chat_background_color || "",
    accent: rolePersonaDrafts[roleId]?.chatAccentColor || source.chat_accent_color || character.chat_accent_color || "",
    avatarUrl: rolePersonaDrafts[roleId]?.avatarUrl || source.avatar_url || character.avatar_url || "",
  };
}

function applyChatRoleVisuals(row, mark, source = {}, kind = "host") {
  const character = characterRecordForMessage(source);
  const roleKey = String(source.character_id || character.character_id || source.character_name || source.author_display_name || kind);
  const manualPalette = manualPaletteForMessage(source, character);
  const generatedPalette = rolePalette(roleKey || kind);
  const palette = {
    bg: manualPalette.bg || generatedPalette.bg,
    accent: manualPalette.accent || generatedPalette.accent,
  };
  row.style.setProperty("--chat-bg", palette.bg);
  row.style.setProperty("--chat-accent", palette.accent);
  row.style.setProperty("--avatar-bg", palette.accent);

  const imageUrl = manualPalette.avatarUrl || avatarImageUrl(source) || avatarImageUrl(character);
  if (imageUrl) {
    const image = document.createElement("img");
    image.src = imageUrl;
    image.alt = `${String(source.character_name || source.author_display_name || roleName(character) || "角色")} 頭像`;
    mark.textContent = "";
    mark.append(image);
  }
}

function chatPreviewTime(message) {
  const raw = message?.created_at || message?.timestamp || message?.published_at || "";
  if (!raw) return nowTime();
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) return nowTime();
  return date.toLocaleTimeString("zh-TW", { hour12: false });
}

function previewMessageKey(message = {}) {
  const messageId = String(message?.message_id || message?.id || "").trim();
  if (messageId) return `${message?.role || "message"}:${messageId}`;
  return `${message?.character_id || message?.character_name || message?.role || "message"}:${message?.created_at || message?.timestamp || ""}:${String(message?.content || message?.message_text || "").slice(0, 80)}`;
}

function previewMessageTimeValue(message = {}) {
  const raw = message?.created_at || message?.timestamp || message?.published_at || "";
  const value = raw ? new Date(raw).getTime() : 0;
  return Number.isFinite(value) ? value : 0;
}

function liveEventKey(item = {}) {
  const rawId = item?.event_id || item?.id || item?.youtube_message_id || "";
  if (rawId) return `event:${rawId}`;
  const content = String(item?.text || item?.message_text || "").trim();
  return `event:${item?.published_at || item?.received_at || item?.created_at || item?.timestamp || ""}:${item?.name || item?.author_display_name || ""}:${content.slice(0, 40)}`;
}

function liveEventTimeValue(item = {}) {
  const raw = item?.published_at || item?.received_at || item?.created_at || item?.timestamp || "";
  const value = raw ? new Date(raw).getTime() : 0;
  return Number.isFinite(value) ? value : 0;
}

function mergePreviewMessages(...groups) {
  const merged = new Map();
  groups.flat().forEach((message) => {
    const content = String(message?.content || message?.message_text || "").trim();
    if (!content) return;
    merged.set(previewMessageKey(message), message);
  });
  return Array.from(merged.values()).sort((left, right) => {
    const timeDelta = previewMessageTimeValue(left) - previewMessageTimeValue(right);
    if (timeDelta !== 0) return timeDelta;
    return previewMessageKey(left).localeCompare(previewMessageKey(right));
  });
}

function pruneVisibleLiveEvents(limit = CHAT_PREVIEW_VISIBLE_LIMIT) {
  const maxItems = Math.max(1, Number(limit) || CHAT_PREVIEW_VISIBLE_LIMIT);
  const kept = Array.from(state.visibleLiveEvents.values()).sort((left, right) => {
    const timeDelta = liveEventTimeValue(left) - liveEventTimeValue(right);
    if (timeDelta !== 0) return timeDelta;
    return liveEventKey(left).localeCompare(liveEventKey(right));
  }).slice(-maxItems);
  state.visibleLiveEvents.clear();
  kept.forEach((item) => {
    state.visibleLiveEvents.set(liveEventKey(item), item);
  });
}

function pruneVisibleMessages(limit = CHAT_PREVIEW_VISIBLE_LIMIT) {
  const maxItems = Math.max(1, Number(limit) || CHAT_PREVIEW_VISIBLE_LIMIT);
  const kept = mergePreviewMessages(Array.from(state.visibleMessages.values())).slice(-maxItems);
  state.visibleMessages.clear();
  kept.forEach((message) => {
    state.visibleMessages.set(previewMessageKey(message), message);
  });
}

function rememberVisibleMessage(message = {}) {
  const content = String(message?.content || message?.message_text || "").trim();
  if (!content) return;
  state.visibleMessages.set(previewMessageKey(message), message);
  pruneVisibleMessages();
}

function rememberVisibleLiveEvent(item = {}) {
  const content = String(item?.text || item?.message_text || "").trim();
  if (!content) return;
  state.visibleLiveEvents.set(liveEventKey(item), item);
  pruneVisibleLiveEvents();
}

function renderConversationTimeline() {
  const messageItems = Array.from(state.visibleMessages.values()).map((message) => ({
    type: "message",
    key: previewMessageKey(message),
    time: previewMessageTimeValue(message),
    value: message,
  }));
  const eventItems = shouldShowLiveEvents()
    ? Array.from(state.visibleLiveEvents.values()).map((event) => ({
      type: "event",
      key: liveEventKey(event),
      time: liveEventTimeValue(event),
      value: event,
    }))
    : [];
  const items = [...messageItems, ...eventItems].sort((left, right) => {
    const timeDelta = left.time - right.time;
    if (timeDelta !== 0) return timeDelta;
    return left.key.localeCompare(right.key);
  }).slice(-CHAT_PREVIEW_VISIBLE_LIMIT);
  clearConversationFeed();
  if (!items.length) {
    renderConversationEmpty(state.sessionId ? "Live Session 已建立，等待後端產生 AI 對話。" : undefined);
    return;
  }
  items.forEach((item) => {
    if (item.type === "event") appendLiveEventItem(item.value, { prepend: true });
    else appendChatPreviewMessage(item.value, { prepend: true });
  });
}

function appendChatPreviewMessage(message, { prepend = true } = {}) {
  const content = String(message?.content || message?.message_text || "").trim();
  if (!content) return;
  rememberVisibleMessage(message);
  const messageId = String(message?.message_id || message?.id || `${message?.created_at || ""}-${content.slice(0, 24)}`);
  if (messageId && feed.querySelector(`[data-message-id="${CSS.escape(messageId)}"]`)) return;
  const kind = chatPreviewKind(message);
  const row = document.createElement("article");
  row.className = `chat-line ${kind}`;
  row.dataset.messageId = messageId;

  const time = document.createElement("time");
  time.className = "chat-time";
  time.textContent = chatPreviewTime(message);

  const mark = document.createElement("div");
  mark.className = "chat-avatar";
  const name = String(message?.character_name || message?.author_display_name || "AI");
  mark.textContent = name.slice(0, 1) || "AI";

  const copy = document.createElement("div");
  copy.className = "chat-copy";
  const title = document.createElement("strong");
  title.textContent = name;
  const body = document.createElement("p");
  body.textContent = content;
  copy.append(title, body, time);
  applyChatRoleVisuals(row, mark, { ...message, character_name: name }, kind);

  row.append(mark, copy);
  if (prepend) feed.prepend(row);
  else feed.append(row);
  feed.scrollTop = 0;
}

function rememberChatPreviewMessages(messages = []) {
  const visible = Array.isArray(messages) ? messages.filter((message) => (
    String(message?.role || "") !== "system_event"
    && String(message?.content || message?.message_text || "").trim()
  )) : [];
  visible.forEach(rememberVisibleMessage);
  const merged = mergePreviewMessages(Array.from(state.visibleMessages.values()), visible).slice(-CHAT_PREVIEW_VISIBLE_LIMIT);
  state.visibleMessages.clear();
  merged.forEach((message) => state.visibleMessages.set(previewMessageKey(message), message));
  return merged;
}

function renderChatPreviewMessages(messages = []) {
  rememberChatPreviewMessages(messages);
  renderConversationTimeline();
}

function eventToLiveEventItem(event) {
  return {
    event_id: event?.id || event?.event_id || event?.youtube_message_id || "",
    youtube_message_id: event?.youtube_message_id || "",
    kind: event?.priority_class === "super_chat" || event?.message_type === "superChatEvent" ? "super" : "comment",
    amount: event?.amount_display_string || event?.amount_display || event?.amount || "",
    name: event?.author_display_name || "觀眾",
    text: event?.message_text || event?.text || "",
    published_at: event?.published_at || event?.received_at || event?.created_at || "",
  };
}

async function refreshConversation() {
  if (!state.sessionId) {
    renderConversationEmpty();
    return;
  }
  try {
    const [preview, recent] = await Promise.all([
      api(`/sessions/${encodeURIComponent(state.sessionId)}/chat-preview?limit=120`),
      api(`/sessions/${encodeURIComponent(state.sessionId)}/recent?limit=120`),
    ]);
    rememberChatPreviewMessages(preview.messages || []);
    const events = Array.isArray(recent.events) ? recent.events.map(eventToLiveEventItem).filter((item) => item.text) : [];
    if (events.length && shouldShowLiveEvents()) {
      events.forEach(rememberVisibleLiveEvent);
    }
    renderConversationTimeline();
  } catch (error) {
    appendLog("WARN", `直播對話更新失敗：${error.message || error}`);
  }
}

function scheduleConversationRefresh(source = "直播對話") {
  if (!state.sessionId) return;
  const alreadyQueued = Boolean(state.chatRefreshTimer);
  if (state.chatRefreshTimer) clearTimeout(state.chatRefreshTimer);
  state.chatRefreshTimer = setTimeout(() => {
    state.chatRefreshTimer = null;
    refreshConversation();
  }, 900);
  if (!alreadyQueued) appendLog("DEBUG", `${source}已排程重新整理`);
}

function subscribeSessionEvents(sessionId) {
  if (!sessionId) return;
  unsubscribeSessionEvents();
  startMainThreadProbe();
  state.eventSource = new EventSource(`/sessions/${encodeURIComponent(sessionId)}/events`);
  state.eventSource.onopen = () => appendLog("INFO", "Live Session 即時事件已連線");
  state.eventSource.onmessage = (event) => {
    try {
      const payload = JSON.parse(event.data);
      if (payload.type === "chat_message" && payload.message) {
        appendChatPreviewMessage(payload.message, { prepend: true });
        return;
      }
      if (payload.type === "youtube_live_event" && payload.event) {
        scheduleConversationRefresh("直播事件");
        return;
      }
      if (payload.type === "status") {
        refreshStudioSession();
        return;
      }
      if (payload.type === "presentation_debug" && payload.event) {
        appendPresentationDebugLog(payload.event);
        return;
      }
      if (payload.type === "presentation_item_preload" && payload.item) {
        cachePresentationAudio(attachPresentationSseTiming(payload.item, payload));
        return;
      }
      if (payload.type === "presentation_item_ready" && payload.item) {
        enqueuePresentationItem(attachPresentationSseTiming(payload.item, payload));
        return;
      }
      if (payload.type === "interrupt_requested") {
        handlePresentationInterrupt(payload).catch((error) => {
          appendLog("WARN", `直播打斷處理失敗：${error.message || error}`);
        });
        return;
      }
      if (payload.type === "interaction_interrupted") {
        appendLog("DEBUG", "互動已中斷，保留已顯示對話");
        return;
      }
      if (payload.type === "phase_finalize_completed") {
        appendLog("INFO", "節目收尾流程已完成");
        refreshStudioSession();
        refreshConversation();
        return;
      }
      if (payload.type === "phase_finalize_failed") {
        appendLog("WARN", `節目收尾失敗：${payload.error || "unknown error"}`);
        refreshStudioSession();
        return;
      }
      if (["interaction_completed", "super_chat_batch_injected"].includes(payload.type)) {
        refreshConversation();
      }
    } catch (error) {
      appendLog("WARN", `Live Session 事件解析失敗：${error.message || error}`);
    }
  };
  state.eventSource.onerror = () => {
    appendLog("WARN", "Live Session 即時事件中斷，等待瀏覽器自動重連");
  };
}

function sessionIsRunning(session) {
  if (!session) return false;
  if (sessionIsClosing(session)) return false;
  if (sessionFinalizeFailed(session)) return false;
  const runtime = session.runtime_status || {};
  return Boolean(runtime.running || runtime.status === "running" || runtime.status === "starting" || session.status === "running" || session.status === "starting");
}

function sessionIsClosing(session) {
  if (!session) return false;
  const runtime = session.runtime_status || {};
  return Boolean(runtime.status === "closing" || session.status === "closing");
}

function sessionFinalizeFailed(session) {
  if (!session) return false;
  const runtime = session.runtime_status || {};
  return Boolean(runtime.status === "closing_failed" || session.status === "closing_failed");
}

function sessionShouldReceiveEvents(session) {
  return sessionIsRunning(session) || sessionIsClosing(session);
}

function phaseSummaryStatus(summary = {}) {
  if (!summary || typeof summary !== "object") return "未開始";
  return summary.memory_write_status || summary.status || "未開始";
}

function countValue(value) {
  const numberValue = Number(value);
  return Number.isFinite(numberValue) ? Math.max(0, Math.floor(numberValue)) : 0;
}

function freeTalkClosingText(metadata = {}) {
  const closing = metadata?.free_talk_audience_closing;
  if (!closing || typeof closing !== "object") return "";
  const countKeys = [
    "handled_count",
    "eligible_processed_count",
    "processed_count",
    "skipped_count",
    "closing_skipped_count",
    "low_signal_skipped_count",
  ];
  if (!countKeys.some((key) => Object.prototype.hasOwnProperty.call(closing, key))) {
    return "";
  }
  const handled = countValue(closing.handled_count ?? closing.eligible_processed_count ?? closing.processed_count);
  const skipped = countValue(closing.skipped_count ?? closing.closing_skipped_count);
  const lowSignal = countValue(closing.low_signal_skipped_count);
  return `雜談收尾：處理 ${handled} / 跳過 ${skipped} / 低訊號 ${lowSignal}`;
}

function phaseSummaryText(session) {
  const base = state.live ? "直播中" : (session ? "已停止" : "未開始");
  const metadata = session?.director_state?.metadata || session?.metadata || session?.runtime_status?.metadata || {};
  const mainSummary = phaseSummaryStatus(metadata.main_summary);
  const freeTalkSummary = phaseSummaryStatus(metadata.free_talk_summary);
  const summaryText = `${base} · 正式摘要：${mainSummary} / 雜談摘要：${freeTalkSummary}`;
  const closingText = freeTalkClosingText(metadata);
  return closingText ? `${summaryText} · ${closingText}` : summaryText;
}

function applySessionSnapshot(session) {
  const wasLive = state.live;
  const presentationPlayerActive = Boolean(
    state.currentPresentationItem
    || state.currentAudio
    || state.presentationPlaying
    || state.audioUnlockRequired
    || state.presentationQueue.length
  );
  state.currentSession = session || null;
  state.sessionId = session?.session_id || "";
  state.detectedVideoId = session?.video_id || "";
  state.detectedLiveChatId = session?.live_chat_id || "";
  state.live = sessionIsRunning(session);
  const closing = sessionIsClosing(session);
  const closingFailed = sessionFinalizeFailed(session);
  sessionDot.classList.toggle("is-live", state.live);
  liveBadge.textContent = closing ? "收尾中" : (closingFailed ? "收尾失敗" : (state.live ? "直播中" : "待機"));
  liveBadge.className = (closing || closingFailed) ? "state-badge warn" : (state.live ? "state-badge good" : "state-badge neutral");
  leftStatusBadge.textContent = closing ? "收尾中" : (closingFailed ? "收尾失敗" : (state.live ? "直播中" : (session ? "已停止" : "未開始")));
  leftStatusBadge.className = (closing || closingFailed) ? "state-badge warn" : (state.live ? "state-badge good" : (session ? "state-badge neutral" : "state-badge neutral"));
  sessionStateText.textContent = phaseSummaryText(session);

  if (session?.started_at) {
    const startedAt = new Date(session.started_at);
    state.startedAt = Number.isNaN(startedAt.getTime()) ? null : startedAt;
  } else {
    state.startedAt = null;
  }
  startedAtText.textContent = state.startedAt
    ? state.startedAt.toLocaleTimeString("zh-TW", { hour: "2-digit", minute: "2-digit", hour12: false })
    : "--:--";
  if (state.elapsedTimer) clearInterval(state.elapsedTimer);
  state.elapsedTimer = null;
  if (state.live) {
    state.elapsedTimer = setInterval(updateDuration, 1000);
  }
  state.sourceStatus = closing
    ? "closing"
    : (closingFailed ? "closing_failed" : (state.live ? (state.detectedVideoId || state.detectedLiveChatId ? "detected" : "test") : "idle"));
  if (state.live) {
    renderSummaryPreview(null, "直播中不產生摘要；停止直播後可重新生成。");
  } else if (!state.sessionId) {
    renderSummaryPreview(null);
  }
  updateDuration();
  updateSourceDetectionState();
  updateSummaryControls();
  applyStartButtonState();
  const reachedTerminalSessionState = !state.live && !closing && !closingFailed;
  if (reachedTerminalSessionState && (wasLive || presentationPlayerActive)) {
    resetPresentationPlayer({ statusText: "已停止" });
  }
}

function studioLiveSessionPayload() {
  const liveDefaults = collectLiveDefaults();
  const payload = {
    session_id: "",
    display_name: "YouTube Live",
    video_id: parseManualVideoId(),
    target_memoria_session_id: "",
    episode_plan_id: planSelect.value,
    character_ids: [],
    auto_connect: true,
    auto_inject: liveDefaults.auto_inject_pending_enabled,
    inject_interval_seconds: liveDefaults.inject_interval_seconds,
    inject_min_interval_seconds: liveDefaults.inject_min_interval_seconds,
    min_pending_events: liveDefaults.min_pending_comments,
    max_pending_events: liveDefaults.pending_force_limit,
    dynamic_inject_enabled: true,
    planned_duration_minutes: liveDefaults.planned_duration_minutes,
    auto_finalize_on_duration: liveDefaults.auto_finalize_at_limit,
    auto_sc_thanks_on_finalize: liveDefaults.thank_unhandled_super_chats,
    auto_delete_after_processed: liveDefaults.clear_runtime_session_after_summary,
    sc_interrupt_cooldown_seconds: liveDefaults.super_chat_cooldown_seconds,
    max_sc_per_batch: liveDefaults.super_chat_batch_limit,
    free_talk_closing_target_batches: liveDefaults.free_talk_closing_target_batches,
    free_talk_closing_min_batch_size: liveDefaults.free_talk_closing_min_batch_size,
    free_talk_closing_max_batch_size: liveDefaults.free_talk_closing_max_batch_size,
    free_talk_closing_time_limit_seconds: liveDefaults.free_talk_closing_time_limit_seconds,
    research_enabled: liveDefaults.safe_search_enabled,
    presentation_enabled: liveDefaults.presentation_queue_enabled,
    tts_enabled: liveDefaults.tts_enabled,
    tts_provider: "gpt_sovits",
  };
  if (state.freeTalkTopicSelectionInitialized) {
    payload.post_plan_free_talk_topic_pack_ids = selectedFreeTalkTopicPackIds();
  }
  return payload;
}

async function startStudioDirector(sessionId) {
  try {
    await api(`/sessions/${encodeURIComponent(sessionId)}/director/start`, {
      method: "POST",
      body: { idle_seconds: 60, guidance: "", kickoff: true },
    });
    appendLog("INFO", "導播 kickoff 已啟動");
  } catch (error) {
    appendLog("WARN", `直播已開始，導播啟動失敗：${error.message || error}`);
    startBlockReason.textContent = "直播已開始，導播啟動失敗；可先停止直播後重試。";
  }
}

async function loadDirectorState(sessionId) {
  if (!sessionId) return null;
  try {
    return await api(`/sessions/${encodeURIComponent(sessionId)}/director`);
  } catch {
    return null;
  }
}

async function refreshStudioSession() {
  try {
    const sessions = await api("/sessions");
    const list = Array.isArray(sessions) ? sessions : [];
    const selected = list.find((session) => session.session_id === state.sessionId)
      || list.find((session) => sessionIsRunning(session))
      || list[0]
      || null;
    if ((selected?.session_id || "") !== state.sessionId) {
      state.visibleMessages.clear();
    }
    const directorState = selected?.session_id ? await loadDirectorState(selected.session_id) : null;
    const selectedWithDirector = selected && directorState ? { ...selected, director_state: directorState } : selected;
    applySessionSnapshot(selectedWithDirector);
    if (selected?.episode_plan_id && state.episodePlans.some((plan) => plan.plan_id === selected.episode_plan_id)) {
      planSelect.value = selected.episode_plan_id;
      subtitle.textContent = planSelect.options[planSelect.selectedIndex]?.textContent || selected.episode_plan_id;
      updatePlanState();
    }
    if (selected?.session_id) {
      await refreshConversation();
      if (sessionShouldReceiveEvents(selected)) {
        renderSummaryPreview(
          null,
          sessionIsClosing(selected)
            ? "直播收尾中，等待最後訊息完成。"
            : "直播中不產生摘要；停止直播後可重新生成。",
        );
      } else {
        await loadSessionSummary({ quiet: true });
      }
      if (sessionShouldReceiveEvents(selected)) {
        subscribeSessionEvents(selected.session_id);
      } else {
        unsubscribeSessionEvents();
      }
    } else {
      renderSummaryPreview(null);
    }
    return selected;
  } catch (error) {
    appendLog("WARN", `直播狀態刷新失敗：${error.message || error}`);
    return null;
  }
}

async function startLive() {
  if (state.live || state.sourceStatus === "starting") return;
  const readiness = startReadiness();
  if (!readiness.canStart) {
    updatePreflightChecklist();
    appendLog("WARN", "開播前檢查未通過，未建立 Live Session");
    return;
  }
  state.sourceStatus = "starting";
  resetConversationForNewSession();
  updateSourceDetectionState();
  applyStartButtonState();
  startButton.disabled = true;
  try {
    const data = await api("/sessions/current/start", {
      method: "POST",
      body: studioLiveSessionPayload(),
    });
    applySessionSnapshot(data);
    subtitle.textContent = planSelect.options[planSelect.selectedIndex]?.textContent || data.episode_plan_id || "Live Session";
    subscribeSessionEvents(data.session_id);
    await startStudioDirector(data.session_id);
    await refreshConversation();
    appendLog("INFO", `直播已開始：${data.session_id}`);
  } catch (error) {
    state.sourceStatus = "idle";
    updateSourceDetectionState();
    appendLog("WARN", `直播啟動失敗：${error.message || error}`);
  } finally {
    applyStartButtonState();
  }
}

async function stopLive() {
  const sessionId = state.sessionId;
  if (!sessionId || state.sourceStatus === "starting" || state.sourceStatus === "closing") return;
  stopButton.disabled = true;
  try {
    const data = await api(`/sessions/${encodeURIComponent(sessionId)}/phase/finalize`, {
      method: "POST",
      body: { reason: "operator_finalize", background: true },
    });
    appendLog("INFO", "節目收尾流程已送出");
    applySessionSnapshot({ ...(state.currentSession || {}), runtime_status: data.runtime_status || data, status: "closing" });
  } catch (error) {
    appendLog("WARN", `節目收尾失敗：${error.message || error}`);
  } finally {
    applyStartButtonState();
  }
}

function switchDebugTab(nextTab) {
  document.querySelectorAll(".segment").forEach((button) => {
    const active = button.dataset.tab === nextTab;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
  });
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    const active = panel.id === `${nextTab}Panel`;
    panel.classList.toggle("active", active);
    panel.hidden = !active;
  });
}

function openRoleSettings() {
  switchDebugTab("roles");
  appendLog("INFO", "已開啟直播角色設定頁");
}

function updateTestCount() {
  testCount.textContent = `${testMessage.value.length} / 200`;
}

function eventToTestManualEvent(item) {
  return {
    kind: item.kind === "super" ? "super" : "comment",
    author_display_name: item.name || (item.kind === "super" ? "SC 測試帳號" : "觀眾 測試帳號"),
    message_text: item.text || "",
    amount_display_string: item.kind === "super" ? (item.amount || "NT$75") : "",
  };
}

function testCommentTopicHint() {
  const plan = selectedEpisodePlan();
  const parts = [
    subtitle.textContent,
    plan?.title,
    plan?.plan_id,
  ];
  const recentLines = Array.from(feed.querySelectorAll(".chat-copy p"))
    .slice(0, 6)
    .map((node) => node.textContent.trim())
    .filter(Boolean);
  if (recentLines.length) {
    parts.push(`近期直播對話：${recentLines.join(" / ")}`);
  }
  return parts.filter(Boolean).join("\n").slice(0, 1200);
}

async function submitBackendTestEvents({
  events = [],
  count = 0,
  superChatCount = 0,
  useLlm = false,
  includeMalicious = false,
  scBurst = false,
  source = "測試留言",
} = {}) {
  if (!(state.sessionId && state.live)) return null;
  const manualEvents = events.map(eventToTestManualEvent).filter((event) => event.message_text);
  const total = manualEvents.length + count + superChatCount;
  if (!total) return null;
  const result = await api(`/sessions/${encodeURIComponent(state.sessionId)}/test-events/generate`, {
    method: "POST",
    body: {
      count: Math.max(0, Math.floor(count || 0)),
      super_chat_count: Math.max(0, Math.floor(superChatCount || 0)),
      use_llm: useLlm,
      topic_hint: testCommentTopicHint(),
      include_malicious_sc: Boolean(includeMalicious),
      sc_burst: Boolean(scBurst),
      manual_events: manualEvents,
    },
  });
  appendLog("INFO", `${source}已送入後端 pending queue：${result.generated || total} 則`);
  return result;
}

async function sendTestMessage() {
  const content = testMessage.value.trim();
  if (!content) {
    testResult.textContent = "請先輸入測試留言。";
    appendLog("WARN", "測試留言送出失敗：內容為空");
    return;
  }
  if (state.sessionId && state.live) {
    try {
      await submitBackendTestEvents({ events: [{
        kind: "comment",
        name: "觀眾 測試帳號",
        text: content,
      }], source: "測試留言" });
      testResult.textContent = `已送入後端測試留言：「${content}」；等待安全分類與自動注入。`;
      testMessage.value = "";
      updateTestCount();
    } catch (error) {
      testResult.textContent = `測試留言送出失敗：${error.message || error}`;
      appendLog("WARN", `測試留言送出失敗：${error.message || error}`);
    }
    return;
  }
  testResult.textContent = `已建立測試留言：「${content}」；目前只顯示本頁模擬結果。`;
  appendLog("DEBUG", "送出一則測試留言");
  appendLiveEventGroup("YouTube Live 留言注入：1 則", [{
    kind: "comment",
    name: "觀眾 測試帳號",
    text: content,
  }]);
  testMessage.value = "";
  updateTestCount();
}

async function startFreeTalkTest() {
  if (!(state.sessionId && state.live)) {
    freeTalkTestState.textContent = "請先開始直播。";
    appendLog("WARN", "雜談測試啟動失敗：尚未開始直播");
    return;
  }
  startFreeTalkTestButton.disabled = true;
  freeTalkTestState.textContent = "正在啟動雜談測試。";
  try {
    const result = await api(`/sessions/${encodeURIComponent(state.sessionId)}/phase/free-talk-test/start`, {
      method: "POST",
      body: {},
    });
    const phase = result?.phase || "";
    if (result?.status === "wait") {
      freeTalkTestState.textContent = "已切換到雜談測試；目前互動執行中，會在結束後繼續。";
      appendLog("INFO", "已切換到雜談測試，等待目前互動結束");
      await refreshStudioSession();
      await refreshConversation();
      return;
    }
    freeTalkTestState.textContent = phase === "post_plan_free_talk"
      ? "已進入雜談測試。"
      : `雜談測試已啟動：${phase || "等待後端回傳 phase"}`;
    appendLog("INFO", "已啟動雜談測試");
    await refreshConversation();
  } catch (error) {
    freeTalkTestState.textContent = `雜談測試啟動失敗：${error.message || error}`;
    appendLog("WARN", `雜談測試啟動失敗：${error.message || error}`);
  } finally {
    startFreeTalkTestButton.disabled = false;
  }
}

async function skipMainToFreeTalk() {
  if (!(state.sessionId && state.live)) {
    skipMainToFreeTalkState.textContent = "請先開始直播。";
    appendLog("WARN", "正式節目結束測試失敗：尚未開始直播");
    return;
  }
  skipMainToFreeTalkButton.disabled = true;
  skipMainToFreeTalkState.textContent = "正在結束正式節目並進入雜談。";
  try {
    const result = await api(`/sessions/${encodeURIComponent(state.sessionId)}/phase/finish-main`, {
      method: "POST",
      body: {
        reason: "operator_debug_skip_to_free_talk",
        enter_free_talk: true,
        force_enter_free_talk: true,
      },
    });
    skipMainToFreeTalkState.textContent = "已結束正式節目階段並進入雜談流程。";
    appendLog("INFO", `正式節目結束測試完成：${result.phase || "post_plan_free_talk"}`);
    await refreshStudioSession();
    await refreshConversation();
  } catch (error) {
    skipMainToFreeTalkState.textContent = `正式節目結束測試失敗：${error.message || error}`;
    appendLog("WARN", `正式節目結束測試失敗：${error.message || error}`);
  } finally {
    skipMainToFreeTalkButton.disabled = false;
  }
}

function readPositiveNumber(input, fallback) {
  const value = Number(input.value);
  if (!Number.isFinite(value) || value < 0) return fallback;
  return Math.floor(value);
}

function collectTestSettings() {
  return {
    auto_comment_enabled: autoCommentEnabled.checked,
    normal_comment_count: readPositiveNumber(normalCommentCount, 8),
    super_chat_count: readPositiveNumber(superChatCount, 2),
    malicious_comment_enabled: maliciousCommentEnabled.checked,
    comment_frequency_seconds: Math.max(1, readPositiveNumber(commentFrequencySeconds, 8)),
    test_message: testMessage.value,
  };
}

function collectDisplaySettings() {
  return {
    show_live_events_enabled: $("showLiveEventsEnabled").checked,
  };
}

function collectLiveDefaults() {
  const payload = {
    auto_inject_pending_enabled: $("autoInjectPendingEnabled").checked,
    inject_interval_seconds: readPositiveNumber($("injectIntervalSeconds"), 30),
    inject_min_interval_seconds: readPositiveNumber($("injectMinIntervalSeconds"), 10),
    min_pending_comments: readPositiveNumber($("minPendingComments"), 1),
    pending_force_limit: readPositiveNumber($("pendingForceLimit"), 12),
    planned_duration_minutes: readPositiveNumber($("plannedDurationMinutes"), 52),
    auto_finalize_at_limit: $("autoFinalizeAtLimit").checked,
    thank_unhandled_super_chats: $("thankUnhandledSuperChats").checked,
    clear_runtime_session_after_summary: $("clearRuntimeSessionAfterSummary").checked,
    post_plan_free_talk_enabled: $("postPlanFreeTalkEnabled").checked,
    post_plan_free_talk_minutes: readPositiveNumber($("postPlanFreeTalkMinutes"), 20),
    free_talk_closing_target_batches: readPositiveNumber($("freeTalkClosingTargetBatches"), 10),
    free_talk_closing_min_batch_size: readPositiveNumber($("freeTalkClosingMinBatchSize"), 5),
    free_talk_closing_max_batch_size: readPositiveNumber($("freeTalkClosingMaxBatchSize"), 30),
    free_talk_closing_time_limit_seconds: readPositiveNumber($("freeTalkClosingTimeLimitSeconds"), 300),
    super_chat_cooldown_seconds: readPositiveNumber($("superChatCooldownSeconds"), 45),
    super_chat_batch_limit: readPositiveNumber($("superChatBatchLimit"), 3),
    safe_search_enabled: $("safeSearchEnabled").checked,
    presentation_queue_enabled: $("presentationQueueEnabled").checked,
    tts_enabled: $("ttsEnabled").checked,
  };
  if (state.savedFreeTalkTopicPackIds !== null) {
    payload.post_plan_free_talk_topic_pack_ids = selectedFreeTalkTopicPackIds();
  }
  return payload;
}

function collectConnectorSettings() {
  return {
    api_key: connectorApiKeyInput.value.trim(),
  };
}

function collectMemoriaAuthSettings() {
  return {
    base_url: memoriaBaseUrl.value.trim() || "http://127.0.0.1:8088/api/v1",
    username: memoriaUsername.value.trim(),
    password: memoriaPassword.value,
    admin_bypass: $("memoriaAdminBypass").checked,
  };
}

function applyConnectorSettings(connector = {}) {
  connectorApiKeyInput.value = "";
  connectorApiKeyInput.placeholder = connector.api_key_configured ? "已設定時會以遮罩顯示" : "尚未設定 API Key";
  connectorStatusBadge.textContent = connector.api_key_configured ? "已設定" : "未設定 API Key";
  connectorStatusBadge.className = connector.api_key_configured ? "state-badge good" : "state-badge warn";
}

function applyMemoriaAuthSettings(config = {}) {
  memoriaBaseUrl.value = config.base_url || "http://127.0.0.1:8088/api/v1";
  memoriaUsername.value = config.username || "";
  memoriaPassword.value = "";
  memoriaPassword.placeholder = config.password_configured ? "已設定時不顯示" : "未設定";
  $("memoriaAdminBypass").checked = config.admin_bypass !== false;
  memoriaAuthState.textContent = $("memoriaAdminBypass").checked ? "Admin bypass" : "帳密模式";
  memoriaAuthState.className = "state-badge good";
}

function applyTestSettings(settings = {}) {
  autoCommentEnabled.checked = Boolean(settings.auto_comment_enabled);
  normalCommentCount.value = settings.normal_comment_count ?? 8;
  superChatCount.value = settings.super_chat_count ?? 2;
  maliciousCommentEnabled.checked = Boolean(settings.malicious_comment_enabled);
  commentFrequencySeconds.value = settings.comment_frequency_seconds ?? 8;
  testMessage.value = settings.test_message || "";
  updateTestCount();
  applyAutoCommentState();
}

function applyDisplaySettings(settings = {}) {
  $("showLiveEventsEnabled").checked = Boolean(settings.show_live_events_enabled);
}

function applyLiveDefaults(settings = {}) {
  setInputChecked("autoInjectPendingEnabled", settings.auto_inject_pending_enabled !== false);
  setInputValue("injectIntervalSeconds", settings.inject_interval_seconds ?? 30);
  setInputValue("injectMinIntervalSeconds", settings.inject_min_interval_seconds ?? 10);
  setInputValue("minPendingComments", settings.min_pending_comments ?? 1);
  setInputValue("pendingForceLimit", settings.pending_force_limit ?? 12);
  setInputValue("plannedDurationMinutes", settings.planned_duration_minutes ?? 52);
  setInputChecked("autoFinalizeAtLimit", settings.auto_finalize_at_limit !== false);
  setInputChecked("thankUnhandledSuperChats", settings.thank_unhandled_super_chats !== false);
  setInputChecked("clearRuntimeSessionAfterSummary", settings.clear_runtime_session_after_summary !== false);
  setInputChecked("postPlanFreeTalkEnabled", settings.post_plan_free_talk_enabled);
  setInputValue("postPlanFreeTalkMinutes", settings.post_plan_free_talk_minutes ?? 20);
  setInputValue("freeTalkClosingTargetBatches", settings.free_talk_closing_target_batches ?? 10);
  setInputValue("freeTalkClosingMinBatchSize", settings.free_talk_closing_min_batch_size ?? 5);
  setInputValue("freeTalkClosingMaxBatchSize", settings.free_talk_closing_max_batch_size ?? 30);
  setInputValue("freeTalkClosingTimeLimitSeconds", settings.free_talk_closing_time_limit_seconds ?? 300);
  if (
    settings.post_plan_free_talk_topic_pack_ids_configured === true
    && Array.isArray(settings.post_plan_free_talk_topic_pack_ids)
  ) {
    state.savedFreeTalkTopicPackIds = [...settings.post_plan_free_talk_topic_pack_ids];
    setSelectedFreeTalkTopicPackIds(state.savedFreeTalkTopicPackIds);
    state.freeTalkTopicSelectionInitialized = true;
  } else {
    state.savedFreeTalkTopicPackIds = null;
    state.freeTalkTopicSelectionInitialized = false;
  }
  if (state.freeTalkTopicPacks.length) {
    renderFreeTalkTopicChecklist({
      packs: state.freeTalkTopicPacks,
      sidecar: state.freeTalkSidecar,
      total_topic_count: state.freeTalkTopicPacks.reduce((count, pack) => count + (pack.topic_count || 0), state.freeTalkSidecar?.topic_count || 0),
    });
  }
  setInputValue("superChatCooldownSeconds", settings.super_chat_cooldown_seconds ?? 45);
  setInputValue("superChatBatchLimit", settings.super_chat_batch_limit ?? 3);
  setInputChecked("safeSearchEnabled", settings.safe_search_enabled !== false);
  setInputChecked("presentationQueueEnabled", settings.presentation_queue_enabled !== false);
  setInputChecked("ttsEnabled", settings.tts_enabled);
  updateLiveSettingsSummary();
}

function applyTtsSources(ttsSources = {}) {
  const root = ttsSources.root || "runtime/YouTubeBridge/TTSSource";
  $("liveTtsSourceRoot").textContent = root;
  const select = $("liveTtsSourcePreset");
  const sources = Array.isArray(ttsSources.sources) ? ttsSources.sources : [];
  if (!sources.length) return;
  select.innerHTML = '<option value="">手動輸入範例語音</option>';
  sources.forEach((source) => {
    if (!source.audio_path) return;
    ttsSourcePresets[source.audio_path] = {
      promptText: source.prompt_text || "",
      promptLang: "zh",
    };
    const option = document.createElement("option");
    option.value = source.audio_path;
    option.textContent = source.name || source.audio_path;
    select.append(option);
  });
}

function applyPersonaSettings(overlays = [], ttsProfiles = []) {
  state.personaOverlays = Array.isArray(overlays) ? overlays : [];
  state.ttsProfiles = Array.isArray(ttsProfiles) ? ttsProfiles : [];
  overlays.forEach((overlay) => {
    const roleId = overlay.character_id;
    if (!roleId) return;
    ensureRolePersonaDraft({ character_id: roleId });
      rolePersonaDrafts[roleId] = {
      ...rolePersonaDrafts[roleId],
      selfAddress: overlay.self_address || "",
      avatarUrl: overlay.avatar_url || rolePersonaDrafts[roleId].avatarUrl || "",
      chatBackgroundColor: overlay.chat_background_color || rolePersonaDrafts[roleId].chatBackgroundColor || rolePalette(roleId).bg,
      chatAccentColor: overlay.chat_accent_color || rolePersonaDrafts[roleId].chatAccentColor || rolePalette(roleId).accent,
      systemPrompt: overlay.system_prompt || "",
      openingIntro: overlay.opening_intro || "",
      addressing: overlay.addressing || {},
      replyRules: overlay.reply_rules || "",
    };
  });
  ttsProfiles.forEach((profile) => {
    const roleId = profile.character_id;
    if (!roleId) return;
    ensureRolePersonaDraft({ character_id: roleId });
    rolePersonaDrafts[roleId].tts = {
      enabled: Boolean(profile.enabled),
      sourcePreset: profile.ref_audio_path || "",
      refAudioPath: profile.ref_audio_path || "",
      promptText: profile.prompt_text || "",
      textLang: profile.text_lang || "zh",
      promptLang: profile.prompt_lang || "zh",
      speedFactor: String(profile.speed_factor ?? "1"),
      mediaType: profile.media_type || "wav",
    };
  });
  fillLivePersonaFormForSelectedRole();
}

async function loadStudioSettings() {
  state.loadingSettings = true;
  try {
    const data = await api("/studio/settings");
    applyConnectorSettings(data.connector || {});
    applyMemoriaAuthSettings(data.memoria_auth || {});
    applyTestSettings(data.test_settings || {});
    applyDisplaySettings(data.display_settings || {});
    applyLiveDefaults(data.live_defaults || {});
    applyTtsSources(data.tts_sources || {});
    applyPersonaSettings(data.persona_overlays || [], data.tts_profiles || []);
    markAutoSaveState(testAutoSaveState, "測試設定");
    markAutoSaveState(systemAutoSaveState, "系統設定");
    markAutoSaveState(livePersonaSaveState, "角色設定");
    appendLog("INFO", "Studio 設定已載入");
  } catch (error) {
    appendLog("WARN", `Studio 設定載入失敗：${error.message || error}`);
  } finally {
    state.loadingSettings = false;
  }
}

function scheduleTestSettingsSave(source = "測試設定") {
  if (state.loadingSettings) return;
  markSavingState(testAutoSaveState, source);
  debounceAutoSave("test-settings", async () => {
    try {
      await api("/studio/settings", {
        method: "PATCH",
        body: {
          test_settings: collectTestSettings(),
          display_settings: collectDisplaySettings(),
        },
      });
      markAutoSaveState(testAutoSaveState, source);
    } catch (error) {
      markSaveError(testAutoSaveState, source, error);
      appendLog("WARN", `測試設定儲存失敗：${error.message || error}`);
    }
  });
}

function scheduleSystemSettingsSave(source = "系統設定") {
  if (state.loadingSettings) return;
  markSavingState(systemAutoSaveState, source);
  debounceAutoSave("system-settings", async () => {
    try {
      const data = await api("/studio/settings", {
        method: "PATCH",
        body: {
          connector: collectConnectorSettings(),
          memoria_auth: collectMemoriaAuthSettings(),
          live_defaults: collectLiveDefaults(),
        },
      });
      applyConnectorSettings(data.connector || {});
      applyMemoriaAuthSettings(data.memoria_auth || {});
      markAutoSaveState(systemAutoSaveState, source);
    } catch (error) {
      markSaveError(systemAutoSaveState, source, error);
      appendLog("WARN", `系統設定儲存失敗：${error.message || error}`);
    }
  });
}

function applyAutoCommentState() {
  const enabled = autoCommentEnabled.checked;
  const normalCount = readPositiveNumber(normalCommentCount, 0);
  const scCount = readPositiveNumber(superChatCount, 0);
  const frequency = Math.max(1, readPositiveNumber(commentFrequencySeconds, 1));
  const malicious = maliciousCommentEnabled.checked;
  if (state.autoCommentTimer) {
    autoCommentStatus.textContent = `自動留言執行中：已送 ${state.autoCommentSent} / ${state.autoCommentTotal}，每 ${frequency} 秒一則`;
    return;
  }
  autoCommentStatus.textContent = enabled
    ? `自動留言啟用：每 ${frequency} 秒，預計 ${normalCount} 則一般留言、${scCount} 則 SC${malicious ? "，包含惡意留言樣本" : ""}`
    : "自動留言未啟用";
}

function buildAutoCommentQueue() {
  const normalCount = readPositiveNumber(normalCommentCount, 0);
  const scCount = readPositiveNumber(superChatCount, 0);
  const malicious = maliciousCommentEnabled.checked;
  const queue = [];
  for (let index = 0; index < normalCount; index += 1) {
    queue.push({
      kind: "comment",
      name: `觀眾 測試${index + 1}`,
      text: normalCommentSamples[index % normalCommentSamples.length],
    });
  }
  for (let index = 0; index < scCount; index += 1) {
    queue.push({
      kind: "super",
      name: `SC 測試${index + 1}`,
      amount: index === 0 ? "NT$750" : "NT$75",
      text: superChatSamples[index % superChatSamples.length],
    });
  }
  if (malicious) {
    queue.push({
      kind: "comment",
      name: "風險樣本",
      text: "這則留言帶有攻擊性語氣，需要測試安全分類與處理流程。",
    });
  }
  return queue;
}

function buildAutoCommentGenerationSlots() {
  const normalCount = readPositiveNumber(normalCommentCount, 0);
  const scCount = readPositiveNumber(superChatCount, 0);
  const queue = [];
  for (let index = 0; index < normalCount; index += 1) {
    queue.push({ kind: "comment" });
  }
  for (let index = 0; index < scCount; index += 1) {
    queue.push({ kind: "super" });
  }
  if (maliciousCommentEnabled.checked) {
    queue.push({
      kind: "comment",
      manualEvent: {
        kind: "comment",
        name: "風險樣本",
        text: "這則留言帶有攻擊性語氣，需要測試安全分類與處理流程。",
      },
    });
  }
  return queue;
}

async function generateAutoComments() {
  const frequency = Math.max(1, readPositiveNumber(commentFrequencySeconds, 1));
  const normalCount = readPositiveNumber(normalCommentCount, 0);
  const scCount = readPositiveNumber(superChatCount, 0);
  const malicious = maliciousCommentEnabled.checked;
  if (state.sessionId && state.live) {
    try {
      const manualEvents = malicious ? [{
        kind: "comment",
        name: "風險樣本",
        text: "這則留言帶有攻擊性語氣，需要測試安全分類與處理流程。",
      }] : [];
      const result = await submitBackendTestEvents({
        events: manualEvents,
        count: normalCount,
        superChatCount: scCount,
        useLlm: true,
        includeMalicious: malicious,
        scBurst: scCount >= 3,
        source: "自動留言批次",
      });
      const generated = result?.generated ?? (normalCount + scCount + manualEvents.length);
      testResult.textContent = `已送入後端 ${generated} 則測試留言；等待安全分類與自動注入。`;
      applyAutoCommentState();
    } catch (error) {
      testResult.textContent = `自動留言批次送出失敗：${error.message || error}`;
      appendLog("WARN", `自動留言批次送出失敗：${error.message || error}`);
    }
    return;
  }
  const queue = buildAutoCommentQueue();
  appendLiveEventGroup(`YouTube Live 留言注入：${queue.length} 則`, queue);
  appendLog("DEBUG", `自動留言批次：一般 ${normalCount}、SC ${scCount}、頻率 ${frequency} 秒`);
  testResult.textContent = `已模擬生成 ${queue.length} 則留言；目前只顯示本頁模擬結果。`;
  if (malicious && queue.length > 0) {
    appendLog("WARN", "自動留言批次包含惡意留言樣本");
  }
  applyAutoCommentState();
}

function stopAutoComments() {
  if (state.autoCommentTimer) {
    clearInterval(state.autoCommentTimer);
    state.autoCommentTimer = null;
  }
  state.autoCommentInFlight = false;
  state.autoCommentQueue = [];
  state.autoCommentTotal = 0;
  state.autoCommentSent = 0;
  applyAutoCommentState();
}

function startAutoComments() {
  stopAutoComments();
  state.autoCommentQueue = state.sessionId && state.live
    ? buildAutoCommentGenerationSlots()
    : buildAutoCommentQueue();
  state.autoCommentTotal = state.autoCommentQueue.length;
  state.autoCommentSent = 0;
  if (!autoCommentEnabled.checked || state.autoCommentQueue.length === 0) {
    autoCommentStatus.textContent = autoCommentEnabled.checked ? "自動留言啟用，但沒有可生成的留言。" : "自動留言未啟用";
    return;
  }
  const frequency = Math.max(1, readPositiveNumber(commentFrequencySeconds, 1));
  const emitNext = async () => {
    if (state.autoCommentInFlight) return;
    const item = state.autoCommentQueue.shift();
    if (!item) {
      appendLog("INFO", "自動留言佇列已完成");
      stopAutoComments();
      return;
    }
    if (state.sessionId && state.live) {
      state.autoCommentInFlight = true;
      try {
        await submitBackendTestEvents({
          events: item.manualEvent ? [item.manualEvent] : [],
          count: item.kind === "comment" ? 1 : 0,
          superChatCount: item.kind === "super" ? 1 : 0,
          useLlm: !item.manualEvent,
          includeMalicious: Boolean(item.manualEvent) || maliciousCommentEnabled.checked,
          scBurst: false,
          source: "自動留言",
        });
        state.autoCommentSent += 1;
        testResult.textContent = `自動留言執行中：已送入後端 ${state.autoCommentSent} / ${state.autoCommentTotal} 則。`;
      } catch (error) {
        testResult.textContent = `自動留言送出失敗：${error.message || error}`;
        appendLog("WARN", `自動留言送出失敗：${error.message || error}`);
      } finally {
        state.autoCommentInFlight = false;
        applyAutoCommentState();
      }
      return;
    }
    appendLiveEventGroup("YouTube Live 留言注入：1 則", [item]);
    state.autoCommentSent += 1;
    testResult.textContent = `自動留言執行中：已送出 ${state.autoCommentSent} / ${state.autoCommentTotal} 則。`;
    applyAutoCommentState();
  };
  appendLog("INFO", `自動留言已啟動，每 ${frequency} 秒送出一則`);
  emitNext();
  state.autoCommentTimer = setInterval(emitNext, frequency * 1000);
  applyAutoCommentState();
}

function clearConversation() {
  state.visibleMessages.clear();
  state.visibleLiveEvents.clear();
  renderConversationEmpty("對話區已清空，等待新的直播內容。");
  appendLog("INFO", "直播對話顯示已清除");
}

async function regenerateSummary() {
  if (!state.sessionId) {
    renderSummaryPreview(null, "尚未建立 Live Session，無法生成摘要。");
    appendLog("WARN", "Summary 生成失敗：尚未建立 Live Session");
    updateSummaryControls();
    return;
  }
  if (state.live) {
    renderSummaryPreview(null, "請先停止直播再生成摘要。");
    appendLog("WARN", "Summary 生成已阻擋：直播仍在進行中");
    updateSummaryControls();
    return;
  }
  state.summaryLoading = true;
  updateSummaryControls();
  renderSummaryPreview(null, "Summary 生成中，請稍候。");
  try {
    const result = await api(`/sessions/${encodeURIComponent(state.sessionId)}/summarize`, {
      method: "POST",
      body: {
        force: true,
        min_events: 1,
        max_events: 1000,
        chunk_size: 120,
        include_memoria_session: true,
        safe_memory_text: true,
      },
    });
    renderSummaryPreview(summaryFromPayload(result));
    appendLog("INFO", "Summary 已由後端生成");
  } catch (error) {
    renderSummaryPreview(null, `Summary 生成失敗：${error.message || error}`);
    appendLog("WARN", `Summary 生成失敗：${error.message || error}`);
  } finally {
    state.summaryLoading = false;
    updateSummaryControls();
  }
}

function applyTestAutoSaveState(source = "測試設定") {
  scheduleTestSettingsSave(source);
}

function updateLiveSettingsSummary() {
  const autoInject = $("autoInjectPendingEnabled").checked ? "自動注入 ON" : "自動注入 OFF";
  const tts = $("ttsEnabled").checked ? "TTS ON" : "TTS OFF";
  const presentation = $("presentationQueueEnabled").checked ? "Queue ON" : "Queue OFF";
  const liveEvents = $("showLiveEventsEnabled").checked ? "事件顯示 ON" : "事件顯示 OFF";
  const freeTalk = $("postPlanFreeTalkEnabled").checked ? "Plan後雜談 ON" : "Plan後雜談 OFF";
  const scLimit = readPositiveNumber($("superChatBatchLimit"), 3);
  const duration = readPositiveNumber($("plannedDurationMinutes"), 52);
  liveSettingsSummary.textContent = `${autoInject} · ${freeTalk} · ${presentation} · ${tts} · ${liveEvents} · SC 批次 ${scLimit} · ${duration} 分鐘`;
  updatePreflightChecklist();
}

function applySystemAutoSaveState(source = "系統設定") {
  const configured = connectorApiKeyInput.value.trim().length > 0 || connectorApiKeyInput.placeholder.includes("已設定");
  connectorStatusBadge.textContent = configured ? "已設定" : "未設定 API Key";
  connectorStatusBadge.className = configured ? "state-badge good" : "state-badge warn";
  memoriaAuthState.textContent = $("memoriaAdminBypass").checked ? "Admin bypass" : "帳密模式";
  memoriaAuthState.className = "state-badge good";
  scheduleSystemSettingsSave(source);
}

async function testMemoriaAuthSettings() {
  const baseUrl = memoriaBaseUrl.value.trim() || "http://127.0.0.1:8088";
  memoriaAuthState.textContent = "測試中";
  memoriaAuthState.className = "state-badge warn";
  try {
    await api("/memoria/auth/test", {
      method: "POST",
      body: collectMemoriaAuthSettings(),
    });
    memoriaAuthState.textContent = "測試完成";
    memoriaAuthState.className = "state-badge good";
    appendLog("DEBUG", `MemoriaCore Auth 測試：${baseUrl}`);
  } catch (error) {
    memoriaAuthState.textContent = "測試失敗";
    memoriaAuthState.className = "state-badge warn";
    appendLog("WARN", `MemoriaCore Auth 測試失敗：${error.message || error}`);
  }
}

function bindLiveSettingsControls() {
  liveSettingControls.forEach((control) => {
    control.addEventListener("input", updateLiveSettingsSummary);
    control.addEventListener("change", updateLiveSettingsSummary);
  });
}

function bindEvents() {
  startButton.addEventListener("click", () => startLive());
  stopButton.addEventListener("click", () => stopLive());
  detectSourceButton.addEventListener("click", () => refreshStudioSession());
  reloadFreeTalkTopicsButton.addEventListener("click", () => loadFreeTalkTopics());
  refreshRolesButton.addEventListener("click", async () => {
    await loadEpisodePlanCharacters(planSelect.value);
    appendLog("INFO", "直播角色設定已重新整理");
  });
  openRoleSettingsButton.addEventListener("click", openRoleSettings);
  manualVideoInput.addEventListener("input", () => {
    updateSourceDetectionState();
    updatePreflightChecklist();
  });
  $("clearConversation").addEventListener("click", clearConversation);
  $("sendTest").addEventListener("click", sendTestMessage);
  startFreeTalkTestButton.addEventListener("click", startFreeTalkTest);
  skipMainToFreeTalkButton.addEventListener("click", skipMainToFreeTalk);
  $("runAutoCommentBatch").addEventListener("click", () => {
    generateAutoComments();
    applyTestAutoSaveState("自動留言測試");
  });
  $("clearTest").addEventListener("click", () => {
    testMessage.value = "";
    testResult.textContent = "等待測試留言。";
    updateTestCount();
    applyTestAutoSaveState("留言測試");
  });
  $("enablePresentationAudio").addEventListener("click", () => {
    retryCurrentPresentationAudio().catch((error) => {
      appendLog("WARN", `啟用 TTS 聲音失敗：${error.message || error}`);
    });
  });
  $("skipPresentation").addEventListener("click", () => {
    skipCurrentPresentation().catch((error) => {
      appendLog("WARN", `跳過 TTS 句子失敗：${error.message || error}`);
    });
  });
  $("regenerateSummary").addEventListener("click", regenerateSummary);
  $("testMemoriaAuthButton").addEventListener("click", testMemoriaAuthSettings);
  livePersonaCharacterSelect.addEventListener("change", () => {
    fillLivePersonaFormForSelectedRole();
    appendLog("INFO", `切換角色設定：${livePersonaCharacterSelect.options[livePersonaCharacterSelect.selectedIndex]?.text || selectedRoleId()}`);
  });
  livePersonaAvatarSelect.addEventListener("change", () => {
    const selected = state.avatarAssets.find((asset) => asset.url === livePersonaAvatarSelect.value);
    if (!selected?.url) return;
    applyAvatarUrl(selected.url, "角色頭像");
  });
  uploadAvatarButton.addEventListener("click", () => {
    uploadLocalAvatar();
  });
  livePersonaAvatarFile.addEventListener("change", () => {
    if (livePersonaAvatarFile.files?.[0]) {
      setAutoSaveState(livePersonaSaveState, "idle", `已選取：${livePersonaAvatarFile.files[0].name}`);
    }
  });
  $("liveTtsSourcePreset").addEventListener("change", applyLiveTtsSourcePreset);
  [
    "livePersonaSelfAddress",
    "livePersonaAvatarUrl",
    "livePersonaChatBackgroundColor",
    "livePersonaChatAccentColor",
    "livePersonaSystemPrompt",
    "livePersonaOpeningIntro",
    "livePersonaReplyRules",
    "liveTtsEnabled",
    "liveTtsRefAudioPath",
    "liveTtsPromptText",
    "liveTtsTextLang",
    "liveTtsPromptLang",
    "liveTtsSpeedFactor",
    "liveTtsMediaType",
  ].forEach((id) => {
    $(id).addEventListener("input", () => autoSaveLivePersonaSettings("角色設定"));
    $(id).addEventListener("change", () => autoSaveLivePersonaSettings("角色設定"));
  });
  document.querySelectorAll(".role-section-tab").forEach((button) => {
    button.addEventListener("click", () => switchRoleEditorSection(button.dataset.roleSection));
  });
  [
    "connectorApiKeyInput",
    "memoriaBaseUrl",
    "memoriaUsername",
    "memoriaPassword",
    "memoriaAdminBypass",
    "injectIntervalSeconds",
    "injectMinIntervalSeconds",
    "minPendingComments",
    "pendingForceLimit",
    "autoInjectPendingEnabled",
    "plannedDurationMinutes",
    "autoFinalizeAtLimit",
    "thankUnhandledSuperChats",
    "clearRuntimeSessionAfterSummary",
    "postPlanFreeTalkEnabled",
    "postPlanFreeTalkMinutes",
    "freeTalkClosingTargetBatches",
    "freeTalkClosingMinBatchSize",
    "freeTalkClosingMaxBatchSize",
    "freeTalkClosingTimeLimitSeconds",
    "freeTalkTopicAll",
    "superChatCooldownSeconds",
    "superChatBatchLimit",
    "safeSearchEnabled",
    "presentationQueueEnabled",
    "ttsEnabled",
  ].forEach((id) => {
    $(id)?.addEventListener("input", () => applySystemAutoSaveState("系統設定"));
    $(id)?.addEventListener("change", () => applySystemAutoSaveState("系統設定"));
  });
  planSelect.addEventListener("change", async () => {
    subtitle.textContent = planSelect.options[planSelect.selectedIndex]?.textContent || planSelect.value;
    updatePlanState();
    await loadEpisodePlanCharacters(planSelect.value);
    await loadFreeTalkTopics({ quiet: true });
    appendLog("INFO", `切換企劃：${planSelect.value || "未選擇"}`);
  });
  testMessage.addEventListener("input", () => {
    updateTestCount();
    applyTestAutoSaveState("留言測試");
  });
  [
    autoCommentEnabled,
    normalCommentCount,
    superChatCount,
    maliciousCommentEnabled,
    commentFrequencySeconds,
  ].forEach((control) => {
    control.addEventListener("input", () => {
      applyAutoCommentState();
      applyTestAutoSaveState("自動留言測試");
    });
    control.addEventListener("change", () => {
      applyAutoCommentState();
      applyTestAutoSaveState("自動留言測試");
    });
  });
  $("showLiveEventsEnabled").addEventListener("change", () => {
    applyTestAutoSaveState("畫面顯示");
    renderConversationTimeline();
  });
  autoCommentEnabled.addEventListener("change", () => {
    if (autoCommentEnabled.checked) startAutoComments();
    else stopAutoComments();
  });
  document.querySelectorAll(".segment").forEach((button) => {
    button.addEventListener("click", () => switchDebugTab(button.dataset.tab));
  });
  bindLiveSettingsControls();
}

async function initStudio() {
  updateClock();
  setInterval(updateClock, 1000);
  applySessionSnapshot(null);
  renderConversationEmpty();
  bindEvents();
  updatePlanState();
  updateRoleBindingState();
  updateSourceDetectionState();
  updateTestCount();
  applyAutoCommentState();
  updateLiveSettingsSummary();
  fillLivePersonaFormForSelectedRole();
  await initStudioApi();
  await loadStudioSettings();
  await loadAvatarAssets();
  await loadEpisodePlans();
  await refreshStudioSession();
}

initStudio();
