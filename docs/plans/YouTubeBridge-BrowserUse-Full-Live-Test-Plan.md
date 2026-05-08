# YouTubeBridge Browser Use 完整直播流程測試計畫

## Summary

本文件用於未來執行 YouTubeBridge Phase 2 完整直播流程測試。測試目標是用 Codex in-app Browser Use 對 `http://127.0.0.1:8091/ui/` 與 `http://127.0.0.1:8091/live/` 執行接近真實直播的端到端流程，驗證單一 Live Session 建立、測試留言、導播、自動與手動注入、`/live/` 左側聊天室、Summary、Shared Memory 與結束清理。

預設使用 YouTubeBridge 測試模式，不需要真實 YouTube `video_id`。若完整 Chat 跳到 MemoriaCore login，使用者可提供測試帳密執行登入；密碼不得寫入文件、Log、截圖說明或測試報告。

## 目前版本基準（2026-05-06）

- 目前 UI 以單一 Live Session 為核心；不再測試多 session 下拉、checkpoint resume、或從舊 session 續跑。
- 啟動新直播時，系統會先收尾 / 清理舊 Live Session，再建立全新 session。長時間 E2E 若中斷，做 post-mortem 後重新清環境開新直播，不把 resume 同一 session 列為 pass / fail 條件。
- 目前主要 tab 是 `Live Session`、`留言測試`、`Summary`、`Topic Pack`、`系統設定`、`規則說明`。舊文件或歷史 issue 中提到的 `Recent Events`、獨立 `Director` tab、`Queue` tab、右側 `Chat Preview`、手動選擇 MemoriaCore session 都不是新版主要驗證路徑。
- Live Session 左右 panel 是主要設定面：左側包含 YouTube URL、角色與導播設定；右側包含注入、SC、Topic Pack 綁定、自動化與收尾設定。
- Topic Pack / FactCards 是預先準備的資料庫；直播中不可生成、匯入或自動補卡。
- 測試主題固定為動畫新番；不要再用舊流程切到 LLM 或美食。

## Browser Use 測試前置

- 長時間 E2E process hygiene：
  - 10 分鐘以上的 Browser E2E / 直播流程測試一律不要使用 8088 或 8091 的 hot reload，例如 `startServerHotReload.bat`、`YouTubeBridge/start_hot_reload.bat` 或任何 `uvicorn reload=True`。
  - hot reload 只用於 UI / API 開發中的短版手動驗證；長時間 E2E 要使用非 hot reload 啟動，例如 MemoriaCore `start.bat` / `run_server.py`，以及 YouTubeBridge `start.bat` / `server.py`。
  - 測試前先確認 8088 與 8091 環境乾淨，不能只看瀏覽器畫面是否關閉；必須確認沒有舊 `LISTENING`、沒有 hot reload wrapper，也沒有殘留 worker。
  - 建議檢查：
    - `netstat -ano | findstr ":8088"` 不應有舊的 `LISTENING`。
    - `netstat -ano | findstr ":8091"` 不應有 `LISTENING`。
    - `Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'startServerHotReload.bat|run_server_hot_reload.py' }` 不應有舊的 MemoriaCore 8088 hot reload process。
    - `Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'YouTubeBridge\\\\start_hot_reload.bat|YouTubeBridge\\\\run_server_hot_reload.py|server.py' }` 不應有舊的 YouTubeBridge 8091 process。
    - 若曾跑 hot reload，除了 listener PID，也要清掉 command line 含 `startServerHotReload.bat` / `run_server_hot_reload.py` / `YouTubeBridge\start_hot_reload.bat` / `YouTubeBridge\run_server_hot_reload.py` 的整棵 process tree；只殺 port owner 可能留下 reload parent 或 multiprocessing worker。
  - 啟動非 hot reload server 後，記錄 8088 與 8091 listener PID 與 process start time，再進行測試；測試途中若任一 PID 改變，該輪 E2E 視為受環境干擾，需要重新判定。
  - 若 8088 / 8091 由使用者端 bat 控制，Codex 不應另行背景啟動同 port server；避免兩組 parent / worker 互相踩 port。
  - Codex / Agent 自行啟動 E2E server 時，測試腳本或人工流程必須使用 `try/finally` 等價流程，在結束、失敗、timeout 或中斷後執行 `stop_e2e_servers.bat`。
  - `stop_e2e_servers.bat` 會依序清理 MemoriaCore 8088 與 YouTubeBridge 8091 的 listener、hot reload wrapper、server parent 與 child process tree，並列印被清理的 PID。
  - `start.bat`、`startServerHotReload.bat`、`YouTubeBridge/start.bat` 與 `YouTubeBridge/start_hot_reload.bat` 啟動前也會先清理同 port process tree；因此不要再用手寫 `Stop-Process` 只殺 port owner 作為 E2E 標準流程。
- 初始化 Browser Use：
  - 使用 `setupAtlasRuntime({ backend: "iab" })`。
  - 取得或建立 tab，前往 `http://127.0.0.1:8091/ui/`。
  - 等待 `domcontentloaded`，避免使用 `networkidle`，因 SSE 會保持連線。
- 頁面健康檢查：
  - `Bridge online` 可見。
  - `Live Session`、`留言測試`、`Summary`、`Topic Pack`、`系統設定`、`規則說明` 可見。
  - `/live/?session_id=...` wrapper 會把左側 iframe 設為 `/live-chat/?session_id=...`，右側 iframe 設為 `/ui/?embedded=control&session_id=...`。
  - console error 不應出現阻斷性 JS error。
- 後端狀態檢查：
  - MemoriaCore Auth 顯示已設定，或可透過 UI 設定。
  - `測試連線並更新角色列表` 後角色清單可載入。
  - 角色下拉至少包含目前測試環境可用角色。

## 核心流程測試

### 1. 建立與啟動單一 Live Session

- 在 `Live Session` tab 設定本輪直播；不需要手動建立草稿或切換 session。
- 設定：
  - `YouTube video_id 或 URL` 留空，確認測試模式可啟動。
  - `角色` 至少選 1 位，常用 E2E 選可可與白蓮。
  - `話題資料包` 綁定預先建立的動畫新番 Topic Pack。
  - `本場直播方向` 固定動畫新番，不切到 LLM / 美食。
  - `自動注入` 勾選。
  - `到達預計時間自動結束` 勾選。
  - `摘要 / 記憶處理完成後自動刪除 runtime session` 勾選。
  - 預計直播分鐘：依測試設定，10 分鐘 E2E 使用 `10`。
- 點 `開始直播` 或 `開始全新直播`。
- 驗證：
  - 按鈕變成 `結束直播並收尾`。
  - Log 出現 start 完成。
  - `/live/` 左側聊天室與右側控制台都綁同一個 session。
  - `chat-preview` API 中可見 `target_memoria_session_id` 或稍後可見。
  - 不應出現 `live session 需要 video_id 或 live_chat_id`。

### 2. 導播開場與自動對話

- 導播設定位於 `Live Session` 左側角色選擇下方。
- 設定：
  - `idle_seconds = 10`。
  - 本場直播方向：`本場只聊動畫新番，讓角色彼此接話、補充、反駁或提出下一個切入點；除非回應留言或 SC，不要把問題丟回觀眾。`
- 點 `更新導播設定`。
- 驗證：
  - Director state 顯示 running 或後續 ended。
  - interactions API 出現 director job。
  - `/live-chat/` 或 chat-preview API 出現 assistant 回覆。
  - 至少一位角色有開場白。
  - 回覆內容不應提到內部 queue、prompt、system prompt 或工具細節。

### 3. 測試留言生成與注入

- 回到 `留言測試`。
- 設定測試留言數：`5`。
- 測試留言方向：`針對剛剛的角色開場和四月新番提出自然留言。`
- 勾選 `使用 LLM 生成測試留言`。
- 點 `生成測試留言`。
- 驗證：
  - 待處理留言區出現 5 則新留言。
  - 留言格式只顯示觀眾名稱與內容，不顯示 YouTube channel id、timestamp、`textMessageEvent`。
- 點 `注入未處理留言`。
- 驗證：
  - interactions API 出現 injection job。
  - `/live-chat/` 出現 `直播事件`，只顯示可公開摘要。
  - AI 回覆有回應觀眾留言內容。
  - 已處理留言在待處理留言區變淡或不可再次勾選。
  - 再次點 `注入未處理留言` 不應重複注入同一批留言。

### 4. 導播 idle 與續話

- 保持方向在動畫新番。
- 可將方向改成：`接下來深入最新一話劇情細節、作畫品質、社群討論與角色觀點，但不要離開動畫新番。`
- 點 `更新導播設定`。
- 等待超過 `idle_seconds`。
- 驗證：
  - interactions API 出現 director job。
  - `/live-chat/` 或 chat-preview API 新增角色回覆。
  - 回覆仍在動畫新番主題內，並且角色彼此接話，不固定兩人各一次就停。
  - 不需要觀眾新留言也能由導播推進。
  - 連續 AI 主動回合達上限時，導播應 wait 或 recap，不無限自言自語。

### 5. 動態注入與中斷

- 在 AI 正在生成或 interactions API 有 active job 時生成新測試留言。
- 驗證：
  - 若 active generation 存在，動態注入不應立刻粗暴插入。
  - pending 留言累積到上限時可觸發注入。
- 點 `中斷目前回應`。
- 驗證：
  - interactions API 中 active job 變成 interrupted/discarded 或完成 graceful interrupt。
  - `/live-chat/` 不應保存半截 assistant reply。
  - 應出現 1-2 句自然收尾或切換提示。
  - 新高優先級留言可被後續處理。

### 6. `/live/` 左側聊天室

- 開啟或 reload `/live/?session_id=<session_id>`。
- 驗證：
  - 左側 `/live-chat/` 不顯示 0 則，會載入同一個 session 的公開聊天內容。
  - 右側 `/ui/?embedded=control&session_id=...` 綁定同一個 session。
  - 左側聊天室不顯示 `## Summary`、`## Facts`、Topic Pack markdown 原文、hidden context、完整 prompt 或攻擊原文。

### 7. Summary 與 Shared Memory

- 進入 `Summary`。
- 點 `產生摘要`。
- 驗證摘要內容：
  - 包含整場直播主題脈絡：動畫新番。
  - 包含觀眾留言與 AI 回覆互動，而不只列觀眾留言。
  - `memory_text` 不包含 prompt injection 原文、channel id、timestamp、API key、token。
- 驗證：
  - Summary state 顯示 completed。
  - summary metadata 中 `memory_write_status=completed`。
  - shared memory audience 對應本 Live Session 選擇的角色。
  - 不應寫入 private memory。

### 8. 結束與清理

- 點 `結束直播並收尾`，或等待預計直播分鐘到達自動收尾。
- 驗證：
  - 按鈕變回 `開始全新直播`。
  - session status / runtime / director 均為 ended。
  - active interaction 為空。
  - 未處理 SC 為 0。
- 若 `摘要 / 記憶處理完成後自動刪除 runtime session` 已勾：
  - 確認 summary/memory 完成後 runtime session 可自動刪除。
- 若不執行刪除：
  - 記錄 session id，供後續除錯。

## 失敗與邊界測試

- MemoriaCore Auth 未設定：
  - `/live-chat/` 或 chat-preview API 顯示讀取失敗或提示設定 Auth。
  - `測試連線並更新下拉` 失敗時不應造成整頁 JS 中斷。
- 無角色選取：
  - 開始直播應阻擋，並顯示至少選擇一位角色的原因。
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
  - 2 個以上角色被選入，且不超過 MemoriaCore `max_session_characters`。
  - 多個導播主動回合：開場、動畫新番內續話、recap / 收尾。
  - 至少 5 則測試留言。
  - 至少 1 次留言注入。
  - `/live-chat/` 至少 1 次 reload 後仍顯示同一 session 的聊天內容。
  - 1 份直播摘要。
  - 1 次自動 shared memory 寫入。
- UI 不出現阻斷性 console error。
- Log、interactions API、`/live-chat/` 三者能互相對應同一批互動。
- 觀眾留言、AI 回覆、導播方向、摘要內容在語意上能串成同一場動畫新番直播。
- 測試結束後 session 狀態清楚：ended、已刪除，或保留且記錄 session id。

## Assumptions

- 測試預設使用 YouTubeBridge 測試模式，不連真實 YouTube live chat。
- 測試會寫入本機 runtime DB、MemoriaCore chat history 與可能的 shared memory；若要避免污染正式資料，應先切到測試 DB 或測試角色。
- `/live-chat/` 依賴 Bridge 後端透過 MemoriaCore API 讀取目標 session history；若 MemoriaCore 忙碌，應顯示可理解的快取或錯誤狀態。
- Browser Use 僅負責 UI 操作與可視狀態驗證；必要時可輔助讀 Log / interactions API / DOM，但不直接改 repo 檔案。
