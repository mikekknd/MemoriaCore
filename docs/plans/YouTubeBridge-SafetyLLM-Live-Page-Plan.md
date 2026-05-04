# YouTubeBridge SafetyLLM 與 Live Page 落地計畫

## 目標

把 YouTubeBridge 的留言安全處理改成 SafetyLLM-only，並新增專用直播入口 `/live/`。直播對話顯示不再依賴 dashboard/chat.html，也不再把 YouTube Live session 混回私人聊天 dashboard。

## SafetyLLM 資料流

1. 原始 YouTube 留言與 SC 仍保存於 YouTubeBridge runtime DB 的 `message_text`，只作測試與稽核用途。
2. 新留言進 DB 時狀態為 `safety_status=pending`、`safety_label=unclassified`。
3. 注入 MemoriaCore 前，Bridge 先呼叫 `youtube_live_safety_classifier_prompt`，批次產生：
   - `safety_label`
   - `safe_message_text`
   - `safety_summary`
   - `safety_reason`
   - `safety_confidence`
4. 主 LLM、Live Chat、Chat Preview、摘要流程只能讀 `safe_message_text` 或安全摘要，不讀 raw `message_text`。
5. SafetyLLM 失敗時採 fail-closed：留言不逐字顯示、不注入原文，只顯示「安全檢查未完成」。
6. SC 與一般留言使用同一安全標準；SC 只影響排程優先級，不提高內容可信度。

## Live Page

1. 新增 `/live/` 作直播入口。
2. `/live/` 左側 iframe 載入 `/live-chat/`，顯示只讀直播聊天室。
3. `/live/` 右側 iframe 載入 `/ui/?embedded=control`，使用現有 YouTubeBridge 主控台。
4. `/live-chat/` 只呼叫 Bridge API：
   - `/sessions`
   - `/sessions/{session_id}/chat-preview`
   - `/sessions/{session_id}/events`
5. 不連到 dashboard/chat.html，不依賴 dashboard 的 session/channel 過濾。
6. Live Chat 預設最新訊息在下方，並提供切換最新在上方。

## 可見內容規則

顯示：
- 觀眾留言 system_event 的安全摘要。
- AI 回覆。
- 導播短提示。

不顯示：
- hidden external context。
- 完整導播 prompt。
- Topic Pack 原始內容。
- 攻擊原文、URL token、system prompt 竄改內容。

## 測試重點

- 新事件預設 pending，不靠規則字典分類。
- SafetyLLM 分類後，注入上下文只含 safe text。
- 括號注入與 prompt injection 不出現在 context/live chat/memory summary。
- `/live/` 與 `/live-chat/` 靜態入口存在。
- SSE 在 interaction completed、director injected、closing thanks、safety classified 時觸發 Live Chat 更新。
