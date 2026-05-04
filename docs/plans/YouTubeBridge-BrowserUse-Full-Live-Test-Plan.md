# YouTubeBridge Browser Use 完整直播流程測試計畫

## Summary

本文件用於未來重跑 YouTubeBridge Phase 2 完整直播流程測試。測試目標是用 Codex in-app Browser Use 對 `http://127.0.0.1:8091/ui/` 執行接近真實直播的端到端流程，驗證 Live Session 建立、測試留言、導播、自動與手動注入、Chat Preview、Queue、Summary、Shared Memory 與結束清理。

預設使用 YouTubeBridge 測試模式，不需要真實 YouTube `video_id`。若完整 Chat 跳到 MemoriaCore login，使用者可提供測試帳密執行登入；密碼不得寫入文件、Log、截圖說明或測試報告。

## Browser Use 測試前置

- 初始化 Browser Use：
  - 使用 `setupAtlasRuntime({ backend: "iab" })`。
  - 取得或建立 tab，前往 `http://127.0.0.1:8091/ui/`。
  - 等待 `domcontentloaded`，避免使用 `networkidle`，因 SSE 會保持連線。
- 頁面健康檢查：
  - `Bridge online` 可見。
  - `Live Session`、`Recent Events`、`Chat Preview`、`Log` 可見。
  - console error 不應出現阻斷性 JS error。
- 後端狀態檢查：
  - MemoriaCore Auth 顯示已設定，或可透過 UI 設定。
  - `測試連線並更新下拉` 後角色與 MemoriaCore session 下拉可載入。
  - 角色下拉至少包含目前測試環境可用角色。

## 核心流程測試

### 1. 建立與啟動直播 Session

- 點 `新直播`。
- 設定：
  - 顯示名稱：`BrowserUse full live flow`。
  - `YouTube video_id 或 URL` 留空，確認測試模式可啟動。
  - `MemoriaCore session` 選 `自動建立或沿用 Live Session 目標`。
  - `角色` 全選。
  - `自動注入` 勾選。
  - `動態調整注入間隔` 勾選。
  - `到達預計時間自動結束` 勾選。
  - `摘要 / 記憶處理完成後自動刪除 runtime session` 勾選。
  - 預計直播分鐘：`30`。
- 點 `開始`。
- 驗證：
  - 按鈕變成紅色 `停止`。
  - Log 出現 start 完成。
  - Live Session 下拉出現新 session。
  - Chat Preview 狀態中可見 `target_memoria_session_id` 或稍後可見。
  - 不應出現 `live session 需要 video_id 或 live_chat_id`。

### 2. 導播開場與自動對話

- 進入 `Director` tab。
- 設定：
  - `idle_seconds = 10`。
  - 本場直播方向：`先聊四月新番，讓角色自然開場並邀請觀眾留言。`
- 點 `更新導播設定`。
- 若導播尚未啟動，點 `啟動導播`。
- 驗證：
  - Director state 顯示 enabled/running。
  - Log 或 Queue 出現 director job。
  - Chat Preview 出現系統事件與 assistant 回覆。
  - 至少一位角色有開場白。
  - 回覆內容不應提到內部 queue、prompt、system prompt 或工具細節。

### 3. 測試留言生成與注入

- 回到 `Recent Events`。
- 設定測試留言數：`5`。
- 測試留言方向：`針對剛剛的角色開場和四月新番提出自然留言。`
- 勾選 `使用 LLM 生成測試留言`。
- 點 `生成測試留言`。
- 驗證：
  - Recent Events 出現 5 則新留言。
  - 留言格式只顯示觀眾名稱與內容，不顯示 YouTube channel id、timestamp、`textMessageEvent`。
- 點 `注入未處理留言`。
- 驗證：
  - Queue 出現 injection job。
  - Chat Preview 出現 `系統事件`，包含外部上下文注入摘要。
  - AI 回覆有回應觀眾留言內容。
  - 已處理留言在 Recent Events 中變淡或不可再次勾選。
  - 再次點 `注入未處理留言` 不應重複注入同一批留言。

### 4. 導播方向動態切換

- 進入 `Director`。
- 將方向改成：`接下來把話題自然轉向 LLM，聊大型語言模型怎麼協助直播互動。`
- 點 `更新導播設定`。
- 等待超過 `idle_seconds`。
- 驗證：
  - Queue 出現 director job。
  - Chat Preview 新增角色回覆。
  - 回覆自然從四月新番轉到 LLM。
  - 不需要觀眾新留言也能由導播推進。
- 再將方向改成：`最後轉向美食，讓角色聊最近想吃什麼，並準備收束直播。`
- 將 `idle_seconds` 改成 `10`。
- 點 `更新導播設定`。
- 驗證：
  - Director JSON / 狀態反映新的 `idle_seconds`。
  - 下一次導播動作在新 idle 門檻附近發生。
  - 回覆主題轉向美食。
  - 連續 AI 主動回合達上限時，導播應 wait，不無限自言自語。

### 5. 動態注入與中斷

- 在 AI 正在生成或 Queue active 時生成新測試留言。
- 驗證：
  - 若 active generation 存在，動態注入不應立刻粗暴插入。
  - pending 留言累積到上限時可觸發注入。
- 點 `中斷目前回應`。
- 驗證：
  - Queue active job 變成 interrupted/discarded 或完成 graceful interrupt。
  - Chat Preview 不應保存半截 assistant reply。
  - 應出現 1-2 句自然收尾或切換提示。
  - 新高優先級留言可被後續處理。

### 6. Chat Preview 與完整 Chat

- 點 `更新 Chat Preview`。
- 驗證：
  - 狀態先顯示 `Chat Preview 更新中...`。
  - 完成後顯示 `MemoriaCore session: ... 已更新`。
  - Log 出現 Chat Preview 已更新。
- 點 `開啟完整 Chat`。
- 驗證：
  - 在 in-app browser 中會跳轉至 MemoriaCore Chat 或 login。
  - 若跳到 `static/login.html`，使用使用者提供的測試帳密登入。
  - 登入後驗證 URL 回到 chat 頁、目標 session 被選中、聊天紀錄與 Bridge Chat Preview 內容一致。
  - 若仍停在 login，記錄為完整 Chat 登入流程問題；Bridge Chat Preview 不因此判定失敗。

### 7. Summary 與 Shared Memory

- 進入 `Summary`。
- 點 `產生摘要`。
- 驗證摘要內容：
  - 包含整場直播主題脈絡：四月新番、LLM、美食。
  - 包含觀眾留言與 AI 回覆互動，而不只列觀眾留言。
  - `memory_text` 不包含 prompt injection 原文、channel id、timestamp、API key、token。
- 點 `寫入 shared memory`。
- 驗證：
  - Summary state 顯示 completed 或 memory write completed。
  - Log 顯示寫入成功。
  - shared memory audience 對應本 Live Session 選擇的角色。
  - 不應寫入 private memory。

### 8. 結束與清理

- 點 `停止`。
- 驗證：
  - 按鈕變回 `開始`。
  - SSE 狀態為 stopped 或不再產生新 director action。
- 若 `到達預計時間自動結束` 測試需要縮短：
  - 建立另一個短 session，預計分鐘設為 `1`。
  - 驗證到時後自動停止或標記結束。
- 若 `摘要 / 記憶處理完成後自動刪除 runtime session` 已勾：
  - 確認 summary/memory 完成後 runtime session 可自動刪除。
  - 多直播除錯下拉中不應殘留已清理 session。
- 若不執行刪除：
  - 記錄 session id，供後續除錯。

## 失敗與邊界測試

- MemoriaCore Auth 未設定：
  - Chat Preview 顯示讀取失敗或提示設定 Auth。
  - `測試連線並更新下拉` 失敗時不應造成整頁 JS 中斷。
- 無角色選取：
  - 開始直播應阻擋或以預設助理處理；實際行為需在 Log 中明確可見。
- 無 MemoriaCore target session：
  - Chat Preview 應顯示尚未綁定，而不是空白沒反應。
- 測試模式無 `video_id`：
  - 應可啟動。
  - 真實模式若有 `video_id` 則才嘗試 YouTube live chat 連線。
- SSE 中斷：
  - UI 應顯示 SSE 連線中斷。
  - 手動 `更新` 可恢復最新狀態。
- 導播 wait 過久：
  - 改變直播方向後應能強制 transition，不應永久等待已處理留言。
- 重複注入：
  - 已處理留言不應被再次注入。
- Browser Use 限制：
  - 不用 `networkidle`。
  - 不依賴新分頁彈出。
  - 每次操作前用 DOM snapshot 或 locator count 確認唯一目標。

## Acceptance Criteria

- 一輪測試內至少產生：
  - 1 個新 Live Session。
  - 3 個角色被選入。
  - 3 個導播主動回合：開場、LLM 轉場、美食轉場。
  - 至少 5 則測試留言。
  - 至少 1 次留言注入。
  - 至少 1 次 Chat Preview 更新。
  - 1 份直播摘要。
  - 1 次 shared memory 寫入。
- UI 不出現阻斷性 console error。
- Log、Queue、Chat Preview 三者能互相對應同一批互動。
- 觀眾留言、AI 回覆、導播方向、摘要內容在語意上能串成同一場直播。
- 測試結束後 session 狀態清楚：已停止、已刪除，或保留且記錄 session id。

## Assumptions

- 測試預設使用 YouTubeBridge 測試模式，不連真實 YouTube live chat。
- 測試會寫入本機 runtime DB、MemoriaCore chat history 與可能的 shared memory；若要避免污染正式資料，應先切到測試 DB 或測試角色。
- `開啟完整 Chat` 依賴 MemoriaCore 前端登入狀態；登入頁不代表 Bridge Chat Preview 失敗。
- Browser Use 僅負責 UI 操作與可視狀態驗證；必要時可輔助讀 Log / Queue / DOM，但不直接改 repo 檔案。
