from __future__ import annotations

import re
from pathlib import Path

from fastapi.routing import APIRoute

from YouTubeBridgeV2.server.routes import router


ROOT = Path(__file__).resolve().parents[2]
DOCS_ROOT = ROOT / "YouTubeBridgeV2" / "docs"
HTTP_ENDPOINT_RE = re.compile(r"`((?:GET|POST|DELETE|PUT|PATCH) /v2[^`]+)`")
ENDPOINT_NAME_RE = re.compile(r"`([a-z_]+_endpoint)`")


def _route_endpoints() -> set[str]:
    endpoints: set[str] = set()
    for route in router.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            endpoints.add(f"{method} {route.path_format}")
    return endpoints


def _route_endpoint_names() -> set[str]:
    return {
        route.name
        for route in router.routes
        if isinstance(route, APIRoute)
    }


def _section(text: str, start_marker: str, end_marker: str) -> str:
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    return text[start:end]


def _documented_endpoints(section: str) -> set[str]:
    return set(HTTP_ENDPOINT_RE.findall(section))


def _documented_endpoint_names(section: str) -> set[str]:
    return set(ENDPOINT_NAME_RE.findall(section))


def test_api_reference_server_surface_lists_all_v2_routes_and_endpoint_names():
    api_reference = (DOCS_ROOT / "api-reference-index.md").read_text(encoding="utf-8")
    section = _section(api_reference, "### Server/API Surface", "### API Key Management Endpoints")

    assert _documented_endpoints(section) == _route_endpoints()
    assert _documented_endpoint_names(section) == _route_endpoint_names()


def test_server_api_surface_module_lists_all_v2_routes_and_endpoint_names():
    module_doc = (DOCS_ROOT / "modules" / "server-api-surface.md").read_text(
        encoding="utf-8"
    )
    section = _section(module_doc, "## Public Entrypoints", "## Endpoint Boundary Rules")

    assert _documented_endpoints(section) == _route_endpoints()
    assert _documented_endpoint_names(section) == _route_endpoint_names()


def test_docs_api_reference_sync_is_documented_in_architecture_index():
    architecture_index = (DOCS_ROOT / "architecture-index.md").read_text(
        encoding="utf-8"
    )

    assert "Docs/API reference sync" in architecture_index
    assert "test_docs_api_reference_sync.py" in architecture_index
