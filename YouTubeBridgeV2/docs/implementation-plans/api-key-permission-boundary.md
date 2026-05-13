# API Key Permission Boundary Implementation Plan

> **給 agentic worker：** REQUIRED SUB-SKILL：使用 `superpowers:executing-plans` 逐項實作本計畫。

**Goal：** 將主 FastAPI app 的 `/v2` production surface 從 loopback-only 存取升級為 prefs-backed API key permission matrix，同時保留本機開發用的 loopback operator access。

**Architecture：** `api/main.py` 針對主 app `/v2` API/SSE routes 掛載 `V2MainSecurityMiddleware`。Middleware 透過 `YouTubeBridgeV2/server/auth_config.py` 從 `StorageManager.load_prefs()` 讀取有效 API key，依每個 request 建立 `AuthRequirement`，並在 runtime dispatch 前拒絕未授權 request。`/v2/static` 維持公開。

## Scope

Source：

- `YouTubeBridgeV2/server/auth_config.py`
- `YouTubeBridgeV2/server/main_security.py`
- `YouTubeBridgeV2/server/security.py`
- `api/main.py`

Tests：

- `tests/youtubebridge_v2/test_main_app_security.py`
- `tests/youtubebridge_v2/test_access_control_security.py`

Docs：

- `YouTubeBridgeV2/docs/architecture-index.md`
- `YouTubeBridgeV2/docs/api-reference-index.md`
- `YouTubeBridgeV2/docs/modules/access-control-security.md`
- `YouTubeBridgeV2/docs/modules/server-api-surface.md`

Out of scope：

- 不接真 YouTube polling。
- 不接真 MemoriaCore group chat 或 chat sync。
- 不接 TTS delivery。
- 不新增 `/v2` tick endpoint。
- 不新增 API key 管理 UI。
- 不新增 env var 或 hybrid secret source。

## Planned Symbols

- `V2_API_KEYS_PREFS_KEY`
- `V2ApiKeyConfig`
- `load_v2_api_key_config(storage_manager)`
- `V2MainSecurityMiddleware`
- `V2LoopbackOnlyMiddleware`

## Red Cases

- `test_main_app_v2_remote_request_without_key_is_rejected_before_runtime`
- `test_main_app_v2_invalid_key_is_rejected_without_secret_leak`
- `test_main_app_v2_operator_key_can_write_and_read_all_v2_surfaces`
- `test_main_app_v2_operator_key_accepts_authorization_bearer`
- `test_main_app_v2_observer_key_can_read_status_events_and_operator_stream_only`
- `test_main_app_v2_display_key_can_read_display_stream_only`
- `test_main_app_v2_loopback_without_key_still_has_operator_access`
- `test_main_app_v2_remote_fails_closed_when_no_valid_keys_configured`
- `test_main_app_v2_static_assets_remain_public_without_api_key`
- `test_display_scope_cannot_satisfy_observer_requirement`

預期 red command：

```powershell
python -m pytest tests\youtubebridge_v2\test_main_app_security.py -q
python -m pytest tests\youtubebridge_v2\test_access_control_security.py::test_display_scope_cannot_satisfy_observer_requirement -q
```

實作前預期 red 結果：

- Remote operator API key request 會被舊 loopback-only boundary 拒絕。
- 未檢查 route action 時，display key 仍可滿足 observer requirement。

## Green Scope

- 從 `StorageManager.load_prefs()` 載入 `youtubebridge_v2_api_keys`。
- 只接受非空 `key` 且 `permission_group` 為 `operator | display | observer` 的 list entry。
- 將主 app `/v2` path 與 method 映射成 route-specific `AuthRequirement`。
- loopback request 不需要 API key，並視為 operator。
- `/v2/static` 不進入 API key boundary。
- API key config 缺失或格式錯誤時採 fail-closed。
- 收緊 permission implication，讓 display 不能滿足 observer requirement。

## Refactor Boundary

Allowed：

- 新增聚焦的 security config helper。
- 替換 main-app middleware 實作，同時保留既有 `V2LoopbackOnlyMiddleware` 名稱作相容 alias。
- route requirement mapping 保持在 `server/main_security.py`。

Forbidden：

- import 或重用 Legacy `YouTubeBridge/`。
- 在 `core/storage/` 與 `core/storage_manager.py` 以外新增直接 SQLite access。
- 呼叫真 YouTube、MemoriaCore 或 TTS adapters。
- 新增 API key 管理 UI。
- 新增環境變數或 hybrid API key source。

## Permission Matrix

| Group | Allowed | Forbidden |
| --- | --- | --- |
| `operator` | create session, bind plan, manual close, aftertalk policy, status, events, operator stream, display stream | raw secrets and hidden context |
| `observer` | status, events, operator stream | writes and display stream |
| `display` | display stream and static assets | status, events, operator stream, writes |

## Docs Sync

實作後：

- 在 `docs/architecture-index.md` 標示 Wave 2C API key permission matrix 已完成。
- 在 `docs/api-reference-index.md` 補 API key config 與 main security middleware source。
- 更新 Server/API 與 Access Control module docs，反映 prefs-backed API key auth。

## Acceptance Criteria

- Remote `/v2` API/SSE request 必須提供有效 API key，除非目標是 `/v2/static`。
- Loopback `/v2` API/SSE request 仍以 operator 通過。
- Operator、observer、display key 遵守固定 permission matrix。
- Auth failure 回 sanitized `401` 或 `403` response，不洩漏 secret。
- Standalone `create_v2_app(...)` tests 不依賴 main-app middleware。
