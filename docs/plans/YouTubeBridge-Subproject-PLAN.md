# YouTubeBridge 子專案落地計畫

## Summary

將 YouTube Live Chat 整合從 MemoriaCore 的 BotRegistry 中拆出，改成類似 `PersonaProbe/` 的同 repo 子專案：`YouTubeBridge/`。

MemoriaCore 保留核心能力：角色、session、AI 對話、記憶、通用 `external_context` 注入。YouTubeBridge 負責 YouTube API、live session、聊天室暫存、OBS/dashboard 管理，以及把外部上下文送進 MemoriaCore。

## 核心決策

- YouTube 不再是 MemoriaCore bot platform。
- YouTube API key 屬於 YouTubeBridge connector。
- `video_id` / `live_chat_id` 屬於 YouTubeBridge live session。
- YouTubeBridge 不直接 import 或寫入 MemoriaCore `core/` / DB。
- YouTubeBridge 透過 MemoriaCore HTTP API 注入外部上下文。
- YouTube 原始留言保存在 `runtime/YouTubeBridge/youtube_live.db`。
- MemoriaCore 中的聊天 API 只接受通用 external context payload，並視為不可信來源。

## 子專案結構

```text
YouTubeBridge/
├── CLAUDE.md
├── __init__.py
├── app.py
├── bridge_engine.py
├── memoria_client.py
├── models.py
├── prompts.py
├── requirements.txt
├── server.py
├── start.bat
├── storage.py
├── youtube_client.py
└── tests/
    └── test_storage.py
```

## MemoriaCore 需要保留的接口

### Chat external context

`POST /api/v1/chat/sync` 與 `POST /api/v1/chat/stream-sync` 已支援：

```json
{
  "content": "請回應最近直播留言",
  "session_id": "memoria-session-id",
  "character_ids": ["coco", "bailian"],
  "external_context": {
    "source": "youtube_live",
    "source_session_id": "yt-session-id",
    "context_text": "- 觀眾A: hello",
    "event_ids": [1, 2, 3],
    "summary": {
      "event_count": 3
    }
  }
}
```

MemoriaCore 規則：

- 不信任 `external_context`。
- 只注入本次 LLM context。
- 不把 external context 寫入 user message。
- 不把 YouTube 觀眾視為 MemoriaCore user。
- group session 仍由 `character_ids` 控制 AI 發言者，不由 external session 名稱當 speaker。

## YouTubeBridge 實作步驟

### 1. Connector

- 建立 `connectors` 表。
- 保存：
  - `connector_id`
  - `display_name`
  - `api_key`
  - `enabled`

### 2. Live Session

- 建立 `live_sessions` 表。
- 保存：
  - `session_id`
  - `connector_id`
  - `video_id`
  - `live_chat_id`
  - `target_memoria_session_id`
  - `character_ids_json`
  - `status`
  - `auto_connect`
  - `max_context_messages`
  - `max_context_chars`
  - `retention_days`

### 3. Event 暫存

- 建立 `live_events` 表。
- 以 `(bridge_session_id, youtube_message_id)` 去重。
- 保存原始留言必要欄位。
- 原始事件只留在 YouTubeBridge DB。

### 4. Runtime

- `bridge_engine.py` 管理 polling task。
- 啟動 session 時：
  - 讀 connector API key。
  - 若沒有 `live_chat_id`，用 `video_id` 解析。
  - polling `liveChatMessages.list`。
  - 新事件寫入 DB 並 SSE broadcast。

### 5. MemoriaCore Client

- `memoria_client.py` 透過 HTTP 呼叫 MemoriaCore。
- 支援：
  - admin bypass。
  - username/password login。
  - existing cookie/csrf env。
  - `POST /api/v1/chat/sync`。

### 6. UI / API

- `server.py` 提供 YouTubeBridge 自己的 API，預設 port `8091`。
- `app.py` 提供 Streamlit 管理 UI，預設 port `8503`。
- `start.bat` 同時啟動 server port `8091` 與 Streamlit port `8503`。

## Rollout

1. 建立 YouTubeBridge 子專案並通過 storage tests。
2. 移除 MemoriaCore BotRegistry 中的 YouTube platform。
3. MemoriaCore Chat API 改成通用 external context。
4. 用 YouTubeBridge 建立 connector + live session。
5. 手動 start session，確認 recent events 與 SSE。
6. 用 reply recent 呼叫 MemoriaCore chat。
7. Phase 2 再加入直播結束摘要與 shared/public memory 寫入。

## 後續 Phase 2

直播摘要仍依 `docs/plans/YouTube-Live-Shared-Memory-Phase2-PLAN.md` 執行，但資料來源改為 YouTubeBridge DB，而不是 MemoriaCore runtime DB。
