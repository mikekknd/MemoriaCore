# UI 與體驗優化 (UI & UX) 修正計畫

## 1. Admin Bypass 登入開關

**目標**：在設定介面提供 Bypass 選項，啟用後可直接一鍵登入 Streamlit 管理介面，無需重複輸入帳密。

### 實作步驟：
1. **更新系統設定 UI (`ui/settings.py`)**：
   - 在「SU 身份設定」區塊中新增一個 checkbox：`admin_bypass_enabled`（預設為 `False`）。
   - 將設定值存入 `user_prefs` 並透過 `/system/config` 儲存。

2. **新增 Bypass Auth 路由 (`api/routers/auth.py` & `api/middleware/auth.py`)**：
   - 在 `api/middleware/auth.py` 的 `PUBLIC_ROUTES` 中新增 `("POST", "/api/v1/auth/bypass")` 以開放存取。
   - 在 `api/routers/auth.py` 新增 `POST /auth/bypass` 端點：
     - 檢查 `user_prefs.get("admin_bypass_enabled")` 是否為 `True`。
     - 若為 `True`，從 DB 取得第一個 role 為 `admin` 的使用者（或特定 ID），並像 `login` 端點一樣簽發 JWT Token 與 Cookie。
     - 若為 `False`，回傳 403 Forbidden。

3. **更新 Streamlit 登入介面 (`app.py`)**：
   - 在 `_render_login()` 表單下方加入「⚡ 一鍵免密碼登入 (Admin Bypass)」按鈕。
   - 點擊後呼叫 `/auth/bypass`：若成功則更新 `st.session_state` 進入後台；若失敗則提示「未啟用此功能」。

---

## 2. 對話介面識別 (`chat.html`)

**目標**：在多重角色或切換角色的對話中，能清楚辨識當前正在發言的 AI 名稱，以利在多角色情境下的視覺辨識。

### 實作步驟：
1. **資料庫 Schema 擴充 (`core/storage_manager.py`)**：
   - 於 `_init_conversation_db()` 增加自動 Migration 邏輯：檢查 `conversation_messages` 資料表，若缺少 `character_name` 欄位則透過 `ALTER TABLE` 加入 (`character_name TEXT`)。
   - 更新 `save_conversation_message` 方法，使其能接收並儲存 `character_name` 欄位。
   - 更新 `load_conversation_messages` 方法，確保能正確讀取並回傳該欄位。

2. **狀態管理器與 DTO 更新 (`api/session_manager.py` & `api/models/responses.py`)**：
   - 在 `SessionMessageDTO` 中新增 `character_name: Optional[str] = None`。
   - 修改 `SessionManager.add_assistant_message()`，增加 `character_name` 參數，並同步更新記憶體中的 `messages` 陣列與資料庫。

3. **後端編排層傳遞 (`api/routers/chat_rest.py` & `core/chat_orchestrator/*`)**：
   - 從 `get_character_manager().get_active_character()` 或 `ChatContext` 獲取當前 AI 的 `name`。
   - 在產生回應時（包括單層、雙層架構），將 `character_name` 傳遞給 `add_assistant_message`。
   - 在 Server-Sent Events (SSE) 的 `result` 事件中，加入 `character_name` 欄位，送交前端。

4. **前端渲染更新 (`static/chat.html`)**：
   - 新增 CSS 樣式 `.msg-author`，以小字體、不同顏色顯示在 assistant 訊息氣泡的上方。
   - 修改 SSE `result` 事件處理邏輯，將收到的 `character_name` 交給 `appendMessage`。
   - 修改 `appendMessage()` 與歷史紀錄渲染邏輯：若是 assistant 發送的訊息且有 `character_name`，則在氣泡 DOM 結構內動態插入發言者名稱標籤。
