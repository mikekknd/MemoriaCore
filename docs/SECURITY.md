# MemoriaCore 正式上線安全指引

> 適用版本：MemoriaCore（FastAPI port 8088 + dashboard.html）  
> 最後更新：2026-04-27

---

## 1. 風險概覽

| 項目 | 嚴重度 | 說明 |
|---|---|---|
| `GET /system/config` 暴露 `su_user_id` | **高** | 任何能連線的用戶皆可取得 SU ID 並冒充 |
| FastAPI 無內建驗證層 | **高** | 所有 API 端點預設無 auth token |
| SQLite 直接位於專案目錄 | 中 | DB 檔若被直接讀取，所有記憶明文可見 |
| LLM API Key 存於 `user_prefs.json` | 中 | 明文儲存；需限制檔案系統存取權限 |
| Telegram Bot Token 存於 `user_prefs.json` | 中 | 同上 |
| dashboard.html 無 CSRF 防護 | 低 | 僅限信任網路部署時影響有限 |

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

`GET /system/config` 回傳的 `su_user_id` 是識別 SU 身份的唯一憑證。  
任何能取得此值的人都可透過 `POST /session`（帶 `user_id`）冒充 SU 取得 private face。

**短期緩解（不改 code）：**
- 完成 2-1 確保只有 SU 能連線即可；`su_user_id` 暴露在 response 中屬已知設計，前提是網路已隔離。

**中期加固（建議實作）：**
- 將 `GET /system/config` 的 response 中 `su_user_id` 改為遮罩輸出（`"su_user_id": "****"`），僅 `PUT /system/config` 允許寫入完整值。
- 或為 `/system/*` 端點加上 `X-Admin-Token` Header 驗證（環境變數注入）。

---

### 2-3. API Key / Secret 檔案權限

`user_prefs.json` 存放 OpenAI key、Telegram token 等敏感資訊，需限制讀取權限：

**Linux / macOS：**
```bash
chmod 600 user_prefs.json
chown <service_user> user_prefs.json
```

**Windows：**
- 右鍵 `user_prefs.json` → 內容 → 安全性
- 移除 Everyone / Users 的讀取權，僅保留執行服務的帳號

---

### 2-4. SQLite 資料庫保護

`conversation.db`、`ai_memory.db` 存放所有對話與記憶明文。

- 確保 DB 檔案不在 web server 靜態目錄內（目前位於根目錄，已符合）
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

- `telegram_bot_token` 不可寫入版本控制（已在 `.gitignore` 確認）
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

## 4. 部署前確認清單

```
[ ] port 8088 已透過防火牆封鎖外部直接存取
[ ] dashboard 已加 Basic Auth 或 VPN 保護
[ ] HTTPS 已啟用（TLS 憑證有效）
[ ] user_prefs.json 檔案權限已收緊（非 service 帳號不可讀）
[ ] su_user_id 已設定（非空字串）
[ ] Telegram bot token 未出現在任何 git commit 中
[ ] SQLite DB 目錄不在靜態資源路徑下
[ ] 已測試：未授權 IP 無法連線到 API
[ ] 已測試：SU user_id 的 session 確實回傳 private face 內容
```

---

## 5. 聯絡與回報

發現安全疑慮請直接在 private channel 通知專案維護者，勿在公開 issue 中揭露細節。
