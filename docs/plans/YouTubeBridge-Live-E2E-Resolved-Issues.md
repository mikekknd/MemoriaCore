# YouTubeBridge Live E2E 已完成問題歸檔

建立日期：2026-05-04
最後整理：2026-05-06

## 目的

這份文件保存從 `YouTubeBridge-Live-E2E-Workflow-Issues.md` 移出的已完成修正、已驗證結果與歷史問題紀錄。
目前仍需追蹤的工作請回到 `docs/plans/YouTubeBridge-Live-E2E-Workflow-Issues.md`。

## 已完成 / 已歸檔項目
### 4. Director idle 欄位 id 與 Browser smoke selector 已穩定

現象：
- 測試腳本曾嘗試填 `#directorIdleSeconds`，實際 UI id 是 `#directorIdle`。
- UI 補上 `data-testid="director-idle-seconds"` 後，又被 `installTestIds()` 覆寫成舊 id 導致 Browser smoke 找不到穩定 selector。

影響：
- Browser Use 腳本依賴 id 時容易脆弱，且欄位位置改到 Live Session panel 後更需要穩定 selector。

改善狀態：
- `installTestIds()` 已改為保留 HTML 內顯式宣告的 `data-testid`。
- 已補回歸測試 `test_install_test_ids_preserves_explicit_stable_testids`。
- 2026-05-06 Browser smoke 已確認 `[data-testid="director-idle-seconds"]` 存在並可被 in-app browser 找到。

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

### 8. 直播時間到自動結束原本缺少 UI 可見的 SC 感謝控制

現象：
- 後端已有 `auto_sc_thanks_on_finalize` 欄位，但 UI 沒有對應 checkbox。

影響：
- 使用者不知道時間到是否會自動進入 SC 感謝環節。
- Browser Use 無法從 UI 設定或驗證該選項。

改善狀態：
- 已新增「結束前自動感謝未處理 SC」checkbox。
- 已補測試：時間到達時會先跑 `closing_super_chat_thanks`，標記未處理 SC，再標記 session ended。

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

### 27. Closing 會標記所有 SC 已處理，但感謝回覆 job 未完成

現象：
- 2026-05-04 16:22 的 10 分鐘 E2E 測試 session `yt_20260504_162209_75cf1025` 正常在 10 分鐘進入 `closing`，約 11 分鐘後轉為 `ended`。
- 本場共 35 則 SC，`handled_in_closing_at` 全部被標記，`GET /super-chats?unhandled_only=true` 回傳 0。
- 但 queue 中所有 `director` job 都是 `interrupted`，包含 `closing_super_chat_thanks` 相關 job；沒有完成的 director closing thanks interaction。
- 最終 queue 統計為 `super_chat:completed = 18`、`director:interrupted = 18`、`running = 0`、`failed = 0`。

影響：
- 系統狀態會顯示 SC 已在 closing 處理完成，但聊天室不一定真的產生「感謝 SC」的結尾回覆。
- 這會讓後續重跑 finalize 時無法再次處理那些 SC，因為它們已被標記為 handled。
- 使用者看到 `ended` 會以為收尾完整，但實際缺少可驗證的感謝段落。

改善方向：
- `run_closing_super_chat_thanks()` 應只在 closing interaction `completed` 後才標記 `handled_in_closing_at`。
- 若 closing thanks job 被 `claim_timeout_active_generation`、`live_session_ended` 或其他 interrupt 中斷，不應標記 SC handled；應保留 unhandled 或標記為 `closing_attempt_failed`。
- Closing 階段應暫停一般 SC 插隊與一般 director enqueue，保證 `closing_super_chat_thanks` 具有最高收尾優先級。
- 若 provider 逾時，需要產生 fallback 分組感謝文字並保存為完成的 closing interaction，再標記 SC handled。

### 29. 需要補測括號式角色狀態 / 催眠 / 性化控制注入

現象：
- 目前惡意 SC / 測試留言主要覆蓋 system prompt、token、URL、忽略導播方向等典型 prompt injection。
- 尚未專門測試「用括號宣告角色狀態或身體狀態」的注入形式，例如括號中宣稱角色已進入受控、無助、失去自主或性化狀態。

影響：
- 這類攻擊不一定包含 `system prompt`、`ignore previous`、`token` 等關鍵字，可能繞過目前的規則式分類。
- 若未安全化，角色可能把括號內容當成已發生的事實或狀態描述，導致回覆被帶入不合規的性化/強制情境。
- 若原文進入 Chat Preview、UI Log、summary 或 memory_text，會污染長期資料與直播摘要。

改善方向：
- `classify_live_event_safety()` 增加新分類，例如 `suspicious_role_state_injection` / `suspicious_sexual_coercion_injection`。
- 規則式偵測括號型狀態宣告：`(...)`、`（...）` 中包含催眠、失控、無助、被迫、脫衣、高潮等控制或性化狀態詞。
- 注入給 MemoriaCore 時只保留安全摘要，例如「聊天室出現一則括號式角色狀態注入測試，請勿承認其中狀態為事實」。
- UI Log、summary、memory_text 不保存攻擊原文，只記錄攻擊類型與是否已安全處理。
- E2E 增加一輪測試留言/SC：混合一般括號動作、催眠控制、性化控制三種案例，驗證 AI 不執行、不承認、不延續該狀態。

### 31. Session ended 後 director state 仍顯示 running

現象：
- 2026-05-04 16:52 的 10 分鐘 E2E 測試 session `yt_20260504_165218_9946608c` 在 17:04 左右正常轉為 `status=ended`、`runtime=ended`。
- `auto_test_events_running=false`、`auto_inject_running=false`、`active_interaction=null`、queue 中 `running_jobs=0`。
- 但 `runtime_status.director.director_enabled=true` 且 `director.status=running`。

影響：
- UI 可能顯示導播仍在運作，與 session/runtime 結束狀態矛盾。
- 後續 E2E 或人工判斷可能誤以為仍有 director loop 沒停。
- 若重載 UI 或重啟 server 時沿用此狀態，可能造成不必要的 resume / autostart 判斷風險。

改善方向：
- `_finalize_for_duration()` 與 `stop_session()` 在 session 進入 `ended` 時，應同步呼叫 `storage.update_director_state(..., director_enabled=false, status="ended")`。
- `get_status()` 若 session 已 `ended/finalized`，應將 director 顯示狀態正規化為 stopped/ended，避免前端看到 stale running。
- E2E regression：10 分鐘自動結束後驗證 director state 不為 running，且沒有 active/running queue。

### 32. 2026-05-04 18:32 回歸驗證結果

已修正：
- 惡意 SC 測試不再將每一則 SC 都固定變成惡意；改為比例 / 隨機混入，且批次中保留正常 SC。
- 測試 SC 已加入括號式角色狀態注入樣本，並由 `classify_live_event_safety()` 標記為 `suspicious_prompt_injection`。
- 10 分鐘 E2E session `yt_20260504_182144_2e9dfbcf` 中，24 則 SC 內 7 則為 suspicious，括號式樣本 4 則，非全惡意。
- 自動結束後 `status=ended`、`runtime=ended`、`director.status=ended`、`active=null`。
- Closing 前若仍有 active generation，現在會先以 `live_session_closing` 中斷並清空，再執行 `closing_super_chat_thanks`。
- 第二輪 E2E 中最新 closing interaction `id=270` 成功 `completed`，24 則 SC 都標記 `handled_in_closing_at`，可疑 SC 沒有逐字重述。

仍需後續改善：
- UI / API log 仍可能在 metadata 內保存完整 closing prompt 與安全化後的 SC 清單；目前不會顯示在 Chat Preview，但後續仍應縮短 log payload。
- 自動測試留言仍可能產生 fact card 外的未驗證作品或技法，需接續第 30 項 factuality gate。

### 34. Duration closing 前最後一批測試留言可能留下 pending safety

現象：
- 2026-05-04 21:11 的 10 分鐘 E2E session `yt_20260504_211156_8c77d8d9` 正常 ended，SC 也全部標記已處理。
- 但 auto test task 在接近結束時間又產生最後一批留言，closing 取消背景 task 後直接進 SC 感謝，導致 ended 後仍有 4 則 `safety_status=pending`。
- Chat Preview 沒有暴露攻擊原文，但 pending 狀態會影響後續 summary / audit 判讀。

修正：
- `_finalize_for_duration()` 在 `closing_super_chat_thanks` 前新增 pending safety resolution。
- 若 SafetyLLM 正常，先分類最後一批事件；若逾時或失敗，剩餘 pending 事件會 fail-closed 成「安全檢查未完成，暫不顯示原始留言」。
- ended 時同步關閉 `auto_inject` 與 `auto_test_events_enabled`，避免 UI 顯示結束 session 仍像可自動運作。
- 2026-05-04 21:33 重跑 10 分鐘 E2E session `yt_20260504_213317_6babd91e` 後，ended 狀態為 `completed=74`、pending=0、failed=0。

### 35. Chat Preview API 仍回傳 MemoriaCore debug_info 與內部 prompt

現象：
- `/sessions/{session_id}/chat-preview` 原本直接回傳 MemoriaCore `get_session_history` 的 messages。
- response 中包含 `debug_info.dynamic_prompt`、`original_query`、retrieved memory、persona prompt、LLM trace metadata 等大量內部資訊。
- Live Chat 畫面不顯示這些欄位，但 API payload 本身過大且暴露內部 prompt，不符合直播入口的資料邊界。

修正：
- `chat-preview` 現在只保留直播頁需要的欄位：`message_id`、`role`、`content`、`created_at/timestamp`、`character_id`、`character_name`。
- session 物件也只保留 live chat 狀態需要的公開欄位。
- 2026-05-04 21:47 驗證 payload 從大量 debug 內容縮到約 14KB，掃描未命中 `debug_info`、`dynamic_prompt`、`original_query`、hidden context、Topic Pack 標記或攻擊原文。

### 36. `/live/` wrapper 需要在 Chat Preview payload 縮小後驗證 iframe 顯示

現象：
- 中段 Browser Use reload `/live/` 時左右 iframe 曾短暫顯示空白框；直接開 `/live-chat/` 初始也顯示 0 則。
- 同時間 API 其實已有訊息，推測主要受到舊版 `chat-preview` payload 過大與未 sanitization 影響。

修正 / 結果：
- 套用 chat-preview sanitizer 並重啟 YouTubeBridge 後，直接 `/live-chat/?session_id=...` 顯示 `37/37` 則。
- `/live/?session_id=...` wrapper 左側 live chat 與右側控制台都能正常載入；Browser Use screenshot 已確認左右 split 可用。

### 37. Interactions API 仍回傳完整導播 prompt 與 SC 清單

現象：
- `GET /sessions/{session_id}/interactions` 原本直接回傳 `live_interactions.metadata_json`。
- `closing_super_chat_thanks` 的 `metadata.decision.prompt` 仍包含完整 SC 清單、導播方向片段與安全化後的可疑 SC 條目。
- 前端 Log 已有 sanitizer，但直接打 API 或 Queue 面板仍可能拿到過多內部資料。

修正：
- `YouTubeBridge/server.py` 新增 `_sanitize_interaction()`，在 interactions API 回傳前移除 / 摘要化 metadata。
- `metadata.decision` 只保留 `action`、`reason`、`current_topic`。
- `summary.event_ids`、`events`、`super_chats` 等清單只回傳 count；embedding 只回傳維度摘要，不回傳向量。
- `content/reply_text/closure_text` 若包含 hidden external context 或 Topic Pack 標記，改成 `[hidden context]`。
- 2026-05-04 22:35 以舊 session `yt_20260504_213317_6babd91e` 驗證，interactions API 未再命中 `完整 SC 清單`、`system prompt`、`催眠`、`脫光`、`高潮`、hidden context 或 debug prompt。

### 38. Test Events API 的 metadata.topic_hint 會暴露惡意測試樣本

現象：
- 重新測試時，手動呼叫 `POST /sessions/{id}/test-events/generate` 並在 `topic_hint` 放入括號式攻擊樣本。
- 回傳 event 的 `message_text` 已 fail-closed 隱藏，但 `metadata.topic_hint` 仍保留完整攻擊樣本。
- 這會進入 Recent Events / UI Log，造成「原文不在 message_text，但仍透過 metadata 外洩」的資料邊界漏洞。

修正：
- `YouTubeBridgeManager._public_event()` 現在會對 event metadata 做公開版 sanitizer。
- `topic_hint`、`director_guidance`、`prompt`、hidden/external context 一律回傳 `[hidden]`。
- `events`、`event_ids`、`super_chats` 等清單只回傳 count。
- 已補 regression：pending safety event 即使原始留言與 topic_hint 包含括號式攻擊，也不會在 `_public_event()` 輸出中出現攻擊原文。

### 39. Runtime status / Director state 仍可回傳完整 director decision prompt

現象：
- 停止受污染測試 session 時，`stop_session()` 回傳的 runtime status 內含 raw director metadata。
- `metadata.opening_decision.prompt` 與 `metadata.last_decision.prompt` 仍包含「不要提到內部導播、queue、prompt 或系統」等完整導播指令。
- 即使 interactions API 已 sanitize，`manager.get_status()`、`/sessions` 的 `runtime_status`、`/sessions/{id}` 的 `runtime_status` 仍有可能透過 director state 暴露內部 prompt。

修正：
- `YouTubeBridgeManager.get_status()` 現在使用公開版 director state。
- director metadata 中的 `opening_decision` / `last_decision` 只保留 `action`、`reason`、`current_topic`。
- active interaction status 也改用公開版 metadata，避免 `summary.event_ids` 或 decision prompt 直接外露。
- 已補 regression：`get_status()` 不再輸出 `prompt` 或 `完整 SC 清單`。

### 40. Closing timeout 後 SC 被標記已處理但沒有完成的收尾訊息

現象：
- 2026-05-04 22:52 的 10 分鐘 E2E session `yt_20260504_225204_239b239e` 正常 ended，且 `unhandled_sc=0`。
- 但 `closing_super_chat_thanks` interaction 被 `live_session_ended` 中斷，director metadata 顯示 `completed_by_timeout`。
- 結果是 SC 已標記 handled，但 live chat 沒有可見的 completed closing thanks 訊息，後續人工檢查會看到「已處理但沒有收尾輸出」。

修正：
- `_finalize_for_duration()` 的 closing timeout 現在會走 fallback completion。
- fallback 會建立 completed `closing_super_chat_thanks` interaction、標記 SC handled，並透過 MemoriaCore `/session/{session_id}/system-event` 寫入一段短收尾。
- MemoriaCore 新增 `POST /session/{session_id}/system-event`，Bridge `MemoriaClient.add_system_event()` 會使用該端點。
- 已補 regression：timeout 仍會留下 completed interaction、system_event 與 `closing_super_chat_thanks_completed` SSE。

### 41. 可疑留言與 director 字眼仍可能出現在直播可見流程

現象：
- Recent Events / SSE 原本會先顯示 pending 或 suspicious event，因此括號式攻擊、安全摘要、或「安全檢查未完成」仍會出現在直播事件面板。
- `youtube_live_director` external context 仍帶有「直播導播 action」與完整 director prompt label，角色有機會照抄「導播提醒」或「可疑留言已安全處理」之類的內部流程語。
- 測試留言缺少 emoji / 100 洗版類樣本，無法覆蓋直播聊天室常見灌水型態。

修正：
- 公開直播事件改為只顯示 `safety_status=completed` 且 `safety_label=clean` 的留言；pending / suspicious / failed 不進 Recent Events 或 `/recent` response。
- suspicious / failed event 在注入流程中會被標記已處理，避免反覆重試；不再放入角色可見的 `visible_events` 或 `context_text`。
- director external context 改成「直播流程 / 直播節奏」語彙，不再送「導播」字樣給角色端。
- closing SC 只列出 clean SC 的公開參考內容；不適合公開回覆的 SC 只概括略過，不要求角色說「已安全處理」。
- fallback 測試留言加入 emoji / `100 100` 洗版樣本，後續 E2E 需覆蓋 SafetyLLM 對 emoji spam 的分類與顯示行為。

### 42. AI 對話停止後 director idle 沒有繼續推進話題

現象：
- 2026-05-04 23:40 的 10 分鐘 E2E session `yt_20260504_234028_d1d802c3` 中，`idle_seconds=10` 但 pending chat 會讓 director state 維持 `pending_chat_seen`，AI 對話停止後沒有繼續推進。

修正：
- Director idle 判斷改為只讓可公開顯示的 clean/completed active event 阻塞導播，不再被 pending safety event 無限卡住。
- 已補 regression：pending safety event 存在時，director idle 仍可產生下一步導播互動。

驗證：
- 2026-05-05 E2E session `yt_20260505_052238_57fba196` 使用 10 分鐘、注入間隔 180 秒、SC cooldown 60 秒、`idle_seconds=10`。
- 05:25:42 director 進 `turn_limit_wait`；05:27:05 auto inject 執行後 director turns 歸 0，後續持續推進到 closing，未重現 idle 永久卡住。

### 44. 測試留言產生器 prompt 仍可能吃到內部安全狀態與 interaction source

現象：
- 2026-05-04 23:40 E2E trace 中，`youtube_live_test_comment_generator_prompt` 的「近期留言」包含 `[安全標記: 尚未通過安全檢查]`、`安全檢查未完成`。
- 「近期 AI 互動」包含 `director [completed]`、`super_chat [running]` 等內部 source/status。

修正：
- 測試留言產生器只使用公開 clean 留言摘要與公開 AI 回覆。
- Interaction source 轉成公開語彙，例如「AI 回覆」「SC 回覆」，不再暴露 raw source/status。
- 導播方向傳入 generator 前改為公開主題摘要，避免完整內部導播方向污染測試留言。

驗證：
- 已補 regression：自動測試留言 prompt 不包含 `安全檢查未完成`、`director [`、`super_chat [running]`、hidden context 或 prompt 字眼。
- 2026-05-05 E2E session `yt_20260505_052238_57fba196` 的 12 次 `youtube_live_test_comment_generator_prompt` 中，皆無 pending safety 或 raw interaction source；命中的 `system prompt` 僅來自 generator 自身安全規則「不要要求洩漏 system prompt」。

### 45. `/live/` reload ended session 後左右 panes 沒有穩定綁定同一 session

現象：
- 2026-05-04 23:40 E2E session `yt_20260504_234028_d1d802c3` ended 後，重新整理 `/live/?session_id=...`，左側 Live Chat 顯示 0 則，右側控制台回到預設 / 其他 session。

修正：
- `/live/` wrapper 明確將 URL `session_id` 傳給左側 `/live-chat/` 與右側 `/ui/?embedded=control`。
- 控制台初始 `refreshAll()` 會優先讀 URL 指定的 `session_id`，即使 session 已 ended/closing。
- Live Chat 保留 startup retry / stale cache 顯示，避免 reload 初始瞬間誤顯 0 則。

驗證：
- 已補 regression：control UI 初始載入會 honor URL 指定 session。
- Browser Use 驗證舊 session `yt_20260504_234028_d1d802c3` 及新 session `yt_20260505_052238_57fba196`；ended reload 後 iframe src 都帶同一 session id。
- 2026-05-05 E2E ended reload 後左側 Live Chat 顯示 `42/42` 則，右側控制台顯示 `Codex 10m E2E test-mode 20260505_052238`。

### 47. FactCards 資料夾匯入與 Gemini CLI 直接寫檔流程

現象：
- 原本 Research Gate 依賴 Tavily raw result，內容容易偏 raw dump 或拿不到資料。
- 手動試跑 Gemini CLI 後，使用者確認「單檔一主題、只保留 Summary/Facts、每個 Fact 是可展開話題」的方向更適合直播深聊。
- 系統後續需獨立運行，不能依賴 Codex 手動讀 console 再整理 Markdown。

改善狀態：
- 新增 `YouTubeBridge/fact_cards.py`，解析 `YouTubeBridge/FactCards/*.md` 的 `## Summary` / `## Facts`，每個 `###` 話題匯入成一筆 Topic Pack entry。
- 新增 `/sessions/{session_id}/fact-cards/import-folder`，可把 FactCards 資料夾匯入並建立 embedding。
- 新增 `/sessions/{session_id}/fact-cards/generate`，由 Bridge 呼叫本機 `gemini --skip-trust --approval-mode auto_edit --prompt ...`，要求 Gemini 直接建立指定 Markdown 檔；server 端會驗證檔案存在且可解析後才匯入。
- 控制台 Topic Pack pane 新增「匯入 FactCards 資料夾」與「Gemini 產生並匯入」按鈕，自動資料卡預設話題固定在動畫新番最新話細節、作畫與劇情討論。
- 補回歸測試：`test_parse_fact_card_markdown_keeps_only_summary_and_facts`、`test_import_fact_cards_folder_creates_linked_topic_pack_entries_and_embeddings`、`test_control_ui_exposes_fact_cards_folder_import_for_anime_topic_flow`。

### 50. 2026-05-05 09:45 E2E 驗證：8088 health timeout 未復現

現象：
- issue 43 曾追蹤 8088 listener 存在但 health timeout / closing SC thanks 期間卡住的問題。

驗證結果：
- 10 分鐘動畫新番 E2E session `yt_20260505_094546_05bf64b0` 中，8088 / 8091 health 全程正常，最後 health 仍為 200。
- `runtime/api_8088.err.log` 長度未增加，測試期間未新增 `Accept failed on a socket`、`WinError 10054`、`WinError 64` 或新的 proactor callback 例外。
- session 正常 `ended`，runtime stopped，director `ended`，active interaction 為 0。

狀態：
- 先歸檔為本輪通過；若後續長測再次出現 listener 存在但 health timeout，重新開 issue 並優先查單 worker request 卡死或長 LLM/IO。

### 51. Director decision prompt 污染已修正

現象：
- issue 46 中，`youtube_live_director_decision_prompt` 曾吃到 `安全檢查未完成`、`director [completed]`、`super_chat [running]` 等內部狀態。

修正：
- Director decision prompt 改用公開 formatter；pending / suspicious / failed event 不進近期留言原文區。
- Recent interactions 轉成公開語彙，例如「AI 回覆」「SC 回覆」，不暴露 raw source/status。

驗證：
- 2026-05-05 09:45 E2E trace 中 13 筆 `youtube_live_director_decision_prompt` 均未命中 `安全檢查未完成`、`director [`、`super_chat [running]`、Topic Pack raw 或攻擊原文。
- 補 regression：director decision prompt 不包含內部安全狀態或 raw interaction source。

### 52. 角色群聊一輪停止問題已修正並驗證

現象：
- issue 49 中，角色常用問句丟回觀眾，導致沒有觀眾回覆時兩位角色各說一次就停止。

修正：
- 非回留言的 director prompt 要求角色彼此接話、補充、反駁或提出下一個切入點。
- `youtube_live_director` external context 明確標示直播自主推進，不保證有觀眾回覆。
- `group_followup_user` 增加直播例外：上一位 AI 用問句結尾時，仍可由另一位角色接話，不強制交還觀眾。

驗證：
- 2026-05-05 09:45 E2E 中已看到 director / 角色多輪推進與 idle 續話題，未再固定停在兩人各一次。

### 53. Closing safety resolution 與 closing SC thanks 本輪驗證通過

現象：
- closing 前最後一批 pending safety 與 closing SC thanks 曾造成結尾不完整或 SC 已處理但沒有可見收尾。

修正：
- closing safety resolution 改成小批次處理，預設每批 10 筆，per-batch timeout 75 秒。
- closing fallback completion 會建立 completed `closing_super_chat_thanks` interaction，必要時寫入短 system event。

驗證：
- 2026-05-05 09:45 E2E ended 前 pending safety resolution 結果：`initial_pending_count=10`、`classified_count=10`、`failed_count=0`、`fallback_count=0`、`batch_count=1`。
- closing SC thanks completed，28 則 SC 全部 marked handled。

### 54. Topic Pack search timeout 與 Gemini FactCard fallback 強化

現象：
- Topic Pack search 可能在 embedding / storage lock 上卡住，影響 8091 health。
- Gemini CLI 有時不直接建立指定 md 檔，或在 Windows console 出現非 UTF-8 / cp950 解碼問題。

修正：
- `/sessions/{session_id}/topic-packs/search` 搬到 `asyncio.to_thread` 並加 30 秒 timeout；embedding 使用短 timeout client。
- Gemini direct-file-output 流程加入 `--include-directories`、指定 FactCards 工作目錄、錯檔名救回、stdout fallback，以及 `encoding="utf-8", errors="replace"`。

驗證：
- 2026-05-05 09:45 E2E 中 8091 health 未因 Topic Pack search 卡住。
- FactCards generate/import regression 已覆蓋直接寫檔、錯檔名救回與 stdout fallback。
