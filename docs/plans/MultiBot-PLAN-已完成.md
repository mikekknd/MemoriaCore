# 多 Bot Token 與多角色 Runtime 改造計畫

## Summary

將現有單一 `telegram_bot_token + active_character_id` 架構改成「獨立 Bot registry」：一份 server 可同時啟動多個 Telegram bot，每個 bot config 綁定自己的 `character_id` 與 token。第一版建立通用模型，Telegram runtime 完整可用；Discord/其他平台保留資料結構與 UI 位置，但不啟動 runtime。Token 顯示依使用者選擇暫時沿用明文。

## Key Changes

- 新增 Bot registry 與 API：
  - 新增 `bot_configs.json`，每筆包含 `bot_id`、`platform`、`display_name`、`character_id`、`token`、`enabled`。
  - 新增 `/api/v1/bots` CRUD 與 `/api/v1/bots/{bot_id}/reload`，僅 admin 可用。
  - 新增 `api/models/bots.py` 放 Pydantic DTO，不在 router 內定義 model。
  - 驗證規則：`bot_id` 唯一且不可更新；enabled Telegram bot 必須有 token；enabled Telegram token 不可重複；`character_id` 必須存在。
  - `platform` v1 支援保存 `telegram`，可預留 `discord`/`other`，但非 Telegram runtime 狀態回 `unsupported`。

- 改造 Telegram runtime：
  - 將 `api/telegram_bot.py` 從 module-level 單例改成多 instance manager (`TelegramBotManager`)。
  - **Singleton DI 註冊**：新的 `TelegramBotManager` 必須在 `api/dependencies.py` 中初始化並註冊為 Singleton，供 FastAPI Router 與背景流程統一注入。
  - **伺服器啟動掛載 (Startup Event)**：在 FastAPI 的啟動階段（lifespan），必須呼叫 manager 讀取 `bot_configs.json`，自動將所有 `enabled=True` 的 bot 初始化並 `start_polling`。
  - 每個 Telegram bot 建立自己的 `Bot + Dispatcher + polling_task`，`start_polling(..., handle_signals=False)`。
  - Handler 由 factory 綁定 `bot_id` 與 `character_id`，避免全域 active character 污染。
  - Session map 改為 `(bot_id, telegram_user_id) -> session_id`，`/clear`、`/status` 只作用於目前 bot。
  - 啟動前呼叫 `delete_webhook(drop_pending_updates=False)`，避免 webhook 與 long polling 衝突。
  - 熱重載採 diff：新增/啟用/修改 token 或角色時只重啟受影響 bot；刪除/停用時停止該 bot；其他 bot 不受影響。
  - runtime status 保留 `running`、`disabled`、`unsupported`、`error`、`last_error`，但 log 不輸出 token。

- 修正角色與 session 隔離：
  - `SessionState` 與 `conversation_sessions` 補 `bot_id`、`character_id` 欄位，建立、還原、歷史列表都帶出。
  - **DB Schema 安全性**：`messages` 資料表 **不需** 新增這兩個欄位，因為訊息依附於 `session_id`，Session 隔離已足夠防止記憶錯亂。
  - `session_manager.create()` 新增 `bot_id`，並讓 `channel_class` 預設跟 `resolve_context()` 的 persona/write visibility 一致。
  - Telegram session 建立時使用 bot config 的 `character_id`，`user_id` 仍是 Telegram user id。
  - 單層與雙層 orchestration 都改用 `session_ctx["character_id"]` 載入角色 prompt，不再硬讀 `active_character_id`。
  - **Web UI 預設角色處理**：REST/SSE/WebSocket 若建立新 session 時未指定，將 fallback 讀取 `user_prefs.json` 中的 `active_character_id`，使其作為「Web 介面 / 全域預設角色」，避免 Web UI 發起的對話失敗。TTS fallback 也一併依循此邏輯。
  - 若 session character 找不到，fallback 到 `default` 並寫 system log；Bot API 驗證會阻止新設定引用不存在角色。

- 管理 UI：
  - Streamlit 新增「Bot 管理」頁，支援新增、編輯、停用、刪除、reload、查看 runtime 狀態，角色用 `/character` 下拉選單選取。
  - Static dashboard 新增 `Bots` tab 與 `static/bots.html`，功能與 Streamlit 同步。
  - 從系統設定頁移除主要 Telegram token 編輯欄位；保留舊欄位相容但標為 deprecated。
  - 角色編輯頁不直接放 token，避免角色設定和平台部署設定混在一起。
  - **廢棄檔案清理**：依照專案規範，所有 UI 變更應在 `ui/character.py`、新增的 `ui/bots.py` 與 `static/dashboard.html` 進行，絕對不要修改已經廢棄的 `ui/chat.py` 與 `ui/history.py`。

- Backward compatibility：
  - 若 `user_prefs.json` 有舊 `telegram_bot_token` 且 `bot_configs.json` 尚無 Telegram bot，首次載入時自動建立 `legacy-telegram`，綁定當前 `active_character_id`。
  - `telegram_bot_token` 與 `active_character_id` 暫時保留於 config DTO，避免舊前端或外部工具壞掉；新 runtime 以 Bot registry 為主。
  - 舊 conversation session 沒有 `character_id`/`bot_id` 時，migration 預設 `character_id='default'`、`bot_id=''`。

- PersonaSync 與 PersonaProbe 漏點修正：
  - `conversation_sessions.character_id` 加入後，PersonaSync 必須可按 `character_id + persona_face/channel_class` 計算訊息數與閒置時間。
  - `PersonaProbe.load_fragments_from_db()` 增加 `character_id` filter，避免多角色反思混用對話。
  - 定時 PersonaSync 不再只跑 `active_character_id`；改為掃描 conversation DB 中所有曾有 assistant 發言的角色作為 dirty 候選，對每個角色的 public/private face 分開判斷。
  - 手動 PersonaProbe 頁保留 active character 流程，但 API 需支援指定 `character_id`，UI 下拉選到哪個角色就傳哪個角色。

## Test Plan

- Bot config API：
  - 建立、更新、停用、刪除 bot config。
  - 重複 `bot_id`、不存在 `character_id`、enabled Telegram 空 token、enabled Telegram token 重複都要失敗。
  - 舊 `telegram_bot_token` 可自動 migration 成 `legacy-telegram`。

- Telegram runtime：
  - FastAPI 啟動時驗證 enabled bots 有自動執行 `start_polling`。
  - 用 fake `Bot/Dispatcher` 測試多個 enabled Telegram config 會各自 start/stop/reload。
  - 修改單一 bot token 或 character 時，只重啟該 bot。
  - 兩個 bot 面對同一 Telegram user 時 session_id 不同，互不覆蓋。
  - `/clear` 只清目前 bot 的 session。

- Chat/persona 行為：
  - 建立兩個 bot config 綁不同 `character_id`，同一 user 對話時 orchestration 收到不同 `session_ctx.character_id`。
  - Web UI 發起對話時能正確吃下 `active_character_id`。
  - 單層與雙層 orchestration 都使用 session character prompt。
  - REST/SSE/WebSocket TTS fallback 使用 session character 的 `tts_language`/`tts_rules`。

- DB/session migration：
  - 新 DB 建立 `bot_id`、`character_id` 欄位（驗證 `messages` 不受影響）。
  - 舊 DB 自動 ALTER TABLE，不破壞既有 session/history。
  - restore session 後 `character_id` 不丟失。

- PersonaSync：
  - 不同角色的 conversation fragments 不互相混入。
  - public/private face 仍依 `resolve_context()` 隔離。
  - Telegram 非 SU 訊息計入 public face；SU 訊息計入 private face。

## Assumptions

- Token 在 API/UI 暫時明文讀寫，符合本次選擇；但不得寫入 log。
- v1 不新增 Discord client/library，只保留 registry 與 UI 架構，Discord runtime 之後接入。
- 多 Bot 目標是 Telegram 私訊型使用；群組支援不在 v1 範圍。
- 一個 bot config 綁定一個 `character_id`；若未來要同一 token 多角色，需要另開 routing 規則，不放進本次改造。
- **SU 權限共用**：v1 階段所有 Telegram bots 將共用全域的 `telegram_su_id` 設定，即所有 bot 的管理員權限規則皆相同，暫不實作獨立 bot 的 SU 列表。
