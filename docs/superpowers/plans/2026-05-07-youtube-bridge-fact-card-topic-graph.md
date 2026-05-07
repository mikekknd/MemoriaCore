# YouTubeBridge Fact Card Topic Graph Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a first usable Topic Graph for YouTubeBridge FactCards, with graph-aware retrieval traces and an admin debug graph view.

**Architecture:** Keep existing Topic Pack entries and embeddings as the semantic retrieval entry point. Add graph tables and a small graph builder/retriever layer that links documents, topics, detail cards, entities, and references, then expands semantic hits through bounded graph edges before context injection.

**Tech Stack:** Python 3.12, SQLite through `BridgeStorage`, FastAPI routes in YouTubeBridge, browser-native HTML/CSS/JS for the admin debug graph.

---

## File Structure

- Create `YouTubeBridge/topic_graph.py`: pure helpers for entity extraction, node key normalization, graph building payloads, graph-aware selection, context formatting, and trace payload shaping.
- Modify `YouTubeBridge/storage_schema.py`: add `topic_graph_nodes`, `topic_graph_edges`, and `topic_graph_retrieval_traces` tables plus indexes.
- Modify `YouTubeBridge/storage_mappers.py`: add mappers for graph nodes, graph edges, and retrieval traces.
- Modify `YouTubeBridge/storage_repositories/topic_packs.py`: add graph CRUD, graph rebuild clearing, trace CRUD, and graph listing APIs.
- Modify `YouTubeBridge/engine_topic_packs.py`: rebuild graph after FactCards import, add graph-aware retrieval wrappers, and record traces.
- Modify `YouTubeBridge/bridge_engine.py`: format graph-aware context and route live query context through graph-aware retrieval.
- Modify `YouTubeBridge/engine_director_runtime.py`: use graph-aware sequence/deepening retrieval for director turns.
- Modify `YouTubeBridge/server_routes/topic_packs.py`: add graph rebuild/list and session trace endpoints.
- Modify `YouTubeBridge/static/ui/core.js`: store graph and trace UI state.
- Modify `YouTubeBridge/static/ui/topic-packs.js`: fetch graph/trace data and render an SVG force-style debug graph.
- Modify `YouTubeBridge/static/ui/app.js`: bind graph refresh/rebuild buttons.
- Modify `YouTubeBridge/static/index.html`: add the debug graph controls and canvas under Topic Pack admin UI.
- Modify `YouTubeBridge/tests/test_storage.py`: cover graph schema, CRUD, and traces.
- Modify `YouTubeBridge/tests/test_fact_cards.py`: cover graph build from overview/deep-dive FactCards.
- Modify `YouTubeBridge/tests/test_bridge_engine.py`: cover graph-aware retrieval behavior and context constraints.
- Modify `YouTubeBridge/tests/test_server_auth.py`: cover debug graph UI/API route exposure if existing UI route tests are already anchored there.

## Task 1: Graph Storage Layer

**Files:**
- Modify: `YouTubeBridge/storage_schema.py`
- Modify: `YouTubeBridge/storage_mappers.py`
- Modify: `YouTubeBridge/storage_repositories/topic_packs.py`
- Test: `YouTubeBridge/tests/test_storage.py`

- [ ] **Step 1: Write failing storage tests**

Add tests that create a pack, upsert graph nodes/edges, list graph data, replace graph data for a pack, and record/list retrieval traces.

- [ ] **Step 2: Run failing tests**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_storage.py -q
```

Expected before implementation: tests fail because graph storage methods do not exist.

- [ ] **Step 3: Implement schema and repository methods**

Add graph tables to schema initialization and repository methods with these public names:

- `replace_topic_graph(pack_id, nodes, edges)`
- `get_topic_graph(pack_id)`
- `record_topic_graph_retrieval_trace(session_id, pack_id, trace)`
- `list_topic_graph_retrieval_traces(session_id, limit=20)`
- `get_latest_topic_graph_retrieval_trace(session_id)`

- [ ] **Step 4: Run storage tests**

Run the same test command. Expected: storage tests pass.

## Task 2: Graph Builder

**Files:**
- Create: `YouTubeBridge/topic_graph.py`
- Modify: `YouTubeBridge/engine_topic_packs.py`
- Test: `YouTubeBridge/tests/test_fact_cards.py`

- [ ] **Step 1: Write failing graph build tests**

Use a small overview card and a deep-dive card. Assert that graph rebuild creates document/category/topic/detail/entity nodes and `contains`, `detail_of`, `mentions`, and `compare_with` edges.

- [ ] **Step 2: Run failing tests**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_fact_cards.py -q
```

Expected before implementation: tests fail because graph builder/rebuild does not exist.

- [ ] **Step 3: Implement builder**

Implement deterministic rule-based extraction:

- `《...》` and parenthesized aliases become entity candidates.
- Filenames or document titles containing `深挖`, `細節`, or `補充` produce detail nodes.
- Overview documents produce category/topic nodes.
- Mentions and comparison words produce typed edges.

- [ ] **Step 4: Rebuild graph after FactCards import**

Call graph rebuild after `_import_fact_card_paths_to_pack()` creates entries. Keep import failures isolated from graph rebuild failures by returning graph status in the import payload.

- [ ] **Step 5: Run FactCards tests**

Run the same test command. Expected: FactCards tests pass.

## Task 3: Graph-Aware Retrieval and Trace Recording

**Files:**
- Modify: `YouTubeBridge/topic_graph.py`
- Modify: `YouTubeBridge/engine_topic_packs.py`
- Modify: `YouTubeBridge/bridge_engine.py`
- Modify: `YouTubeBridge/engine_director_runtime.py`
- Test: `YouTubeBridge/tests/test_bridge_engine.py`

- [ ] **Step 1: Write failing retrieval tests**

Add tests for:

- Query `魔法帽攻頂` selects the topic plus a detail node.
- Query `榜單拉鋸` may include a Re:ZERO reference without exceeding the cross-reference cap.
- Repeated usage lowers the same detail and advances to another detail.
- Unrelated query still does not fallback to unrelated FactCards.

- [ ] **Step 2: Run failing tests**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine.py -q
```

Expected before implementation: graph-aware context assertions fail.

- [ ] **Step 3: Implement graph selection**

Add a selector that starts from vector hits, maps entries to graph nodes, expands bounded `detail_of`, `mentions`, and `compare_with` edges, applies novelty from usage stats, returns selected entries/nodes, and records selected/rejected trace data.

- [ ] **Step 4: Integrate context formatting**

Format graph context as:

```text
<topic_pack_fact_cards>
召回策略：...
- [入口] ...
- [深挖] ...
- [關聯] ...
</topic_pack_fact_cards>
```

Keep debug scores out of LLM-facing context.

- [ ] **Step 5: Run bridge engine tests**

Run the same test command. Expected: bridge engine tests pass.

## Task 4: Graph Debug API

**Files:**
- Modify: `YouTubeBridge/server_routes/topic_packs.py`
- Test: `YouTubeBridge/tests/test_server_auth.py`

- [ ] **Step 1: Write failing API tests**

Cover graph list, graph rebuild, trace list, and latest trace endpoints. Assert hidden context fields are not returned.

- [ ] **Step 2: Run failing tests**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_server_auth.py -q
```

Expected before implementation: route tests fail with 404.

- [ ] **Step 3: Implement routes**

Add:

- `GET /topic-packs/{pack_id}/graph`
- `POST /topic-packs/{pack_id}/graph/rebuild`
- `GET /sessions/{session_id}/topic-graph/traces?limit=20`
- `GET /sessions/{session_id}/topic-graph/latest-trace`

- [ ] **Step 4: Run API tests**

Run the same test command. Expected: route tests pass.

## Task 5: Debug Graph UI

**Files:**
- Modify: `YouTubeBridge/static/index.html`
- Modify: `YouTubeBridge/static/ui/core.js`
- Modify: `YouTubeBridge/static/ui/topic-packs.js`
- Modify: `YouTubeBridge/static/ui/app.js`
- Test: `YouTubeBridge/tests/test_server_auth.py`

- [ ] **Step 1: Write failing UI route test**

Assert the control UI exposes graph debug controls: graph status, graph SVG, refresh/rebuild buttons, and selected node panel.

- [ ] **Step 2: Run failing UI test**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_server_auth.py -q
```

Expected before implementation: UI test fails because elements are missing.

- [ ] **Step 3: Implement minimal debug graph UI**

Render an SVG network with deterministic circular layout for first version. Include dark background, node/edge coloring by type, latest trace highlight, click-to-select node, and side panel metadata.

- [ ] **Step 4: Run UI tests**

Run the same test command. Expected: UI test passes.

## Task 6: Focused Regression

**Files:**
- Verify only.

- [ ] **Step 1: Run focused regression suite**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_fact_cards.py YouTubeBridge/tests/test_bridge_engine.py YouTubeBridge/tests/test_storage.py YouTubeBridge/tests/test_server_auth.py tests/test_chat_external_context.py -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Inspect git status**

Run:

```powershell
git status -sb
```

Expected: only intended Topic Graph files are modified in addition to pre-existing unrelated dirty files.

- [ ] **Step 3: Commit implementation files only**

Stage the exact files changed for this feature. Do not use `git add -A`.
