"""SSE response helpers with ASGI send timing instrumentation."""
from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterable, Callable
from datetime import datetime
from typing import Any

from starlette.responses import StreamingResponse
from starlette.types import Send


logger = logging.getLogger(__name__)
foreground_logger = logging.getLogger("youtube_bridge")

TIMED_SSE_TYPES = {
    "presentation_debug",
    "presentation_item_preload",
    "presentation_item_ready",
}


def _default_send_timing_recorder(record: dict[str, Any]) -> None:
    foreground_logger.warning("SSE_SEND_TIMING %s", json.dumps(record, ensure_ascii=False, sort_keys=True))


def _payload_item_id(payload: dict[str, Any]) -> str:
    item = payload.get("item")
    if isinstance(item, dict) and item.get("item_id"):
        return str(item.get("item_id"))
    event = payload.get("event")
    if isinstance(event, dict) and event.get("item_id"):
        return str(event.get("item_id"))
    if payload.get("item_id"):
        return str(payload.get("item_id"))
    return ""


class InstrumentedSseResponse(StreamingResponse):
    """StreamingResponse variant that records ASGI body send boundaries."""

    def __init__(
        self,
        content: AsyncIterable[str | bytes | memoryview],
        *,
        timed_types: set[str] | None = None,
        send_timing_recorder: Callable[[dict[str, Any]], None] | None = None,
        log_context: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        kwargs.setdefault("media_type", "text/event-stream")
        super().__init__(content, **kwargs)
        self.timed_types = set(timed_types or TIMED_SSE_TYPES)
        self._send_timing_recorder = send_timing_recorder or _default_send_timing_recorder
        self._log_context = dict(log_context or {})
        self._sse_send_sequence = 0

    async def stream_response(self, send: Send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": self.status_code,
                "headers": self.raw_headers,
            }
        )
        async for chunk in self.body_iterator:
            if isinstance(chunk, memoryview):
                body = chunk.tobytes()
            elif isinstance(chunk, bytes):
                body = chunk
            else:
                body = chunk.encode(self.charset)

            body, metadata = self._instrument_body(body)
            if not metadata:
                await send({"type": "http.response.body", "body": body, "more_body": True})
                continue

            start_perf = time.perf_counter()
            self._record({**metadata, "phase": "send_start"})
            try:
                await send({"type": "http.response.body", "body": body, "more_body": True})
            except Exception as exc:
                self._record({**metadata, "phase": "send_error", "error": repr(exc)})
                raise
            done_at = datetime.now().isoformat()
            self._record(
                {
                    **metadata,
                    "phase": "send_done",
                    "sse_send_done_at": done_at,
                    "send_elapsed_ms": round((time.perf_counter() - start_perf) * 1000, 3),
                }
            )

        await send({"type": "http.response.body", "body": b"", "more_body": False})

    def _record(self, record: dict[str, Any]) -> None:
        try:
            self._send_timing_recorder(record)
        except Exception:
            logger.exception("Failed to record SSE send timing")

    def _instrument_body(self, body: bytes) -> tuple[bytes, dict[str, Any] | None]:
        try:
            text = body.decode(self.charset)
        except UnicodeDecodeError:
            return body, None
        if not text.startswith("data: "):
            return body, None
        payload_text = text[len("data: "):].strip()
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError:
            return body, None
        if not isinstance(payload, dict):
            return body, None
        event_type = str(payload.get("type") or "")
        if event_type not in self.timed_types:
            return body, None

        self._sse_send_sequence += 1
        send_start_at = datetime.now().isoformat()
        payload = {**payload, "_sse_send_start_at": send_start_at}
        metadata = {
            **self._log_context,
            "event_type": event_type,
            "item_id": _payload_item_id(payload),
            "sse_yield_at": payload.get("_sse_yield_at", ""),
            "sse_send_start_at": send_start_at,
            "broadcast_at": payload.get("_broadcast_at", ""),
            "send_sequence": self._sse_send_sequence,
        }
        encoded = f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode(self.charset)
        return encoded, metadata
