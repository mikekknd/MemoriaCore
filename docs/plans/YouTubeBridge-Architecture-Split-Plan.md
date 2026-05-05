# YouTubeBridge 架構拆分製作方案

## 目標

降低 `YouTubeBridge/bridge_engine.py`、`YouTubeBridge/storage.py`、`YouTubeBridge/server.py` 的單檔負擔，讓 runtime、storage、HTTP 邊界、資料展示與測試資料產生各自有清楚職責，同時保留目前公開 API 與測試相容性。

## 原則

- 第一階段只做不改行為的 facade 式拆分。
- 原有入口保持可用：`from storage import BridgeStorage`、`from bridge_engine import YouTubeBridgeManager`、`uvicorn server:app` 都不改。
- 先抽純 helper 與 schema/mapping 這類低狀態邊界，再處理 async runtime。
- 測試要證明「拆分後的入口仍產出同樣結果」，避免只測新模組。
- 不納入既有未提交功能變更，也不搬動 `bridge_engine.py` 中目前仍在演進的 live query resolver 邏輯。

## 階段 1：Storage 可測拆分

### 新增檔案

- `YouTubeBridge/storage_schema.py`
  - 負責 DB schema 初始化、索引建立與欄位 migration。
  - 暴露 `init_bridge_db(conn)`、`ensure_live_session_columns(conn)`、`ensure_live_event_columns(conn)`。

- `YouTubeBridge/storage_mappers.py`
  - 負責 JSON、vector、row mapper 與簡單資料轉換。
  - 暴露 `json_dump()`、`json_load()`、`topic_entry_content_hash()`、`vector_to_blob()`、`blob_to_vector()`、`cosine_similarity()`、`row_value()`、`int_or_default()` 與 `row_to_*()`。

### 修改檔案

- `YouTubeBridge/storage.py`
  - 保留 `BridgeStorage` 類別與所有既有 public method。
  - `_init_db()` 委派給 `init_bridge_db()`。
  - `_ensure_live_session_columns()` / `_ensure_live_event_columns()` 包裝新模組函式。
  - `_json_*`、`_row_to_*`、vector helper 改為薄包裝，保持測試與外部呼叫相容。

## 階段 2：Server 邊界拆分

### 新增檔案

- `YouTubeBridge/server_security.py`
  - 負責 loopback 判斷、bridge key 驗證、UI/SSE 例外路徑判斷。

- `YouTubeBridge/server_presenters.py`
  - 負責 public response sanitizer，例如 chat preview、interaction metadata、topic pack usage status。

### 修改檔案

- `YouTubeBridge/server.py`
  - 保留 `app`、路由、`uvicorn server:app` 相容。
  - 將 security 與 presenter helper 轉成委派包裝。
  - 不在本階段拆 router，避免與現有 server 變更混線。

## 階段 3：Bridge Engine 漸進拆分

這階段等 live query resolver 相關變更穩定後再做，避免一次改動 async runtime 與正在演進的 research gate。

候選模組：

- `engine/runtime.py`：`LiveRuntime`、start/stop、polling、subscriber。
- `engine/injection.py`：auto inject、external context 組裝。
- `engine/director.py`：director loop、director decision、closing super chat thanks。
- `engine/topic_packs.py`：embedding、search、usage、auto build。
- `engine/research.py`：audience research、research result to fact card。
- `engine/test_events.py`：測試留言與 Super Chat 產生。
- `engine/safety.py`：SafetyLLM 分類與 public event filtering。

## 第一階段驗證命令

```powershell
python -m pytest YouTubeBridge/tests/test_storage.py -q --basetemp=.pyTestTemp/basetemp
python -m pytest YouTubeBridge/tests/test_server_auth.py -q --basetemp=.pyTestTemp/basetemp
```

若 Windows pytest 暫存 ACL 清理失敗，依根目錄規則先執行：

```powershell
scripts/cleanup_pytest_temp.bat
```

## 不做事項

- 不改 API URL。
- 不改 DB schema 內容。
- 不改 YouTube polling 或 MemoriaCore injection 行為。
- 不把現有未提交功能變更納入重構範圍。
