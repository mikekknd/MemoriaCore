# YouTubeBridge Live E2E 工作流程問題紀錄

建立日期：2026-05-04

## 目的

這份文件紀錄 YouTubeBridge 使用 Browser Use 做完整直播流程測試時遇到的問題。
這些項目代表目前工作流中容易出錯、需要 UI 改善、或測試腳本需要特別處理的點。

## 已遇到的問題

### 1. 舊 ended session 會干擾新測試

現象：
- UI 會優先載入既有 Live Session。
- 如果上一場已 ended 或有舊設定，測試腳本可能誤用舊 session。
- 舊 session 的 `video_id`、Topic Pack、Director state、log 會讓測試結果混淆。

影響：
- 新直播測試可能沒有真正建立乾淨狀態。
- start / update 可能套用舊直播參數。

改善方向：
- UI 增加「建立新測試直播並忽略舊 session」的一鍵流程。
- 新直播時自動清空 log、事件列表、topic pack selection、director state。
- 測試腳本開頭固定查詢並刪除最近 ended session，或提供專用 cleanup API。

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
- UI 刪除文案已提示，但測試腳本應先取得使用者確認。
- 可考慮新增「封存 / 清空目前測試 session」與「永久刪除」分流。

### 3. Director 設定欄位在非 active pane 時不可操作

現象：
- Browser Use 嘗試填寫 `#directorGuidance` 時失敗，原因是 Director pane 尚未開啟，元素不可見。

影響：
- 自動化測試若直接依 id 填值，會因 hidden pane 失敗。
- 使用者如果在 Live Session 區塊設定完後才想設定導播，需要切換頁籤，流程不夠集中。

改善方向：
- Browser Use 腳本填 Director 前必須切到 Director pane。
- UI 可考慮在 Live Session 區塊顯示「目前導播方向摘要」與快捷編輯入口。
- 或將直播初始化必要欄位做成單一 wizard。

### 4. Director idle 欄位 id 與腳本預期不一致

現象：
- 測試腳本嘗試填 `#directorIdleSeconds`，實際 UI id 是 `#directorIdle`。

影響：
- 這類 id 命名不一致會讓 Browser Use 腳本容易脆弱。

改善方向：
- 統一欄位命名，例如 `directorIdleSeconds`。
- 或在測試腳本中使用 label / data-testid。
- 建議 UI 加上穩定 `data-testid`，不要讓測試依賴文字或臨時 id。

### 5. Topic Pack auto-build 曾被 route order 影響

現象：
- `/sessions/{session_id}/topic-packs/auto-build` 曾被 `/sessions/{session_id}/topic-packs/{pack_id}` 吃掉。
- FastAPI 將 `auto-build` 當成 `pack_id`，導致 int parsing error。

影響：
- 固定路由與動態路由順序錯誤時，前端看起來像 auto-build 失敗，但根因在 route matching。

改善方向：
- 固定路由要放在動態 `{pack_id}` 路由前。
- 新增 route order regression test。

### 6. Topic Pack 建立後 UI 狀態訊息不一定能作為成功判斷

現象：
- 點擊「建立資料包」後，下拉選單已有新增資料包，但 `topicPackState` 仍顯示「N 個資料包」。
- 以 `topicPackState` 等待「資料包已建立」會 timeout。

影響：
- Browser Use 等待條件若只看 status badge 會誤判。

改善方向：
- 建立資料包後明確更新 `topicPackState` 為「資料包已建立」。
- 測試腳本應改以資料包下拉是否出現新 title 作為主要成功訊號。

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

### 8. 直播時間到自動結束原本缺少 UI 可見的 SC 感謝控制

現象：
- 後端已有 `auto_sc_thanks_on_finalize` 欄位，但 UI 沒有對應 checkbox。

影響：
- 使用者不知道時間到是否會自動進入 SC 感謝環節。
- Browser Use 無法從 UI 設定或驗證該選項。

改善狀態：
- 已新增「結束前自動感謝未處理 SC」checkbox。
- 已補測試：時間到達時會先跑 `closing_super_chat_thanks`，標記未處理 SC，再標記 session ended。

### 9. 長時間 E2E 測試需要可恢復 checkpoint

現象：
- 10 分鐘直播測試中途若遇到 selector、pane、Research Gate 或 LLM timeout，整個測試容易中斷。

影響：
- 中斷後可能留下 running session、半建立 Topic Pack、部分 fact cards、正在執行的 Director job。

改善方向：
- Browser Use 測試腳本應保存 checkpoint：
  - session_id
  - started_at
  - topic_pack_id
  - expected end time
  - last observed event count / SC count
- 加入 resume 流程：若 session 已建立，從當前階段繼續，而不是重頭建立。

### 10. UI 與 API 測試責任需要分層

現象：
- 某些驗證更適合 API，例如刪除 ended session、確認 research_requests、確認 SC handled。
- 某些驗證必須 UI，例如欄位是否可操作、按鈕狀態、Chat Preview 是否更新。

影響：
- 全部用 Browser Use 點 UI 會慢且脆弱。
- 全部用 API 又無法驗證真實操作流程。

改善方向：
- E2E 分成兩層：
  - Browser Use：驗證使用者操作路徑與畫面狀態。
  - API probe：驗證 DB / queue / research / SC closing 的真實結果。
- 最終測試報告應同時列出 UI 訊號與 API 訊號。

### 11. PowerShell 重啟腳本不可使用 `$PID` 作為迴圈變數

現象：
- 測試中需要重啟 8091 Bridge server 套用修正。
- 初版重啟指令使用 `foreach ($pid in $ports)`，但 `$PID` 是 PowerShell 內建唯讀變數。
- 指令沒有乾淨停止舊 process，導致後續短版回歸仍跑在舊程式碼上。

影響：
- 會誤以為修正後仍有同樣 bug。
- Browser Use / API probe 的結果會和本機程式碼不一致。

改善狀態：
- 改用 `$procId` 停止 8091 listener。
- 重啟後額外查詢 listener PID 與 process start time，確認真的載入新 process。

### 12. Duration finalize 期間背景 task 仍可能產生新事件

現象：
- 10 分鐘長測試在進入 closing 後仍出現一筆未處理 SC。
- API 顯示 session 已 ended，但 `super_chat_unhandled=1`。
- 短版回歸在舊 process 中重現：closing 期間 runtime 仍為 `running=true`，auto test event task 可繼續產生留言。

根因：
- `_finalize_for_duration()` 原本在 closing 階段仍讓 `runtime.running=True`。
- auto inject / auto test / director 多個 loop 都可能同時偵測到 duration reached。
- closing super chat thanks 只會標記當下快照中的 SC；closing 期間新產生的 SC 會漏標。

改善狀態：
- `_finalize_for_duration()` 進入 closing 時立刻設 `runtime.running=False`。
- closing 前取消 poll / auto inject / director / auto test 背景 task。
- 補回歸測試：`test_duration_finalize_cancels_background_tasks_before_closing`。
- 修正後短版回歸結果：closing 期間 event_count 不再增加、所有 SC 都有 `handled_in_closing_at`、queue 無 active job。

### 13. Server 重啟會留下 stale running interaction

現象：
- 長測試中途重啟 Bridge 後，舊 process 內正在跑的 interaction 留在 DB，狀態仍是 `running`。
- 後續 autostart 重新注入同一批事件，舊 `running` interaction 變成 stale active job。

影響：
- Queue 會顯示不實的 running job。
- Director / auto inject 可能因 active interaction 判斷錯誤而延遲或跳過動作。

改善狀態：
- `sync_autostart()` 在恢復 running session 前，先將 DB 內未完成 interaction 標記為 `interrupted`，reason=`server_restarted`。
- 補回歸測試：`test_autostart_finalizes_stale_running_interactions_before_resume`。

### 14. Windows cp950 console logging 會中斷 LLM 生成

現象：
- 10 分鐘直播測試重新啟動後，Chat Preview 出現多筆 `生成錯誤: 'cp950' codec can't encode character ...`。
- assistant turn 被錯誤訊息取代，導致直播流程雖然仍在跑，但內容不可用。
- 觸發內容包含簡中、部分 Unicode 符號或角色回覆中的特殊字。

根因：
- `SystemLogger.log_llm_prompt()` / `log_llm_response()` 會把 prompt 與 response 預覽同步 `print()` 到 console。
- 在 Windows 以 `Start-Process -RedirectStandardOutput` 啟動 API server 時，stdout 仍可能使用 cp950 編碼。
- `print()` 發生 `UnicodeEncodeError` 後，例外往上冒泡，直接中斷 PersonaAgent 生成。

影響：
- 日誌輸出不應影響主要 LLM 流程，但實際上變成生成失敗點。
- Browser Use 長測試會得到大量假陰性結果。

改善狀態：
- `SystemLogger` 新增安全 console 輸出，遇到不可編碼字元時以 replacement 方式寫出，JSONL 結構化日誌仍保留 UTF-8 原文。
- 補回歸測試：`test_system_logger_console_output_survives_cp950_stream`。
- 需要重啟 API server 後重新跑乾淨的 10 分鐘直播測試。

### 15. 長時間直播期間 API / Chat Preview 可能被生成與記憶流程拖慢

現象：
- 10 分鐘直播測試後段，Bridge SSE 仍有部分更新，但 `/health`、Chat Preview、interactions probe 開始出現 10 到 30 秒 timeout。
- Bridge log 顯示 director 呼叫 MemoriaCore 時在 `/auth/bypass` 或後續 chat stream 等待到 180 秒 timeout。
- API log 同時間有多輪群組生成、memory pipeline、summary / distill 相關工作。

影響：
- 使用者會看到 Chat Preview `讀取失敗`，但實際後端可能仍在工作。
- Browser Use 長測試容易被 timeout 中斷，無法分辨是 UI 查詢失敗還是直播流程真的停止。

改善方向：
- `/health` 必須保持輕量且不被 LLM / memory pipeline 阻塞。
- Chat Preview 應使用短 timeout、快取上一筆成功結果，並顯示「後端忙碌」而不是只顯示失敗。
- 直播長流程應降低單輪 group turn 上限或把 memory distill 移到背景 queue，避免同一時間阻塞所有控制 API。
- Browser Use E2E 應將 timeout 視為可恢復狀態，先用 DB / log probe 確認 session 是否仍 running，再決定是否重啟。

改善狀態：
- 已先記錄為流程風險；尚未改造 API 併發模型。

### 16. 隱藏外部上下文 fallback 會污染聊天室歷史

現象：
- 10 分鐘直播測試結尾進入 SC 感謝時，歷史訊息曾出現 `<external_chat_context>`、`直播導播 action=closing_super_chat_thanks`、`<topic_pack_fact_cards>` 等內部控制內容。

根因：
- MemoriaCore `/chat/sync` / `/chat/stream-sync` 以 `body.content` 作為最後 fallback 寫入 user message。
- 對 Bridge 來說，`body.content` 是給 LLM / router 的控制 prompt，不一定是人類可見訊息。

影響：
- 聊天畫面會顯示不應公開的導播 prompt、Topic Pack 片段或安全提示。
- 後續 LLM 歷史也會把內部控制內容當成使用者訊息，污染直播內容。

改善狀態：
- `_resolve_chat_display_content()` 已改成：只要有 `external_context`，沒有明確 `display_content` 或 `visible_events` 時也不會回退到 hidden prompt。
- director fallback 只顯示短句，例如「導播推進直播流程。」或「感謝本場 Super Chat。」。
- 補回歸測試：`test_external_context_without_visible_events_never_displays_hidden_prompt`。

### 17. Bridge UI reload 後不會自動載入目前 running / recently ended session

現象：
- 10 分鐘測試中途重新整理 `http://127.0.0.1:8091/ui/` 後，左側表單回到草稿狀態，右側 Chat Preview 顯示「尚未載入」。
- API 同時確認測試 session 仍為 `running`，並未停止。
- 手動從「載入既有 Live Session」選回該 session 後，Chat Preview 可正常顯示。

影響：
- 使用者容易誤判直播已停止或 Chat Preview 壞掉。
- Browser Use E2E 若 reload 頁面，需要額外選回 session，流程不直覺。

改善方向：
- UI reload 時優先自動載入最近 `running` 的 session。
- 若沒有 running session，載入最近 `closing` / `ended` session。
- Chat Preview 區塊應明確顯示「尚未選擇 Live Session」而不是單純尚未載入。

### 18. Closing SC 感謝階段會被 SC backlog / 高優先級 job 延長

現象：
- 10 分鐘測試 session `yt_20260504_120535_05fd56d3` 於約 12:17 進入 `closing`。
- closing 期間仍有 `super_chat` interaction 插隊，導致 `closing_super_chat_thanks` director job 多次被 `higher_priority:super_chat` interrupt。
- 最終於 12:21:31 才 `ended`，實際總流程約 15 分半。

影響：
- 「10 分鐘直播」會因 closing job 被打斷而明顯超時。
- 大量 SC 時結尾可能拖太久，使用者不易判斷是否卡住。

改善方向：
- 進入 `closing` 後，停止一般 auto inject 與 SC batch 插隊。
- closing SC thanks 應一次消化當前所有未處理 SC，並禁止被新的 `super_chat` interaction 打斷。
- closing 應有 timeout / fallback：超時後標記未能逐一回應的 SC 為「分組感謝完成」，避免卡在 running job。

### 19. 取消 / 打斷中的 stream job 可能出現 provider read 例外

現象：
- 10 分鐘測試中一筆 `super_chat` interaction 被 interrupt 後，reason 顯示：`'NoneType' object has no attribute 'read'`。
- 該 interaction 狀態最後是 `interrupted`，沒有造成整場 failed。

影響：
- 狀態雖可恢復，但錯誤訊息暴露 provider stream 取消流程不乾淨。
- 若使用者在 Queue UI 查看，會看到難以理解的低階錯誤。

改善方向：
- stream cancellation 包裝 provider iterator / response close，將這類 read error 正規化為 `interrupted_by_higher_priority`。
- Queue UI 對中斷 reason 顯示人類可讀文字，低階 exception 放 metadata。

### 20. 直播測試互動會觸發 MemoriaCore 個人 memory pipeline

現象：
- 10 分鐘測試期間，API log 顯示 YouTube 觀眾留言與 AI 直播回覆進入 `distill` / `profile` pipeline。
- 例如系統嘗試把「四月新番、傳統美學分析」萃取成使用者長期偏好。

影響：
- YouTube 直播聊天室內容不應直接污染操作者的 private profile / core memory。
- 長時間測試也會因 memory distill / profile extraction 增加 API 負載，進一步造成 Chat Preview timeout。

改善方向：
- Bridge 注入的直播 session 應標記 `memory_write_policy=transient` 或等效設定。
- 直播中即時互動只允許進入 session history，不進個人 private memory pipeline。
- 直播結束後只由 YouTube summary safe memory 流程寫入 shared memory。
- 若需要保留直播事件，存於 YouTubeBridge DB / Topic Pack / summary，不寫入個人 profile。

## 後續建議

優先改善順序：

1. 為 YouTubeBridge UI 加入穩定 `data-testid`。
2. 建立「新測試直播 wizard」，集中設定 Live Session、Director、Topic Pack、Research、Auto Test Events。
3. 將 Research Gate 結果狀態拆成 `completed_with_results`、`completed_no_results`、`failed`。
4. 補一個可 resume 的 Browser Use E2E 腳本。
5. 將 10 分鐘長測試結果輸出成固定格式報告。

### 21. 自動測試 SC 可能把導播方向長文混入可見觀眾留言

現象：
- 2026-05-04 13:35 的 10 分鐘 E2E 測試 session `yt_20260504_133345_f61a444b` 中，自動生成的 SC 內容出現「本場直播主題先聊四月新番，接著轉到 LLM 的應用與限制，最後用美食與觀眾互動收束...」等完整導播方向文字。
- 該內容被當作 YouTube Live 留言注入，並顯示在 Chat Preview 的 `system_event` 中。

影響：
- 導播方向本來應是內部控場提示，至少不應整段偽裝成觀眾 SC。
- 會污染測試結果，讓角色像是在回應內部策略，而不是自然觀眾留言。

改善方向：
- 自動測試留言產生器只能使用「公開直播主題摘要」，不得直接使用完整 `director_guidance`。
- SC 文案模板應限制長度，避免把 topic hint 原文拼進留言。
- 對測試留言生成結果加一層 safety / visibility sanitizer，移除「導播、控制節奏、不要被帶偏」等內部控場語句。

### 22. Topic Pack 自動資料卡建立會把 embedding 向量大量輸出到 UI Log

現象：
- Topic Pack 自動建立資料卡後，Bridge UI Log 出現大量浮點數 embedding 向量。
- 對使用者而言，Log 尾端被向量資料淹沒，難以看到操作成功與失敗訊息。

影響：
- Browser Use E2E 很難透過 Log 快速判斷資料卡是否成功建立。
- 一般使用者也會誤以為系統卡住或輸出異常。

改善方向：
- UI Log 顯示 topic pack / embedding 操作時只保留摘要，例如 `entry_count`、`embedding_count`、`pack_id`。
- 完整 embedding 不應出現在前端 Log；若需要除錯，寫入後端 debug log 或 metadata 摘要。

### 23. Queue 可能同時出現多筆 running interaction，active interaction 指向不一致

現象：
- 同一個測試 session 中，`GET /sessions/{id}/interactions` 回傳 `director` 與 `super_chat` 兩筆 interaction 都是 `running`。
- 同一份回應的 `active` 欄位只指向其中一筆 `super_chat` job。

影響：
- UI Queue 狀態會讓使用者難以判斷目前到底誰在生成。
- 若兩筆真的同時打到 MemoriaCore，可能造成聊天順序錯亂或中斷狀態互相覆蓋。

改善方向：
- Bridge queue 應保證同一 live session 同時間只有一筆 active generation。
- 若 Director 決策在 SC job running 時觸發，應排隊或合併，不應立即標記 running。
- `active` 的來源應與 storage 中 running job 狀態一致，避免 active / list 顯示互相矛盾。

### 24. 高頻自動留言 / SC 壓力下 Chat Preview API 會 timeout

現象：
- 2026-05-04 13:40 左右的 10 分鐘 E2E 測試 session `yt_20260504_133345_f61a444b` 中，`GET /sessions/{id}/chat-preview?limit=10` 在 10 秒內未回應。
- 同時間 Bridge session 與 runtime 仍為 `running`，auto test / auto inject 仍啟動。

影響：
- UI 右側 Chat Preview 可能顯示更新中、無反應或讀取失敗。
- Browser Use E2E 無法穩定透過 Chat Preview 驗證最新訊息。

改善方向：
- Chat Preview 應只讀 session history 的輕量索引，避免觸發重型 pipeline 或等待其他鎖太久。
- API 可加入短 timeout + partial response，至少回傳最新可讀資料與 `stale=true`。
- UI 顯示「讀取中 / 上次更新時間 / 讀取失敗」分離，不要讓使用者誤判直播停止。

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

### 26. Closing SC 感謝 prompt 在 UI Log 中暴露完整 SC 清單與導播方向片段

現象：
- `closing_super_chat_thanks` 完成前後，UI Log 顯示完整 decision prompt，其中包含大量 SC 內容。
- 部分測試 SC 內容又混入完整導播方向文字，使 Log 也暴露內部控場策略。

影響：
- 雖然 Chat Preview 只顯示較簡短的收尾內容，但 Log 仍會讓使用者看到不該公開的 prompt 細節。
- 若之後 Bridge UI 被 OBS 或其他方式截取，Log 區塊可能洩漏內部策略。

改善方向：
- UI Log 對 interaction metadata 做紅線處理：decision prompt 只顯示 `action`、`reason`、`event_count`，不顯示完整 prompt。
- Closing SC 只在後端保存安全化摘要；原始 SC 清單可留 DB 供除錯，不直接 dump 到前端。
- 測試 SC 產生器修正後，closing prompt 的 SC 清單也會自然降低導播方向外洩風險。
