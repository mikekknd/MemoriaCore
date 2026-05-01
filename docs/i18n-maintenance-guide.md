# i18n 維護手冊

這份文件是「改到任何 UI 可見文字或文字來源」時的檢查清單。它不是 backlog；不要把已接線範圍、長期待辦或進度紀錄放在這裡。進度請放 `docs/i18n-ready-backlog.md`。

## 何時必讀

只要任務包含以下任一類型，修改前先查本文件：

- 新增、刪除或調整 UI 可見文字
- 修改 HTML、Streamlit 頁面、dashboard iframe、toast、confirm、placeholder、title、button、table column、badge
- 修改會被 UI 顯示的 API metadata，例如 label、description、status text
- 修改 `static/shared/i18n.js`、`core/i18n.py`、`static/locales/*.json`

## 基本原則

- UI locale 只控制介面，不要自動改變 LLM 回覆語言、角色語言、prompt 模板或資料內容。
- `zh-TW` 是 canonical catalog；`en-US` 可先覆蓋已接線頁面與代表性 key。
- 資料內容不翻譯；但「用來當 UI label 的 metadata」要有穩定 i18n key。
- 靜態頁新增可見文字優先走 `static/shared/i18n.js`。
- Streamlit 新增可見文字優先走 `core/i18n.py` 的 `t()`。

## 易漏硬編碼類型

### API metadata 顯示

例：`static/prompts.html` 顯示 `/api/v1/prompts` 回傳的 `label` / `description`。

後端 metadata 可能仍是 canonical 繁中。若該 metadata 是 UI label，前端要用穩定 key 轉成 locale，例如：

```js
MCI18N.t(`prompts.meta.${prompt.key}.label`, {}, prompt.label || prompt.key)
```

### iframe 快取與 parent 頁 src

dashboard iframe 可能持續載入舊 HTML / 舊 `i18n.js`。

修改 iframe 頁或 helper 後，同步檢查：

- `static/dashboard.html` 的 iframe `src` 是否需要版本參數，例如 `?v=i18n-ready-ui`
- 各頁引用 `/static/shared/i18n.js` 是否需要 cache-bust

### `DOMContentLoaded` 時序

iframe 或頁尾 script 可能在 listener 掛上前已完成 `DOMContentLoaded`，導致 `MCI18N.apply()` 沒跑。初始化建議採 readyState-safe pattern：

```js
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initPage);
} else {
  initPage();
}
```

### 未引用 `common.js` 的靜態工具頁

`static/shared/i18n.js` 需要 API base。若頁面未載入 `common.js`，要確認 helper 會 fallback 到 `/api/v1`。

例：`static/db_viewer.html` 曾因沒有 `common.js` 而讀不到 `/system/ui-locale`，結果 fallback 成 `zh-TW`。

### shared script 全域命名衝突

多數靜態頁會同時載入 `static/shared/common.js` 與頁面自己的 inline script。`common.js` 已宣告全域 `const API = '/api/v1'`，頁面 script 不要再次宣告同名 top-level `const API`。

若需要頁內 API base，使用頁面專用名稱，例如：

```js
const API_BASE = '/api/v1';
```

例：`static/persona_tree.html` 曾因重複宣告 `const API` 造成 `SyntaxError: Identifier 'API' has already been declared`，頁面 script 中斷後 `MCI18N.apply()` 沒有跑，畫面就保留繁中 fallback。

### `data-i18n` fallback 文字

有 `data-i18n` 的元素仍會先顯示 HTML 內的 fallback 文字，等 catalog 載入後才替換。若 fallback 使用繁中，在英文介面載入前或 script 中斷時會看起來像未翻譯殘留。

對已接線的靜態頁，建議 fallback 使用英文或中性文字；繁中顯示交給 `zh-TW` catalog。特別檢查 dashboard tab、app nav、iframe 入口按鈕這類第一眼可見文字。

### 動態產生的 HTML

`data-i18n` 只處理靜態 DOM。JS 動態產生的 `innerHTML`、template literal、table row、toast、confirm 都要手動用 `MCI18N.t()`。

例：`persona_tree.html` 的節點詳情、狀態訊息、版本切換提示。

動態 HTML 內插文字時要 escape。

### `<select><option>`、placeholder、title

`data-i18n` 不會自動覆蓋未標記的 option，也不會處理 placeholder/title。

檢查：

- `data-i18n-placeholder`
- `data-i18n-title`
- option text
- icon button tooltip

例：Persona 頁的 `Face:`、`public/private`。

### 狀態 tag / badge / table column / action text

這些常藏在 JS template string：

- `default/custom`
- `enabled/disabled`
- `Revoke login`
- `Reset password`
- `Delete`
- table header / cell label

### zh-TW catalog 也要檢查英文殘留

`zh-TW` 是 canonical catalog，但不代表可以保留所有英文 fallback。新增或補接頁面時，除了確認 `en-US` 有 key，也要掃 `static/locales/zh-TW.json` 內同頁面的 UI key 是否仍是英文。

可以保留英文的通常是產品名、協定名、縮寫、技術工具名或資料內容，例如 `LLM`、`DB`、`JSONL`、`SQLite`、`Bot`、log category 原始值。一般操作或導覽文字應翻成繁中，例如：

- `profile` → `個人設定`
- `logout` → `登出`
- `routing` → `路由`
- `side by side` → `並排顯示`
- `Log only` → `只看日誌`

### Log / status 類型 badge

Log Viewer 這類工具常同時有 filter button、統計 badge、entry type badge 三種相似文字。補 i18n 時不要只改上方 filter，還要檢查動態統計和每筆 entry 的 type label。

例：`static/log_viewer.html` 曾出現 filter 已翻譯，但統計或 entry badge 仍顯示 `Event` / `Error` / `EVENT` / `ERROR`。這類文字應走穩定 key，例如 `log_viewer.filter.event` 與 `log_viewer.type.event`。

### 未登入入口的 locale 來源

登入前通常拿不到需要認證的 `user_prefs` 或 `/system/config`，不可直接固定使用 `DEFAULT_LOCALE`。若頁面在未登入狀態也要顯示 i18n 文案，應讀公開的 `/api/v1/system/ui-locale`，失敗時才 fallback `zh-TW`。

例：`app.py` 的 Streamlit `_render_login()` 曾固定 `DEFAULT_LOCALE`，導致系統語系已切到 `en-US` 時，Streamlit 登入頁仍永遠顯示繁中。

### Browser Use 驗證流程

dashboard/chat 頁可能因 current session 或 confirm dialog 卡住新分頁初始化。

較乾淨的檢查方式：

- 直接開目標 iframe 頁，例如 `/static/prompts.html?v=i18n-ready-ui`
- 直接開 `/static/persona_tree.html?v=i18n-ready-ui`
- 需要 dashboard 整合時再開 `/static/dashboard.html?v=i18n-ready-ui`

若用 dashboard 測，避免從 chat/app 頁帶著未處理 confirm 直接 `goto()`。

## 修改後檢查

至少跑：

```powershell
python -m json.tool static\locales\zh-TW.json > $null
python -m json.tool static\locales\en-US.json > $null
python -m pytest tests\test_i18n.py
```

若改靜態 HTML inline script，另做 JS syntax check。若改 Streamlit Python，跑 `python -m py_compile`。
