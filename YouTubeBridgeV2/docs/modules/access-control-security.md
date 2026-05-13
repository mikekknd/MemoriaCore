# Access Control / Security Module Design

## Purpose

Access Control / Security 負責 V2 API 的存取控制、loopback/API key 規則、MemoriaCore auth delegation、不可信 payload 邊界、secret handling 與安全錯誤回應。

## Ownership

- 擁有 API permission group 與 auth requirement metadata。
- 擁有 operator、display、observer、internal scope 的區分。
- 擁有 secret/config 的注入邊界與不外洩規則；raw API key 不保留在 public dataclass serialization。
- 擁有 sanitized security error contract。
- 不擁有 route business logic、phase decision、adapter payload mapping 或 UI rendering。

## Inputs

- HTTP request metadata：host、origin、headers、API key、session id。
- route auth requirement。
- MemoriaCore auth delegation config。
- untrusted payload summary。

## Outputs

- `PermissionContext`：呼叫端身份、scope、可執行 action。
- `AuthRequirement`：route 或 stream 的權限需求。
- `SecurityErrorResponse`：sanitized error body。
- `SecretBoundary`：可傳入 adapter 的 credential reference。

## Dependencies

- Server/API Surface 使用 permission context。
- MemoriaCore Adapter 使用 auth delegation metadata。
- Operator Console UI 與 Chat Display UI 依賴不同 permission group。
- Observability 記錄 sanitized security event。

## Out Of Scope

- route handler 的 business command。
- UI control layout。
- MemoriaCore transport。
- storage schema。
- YouTube API credential exchange。

## Public Entrypoints

本模組的 public contracts 已由 `YouTubeBridgeV2/server/security.py` 實作。Security module 只產生 permission context、sanitized security error 與 secret reference，不執行 route business command 或 adapter call。

Wave 2C 已將主 app `/v2` API/SSE 升級為 `V2MainSecurityMiddleware`。此 middleware 使用 `StorageManager.load_prefs()` 的 `youtubebridge_v2_api_keys` 作為 API key source，依 path/method 建立 `AuthRequirement`，並排除 `/v2/static`。Loopback request 保留 operator access；非 loopback request 必須提供有效 API key。

- `AuthRequirement`
- `PermissionContext`
- `PermissionGroup`
- `SecurityErrorResponse`
- `SecretBoundary`
- `resolve_permission_context(request, requirement)`
- `sanitize_security_error(error)`
- `V2ApiKeyConfig`
- `V2ApiKeyPublicEntry`
- `load_v2_api_key_config(storage_manager)`
- `list_v2_api_key_entries(storage_manager)`
- `upsert_v2_api_key_entry(storage_manager, key, permission_group)`
- `delete_v2_api_key_entry(storage_manager, key_fingerprint)`
- `V2MainSecurityMiddleware`
- `V2LoopbackOnlyMiddleware`

## Permission Rules

| Permission Group | Allowed Surface | Forbidden Surface |
| --- | --- | --- |
| `operator` | session control, runtime tick, aftertalk toggle, manual close, diagnostics | raw secrets, raw hidden prompt |
| `display` | display stream, display-safe assets/metadata | runtime tick, manual close, aftertalk toggle, operator diagnostics |
| `observer` | read-only status and redacted diagnostics | runtime tick, control endpoints, secret-bearing adapter config |
| `internal` | service-to-service calls with secret boundary references | public response rendering |

All denied requests must fail before Runtime Application Service command dispatch.
When `route_id` is provided, it must map to an allowed action for the resolved permission group.
Display scope 不滿足 observer requirements；它只能讀 display stream 與 display assets。

## API Key Config

Wave 2C 的 API key config 固定讀取 prefs key `youtubebridge_v2_api_keys`：

```json
[
  {"key": "operator-secret", "permission_group": "operator"},
  {"key": "display-secret", "permission_group": "display"},
  {"key": "observer-secret", "permission_group": "observer"}
]
```

2C 只接受 `operator`、`display`、`observer`。空 key、非 list config、無效 permission group 或讀取失敗都採 fail-closed，非 loopback request 會被拒絕。Env var、hybrid source 與 API key 管理 UI 不屬於 2C。

Wave 5D 新增 operator-only API key management surface，仍寫入相同 prefs key。`GET /v2/api-keys`、`POST /v2/api-keys` 與 `DELETE /v2/api-keys/{key_fingerprint}` 只允許 operator context，response 只回傳 `key_fingerprint`、`key_prefix` 與 `permission_group`。Raw key 只存在於 operator submit request 與 prefs storage，不得出現在 response、SSE、UI HTML 或 sanitized diagnostics。

## Failure Modes

- missing API key 回傳 unauthorized，不暴露 expected secret。
- invalid API key 回傳 unauthorized。
- display scope 呼叫 operator action 回傳 forbidden。
- internal scope 呼叫 public operator/display/observer surface 回傳 forbidden。
- untrusted payload 不得進入 logs 的 raw body。
- secret/config 不得出現在 API response、SSE、public trace 或 dataclass serialization。
- MemoriaCore auth delegation 缺失時回傳可診斷但不外洩的錯誤。

## Test Strategy

- auth tests：missing key、invalid key、loopback access。
- permission tests：operator/display/observer/internal scope。
- forbidden tests：display read-only scope 不能控制 runtime。
- API key management tests：operator 可新增/列出/撤銷；observer/display 不能進入管理 surface；response 不回顯 raw key。
- sanitized error tests：錯誤不含 secret、raw payload 或非 allowlisted error code。
- delegation tests：MemoriaCore credential 以 reference 傳遞。
- integration boundary tests：Server/API Surface 不自行複製 security 判斷。

## Open Questions

- API key 到期日、命名 label 與 rotation reminder 尚未設計；目前以 fingerprint revoke 完成撤銷。
- MemoriaCore auth delegation 是否使用使用者 token 或 service token，需與主系統 auth 設計對齊。
