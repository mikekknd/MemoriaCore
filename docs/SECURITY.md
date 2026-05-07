# MemoriaCore 正式上線安全指引

> 適用版本：MemoriaCore（FastAPI port 8088 + dashboard.html）  
> 最後更新：2026-05-07

---

## 1. 風險概覽

| 項目 | 嚴重度 | 說明 |
|---|---|---|
| 管理員帳號或 Cookie 外洩 | **高** | `/system/config`、Prompt、角色、Log 等管理端點依賴 admin 身份保護 |
| JWT/CSRF/CORS 設定錯誤 | **高** | `MEMORIACORE_JWT_SECRET`、`MEMORIACORE_COOKIE_SECURE`、`MEMORIACORE_CORS_ORIGINS` 設錯會削弱登入保護 |
| runtime SQLite 明文資料 | 中 | `runtime/*.db` 存放對話、記憶、使用者與人格資料，需限制檔案系統權限 |
| LLM API Key 存於 `runtime/user_prefs.json` | 中 | 明文儲存；需限制檔案系統存取權限 |
| Bot Token 存於 `runtime/bot_configs.json` / legacy prefs | 中 | 明文儲存；需限制檔案系統存取權限 |
| dashboard.html 暴露於不可信網路 | 中 | 即使有登入，也應搭配 HTTPS、VPN / reverse proxy 與防火牆 |

---

## 2. 必做項目（上線前全部完成）

### 2-1. 網路層隔離（最高優先）

**目標：只讓 SU 本人能連線到 port 8088 與 dashboard**

**方案 A — 僅 localhost（最嚴格）**
```
# Windows 防火牆：封鎖外部對 8088 的入站
netsh advfirewall firewall add rule `
  name="MemoriaCore Block External" `
  dir=in action=block `
  protocol=TCP localport=8088 `
  remoteip=!127.0.0.1
```
SU 透過 SSH tunnel 遠端存取：
```
ssh -L 8088:127.0.0.1:8088 user@server
```

**方案 B — 限定內網 IP（多人信任網路）**  
在 FastAPI 啟動時綁定特定介面：
```bash
uvicorn main:app --host 192.168.1.x --port 8088
```
再於路由器 / 防火牆設定只允許特定 IP 段存取該 port。

**方案 C — Reverse Proxy + Basic Auth（對外服務必選）**  
以 nginx 為例：
```nginx
server {
    listen 443 ssl;
    server_name your.domain;

    # SSL 憑證
    ssl_certificate     /path/to/fullchain.pem;
    ssl_certificate_key /path/to/privkey.pem;

    location / {
        # Basic Auth 擋在最前面
        auth_basic "MemoriaCore";
        auth_basic_user_file /etc/nginx/.htpasswd;

        proxy_pass http://127.0.0.1:8088;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;

        # WebSocket 支援
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```
產生密碼檔：
```bash
htpasswd -c /etc/nginx/.htpasswd su_username
```

---

### 2-2. SU User ID 保護

`GET /system/config` 僅 admin 可讀，但其中仍包含 `su_user_id`、API key 狀態與多項高權限設定。
任何取得 admin Cookie / CSRF token 的人都可操作管理端點，因此 admin 瀏覽器 session 必須視為高敏感憑證。

**短期緩解（不改 code）：**
- 完成 2-1 確保只有可信網路能連線；對外部署時使用 HTTPS、Secure Cookie 與明確 CORS allowlist。

**中期加固（建議實作）：**
- 將 `GET /system/config` response 中的 secret 類欄位遮罩輸出，僅 `PUT /system/config` 允許寫入完整值。
- 對高風險管理操作保留 CSRF 檢查，並維持 admin role gate。

---

### 2-3. API Key / Secret 檔案權限

`runtime/user_prefs.json` 存放 OpenAI/OpenRouter/Tavily/OpenWeather 等 key；`runtime/bot_configs.json` 存放 Bot token。兩者都需限制讀取權限：

**Linux / macOS：**
```bash
chmod 600 runtime/user_prefs.json runtime/bot_configs.json
chown <service_user> runtime/user_prefs.json runtime/bot_configs.json
```

**Windows：**
- 右鍵 `runtime/user_prefs.json` 與 `runtime/bot_configs.json` → 內容 → 安全性
- 移除 Everyone / Users 的讀取權，僅保留執行服務的帳號

---

### 2-4. SQLite 資料庫保護

`runtime/conversation.db`、`runtime/memory_db_*.db`、`runtime/users.db`、`runtime/persona_snapshots.db` 存放對話、記憶、使用者與人格資料明文。

- 確保 `runtime/` 不在 web server 靜態目錄內；FastAPI 只掛載 `static/`
- 限制 `runtime/` 讀寫權限，只允許執行服務的帳號存取
- 定期備份並加密後傳至離線儲存
- 若需更高安全性，考慮改用 PostgreSQL + 資料庫層加密

---

### 2-5. HTTPS 強制

不論使用何種部署方式，對外服務必須啟用 TLS：

- 使用 Let's Encrypt（`certbot`）取得免費憑證
- 或自簽憑證（僅限內部信任環境）
- **禁止以明文 HTTP 對外暴露任何端點**（API Key 會在傳輸中裸奔）

---

### 2-6. Telegram Bot 安全

- `bot_configs.json` 與 legacy `telegram_bot_token` 不可寫入版本控制（已在 `.gitignore` 確認）
- 建議在 BotFather 設定 `setPrivacy`：只回應直接訊息，不接受加入群組
- 定期到 BotFather 執行 `revoke` 輪換 token

---

## 3. 建議但非強制項目

| 項目 | 說明 |
|---|---|
| Rate limiting | nginx `limit_req_zone` 防止暴力探測 |
| 請求 logging | 記錄所有 `/system/config` PUT 操作，供稽核追蹤 |
| Fail2ban | 多次 Basic Auth 失敗自動封鎖 IP |
| Docker 容器化 | 以 non-root user 執行，隔離檔案系統 |
| 定期輪換 `su_user_id` | 若使用 Telegram UID 作為 SU ID，本身不可輪換；若自訂字串需定期更換 |

---

## 4. 內建登入系統部署注意

登入系統啟用後，API 預設由 HttpOnly Cookie + CSRF token 保護：

- 第一個註冊帳號會自動成為 admin；後續帳號預設為 user
- `MEMORIACORE_JWT_SECRET` 應在正式環境以環境變數提供，長度至少 32 bytes
- 對外 HTTPS 部署時設定 `MEMORIACORE_COOKIE_SECURE=1`
- `MEMORIACORE_CORS_ORIGINS` 必須明列允許來源，禁止公開部署時使用萬用字元
- 登出與修改密碼會遞增 `token_version`，讓既有 JWT 立即失效
- `/system/*`、`/prompts/*`、`/character/*` 等敏感 API 僅 admin 可用
- 對話 session 會綁定 owner user id，不能用他人的 `session_id` 還原內容
- 若需要暫停自由註冊，可在 `user_prefs.json` 設定 `registration_enabled=false`

---

## 5. 部署前確認清單

```
[ ] port 8088 已透過防火牆封鎖外部直接存取
[ ] dashboard 已加 Basic Auth 或 VPN 保護
[ ] HTTPS 已啟用（TLS 憑證有效）
[ ] MEMORIACORE_JWT_SECRET 已設定且未寫入版本控制
[ ] 對外 HTTPS 部署時 MEMORIACORE_COOKIE_SECURE=1
[ ] MEMORIACORE_CORS_ORIGINS 已收斂到可信來源
[ ] user_prefs.json / bot_configs.json 檔案權限已收緊（非 service 帳號不可讀）
[ ] su_user_id 已設定（非空字串）
[ ] Telegram bot token 未出現在任何 git commit 中
[ ] SQLite DB 目錄不在靜態資源路徑下
[ ] 已測試：未授權 IP 無法連線到 API
[ ] 已測試：SU user_id 的 session 確實回傳 private face 內容
```

---

## 6. 聯絡與回報

發現安全疑慮請直接在 private channel 通知專案維護者，勿在公開 issue 中揭露細節。
