# YouTubeBridge Free Talk Stage 1 Topic Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan with worker and verifier subagents. The main orchestrator monitors flow, reviews outputs, runs final verification, and updates roadmap status. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a manually testable post-plan free talk runtime that reads JSON topic packs, lets Studio select packs, and makes characters chat from selected topics without wiring LiveEpisodePlan completion yet.

**Architecture:** Add a focused topic-loader module, persist free talk defaults and session snapshots, expose a Studio topic-pack API, and add a debug phase transition that starts `post_plan_free_talk` directly for E2E testing. This stage is a vertical slice: Studio can load topic packs, start a test session, enter free talk, and produce real AI dialogue from a selected topic.

**Tech Stack:** FastAPI routes, SQLite-backed BridgeStorage repositories, vanilla Studio HTML/CSS/JS, pytest, Browser QA against `http://127.0.0.1:8091/studio/`.

---

## File Structure

- Create `YouTubeBridge/free_talk_topics.py`: parse global topic packs and LiveEpisodePlan sidecar topic files.
- Modify `YouTubeBridge/models.py`: add free talk defaults/session request fields.
- Modify `YouTubeBridge/storage_schema.py`: add session columns for free talk config.
- Modify `YouTubeBridge/storage_mappers.py`: include new session fields in API/session dictionaries.
- Modify `YouTubeBridge/storage_repositories/sessions.py`: persist new fields in `upsert_session`.
- Modify `YouTubeBridge/server_state.py`: expose `free_talk_topic_root`.
- Modify `YouTubeBridge/server_routes/studio_settings.py`: add `GET /studio/free-talk-topics`.
- Modify `YouTubeBridge/server_routes/sessions.py`: add a debug-safe free talk phase transition endpoint for Stage 1.
- Modify `YouTubeBridge/engine_director_runtime.py`: run a `post_plan_free_talk` tick that sends one topic or natural fallback.
- Modify `YouTubeBridge/static/studio.html`, `YouTubeBridge/static/ui/studio.js`, `YouTubeBridge/static/ui/studio.css`: add topic-pack checklist, free talk settings, and debug button.
- Create `YouTubeBridge/tests/test_free_talk_topics.py`: topic loader unit tests.
- Modify `YouTubeBridge/tests/test_storage.py`: session persistence tests.
- Modify `YouTubeBridge/tests/test_studio_settings_api.py`: topic API tests.
- Modify `YouTubeBridge/tests/test_studio_ui.py`: source tests for topic checklist and start payload.
- Create or extend `YouTubeBridge/tests/test_bridge_engine_free_talk.py`: runtime tick tests.

---

### Task 1: Topic Pack Loader

**Files:**
- Create: `YouTubeBridge/free_talk_topics.py`
- Test: `YouTubeBridge/tests/test_free_talk_topics.py`

- [ ] **Step 1: Write failing tests for both JSON formats and invalid file isolation**

Add `YouTubeBridge/tests/test_free_talk_topics.py`:

```python
from pathlib import Path

from free_talk_topics import load_free_talk_topic_library


def test_load_free_talk_topic_library_supports_object_and_array_formats(tmp_path: Path):
    root = tmp_path / "runtime" / "YouTubeBridge" / "freeTalkTopics"
    root.mkdir(parents=True)
    (root / "anime-casual.json").write_text(
        '{"name":"動畫雜談","topics":[{"title":"最近看的作品","prompt":"請聊最近看的作品。"}]}',
        encoding="utf-8",
    )
    (root / "creator-life.json").write_text(
        '[{"title":"創作近況","prompt":"請聊聊最近創作時遇到的事情。"}]',
        encoding="utf-8",
    )

    result = load_free_talk_topic_library(root)

    assert [pack["pack_id"] for pack in result["packs"]] == ["anime-casual", "creator-life"]
    assert result["packs"][0]["display_name"] == "動畫雜談"
    assert result["packs"][0]["topic_count"] == 1
    assert result["packs"][1]["display_name"] == "creator-life"
    assert result["total_topic_count"] == 2
    assert result["warnings"] == []


def test_load_free_talk_topic_library_skips_bad_topics_and_reports_bad_json(tmp_path: Path):
    root = tmp_path / "runtime" / "YouTubeBridge" / "freeTalkTopics"
    root.mkdir(parents=True)
    (root / "mixed.json").write_text(
        '{"topics":[{"title":"有效","prompt":"有效內容"},{"title":"","prompt":"缺 title"}]}',
        encoding="utf-8",
    )
    (root / "broken.json").write_text("{not-json", encoding="utf-8")

    result = load_free_talk_topic_library(root)

    assert result["packs"][0]["pack_id"] == "mixed"
    assert result["packs"][0]["topic_count"] == 1
    assert result["total_topic_count"] == 1
    assert any("broken.json" in warning for warning in result["warnings"])
```

- [ ] **Step 2: Run the topic loader tests and verify they fail**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_free_talk_topics.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'free_talk_topics'`.

- [ ] **Step 3: Implement the topic loader**

Create `YouTubeBridge/free_talk_topics.py`:

```python
"""Post-plan free talk topic-pack loading helpers."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


MAX_TOPIC_COUNT = 200
MAX_TITLE_CHARS = 120
MAX_PROMPT_CHARS = 1000


def _clean_topic(raw: Any) -> dict[str, str] | None:
    if not isinstance(raw, dict):
        return None
    title = str(raw.get("title") or "").strip()
    prompt = str(raw.get("prompt") or "").strip()
    if not title or not prompt:
        return None
    return {
        "title": title[:MAX_TITLE_CHARS],
        "prompt": prompt[:MAX_PROMPT_CHARS],
    }


def _parse_topic_pack(path: Path) -> tuple[str, list[dict[str, str]], list[str]]:
    warnings: list[str] = []
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        return path.stem, [], [f"{path.name}: JSON 載入失敗：{exc}"]
    if isinstance(payload, list):
        display_name = path.stem
        raw_topics = payload
    elif isinstance(payload, dict):
        display_name = str(payload.get("name") or path.stem).strip() or path.stem
        raw_topics = payload.get("topics") if isinstance(payload.get("topics"), list) else []
    else:
        return path.stem, [], [f"{path.name}: 根節點必須是 object 或 array"]
    topics = [topic for item in raw_topics if (topic := _clean_topic(item))]
    skipped = len(raw_topics) - len(topics)
    if skipped:
        warnings.append(f"{path.name}: 已略過 {skipped} 筆無效 topic")
    return display_name[:120], topics[:MAX_TOPIC_COUNT], warnings


def load_free_talk_topic_library(root: Path) -> dict[str, Any]:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    packs: list[dict[str, Any]] = []
    warnings: list[str] = []
    total = 0
    for path in sorted(root.glob("*.json"), key=lambda item: item.name.lower()):
        display_name, topics, pack_warnings = _parse_topic_pack(path)
        warnings.extend(pack_warnings)
        if not topics:
            continue
        pack = {
            "pack_id": path.stem,
            "display_name": display_name,
            "filename": path.name,
            "topic_count": len(topics),
            "topics": topics,
            "warnings": pack_warnings,
        }
        packs.append(pack)
        total += len(topics)
    return {
        "topic_dir": str(root),
        "packs": packs,
        "total_topic_count": total,
        "warnings": warnings,
    }


def load_free_talk_sidecar(path: Path | None) -> dict[str, Any]:
    if path is None or not Path(path).is_file():
        return {"found": False, "topic_count": 0, "topics": [], "warnings": []}
    display_name, topics, warnings = _parse_topic_pack(Path(path))
    return {
        "found": True,
        "display_name": display_name,
        "topic_count": len(topics),
        "topics": topics,
        "warnings": warnings,
    }
```

- [ ] **Step 4: Run the topic loader tests and verify they pass**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_free_talk_topics.py -q
```

Expected: `2 passed`.

- [ ] **Step 5: Commit**

```powershell
git add YouTubeBridge/free_talk_topics.py YouTubeBridge/tests/test_free_talk_topics.py
git commit -m "feat(youtube-bridge): load free talk topic packs"
```

---

### Task 2: Session Snapshot Fields

**Files:**
- Modify: `YouTubeBridge/models.py`
- Modify: `YouTubeBridge/storage_schema.py`
- Modify: `YouTubeBridge/storage_mappers.py`
- Modify: `YouTubeBridge/storage_repositories/sessions.py`
- Test: `YouTubeBridge/tests/test_storage.py`

- [ ] **Step 1: Write a failing storage test for free talk session fields**

Append to `YouTubeBridge/tests/test_storage.py`:

```python
def test_session_persists_post_plan_free_talk_fields(tmp_path):
    storage = BridgeStorage(tmp_path / "youtube_live.db")
    storage.upsert_connector({
        "connector_id": "youtube-main",
        "display_name": "YouTube Main",
        "api_key": "",
        "enabled": True,
    })

    saved = storage.upsert_session({
        "session_id": "live-a",
        "connector_id": "youtube-main",
        "display_name": "Free Talk Test",
        "post_plan_free_talk_enabled": True,
        "post_plan_free_talk_minutes": 25,
        "post_plan_free_talk_tick_interval_seconds": 30,
        "post_plan_free_talk_idle_turns_min": 6,
        "post_plan_free_talk_idle_turns_max": 6,
        "post_plan_free_talk_audience_turns_min": 3,
        "post_plan_free_talk_audience_turns_max": 3,
        "post_plan_free_talk_topic_pack_ids": ["anime-casual", "creator-life"],
    })

    assert saved["post_plan_free_talk_enabled"] is True
    assert saved["post_plan_free_talk_minutes"] == 25
    assert saved["post_plan_free_talk_tick_interval_seconds"] == 30
    assert saved["post_plan_free_talk_idle_turns_min"] == 6
    assert saved["post_plan_free_talk_idle_turns_max"] == 6
    assert saved["post_plan_free_talk_audience_turns_min"] == 3
    assert saved["post_plan_free_talk_audience_turns_max"] == 3
    assert saved["post_plan_free_talk_topic_pack_ids"] == ["anime-casual", "creator-life"]
```

- [ ] **Step 2: Run the storage test and verify it fails**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_storage.py::test_session_persists_post_plan_free_talk_fields -q
```

Expected: FAIL because the session dictionary does not contain the new keys.

- [ ] **Step 3: Add model fields**

Modify `LiveSessionConfig` in `YouTubeBridge/models.py`:

```python
    post_plan_free_talk_enabled: bool = False
    post_plan_free_talk_minutes: int = Field(20, ge=0, le=240)
    post_plan_free_talk_tick_interval_seconds: int = Field(30, ge=5, le=600)
    post_plan_free_talk_idle_turns_min: int = Field(6, ge=1, le=12)
    post_plan_free_talk_idle_turns_max: int = Field(6, ge=1, le=12)
    post_plan_free_talk_audience_turns_min: int = Field(3, ge=1, le=12)
    post_plan_free_talk_audience_turns_max: int = Field(3, ge=1, le=12)
    post_plan_free_talk_topic_pack_ids: list[str] = Field(default_factory=list)
```

Modify `StudioLiveDefaults` in `YouTubeBridge/models.py` so Studio defaults and session snapshots share names:

```python
    post_plan_free_talk_tick_interval_seconds: int = Field(30, ge=5, le=600)
    post_plan_free_talk_idle_turns_min: int = Field(6, ge=1, le=12)
    post_plan_free_talk_idle_turns_max: int = Field(6, ge=1, le=12)
    post_plan_free_talk_audience_turns_min: int = Field(3, ge=1, le=12)
    post_plan_free_talk_audience_turns_max: int = Field(3, ge=1, le=12)
    post_plan_free_talk_topic_pack_ids: list[str] = Field(default_factory=list)
```

- [ ] **Step 4: Add schema and mapper fields**

Modify `YouTubeBridge/storage_schema.py` `live_sessions` table:

```sql
            post_plan_free_talk_enabled INTEGER NOT NULL DEFAULT 0,
            post_plan_free_talk_minutes INTEGER NOT NULL DEFAULT 20,
            post_plan_free_talk_tick_interval_seconds INTEGER NOT NULL DEFAULT 30,
            post_plan_free_talk_idle_turns_min INTEGER NOT NULL DEFAULT 6,
            post_plan_free_talk_idle_turns_max INTEGER NOT NULL DEFAULT 6,
            post_plan_free_talk_audience_turns_min INTEGER NOT NULL DEFAULT 3,
            post_plan_free_talk_audience_turns_max INTEGER NOT NULL DEFAULT 3,
            post_plan_free_talk_topic_pack_ids_json TEXT DEFAULT '[]',
```

Add the same columns to `LIVE_SESSION_COLUMNS`.

Modify `YouTubeBridge/storage_mappers.py` session mapper:

```python
        "post_plan_free_talk_enabled": bool(row_value(row, "post_plan_free_talk_enabled", 0)),
        "post_plan_free_talk_minutes": int(row_value(row, "post_plan_free_talk_minutes", 20)),
        "post_plan_free_talk_tick_interval_seconds": int(row_value(row, "post_plan_free_talk_tick_interval_seconds", 30)),
        "post_plan_free_talk_idle_turns_min": int(row_value(row, "post_plan_free_talk_idle_turns_min", 6)),
        "post_plan_free_talk_idle_turns_max": int(row_value(row, "post_plan_free_talk_idle_turns_max", 6)),
        "post_plan_free_talk_audience_turns_min": int(row_value(row, "post_plan_free_talk_audience_turns_min", 3)),
        "post_plan_free_talk_audience_turns_max": int(row_value(row, "post_plan_free_talk_audience_turns_max", 3)),
        "post_plan_free_talk_topic_pack_ids": json_loads(row_value(row, "post_plan_free_talk_topic_pack_ids_json", "[]"), []),
```

- [ ] **Step 5: Persist fields in the session repository**

Modify `row_data` in `YouTubeBridge/storage_repositories/sessions.py`:

```python
                "post_plan_free_talk_enabled": 1 if config.get("post_plan_free_talk_enabled", False) else 0,
                "post_plan_free_talk_minutes": max(0, min(self._int_or_default(config.get("post_plan_free_talk_minutes", 20), 20), 240)),
                "post_plan_free_talk_tick_interval_seconds": max(5, min(self._int_or_default(config.get("post_plan_free_talk_tick_interval_seconds", 30), 30), 600)),
                "post_plan_free_talk_idle_turns_min": max(1, min(self._int_or_default(config.get("post_plan_free_talk_idle_turns_min", 6), 6), 12)),
                "post_plan_free_talk_idle_turns_max": max(1, min(self._int_or_default(config.get("post_plan_free_talk_idle_turns_max", 6), 6), 12)),
                "post_plan_free_talk_audience_turns_min": max(1, min(self._int_or_default(config.get("post_plan_free_talk_audience_turns_min", 3), 3), 12)),
                "post_plan_free_talk_audience_turns_max": max(1, min(self._int_or_default(config.get("post_plan_free_talk_audience_turns_max", 3), 3), 12)),
                "post_plan_free_talk_topic_pack_ids_json": self._json_dump([
                    str(item).strip()[:120]
                    for item in (config.get("post_plan_free_talk_topic_pack_ids") or [])
                    if str(item).strip()
                ]),
```

After building `row_data`, normalize min/max pairs:

```python
            if row_data["post_plan_free_talk_idle_turns_min"] > row_data["post_plan_free_talk_idle_turns_max"]:
                row_data["post_plan_free_talk_idle_turns_max"] = row_data["post_plan_free_talk_idle_turns_min"]
            if row_data["post_plan_free_talk_audience_turns_min"] > row_data["post_plan_free_talk_audience_turns_max"]:
                row_data["post_plan_free_talk_audience_turns_max"] = row_data["post_plan_free_talk_audience_turns_min"]
```

- [ ] **Step 6: Run the storage regression**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_storage.py::test_session_persists_post_plan_free_talk_fields -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add YouTubeBridge/models.py YouTubeBridge/storage_schema.py YouTubeBridge/storage_mappers.py YouTubeBridge/storage_repositories/sessions.py YouTubeBridge/tests/test_storage.py
git commit -m "feat(youtube-bridge): persist free talk session settings"
```

---

### Task 3: Studio Topic API and Checklist UI

**Files:**
- Modify: `YouTubeBridge/server_state.py`
- Modify: `YouTubeBridge/server_routes/studio_settings.py`
- Modify: `YouTubeBridge/static/studio.html`
- Modify: `YouTubeBridge/static/ui/studio.js`
- Modify: `YouTubeBridge/static/ui/studio.css`
- Test: `YouTubeBridge/tests/test_studio_settings_api.py`
- Test: `YouTubeBridge/tests/test_studio_ui.py`

- [ ] **Step 1: Write API tests for global packs and sidecar visibility**

Append to `YouTubeBridge/tests/test_studio_settings_api.py`:

```python
def test_studio_free_talk_topics_lists_global_packs_and_plan_sidecar(tmp_path, monkeypatch):
    from server_routes import studio_settings as route

    topic_root = tmp_path / "runtime" / "YouTubeBridge" / "freeTalkTopics"
    topic_root.mkdir(parents=True)
    (topic_root / "anime.json").write_text(
        '{"name":"動畫","topics":[{"title":"作品近況","prompt":"聊聊作品近況。"}]}',
        encoding="utf-8",
    )
    plan_dir = tmp_path / "plans" / "ep1"
    plan_dir.mkdir(parents=True)
    (plan_dir / "free-talk-topics.json").write_text(
        '[{"title":"本場補充","prompt":"聊聊本場補充。"}]',
        encoding="utf-8",
    )

    class FakeStorage:
        def get_episode_plan(self, plan_id):
            return {"plan_id": plan_id, "source_path": str(plan_dir / "episode-plan.json")}

    class FakeState:
        storage = FakeStorage()
        manager = None
        summary_manager = None
        free_talk_topic_root = topic_root

    route.configure(FakeState())

    result = asyncio.run(route.list_studio_free_talk_topics("ep1"))

    assert result["packs"][0]["pack_id"] == "anime"
    assert result["packs"][0]["topic_count"] == 1
    assert result["sidecar"]["found"] is True
    assert result["sidecar"]["topic_count"] == 1
```

- [ ] **Step 2: Run the API test and verify it fails**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_studio_settings_api.py::test_studio_free_talk_topics_lists_global_packs_and_plan_sidecar -q
```

Expected: FAIL because `list_studio_free_talk_topics` does not exist.

- [ ] **Step 3: Add the API route**

Modify `YouTubeBridge/server_state.py` when building route state:

```python
free_talk_topic_root = Path("runtime") / "YouTubeBridge" / "freeTalkTopics"
```

Modify `YouTubeBridge/server_routes/studio_settings.py` imports:

```python
from free_talk_topics import load_free_talk_sidecar, load_free_talk_topic_library
```

Add route:

```python
@router.get("/studio/free-talk-topics")
async def list_studio_free_talk_topics(episode_plan_id: str = ""):
    state = _require_state()
    root = Path(getattr(state, "free_talk_topic_root", Path("runtime") / "YouTubeBridge" / "freeTalkTopics"))
    library = load_free_talk_topic_library(root)
    sidecar_path = None
    if episode_plan_id:
        plan = storage.get_episode_plan(episode_plan_id)
        source_path = Path(str((plan or {}).get("source_path") or ""))
        if source_path.name:
            sidecar_path = source_path.parent / "free-talk-topics.json"
    sidecar = load_free_talk_sidecar(sidecar_path)
    return {
        **library,
        "sidecar": sidecar,
        "total_topic_count": int(library.get("total_topic_count") or 0) + int(sidecar.get("topic_count") or 0),
    }
```

- [ ] **Step 4: Write frontend source tests**

Append to `YouTubeBridge/tests/test_studio_ui.py`:

```python
def test_studio_free_talk_topic_pack_ui_and_payload():
    studio_html = STUDIO_HTML.read_text(encoding="utf-8")
    studio_js = STUDIO_JS.read_text(encoding="utf-8")

    assert "雜談話題庫" in studio_html
    assert "runtime/YouTubeBridge/freeTalkTopics/" in studio_html
    assert "全部話題庫" in studio_html
    assert "重新載入話題庫" in studio_html
    assert '"/studio/free-talk-topics?episode_plan_id="' in studio_js
    assert "post_plan_free_talk_topic_pack_ids" in studio_js
    assert "selectedFreeTalkTopicPackIds()" in studio_js
```

- [ ] **Step 5: Implement Studio UI**

In `YouTubeBridge/static/studio.html`, inside the `LiveEpisodePlan 後續流程` settings section, add:

```html
<div class="free-talk-topic-box">
  <div class="section-row">
    <strong>雜談話題庫</strong>
    <button class="secondary small" id="reloadFreeTalkTopics" type="button">重新載入話題庫</button>
  </div>
  <p class="muted small">固定讀取 runtime/YouTubeBridge/freeTalkTopics/</p>
  <div id="freeTalkTopicStats" class="muted small">尚未載入話題庫</div>
  <div id="freeTalkSidecarState" class="muted small">本企劃補充話題：尚未檢查</div>
  <div id="freeTalkTopicChecklist" class="check-list"></div>
</div>
```

In `YouTubeBridge/static/ui/studio.js`, add state:

```js
state.freeTalkTopicPacks = [];
state.freeTalkSidecar = { found: false, topic_count: 0 };
state.selectedFreeTalkTopicPackIds = new Set();
```

Add helpers:

```js
function selectedFreeTalkTopicPackIds() {
  return Array.from(state.selectedFreeTalkTopicPackIds);
}

async function loadFreeTalkTopics() {
  const planId = encodeURIComponent(planSelect.value || "");
  const result = await api(`/studio/free-talk-topics?episode_plan_id=${planId}`);
  state.freeTalkTopicPacks = Array.isArray(result.packs) ? result.packs : [];
  state.freeTalkSidecar = result.sidecar || { found: false, topic_count: 0 };
  state.selectedFreeTalkTopicPackIds = new Set(state.freeTalkTopicPacks.map((pack) => pack.pack_id));
  renderFreeTalkTopicChecklist(result);
  appendLog("INFO", `雜談話題庫已載入：${state.freeTalkTopicPacks.length} pack / ${result.total_topic_count || 0} topics`);
}

function renderFreeTalkTopicChecklist(result) {
  const target = $("freeTalkTopicChecklist");
  target.innerHTML = "";
  $("freeTalkTopicStats").textContent = `已載入 ${state.freeTalkTopicPacks.length} 個話題庫 / ${result.total_topic_count || 0} 個話題`;
  $("freeTalkSidecarState").textContent = state.freeTalkSidecar.found
    ? `本企劃補充話題：已啟用，${state.freeTalkSidecar.topic_count || 0} 個話題`
    : "本企劃沒有補充話題";
  const allRow = document.createElement("label");
  allRow.className = "check-row";
  allRow.innerHTML = `<input type="checkbox" id="allFreeTalkTopics"> <span>全部話題庫</span>`;
  target.appendChild(allRow);
  const allInput = allRow.querySelector("input");
  allInput.checked = state.freeTalkTopicPacks.length > 0 && state.selectedFreeTalkTopicPackIds.size === state.freeTalkTopicPacks.length;
  allInput.disabled = state.freeTalkTopicPacks.length === 0;
  allInput.addEventListener("change", () => {
    state.selectedFreeTalkTopicPackIds = allInput.checked
      ? new Set(state.freeTalkTopicPacks.map((pack) => pack.pack_id))
      : new Set();
    renderFreeTalkTopicChecklist(result);
  });
  for (const pack of state.freeTalkTopicPacks) {
    const row = document.createElement("label");
    row.className = "check-row";
    row.innerHTML = `<input type="checkbox" data-pack-id="${pack.pack_id}"> <span>${pack.display_name} (${pack.topic_count})</span>`;
    const input = row.querySelector("input");
    input.checked = state.selectedFreeTalkTopicPackIds.has(pack.pack_id);
    input.addEventListener("change", () => {
      if (input.checked) state.selectedFreeTalkTopicPackIds.add(pack.pack_id);
      else state.selectedFreeTalkTopicPackIds.delete(pack.pack_id);
      renderFreeTalkTopicChecklist(result);
    });
    target.appendChild(row);
  }
}
```

Update `collectLiveDefaults()` and `studioLiveSessionPayload()` to include `post_plan_free_talk_topic_pack_ids: selectedFreeTalkTopicPackIds()`.

- [ ] **Step 6: Run API and frontend source tests**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_studio_settings_api.py::test_studio_free_talk_topics_lists_global_packs_and_plan_sidecar YouTubeBridge/tests/test_studio_ui.py::test_studio_free_talk_topic_pack_ui_and_payload -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add YouTubeBridge/server_state.py YouTubeBridge/server_routes/studio_settings.py YouTubeBridge/static/studio.html YouTubeBridge/static/ui/studio.js YouTubeBridge/static/ui/studio.css YouTubeBridge/tests/test_studio_settings_api.py YouTubeBridge/tests/test_studio_ui.py
git commit -m "feat(youtube-bridge): expose free talk topic packs in studio"
```

---

### Task 4: Manual Free Talk Runtime Tick

**Files:**
- Modify: `YouTubeBridge/engine_director_runtime.py`
- Modify: `YouTubeBridge/server_routes/sessions.py`
- Test: `YouTubeBridge/tests/test_bridge_engine_free_talk.py`
- Test: `YouTubeBridge/tests/test_server_route_split.py`

- [ ] **Step 1: Write failing runtime tests**

Create `YouTubeBridge/tests/test_bridge_engine_free_talk.py`:

```python
import pytest

from bridge_engine import YouTubeBridgeManager
from storage import BridgeStorage


class FakeFreeTalkMemoriaClient:
    def __init__(self):
        self.calls = []

    def chat_stream_sync(self, **kwargs):
        self.calls.append(kwargs)
        return {"session_id": kwargs.get("session_id") or "mem-free-talk", "message_id": 99, "reply": "雜談回應"}


@pytest.mark.asyncio
async def test_manual_free_talk_phase_sends_selected_topic(tmp_path):
    topic_root = tmp_path / "runtime" / "YouTubeBridge" / "freeTalkTopics"
    topic_root.mkdir(parents=True)
    (topic_root / "casual.json").write_text(
        '[{"title":"創作近況","prompt":"請聊聊最近創作時遇到的事情。"}]',
        encoding="utf-8",
    )
    storage = BridgeStorage(tmp_path / "youtube_live.db")
    storage.upsert_connector({"connector_id": "youtube-main", "display_name": "YT", "api_key": "", "enabled": True})
    session = storage.upsert_session({
        "session_id": "live-a",
        "connector_id": "youtube-main",
        "display_name": "Free Talk",
        "post_plan_free_talk_enabled": True,
        "post_plan_free_talk_topic_pack_ids": ["casual"],
        "post_plan_free_talk_idle_turns_min": 6,
        "post_plan_free_talk_idle_turns_max": 6,
    })
    manager = YouTubeBridgeManager(storage)
    fake = FakeFreeTalkMemoriaClient()
    manager._memoria_client_cached = fake

    result = await manager.start_post_plan_free_talk_test(
        session["session_id"],
        topic_root=topic_root,
        transition_reason="operator_debug_start_free_talk",
    )

    assert result["phase"] == "post_plan_free_talk"
    assert fake.calls
    assert "雜談話題：創作近況" in fake.calls[0]["content"]
    assert fake.calls[0]["external_context"]["group_turn_limit"] == 6
```

- [ ] **Step 2: Run runtime test and verify it fails**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_free_talk.py::test_manual_free_talk_phase_sends_selected_topic -q
```

Expected: FAIL because `start_post_plan_free_talk_test` does not exist.

- [ ] **Step 3: Implement manager methods**

In `YouTubeBridge/engine_director_runtime.py`, add methods on the director runtime mixin:

```python
async def start_post_plan_free_talk_test(
    self,
    session_id: str,
    *,
    topic_root: Path,
    transition_reason: str = "operator_debug_start_free_talk",
) -> dict[str, Any]:
    session = self.storage.get_session(session_id)
    if not session:
        raise ValueError("live session 不存在")
    runtime = self._runtimes.setdefault(session_id, LiveRuntime(session_id=session_id, running=True, status="running"))
    state = self.storage.get_director_state(session_id)
    free_talk = self._build_free_talk_state(session, topic_root=topic_root, sidecar_path=None, transition_reason=transition_reason)
    next_state = self.storage.update_director_state(
        session_id,
        status="post_plan_free_talk",
        metadata={**(state.get("metadata") or {}), "phase": "post_plan_free_talk", "post_plan_free_talk": free_talk},
    )
    await self._broadcast(session_id, {"type": "director_state", "director": next_state})
    tick = await self._run_post_plan_free_talk_tick(runtime, session, next_state)
    return {"phase": "post_plan_free_talk", "director": next_state, "tick": tick}
```

Add `_build_free_talk_state(...)` that loads selected packs using `load_free_talk_topic_library`, shuffles topics with `random.shuffle`, truncates to 200 topics, and stores `topic_queue`, `topic_cursor`, `started_at`, `deadline_at`, `selected_topic_pack_ids`, `last_tick_at`, and `last_tick_action`.

Add `_run_post_plan_free_talk_tick(...)`:

```python
async def _run_post_plan_free_talk_tick(self, runtime: LiveRuntime, session: dict[str, Any], director_state: dict[str, Any]) -> dict[str, Any]:
    if self.storage.get_active_interaction(runtime.session_id):
        return {"action": "wait", "reason": "active_interaction"}
    metadata = dict(director_state.get("metadata") or {})
    free_talk = dict(metadata.get("post_plan_free_talk") or {})
    queue = free_talk.get("topic_queue") if isinstance(free_talk.get("topic_queue"), list) else []
    cursor = int(free_talk.get("topic_cursor") or 0)
    if cursor < len(queue):
        topic = queue[cursor]
        content = f"雜談話題：{topic.get('title')}\n{topic.get('prompt')}"
        action = "topic_chat"
        free_talk["topic_cursor"] = cursor + 1
    else:
        content = "目前沒有新的聊天室留言。請依目前群聊氣氛自然閒聊一輪。"
        action = "natural_chat"
    free_talk["last_tick_at"] = datetime.now().isoformat()
    free_talk["last_tick_action"] = action
    self.storage.update_director_state(
        runtime.session_id,
        status="post_plan_free_talk",
        metadata={**metadata, "post_plan_free_talk": free_talk},
    )
    group_turn_limit = self._free_talk_group_turn_limit(session, audience=False)
    result = await self._send_director_turn(
        session,
        action=action,
        public_prompt=content,
        display_content=content,
        group_turn_limit=group_turn_limit,
        external_context_patch={"source": "youtube_live_director", "phase_label": "post_plan_free_talk"},
    )
    return {"action": action, "group_turn_limit": group_turn_limit, "interaction": result.get("interaction")}
```

Add `_free_talk_group_turn_limit(session, audience: bool)` so it uses idle min/max for topic/natural chat and audience min/max for audience replies.

- [ ] **Step 4: Add route and split-route regression**

In `YouTubeBridge/server_routes/sessions.py`, add:

```python
@router.post("/sessions/{session_id}/phase/free-talk-test/start")
async def start_free_talk_test(session_id: str):
    if not storage.get_session(session_id):
        raise HTTPException(status_code=404, detail="session not found")
    try:
        return await manager.start_post_plan_free_talk_test(
            session_id,
            topic_root=_require_state().free_talk_topic_root,
            transition_reason="operator_debug_start_free_talk",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
```

Add `/sessions/{session_id}/phase/free-talk-test/start` to `YouTubeBridge/tests/test_server_route_split.py` public route assertions.

- [ ] **Step 5: Run runtime route tests**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_free_talk.py YouTubeBridge/tests/test_server_route_split.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add YouTubeBridge/engine_director_runtime.py YouTubeBridge/server_routes/sessions.py YouTubeBridge/tests/test_bridge_engine_free_talk.py YouTubeBridge/tests/test_server_route_split.py
git commit -m "feat(youtube-bridge): add manual free talk runtime tick"
```

---

### Task 5: Studio Debug Button and Browser E2E

**Files:**
- Modify: `YouTubeBridge/static/studio.html`
- Modify: `YouTubeBridge/static/ui/studio.js`
- Test: `YouTubeBridge/tests/test_studio_ui.py`

- [ ] **Step 1: Write frontend source test**

Append to `YouTubeBridge/tests/test_studio_ui.py`:

```python
def test_studio_has_manual_free_talk_test_button():
    studio_html = STUDIO_HTML.read_text(encoding="utf-8")
    studio_js = STUDIO_JS.read_text(encoding="utf-8")

    assert "開始雜談測試" in studio_html
    assert "/phase/free-talk-test/start" in studio_js
    assert "post_plan_free_talk" in studio_js
```

- [ ] **Step 2: Run frontend source test and verify it fails**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_studio_ui.py::test_studio_has_manual_free_talk_test_button -q
```

Expected: FAIL because the button is missing.

- [ ] **Step 3: Add Studio debug button**

In `YouTubeBridge/static/studio.html`, inside the Test tab area:

```html
<section class="test-card">
  <div class="section-row">
    <h2>雜談測試</h2>
    <button class="secondary" id="startFreeTalkTest" type="button">開始雜談測試</button>
  </div>
  <p id="freeTalkTestState" class="muted">啟動直播後可直接切入 post-plan free talk。</p>
</section>
```

In `YouTubeBridge/static/ui/studio.js`, add:

```js
async function startFreeTalkTest() {
  if (!(state.sessionId && state.live)) {
    $("freeTalkTestState").textContent = "請先開始直播。";
    return;
  }
  try {
    const result = await api(`/sessions/${encodeURIComponent(state.sessionId)}/phase/free-talk-test/start`, {
      method: "POST",
      body: {},
    });
    $("freeTalkTestState").textContent = "已進入雜談測試。";
    appendLog("INFO", `雜談測試已啟動：${result.phase || "post_plan_free_talk"}`);
    await refreshChatPreview();
  } catch (error) {
    $("freeTalkTestState").textContent = `雜談測試啟動失敗：${error.message || error}`;
    appendLog("WARN", `雜談測試啟動失敗：${error.message || error}`);
  }
}

$("startFreeTalkTest").addEventListener("click", startFreeTalkTest);
```

- [ ] **Step 4: Run frontend source test**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_studio_ui.py::test_studio_has_manual_free_talk_test_button -q
```

Expected: PASS.

- [ ] **Step 5: Run Stage 1 regression**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_free_talk_topics.py YouTubeBridge/tests/test_studio_settings_api.py YouTubeBridge/tests/test_studio_ui.py YouTubeBridge/tests/test_bridge_engine_free_talk.py YouTubeBridge/tests/test_storage.py::test_session_persists_post_plan_free_talk_fields -q
node --check YouTubeBridge/static/ui/studio.js
git diff --check
```

Expected: pytest PASS, `node --check` exit 0, `git diff --check` exit 0 or only existing CRLF warnings.

- [ ] **Step 6: Browser E2E**

Start 8091 in a visible foreground window:

```powershell
Start-Process -FilePath "cmd.exe" -ArgumentList "/k", "G:\ClaudeProject\MemoriaCore\YouTubeBridge\start.bat" -WorkingDirectory "G:\ClaudeProject\MemoriaCore\YouTubeBridge" -WindowStyle Normal
```

Create a test topic pack:

```powershell
New-Item -ItemType Directory -Force -Path "runtime\YouTubeBridge\freeTalkTopics"
Set-Content -Path "runtime\YouTubeBridge\freeTalkTopics\stage1-smoke.json" -Encoding UTF8 -Value '[{"title":"測試雜談話題","prompt":"請用輕鬆語氣聊一輪測試雜談。"}]'
```

Browser QA:

- Open `http://127.0.0.1:8091/studio/`.
- Confirm the topic checklist shows `stage1-smoke`.
- Start a test-mode live session.
- Click `開始雜談測試`.
- Confirm central chat receives AI dialogue.
- Confirm Debug Log has no relevant error.
- Confirm `GET /sessions/{session_id}/interactions` shows an interaction whose metadata includes `phase` or whose director state metadata includes `post_plan_free_talk`.

- [ ] **Step 7: Commit**

```powershell
git add YouTubeBridge/static/studio.html YouTubeBridge/static/ui/studio.js YouTubeBridge/tests/test_studio_ui.py
git commit -m "feat(youtube-bridge): add studio free talk smoke control"
```

---

## Stage 1 Acceptance Criteria

- Studio can load topic packs from `runtime/YouTubeBridge/freeTalkTopics/*.json`.
- Studio can select zero or more global topic packs.
- Plan sidecar state is visible when present.
- Starting a test live session and clicking `開始雜談測試` produces real AI dialogue.
- The LLM receives only the active topic or natural fallback prompt, not the full metadata snapshot.
- All listed tests pass and Browser E2E is recorded in the final execution note.
