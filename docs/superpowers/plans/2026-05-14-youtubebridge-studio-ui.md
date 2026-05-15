# YouTubeBridge Studio UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a new static `/studio/` YouTubeBridge livestream workspace with a simplified three-panel layout.

**Architecture:** Add a parallel static page instead of replacing the legacy `/ui/`. The page owns its mock UI behavior in `studio.js`, its visual system in `studio.css`, and is served through the existing FastAPI static route module.

**Tech Stack:** FastAPI route registration, static HTML, CSS, browser-native JavaScript, pytest smoke tests.

---

### Task 1: Route And Boundary Test

**Files:**
- Create: `YouTubeBridge/tests/test_studio_ui.py`
- Modify: `YouTubeBridge/server_routes/ui.py`
- Modify: `YouTubeBridge/server.py`
- Modify: `YouTubeBridge/server_security.py`

- [ ] **Step 1: Write the failing test**

Create a pytest module that imports `server.py`, checks `/studio` and `/studio/` are registered, checks `studio.html` links to `studio.css` and `studio.js`, checks static assets are served through `/ui-assets`, and checks legacy terms are absent.

- [ ] **Step 2: Run the failing test**

Run: `python -m pytest YouTubeBridge/tests/test_studio_ui.py -q`

Expected before implementation: failure because `/studio/` and `studio.html` do not exist.

- [ ] **Step 3: Add the route and loopback policy**

Add `bridge_studio()` to `server_routes/ui.py`, expose it from `server.py`, and add `/studio` plus `/studio/` to `LOOPBACK_ONLY_PATHS`.

- [ ] **Step 4: Re-run the test**

Run: `python -m pytest YouTubeBridge/tests/test_studio_ui.py -q`

Expected after adding files and route: pass.

### Task 2: Static Studio Screen

**Files:**
- Create: `YouTubeBridge/static/studio.html`
- Create: `YouTubeBridge/static/ui/studio.css`
- Create: `YouTubeBridge/static/ui/studio.js`

- [ ] **Step 1: Add semantic HTML**

Create an app shell with three landmarks: `studio-control`, `studio-conversation`, and `studio-debug`. Keep all text Traditional Chinese and concise.

- [ ] **Step 2: Add design tokens and desktop layout**

Implement true white/cool gray surfaces, charcoal text, teal primary buttons, amber debug highlights, thin borders, 6-8px radii, and a three-column grid.

- [ ] **Step 3: Add responsive layout**

At medium widths, keep the center panel first and stack the side panels. At phone widths, use one column with stable spacing and no horizontal overflow.

- [ ] **Step 4: Add mock interactions**

Implement local state for start/stop, role toggles, debug tabs, test-message submission, and mock chat append. Do not call backend APIs in this first version.

### Task 3: Verification

**Files:**
- Verify: `YouTubeBridge/tests/test_studio_ui.py`
- Verify: browser render for `/studio/`

- [ ] **Step 1: Run smoke tests**

Run: `python -m pytest YouTubeBridge/tests/test_studio_ui.py YouTubeBridge/tests/test_server_route_split.py -q`

Expected: all selected tests pass.

- [ ] **Step 2: Run static sanity checks**

Run: `git diff --check`

Expected: no whitespace errors.

- [ ] **Step 3: Browser visual QA**

Open `http://localhost:8091/studio/` after starting YouTubeBridge in the required visible foreground server window. Verify desktop and narrow viewport layout, core buttons, tabs, and text wrapping.
