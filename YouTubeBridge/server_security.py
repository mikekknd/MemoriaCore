"""YouTubeBridge FastAPI access control helpers。"""
from __future__ import annotations

import ipaddress
import os
import re
from typing import Any

from fastapi import HTTPException


LOOPBACK_ONLY_PATHS = frozenset({
    "/ui/",
    "/ui",
    "/studio/",
    "/studio",
    "/ui-config",
})
UI_ASSET_PATH_RE = re.compile(r"^/ui-assets/.+$")
STUDIO_AVATAR_PATH_RE = re.compile(r"^/studio/avatar-assets/.+$")
SSE_PATH_RE = re.compile(r"^/sessions/[^/]+/events$")
PRESENTATION_AUDIO_PATH_RE = re.compile(r"^/sessions/[^/]+/presentation/[^/]+/audio$")


def is_loopback_request(request: Any) -> bool:
    host = request.client.host if getattr(request, "client", None) else ""
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host in {"localhost"}


def require_bridge_key(request: Any) -> None:
    path = getattr(getattr(request, "url", None), "path", "")
    if (
        path in LOOPBACK_ONLY_PATHS
        or UI_ASSET_PATH_RE.match(path)
        or STUDIO_AVATAR_PATH_RE.match(path)
        or SSE_PATH_RE.match(path)
        or PRESENTATION_AUDIO_PATH_RE.match(path)
    ):
        if not is_loopback_request(request):
            raise HTTPException(status_code=403, detail="loopback access only")
        return
    expected = os.getenv("YOUTUBE_BRIDGE_API_KEY", "").strip()
    if expected:
        if request.headers.get("X-Bridge-Key") != expected:
            raise HTTPException(status_code=403, detail="invalid bridge key")
        return
    if not is_loopback_request(request):
        raise HTTPException(status_code=403, detail="invalid bridge key")
