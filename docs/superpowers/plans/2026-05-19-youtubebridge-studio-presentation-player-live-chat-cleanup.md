# YouTubeBridge Studio Presentation Player Live Chat Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 將 Presentation Player 收斂為 Studio 唯一操作面，移除 Studio 沒有使用的 legacy `/live/` 與 `/live-chat/` UI route/assets，並同步整理文件、launcher 與測試 contract。

**Architecture:** 保留後端 YouTube Live Chat 讀取、`live_chat_id`、SSE、`chat-preview`、presentation queue、ACK、skip、audio route，因為這些是 Studio Presentation Player 的 runtime seam。刪除的是舊瀏覽器 Live Chat UI adapter：`live.html`、`live_chat.html`、`live-chat.js`、`live-chat.css`、`/live/`、`/live-chat/` route 與相關測試。Studio 的 Presentation Player 仍在 `studio.js`，本計畫先用刪除 legacy adapter 換取 locality；不抽新 JS module。

**Tech Stack:** FastAPI route modules, plain HTML/CSS/ES module frontend, pytest static contract tests, Windows batch launchers.

---

## Architecture Review Findings

1. **Legacy Live Chat UI Adapter**
   - **Files:** `YouTubeBridge/static/live.html`, `YouTubeBridge/static/live_chat.html`, `YouTubeBridge/static/ui/live-chat.js`, `YouTubeBridge/static/ui/live-chat.css`, `YouTubeBridge/server_routes/ui.py`, `YouTubeBridge/server.py`, `YouTubeBridge/server_security.py`
   - **Problem:** Studio 已經有自己的 Presentation Player implementation；legacy Live Chat UI 仍保留另一份 queue、audio、ACK、skip、SSE handling。這個 module 變成 shallow adapter：interface 是整個舊頁面與 routes，implementation 又複製 Studio 行為，會造成雙開頁面 ACK race 與維護分叉。
   - **Solution:** 刪掉 legacy UI adapter 與 routes，讓 Presentation Player 的 browser adapter 只剩 Studio。
   - **Benefits:** locality 回到 `studio.js` 與後端 presentation seam；測試只需驗證 Studio adapter，不再維護兩套播放器。

2. **Backend Presentation Queue Seam**
   - **Files:** `YouTubeBridge/bridge_engine.py`, `YouTubeBridge/server_routes/sessions.py`, `YouTubeBridge/storage_repositories/presentation.py`, `YouTubeBridge/tests/test_presentation_queue.py`, `YouTubeBridge/tests/test_studio_ui.py`
   - **Problem:** 名稱裡仍有 "Live Chat" domain，容易誤刪成 backend polling / event ingestion。
   - **Solution:** 明確保留後端 seam：`presentation_item_ready/preload`、audio URL、ACK、skip、`chat-preview`。本次只刪 frontend legacy adapter。
   - **Benefits:** leverage 保留；Studio 不需要重新實作 TTS/presentation runtime，也不破壞 YouTube Live Chat 讀取。

3. **System Entry Point Drift**
   - **Files:** `YouTubeBridge/start.bat`, `YouTubeBridge/start_hot_reload.bat`, `YouTubeBridge/README.md`, `YouTubeBridge/CLAUDE.md`
   - **Problem:** launcher 與 docs 仍把 `/live/` 當入口或列為控制台 surface，跟 Studio-first 現況不一致。
   - **Solution:** launcher 改顯示 `/studio/`，README/CLAUDE 改寫為 Studio primary、`/ui/` legacy control/config surface，移除 `/live/`、`/live-chat/` 畫面入口。
   - **Benefits:** interface 更小，接手者不會從錯的 UI adapter 開始調 Presentation Player。

## Scope Rules

- 不刪除後端 YouTube Live Chat 讀取、`live_chat_id`、YouTube polling、events table、SafetyLLM、Research Gate、external context。
- 不刪除 `/sessions/{session_id}/events` SSE。
- 不刪除 `/sessions/{session_id}/chat-preview`，Studio 仍用它做可見對話 aggregate。
- 不刪除 `/sessions/{session_id}/presentation/{item_id}/audio`、`ack`、`current/skip`，Studio Presentation Player 仍用這些 endpoint。
- 不刪除 `/ui/` 舊控制台；本計畫只移除 `/live/` 與 `/live-chat/` browser playback adapter。

## File Structure

- Delete: `YouTubeBridge/static/live.html`
- Delete: `YouTubeBridge/static/live_chat.html`
- Delete: `YouTubeBridge/static/ui/live-chat.js`
- Delete: `YouTubeBridge/static/ui/live-chat.css`
- Modify: `YouTubeBridge/server_routes/ui.py` removes `bridge_live()` and `bridge_live_chat()`.
- Modify: `YouTubeBridge/server.py` removes route handler exports for deleted UI routes.
- Modify: `YouTubeBridge/server_security.py` removes `/live`, `/live/`, `/live-chat`, `/live-chat/` from loopback-only page allowlist while preserving `/studio`, `/ui`, assets, SSE, and presentation audio rules.
- Modify: `YouTubeBridge/start.bat` prints Studio as the live operator entry.
- Modify: `YouTubeBridge/start_hot_reload.bat` prints Studio as the hot reload entry.
- Modify: `YouTubeBridge/README.md` documents Studio primary UI and `/ui/` legacy/config role.
- Modify: `YouTubeBridge/CLAUDE.md` documents Studio primary UI and removes deleted routes from current architecture notes.
- Modify: `YouTubeBridge/tests/test_control_ui_static_contract.py` removes legacy Live Chat tests and adds removal contract.
- Modify: `YouTubeBridge/tests/test_server_route_split.py` expects only `/studio` and `/ui` UI page routes, not `/live` or `/live-chat`.
- Modify: `YouTubeBridge/tests/test_session_routes.py` removes the `/live/` iframe propagation test.
- Modify: `YouTubeBridge/tests/test_launcher_contract.py` pins launcher output to `/studio/`.
- Modify: `YouTubeBridge/tests/test_server_auth_loopback.py` pins security allowlist no longer includes deleted page routes.
- Modify only if imported helper is still dead after the previous edits: remove unused `_live_chat_source()` helpers from `YouTubeBridge/tests/test_chat_preview_routes.py`, `YouTubeBridge/tests/test_episode_plan_routes.py`, `YouTubeBridge/tests/test_launcher_contract.py`, `YouTubeBridge/tests/test_server_auth.py`, `YouTubeBridge/tests/test_server_auth_loopback.py`, `YouTubeBridge/tests/test_topic_pack_routes.py`.

---

### Task 1: Pin Legacy UI Removal Contract

**Files:**
- Modify: `YouTubeBridge/tests/test_control_ui_static_contract.py`
- Modify: `YouTubeBridge/tests/test_server_route_split.py`
- Modify: `YouTubeBridge/tests/test_session_routes.py`
- Modify: `YouTubeBridge/tests/test_server_auth_loopback.py`

- [ ] **Step 1: Replace live static file presence test with deletion contract**

In `YouTubeBridge/tests/test_control_ui_static_contract.py`, replace `test_live_page_static_files_are_registered()` and delete all `test_live_chat_*` tests that only validate `live_chat.html`, `live-chat.js`, or `live-chat.css`.

Add this test near the existing static UI contract tests:

```python
def test_legacy_live_chat_static_files_are_removed():
    static_root = Path(server_module.STATIC_ROOT)
    ui_root = static_root / "ui"

    assert not (static_root / "live.html").exists()
    assert not (static_root / "live_chat.html").exists()
    assert not (ui_root / "live-chat.js").exists()
    assert not (ui_root / "live-chat.css").exists()
```

- [ ] **Step 2: Run the deletion contract and verify it fails**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_control_ui_static_contract.py::test_legacy_live_chat_static_files_are_removed -q
```

Expected: FAIL because the four legacy static files still exist.

- [ ] **Step 3: Update route registration contract**

In `YouTubeBridge/tests/test_server_route_split.py`, remove these entries from the `expected` set in `test_split_routes_keep_existing_public_paths()`:

```python
"/live",
"/live/",
"/live-chat",
"/live-chat/",
```

Add this test below `test_split_routes_keep_existing_public_paths()`:

```python
def test_legacy_live_chat_routes_are_not_registered():
    paths = _route_paths()

    assert "/live" not in paths
    assert "/live/" not in paths
    assert "/live-chat" not in paths
    assert "/live-chat/" not in paths
    assert "/studio" in paths
    assert "/studio/" in paths
```

- [ ] **Step 4: Run route contract and verify it fails**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_server_route_split.py::test_legacy_live_chat_routes_are_not_registered -q
```

Expected: FAIL because `/live` and `/live-chat` routes are still registered.

- [ ] **Step 5: Remove obsolete live iframe test**

In `YouTubeBridge/tests/test_session_routes.py`, delete this test:

```python
def test_live_page_propagates_requested_session_id_to_live_chat_frame():
    live_html = (Path(server_module.STATIC_ROOT) / "live.html").read_text(encoding="utf-8")

    assert 'id="liveChatFrame"' in live_html
    assert "URLSearchParams(location.search)" in live_html
    assert "session_id" in live_html
```

Add this replacement test near the presentation endpoint contract:

```python
def test_session_routes_keep_backend_presentation_endpoints_for_studio():
    source = (BRIDGE_ROOT / "server_routes" / "sessions.py").read_text(encoding="utf-8")

    assert '@router.post("/sessions/{session_id}/presentation/{item_id}/ack")' in source
    assert '@router.get("/sessions/{session_id}/presentation/{item_id}/audio")' in source
    assert '@router.post("/sessions/{session_id}/presentation/current/skip")' in source
    assert 'api(`/sessions/${encodeURIComponent(state.sessionId)}/presentation/${encodeURIComponent(item.item_id)}/ack`, {' in (
        Path(server_module.UI_ASSETS_ROOT) / "studio.js"
    ).read_text(encoding="utf-8")
```

- [ ] **Step 6: Pin loopback allowlist behavior**

In `YouTubeBridge/tests/test_server_auth_loopback.py`, add:

```python
def test_legacy_live_chat_pages_are_not_loopback_only_exceptions():
    from server_security import LOOPBACK_ONLY_PATHS

    assert "/live" not in LOOPBACK_ONLY_PATHS
    assert "/live/" not in LOOPBACK_ONLY_PATHS
    assert "/live-chat" not in LOOPBACK_ONLY_PATHS
    assert "/live-chat/" not in LOOPBACK_ONLY_PATHS
    assert "/studio" in LOOPBACK_ONLY_PATHS
    assert "/studio/" in LOOPBACK_ONLY_PATHS
```

- [ ] **Step 7: Commit the failing contract**

```powershell
git add YouTubeBridge/tests/test_control_ui_static_contract.py YouTubeBridge/tests/test_server_route_split.py YouTubeBridge/tests/test_session_routes.py YouTubeBridge/tests/test_server_auth_loopback.py
git commit -m "test: pin studio-only presentation surface"
```

---

### Task 2: Delete Legacy Live Chat Routes and Assets

**Files:**
- Delete: `YouTubeBridge/static/live.html`
- Delete: `YouTubeBridge/static/live_chat.html`
- Delete: `YouTubeBridge/static/ui/live-chat.js`
- Delete: `YouTubeBridge/static/ui/live-chat.css`
- Modify: `YouTubeBridge/server_routes/ui.py`
- Modify: `YouTubeBridge/server.py`
- Modify: `YouTubeBridge/server_security.py`

- [ ] **Step 1: Remove legacy UI route functions**

In `YouTubeBridge/server_routes/ui.py`, delete this block:

```python
@router.get("/live/")
@router.get("/live")
async def bridge_live():
    return FileResponse(os.path.join(STATIC_ROOT, "live.html"))


@router.get("/live-chat/")
@router.get("/live-chat")
async def bridge_live_chat():
    return FileResponse(os.path.join(STATIC_ROOT, "live_chat.html"))
```

- [ ] **Step 2: Remove facade aliases for deleted UI routes**

In `YouTubeBridge/server.py`, delete:

```python
bridge_live = _route_handler(_ui_routes.bridge_live)
bridge_live_chat = _route_handler(_ui_routes.bridge_live_chat)
```

- [ ] **Step 3: Remove deleted page paths from loopback-only allowlist**

In `YouTubeBridge/server_security.py`, replace the top `LOOPBACK_ONLY_PATHS` definition with:

```python
LOOPBACK_ONLY_PATHS = frozenset({
    "/ui/",
    "/ui",
    "/studio/",
    "/studio",
    "/ui-config",
})
```

Keep these regex allowlists unchanged:

```python
UI_ASSET_PATH_RE = re.compile(r"^/ui-assets/.+$")
STUDIO_AVATAR_PATH_RE = re.compile(r"^/studio/avatar-assets/.+$")
SSE_PATH_RE = re.compile(r"^/sessions/[^/]+/events$")
PRESENTATION_AUDIO_PATH_RE = re.compile(r"^/sessions/[^/]+/presentation/[^/]+/audio$")
```

- [ ] **Step 4: Delete the legacy static files**

Use `apply_patch` delete hunks for these files:

```text
YouTubeBridge/static/live.html
YouTubeBridge/static/live_chat.html
YouTubeBridge/static/ui/live-chat.js
YouTubeBridge/static/ui/live-chat.css
```

- [ ] **Step 5: Run Task 1 tests and verify they pass**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_control_ui_static_contract.py::test_legacy_live_chat_static_files_are_removed YouTubeBridge/tests/test_server_route_split.py::test_legacy_live_chat_routes_are_not_registered YouTubeBridge/tests/test_server_auth_loopback.py::test_legacy_live_chat_pages_are_not_loopback_only_exceptions -q
```

Expected: PASS.

- [ ] **Step 6: Commit route and asset removal**

```powershell
git add YouTubeBridge/server_routes/ui.py YouTubeBridge/server.py YouTubeBridge/server_security.py YouTubeBridge/static/live.html YouTubeBridge/static/live_chat.html YouTubeBridge/static/ui/live-chat.js YouTubeBridge/static/ui/live-chat.css
git commit -m "refactor: remove legacy live chat ui surface"
```

---

### Task 3: Clean Dead Test Helpers and Preserve Studio Presentation Contracts

**Files:**
- Modify: `YouTubeBridge/tests/test_control_ui_static_contract.py`
- Modify: `YouTubeBridge/tests/test_chat_preview_routes.py`
- Modify: `YouTubeBridge/tests/test_episode_plan_routes.py`
- Modify: `YouTubeBridge/tests/test_launcher_contract.py`
- Modify: `YouTubeBridge/tests/test_server_auth.py`
- Modify: `YouTubeBridge/tests/test_server_auth_loopback.py`
- Modify: `YouTubeBridge/tests/test_topic_pack_routes.py`
- Modify: `YouTubeBridge/tests/test_studio_ui.py`

- [ ] **Step 1: Remove unused `_live_chat_source()` helper definitions**

After Task 1 and Task 2, run:

```powershell
rg -n "_live_chat_source\\(" YouTubeBridge/tests
```

Expected remaining matches are helper definitions only, with no call sites. Remove each unused helper:

```python
def _live_chat_source() -> str:
    static_root = Path(server_module.STATIC_ROOT)
    ui_root = static_root / "ui"
    parts = [(static_root / "live_chat.html").read_text(encoding="utf-8")]
    for name in ("live-chat.css", "live-chat.js"):
        path = ui_root / name
        if path.exists():
            parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)
```

- [ ] **Step 2: Add Studio-only Presentation Player contract**

In `YouTubeBridge/tests/test_studio_ui.py`, add this test near `test_studio_presentation_tts_events_and_lifecycle_are_wired()`:

```python
def test_studio_is_the_only_browser_presentation_player_surface():
    studio_js = (Path(server_module.UI_ASSETS_ROOT) / "studio.js").read_text(encoding="utf-8")
    static_root = Path(server_module.STATIC_ROOT)
    ui_root = static_root / "ui"

    assert "function resetPresentationPlayer(" in studio_js
    assert "function playPresentationItem(" in studio_js
    assert "async function ackPresentationItem(item)" in studio_js
    assert "async function skipCurrentPresentation()" in studio_js
    assert 'if (payload.type === "presentation_item_ready" && payload.item) {' in studio_js
    assert not (static_root / "live_chat.html").exists()
    assert not (ui_root / "live-chat.js").exists()
```

- [ ] **Step 3: Run helper search and Studio contract**

Run:

```powershell
rg -n "_live_chat_source\\(" YouTubeBridge/tests
python -m pytest YouTubeBridge/tests/test_studio_ui.py::test_studio_is_the_only_browser_presentation_player_surface -q
```

Expected: `rg` has no output, pytest PASS.

- [ ] **Step 4: Run affected static route tests**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_control_ui_static_contract.py YouTubeBridge/tests/test_server_route_split.py YouTubeBridge/tests/test_session_routes.py YouTubeBridge/tests/test_server_auth_loopback.py YouTubeBridge/tests/test_studio_ui.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit test cleanup**

```powershell
git add YouTubeBridge/tests/test_control_ui_static_contract.py YouTubeBridge/tests/test_chat_preview_routes.py YouTubeBridge/tests/test_episode_plan_routes.py YouTubeBridge/tests/test_launcher_contract.py YouTubeBridge/tests/test_server_auth.py YouTubeBridge/tests/test_server_auth_loopback.py YouTubeBridge/tests/test_topic_pack_routes.py YouTubeBridge/tests/test_studio_ui.py
git commit -m "test: consolidate presentation player contracts around studio"
```

---

### Task 4: Update Launcher and Documentation Entry Points

**Files:**
- Modify: `YouTubeBridge/start.bat`
- Modify: `YouTubeBridge/start_hot_reload.bat`
- Modify: `YouTubeBridge/README.md`
- Modify: `YouTubeBridge/CLAUDE.md`
- Modify: `YouTubeBridge/tests/test_launcher_contract.py`

- [ ] **Step 1: Add launcher contract**

In `YouTubeBridge/tests/test_launcher_contract.py`, add:

```python
def test_bridge_launchers_point_operators_to_studio_not_legacy_live_page():
    start_script = (BRIDGE_ROOT / "start.bat").read_text(encoding="utf-8")
    hot_reload_script = (BRIDGE_ROOT / "start_hot_reload.bat").read_text(encoding="utf-8")

    assert "Studio UI" in start_script
    assert "http://localhost:%API_PORT%/studio/" in start_script
    assert "http://127.0.0.1:%API_PORT%/studio/" in hot_reload_script
    assert "/live/" not in start_script
    assert "/live/" not in hot_reload_script
```

- [ ] **Step 2: Run launcher contract and verify it fails**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_launcher_contract.py::test_bridge_launchers_point_operators_to_studio_not_legacy_live_page -q
```

Expected: FAIL because launchers still print `/live/`.

- [ ] **Step 3: Update `start.bat` operator URLs**

In `YouTubeBridge/start.bat`, replace:

```bat
echo   Control UI   : http://localhost:%API_PORT%/ui/
echo   Live page    : http://localhost:%API_PORT%/live/
```

with:

```bat
echo   Studio UI    : http://localhost:%API_PORT%/studio/
echo   Legacy UI    : http://localhost:%API_PORT%/ui/
```

- [ ] **Step 4: Update `start_hot_reload.bat` URL**

In `YouTubeBridge/start_hot_reload.bat`, replace:

```bat
echo   URL: http://127.0.0.1:%API_PORT%/live/
```

with:

```bat
echo   URL: http://127.0.0.1:%API_PORT%/studio/
```

- [ ] **Step 5: Update current YouTubeBridge docs**

In `YouTubeBridge/README.md`, replace the static UI structure paragraph:

```markdown
舊的 Streamlit 入口已移除；控制台使用 `server.py` 掛載的 `/ui/` 靜態頁。
```

with:

```markdown
舊的 Streamlit 入口已移除；主要操作面是 `server.py` 掛載的 `/studio/` Studio。`/ui/` 仍保留作為 legacy 設定/控制面，但 Presentation Player 只由 Studio 承擔。舊 `/live/` 與 `/live-chat/` browser playback adapter 已移除。
```

In `YouTubeBridge/CLAUDE.md`, replace:

```markdown
- 舊的 Streamlit `app.py` 已移除；控制台由 `server.py` 掛載 `static/` 內的 `/ui/`、`/live/`、`/live-chat/`。
```

with:

```markdown
- 舊的 Streamlit `app.py` 已移除；主要控制台由 `server.py` 掛載 `static/studio.html` 的 `/studio/`。`/ui/` 是 legacy 設定/控制面；舊 `/live/` 與 `/live-chat/` browser playback adapter 已移除，Presentation Player 只在 Studio 維護。
```

In the `CLAUDE.md` execution section, replace:

```markdown
- YouTubeBridge Control UI: `http://localhost:8091/ui/`
```

with:

```markdown
- YouTubeBridge Studio UI: `http://localhost:8091/studio/`
- YouTubeBridge legacy UI: `http://localhost:8091/ui/`
```

- [ ] **Step 6: Run launcher contract and docs grep**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_launcher_contract.py::test_bridge_launchers_point_operators_to_studio_not_legacy_live_page -q
rg -n "/live/|/live-chat/|live_chat\\.html|live-chat\\.js|live-chat\\.css" YouTubeBridge/README.md YouTubeBridge/CLAUDE.md YouTubeBridge/start.bat YouTubeBridge/start_hot_reload.bat
```

Expected: pytest PASS. `rg` has no output for those current entry-point files.

- [ ] **Step 7: Commit launcher and docs cleanup**

```powershell
git add YouTubeBridge/start.bat YouTubeBridge/start_hot_reload.bat YouTubeBridge/README.md YouTubeBridge/CLAUDE.md YouTubeBridge/tests/test_launcher_contract.py
git commit -m "docs: make studio the presentation entry point"
```

---

### Task 5: Final Verification and Residual Legacy Scan

**Files:**
- Verify only unless a failing contract points to a missed reference.

- [ ] **Step 1: Compile Python surfaces touched by route cleanup**

Run:

```powershell
python -m compileall YouTubeBridge/server.py YouTubeBridge/server_routes/ui.py YouTubeBridge/server_security.py
```

Expected: compilation succeeds with no syntax errors.

- [ ] **Step 2: Run static UI and route suites**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_studio_ui.py YouTubeBridge/tests/test_control_ui_static_contract.py YouTubeBridge/tests/test_server_route_split.py YouTubeBridge/tests/test_session_routes.py YouTubeBridge/tests/test_server_auth_loopback.py YouTubeBridge/tests/test_launcher_contract.py -q
```

Expected: PASS.

- [ ] **Step 3: Run backend presentation queue regression**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_presentation_queue.py YouTubeBridge/tests/test_sse_response.py -q
```

Expected: PASS. This proves backend presentation queue and SSE payload timing survived the frontend adapter deletion.

- [ ] **Step 4: Run route/auth smoke set**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_server_auth.py YouTubeBridge/tests/test_server_auth_loopback.py YouTubeBridge/tests/test_server_route_split.py -q
```

Expected: PASS.

- [ ] **Step 5: Scan for deleted browser adapter references**

Run:

```powershell
rg -n "live_chat\\.html|live-chat\\.js|live-chat\\.css|/live-chat|/live/" YouTubeBridge/static YouTubeBridge/server_routes YouTubeBridge/server.py YouTubeBridge/server_security.py YouTubeBridge/tests YouTubeBridge/start.bat YouTubeBridge/start_hot_reload.bat YouTubeBridge/README.md YouTubeBridge/CLAUDE.md
```

Expected: no references to deleted browser adapter files or routes. References to domain terms such as `live_chat_id`, `YouTube Live Chat`, `fetch_live_chat_messages`, and YouTube URLs like `youtube.com/live/...` are allowed because they are backend/live-platform domain, not the deleted UI adapter.

- [ ] **Step 6: Commit any missed cleanup**

If Step 5 found missed route/static references and they were fixed:

```powershell
git add <fixed-files>
git commit -m "chore: finish legacy live chat cleanup"
```

If Step 5 found only allowed backend/domain references, do not create an empty commit.

---

## Self-Review

- **Spec coverage:** The plan removes Studio-unused legacy Live Chat browser UI, keeps Studio Presentation Player and backend presentation queue, updates routes/security/docs/launcher/tests, and explicitly preserves YouTube Live Chat domain behavior.
- **Placeholder scan:** No placeholder tokens are present; each code-changing task includes exact snippets or exact deletion targets.
- **Type consistency:** Route names, paths, and filenames match the current codebase: `/studio`, `/ui`, `/sessions/{session_id}/presentation/*`, `live_chat.html`, `live-chat.js`, `live-chat.css`.
- **Risk:** The main risk is confusing deleted UI adapter references with backend YouTube Live Chat domain references. The final scan separates deleted browser adapter strings from allowed backend terms.
