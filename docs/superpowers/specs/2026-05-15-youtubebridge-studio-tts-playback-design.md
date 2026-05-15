# YouTubeBridge Studio TTS Playback Design

## 目標

將目前直播系統的 TTS 播放主控面移到 `/studio/`。Studio 必須負責接收 presentation queue item、播放 GPT-SoVITS 音訊、在音訊播放完成後 ACK，並在 ACK 後才允許下一句對話進入播放流程。

本次設計沿用既有 YouTubeBridge Live Presentation Queue 後端：後端已負責將 AI 回覆拆句、合成 TTS、提前準備下一句、等待前端 ACK、處理 ACK timeout，以及在高優先度留言或 Super Chat 注入時走既有 interrupt 流程。這次不重寫後端 queue 核心，只把 Studio 接成新的唯一 playback/ACK 操作面。

## 背景與現況

- `codex/youtube-live-presentation-tts` 已把 Live Presentation/TTS 後端合入主線。
- `YouTubeBridge/bridge_engine.py` 已有 `presentation_enabled`、`tts_enabled`、`presentation_item_ready`、音訊檔輸出、ACK、skip current、timeout 與下一句 prefetch。
- 既有 `/live-chat/` 已有播放與 ACK 的瀏覽器邏輯，但之後會改為 legacy。
- 新版 `/studio/` 已有「Live Presentation Queue」與「GPT-SoVITS TTS」系統設定，但 `studioLiveSessionPayload()` 目前仍把 `presentation_enabled` 與 `tts_enabled` 固定為 `false`，因此 Studio 不會啟用 TTS runtime。
- Studio 已經訂閱 session SSE，但目前收到 `presentation_item_ready` 時只刷新對話，沒有播放音訊、ACK、skip 或 interrupt handling。

## 決策

採用「Studio 內建 presentation player」方案。

Studio 成為唯一 TTS playback/ACK 面。`/live-chat/` 保留既有功能但視為 legacy，不作為本次目標，也不再要求與 Studio 同時控制同一場直播。若兩個頁面同時開啟，實務上應以 Studio 為準，避免雙方都播放同一個 presentation item 並造成 ACK race。

不抽共用 JS module。抽共用 player 會牽動 legacy `/live-chat/`，超出本次目的。若之後要正式移除或降級 `/live-chat/`，再獨立整理。

## 使用者流程

1. 操作者在 `/studio/` 的「輸出管線」啟用 Live Presentation Queue 與 GPT-SoVITS TTS。
2. 操作者在角色設定中替每個直播角色保存 TTS profile。
3. 按「開始直播」時，Studio 將系統設定映射到 `POST /sessions/current/start`：
   - `presentation_enabled` 取自 `presentationQueueEnabled`
   - `tts_enabled` 取自 `ttsEnabled`
   - `tts_provider` 固定為 `gpt_sovits`
4. 開播後 Studio 訂閱 `EventSource("/sessions/{session_id}/events")`。
5. 收到 `presentation_item_ready` 時，Studio 將 item 放入本地播放佇列。
6. 若目前沒有正在播放的句子，Studio 立即播放隊首 item：
   - 先把該句 append 到中央對話區，讓字幕與對話 UI 同步。
   - 若 item 有 `audio_url`，建立 `Audio` 並播放。
   - 若 item 沒有 `audio_url`，視為文字 fallback，立即 ACK 並播放下一句。
7. 音訊 `ended` 或不可恢復的 `error` 後，Studio 呼叫 `POST /sessions/{session_id}/presentation/{item_id}/ack`。
8. ACK 完成後，Studio 才播放下一個本地 queued item。

## 下一句快取與延遲控制

後端已經在 presentation mode 中提前準備下一句：

- 單一 AI 回覆拆成多句時，第一句送出前會先開始合成下一句。
- 導播模式中，opening / planned turn / speculative next role 已有 prefetch 測試保護。
- Studio 不自行呼叫 TTS 合成 API，也不在前端猜測下一句內容。它只播放後端已準備好的 `presentation_item_ready`。

Studio 的責任是不要在目前音訊結束前 ACK。只要不提前 ACK，後端就會維持「一句播完才允許下一句」的節奏；同時，因為後端已 prefetch，下一個 item 通常會在目前句子播放期間就準備完成，減少兩句之間的空白。

## 打斷與留言插入

留言插入與 Super Chat 打斷維持後端既有優先度流程：

- 一般留言注入若已有 active interaction，後端會等待或排隊。
- 高優先度 Super Chat 或手動高優先度注入會呼叫 `interrupt_session()`，讓目前 interaction 轉為 `interrupt_requested`。
- 後端會透過 SSE 廣播 `interrupt_requested` / `interaction_interrupted`，後續再送新的 presentation item。

Studio 收到 `interrupt_requested` 時必須執行播放端中斷：

- 停止目前 `Audio`，清掉 `src`，釋放本地 current audio。
- 清空本地尚未播放的 `presentationQueue`，避免已快取的舊句子在打斷後繼續播。
- 呼叫 `POST /sessions/{session_id}/presentation/current/skip`，讓後端目前 presenting item 解除等待。
- 在中央對話區寫入一筆非角色狀態提示或 log，讓操作者知道播放已被直播互動打斷。
- 等待後端送新的 `presentation_item_ready`；不要由前端自行生成打斷台詞。

如果 interrupt 發生在沒有 current item 的時候，Studio 只清空本地 queue 並記錄 log，不需呼叫 skip。

## Studio 播放狀態

Studio 前端新增以下狀態：

- `presentationQueue`: 尚未播放的 presentation item。
- `presentationPlaying`: 是否正在播放或等待目前 item ACK。
- `currentPresentationItem`: 目前播放中的 item。
- `currentAudio`: 目前的 `Audio` 物件。
- `audioUnlockRequired`: 瀏覽器阻擋 autoplay 時標記需要操作者啟用聲音。

播放狀態必須跟 session lifecycle 綁定：

- 新 session 開始前呼叫 `resetConversationForNewSession()` 時，也要清掉 presentation player state。
- session 停止或 finalize 後停止目前音訊並清空 queue。
- SSE 斷線時不自動 ACK；等待重新連線或 fallback refresh。

## Autoplay 與操作控制

瀏覽器可能阻擋自動播放音訊。Studio 應提供操作端控制，不把問題丟到 `/live-chat/`：

- 當 `audio.play()` 被拒絕時，保留目前 item，不 ACK，顯示「啟用聲音」按鈕或狀態。
- 操作者按下啟用聲音後，重新播放目前 item。
- 提供「跳過目前句子」控制，呼叫 `presentation/current/skip`，停止目前音訊，解除後端等待並播放下一句。
- 跳過只影響目前 presenting item，不清空全部 queue；interrupt 才清空本地 queue。

## UI 邊界

Studio 的中央對話區仍維持目前設計，不新增獨立播放器大面板。只新增必要狀態與控制：

- 目前播放狀態：待機、準備中、播放中、等待啟用聲音、已打斷。
- 「啟用聲音」按鈕。
- 「跳過目前句子」按鈕。
- Debug log 寫入 presentation item ready、播放開始、ACK、skip、interrupt。

不在 Studio 顯示 raw prompt、raw context、TTS provider raw payload 或後端檔案路徑。`audio_url` 可用於播放，但 UI 不顯示完整本機路徑。

## API 與資料流

沿用既有 API：

- `POST /sessions/current/start`
- `GET /sessions/{session_id}/events`
- `POST /sessions/{session_id}/presentation/{item_id}/ack`
- `POST /sessions/{session_id}/presentation/current/skip`
- `GET /sessions/{session_id}/presentation/{item_id}/audio`
- `GET /sessions/{session_id}/chat-preview`

本階段不新增 backend route。若測試發現 Studio 需要更多 presentation status，優先從既有 SSE payload 與 chat preview 解決；不要先擴 API。

## 錯誤處理

- TTS 合成失敗：後端送出的 item 沒有 `audio_url` 且 status 可能為 `failed`。Studio 顯示文字並 ACK，避免直播卡住。
- 音訊載入失敗：Studio 記錄 log，ACK 該 item，繼續下一句。
- ACK 失敗：Studio 記錄警告並刷新 session；不要立刻播放下一句，避免前端節奏超過後端狀態。
- Skip 失敗：Studio 記錄警告並刷新 session；若 current audio 已停止，保持 player idle，等待後端狀態。
- SSE 斷線：保持現有 reconnect/fallback 行為，但不要對未知 item 自行 ACK。

## 測試策略

實作必須先補紅測，再改 production code。

### Source tests

更新 `YouTubeBridge/tests/test_studio_ui.py`：

- `studioLiveSessionPayload()` 不再包含 `presentation_enabled: false` / `tts_enabled: false`，而是映射 `liveDefaults.presentation_queue_enabled` 與 `liveDefaults.tts_enabled`。
- Studio JS 包含 presentation player functions，例如 `enqueuePresentationItem`、`playPresentationItem`、`ackPresentationItem`、`skipCurrentPresentation`、`handlePresentationInterrupt`。
- Studio SSE handler 對 `presentation_item_ready` 會 enqueue，不只是 refresh conversation。
- Studio SSE handler 對 `interrupt_requested` 會呼叫 interrupt handler。
- Studio reset/stop path 會清理 presentation player state。

### Backend regression

既有後端測試應維持：

- `YouTubeBridge/tests/test_presentation_queue.py`
- `YouTubeBridge/tests/test_bridge_engine_injection.py::test_inject_recent_uses_presentation_queue_when_enabled`
- `YouTubeBridge/tests/test_bridge_engine_injection.py::test_auto_inject_waits_while_presentation_is_active`
- 相關 director prefetch tests。

### Verification commands

建議先跑 targeted：

```powershell
python -m pytest YouTubeBridge/tests/test_studio_ui.py YouTubeBridge/tests/test_presentation_queue.py YouTubeBridge/tests/test_bridge_engine_injection.py -q
```

再跑 YouTubeBridge 全套：

```powershell
python -m pytest YouTubeBridge/tests -q
git diff --check
```

若 `.pyTestTemp` 發生 Windows ACL / PermissionError，依 repo 規則先跑：

```powershell
scripts\cleanup_pytest_temp.bat
```

## Browser QA

若實作後需要驗證 UI，使用可見前景視窗啟動 8091，不用 hidden/background：

```powershell
Start-Process -FilePath "cmd.exe" -ArgumentList "/k", "G:\ClaudeProject\MemoriaCore\YouTubeBridge\start.bat" -WorkingDirectory "G:\ClaudeProject\MemoriaCore\YouTubeBridge" -WindowStyle Normal
```

QA 路徑：

1. 開啟 `http://127.0.0.1:8091/studio/`。
2. 啟用 Live Presentation Queue 與 GPT-SoVITS TTS。
3. 選擇已配置 TTS profile 的角色與 LiveEpisodePlan。
4. 開始直播。
5. 確認 Studio 中央對話逐句顯示，音訊播完後才顯示下一句。
6. 確認下一句不出現明顯等待 TTS 合成的空白。
7. 送入高優先度留言或 Super Chat，確認目前音訊停止、舊 queue 被清掉，後續播放插入內容。
8. 測試「啟用聲音」與「跳過目前句子」。

## 明確不做

- 不把 `/live-chat/` 改成新的主畫面。
- 不重寫後端 presentation queue。
- 不在前端直接呼叫 GPT-SoVITS。
- 不新增第二套 TTS storage schema。
- 不把 raw prompt、raw context 或 provider raw payload 顯示到 Studio。
- 不把 legacy `/ui/` 的整套 session-control import 到 Studio。

## 風險與控制

- **雙頁面同播風險**：Studio 是唯一主控；live-chat 暫時保留但不建議同場同時開啟。之後 legacy 化時再處理入口提示或禁用播放。
- **autoplay 阻擋**：Studio 必須保留 current item 並要求使用者啟用聲音，不可提前 ACK。
- **interrupt race**：interrupt handler 要先停止本地音訊與清 queue，再呼叫 skip；若 skip 失敗，刷新 session 並等後端狀態。
- **ACK race**：每個 item 只能 ACK 一次；前端需以 `currentPresentationItem` 與 item id 檢查避免重複 ACK。
- **延遲回歸**：不要把 prefetch 移到前端；保持後端先合成下一句，前端只照 item ready 事件播放。
