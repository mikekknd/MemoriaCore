# i18n-ready 後續參考清單

MemoriaCore 目前已建立 v1 i18n-ready 基礎：

- `user_prefs.ui_locale`
- `core/i18n.py`
- `static/shared/i18n.js`
- `static/locales/zh-TW.json`
- `static/locales/en-US.json`

已接線範圍：

- `static/app.html`
- `static/dashboard.html`
- `static/chat.html`
- `static/routing.html`
- `static/login.html`
- `static/register.html`
- `static/prompts.html`
- `static/bots.html`
- `static/users.html`
- `static/user_profile.html`
- `static/persona_tree.html`
- `static/log_viewer.html`
- `static/db_viewer.html`
- `app.py`（Streamlit 入口 / sidebar / login）
- `ui/settings.py`
- `ui/routing.py`
- `ui/bots.py`
- `ui/prompts.py`
- `ui/log_viewer.py`
- `ui/db_manager.py`

後續 Agent 若接續多語系工作，請以以下 backlog 為準。

若任務會修改任何 UI 可見文字或文字來源，先查 `docs/i18n-maintenance-guide.md`。該文件是維護檢查清單；本文件只保留進度與待辦。

## Backlog

1. 補齊 Streamlit 尚未接線頁面

   - `ui/character.py`
   - `ui/settings.py` 目前只完成入口語言 selector 與部分頁面，設定表單大量 label/help/caption 仍待抽 key。
   - `ui/db_manager.py` 的「開發者工具 / 模擬資料生成器」仍有硬編碼繁中。預設測試主題屬於測試資料內容，可保留或另行決定。
   - `ui/log_viewer.py` 的 category label 仍使用 `CATEGORY_META` 內繁中，可改為 `log_viewer.category.*`。
   - `ui/chat.py` / `ui/history.py` 已 deprecated，依 AGENTS.md 不要再參考。

2. API 錯誤訊息 i18n
   - 目前許多 `ValueError`、auth error、router error 仍是硬編碼繁中。
   - 後續可建立 `error.code -> localized message` 機制。

3. PersonaProbe i18n
   - `PersonaProbe/app.py` 有大量 Streamlit UI 文字。
   - 建議獨立建立 PersonaProbe catalog，或明確決定是否共用主專案 catalog。

4. Prompt / LLM 語言策略
   - 目前刻意不動 `prompts_default.json`。
   - 後續可分成「UI locale」與「assistant reply language」兩個設定，避免介面語言影響角色回覆語言。

5. 使用者層級 locale
   - 目前是 `user_prefs.ui_locale` 全域設定。
   - 多使用者部署時，可改為 user profile 欄位，例如 `users.ui_locale`。

6. 前端語言切換體驗
   - 目前切換後需要重新整理頁面。
   - 目前 dashboard 內每個 iframe（chat / routing / db_viewer / persona_tree …）各自呼叫一次 `MCI18N.init()` 並重複 fetch `/system/config` 或 `/system/ui-locale`，造成 N 個 iframe = N 次 API。
   - 後續可由 parent 頁面取得一次後透過 `postMessage` 廣播 locale 給 iframe，並順便支援即時切換。

7. 瀏覽器實測
   - 目前已有 JSON、Python、pytest、JS syntax 驗證。
   - 後續完整 i18n PR 應用瀏覽器確認 `zh-TW` / `en-US` 切換後各 iframe 顯示狀態。

## 維護提醒

- 新增 routing 任務時，需同步更新 `static/shared/routing_config.js` 的 `TASK_KEYS` 與 `static/locales/*.json` 的 `routing.tasks.*` key。
- 新增可見 UI 文字時，優先走 `static/shared/i18n.js` 或 `core/i18n.py`，不要新增硬編碼文字。
- 不要讓 UI locale 自動改變 LLM 回覆語言；若要支援回覆語言，應另設獨立設定。
- 若新增由 API 回傳但當 UI label 使用的 metadata，請同步建立穩定 i18n key，不要直接顯示 canonical 繁中。
- i18n 維護檢查點請集中更新 `docs/i18n-maintenance-guide.md`，避免 backlog 變成長上下文手冊。
