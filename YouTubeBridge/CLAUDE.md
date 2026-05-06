# YouTubeBridge — CLAUDE.md

## 先讀

本子專案的最新結構索引在 `YouTubeBridge/README.md`。其他 session 接手 YouTubeBridge 前，先讀 README，再讀任務相關模組。

## 專案概述

YouTubeBridge 是 MemoriaCore 的同 repo 子專案，負責 YouTube Live Chat 讀取、live session 管理、暫存事件、SSE 推送、導播/注入流程，以及把直播留言脈絡送入 MemoriaCore 的通用 `external_context`。

此子專案定位類似 `PersonaProbe/`：可以獨立啟動，有自己的 FastAPI API server 與靜態控制台，但不直接 import MemoriaCore 的 `core/` 或寫入 MemoriaCore 主 DB。與 MemoriaCore 溝通時使用 `memoria_client.py` 的 HTTP API client。

## 目前架構重點

- `server.py` 是 FastAPI app facade；route implementation 已拆到 `server_routes/`。
- `storage.py` 是 `BridgeStorage` facade；repository implementation 已拆到 `storage_repositories/`。
- `bridge_engine.py` 是 `YouTubeBridgeManager` facade；多數 runtime 職責已拆到 `engine_*.py` mixin。
- `bridge_engine.py` 目前仍保留 YouTube polling、Research Gate / external context、`_broadcast()` 與部分 presenter facade。
- 舊的 Streamlit `app.py` 已移除；控制台由 `server.py` 掛載 `static/` 內的 `/ui/`、`/live/`、`/live-chat/`。
- 原始 YouTube 留言只保存於 `runtime/YouTubeBridge/youtube_live.db`。
- 注入 MemoriaCore 的 `external_context` 必須視為不可信資料。

## Root-Level Facade 規則

不要為了整理資料夾而直接搬走這些 root-level 入口：

- `bridge_engine.py`
- `storage.py`
- `server.py`
- `memoria_client.py`
- `youtube_client.py`

原因是測試、啟動腳本與外部 session 仍大量依賴 root-level import，例如 `from bridge_engine import YouTubeBridgeManager`、`from storage import BridgeStorage`、`uvicorn server:app`。

不要重新新增 YouTubeBridge Streamlit 入口；若要改控制台，請修改 `static/` 與 `server_routes/ui.py`。

後續拆分建議採用「行為修正優先，碰到肥大區塊才局部拆分」；大規模 package 化應獨立成純整理階段，不要混入行為變更。

## 執行方式

```bat
start.bat
```

預設：

- YouTubeBridge API: `http://localhost:8091`
- YouTubeBridge Control UI: `http://localhost:8091/ui/`
- MemoriaCore API: `http://localhost:8088/api/v1`

可用環境變數：

- `YOUTUBE_BRIDGE_API_KEY`：若設定，Bridge API 需要 `X-Bridge-Key`。
- `MEMORIACORE_BASE_URL`：預設 `http://localhost:8088/api/v1`。
- `MEMORIACORE_USERNAME` / `MEMORIACORE_PASSWORD`：用帳密登入 MemoriaCore。
- `MEMORIACORE_AUTH_COOKIE` / `MEMORIACORE_CSRF_TOKEN`：直接使用既有登入 cookie。
- `MEMORIACORE_ADMIN_BYPASS=1`：使用 `/auth/bypass`，需 MemoriaCore 啟用本機 admin bypass。`start.bat` 會在未設定時預設設為 `1`，但 MemoriaCore 仍會執行自己的 bypass 開關與 loopback 檢查。
- `MEMORIACORE_TIMEOUT_SECONDS`：等待 MemoriaCore `/chat/sync` 完成的秒數，預設 `180`。LLM 回應較慢時可調高。

## 測試規則

完整 YouTubeBridge 測試：

```powershell
python -m pytest YouTubeBridge/tests -q
```

拆分或 engine runtime 相關 targeted 測試：

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_split_modules.py YouTubeBridge/tests/test_bridge_engine.py -q
```

若 Windows 上 `.pyTestTemp\basetemp` 發生 ACL / PermissionError，先執行：

```powershell
scripts\cleanup_pytest_temp.bat
```

不要改用其他 ad hoc `--basetemp` 位置繞過。
