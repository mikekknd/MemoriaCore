# YouTubeBridge 結構索引

這份文件是給 Codex / Claude / 其他 agent session 的快速入口。先讀這份，再視任務深入特定模組。

## 專案定位

YouTubeBridge 是 MemoriaCore 同 repo 子專案，負責 YouTube Live Chat 讀取、live session 管理、暫存事件、SSE 推送、導播/注入流程，以及把直播留言脈絡送入 MemoriaCore 的 `external_context`。

YouTubeBridge 可獨立啟動，有自己的 FastAPI API server 與由 `server.py` 提供的靜態控制台。它不直接 import MemoriaCore 的 `core/`，也不直接寫入 MemoriaCore 主 DB；需要和 MemoriaCore 溝通時使用 HTTP client。

## 重要入口

- `server.py`：FastAPI app facade，保留給 `uvicorn server:app` 與既有 import 相容。
- `bridge_engine.py`：`YouTubeBridgeManager` facade，組合多個 `engine_*` mixin。
- `storage.py`：`BridgeStorage` facade，組合 storage repository mixin。
- `memoria_client.py`：透過 HTTP 呼叫 MemoriaCore API。
- `youtube_client.py`：YouTube Data API client 與訊息 normalize helper。
- `summary_engine.py`：直播摘要與記憶寫入流程。

不要為了整理資料夾而先搬動這些 facade。很多測試、啟動腳本與外部 session 仍依賴 root-level import。舊的 Streamlit 入口已移除；控制台使用 `server.py` 掛載的 `/ui/` 靜態頁。

## 目前資料夾結構

```text
YouTubeBridge/
├── server_routes/             # FastAPI route 分拆
├── storage_repositories/      # BridgeStorage repository 分拆
├── static/                    # 控制台與直播頁靜態資源
│   └── ui/                    # 控制台 JS/CSS modules
├── tests/                     # YouTubeBridge pytest suite
├── bridge_engine.py           # Manager facade，目前仍保留 polling、Research Gate、external context
├── engine_*.py                # bridge_engine 拆出的 mixin/helper
├── storage.py                 # Storage facade
└── server.py                  # FastAPI app facade，提供 API 與 /ui/ 靜態控制台
```

## Engine 模組地圖

`bridge_engine.py` 目前不是所有 runtime 的實作集中地，而是 `YouTubeBridgeManager` facade。新增 runtime/流程責任時，優先找既有 mixin 邊界，不要直接塞回主檔。

- `bridge_runtime.py`：`LiveRuntime` runtime state。
- `bridge_contracts.py`：LLM schema、classifier limit、常數 contract。
- `engine_runtime_lifecycle.py`：session start/stop/status、subscribe/unsubscribe、autostart、duration helper。
- `engine_director.py`：director 決策與公開 prompt/helper。
- `engine_director_runtime.py`：director 啟停、kickoff、loop、turn execution。
- `engine_injection.py`：手動/自動注入、interaction claim/interrupt、SC interrupt cooldown。
- `engine_closing.py`：duration finalize、closing Super Chat thanks、closing safety resolution。
- `engine_event_safety.py`：pending event SafetyLLM 分類與 safety result normalize。
- `engine_test_runtime.py`：auto test event loop、`generate_test_events()`、test generation facade wrappers。
- `engine_test_events.py`：純測試留言與 Super Chat 產生 helper。
- `engine_public_events.py`：公開事件/metadata presenter helper。
- `engine_topic_packs.py`：Topic Pack、fact cards、embedding、replenish、import/generation。

`bridge_engine.py` 目前還保留的主要責任：

- YouTube polling loop。
- Research Gate / audience query / external context 組裝。
- `_broadcast()`。
- 部分 public presenter facade。

## Server 與 Storage 邊界

`server.py` 是 FastAPI facade，route implementation 在 `server_routes/`：

- `connectors.py`
- `director.py`
- `fact_cards.py`
- `memoria.py`
- `research.py`
- `sessions.py`
- `summaries.py`
- `testing.py`
- `topic_packs.py`
- `ui.py`

Server 支援模組：

- `server_state.py`
- `server_helpers.py`
- `server_presenters.py`
- `server_security.py`

`storage.py` 是 `BridgeStorage` facade，repository implementation 在 `storage_repositories/`：

- `connectors.py`
- `director_state.py`
- `events.py`
- `interactions.py`
- `sessions.py`
- `summaries.py`
- `topic_packs.py`

Storage 支援模組：

- `storage_schema.py`
- `storage_mappers.py`
- `storage_event_utils.py`
- `storage_constants.py`

## 修改建議

- 行為修正優先；只有碰到肥大區塊時才做局部拆分。
- 不要先做大規模 package 化或搬 root-level 檔案。
- 新拆分應保留 `bridge_engine.py`、`storage.py`、`server.py` 的 facade 相容。
- 不要重新新增 Streamlit 入口；YouTubeBridge UI 目前以 `static/` + FastAPI routes 提供。
- 目前下一個高價值但高耦合拆分候選是 Research Gate / external context，應獨立規劃與驗證。
- `engine_topic_packs.py` 與 `storage_repositories/topic_packs.py` 仍偏大，但不要和行為修正混在同一批做大搬移。

## 測試與驗證

常用 targeted 測試：

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_split_modules.py YouTubeBridge/tests/test_bridge_engine.py -q
python -m pytest YouTubeBridge/tests/test_server_auth.py YouTubeBridge/tests/test_server_route_split.py -q
python -m pytest YouTubeBridge/tests/test_storage.py YouTubeBridge/tests/test_storage_repository_split.py -q
```

完整測試：

```powershell
python -m pytest YouTubeBridge/tests -q
```

若 Windows 上 `.pyTestTemp\basetemp` 發生 ACL / PermissionError，先執行：

```powershell
scripts\cleanup_pytest_temp.bat
```

不要改用其他 ad hoc `--basetemp` 位置繞過。
