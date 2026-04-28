# 使用者登入系統 — 實作計畫書

> 最後更新：2026-04-28
> 現況：已規劃，待實作

---

## 一、現狀問題

- 所有 API 端點完全無身份驗證，任何能連線的客戶端即可操作全部功能
- `GET /system/config` 暴露 `su_user_id`，攻擊者可冒充超級使用者
- dashboard.html 為零信任 SPA，靜態頁面直接可訪問
- `user_prefs.json` 存放明文 API Key，完全無保護
- 現有 session 可由外部傳入 `session_id` 還原，若未綁定登入者，會形成跨使用者讀取對話與記憶的風險

---

## 二、功能需求

| 頁面 / 功能 | 說明 |
|---|---|
| 登入頁 (`login.html`) | 輸入帳號密碼，失敗顯示錯誤，成功後跳轉 dashboard |
| 註冊頁 (`register.html`) | 輸入帳號 + 密碼 + 密碼確認，建立新帳號 |
| 使用者頁 (`user_profile.html`) | 編輯暱稱、Telegram UID、Discord UID；修改密碼（驗證舊密碼） |
| 資料庫 | `users.db`（與現有 DB 隔離），密碼使用 Argon2 雜湊（內建 salt） |
| Session 驗證 | 登入後以 HttpOnly Cookie 保存 JWT（7 天效期），所有非白名單 `/api/v1/*` 端點需驗證 |
| CSRF 防護 | 所有 mutating endpoint 需帶 CSRF token header |
| 註冊策略 | 第一個註冊帳號自動成為 admin；後續自由註冊為一般 user |
| 權限控管 | `/system/*`、routing、API key、角色設定等敏感端點僅 admin 可用 |
| 暴力登入防護 | 登入端點 5 次失敗鎖定 15 分鐘，計數持久化保存 |

---

## 三、資料庫設計

### 資料庫：`users.db`（位於根目錄，非 static/）

```sql
CREATE TABLE users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT    UNIQUE NOT NULL,
    nickname        TEXT    DEFAULT '',
    password_hash   TEXT    NOT NULL,
    role            TEXT    NOT NULL DEFAULT 'user',
    token_version   INTEGER NOT NULL DEFAULT 0,
    telegram_uid    TEXT    DEFAULT NULL,
    discord_uid     TEXT    DEFAULT NULL,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_users_username ON users(username);
CREATE INDEX idx_users_role ON users(role);
```

第一個註冊成功的帳號自動寫入 `role='admin'`；之後註冊的帳號預設 `role='user'`。

### 登入防護資料表

```sql
CREATE TABLE auth_attempts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT NOT NULL,
    ip_address      TEXT NOT NULL,
    failed_count    INTEGER NOT NULL DEFAULT 0,
    locked_until    TIMESTAMP DEFAULT NULL,
    last_failed_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(username, ip_address)
);

CREATE INDEX idx_auth_attempts_lookup ON auth_attempts(username, ip_address);
```

密碼雜湊：`argon2-cffi` Argon2id，預設參數（memory=64MB，time=3，parallelism=4）

低複雜度防濫用規則：
- 註冊與登入都以 IP 做簡易 rate limit（SQLite 持久化，不引入外部服務）
- 登入失敗訊息統一為「帳號或密碼錯誤」，避免帳號枚舉
- 註冊時 username 僅允許英數、底線、連字號，長度 3-32
- 密碼至少 6 字元，不可與 username 相同，且不可為常見弱密碼清單內字串
- 保留 `registration_enabled` 設定，必要時 admin 可暫停新註冊

---

## 四、API 端點設計

| 端點 | 方法 | 說明 | 需認證 |
|------|------|------|--------|
| `/api/v1/auth/register` | POST | 註冊新帳號（body: username, password, password_confirm） | 否 |
| `/api/v1/auth/login` | POST | 登入（body: username, password），設定 HttpOnly Cookie 並回傳 CSRF token | 否 |
| `/api/v1/auth/logout` | POST | 登出，遞增 `token_version` 並清除 cookie | 是 |
| `/api/v1/auth/me` | GET | 取得目前使用者資料（不含密碼） | 是 |
| `/api/v1/auth/profile` | PUT | 更新暱稱 / Telegram UID / Discord UID | 是 |
| `/api/v1/auth/password` | PUT | 修改密碼（body: old_password, new_password），成功後遞增 `token_version` | 是 |
| `/api/v1/auth/session` | POST | 建立目前登入者擁有的 session_id（銜接現有 session 管理） | 是 |

### JWT Payload

```json
{
  "sub": "<user_id>",
  "username": "<username>",
  "role": "admin|user",
  "ver": <token_version>,
  "exp": <exp_timestamp>,
  "iat": <iat_timestamp>
}
```

Algorithm: HS256。金鑰優先讀取環境變數 `MEMORIACORE_JWT_SECRET`；開發模式若未設定，可自動產生到非 `static/` 的本機 secret file。禁止把 JWT secret 存入 `user_prefs.json` 或版本控制。

### 公開端點白名單

預設所有 `/api/v1/*` 都需要認證，僅以下端點例外：
- `GET /api/v1/health`
- `POST /api/v1/auth/login`
- `POST /api/v1/auth/register`

### 身份與 Session 綁定

- 登入後的 MemoriaCore `user_id` 一律使用 `users.id`（JWT `sub`），不再信任 request body、query string 或 WebSocket frame 中的 `user_id`
- 建立 conversation session 時必須寫入 owner user id
- 還原既有 session 時必須檢查 session owner 等於目前登入者，否則回 403
- WebSocket `/api/v1/chat/stream` 連線時必須驗證 cookie；帶入的 `session_id` 也必須做 owner check
- SSE `/api/v1/chat/stream-sync` 走同一套 current user 驗證
- `role='admin'` 才能操作 `/system/*`、routing config、API key、Telegram token、角色設定與 PersonaSync 手動觸發

---

## 五、前端頁面

| 檔案 | 功能 |
|------|------|
| `static/login.html` | 登入表單，失敗 toast，成功後 `window.location = '/static/dashboard.html'` |
| `static/register.html` | 註冊表單（帳號 / 密碼 / 密碼確認），成功後直接登入並跳轉 dashboard |
| `static/user_profile.html` | 個人設定（暱稱、Telegram UID、Discord UID 修改 + 密碼修改） |

### 登入狀態控管（`common.js` 更新）

```javascript
// JWT 存於 HttpOnly Cookie，前端不可讀；mutating request 需附 CSRF token
const CSRF_KEY = 'mc_csrf';

const originalFetch = window.fetch;
window.fetch = function(url, opts = {}) {
  opts.credentials = opts.credentials || 'same-origin';
  const method = (opts.method || 'GET').toUpperCase();
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
    const csrf = sessionStorage.getItem(CSRF_KEY);
    opts.headers = opts.headers || {};
    if (csrf && !opts.headers['X-CSRF-Token']) {
      opts.headers['X-CSRF-Token'] = csrf;
    }
  }
  return originalFetch(url, opts);
};

// fetch wrapper 應在 response.status === 401 時 redirect 登入頁
```

### Dashboard 登入檢查

`dashboard.html` 初始化時呼叫 `/api/v1/auth/me`。若回 401，跳轉 `login.html`；若回 403，顯示權限不足。

---

## 六、程式碼變更

### 新增檔案

| 檔案 | 說明 |
|------|------|
| `api/middleware/auth.py` | AuthMiddleware — JWT 解析，附加 user 到 request state |
| `api/routers/auth.py` | 所有認證端點（register/login/me/password/profile） |
| `static/login.html` | 登入頁 |
| `static/register.html` | 註冊頁 |
| `static/user_profile.html` | 個人設定頁 |

### 修改檔案

| 檔案 | 變更 |
|------|------|
| `core/storage_manager.py` | 新增 `SECTION: Users DB`，實作使用者 CRUD |
| `static/shared/common.js` | 加入 Token 自動附加、401 redirect 邏輯 |
| `static/dashboard.html` | 初始化時檢查 token，無則 redirect |
| `api/dependencies.py` | 加入 auth middleware 初始化、get_current_user() |
| `api/main.py` | 掛載 auth router、註冊 AuthMiddleware |
| `api/session_manager.py` | session 建立 / 還原時綁定 authenticated user owner |
| `api/routers/chat_ws.py` | WebSocket 握手驗證與 session owner 檢查 |
| `api/routers/chat_rest.py` | REST / SSE 對話端點使用 current user 建立 session |
| `docs/SECURITY.md` | 更新登入系統、Cookie、CSRF、admin 權限與 CORS 部署指引 |

---

## 七、安全性對照表

| 威脅 | 緩解方式 |
|------|----------|
| 密碼盜取 | Argon2id 雜湊（記憶/時間成本高），每使用者獨立 salt |
| 彩虹表 | Argon2 內建 salt，不可能使用預先計算的雜湊表 |
| JWT 盜用 | JWT 存入 HttpOnly + Secure Cookie，前端 JavaScript 不可直接讀取 |
| CSRF | SameSite Cookie + mutating endpoint 強制 `X-CSRF-Token` |
| XSS | 所有輸出 escapeHtml()，新增 CSP header，禁止 inline script 作為後續強化目標 |
| SQL Injection | 全部參數化查詢（StorageManager 既有模式） |
| 暴力破解 | 登入端點 5 次失敗鎖 15 分鐘，username + IP 計數持久化 |
| 註冊濫用 | IP rate limit、username 格式限制、可由 admin 暫停註冊 |
| 密碼太弱 | 至少 6 字元，不可與 username 相同，不可使用常見弱密碼 |
| JWT 太久 | 效期 7 天，過期需重新登入 |
| Token 撤銷 | `token_version` 寫入 JWT，logout / 改密碼後舊 token 立即失效 |
| 權限提升 | 第一個帳號為 admin；敏感端點需 admin role；一般 user 不可讀寫系統設定 |
| Session 竊用 | session owner 必須等於 current user，否則 403 |
| CORS 誤設 | 改為可設定 allowlist，禁止公開部署時使用 `allow_origins=["*"]` |
| 傳輸洩漏 | HTTPS 強制（配合 SECURITY.md TLS 指引） |

---

## 八、驗證測試

1. 啟動 FastAPI，訪問 `/static/login.html`
2. 嘗試直接呼叫 `GET /api/v1/memory/blocks` → 預期 401 Unauthorized
3. 填寫註冊表單，確認 `users.db` 中密碼欄位為非明文雜湊
4. 登入，確認設定 HttpOnly Cookie，dashboard 正常載入
5. 嘗試連續 5 次錯誤密碼，確認第 6 次被鎖
6. 更新暱稱 / Telegram UID，確認寫入成功
7. 修改密碼（驗證舊密碼正確性）
8. 登出，確認舊 JWT 因 `token_version` 失效（仍可訪問靜態頁面，但 API 401）
9. A 使用者嘗試使用 B 的 `session_id` 還原對話 → 預期 403
10. 未登入 WebSocket `/api/v1/chat/stream` → 預期拒絕連線
11. 一般 user 呼叫 `/api/v1/system/config` PUT → 預期 403
12. 缺少 `X-CSRF-Token` 的 mutating request → 預期 403
13. 第一個註冊帳號 role 為 admin，第二個註冊帳號 role 為 user

---

## 九、相關文件

- `docs/SECURITY.md` — 安全部署指引（需配合更新，加入本系統的相關說明）
- `static/shared/theme.css` — 已有的 dark theme，可直接複用於新頁面
- `static/shared/common.js` — 共用工具函式（escapeHtml、toast）
