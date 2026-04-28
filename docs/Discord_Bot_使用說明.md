# Discord Bot 使用說明

本文說明 MemoriaCore Discord Bot 的設定方式、呼叫方式與常見排查。

## 前置需求

- 已安裝依賴：
  ```bash
  pip install -r requirements.txt
  ```
- `requirements.txt` 需包含：
  ```text
  discord.py>=2.7,<3.0
  ```
- Discord Developer Portal 的 Bot 設定必須開啟 **Message Content Intent**，否則 bot 可能只能收到事件，無法讀取訊息內容。

## 建立 Discord Bot

1. 到 Discord Developer Portal 建立 Application。
2. 在 Bot 頁面建立 bot，複製 bot token。
3. 在 Bot 的 Privileged Gateway Intents 開啟 **Message Content Intent**。
4. 到 OAuth2 / URL Generator 產生邀請連結，建議 scope：
   - `bot`
5. 建議 bot permissions：
   - View Channels
   - Send Messages
   - Read Message History
   - Attach Files
6. 使用邀請連結把 bot 加入伺服器。

## 在 MemoriaCore 新增 Bot

可從 Streamlit UI 或 dashboard 的 Bot 管理頁新增：

- `Bot ID`：自訂唯一 ID，例如 `main-discord`
- `平台`：選 `discord`
- `顯示名稱`：自訂名稱
- `綁定角色`：選擇要使用的人格角色
- `Token`：貼上 Discord bot token
- `啟用`：勾選

儲存後，後端會在 FastAPI lifespan startup 或 bot reload 時啟動 Discord gateway runtime。若狀態為 `running`，表示已連線。

同一套 MemoriaCore 可以同時建立多個 bot config；單一 `bot_id` 只代表一個平台。若同一角色要同時接 Telegram 與 Discord，請建立兩筆設定，例如：

- `main-telegram`：`platform="telegram"`
- `main-discord`：`platform="discord"`

## 如何在 Discord 呼叫 AI

### 私訊 DM

直接對 bot 傳訊息即可。

```text
你好，幫我整理今天的待辦
```

DM 會被記錄為：

- `channel = discord_private`
- 允許 profile 抽取
- 使用 Discord user id 作為記憶歸屬 `user_id`

### 伺服器頻道

在伺服器文字頻道中，bot 不會回應所有訊息。必須用下列其中一種方式觸發：

1. 提及 bot：
   ```text
   @你的Bot 幫我總結這段討論
   ```
2. 回覆 bot 先前傳出的訊息。

伺服器頻道訊息會被記錄為：

- `channel = discord_public`
- 只讀 public 記憶
- 不進行 profile 抽取

這個設計是為了避免 bot 監聽整個伺服器聊天，也避免公開頻道污染私人記憶。

## 可用指令

```text
/status
```

查看目前 Discord 對話來源的 session 狀態。

```text
/clear
```

清除目前 Discord 對話來源的 session。DM、不同伺服器頻道、不同使用者會有不同 session，不會互相清掉。

## 回覆行為

- 長回覆會自動分段，避免超過 Discord 單則訊息 2000 字元限制。
- 生成圖片時，MemoriaCore 會讀取本地 `.jpeg` 並用 Discord 附件傳送。
- 工具呼叫或搜尋期間會送出 `🔍` 狀態訊息，後續用 edit 更新狀態。

## 常見排查

### Bot 狀態不是 running

檢查：

- Token 是否正確。
- Bot config 是否已勾選啟用。
- 後端是否已重啟，或是否按過 reload。
- `discord.py` 是否已安裝。

### Bot 在 Discord 沒有回應

檢查：

- Developer Portal 是否開啟 **Message Content Intent**。
- Bot 是否有該頻道的 View Channels / Send Messages 權限。
- 在伺服器頻道是否有提及 bot，或是否正在回覆 bot 的訊息。
- 若是私訊，確認 bot 沒有被使用者封鎖。

### Bot 能回文字但不能傳圖片

檢查：

- Bot 是否有 Attach Files 權限。
- 圖片是否仍存在於本地生成圖路徑。
- 伺服器或頻道是否限制附件上傳。

