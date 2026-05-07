import { $, state, api, log } from "./core.js";
import { refreshTopicEntries, refreshTopicPacks } from "./topic-pack-crud.js";
import { factCardActionsBlockedDuringLive, updateTopicActionVisibility } from "./topic-packs.js";

export function setFactCardImportBusy(isBusy, message = "正在讀取 FactCards 資料夾、建立資料卡並重建向量，請稍候。") {
  const busy = !!isBusy;
  state.factCardImportBusy = busy;
  $("factCardImportMessage").textContent = message;
  $("factCardImportOverlay").classList.toggle("is-hidden", !busy);
  $("factCardImportOverlay").setAttribute("aria-hidden", busy ? "false" : "true");
  updateTopicActionVisibility();
}

export async function importFactCardsFolder() {
  if (factCardActionsBlockedDuringLive()) throw new Error("直播中不產生或匯入 Fact Cards");
  const packId = Number($("topicPackSelect").value || 0) || null;
  setFactCardImportBusy(true);
  try {
    const data = await api("/topic-packs/fact-cards/import-folder", {
      method: "POST",
      body: JSON.stringify({
        pack_id: packId,
        max_files: 50,
      }),
    });
    log("FactCards 資料夾已匯入", data);
    await refreshTopicPacks();
    $("topicPackSelect").value = data.pack_id;
    await refreshTopicEntries();
    if (data.graph?.status && data.graph.status !== "completed") {
      $("topicGraphState").textContent = "匯入完成，但關係圖建立失敗，請查看 Log 或點重建關係圖";
      $("topicGraphState").className = "status bad";
    }
  } finally {
    setFactCardImportBusy(false);
  }
}

export async function rebuildTopicEmbeddings() {
  const packId = Number($("topicPackSelect").value || 0);
  if (!packId) throw new Error("請先選擇資料包");
  const data = await api(`/topic-packs/${packId}/embeddings/rebuild`, {
    method: "POST",
    body: "{}",
  });
  log("向量索引已重建", data);
  await refreshTopicEntries();
}
