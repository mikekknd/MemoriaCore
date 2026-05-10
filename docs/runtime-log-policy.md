# Runtime Log Policy

## 目標

`runtime/` 根層保留資料庫、設定、secret 與結構化 trace。一般 process stdout/stderr log 統一寫入：

```text
runtime/log/
```

`runtime/llm_trace.jsonl` 是例外：它是 MemoriaCore dashboard / API 使用的結構化 LLM trace，仍保留在 `runtime/` 根層。

## 固定輸出來源

- `start.bat`：`runtime/log/api_8088.out.log`、`runtime/log/api_8088.err.log`
- `startServerHotReload.bat`：`runtime/log/api_8088_hot_reload.out.log`、`runtime/log/api_8088_hot_reload.err.log`
- `start_full.bat`：
  - `runtime/log/api_8088.out.log`、`runtime/log/api_8088.err.log`
  - `runtime/log/streamlit_8501.out.log`、`runtime/log/streamlit_8501.err.log`
- `YouTubeBridge/start.bat`：`runtime/log/youtube_bridge_8091.out.log`、`runtime/log/youtube_bridge_8091.err.log`
- `YouTubeBridge/start_hot_reload.bat`：`runtime/log/youtube_bridge_8091_hot_reload.out.log`、`runtime/log/youtube_bridge_8091_hot_reload.err.log`

## 清理規則

使用：

```bat
scripts\cleanup_runtime_logs.bat
```

預設會把散落在 `runtime/` 或 `runtime/YouTubeBridge/` 的 `*.log` 與 `youtube_bridge_e2e_*.jsonl` 搬到：

```text
runtime/log/legacy-YYYYMMDD-HHMMSS/
```

若確認不需要保留舊 log，可使用：

```bat
scripts\cleanup_runtime_logs.bat -Delete
```

清理腳本不會處理：

- `runtime/llm_trace.jsonl`
- `*.db`
- `*.db-wal` / `*.db-shm`
- `.memoriacore_jwt_secret`
- JSON 設定檔
- `runtime/YouTubeBridge/FactCards/`
