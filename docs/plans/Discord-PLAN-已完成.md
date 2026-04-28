# Discord 整合計畫書

## Summary (目標與範圍)
將 MemoriaCore 接入 Discord 平台，利用現有的 `BotRegistry` 多機器人管理架構，建立與 Telegram 類似的 Discord 背景連線（Gateway）。此計畫旨在讓使用者可透過 Discord 機器人與 AI 互動，並遵守 MemoriaCore 的三維度記憶隔離架構。

## 既有計畫的缺陷與修正建議
1. **不需建立 `api/routers/discord.py`**：Discord 的標準對話機器人是使用 WebSocket Gateway 連線（透過 `discord.py` 函式庫），而非被動的 HTTP Webhook。因此，不需要建立 FastAPI router 來接收事件，只需要在背景執行 client 即可。
2. **WebSocket 連線管理不需改動**：MemoriaCore 的 UI 更新是透過 `session_manager` 統一處理。Discord 的事件只要轉交給 `orchestration.py` 並寫入 session 即可，不需特別修改 FastAPI 的 `ws_manager.py`（除非有額外的廣播需求）。
3. **頻道映射區分公私域 (Critical)**：現有 `resolve_context` 定義了 `discord_public` 和 `discord_private`。使用單一 `channel='discord'` 會破壞權限隔離。實作時應根據 `message.guild` 是否為 `None`，動態給定 `channel='discord_private'` 或 `channel='discord_public'`。
4. **套件依賴**：必須在 `requirements.txt` 中加入 `discord.py`。
5. **缺少 Manager 整合機制**：只建立 `api/discord_bot.py` 不夠，需要像 Telegram 一樣，在 `api/dependencies.py` 和 `api/main.py` 處理 lifecycle，並在 `api/routers/bots.py` 實作狀態查詢。

## Key Changes (具體實作步驟)

### 1. 基礎環境設定
- 更新 `requirements.txt`，加入 `discord.py`。
- 準備 Discord Developer Portal 的 Application 與 Bot Token 設定，需開啟 **Message Content Intent**。

### 2. 開發 `api/discord_bot.py` 核心模組
- 建立 `DiscordRuntime` dataclass 與 `DiscordBotManager`，架構參照 `TelegramBotManager`。
- `DiscordBotManager` 負責依據 `bot_configs.json` 中 `platform="discord"` 的設定啟動或停止 `discord.Client` 任務。
- 實作訊息處理器 (`on_message` 事件)：
  - 略過 Bot 自身的訊息 (`message.author == client.user`)。
  - 根據頻道類型判定 `channel`：`message.guild` 存在為 `discord_public`，否則為 `discord_private`。
  - 呼叫 `SessionMap` (或共用) 建立 Session 並交給 `_select_orchestration` 執行推論。
  - 實作支援多圖與長文分段發送的邏輯：利用 `discord.File` 傳送圖片，並攔截 Markdown 圖片語法。
  - 狀態回報 (`_telegram_event_callback` 替代)：可利用 `message.channel.typing()` 加上臨時訊息（`await message.reply("🔍 ...")`）然後 `edit()` 來達成即時狀態更新。

### 3. Lifecycle 與 Dependency 注入
- **`api/dependencies.py`**：
  - 新增 `discord_bot_mgr: DiscordBotManager | None = None` 單例。
  - 於 `init_all()` 中初始化。
  - 建立 `get_discord_bot_manager()`。
- **`api/main.py`**：
  - 在 `lifespan` 區段內，呼叫 `await get_discord_bot_manager().sync_from_registry()`。
  - 關機清理區段內，呼叫 `await get_discord_bot_manager().stop_all()`。

### 4. 修正 Bot API 路由 (`api/routers/bots.py`)
- 修改 `_dto()` 函式：根據 `config.get("platform")` 判斷，若是 `telegram` 呼叫 `telegram_bot_manager`，若是 `discord` 則呼叫 `discord_bot_manager` 取狀態。
- 調整 `reload_bot` 與 `delete_bot`：判斷對應的 platform，呼叫正確 manager 的 `reload_bot` 或 `stop_bot`。
