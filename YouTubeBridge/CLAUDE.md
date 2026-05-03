# YouTubeBridge — CLAUDE.md

## 專案概述

YouTubeBridge 是 MemoriaCore 的同 repo 子專案，負責 YouTube Live Chat 讀取、live session 管理、暫存事件、SSE 推送，以及把直播留言脈絡送入 MemoriaCore 的通用 `external_context`。

此子專案定位類似 `PersonaProbe/`：可以獨立啟動，有自己的 FastAPI API server 與 Streamlit UI，但不直接 import MemoriaCore 的 `core/` 或寫入 MemoriaCore DB。

## 檔案結構

```text
YouTubeBridge/
├── bridge_engine.py   # polling runtime 與 session lifecycle
├── youtube_client.py  # YouTube Data API client
├── storage.py         # YouTubeBridge 自己的 SQLite 存取
├── memoria_client.py  # 透過 HTTP 呼叫 MemoriaCore API
├── models.py          # Pydantic request/response models
├── server.py          # FastAPI API server（port 8091）
├── app.py             # Streamlit UI（port 8503）
├── prompts.py         # Phase 2 摘要 prompt placeholder
├── requirements.txt
└── tests/
```

## 架構原則

- `bridge_engine.py` 與 `storage.py` 是純 Python 模組，禁止 import Streamlit。
- `app.py` 只負責 UI 與呼叫 `server.py` API。
- `server.py` 只負責 HTTP 邊界與 manager lifecycle。
- `memoria_client.py` 只能透過 HTTP 呼叫 MemoriaCore，不直接讀寫主專案 DB。
- YouTube API key 屬於 connector。
- `video_id` / `live_chat_id` 屬於 live session。
- 原始 YouTube 留言只保存於 `runtime/YouTubeBridge/youtube_live.db`。
- 注入 MemoriaCore 的 `external_context` 必須視為不可信資料。

## 執行方式

```bat
start.bat
```

預設：

- YouTubeBridge API: `http://localhost:8091`
- YouTubeBridge UI: `http://localhost:8503`
- MemoriaCore API: `http://localhost:8088/api/v1`

可用環境變數：

- `YOUTUBE_BRIDGE_API_KEY`：若設定，Bridge API 需要 `X-Bridge-Key`。
- `MEMORIACORE_BASE_URL`：預設 `http://localhost:8088/api/v1`。
- `MEMORIACORE_USERNAME` / `MEMORIACORE_PASSWORD`：用帳密登入 MemoriaCore。
- `MEMORIACORE_AUTH_COOKIE` / `MEMORIACORE_CSRF_TOKEN`：直接使用既有登入 cookie。
- `MEMORIACORE_ADMIN_BYPASS=1`：使用 `/auth/bypass`，需 MemoriaCore 啟用本機 admin bypass。`start.bat` 會在未設定時預設設為 `1`，但 MemoriaCore 仍會執行自己的 bypass 開關與 loopback 檢查。
- `MEMORIACORE_TIMEOUT_SECONDS`：等待 MemoriaCore `/chat/sync` 完成的秒數，預設 `180`。LLM 回應較慢時可調高。

## 目前範圍

- Connector CRUD。
- Live session CRUD。
- YouTube live chat polling。
- Event 暫存與去重。
- SSE 訂閱事件。
- 將最近事件整理成 MemoriaCore `external_context` 並呼叫 `/chat/sync`。

## 不在目前範圍

- 回覆 YouTube 留言。
- YouTube 觀眾 ID 與 MemoriaCore user 綁定。
- 長期記憶寫入。
- 直播結束摘要。

直播摘要將在 Phase 2 依 `docs/plans/YouTube-Live-Shared-Memory-Phase2-PLAN.md` 另行實作。
