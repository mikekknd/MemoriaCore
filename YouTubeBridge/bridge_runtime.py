"""YouTubeBridge live session runtime state。"""
from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field


@dataclass
class LiveRuntime:
    session_id: str
    mode: str = "youtube"
    task: asyncio.Task | None = None
    inject_task: asyncio.Task | None = None
    director_task: asyncio.Task | None = None
    director_kickoff_task: asyncio.Task | None = None
    test_event_task: asyncio.Task | None = None
    safety_task: asyncio.Task | None = None
    running: bool = False
    status: str = "stopped"
    next_page_token: str | None = None
    last_error: str | None = None
    last_auto_inject_at: str | None = None
    last_auto_inject_error: str | None = None
    last_auto_test_event_at: str | None = None
    last_auto_test_event_error: str | None = None
    last_sc_interrupt_at: str | None = None
    subscribers: set[asyncio.Queue] = field(default_factory=set)
    inject_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    safety_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    closing_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    cancel_events: dict[str, threading.Event] = field(default_factory=dict)
    audience_research_tasks: dict[str, threading.Thread] = field(default_factory=dict)
    presentation_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    presentation_sequence_condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    presentation_next_sequence: int = 0
    presentation_present_sequence: int = 0
    presentation_skipped_sequences: set[int] = field(default_factory=set)
    presentation_ack_events: dict[str, asyncio.Event] = field(default_factory=dict)
    director_prefetch_in_flight: int = 0
    post_plan_free_talk_topic_queue: list[dict[str, str]] = field(default_factory=list)
