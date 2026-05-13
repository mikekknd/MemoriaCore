# Legacy Boundary Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Final Hardening audit that proves YouTubeBridgeV2 production source and the main app V2 mount do not depend on the legacy `YouTubeBridge/` runtime, direct SQLite, or unapproved external transports.

**Architecture:** Keep this item as an audit layer, not a behavior rewrite. The new test file will scan AST imports for V2 Python source plus `api/main.py`, and scan production source text for direct legacy path/runtime tokens. Existing module-level tests remain in place; this item adds a single final-hardening view and documents the result in architecture/API indexes.

**Tech Stack:** pytest, Python `ast`, `pathlib`, docs index updates.

---

## Scope Boundary

- Implement only `Final Hardening / Legacy boundary audit`.
- Do not edit `YouTubeBridgeV2/docs/roadmap.md` checkbox state.
- Do not refactor runtime behavior unless the audit exposes a real violation.
- Do not perform the standalone `docs/API reference sync`, final code review, or PR readiness items.
- Do not scan implementation-plan Markdown as a runtime source of truth; plans intentionally mention old modules as historical boundaries.
- Tests may scan `api/main.py` because production V2 is mounted there, but must not broaden into unrelated app routers.

## File Structure

- Create `tests/youtubebridge_v2/test_legacy_boundary_audit.py`
  - Owns final hardening import/text audits and architecture status check.
- Modify `YouTubeBridgeV2/docs/architecture-index.md`
  - Add Final Hardening legacy boundary audit status.
- Modify `YouTubeBridgeV2/docs/api-reference-index.md`
  - Add an internal audit reference pointing to the final hardening test.

---

### Task 1: Final Hardening Audit Tests

**Files:**
- Create: `tests/youtubebridge_v2/test_legacy_boundary_audit.py`

- [ ] **Step 1: Add audit tests**

Create `tests/youtubebridge_v2/test_legacy_boundary_audit.py`:

```python
from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
V2_ROOT = ROOT / "YouTubeBridgeV2"


def _python_source_files() -> list[Path]:
    return sorted(V2_ROOT.rglob("*.py")) + [ROOT / "api" / "main.py"]


def _production_text_files() -> list[Path]:
    suffixes = {".py", ".js", ".html", ".css"}
    return sorted(
        path
        for path in V2_ROOT.rglob("*")
        if path.is_file()
        and path.suffix in suffixes
        and "docs" not in path.parts
    )


def _imported_modules(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.append(node.module or "")
    return modules


def _relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _is_forbidden_import(module: str) -> bool:
    forbidden_exact_or_prefix = (
        "googleapiclient",
        "google.oauth",
        "requests",
        "sqlite3",
        "aiosqlite",
    )
    if module == "YouTubeBridge" or module.startswith("YouTubeBridge."):
        return True
    return any(
        module == forbidden or module.startswith(f"{forbidden}.")
        for forbidden in forbidden_exact_or_prefix
    )


def test_final_hardening_source_has_no_legacy_runtime_or_banned_transport_imports():
    violations: list[tuple[str, str]] = []

    for path in _python_source_files():
        for module in _imported_modules(path):
            if _is_forbidden_import(module):
                violations.append((_relative(path), module))

    assert violations == []


def test_final_hardening_source_has_no_direct_legacy_runtime_path_references():
    forbidden_tokens = (
        "YouTubeBridge/",
        r"YouTubeBridge\\",
        "YouTubeBridge.bridge_engine",
        "YouTubeBridge.engine",
        "bridge_engine.py",
    )
    violations: list[tuple[str, str]] = []

    for path in _production_text_files():
        text = path.read_text(encoding="utf-8")
        for token in forbidden_tokens:
            if token in text:
                violations.append((_relative(path), token))

    assert violations == []


def test_legacy_boundary_audit_is_documented_in_architecture_index():
    architecture_index = (V2_ROOT / "docs" / "architecture-index.md").read_text(
        encoding="utf-8"
    )

    assert "Legacy boundary audit" in architecture_index
    assert "test_legacy_boundary_audit.py" in architecture_index
```

- [ ] **Step 2: Run the new audit tests and verify the docs status test fails**

```powershell
python -m pytest tests\youtubebridge_v2\test_legacy_boundary_audit.py -q
```

Expected: import/path scans pass and `test_legacy_boundary_audit_is_documented_in_architecture_index` fails because the Final Hardening status has not been documented yet.

---

### Task 2: Document the Legacy Boundary Audit

**Files:**
- Modify: `YouTubeBridgeV2/docs/architecture-index.md`
- Modify: `YouTubeBridgeV2/docs/api-reference-index.md`

- [ ] **Step 1: Update Final Hardening architecture status**

In `YouTubeBridgeV2/docs/architecture-index.md`, add this bullet under `## Final Hardening 狀態`:

```markdown
- [x] Legacy boundary audit：`tests/youtubebridge_v2/test_legacy_boundary_audit.py` 集中掃描 V2 production source 與 `api/main.py` 的 V2 mount，確認沒有 legacy `YouTubeBridge/` runtime import、直接 SQLite、`requests`/Google YouTube transport 或直接 legacy runtime path dependency。
```

- [ ] **Step 2: Update API reference index with internal audit reference**

In `YouTubeBridgeV2/docs/api-reference-index.md`, add this entry near the internal hardening references:

```markdown
### `tests/youtubebridge_v2/test_legacy_boundary_audit.py`

Purpose:
Final Hardening audit，集中驗證 V2 production source 與主 app V2 mount 不依賴 legacy `YouTubeBridge/` runtime、直接 SQLite 或未批准外部 transport。

Params:
- 無。

Returns:
- pytest pass/fail audit result。

Raises:
- AssertionError — source import 或 direct legacy path reference 違反 V2 boundary 時。

Side Effects:
- 無；只讀取 source/docs。

Since:
- `YouTubeBridgeV2 v0.1`

Stability:
- `internal`

Source:
- `tests/youtubebridge_v2/test_legacy_boundary_audit.py`
```

- [ ] **Step 3: Verify docs references**

```powershell
rg -n "Legacy boundary audit|test_legacy_boundary_audit|direct legacy runtime" YouTubeBridgeV2\docs
```

Expected: hits in architecture index, API reference index, and this implementation plan.

- [ ] **Step 4: Re-run the audit tests**

```powershell
python -m pytest tests\youtubebridge_v2\test_legacy_boundary_audit.py -q
```

Expected: `3 passed`.

---

### Task 3: Focused and Full Verification

**Files:**
- Verify: `tests/youtubebridge_v2/test_legacy_boundary_audit.py`
- Verify: `YouTubeBridgeV2/docs/architecture-index.md`
- Verify: `YouTubeBridgeV2/docs/api-reference-index.md`
- Verify: `YouTubeBridgeV2/docs/implementation-plans/legacy-boundary-audit.md`

- [ ] **Step 1: Run related boundary suites**

```powershell
python -m pytest tests\youtubebridge_v2\test_legacy_boundary_audit.py tests\youtubebridge_v2\test_youtube_ingestion_boundaries.py tests\youtubebridge_v2\test_main_app_wiring.py tests\youtubebridge_v2\test_storage.py -q
```

Expected: all tests pass.

- [ ] **Step 2: Run full V2 suite**

```powershell
python -m pytest tests\youtubebridge_v2 -q
```

Expected: all non-opt-in tests pass; browser/external opt-in tests skip unless their env vars are explicitly set.

- [ ] **Step 3: Run diff checks**

```powershell
git diff --check
git diff -- YouTubeBridgeV2\docs\roadmap.md
```

Expected: `git diff --check` exits 0. Roadmap diff is empty.

- [ ] **Step 4: Commit this item only**

```powershell
git add tests\youtubebridge_v2\test_legacy_boundary_audit.py YouTubeBridgeV2\docs\architecture-index.md YouTubeBridgeV2\docs\api-reference-index.md YouTubeBridgeV2\docs\implementation-plans\legacy-boundary-audit.md
git commit -m "test: audit V2 legacy boundaries"
```

Expected: one commit containing only the Legacy boundary audit item.

---

## Self-Review

- Spec coverage: Covers only Final Hardening / Legacy boundary audit. It does not implement the standalone docs/API reference sync, final code review, or PR readiness items.
- Placeholder scan: No `TBD`, no open-ended TODO, no unspecified validation.
- Type consistency: `_python_source_files()` returns `Path` values consumed by `_imported_modules()`, and the architecture documentation assertion matches the exact filename added by this plan.

Plan complete and saved to `YouTubeBridgeV2/docs/implementation-plans/legacy-boundary-audit.md`. Because the user asked to continue the roadmap goal directly, execute inline with `superpowers:executing-plans` for this single checklist item.
