# Research Gate External Context Deepening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 YouTubeBridge 的 Research Gate、audience query resolution、`external_context` payload 組裝從 `YouTubeBridgeManager` facade 移到可測、可替換、具 Locality 的深 Module。

**Architecture:** 保留 `bridge_engine.py` 作為 root-level facade，不搬 root import。新增 `research_gate.py` 與 `external_context.py`，讓 `YouTubeBridgeManager` 只保留相容 wrapper 與 broadcast orchestration。新的 Seam 以兩個小 Interface 呈現：`ResearchGateModule.request_sync()/ensure_audience_worker()/completed_context()` 與 `ExternalContextBuilder.build()`。

**Tech Stack:** Python 3.12、FastAPI route facade、SQLite `BridgeStorage` facade、pytest、YouTubeBridge 既有 `engine_*` mixin 風格。

---

## Current Friction

`YouTubeBridge/bridge_engine.py` 目前同時承擔：

- audience query intent 分類與 query term normalization。
- Topic Pack 命中、topic graph context 轉文字、usage trace。
- Research Gate 手動查詢、背景 worker、cooldown/session limit、FactCard 建立、embedding。
- `external_context` event selection、安全顯示、presentation 模式字數壓縮、payload/summary 組裝。

這讓 `YouTubeBridgeManager` 的 Interface 太淺：測試想驗證 Research Gate 或 `external_context` 時，必須知道 manager 的私有方法、runtime thread registry、Topic Pack helper、Memoria classifier 與 storage side effect。刪除測試中的 manager monkeypatch 後，複雜度會散回多個測試檔，代表目前缺少能承載行為的深 Module。

## Target File Structure

- Create: `YouTubeBridge/research_gate.py`
  - Owns Research Gate request execution, result normalization, FactCard body formatting, background audience research job state, completed research context lookup.
  - Interface used by facade: `ResearchGateModule.request_sync()`, `ResearchGateModule.ensure_audience_worker()`, `ResearchGateModule.run_audience_worker()`, `ResearchGateModule.completed_context()`.
- Create: `YouTubeBridge/external_context.py`
  - Owns event filtering, context line construction, char limit, visible events, summary, and payload assembly.
  - Interface used by facade: `ExternalContextBuilder.build(session_id, event_ids=None, max_events=None)`.
- Modify: `YouTubeBridge/bridge_engine.py`
  - Keeps root-level `YouTubeBridgeManager` facade and existing public/private wrapper names for compatibility.
  - Delegates Research Gate and `external_context` work to the new Modules.
- Modify: `YouTubeBridge/tests/test_bridge_engine_research_fact_cards.py`
  - Add module-facing tests for Research Gate and keep high-level manager regression tests.
- Modify: `YouTubeBridge/tests/test_bridge_engine_topic_context.py`
  - Add module-facing tests for `ExternalContextBuilder` payload assembly and keep high-level manager regression tests.

---

### Task 1: Add Research Gate Module Shell

**Files:**
- Create: `YouTubeBridge/research_gate.py`
- Test: `YouTubeBridge/tests/test_bridge_engine_research_fact_cards.py`

- [ ] **Step 1: Write the failing import and construction test**

Add this test near the top of `YouTubeBridge/tests/test_bridge_engine_research_fact_cards.py`, after imports:

```python
def test_research_gate_module_can_be_constructed_with_manager_adapters():
    from research_gate import ResearchGateModule, TavilyResearchSearchAdapter

    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        manager = YouTubeBridgeManager(storage, memoria_client_factory=OffTopicEmbeddingMemoriaClient)

        module = ResearchGateModule(
            storage=storage,
            runtime_lookup=lambda session_id: manager._runtimes.setdefault(session_id, LiveRuntime(session_id=session_id)),
            topic_pack_context_text=manager._topic_pack_context_text,
            record_topic_pack_usage=manager._record_topic_pack_usage,
            index_topic_pack_entry=manager.index_topic_pack_entry,
            search_adapter=TavilyResearchSearchAdapter(),
        )

        assert module.storage is storage
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
```

- [ ] **Step 2: Run the focused failing test**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_research_fact_cards.py::test_research_gate_module_can_be_constructed_with_manager_adapters -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'research_gate'`.

- [ ] **Step 3: Create `research_gate.py` with the Module Interface**

Create `YouTubeBridge/research_gate.py`:

```python
"""Research Gate Module for YouTubeBridge live context."""
from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, timedelta
from typing import Any, Callable, Protocol

from bridge_runtime import LiveRuntime


logger = logging.getLogger("youtube_bridge")


class ResearchSearchAdapter(Protocol):
    def search(self, query: str) -> Any:
        """Return raw search result data for a Research Gate query."""


class TavilyResearchSearchAdapter:
    def search(self, query: str) -> Any:
        from tools.tavily import search_web

        return search_web(query=query, topic="general")


class ResearchGateModule:
    def __init__(
        self,
        *,
        storage,
        runtime_lookup: Callable[[str], LiveRuntime],
        topic_pack_context_text: Callable[[list[dict[str, Any]]], str],
        record_topic_pack_usage: Callable[[str, list[dict[str, Any]], str, str], Any],
        index_topic_pack_entry: Callable[[int], Any],
        search_adapter: ResearchSearchAdapter | None = None,
    ) -> None:
        self.storage = storage
        self._runtime_lookup = runtime_lookup
        self._topic_pack_context_text = topic_pack_context_text
        self._record_topic_pack_usage = record_topic_pack_usage
        self._index_topic_pack_entry = index_topic_pack_entry
        self._search_adapter = search_adapter or TavilyResearchSearchAdapter()

    def request_sync(
        self,
        session_id: str,
        query: str,
        *,
        pack_id: int | None = None,
        enforce_cooldown: bool = True,
    ) -> dict[str, Any]:
        session = self.storage.get_session(session_id)
        if not session:
            raise ValueError("live session 不存在")
        if not session.get("research_enabled"):
            raise ValueError("本場直播未啟用 Research Gate")
        query = str(query or "").strip()
        if not query:
            raise ValueError("research query 不可為空")
        cooldown = max(0, int(session.get("research_cooldown_seconds", 300) or 300))
        session_limit = max(0, int(session.get("research_max_per_session", 12) or 12))
        if session_limit and self.storage.count_research_requests(session_id) >= session_limit:
            raise ValueError("Research Gate 已達本場查詢上限")
        if enforce_cooldown and cooldown:
            since = (datetime.now() - timedelta(seconds=cooldown)).isoformat()
            if self.storage.count_research_requests(session_id, since_iso=since) >= 2:
                raise ValueError("Research Gate 冷卻中，稍後再查")
        target_pack_id = pack_id or self.first_session_topic_pack_id(session_id)
        if target_pack_id is None:
            pack = self.storage.create_topic_pack({
                "title": f"{session.get('display_name') or session_id} Research",
                "description": "Bridge Research Gate 自動建立的直播 fact cards。",
            })
            self.storage.link_topic_pack_to_session(session_id, int(pack["id"]))
            target_pack_id = int(pack["id"])
        try:
            raw_result = self._search_adapter.search(query)
        except Exception as exc:
            self.storage.create_research_request(session_id, query, status="failed", metadata={"error": str(exc)[:500]})
            raise
        body = self.research_result_to_fact_card(query, raw_result)
        research_meta = self.research_result_metadata(raw_result)
        entry = self.storage.create_topic_pack_entry(int(target_pack_id), {
            "title": query[:120],
            "body": body,
            "source_url": research_meta["source_urls"][0] if research_meta["source_urls"] else "",
            "source_type": "research_gate",
            "tags": ["research_gate"],
        })
        embedding = None
        try:
            embedding = self._index_topic_pack_entry(int(entry["id"]))
        except Exception as exc:
            logger.warning("research fact card embedding failed session_id=%s entry_id=%s error=%s", session_id, entry["id"], exc)
        record = self.storage.create_research_request(
            session_id,
            query,
            status=research_meta["status"],
            result_entry_id=int(entry["id"]),
            metadata={
                "pack_id": int(target_pack_id),
                "status": research_meta["status"],
                "source_count": len(research_meta["source_urls"]),
                "source_urls": research_meta["source_urls"],
                "source_titles": research_meta["source_titles"],
            },
        )
        return {
            "status": research_meta["status"],
            "source_count": len(research_meta["source_urls"]),
            "source_urls": research_meta["source_urls"],
            "entry": entry,
            "research": record,
            "record": record,
            "embedding": embedding,
        }
```

- [ ] **Step 4: Run the focused construction test**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_research_fact_cards.py::test_research_gate_module_can_be_constructed_with_manager_adapters -q
```

Expected: PASS.

- [ ] **Step 5: Commit Task 1**

```powershell
git add YouTubeBridge/research_gate.py YouTubeBridge/tests/test_bridge_engine_research_fact_cards.py
git commit -m "refactor: add research gate module shell"
```

---

### Task 2: Move Research Gate Behavior Behind the Module

**Files:**
- Modify: `YouTubeBridge/research_gate.py`
- Modify: `YouTubeBridge/bridge_engine.py`
- Test: `YouTubeBridge/tests/test_bridge_engine_research_fact_cards.py`

- [ ] **Step 1: Write a module-level request test with a fake search Adapter**

Add this test to `YouTubeBridge/tests/test_bridge_engine_research_fact_cards.py`:

```python
def test_research_gate_module_request_sync_creates_research_fact_card():
    from research_gate import ResearchGateModule

    class FakeSearchAdapter:
        def search(self, query: str):
            assert query == "最新一話聲優陣容"
            return {
                "results": [
                    {
                        "title": "官方聲優陣容公告",
                        "url": "https://example.test/cast",
                        "content": "官方公告列出主役聲優與配角聲優，社群討論集中在聲線變化。",
                    }
                ]
            }

    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({"connector_id": "yt-main", "display_name": "YouTube Main", "api_key": "key", "enabled": True})
        storage.upsert_session({"session_id": "live-a", "connector_id": "yt-main", "video_id": "video-a", "live_chat_id": "chat-a", "research_enabled": True})
        pack = storage.create_topic_pack({"title": "直播資料包"})
        storage.link_topic_pack_to_session("live-a", pack["id"])
        module = ResearchGateModule(
            storage=storage,
            runtime_lookup=lambda session_id: LiveRuntime(session_id=session_id),
            topic_pack_context_text=lambda entries: "\n".join(str(entry["body"]) for entry in entries),
            record_topic_pack_usage=lambda *_args, **_kwargs: None,
            index_topic_pack_entry=lambda _entry_id: None,
            search_adapter=FakeSearchAdapter(),
        )

        result = module.request_sync("live-a", "最新一話聲優陣容", pack_id=pack["id"], enforce_cooldown=True)

        assert result["status"] == "completed_with_results"
        assert result["entry"]["source_type"] == "research_gate"
        assert result["research"]["result_entry_id"] == result["entry"]["id"]
        assert "官方公告列出主役聲優" in result["entry"]["body"]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
```

- [ ] **Step 2: Run the focused failing test**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_research_fact_cards.py::test_research_gate_module_request_sync_creates_research_fact_card -q
```

Expected: FAIL with `AttributeError` for missing helper methods such as `first_session_topic_pack_id`, `research_result_to_fact_card`, or `research_result_metadata`.

- [ ] **Step 3: Move pure Research Gate helpers into `research_gate.py`**

Append these methods inside `ResearchGateModule` in `YouTubeBridge/research_gate.py`:

```python
    def first_session_topic_pack_id(self, session_id: str) -> int | None:
        packs = self.storage.list_session_topic_packs(session_id)
        if not packs:
            return None
        return int(packs[0]["id"])

    def completed_context(self, session_id: str, query_text: str) -> tuple[str, str]:
        query_key = self.audience_query_key(session_id, query_text)
        job = self.audience_research_job(session_id, query_key)
        status = str(job.get("status") or "")
        if status not in {"completed", "completed_with_results", "degraded"}:
            return "", status
        entry_id = int(job.get("entry_id") or 0)
        if not entry_id:
            return "", status
        entry = self.storage.get_topic_pack_entry(entry_id)
        if not entry:
            return "", status
        self._record_topic_pack_usage(session_id, [entry], query_text, "external_context")
        return self._topic_pack_context_text([entry]), status

    @staticmethod
    def audience_query_key(session_id: str, query_text: str) -> str:
        return uuid.uuid5(uuid.NAMESPACE_URL, f"youtube-live:{session_id}:{query_text}").hex

    @staticmethod
    def research_items(raw_result: Any) -> list[dict[str, str]]:
        raw = raw_result
        if isinstance(raw_result, str):
            stripped = raw_result.strip()
            try:
                raw = json.loads(stripped)
            except Exception:
                raw = {"search_results": [{"title": "Research Gate result", "url": "", "content": stripped}]}
        if isinstance(raw, dict):
            candidates = raw.get("results") or raw.get("search_results") or raw.get("items") or raw.get("data") or []
        elif isinstance(raw, list):
            candidates = raw
        else:
            candidates = []
        if isinstance(candidates, str):
            candidates = ResearchGateModule.legacy_research_text_items(candidates)

        items: list[dict[str, str]] = []
        for item in candidates[:8]:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or item.get("name") or item.get("source") or "").strip()
            url = str(item.get("url") or item.get("source_url") or item.get("link") or "").strip()
            content = str(item.get("content") or item.get("snippet") or item.get("summary") or item.get("body") or "").strip()
            if not any((title, url, content)):
                continue
            items.append({"title": title[:180], "url": url[:1000], "content": " ".join(content.replace("\r", " ").split())[:700]})
        return items

    @staticmethod
    def legacy_research_text_items(text: str) -> list[dict[str, str]]:
        blocks = [block.strip() for block in str(text or "").split("\n\n") if block.strip()]
        items: list[dict[str, str]] = []
        for block in blocks[:8]:
            lines = [line.strip() for line in block.splitlines() if line.strip()]
            if not lines:
                continue
            title = lines[0]
            if title.startswith("[") and "]" in title:
                title = title.split("]", 1)[1].strip()
            content = " ".join(lines[1:]).strip()
            items.append({"title": title[:180], "url": "", "content": content[:700]})
        return items

    @staticmethod
    def research_result_metadata(raw_result: Any) -> dict[str, Any]:
        items = ResearchGateModule.research_items(raw_result)
        source_titles = [item["title"] for item in items if item.get("title")][:5]
        source_urls = [item["url"] for item in items if item.get("url")][:5]
        return {
            "status": "completed_with_results" if items else "completed_no_results",
            "source_titles": source_titles,
            "source_urls": source_urls,
        }

    @staticmethod
    def research_result_to_fact_card(query: str, raw_result: Any) -> str:
        items = ResearchGateModule.research_items(raw_result)
        if not items:
            return (
                f"summary: Research Gate 查詢「{query}」沒有取得可用摘要。\n"
                "facts:\n"
                "- 目前沒有可引用的外部資料。\n"
                "source_titles:\n"
                "- none\n"
                "source_urls:\n"
                "- none\n"
                "confidence: low\n"
                "status: completed_no_results"
            )
        trusted_hosts = ("official", "anime", "news", "wikipedia", "wiki", "ann", "crunchyroll")
        ranked = sorted(
            items,
            key=lambda item: (
                0 if any(token in (item.get("url", "") + " " + item.get("title", "")).lower() for token in trusted_hosts) else 1,
                len(item.get("content", "")) * -1,
            ),
        )
        top = ranked[:4]
        facts = []
        for item in top:
            content = item.get("content") or item.get("title") or item.get("url") or ""
            if content:
                facts.append(content[:240])
        source_titles = [item.get("title") or "untitled" for item in top if item.get("title") or item.get("url")]
        source_urls = [item.get("url") for item in top if item.get("url")]
        summary_text = facts[0] if facts else f"Research Gate 查詢「{query}」取得 {len(items)} 筆來源。"
        lines = [
            f"summary: {summary_text}",
            "facts:",
            *[f"- {fact}" for fact in facts[:5]],
            "source_titles:",
            *[f"- {title}" for title in source_titles[:5]],
            "source_urls:",
            *[f"- {url}" for url in source_urls[:5]],
            "confidence: medium" if source_urls else "confidence: low",
            "status: completed_with_results",
        ]
        return "\n".join(lines)
```

- [ ] **Step 4: Move audience research job state into the Module**

Append these methods inside `ResearchGateModule`:

```python
    def ensure_audience_worker(self, session: dict[str, Any], query_text: str, *, pack_id: int | None = None) -> dict[str, Any]:
        session_id = str(session.get("session_id") or "")
        query_key = self.audience_query_key(session_id, query_text)
        runtime = self._runtime_lookup(session_id)
        thread = runtime.audience_research_tasks.get(query_key)
        thread_alive = bool(thread and thread.is_alive())
        existing = self.audience_research_job(session_id, query_key)
        if existing.get("in_progress") and thread_alive:
            return {**existing, "status": str(existing.get("status") or "running")}
        if str(existing.get("status") or "") in {"completed", "completed_with_results", "completed_no_results", "degraded", "failed"}:
            return existing
        if thread_alive:
            return {"status": "running", "query_key": query_key, "query": query_text}
        if thread:
            runtime.audience_research_tasks.pop(query_key, None)
        started_at = datetime.now().isoformat()
        self.update_audience_research_job(session_id, query_key, {
            "status": "queued",
            "in_progress": True,
            "query": query_text,
            "pack_id": int(pack_id) if pack_id else 0,
            "started_at": started_at,
            "updated_at": started_at,
            "error": "",
        })
        thread = threading.Thread(
            target=self.run_audience_worker,
            args=(session_id, query_key, query_text),
            kwargs={"pack_id": pack_id},
            name=f"audience-research-{session_id[:12]}",
            daemon=True,
        )
        runtime.audience_research_tasks[query_key] = thread
        thread.start()
        return {"status": "queued", "query_key": query_key, "query": query_text}

    def run_audience_worker(self, session_id: str, query_key: str, query_text: str, *, pack_id: int | None = None) -> None:
        self.update_audience_research_job(session_id, query_key, {
            "status": "running",
            "in_progress": True,
            "query": query_text,
            "pack_id": int(pack_id) if pack_id else 0,
            "updated_at": datetime.now().isoformat(),
            "error": "",
        })
        try:
            result = self.request_sync(session_id, query_text, pack_id=pack_id, enforce_cooldown=True)
            entry = result.get("entry") if isinstance(result, dict) else {}
            record = result.get("record") if isinstance(result, dict) else {}
            status = str((record or {}).get("status") or result.get("status") or "completed")
            self.update_audience_research_job(session_id, query_key, {
                "status": status,
                "in_progress": False,
                "query": query_text,
                "pack_id": int(pack_id) if pack_id else int((entry or {}).get("pack_id") or 0),
                "entry_id": int((entry or {}).get("id") or 0),
                "updated_at": datetime.now().isoformat(),
                "error": "",
            })
        except Exception as exc:
            self.update_audience_research_job(session_id, query_key, {
                "status": "failed",
                "in_progress": False,
                "query": query_text,
                "pack_id": int(pack_id) if pack_id else 0,
                "updated_at": datetime.now().isoformat(),
                "error": str(exc)[:500],
            })
            logger.warning("audience research worker failed session_id=%s error=%s", session_id, exc)
        finally:
            runtime = self._runtime_lookup(session_id)
            runtime.audience_research_tasks.pop(query_key, None)

    def audience_research_job(self, session_id: str, query_key: str) -> dict[str, Any]:
        state = self.storage.get_director_state(session_id)
        metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
        jobs = metadata.get("audience_query_research") if isinstance(metadata.get("audience_query_research"), dict) else {}
        job = jobs.get(query_key) if isinstance(jobs.get(query_key), dict) else {}
        return dict(job)

    def update_audience_research_job(self, session_id: str, query_key: str, fields: dict[str, Any]) -> dict[str, Any]:
        state = self.storage.get_director_state(session_id)
        metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
        jobs = dict(metadata.get("audience_query_research") or {})
        current = dict(jobs.get(query_key) or {})
        current.update(fields)
        current["query_key"] = query_key
        jobs[query_key] = current
        self.storage.update_director_state(session_id, metadata={"audience_query_research": jobs})
        return current
```

- [ ] **Step 5: Wire `YouTubeBridgeManager` to instantiate the Module**

Modify `YouTubeBridge/bridge_engine.py` imports:

```python
from research_gate import ResearchGateModule, TavilyResearchSearchAdapter
```

Add this at the end of `YouTubeBridgeManager.__init__()`:

```python
        self._research_gate = ResearchGateModule(
            storage=self.storage,
            runtime_lookup=lambda session_id: self._runtimes.setdefault(session_id, LiveRuntime(session_id=session_id)),
            topic_pack_context_text=self._topic_pack_context_text,
            record_topic_pack_usage=self._record_topic_pack_usage,
            index_topic_pack_entry=self.index_topic_pack_entry,
            search_adapter=TavilyResearchSearchAdapter(),
        )
```

- [ ] **Step 6: Replace manager Research Gate method bodies with delegation wrappers**

In `YouTubeBridge/bridge_engine.py`, keep the existing method names and replace their bodies with:

```python
    def _research_request_sync(
        self,
        session_id: str,
        query: str,
        *,
        pack_id: int | None = None,
        enforce_cooldown: bool = True,
    ) -> dict[str, Any]:
        return self._research_gate.request_sync(
            session_id,
            query,
            pack_id=pack_id,
            enforce_cooldown=enforce_cooldown,
        )

    def _ensure_audience_research_worker(
        self,
        session: dict[str, Any],
        query_text: str,
        *,
        pack_id: int | None = None,
    ) -> dict[str, Any]:
        return self._research_gate.ensure_audience_worker(session, query_text, pack_id=pack_id)

    def _run_audience_research_worker(
        self,
        session_id: str,
        query_key: str,
        query_text: str,
        *,
        pack_id: int | None = None,
    ) -> None:
        self._research_gate.run_audience_worker(session_id, query_key, query_text, pack_id=pack_id)

    @staticmethod
    def _audience_query_key(session_id: str, query_text: str) -> str:
        return ResearchGateModule.audience_query_key(session_id, query_text)

    def _audience_research_job(self, session_id: str, query_key: str) -> dict[str, Any]:
        return self._research_gate.audience_research_job(session_id, query_key)

    def _update_audience_research_job(self, session_id: str, query_key: str, fields: dict[str, Any]) -> dict[str, Any]:
        return self._research_gate.update_audience_research_job(session_id, query_key, fields)

    def _first_session_topic_pack_id(self, session_id: str) -> int | None:
        return self._research_gate.first_session_topic_pack_id(session_id)

    def _completed_audience_research_context(self, session_id: str, query_text: str) -> tuple[str, str]:
        return self._research_gate.completed_context(session_id, query_text)

    @staticmethod
    def _research_items(raw_result: Any) -> list[dict[str, str]]:
        return ResearchGateModule.research_items(raw_result)

    @staticmethod
    def _legacy_research_text_items(text: str) -> list[dict[str, str]]:
        return ResearchGateModule.legacy_research_text_items(text)

    @staticmethod
    def _research_result_metadata(raw_result: Any) -> dict[str, Any]:
        return ResearchGateModule.research_result_metadata(raw_result)

    @staticmethod
    def _research_result_to_fact_card(query: str, raw_result: Any) -> str:
        return ResearchGateModule.research_result_to_fact_card(query, raw_result)
```

- [ ] **Step 7: Run Research Gate regression tests**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_research_fact_cards.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit Task 2**

```powershell
git add YouTubeBridge/research_gate.py YouTubeBridge/bridge_engine.py YouTubeBridge/tests/test_bridge_engine_research_fact_cards.py
git commit -m "refactor: move research gate behavior behind module"
```

---

### Task 3: Add External Context Builder Module

**Files:**
- Create: `YouTubeBridge/external_context.py`
- Modify: `YouTubeBridge/bridge_engine.py`
- Test: `YouTubeBridge/tests/test_bridge_engine_topic_context.py`

- [ ] **Step 1: Write a builder-facing test for payload assembly**

Add this test to `YouTubeBridge/tests/test_bridge_engine_topic_context.py`:

```python
def test_external_context_builder_builds_payload_with_query_resolution():
    from external_context import ExternalContextBuilder

    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({"connector_id": "yt-main", "display_name": "YouTube Main", "api_key": "key", "enabled": True})
        storage.upsert_session({"session_id": "live-a", "connector_id": "yt-main", "video_id": "video-a", "live_chat_id": "chat-a"})
        event = storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt-main",
            "youtube_message_id": "msg-a",
            "message_text": "四月新番有哪些作品可以聊？",
            "author_display_name": "觀眾A",
        })
        _mark_event_clean(storage, event)

        manager = YouTubeBridgeManager(storage, memoria_client_factory=FakeEmbeddingMemoriaClient)
        builder = ExternalContextBuilder(
            storage=storage,
            event_line=manager._event_line,
            visible_event=manager._visible_event,
            is_public_live_event_displayable=manager._is_public_live_event_displayable,
            query_context_for_events=lambda session, events, lines: (
                "Topic Pack context",
                {"query": "四月新番", "research_status": "not_needed"},
            ),
            presentation_enabled=lambda session: False,
            attach_live_persona_overrides=lambda session, payload: payload,
        )

        payload, summary = builder.build("live-a")

        assert payload["context_text"] == "- 觀眾A: 四月新番有哪些作品可以聊？\nTopic Pack context"
        assert payload["event_ids"] == [event["id"]]
        assert summary["query_resolution"]["research_status"] == "not_needed"
        assert payload["summary"] is summary
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
```

- [ ] **Step 2: Run the focused failing test**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_topic_context.py::test_external_context_builder_builds_payload_with_query_resolution -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'external_context'`.

- [ ] **Step 3: Create `external_context.py` with the builder Interface**

Create `YouTubeBridge/external_context.py`:

```python
"""External context builder Module for YouTubeBridge live injection."""
from __future__ import annotations

from typing import Any, Callable


class ExternalContextBuilder:
    def __init__(
        self,
        *,
        storage,
        event_line: Callable[[dict[str, Any]], str],
        visible_event: Callable[[dict[str, Any]], dict[str, Any]],
        is_public_live_event_displayable: Callable[[dict[str, Any]], bool],
        query_context_for_events: Callable[[dict[str, Any], list[dict[str, Any]], list[str]], tuple[str, dict[str, Any]]],
        presentation_enabled: Callable[[dict[str, Any]], bool],
        attach_live_persona_overrides: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
    ) -> None:
        self.storage = storage
        self._event_line = event_line
        self._visible_event = visible_event
        self._is_public_live_event_displayable = is_public_live_event_displayable
        self._query_context_for_events = query_context_for_events
        self._presentation_enabled = presentation_enabled
        self._attach_live_persona_overrides = attach_live_persona_overrides

    def build(
        self,
        session_id: str,
        *,
        event_ids: list[int] | None = None,
        max_events: int | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        session = self.storage.get_session(session_id)
        if not session:
            raise ValueError("live session 不存在")
        limit = max(1, min(int(max_events or session.get("max_context_messages", 50)), 100))
        if event_ids:
            events = self.storage.get_events_by_ids(session_id, event_ids, limit=limit)
            events = [event for event in events if not event.get("injected_at")]
        else:
            events = self.storage.list_events(session_id, limit=limit, uninjected_only=True)
        active_events = [
            event
            for event in events
            if event.get("status") == "active"
            and event.get("message_text")
            and event.get("safety_status") == "completed"
            and self._is_public_live_event_displayable(event)
        ]
        hidden_event_ids = [
            int(event["id"])
            for event in events
            if event.get("status") == "active"
            and event.get("message_text")
            and event.get("safety_status") in {"completed", "failed"}
            and not self._is_public_live_event_displayable(event)
        ]
        if hidden_event_ids:
            self.storage.mark_events_injected(session_id, hidden_event_ids)

        lines: list[str] = []
        used_ids: list[int] = []
        visible_events: list[dict[str, Any]] = []
        max_chars = int(session.get("max_context_chars", 8000) or 8000)
        presentation_mode = self._presentation_enabled(session)
        if presentation_mode:
            max_chars = min(max_chars, 1200)
        used_chars = 0
        for event in active_events:
            line = self._event_line(event)
            next_len = len(line) + 1
            if lines and used_chars + next_len > max_chars:
                break
            lines.append(line)
            used_ids.append(int(event["id"]))
            if self._is_public_live_event_displayable(event):
                visible_events.append(self._visible_event(event))
            used_chars += next_len
        if not lines:
            raise ValueError("沒有可注入的直播留言")

        summary = {
            "source": "youtube_live",
            "source_session_id": session_id,
            "connector_id": session["connector_id"],
            "video_id": session.get("video_id", ""),
            "live_chat_id": session.get("live_chat_id", ""),
            "event_ids": used_ids,
            "event_count": len(used_ids),
            "hidden_unsafe_count": len(hidden_event_ids),
            "dropped_count": max(0, len(active_events) - len(used_ids)),
        }
        if presentation_mode:
            summary["presentation_enabled"] = True
            summary["group_turn_limit"] = 1
        topic_context, query_resolution = self._query_context_for_events(session, active_events, lines)
        summary["query_resolution"] = query_resolution
        context_parts = ["\n".join(lines), topic_context]
        if presentation_mode:
            context_parts.append(
                "直播輸出模式：請只產生一個短 spoken beat；避免多角色連續接話，讓前端播放完成後再進入下一輪。"
            )
        payload = {
            "source": "youtube_live",
            "source_session_id": session_id,
            "connector_id": session["connector_id"],
            "video_id": session.get("video_id", ""),
            "live_chat_id": session.get("live_chat_id", ""),
            "context_text": "\n".join([part for part in context_parts if part]),
            "event_ids": used_ids,
            "visible_events": visible_events,
            "max_chars": max_chars,
            "summary": summary,
        }
        if presentation_mode:
            payload["group_turn_limit"] = 1
        return self._attach_live_persona_overrides(session, payload), summary
```

- [ ] **Step 4: Wire `YouTubeBridgeManager` to instantiate the builder**

Modify `YouTubeBridge/bridge_engine.py` imports:

```python
from external_context import ExternalContextBuilder
```

Add this at the end of `YouTubeBridgeManager.__init__()`, after `_research_gate` is assigned:

```python
        self._external_context_builder = ExternalContextBuilder(
            storage=self.storage,
            event_line=self._event_line,
            visible_event=self._visible_event,
            is_public_live_event_displayable=self._is_public_live_event_displayable,
            query_context_for_events=self._live_query_context_for_events,
            presentation_enabled=self._presentation_enabled,
            attach_live_persona_overrides=self._attach_live_persona_overrides,
        )
```

- [ ] **Step 5: Replace `build_external_context()` body with delegation**

In `YouTubeBridge/bridge_engine.py`, replace the body of `build_external_context()` with:

```python
        return self._external_context_builder.build(
            session_id,
            event_ids=event_ids,
            max_events=max_events,
        )
```

- [ ] **Step 6: Run external context focused tests**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_topic_context.py::test_external_context_builder_builds_payload_with_query_resolution YouTubeBridge/tests/test_bridge_engine_topic_context.py::test_build_external_context_uses_compact_llm_lines -q
```

Expected: PASS.

- [ ] **Step 7: Commit Task 3**

```powershell
git add YouTubeBridge/external_context.py YouTubeBridge/bridge_engine.py YouTubeBridge/tests/test_bridge_engine_topic_context.py
git commit -m "refactor: move external context build behind module"
```

---

### Task 4: Collapse Research Query Resolution Into a Local Module Seam

**Files:**
- Modify: `YouTubeBridge/bridge_engine.py`
- Modify: `YouTubeBridge/research_gate.py`
- Test: `YouTubeBridge/tests/test_bridge_engine_research_fact_cards.py`

- [ ] **Step 1: Write a regression test for completed research reuse through the manager wrapper**

Add this test to `YouTubeBridge/tests/test_bridge_engine_research_fact_cards.py`:

```python
def test_manager_reuses_completed_audience_research_context_after_module_extraction():
    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({"connector_id": "yt-main", "display_name": "YouTube Main", "api_key": "key", "enabled": True})
        storage.upsert_session({"session_id": "live-a", "connector_id": "yt-main", "video_id": "video-a", "live_chat_id": "chat-a", "research_enabled": True})
        pack = storage.create_topic_pack({"title": "直播資料包"})
        storage.link_topic_pack_to_session("live-a", pack["id"])
        entry = storage.create_topic_pack_entry(pack["id"], {
            "title": "最新一話聲優陣容",
            "body": "summary: 官方公告列出主役聲優與配角聲優。",
            "source_type": "research_gate",
            "tags": ["research_gate"],
        })
        manager = YouTubeBridgeManager(storage, memoria_client_factory=OffTopicEmbeddingMemoriaClient)
        query = "最新一話的聲優陣容有什麼看點？"
        query_key = manager._audience_query_key("live-a", query)
        manager._update_audience_research_job("live-a", query_key, {
            "status": "completed_with_results",
            "in_progress": False,
            "query": query,
            "pack_id": pack["id"],
            "entry_id": entry["id"],
        })

        context, status = manager._completed_audience_research_context("live-a", query)

        assert status == "completed_with_results"
        assert "官方公告列出主役聲優" in context
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
```

- [ ] **Step 2: Run the focused test**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_research_fact_cards.py::test_manager_reuses_completed_audience_research_context_after_module_extraction -q
```

Expected: PASS after Tasks 1-3 are complete. If it fails, fix only the delegation between `YouTubeBridgeManager._completed_audience_research_context()` and `ResearchGateModule.completed_context()`.

- [ ] **Step 3: Move `_live_query_context_for_events()` only after Research Gate wrappers are stable**

Keep the manager method name for monkeypatch compatibility, but change its body to call a new `ResearchGateModule.live_query_context_for_events()` method.

Add this method to `ResearchGateModule`:

```python
    def live_query_context_for_events(
        self,
        *,
        session: dict[str, Any],
        events: list[dict[str, Any]],
        lines: list[str],
        audience_query_intent_from_events: Callable[[list[dict[str, Any]]], dict[str, Any]],
        topic_pack_sequence_context_for_session: Callable[[str, str], str],
        topic_pack_entries_for_query: Callable[[str, str], tuple[list[dict[str, Any]], dict[str, Any]]],
        audience_query_topic_terms: Callable[[str], list[str]],
        topic_pack_entry_matches_query_terms: Callable[[dict[str, Any], list[str]], bool],
        topic_pack_entries_can_answer: Callable[[list[dict[str, Any]], str], bool],
        topic_graph_context_entries_for_hits: Callable[[str, list[dict[str, Any]], str], list[dict[str, Any]]],
    ) -> tuple[str, dict[str, Any]]:
        session_id = str(session.get("session_id") or "")
        query_intent = audience_query_intent_from_events(events)
        query_text = str(query_intent.get("sanitized_query") or "").strip()
        resolution: dict[str, Any] = {
            "query": query_text,
            "query_intent": query_intent,
            "local_answerable": False,
            "local_entry_count": 0,
            "local_rejected_by_topic_count": 0,
            "local_top_similarity": None,
            "research_status": "not_needed" if not query_text else "not_attempted",
            "research_error": "",
        }
        base_query = "\n".join([*lines, str(session.get("director_guidance") or "")])
        if not query_text:
            context = topic_pack_sequence_context_for_session(session_id, base_query)
            return context, resolution

        entries, search_status = topic_pack_entries_for_query(session_id, query_text)
        resolution["local_entry_count"] = len(entries)
        resolution["local_top_similarity"] = search_status.get("top_similarity")
        query_terms = audience_query_topic_terms(query_text)
        topic_matched_entries = entries
        if query_terms:
            topic_matched_entries = [
                entry for entry in entries
                if topic_pack_entry_matches_query_terms(entry, query_terms)
            ]
            resolution["local_rejected_by_topic_count"] = max(0, len(entries) - len(topic_matched_entries))
        if topic_pack_entries_can_answer(topic_matched_entries, query_text):
            resolution["local_answerable"] = True
            resolution["research_status"] = "not_needed"
            context_entries = topic_graph_context_entries_for_hits(session_id, topic_matched_entries[:1], query_text)
            return self._topic_pack_context_text(context_entries), resolution

        if not session.get("research_enabled"):
            resolution["research_status"] = "disabled"
            return "", resolution
        if not query_intent.get("needs_external_search") or not query_intent.get("safe_search_allowed"):
            resolution["research_status"] = "not_allowed"
            return "", resolution

        completed_context, completed_status = self.completed_context(session_id, query_text)
        if completed_context:
            resolution["research_status"] = completed_status or "completed"
            return completed_context, resolution

        worker = self.ensure_audience_worker(session, query_text, pack_id=self.first_session_topic_pack_id(session_id))
        resolution["research_status"] = str(worker.get("status") or "queued")
        if worker.get("error"):
            resolution["research_error"] = str(worker.get("error") or "")[:300]
        if resolution["research_status"] in {"queued", "running"}:
            resolution["fallback_reason"] = "research_incomplete"
            return (
                "觀眾查詢資料狀態：相關查證仍在背景處理；"
                "本輪只能根據已知直播脈絡安全回應，不得宣稱已查到最新資料或具體排名。",
                resolution,
            )
        return "", resolution
```

- [ ] **Step 4: Keep the manager wrapper as the compatibility Adapter**

Replace `YouTubeBridgeManager._live_query_context_for_events()` body with:

```python
        return self._research_gate.live_query_context_for_events(
            session=session,
            events=events,
            lines=lines,
            audience_query_intent_from_events=self._audience_query_intent_from_events,
            topic_pack_sequence_context_for_session=lambda session_id, base_query: self._topic_pack_sequence_context_for_session(
                session_id,
                base_query,
                usage_source="external_context",
            ),
            topic_pack_entries_for_query=lambda session_id, query_text: self._topic_pack_entries_for_query(
                session_id,
                query_text,
                limit=6,
                min_score=AUDIENCE_QUERY_FACT_CARD_MIN_SCORE,
                allow_fallback=False,
            ),
            audience_query_topic_terms=self._audience_query_topic_terms,
            topic_pack_entry_matches_query_terms=self._topic_pack_entry_matches_query_terms,
            topic_pack_entries_can_answer=lambda entries, query_text: self._topic_pack_entries_can_answer(entries, query_text=query_text),
            topic_graph_context_entries_for_hits=lambda session_id, entries, query_text: self._topic_graph_context_entries_for_hits(
                session_id,
                entries,
                query_text,
                "external_context",
                max_entries=4,
            ),
        )
```

- [ ] **Step 5: Run audience research and topic context regressions**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_research_fact_cards.py YouTubeBridge/tests/test_bridge_engine_topic_context.py YouTubeBridge/tests/test_auto_inject_legacy.py::test_build_external_context_falls_back_when_audience_research_is_running -q
```

Expected: PASS.

- [ ] **Step 6: Commit Task 4**

```powershell
git add YouTubeBridge/research_gate.py YouTubeBridge/bridge_engine.py YouTubeBridge/tests/test_bridge_engine_research_fact_cards.py
git commit -m "refactor: route live query context through research gate module"
```

---

### Task 5: Clean Imports and Verify the Facade Stays Shallow

**Files:**
- Modify: `YouTubeBridge/bridge_engine.py`
- Test: no new test file

- [ ] **Step 1: Remove unused imports from `bridge_engine.py`**

After Tasks 1-4, run:

```powershell
python -m compileall YouTubeBridge/bridge_engine.py YouTubeBridge/research_gate.py YouTubeBridge/external_context.py
```

Expected: PASS. Then remove imports that are only used by moved code. Start by checking these imports:

```python
import threading
import uuid
from datetime import timedelta
```

Keep imports if PowerShell search proves they are still used:

```powershell
rg -n "\b(threading|uuid|timedelta)\b" YouTubeBridge/bridge_engine.py
```

- [ ] **Step 2: Verify facade wrappers remain thin**

Run:

```powershell
rg -n "def (_research_request_sync|_ensure_audience_research_worker|_run_audience_research_worker|_completed_audience_research_context|build_external_context)" YouTubeBridge/bridge_engine.py
```

Expected: each listed method exists in `bridge_engine.py`, and its body delegates to `_research_gate` or `_external_context_builder` without reintroducing Research Gate or payload assembly logic.

- [ ] **Step 3: Run targeted suite**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_research_fact_cards.py YouTubeBridge/tests/test_bridge_engine_topic_context.py YouTubeBridge/tests/test_bridge_engine_safety.py YouTubeBridge/tests/test_auto_inject_legacy.py -q
```

Expected: PASS.

- [ ] **Step 4: Run broader YouTubeBridge verification**

Run:

```powershell
$engineTests = (Get-ChildItem YouTubeBridge/tests/test_bridge_engine_*.py).FullName
python -m pytest $engineTests -q
```

Expected: PASS.

- [ ] **Step 5: Commit Task 5**

```powershell
git add YouTubeBridge/bridge_engine.py YouTubeBridge/research_gate.py YouTubeBridge/external_context.py
git commit -m "refactor: keep bridge engine research context facade shallow"
```

---

## Verification Matrix

- `python -m pytest YouTubeBridge/tests/test_bridge_engine_research_fact_cards.py -q`
- `python -m pytest YouTubeBridge/tests/test_bridge_engine_topic_context.py -q`
- `python -m pytest YouTubeBridge/tests/test_bridge_engine_safety.py -q`
- `python -m pytest YouTubeBridge/tests/test_auto_inject_legacy.py -q`
- `$engineTests = (Get-ChildItem YouTubeBridge/tests/test_bridge_engine_*.py).FullName; python -m pytest $engineTests -q`

If Windows pytest temp cleanup fails with `.pyTestTemp\basetemp` permission errors, run `scripts\cleanup_pytest_temp.bat` first, then rerun the same pytest command.

## Self-Review

Spec coverage:

- Research Gate manual request moves behind `ResearchGateModule.request_sync()`.
- Audience research worker state moves behind `ResearchGateModule.ensure_audience_worker()` and `run_audience_worker()`.
- Completed Research Gate result reuse moves behind `ResearchGateModule.completed_context()`.
- `external_context` event selection and payload assembly moves behind `ExternalContextBuilder.build()`.
- `bridge_engine.py` remains a root-level facade and keeps compatibility wrapper method names.

Placeholder scan:

- The plan avoids future-only placeholders and gives exact file paths, test names, command lines, expected outputs, and code blocks for each code step.

Type consistency:

- `ResearchGateModule` receives storage and callables in `__init__()`.
- `ExternalContextBuilder.build()` returns the same `(payload, summary)` shape as `YouTubeBridgeManager.build_external_context()`.
- Manager wrappers preserve existing method names used by tests and routes.
