# YouTubeBridge Index Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 將 `YouTubeBridge/static/index.html` 拆成可維護的 HTML、CSS 與原生 ES module JS 檔，同時保留現有 `/ui/` 行為。

**Architecture:** 保持純靜態前端，不引入 bundler。`index.html` 保留 DOM markup，CSS 搬到 `static/ui/index.css`，控制邏輯依責任切成 `static/ui/*.js`，並由 `static/ui/app.js` 初始化。`server.py` 新增 loopback-only `/ui-assets/{path:path}` 供 `/ui/` 載入拆出的資源。

**Tech Stack:** FastAPI `FileResponse`、原生 ES modules、pytest string-contract tests。

---

### Task 1: 測試契約與測試 helper

**Files:**
- Modify: `YouTubeBridge/tests/test_server_auth.py`

- [x] **Step 1: 新增 UI source helper**

在 `test_server_auth.py` 中新增 helper，讓既有 assertions 可以搜尋 `index.html` 與拆出的 `static/ui/*.js`、`static/ui/*.css`：

```python
def _control_ui_source() -> str:
    static_root = Path(server_module.STATIC_ROOT)
    parts = [(static_root / "index.html").read_text(encoding="utf-8")]
    ui_root = static_root / "ui"
    if ui_root.exists():
        for name in ("index.css", "core.js", "selectors.js", "topic-packs.js", "control.js", "app.js"):
            path = ui_root / name
            if path.exists():
                parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)
```

把所有 `index_html = (... / "index.html").read_text(...)` 且搜尋 JS/CSS 行為的測試改成 `index_html = _control_ui_source()`；只檢查 HTML 結構區塊的測試可維持讀 `index.html`。

- [x] **Step 2: 新增 red 測試**

新增兩個測試：

```python
def test_control_ui_loads_external_css_and_module_script():
    index_html = (Path(server_module.STATIC_ROOT) / "index.html").read_text(encoding="utf-8")

    assert '<link rel="stylesheet" href="/ui-assets/index.css">' in index_html
    assert '<script type="module" src="/ui-assets/app.js"></script>' in index_html
    assert "<style>" not in index_html
    assert "<script>\n" not in index_html


def test_ui_assets_bypass_key_only_for_loopback(monkeypatch):
    monkeypatch.setenv("YOUTUBE_BRIDGE_API_KEY", "secret")

    require_bridge_key(_request("127.0.0.1", path="/ui-assets/app.js"))

    with pytest.raises(HTTPException) as exc:
        require_bridge_key(_request("203.0.113.10", path="/ui-assets/app.js"))

    assert exc.value.status_code == 403
```

- [x] **Step 3: Run red**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_server_auth.py::test_control_ui_loads_external_css_and_module_script YouTubeBridge/tests/test_server_auth.py::test_ui_assets_bypass_key_only_for_loopback -q
```

Expected: both fail because `index.html` still embeds CSS/JS and `server.py` has no `/ui-assets` bypass.

### Task 2: 拆出靜態資源

**Files:**
- Modify: `YouTubeBridge/static/index.html`
- Create: `YouTubeBridge/static/ui/index.css`
- Create: `YouTubeBridge/static/ui/core.js`
- Create: `YouTubeBridge/static/ui/selectors.js`
- Create: `YouTubeBridge/static/ui/topic-packs.js`
- Create: `YouTubeBridge/static/ui/control.js`
- Create: `YouTubeBridge/static/ui/app.js`

- [x] **Step 1: 搬出 CSS**

把 `index.html` 的 `<style>...</style>` 內容移到 `static/ui/index.css`，並在 `<head>` 加入：

```html
<link rel="stylesheet" href="/ui-assets/index.css">
```

- [x] **Step 2: 搬出 JS 並切 module**

以現有函式責任切檔。共享 helper 與 state 只透過明確 import/export 連接，不掛全域：

```javascript
// app.js
import { initBridgeKey, installTestIds, log } from "./core.js";
import { requestedSessionIdFromUrl } from "./selectors.js";
import { loadSessions, refreshEvents } from "./control.js";
import { updateTopicActionVisibility } from "./topic-packs.js";
```

`core.js` 放共用 state、DOM helper、log 與 API wrapper；`selectors.js` 放目前選取 session/topic 的查詢函式；`topic-packs.js` 放 Topic Pack / Fact Card 編輯器；`control.js` 放其餘直播控制、MemoriaCore config、events、summary、director、queue 與 SSE；`app.js` 只負責匯入 module、定義 `refreshAll()`、綁定 DOM events，最後執行初始化。

- [x] **Step 3: 更新 `index.html` script**

把內嵌 `<script>...</script>` 替換為：

```html
<script type="module" src="/ui-assets/app.js"></script>
```

### Task 3: FastAPI 靜態 assets route

**Files:**
- Modify: `YouTubeBridge/server.py`

- [x] **Step 1: 新增 assets root 與安全 path 檢查**

新增：

```python
UI_ASSETS_ROOT = Path(STATIC_ROOT) / "ui"
```

並新增 route：

```python
@app.get("/ui-assets/{asset_path:path}")
async def bridge_ui_asset(asset_path: str):
    resolved = (UI_ASSETS_ROOT / asset_path).resolve()
    try:
        resolved.relative_to(UI_ASSETS_ROOT.resolve())
    except ValueError:
        raise HTTPException(status_code=404, detail="asset not found")
    if not resolved.is_file() or resolved.suffix not in {".css", ".js"}:
        raise HTTPException(status_code=404, detail="asset not found")
    return FileResponse(resolved)
```

- [x] **Step 2: 更新 auth bypass**

讓 `/ui-assets/...` 與 `/ui/` 一樣只允許 loopback bypass：

```python
_UI_ASSET_PATH_RE = re.compile(r"^/ui-assets/.+$")
```

`require_bridge_key()` 的 loopback-only 條件需包含 `_UI_ASSET_PATH_RE.match(path)`。

### Task 4: Verification

**Files:**
- Test only

- [x] **Step 1: 跑 targeted pytest**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_server_auth.py -q
```

Expected: pass。

- [x] **Step 2: 靜態合約檢查**

Run:

```powershell
Select-String -Path YouTubeBridge\static\index.html -Pattern '<style>','<script>','ui-assets'
```

Expected: 只看到 `ui-assets` 與 module script，不再有內嵌 `<style>` 或裸 `<script>`。

- [x] **Step 3: 檢查工作樹**

Run:

```powershell
git status -sb
```

Expected: 只新增/修改本計畫列出的檔案；既有 unrelated 變更仍未被 stage 或改動。
