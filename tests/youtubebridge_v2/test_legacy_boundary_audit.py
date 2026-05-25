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
