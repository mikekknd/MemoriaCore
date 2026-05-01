# i18n-ready 後續參考清單

MemoriaCore 目前已建立 v1 i18n-ready 基礎：

- `user_prefs.ui_locale`
- `core/i18n.py`
- `static/shared/i18n.js`
- `static/locales/zh-TW.json`
- `static/locales/en-US.json`

第一批已接線範圍：

- `static/app.html`
- `static/dashboard.html`
- `static/chat.html`
- `static/routing.html`
- `ui/settings.py`
- `ui/routing.py`

後續 Agent 若接續多語系工作，請以以下 backlog 為準。

## Backlog

1. 補齊靜態 HTML 頁面
   - `login.html`
   - `register.html`
   - `prompts.html`
   - `bots.html`
   - `users.html`
   - `user_profile.html`
   - `persona_tree.html`
   - `log_viewer.html`
   - `db_viewer.html`

2. 補齊 Streamlit 頁面
   - `ui/character.py`
   - `ui/bots.py`
   - `ui/prompts.py`
   - `ui/log_viewer.py`
   - `ui/db_manager.py`

3. API 錯誤訊息 i18n
   - 目前許多 `ValueError`、auth error、router error 仍是硬編碼繁中。
   - 後續可建立 `error.code -> localized message` 機制。

4. PersonaProbe i18n
   - `PersonaProbe/app.py` 有大量 Streamlit UI 文字。
   - 建議獨立建立 PersonaProbe catalog，或明確決定是否共用主專案 catalog。

5. Prompt / LLM 語言策略
   - 目前刻意不動 `prompts_default.json`。
   - 後續可分成「UI locale」與「assistant reply language」兩個設定，避免介面語言影響角色回覆語言。

6. 使用者層級 locale
   - 目前是 `user_prefs.ui_locale` 全域設定。
   - 多使用者部署時，可改為 user profile 欄位，例如 `users.ui_locale`。

7. 前端語言切換體驗
   - 目前切換後需要重新整理頁面。
   - 目前 dashboard 內每個 iframe（chat / routing / db_viewer / persona_tree …）各自呼叫一次 `MCI18N.init()` 並重複 fetch `/system/config` 或 `/system/ui-locale`，造成 N 個 iframe = N 次 API。
   - 後續可由 parent 頁面取得一次後透過 `postMessage` 廣播 locale 給 iframe，並順便支援即時切換。

8. 瀏覽器實測
   - 目前已有 JSON、Python、pytest、JS syntax 驗證。
   - 後續完整 i18n PR 應用瀏覽器確認 `zh-TW` / `en-US` 切換後各 iframe 顯示狀態。

## 維護提醒

- 新增 routing 任務時，需同步更新 `static/shared/routing_config.js` 的 `TASK_KEYS` 與 `static/locales/*.json` 的 `routing.tasks.*` key。
- 新增可見 UI 文字時，優先走 `static/shared/i18n.js` 或 `core/i18n.py`，不要新增硬編碼文字。
- 不要讓 UI locale 自動改變 LLM 回覆語言；若要支援回覆語言，應另設獨立設定。
