import { $, state } from "./core.js";

export function selectedSessionId() {
  return $("sessionSelect").value || $("sessionId").value.trim();
}

export function selectedSessionInfo() {
  const id = selectedSessionId();
  return state.sessions.find((session) => session.session_id === id) || null;
}

export function selectedTopicPack() {
  const packId = Number($("topicPackSelect").value || 0);
  return state.topicPacks.find((pack) => Number(pack.id) === packId) || null;
}

export function currentTopicEntryId() {
  return Number(state.currentTopicEntryId || $("topicEntrySelect").value || 0);
}

export function topicEntryById(entryId) {
  const id = Number(entryId || 0);
  if (!id) return null;
  return state.topicEntries.find((entry) => Number(entry.id) === id) || null;
}

export function selectedTopicEntry() {
  return topicEntryById(currentTopicEntryId());
}

export function defaultLiveSession(preferredId = "") {
  return state.sessions.find((session) => session.session_id === preferredId)
    || state.sessions.find((session) => session.runtime_status?.running || session.status === "running")
    || state.sessions.find((session) => session.target_memoria_session_id)
    || state.sessions[0]
    || null;
}

export function requestedSessionIdFromUrl() {
  return new URLSearchParams(location.search).get("session_id") || "";
}

export function isSelectedSessionRunning() {
  const session = selectedSessionInfo();
  const runtimeStatus = session?.runtime_status?.status || session?.status || "stopped";
  return session?.runtime_status?.running || runtimeStatus === "running" || runtimeStatus === "starting";
}
