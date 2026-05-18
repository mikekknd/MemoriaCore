import json
import sys
from pathlib import Path

import pytest


BRIDGE_ROOT = Path(__file__).resolve().parents[1]
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))

from server_routes.sse_response import InstrumentedSseResponse, _default_send_timing_recorder


@pytest.mark.asyncio
async def test_instrumented_sse_response_records_asgi_body_send_boundaries():
    records = []
    sent_messages = []

    async def chunks():
        yield (
            'data: {"type":"presentation_item_ready","item":{"item_id":"item-1"},'
            '"_sse_yield_at":"2026-05-19T10:00:00"}\n\n'
        )
        yield ": ping\n\n"

    async def send(message):
        sent_messages.append(message)

    response = InstrumentedSseResponse(
        chunks(),
        timed_types={"presentation_item_ready"},
        send_timing_recorder=records.append,
        log_context={"session_id": "session-1"},
    )

    await response.stream_response(send)

    body_messages = [
        message
        for message in sent_messages
        if message["type"] == "http.response.body" and message.get("body")
    ]
    ready_body = body_messages[0]["body"].decode("utf-8")
    payload = json.loads(ready_body.removeprefix("data: ").strip())

    assert payload["_sse_yield_at"] == "2026-05-19T10:00:00"
    assert payload["_sse_send_start_at"]
    assert len(records) == 2
    assert records[0]["phase"] == "send_start"
    assert records[0]["event_type"] == "presentation_item_ready"
    assert records[0]["session_id"] == "session-1"
    assert records[0]["item_id"] == "item-1"
    assert records[0]["sse_yield_at"] == "2026-05-19T10:00:00"
    assert records[0]["sse_send_start_at"] == payload["_sse_send_start_at"]
    assert records[1]["phase"] == "send_done"
    assert records[1]["event_type"] == "presentation_item_ready"
    assert records[1]["item_id"] == "item-1"
    assert records[1]["sse_send_start_at"] == payload["_sse_send_start_at"]
    assert records[1]["sse_send_done_at"]
    assert records[1]["send_elapsed_ms"] >= 0


def test_default_send_timing_recorder_uses_foreground_visible_logger(caplog):
    caplog.set_level("WARNING", logger="youtube_bridge")

    _default_send_timing_recorder({
        "event_type": "presentation_item_ready",
        "phase": "send_start",
        "item_id": "item-1",
    })

    assert any(
        record.name == "youtube_bridge"
        and record.levelname == "WARNING"
        and record.getMessage().startswith("SSE_SEND_TIMING ")
        for record in caplog.records
    )
