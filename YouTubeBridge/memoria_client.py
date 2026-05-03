"""MemoriaCore HTTP client。"""
from __future__ import annotations

import os
from typing import Any

import requests


CSRF_HEADER_NAME = "X-CSRF-Token"
AUTH_COOKIE_NAME = "mc_auth"


class MemoriaClient:
    def __init__(
        self,
        base_url: str | None = None,
        *,
        username: str | None = None,
        password: str | None = None,
        auth_cookie: str | None = None,
        csrf_token: str | None = None,
        admin_bypass: bool | None = None,
        timeout: float | None = None,
    ):
        self.base_url = (base_url or os.getenv("MEMORIACORE_BASE_URL") or "http://localhost:8088/api/v1").rstrip("/")
        self.username = username if username is not None else os.getenv("MEMORIACORE_USERNAME", "")
        self.password = password if password is not None else os.getenv("MEMORIACORE_PASSWORD", "")
        self.csrf_token = csrf_token if csrf_token is not None else os.getenv("MEMORIACORE_CSRF_TOKEN", "")
        if timeout is None:
            try:
                timeout = float(os.getenv("MEMORIACORE_TIMEOUT_SECONDS", "180"))
            except ValueError:
                timeout = 180.0
        self.timeout = timeout
        self.admin_bypass = (
            admin_bypass
            if admin_bypass is not None
            else os.getenv("MEMORIACORE_ADMIN_BYPASS", "").strip().lower() in {"1", "true", "yes", "on"}
        )
        self.session = requests.Session()
        cookie = auth_cookie if auth_cookie is not None else os.getenv("MEMORIACORE_AUTH_COOKIE", "")
        if cookie:
            self.session.cookies.set(AUTH_COOKIE_NAME, cookie)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.csrf_token:
            headers[CSRF_HEADER_NAME] = self.csrf_token
        return headers

    def ensure_auth(self) -> None:
        if self.session.cookies.get(AUTH_COOKIE_NAME) and self.csrf_token:
            return
        if self.username and self.password:
            response = self.session.post(
                f"{self.base_url}/auth/login",
                json={"username": self.username, "password": self.password},
                timeout=self.timeout,
            )
        elif self.admin_bypass:
            response = self.session.post(f"{self.base_url}/auth/bypass", timeout=self.timeout)
        else:
            raise RuntimeError("MemoriaCore auth 未設定；請設定帳密、cookie/csrf，或啟用 admin bypass")
        if response.status_code >= 400:
            raise RuntimeError(f"MemoriaCore auth failed: HTTP {response.status_code} {response.text[:300]}")
        data = response.json()
        self.csrf_token = data.get("csrf_token") or data.get("user", {}).get("csrf_token") or ""
        if not self.csrf_token:
            raise RuntimeError("MemoriaCore auth response 缺少 csrf_token")

    def chat_sync(
        self,
        *,
        content: str,
        session_id: str,
        character_ids: list[str] | None,
        external_context: dict[str, Any],
        include_speech: bool = False,
    ) -> dict[str, Any]:
        self.ensure_auth()
        payload = {
            "content": content,
            "session_id": session_id or None,
            "character_ids": character_ids or None,
            "external_context": external_context,
            "include_speech": include_speech,
        }
        response = self.session.post(
            f"{self.base_url}/chat/sync",
            json=payload,
            headers=self._headers(),
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"MemoriaCore chat failed: HTTP {response.status_code} {response.text[:500]}")
        return response.json()

    def list_characters(self) -> list[dict[str, Any]]:
        self.ensure_auth()
        response = self.session.get(
            f"{self.base_url}/character",
            headers=self._headers(),
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"MemoriaCore character list failed: HTTP {response.status_code} {response.text[:500]}")
        data = response.json()
        return data if isinstance(data, list) else []

    def list_sessions(self, *, limit: int = 100) -> list[dict[str, Any]]:
        self.ensure_auth()
        response = self.session.get(
            f"{self.base_url}/session/history",
            params={"limit": max(1, min(int(limit or 100), 200))},
            headers=self._headers(),
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"MemoriaCore session list failed: HTTP {response.status_code} {response.text[:500]}")
        data = response.json()
        return data if isinstance(data, list) else []
