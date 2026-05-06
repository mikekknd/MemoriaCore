# YouTubeBridge Live E2E 未完成問題追蹤

建立日期：2026-05-04
最後整理：2026-05-06

## 目的

這份文件只保留 YouTubeBridge 長時間直播 E2E 測試中「尚未完成、仍需追蹤或需要後續修正」的項目。
已完成或已歸檔的歷史項目已移到 `docs/plans/YouTubeBridge-Live-E2E-Resolved-Issues.md`。

## 目前剩餘測試目標（2026-05-06）

目前 dashboard 與直播流程已改成「單一 Live Session」與「FactCards 只作為預先準備的資料庫」。後續測試以此為基準；舊測試計畫中出現的 `Recent Events`、獨立 `Director` tab、`Queue` tab、`Chat Preview`、手動選擇 MemoriaCore session、直播中 auto-build / 自動補卡等流程都不可再作為主要驗證路徑。

1. Targeted regression
   - 最新狀態：2026-05-06 已通過 `python -m pytest YouTubeBridge\tests\test_server_auth.py YouTubeBridge\tests\test_storage.py YouTubeBridge\tests\test_bridge_engine.py YouTubeBridge\tests\test_fact_cards.py YouTubeBridge\tests\test_summary_engine.py tests\test_chat_external_context.py --basetemp=.pyTestTemp\basetemp-targeted-regression-20260506 -q`，共 206 passed。
   - 跑 YouTubeBridge UI / API 回歸，確認單一 Live Session、角色選擇限制、正式直播停用測試留言、Topic Pack CRUD、Fact Card 生成 / 匯入 live lock、runtime/log launcher 規則仍通過。
   - 跑 Bridge engine / storage / summary / FactCards 回歸，確認導播 idle、SafetyLLM、Research worker、FactCards usage、summary factuality gate 與 closing SC thanks 沒被 UI 改動破壞。
   - 跑 `tests/test_chat_external_context.py` 相關回歸，確認 YouTube live external context 仍是 public/transient，不寫 private memory，也不公開導播節奏提示。

2. Browser smoke
   - 最新狀態：2026-05-06 Browser smoke 已通過；測試 URL `http://localhost:8091/ui?smoke=full-*`，並補做 `/live/` reload 驗證。
   - 已確認目前 tab 為 `Live Session`、`留言測試`、`Summary`、`Topic Pack`、`系統設定`、`規則說明`。
   - 已確認 `data-testid="director-idle-seconds"` 可被 Browser Use 找到；正式 YouTube URL 會停用手動與自動測試留言；Topic Pack 頁面不顯示 usage 摘要、自動補卡、依主題自動建立資料卡或建立張數。
   - 已確認 `/live/?session_id=yt_20260506_170833_2435497b` wrapper reload 後兩個 iframe 都綁定同一 session；直接 reload `/live-chat/?session_id=...` 顯示 `46/46` 則，沒有回到 0 則。
   - 用目前 UI 驗證 6 個 tab：`Live Session`、`留言測試`、`Summary`、`Topic Pack`、`系統設定`、`規則說明`。
   - Live Session 左右 panel 要符合新布局：左側 YouTube URL / 角色 / 導播設定 / 開始直播；右側注入、SC、Topic Pack 綁定、自動化與收尾設定。
   - 沒有角色時禁止開始；角色數量上限需跟 MemoriaCore `max_session_characters` 同步。
   - 真實 YouTube URL 模式下，手動與自動測試留言功能要灰階或阻擋。
   - Topic Pack 頁面不得顯示 usage 摘要、自動補卡、依主題自動建立資料卡、建立張數等已移除功能。

3. 10 分鐘動畫新番 E2E
   - 最新狀態：2026-05-06 已完成 10 分鐘動畫新番 E2E，session `yt_20260506_170833_2435497b`，Topic Pack `49`。
   - 測試設定：注入間隔 180 秒、動態最短 120 秒、SC 打斷 CD 120 秒、導播回合上限 10、idle 10 秒、啟用 Research Gate / LLM 測試留言 / 惡意留言與 SC；本輪為 post-test 稽核保留 runtime session，`auto_delete_after_processed=false`。
   - 結果：session `ended`、runtime `ended`、director `ended`、active interaction `null`、未處理 SC `0`、summary `completed`，shared memory 寫入 `completed`，memory block `c62f8c50-0876-4024-af65-987f742097c4`。
   - Browser / API 稽核：live chat reload 顯示 `46/46` 則；公開 chat-preview 沒有 `## Summary`、`## Facts`、`<external_chat_context>`、攻擊原文、`導播提醒` 或 `安全處理`。
   - Health 稽核：測試期間每輪 8088 `/api/v1/health` 與 8091 `/health` 均回應，未觀察到 8 秒以上 timeout；`runtime/log/*.err.log` 未新增 `Accept failed on a socket`、`WinError 10054`、`WinError 64`、`proactor_events.py`、`Invalid argument` 或 `Traceback`。
   - 觀察到的殘留風險：SC 打斷 director 時 interaction metadata 仍記錄 `error: "'NoneType' object has no attribute 'read'"`，流程未卡住也未寫入 err log；後續可視為 provider cancel metadata 清理問題另案追蹤。
   - 測試前完整停止 8088 / 8091 hot reload wrapper、worker 與 listener，使用非 hot reload server 啟動。
   - 清空 `runtime/llm_trace.jsonl`，確認 `runtime/` 根層沒有新散落 process log；stdout/stderr 只應出現在 `runtime/log/`。
   - 預先建立或選擇動畫新番 Topic Pack，再從 Live Session 綁定；直播中不可產生或匯入 FactCards。
   - 建議設定：注入間隔 180 秒、動態最短 120 秒、SC 打斷 CD 120 秒、導播回合上限 10、idle 10 秒、啟用安全搜尋、LLM 測試留言、惡意留言與 SC。
   - 驗證 8088 / 8091 health 全程無 timeout，角色有多輪互相接話，Director idle 能續話題，SC closing thanks 完成，session ended 後 summary 自動完成並寫入 shared memory。
   - 驗證 live chat 不顯示 hidden context、完整導播 prompt、攻擊原文、Topic Pack raw content、Fact Card markdown 或 embedding。
   - 驗證 FactCards usage 在 API 中增加，但沒有觸發 Gemini 產卡、資料夾匯入、自動補卡 worker 或 auto-build route。

4. Research / Query Resolver
   - 觀眾資料型提問若 FactCards 可回答，應只召回相關資料，不把不相關卡片硬塞給角色。
   - FactCards 不足且啟用 Research Gate 時，外部搜尋 fallback 必須走背景 worker，不可阻塞 live injection 主流程。
   - `completed_no_results`、缺 key、cooldown、低品質來源必須可辨識為 degraded，不可被 UI 或 API 當作成功 fact card。

5. 單一 Live Session 測試邊界
   - 最新狀態：2026-05-06 起不再把 checkpoint / resume / 重跑既有 session 當作 E2E 主要驗證目標。
   - 現行產品流程是單一 Live Session：啟動新直播會先收尾並清理舊 session，再建立全新 session。
   - `runtime/youtube_bridge_e2e_checkpoint.json` 只保留為舊測試輔助或人工排錯線索；新一輪 E2E 不需要驗證從 checkpoint resume。
   - 若長時間測試中斷，優先依單一 session 狀態做 post-mortem；需要重跑時從乾淨環境啟動新直播，不測「續跑同一 session」。

## 復發風險索引

以下類型目前不列為待辦，但屬於已經踩過、未來可能因改動再次復發的問題。若遇到類似症狀，先回 `docs/plans/YouTubeBridge-Live-E2E-Resolved-Issues.md` 查對應編號與既有修法，再判斷是否需要重新開 issue。

- Session lifecycle / reload / stale state：查 resolved issue 1、12、13、17、31、34、36、40。
  常見症狀包含舊 session 干擾新測試、server 重啟後留下 stale running interaction、ended 後 director state 沒同步、duration closing 前最後一批事件殘留 pending、`/live/` 或 chat preview reload 狀態不同步。

- Browser Use / UI selector / route order 脆弱性：查 resolved issue 3、5、6、10、11、14。
  常見症狀包含 hidden pane 欄位不可填、固定路由被動態路由吃掉、UI 狀態文字不足以作為等待條件、PowerShell 腳本踩 `$PID`、Windows cp950 console log 造成流程中斷。

- Queue / interrupt / closing 競態：查 resolved issue 18、19、23、27、40。
  常見症狀包含 closing SC thanks 被 SC backlog 插隊、多筆 `running` interaction 同時存在、provider cancel 後 read 例外、SC 已標記 handled 但沒有可見收尾訊息。

- Hidden context / prompt / metadata 外洩：查 resolved issue 16、20、21、26、35、37、38、39、41。
  常見症狀包含 `<external_chat_context>` 或 Topic Pack 原文出現在聊天室、直播互動觸發 private memory pipeline、Chat Preview 或 interactions API 回傳 debug prompt、metadata 暴露惡意樣本、director 字眼或可疑留言出現在公開流程。

- Performance / timeout / payload 過大：查 resolved issue 15、24、35、36。
  常見症狀包含高頻留言或 SC 壓測下 Chat Preview timeout、UI iframe 空白、payload 過大導致 reload 卡頓、後端忙碌時沒有 stale cache fallback。

- 長時間 E2E process hygiene：測試流程守則見 `docs/plans/YouTubeBridge-BrowserUse-Full-Live-Test-Plan.md` 的「長時間 E2E process hygiene」。
  10 分鐘以上 E2E 測試前必須先確認 8088 / 8091 / hot reload wrapper / worker process 都已清乾淨，並使用非 hot reload server 啟動；hot reload 只作短版開發驗證。

- Safety / 惡意留言覆蓋率：查 resolved issue 29、32、38、41。
  常見症狀包含括號式角色狀態注入、URL/token-like SC、惡意樣本透過 metadata 洩漏、pending/suspicious event 被公開顯示。

- Topic Pack / Research Gate 輸出污染：查 resolved issue 22。
  常見症狀包含 embedding array 被輸出到 UI Log，或資料卡建立流程把大量內部資料 dump 到前端。Research 品質本身仍屬未完成項，見 issue 7、25、28、30。

## 未完成 / 需追蹤項目
### 2. 刪除 session 是破壞性動作，需要清楚確認

現象：
- 刪除 Live Session 會刪除本機 runtime 資料：
  - raw events
  - queue / interactions
  - director state
  - live session topic pack 關聯

影響：
- 測試自動化若直接刪除，可能誤刪還需要分析的測試資料。

改善方向：
- 目前 UI 依測試效率需求不跳確認；測試腳本只能對明確標記為 disposable 的 session 執行刪除。
- 刪除前應先記錄 session id、summary 狀態與必要 debug probe，避免刪掉還需要分析的 E2E 現場。
- 可考慮新增「封存 / 清空目前測試 session」與「永久刪除」分流。

### 7. Research Gate 成功與「真的有外部資訊」需要分開驗證

現象：
- Research Gate 可能建立 fact card，但如果 Tavily key 缺失或搜尋失敗，fact card 內容可能只是錯誤摘要。
- UI log 只看「Research Gate fact card 已建立」不足以證明取得了最新資訊。

影響：
- 「已調用搜尋」和「取得可用最新資訊」是不同層級的驗證。

改善方向：
- 測試需要檢查 `research_requests` 狀態與 fact card body。
- UI 可在 fact card 上顯示 Research Gate 的 success / failed / degraded 狀態。
- 若搜尋工具缺 key，應顯示明確警告，不應被誤認為成功補資料。

### 9. checkpoint / resume 不再是主要 E2E 目標

現象：
- 舊測試流程曾規劃用 checkpoint 續跑既有 session，避免中斷後重建 session 污染 DB。
- 目前產品設計已改為單一 Live Session：新直播會自動收尾與清理舊 session，因此不再需要測「重跑同一 session」。

影響：
- 若測試文件仍要求 checkpoint / resume，會和目前單一 session 流程互相矛盾。
- 舊 `runtime/youtube_bridge_e2e_checkpoint.json` 可以保留作人工排錯線索，但不應成為新 E2E pass / fail 條件。

改善方向：
- 新 E2E 只驗證乾淨環境啟動、單一 session 跑完、summary/shared memory 完成、live reload 綁定最新 session。
- 中斷時記錄目前 session id 與 post-mortem 資料；真正重跑時重新清環境並開新直播。

### 25. Research Gate fact card 內容仍偏 raw search dump，且來源品質/來源欄位不夠乾淨

現象：
- 10 分鐘 E2E 測試自動建立 Topic Pack 後，fact card body 仍保存 `{"search_results": "...長文字..."}` 形式的原始搜尋結果摘要。
- `source_url` 欄位為空，無法從 UI 直接追蹤資料來源。
- 搜尋結果混入 Facebook、商店頁、簡中整理站等來源，內容可用但品質不穩。

影響：
- LLM 可能引用未整理或低可信度內容。
- 使用者難以審查 fact card 是否值得放進直播上下文。
- 向量檢索可召回內容，但召回的是長篇 raw dump，不是乾淨知識卡。

改善方向：
- Research Gate 結果應先整理成「結論、作品/名詞、日期或製作資訊、來源摘要」的短 fact card。
- 儲存 top sources 的 URL / title / publisher，UI 顯示來源清單。
- 加入來源白名單或可信度排序；低可信來源只作輔助，不直接進 memory_text 或關鍵 fact。

### 28. Research Gate 有觸發但未取得可用外部資料

現象：
- 2026-05-04 16:22 的 10 分鐘 E2E 測試 session `yt_20260504_162209_75cf1025` 啟用 `research_enabled=true`，並透過 Topic Pack auto-build 建立 4 張 `source_type=research_gate` fact card。
- 4 張 fact card 全部為 `status: completed_no_results`，內容都是「沒有取得可用摘要 / 目前沒有可引用的外部資料 / source_urls: none」。
- 隨後直接呼叫 `/research/request` 查詢「2026 年 4 月 新番 動畫 官方 播出 資訊 重點作品」時，被 `Research Gate 冷卻中，稍後再查` 擋下。

影響：
- E2E 能證明 Research Gate 流程被觸發，但不能證明直播真的取得了最新外部資料。
- 對「四月新番」這類需要時效資訊的主題，角色仍可能只能根據既有上下文與模型常識回應。
- 使用者在 UI 上只看到資料包已建立，若不展開 fact card，很難知道其實沒有可用來源。

改善方向：
- UI 應把 `completed_no_results` 顯示成明確警示，而不是和成功資料卡混在一起。
- Auto-build 若多張 research card 都無結果，應提示「本次 Research 未取得資料」，並允許使用者立即重試或改查詢字。
- Research quota/cooldown 應區分 auto-build 內部查詢與使用者手動查詢；至少在同一批 auto-build 結束後允許一次手動查詢驗證。
- 對四月新番這類直播主題，可支援「預先建立查詢清單並逐步補齊」的流程，而不是一次 auto-build 全部消耗 quota。

### 30. Fact Card 有效但測試留言中的未驗證內容會被 AI 與 summary 寫成事實

現象：
- 2026-05-04 16:52 的 10 分鐘 E2E 測試 session `yt_20260504_165218_9946608c` 開始前，已建立 `2026 四月新番直播資料包 165218`，4 張 fact card 都成功建立 embedding。
- `/sessions/{id}/topic-packs/search` 以「Witch Hat Atelier Re:Zero 四月新番 續作 新作」與「本季新番有哪些作品可以開場討論」查詢，皆可命中 4 筆資料。
- 直播過程中自動測試留言產生了資料包外的作品名或未驗證說法，例如虛構作品、製作組背景、具體美術技法等。
- 角色回覆與最終 summary / memory_text 將部分未驗證測試留言延伸成確定事實。

影響：
- Topic Pack 本身有效，但缺少 factuality gate 時，AI 仍會把「觀眾留言」當成可信資訊源。
- `memory_text` 可能寫入未驗證或虛構內容，污染 shared memory。
- 使用者會誤以為 fact card 已經約束了直播知識來源，但實際上測試留言仍能把內容帶偏。

改善方向：
- 測試留言生成器應區分 `verified_topic` 與 `speculative/audience_claim`，未驗證作品或說法需加標記。
- 注入給 MemoriaCore 的觀眾留言可保留原話，但 external context 應額外標示「觀眾主張，未經 fact card 驗證」。
- Summary / memory_text 生成前應加 factuality gate：只能把 Topic Pack、Research Gate 或明確標記為已驗證的內容寫成事實；其餘只能寫成「觀眾提到」或不寫入 memory_text。
- 若 memory_text 包含資料包外作品名或未驗證技法，應回傳 `memory_text_requires_review=true`，不要允許一鍵寫入 shared memory。

### 33. SafetyLLM prompt 新增後若未重啟 MemoriaCore API 會全數分類失敗

現象：
- 2026-05-04 21:04 的 E2E session 開始後，所有留言的 SafetyLLM 分類都失敗。
- `safety_reason` 顯示 `Prompt key 'youtube_live_safety_classifier_prompt' not found in defaults or user overrides.`。
- 重啟 8088 MemoriaCore API 後，SafetyLLM prompt key 正常載入，後續分類恢復。

影響：
- 新增或修改 `prompts_default.json` 後，如果 API server 沒有重啟，Bridge 端會進入 fail-closed，但整場測試會失去有效 SafetyLLM 判斷。
- UI 只看到安全檢查未完成或分類失敗，不容易直覺知道是 prompt registry 未重載。

改善方向：
- YouTubeBridge E2E bootstrap 前先做 prompt-key smoke test，確認 `youtube_live_safety_classifier_prompt` 可用。
- MemoriaCore 可提供 prompt reload endpoint，或在開發模式偵測 prompt 檔修改後自動重載。
- Bridge UI 的 SafetyLLM 失敗原因若包含 missing prompt key，應顯示明確操作提示：「請重啟 / reload MemoriaCore prompts」。

### 48. FactCards usage telemetry 仍需 10 分鐘 E2E 驗證

現況：
- 已新增 `topic_pack_entry_usages`，記錄 `entry_id`、相似度、使用時間、使用來源與 query 摘要。
- Topic Pack search / live external context / director topic context 召回 fact card 後會記錄 usage。
- `GET /sessions/{session_id}/topic-packs/usage` 保留為除錯 API；控制台不再顯示「已召回 / 未召回 / 最近補卡」摘要。
- 直播中不再自動補卡，也不在 session 執行中呼叫依主題自動建立資料卡；FactCards 只作為預先準備與手動管理的向量資料庫。

仍需驗證：
- 新 10 分鐘動畫新番 E2E 中，FactCards usage 是否持續增加。
- 直播中不應觸發 Gemini 產卡、資料夾匯入、自動補卡 worker 或 auto-build route。
- usage API / 控制台不得顯示 raw FactCard markdown、embedding、hidden context 或 Topic Pack raw content。
- 直播中被召回的 FactCards 應提供可展開討論的細節，而不是反覆召回同一批表面內容。
