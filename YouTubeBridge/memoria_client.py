"""MemoriaCore HTTP client。"""
from __future__ import annotations

import os
import json
import threading
from typing import Any

import requests


CSRF_HEADER_NAME = "X-CSRF-Token"
AUTH_COOKIE_NAME = "mc_auth"


class GenerationInterrupted(RuntimeError):
    """Bridge 要求中斷目前直播 generation。"""


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

    @staticmethod
    def _live_scope_payload(external_context: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(external_context, dict):
            return {}
        source = str(external_context.get("source") or "").strip()
        if source not in {"youtube_live", "youtube_live_director"}:
            return {}
        summary = external_context.get("summary") if isinstance(external_context.get("summary"), dict) else {}
        channel_uid = (
            str(summary.get("source_session_id") or "").strip()
            or str(external_context.get("source_session_id") or "").strip()
            or "youtube_live"
        )
        return {
            "channel": "youtube_live",
            "channel_uid": channel_uid[:128],
            "user_id": "__youtube_live__",
            "channel_class": "public",
            "persona_face": "public",
            "group_name": "YouTube Live",
        }

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
        display_content: str | None = None,
        session_id: str,
        character_ids: list[str] | None,
        external_context: dict[str, Any],
        include_speech: bool = False,
    ) -> dict[str, Any]:
        self.ensure_auth()
        payload = {
            "content": content,
            "display_content": display_content or None,
            "session_id": session_id or None,
            "character_ids": character_ids or None,
            "external_context": external_context,
            "include_speech": include_speech,
            "memory_write_policy": "transient" if external_context else "normal",
        }
        payload.update(self._live_scope_payload(external_context))
        response = self.session.post(
            f"{self.base_url}/chat/sync",
            json=payload,
            headers=self._headers(),
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"MemoriaCore chat failed: HTTP {response.status_code} {response.text[:500]}")
        return response.json()

    def chat_stream_sync(
        self,
        *,
        content: str,
        display_content: str | None = None,
        session_id: str,
        character_ids: list[str] | None,
        external_context: dict[str, Any],
        include_speech: bool = False,
        should_cancel=None,
        cancel_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        """使用 MemoriaCore SSE 路徑取得最終回應。

        若 should_cancel 回傳 True，會關閉 HTTP stream。MemoriaCore 端的 generator
        會停止等待結果，因此不會把未完成 assistant reply 寫回 session。
        """
        self.ensure_auth()
        payload = {
            "content": content,
            "display_content": display_content or None,
            "session_id": session_id or None,
            "character_ids": character_ids or None,
            "external_context": external_context,
            "include_speech": include_speech,
            "memory_write_policy": "transient" if external_context else "normal",
        }
        payload.update(self._live_scope_payload(external_context))
        last_result: dict[str, Any] | None = None
        watcher_done = threading.Event()
        with self.session.post(
            f"{self.base_url}/chat/stream-sync",
            json=payload,
            headers=self._headers(),
            timeout=self.timeout,
            stream=True,
        ) as response:
            if response.status_code >= 400:
                raise RuntimeError(f"MemoriaCore stream chat failed: HTTP {response.status_code} {response.text[:500]}")
            watcher: threading.Thread | None = None
            if cancel_event is not None:
                def _close_when_cancelled() -> None:
                    while not watcher_done.wait(0.1):
                        if cancel_event.is_set():
                            response.close()
                            return

                watcher = threading.Thread(target=_close_when_cancelled, daemon=True)
                watcher.start()
            try:
                for raw_line in response.iter_lines(decode_unicode=True):
                    if (cancel_event and cancel_event.is_set()) or (should_cancel and should_cancel()):
                        response.close()
                        raise GenerationInterrupted("generation interrupted")
                    if not raw_line or not raw_line.startswith("data:"):
                        continue
                    raw_data = raw_line[5:].strip()
                    if not raw_data:
                        continue
                    try:
                        event = json.loads(raw_data)
                    except Exception:
                        continue
                    if event.get("type") == "error":
                        raise RuntimeError(str(event.get("message") or "MemoriaCore stream chat failed"))
                    if event.get("type") == "result":
                        last_result = event
            finally:
                watcher_done.set()
                if watcher:
                    watcher.join(timeout=0.2)
        if (cancel_event and cancel_event.is_set()) or (should_cancel and should_cancel()):
            raise GenerationInterrupted("generation interrupted")
        if not last_result:
            raise RuntimeError("MemoriaCore stream chat ended without result")
        return last_result

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

    def get_session_messages(self, session_id: str) -> list[dict[str, Any]]:
        history = self.get_session_history(session_id)
        messages = history.get("messages") if isinstance(history, dict) else None
        return messages if isinstance(messages, list) else []

    def get_session_history(self, session_id: str) -> dict[str, Any]:
        self.ensure_auth()
        response = self.session.get(
            f"{self.base_url}/session/history/{session_id}",
            headers=self._headers(),
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"MemoriaCore session history failed: HTTP {response.status_code} {response.text[:500]}")
        data = response.json()
        return data if isinstance(data, dict) else {}

    def add_system_event(
        self,
        *,
        session_id: str,
        content: str,
        debug_info: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.ensure_auth()
        response = self.session.post(
            f"{self.base_url}/session/{session_id}/system-event",
            json={"content": content, "debug_info": debug_info or {}},
            headers=self._headers(),
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"MemoriaCore system event failed: HTTP {response.status_code} {response.text[:500]}")
        data = response.json()
        return data if isinstance(data, dict) else {}

    def generate_prompt_json(
        self,
        *,
        prompt_key: str,
        variables: dict[str, Any],
        task_key: str = "compress",
        temperature: float = 0.1,
        schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.ensure_auth()
        response = self.session.post(
            f"{self.base_url}/llm/prompt-json",
            json={
                "prompt_key": prompt_key,
                "variables": variables,
                "task_key": task_key,
                "temperature": temperature,
                "schema": schema,
            },
            headers=self._headers(),
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"MemoriaCore prompt JSON failed: HTTP {response.status_code} {response.text[:500]}")
        data = response.json()
        result = data.get("result") if isinstance(data, dict) else None
        return result if isinstance(result, dict) else {}

    def get_prompt_template(self, prompt_key: str) -> str:
        self.ensure_auth()
        response = self.session.get(
            f"{self.base_url}/llm/prompt-template/{prompt_key}",
            headers=self._headers(),
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"MemoriaCore prompt template failed: HTTP {response.status_code} {response.text[:500]}")
        data = response.json()
        return str(data.get("template") or "")

    def embed_text(self, text: str, model: str = "") -> dict[str, Any]:
        self.ensure_auth()
        response = self.session.post(
            f"{self.base_url}/llm/embed",
            json={"text": text, "model": model},
            headers=self._headers(),
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"MemoriaCore embedding failed: HTTP {response.status_code} {response.text[:500]}")
        data = response.json()
        return data if isinstance(data, dict) else {}

    def write_shared_youtube_memory(
        self,
        *,
        summary_id: int,
        session_id: str,
        video_id: str,
        memory_text: str,
        character_ids: list[str],
    ) -> dict[str, Any]:
        self.ensure_auth()
        response = self.session.post(
            f"{self.base_url}/memory/shared-youtube-summary",
            json={
                "summary_id": summary_id,
                "session_id": session_id,
                "video_id": video_id,
                "memory_text": memory_text,
                "character_ids": character_ids,
            },
            headers=self._headers(),
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"MemoriaCore shared memory write failed: HTTP {response.status_code} {response.text[:500]}")
        data = response.json()
        return data if isinstance(data, dict) else {}
