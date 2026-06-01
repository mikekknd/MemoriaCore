import asyncio
import contextlib
import json
import shutil
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from bridge_engine_test_support import (
    BRIDGE_ROOT,
    BridgeStorage,
    CapturingDirectorDecisionClient,
    ContractOnlyQueryClient,
    FakeBatchRecordingSafetyClient,
    FakeClosingFailingSafetyClient,
    FakeClosingMemoriaClient,
    FakeClosingSystemEventClient,
    FakeEmbeddingMemoriaClient,
    FakeFailingSafetyMemoriaClient,
    FakeSafetyMemoriaClient,
    LiveEndedClient,
    LiveRuntime,
    OffTopicEmbeddingMemoriaClient,
    OneMessagePollingClient,
    ResolveLiveChatFailedClient,
    YouTubeBridgeManager,
    _mark_event_clean,
    _tmp_dir,
    bridge_engine,
    normalize_message,
)


def _pending_lifecycle_runtime_tasks() -> set[asyncio.Task]:
    current = asyncio.current_task()
    names = {
        "DirectorRuntimeManagerMixin._director_kickoff",
        "DirectorRuntimeManagerMixin._director_loop",
        "InjectionManagerMixin._auto_inject_loop",
        "TestRuntimeManagerMixin._auto_test_event_loop",
        "YouTubeBridgeManager._poll_loop",
    }
    return {
        task
        for task in asyncio.all_tasks()
        if task is not current
        and not task.done()
        and (
            getattr(task.get_coro(), "__qualname__", "") in names
            or "audience_preprocessing_loop" in getattr(task.get_coro(), "__qualname__", "")
        )
    }


async def _cancel_tasks(tasks: set[asyncio.Task]) -> None:
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_poll_loop_marks_live_chat_ended():
    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "api_key": "key",
            "enabled": True,
        })
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "video_id": "video-a",
            "live_chat_id": "chat-a",
            "auto_connect": True,
        })
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())

        await manager.start_session("live-a")
        for _ in range(20):
            if storage.get_session("live-a")["status"] == "ended":
                break
            await asyncio.sleep(0.05)

        session = storage.get_session("live-a")
        assert session["status"] == "ended"
        assert session["finalized_at"]
        assert session["summary_status"] == "pending"
        assert manager.get_status("live-a")["running"] is False
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

@pytest.mark.asyncio
async def test_poll_loop_classifies_and_broadcasts_clean_live_event(monkeypatch):
    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "api_key": "key",
            "enabled": True,
        })
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "video_id": "video-a",
            "live_chat_id": "chat-a",
            "auto_connect": True,
        })
        manager = YouTubeBridgeManager(storage, youtube_client=OneMessagePollingClient())
        monkeypatch.setattr(manager, "_memoria_client", lambda: FakeSafetyMemoriaClient())
        queue = await manager.subscribe("live-a")

        await manager.start_session("live-a")
        payloads = []
        for _ in range(40):
            while not queue.empty():
                payloads.append(await queue.get())
            if any(payload.get("type") == "youtube_live_event" for payload in payloads):
                break
            await asyncio.sleep(0.05)
        await manager.stop_session("live-a")

        live_events = [payload["event"] for payload in payloads if payload.get("type") == "youtube_live_event"]
        assert live_events
        assert live_events[0]["message_text"] == "即時測試留言"
        assert live_events[0]["safety_status"] == "completed"
        assert live_events[0]["safety_label"] == "clean"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

@pytest.mark.asyncio
async def test_start_session_without_video_id_uses_test_mode_without_clearing_llm_trace(monkeypatch):
    tmp_dir = _tmp_dir()
    try:
        trace_path = tmp_dir / "runtime" / "llm_trace.jsonl"
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        trace_path.write_text("stale trace\n", encoding="utf-8")
        monkeypatch.setattr(bridge_engine, "DEFAULT_LLM_TRACE_PATH", trace_path)
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "enabled": True,
        })
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "display_name": "QA Live",
            "auto_inject": True,
        })
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())

        status = await manager.start_session("live-a")

        session = storage.get_session("live-a")
        assert status["running"] is True
        assert status["mode"] == "test"
        assert session["status"] == "running"
        assert session["started_at"]
        assert trace_path.read_text(encoding="utf-8") == "stale trace\n"

        stopped = await manager.stop_session("live-a")
        assert stopped["running"] is False
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_start_session_without_audience_preprocessing_hooks_leaves_worker_idle(monkeypatch):
    tmp_dir = _tmp_dir()
    try:
        trace_path = tmp_dir / "runtime" / "llm_trace.jsonl"
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(bridge_engine, "DEFAULT_LLM_TRACE_PATH", trace_path)
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "enabled": True,
        })
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "display_name": "QA Live",
            "auto_inject": True,
        })
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())

        status = await manager.start_session("live-a")
        runtime = manager._runtimes["live-a"]

        assert status["running"] is True
        assert runtime.audience_preprocess_task is None
        await manager.stop_session("live-a")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_start_session_running_runtime_ignores_later_partial_audience_hooks(monkeypatch):
    tmp_dir = _tmp_dir()
    manager = None
    try:
        trace_path = tmp_dir / "runtime" / "llm_trace.jsonl"
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(bridge_engine, "DEFAULT_LLM_TRACE_PATH", trace_path)
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "enabled": True,
        })
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "display_name": "QA Live",
            "auto_inject": True,
        })
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())

        first_status = await manager.start_session("live-a")
        runtime = manager._runtimes["live-a"]
        monkeypatch.setattr(manager, "_audience_preprocessing_enabled", lambda _session: True, raising=False)

        second_status = await manager.start_session("live-a")

        assert first_status["running"] is True
        assert second_status["running"] is True
        assert second_status["status"] == "running"
        assert manager._runtimes.get("live-a") is runtime
        assert runtime.running is True
        assert runtime.status == "running"
        assert storage.get_session("live-a")["status"] == "running"
    finally:
        if manager is not None:
            await manager.stop_all()
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_hook", ["loop", "enabled"])
async def test_start_session_rejects_partial_audience_preprocessing_hooks(monkeypatch, missing_hook):
    tmp_dir = _tmp_dir()
    try:
        trace_path = tmp_dir / "runtime" / "llm_trace.jsonl"
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(bridge_engine, "DEFAULT_LLM_TRACE_PATH", trace_path)
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "enabled": True,
        })
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "display_name": "QA Live",
            "auto_inject": True,
        })
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())
        if missing_hook == "loop":
            monkeypatch.setattr(manager, "_audience_preprocessing_enabled", lambda _session: True, raising=False)
            monkeypatch.setattr(manager, "_audience_preprocessing_loop", None, raising=False)
        else:
            async def fake_audience_preprocessing_loop(_runtime):
                await asyncio.Event().wait()

            monkeypatch.setattr(manager, "_audience_preprocessing_enabled", None, raising=False)
            monkeypatch.setattr(
                manager,
                "_audience_preprocessing_loop",
                fake_audience_preprocessing_loop,
                raising=False,
            )

        with pytest.raises(RuntimeError, match="audience preprocessing lifecycle requires both"):
            await manager.start_session("live-a")
    finally:
        await manager.stop_all()
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_start_session_rejects_partial_audience_hooks_before_youtube_side_effects(monkeypatch):
    class CountingResolveClient:
        def __init__(self):
            self.calls = 0

        def resolve_live_chat_id(self, **_kwargs):
            self.calls += 1
            return "chat-resolved"

    tmp_dir = _tmp_dir()
    try:
        trace_path = tmp_dir / "runtime" / "llm_trace.jsonl"
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(bridge_engine, "DEFAULT_LLM_TRACE_PATH", trace_path)
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "api_key": "key",
            "enabled": True,
        })
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "display_name": "Real YouTube Live",
            "video_id": "video-a",
            "auto_test_events_enabled": True,
        })
        youtube_client = CountingResolveClient()
        manager = YouTubeBridgeManager(storage, youtube_client=youtube_client)
        disable_test_events_calls = 0

        def fake_disable_test_events(_session_id, _session=None):
            nonlocal disable_test_events_calls
            disable_test_events_calls += 1
            return True

        monkeypatch.setattr(
            manager,
            "_disable_test_events_for_real_youtube_session",
            fake_disable_test_events,
        )
        monkeypatch.setattr(manager, "_audience_preprocessing_enabled", lambda _session: True, raising=False)
        monkeypatch.setattr(manager, "_audience_preprocessing_loop", None, raising=False)

        with pytest.raises(RuntimeError, match="audience preprocessing lifecycle requires both"):
            await manager.start_session("live-a")

        session = storage.get_session("live-a")
        assert youtube_client.calls == 0
        assert disable_test_events_calls == 0
        assert session.get("live_chat_id") in {None, ""}
        assert session["status"] == "stopped"
    finally:
        await manager.stop_all()
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_start_session_rejects_non_callable_audience_preprocessing_hook(monkeypatch):
    tmp_dir = _tmp_dir()
    try:
        trace_path = tmp_dir / "runtime" / "llm_trace.jsonl"
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(bridge_engine, "DEFAULT_LLM_TRACE_PATH", trace_path)
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "enabled": True,
        })
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "display_name": "QA Live",
            "auto_inject": True,
        })
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())
        monkeypatch.setattr(manager, "_audience_preprocessing_enabled", True, raising=False)

        async def fake_audience_preprocessing_loop(_runtime):
            await asyncio.Event().wait()

        monkeypatch.setattr(
            manager,
            "_audience_preprocessing_loop",
            fake_audience_preprocessing_loop,
            raising=False,
        )

        with pytest.raises(RuntimeError, match="audience preprocessing lifecycle hooks must be callable"):
            await manager.start_session("live-a")
    finally:
        await manager.stop_all()
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_start_session_rejects_async_audience_preprocessing_enabled_without_side_effects(monkeypatch):
    class CountingResolveClient:
        def __init__(self):
            self.calls = 0

        def resolve_live_chat_id(self, **_kwargs):
            self.calls += 1
            return "chat-resolved"

    tmp_dir = _tmp_dir()
    leaked_tasks: set[asyncio.Task] = set()
    baseline_tasks = _pending_lifecycle_runtime_tasks()
    manager = None
    try:
        trace_path = tmp_dir / "runtime" / "llm_trace.jsonl"
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(bridge_engine, "DEFAULT_LLM_TRACE_PATH", trace_path)
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "api_key": "key",
            "enabled": True,
        })
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "display_name": "Real YouTube Live",
            "video_id": "video-a",
            "auto_inject": True,
        })
        youtube_client = CountingResolveClient()
        manager = YouTubeBridgeManager(storage, youtube_client=youtube_client)
        disable_test_events_calls = 0

        def fake_disable_test_events(_session_id, _session=None):
            nonlocal disable_test_events_calls
            disable_test_events_calls += 1
            return True

        async def async_audience_preprocessing_enabled(_session):
            return True

        async def fake_audience_preprocessing_loop(_runtime):
            await asyncio.Event().wait()

        monkeypatch.setattr(
            manager,
            "_disable_test_events_for_real_youtube_session",
            fake_disable_test_events,
        )
        monkeypatch.setattr(
            manager,
            "_audience_preprocessing_enabled",
            async_audience_preprocessing_enabled,
            raising=False,
        )
        monkeypatch.setattr(
            manager,
            "_audience_preprocessing_loop",
            fake_audience_preprocessing_loop,
            raising=False,
        )

        with pytest.raises(RuntimeError, match="audience preprocessing enabled hook must be synchronous"):
            await manager.start_session("live-a")
        await asyncio.sleep(0)
        leaked_tasks = _pending_lifecycle_runtime_tasks() - baseline_tasks

        session = storage.get_session("live-a")
        assert leaked_tasks == set()
        assert "live-a" not in manager._runtimes
        assert youtube_client.calls == 0
        assert disable_test_events_calls == 0
        assert session.get("live_chat_id") in {None, ""}
        assert session["status"] == "stopped"
    finally:
        await _cancel_tasks(leaked_tasks or (_pending_lifecycle_runtime_tasks() - baseline_tasks))
        if manager is not None:
            await manager.stop_all()
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_start_session_rejects_enabled_hook_returning_task_without_orphan(monkeypatch):
    class CountingResolveClient:
        def __init__(self):
            self.calls = 0

        def resolve_live_chat_id(self, **_kwargs):
            self.calls += 1
            return "chat-resolved"

    tmp_dir = _tmp_dir()
    leaked_tasks: set[asyncio.Task] = set()
    baseline_tasks = _pending_lifecycle_runtime_tasks()
    manager = None
    enabled_task = None
    enabled_task_cancelled = asyncio.Event()
    try:
        trace_path = tmp_dir / "runtime" / "llm_trace.jsonl"
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(bridge_engine, "DEFAULT_LLM_TRACE_PATH", trace_path)
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "api_key": "key",
            "enabled": True,
        })
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "display_name": "Real YouTube Live",
            "video_id": "video-a",
            "auto_inject": True,
        })
        youtube_client = CountingResolveClient()
        manager = YouTubeBridgeManager(storage, youtube_client=youtube_client)
        disable_test_events_calls = 0

        def fake_disable_test_events(_session_id, _session=None):
            nonlocal disable_test_events_calls
            disable_test_events_calls += 1
            return True

        async def pending_enabled_result():
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                enabled_task_cancelled.set()
                raise

        enabled_task = asyncio.create_task(pending_enabled_result())

        async def fake_audience_preprocessing_loop(_runtime):
            await asyncio.Event().wait()

        monkeypatch.setattr(manager, "_audience_preprocessing_enabled", lambda _session: enabled_task, raising=False)
        monkeypatch.setattr(manager, "_audience_preprocessing_loop", fake_audience_preprocessing_loop, raising=False)
        monkeypatch.setattr(
            manager,
            "_disable_test_events_for_real_youtube_session",
            fake_disable_test_events,
        )

        with pytest.raises(RuntimeError, match="audience preprocessing enabled hook must be synchronous"):
            await manager.start_session("live-a")
        await asyncio.sleep(0)
        leaked_tasks = _pending_lifecycle_runtime_tasks() - baseline_tasks

        session = storage.get_session("live-a")
        assert leaked_tasks == set()
        assert enabled_task.done()
        assert enabled_task.cancelled()
        assert "live-a" not in manager._runtimes
        assert youtube_client.calls == 0
        assert disable_test_events_calls == 0
        assert session.get("live_chat_id") in {None, ""}
        assert session["status"] == "stopped"
    finally:
        if enabled_task is not None and not enabled_task.done():
            enabled_task.cancel()
            await asyncio.gather(enabled_task, return_exceptions=True)
        await _cancel_tasks(leaked_tasks or (_pending_lifecycle_runtime_tasks() - baseline_tasks))
        if manager is not None:
            await manager.stop_all()
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_start_session_cleans_up_when_audience_preprocessing_enabled_raises(monkeypatch):
    class CountingResolveClient:
        def __init__(self):
            self.calls = 0

        def resolve_live_chat_id(self, **_kwargs):
            self.calls += 1
            return "chat-resolved"

    tmp_dir = _tmp_dir()
    leaked_tasks: set[asyncio.Task] = set()
    baseline_tasks = _pending_lifecycle_runtime_tasks()
    manager = None
    try:
        trace_path = tmp_dir / "runtime" / "llm_trace.jsonl"
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(bridge_engine, "DEFAULT_LLM_TRACE_PATH", trace_path)
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "api_key": "key",
            "enabled": True,
        })
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "display_name": "Real YouTube Live",
            "video_id": "video-a",
            "auto_inject": True,
        })
        youtube_client = CountingResolveClient()
        manager = YouTubeBridgeManager(storage, youtube_client=youtube_client)
        disable_test_events_calls = 0

        def fake_disable_test_events(_session_id, _session=None):
            nonlocal disable_test_events_calls
            disable_test_events_calls += 1
            return True

        monkeypatch.setattr(
            manager,
            "_disable_test_events_for_real_youtube_session",
            fake_disable_test_events,
        )

        def raising_audience_preprocessing_enabled(_session):
            raise RuntimeError("audience preprocessing enabled failed")

        async def fake_audience_preprocessing_loop(_runtime):
            await asyncio.Event().wait()

        monkeypatch.setattr(
            manager,
            "_audience_preprocessing_enabled",
            raising_audience_preprocessing_enabled,
            raising=False,
        )
        monkeypatch.setattr(
            manager,
            "_audience_preprocessing_loop",
            fake_audience_preprocessing_loop,
            raising=False,
        )

        with pytest.raises(RuntimeError, match="audience preprocessing enabled failed"):
            await manager.start_session("live-a")
        await asyncio.sleep(0)
        leaked_tasks = _pending_lifecycle_runtime_tasks() - baseline_tasks

        assert leaked_tasks == set()
        assert "live-a" not in manager._runtimes
        session = storage.get_session("live-a")
        assert youtube_client.calls == 0
        assert disable_test_events_calls == 0
        assert session.get("live_chat_id") in {None, ""}
        assert session["status"] == "stopped"
    finally:
        await _cancel_tasks(leaked_tasks or (_pending_lifecycle_runtime_tasks() - baseline_tasks))
        if manager is not None:
            await manager.stop_all()
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_start_session_rejects_invalid_audience_preprocessing_loop_without_orphans(monkeypatch):
    class CountingResolveClient:
        def __init__(self):
            self.calls = 0

        def resolve_live_chat_id(self, **_kwargs):
            self.calls += 1
            return "chat-resolved"

    tmp_dir = _tmp_dir()
    leaked_tasks: set[asyncio.Task] = set()
    baseline_tasks = _pending_lifecycle_runtime_tasks()
    manager = None
    try:
        trace_path = tmp_dir / "runtime" / "llm_trace.jsonl"
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(bridge_engine, "DEFAULT_LLM_TRACE_PATH", trace_path)
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "api_key": "key",
            "enabled": True,
        })
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "display_name": "Real YouTube Live",
            "video_id": "video-a",
            "auto_inject": True,
        })
        youtube_client = CountingResolveClient()
        manager = YouTubeBridgeManager(storage, youtube_client=youtube_client)
        disable_test_events_calls = 0

        def fake_disable_test_events(_session_id, _session=None):
            nonlocal disable_test_events_calls
            disable_test_events_calls += 1
            return True

        monkeypatch.setattr(
            manager,
            "_disable_test_events_for_real_youtube_session",
            fake_disable_test_events,
        )
        monkeypatch.setattr(manager, "_audience_preprocessing_enabled", lambda _session: True, raising=False)
        monkeypatch.setattr(manager, "_audience_preprocessing_loop", lambda _runtime: None, raising=False)

        with pytest.raises((RuntimeError, TypeError)) as exc_info:
            await manager.start_session("live-a")
        await asyncio.sleep(0)
        leaked_tasks = _pending_lifecycle_runtime_tasks() - baseline_tasks

        assert leaked_tasks == set()
        assert "audience preprocessing" in str(exc_info.value)
        assert "live-a" not in manager._runtimes
        session = storage.get_session("live-a")
        assert youtube_client.calls == 0
        assert disable_test_events_calls == 0
        assert session.get("live_chat_id") in {None, ""}
        assert session["status"] == "stopped"
    finally:
        await _cancel_tasks(leaked_tasks or (_pending_lifecycle_runtime_tasks() - baseline_tasks))
        if manager is not None:
            await manager.stop_all()
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_start_session_rejects_audience_loop_returning_precreated_task(monkeypatch):
    class CountingResolveClient:
        def __init__(self):
            self.calls = 0

        def resolve_live_chat_id(self, **_kwargs):
            self.calls += 1
            return "chat-resolved"

    tmp_dir = _tmp_dir()
    leaked_tasks: set[asyncio.Task] = set()
    baseline_tasks = _pending_lifecycle_runtime_tasks()
    manager = None
    loop_task = None
    loop_task_cancelled = asyncio.Event()
    try:
        trace_path = tmp_dir / "runtime" / "llm_trace.jsonl"
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(bridge_engine, "DEFAULT_LLM_TRACE_PATH", trace_path)
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "api_key": "key",
            "enabled": True,
        })
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "display_name": "Real YouTube Live",
            "video_id": "video-a",
            "auto_inject": True,
        })
        youtube_client = CountingResolveClient()
        manager = YouTubeBridgeManager(storage, youtube_client=youtube_client)
        disable_test_events_calls = 0

        def fake_disable_test_events(_session_id, _session=None):
            nonlocal disable_test_events_calls
            disable_test_events_calls += 1
            return True

        async def precreated_audience_task():
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                loop_task_cancelled.set()
                raise

        loop_task = asyncio.create_task(precreated_audience_task())

        monkeypatch.setattr(manager, "_audience_preprocessing_enabled", lambda _session: True, raising=False)
        monkeypatch.setattr(manager, "_audience_preprocessing_loop", lambda _runtime: loop_task, raising=False)
        monkeypatch.setattr(
            manager,
            "_disable_test_events_for_real_youtube_session",
            fake_disable_test_events,
        )

        with pytest.raises(RuntimeError, match="audience preprocessing loop must return a coroutine"):
            await manager.start_session("live-a")
        await asyncio.sleep(0)
        leaked_tasks = _pending_lifecycle_runtime_tasks() - baseline_tasks

        session = storage.get_session("live-a")
        assert leaked_tasks == set()
        assert loop_task.done()
        assert loop_task.cancelled()
        assert "live-a" not in manager._runtimes
        assert youtube_client.calls == 0
        assert disable_test_events_calls == 0
        assert session.get("live_chat_id") in {None, ""}
        assert session["status"] == "stopped"
    finally:
        if loop_task is not None and not loop_task.done():
            loop_task.cancel()
            await asyncio.gather(loop_task, return_exceptions=True)
        await _cancel_tasks(leaked_tasks or (_pending_lifecycle_runtime_tasks() - baseline_tasks))
        if manager is not None:
            await manager.stop_all()
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_start_session_missing_youtube_api_key_rolls_back_starting_status(monkeypatch):
    tmp_dir = _tmp_dir()
    leaked_tasks: set[asyncio.Task] = set()
    baseline_tasks = _pending_lifecycle_runtime_tasks()
    manager = None
    try:
        trace_path = tmp_dir / "runtime" / "llm_trace.jsonl"
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(bridge_engine, "DEFAULT_LLM_TRACE_PATH", trace_path)
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "enabled": True,
        })
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "display_name": "Real YouTube Live",
            "video_id": "video-a",
            "auto_inject": True,
            "status": "starting",
        })
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())

        with pytest.raises(ValueError, match="YouTube API key"):
            await manager.start_session("live-a")
        await asyncio.sleep(0)
        leaked_tasks = _pending_lifecycle_runtime_tasks() - baseline_tasks

        assert leaked_tasks == set()
        assert "live-a" not in manager._runtimes
        assert storage.get_session("live-a")["status"] == "stopped"
    finally:
        await _cancel_tasks(leaked_tasks or (_pending_lifecycle_runtime_tasks() - baseline_tasks))
        if manager is not None:
            await manager.stop_all()
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_start_session_resolve_failure_rolls_back_running_status_without_orphans(monkeypatch):
    class FailingResolveClient:
        def __init__(self):
            self.calls = 0

        def resolve_live_chat_id(self, **_kwargs):
            self.calls += 1
            raise RuntimeError("resolve failed")

    tmp_dir = _tmp_dir()
    leaked_tasks: set[asyncio.Task] = set()
    baseline_tasks = _pending_lifecycle_runtime_tasks()
    manager = None
    try:
        trace_path = tmp_dir / "runtime" / "llm_trace.jsonl"
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(bridge_engine, "DEFAULT_LLM_TRACE_PATH", trace_path)
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "api_key": "key",
            "enabled": True,
        })
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "display_name": "Real YouTube Live",
            "video_id": "video-a",
            "auto_inject": True,
            "status": "running",
        })
        youtube_client = FailingResolveClient()
        manager = YouTubeBridgeManager(storage, youtube_client=youtube_client)

        with pytest.raises(RuntimeError, match="resolve failed"):
            await manager.start_session("live-a")
        await asyncio.sleep(0)
        leaked_tasks = _pending_lifecycle_runtime_tasks() - baseline_tasks

        session = storage.get_session("live-a")
        assert leaked_tasks == set()
        assert youtube_client.calls == 1
        assert "live-a" not in manager._runtimes
        assert session.get("live_chat_id") in {None, ""}
        assert session["status"] == "stopped"
    finally:
        await _cancel_tasks(leaked_tasks or (_pending_lifecycle_runtime_tasks() - baseline_tasks))
        if manager is not None:
            await manager.stop_all()
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_start_session_late_startup_failure_cleans_registered_runtime_tasks(monkeypatch):
    tmp_dir = _tmp_dir()
    leaked_tasks: set[asyncio.Task] = set()
    baseline_tasks = _pending_lifecycle_runtime_tasks()
    manager = None
    try:
        trace_path = tmp_dir / "runtime" / "llm_trace.jsonl"
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(bridge_engine, "DEFAULT_LLM_TRACE_PATH", trace_path)
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "enabled": True,
        })
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "display_name": "QA Live",
            "auto_inject": True,
        })
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())
        worker_started = asyncio.Event()

        async def fake_audience_preprocessing_loop(_runtime):
            worker_started.set()
            await asyncio.Event().wait()

        async def failing_broadcast(_session_id, _payload):
            raise RuntimeError("broadcast failed after startup tasks")

        monkeypatch.setattr(manager, "_audience_preprocessing_enabled", lambda _session: True, raising=False)
        monkeypatch.setattr(manager, "_audience_preprocessing_loop", fake_audience_preprocessing_loop, raising=False)
        monkeypatch.setattr(manager, "_broadcast", failing_broadcast)

        with pytest.raises(RuntimeError, match="broadcast failed after startup tasks"):
            await manager.start_session("live-a")
        await asyncio.sleep(0)
        leaked_tasks = _pending_lifecycle_runtime_tasks() - baseline_tasks

        assert worker_started.is_set()
        assert leaked_tasks == set()
        assert "live-a" not in manager._runtimes
        assert storage.get_session("live-a")["status"] == "stopped"
    finally:
        await _cancel_tasks(leaked_tasks or (_pending_lifecycle_runtime_tasks() - baseline_tasks))
        if manager is not None:
            await manager.stop_all()
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_start_session_task_creation_failure_cleans_existing_registered_runtime(monkeypatch):
    tmp_dir = _tmp_dir()
    leaked_tasks: set[asyncio.Task] = set()
    baseline_tasks = _pending_lifecycle_runtime_tasks()
    manager = None
    try:
        trace_path = tmp_dir / "runtime" / "llm_trace.jsonl"
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(bridge_engine, "DEFAULT_LLM_TRACE_PATH", trace_path)
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "enabled": True,
        })
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "display_name": "QA Live",
            "auto_inject": True,
            "status": "starting",
        })
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())
        queue = await manager.subscribe("live-a")
        existing_runtime = manager._runtimes["live-a"]
        monkeypatch.setattr(manager, "_auto_inject_loop", lambda _runtime: None)

        with pytest.raises(TypeError):
            await manager.start_session("live-a")
        await asyncio.sleep(0)
        leaked_tasks = _pending_lifecycle_runtime_tasks() - baseline_tasks

        assert leaked_tasks == set()
        assert manager._runtimes.get("live-a") is existing_runtime
        assert existing_runtime.running is False
        assert existing_runtime.status == "stopped"
        assert storage.get_session("live-a")["status"] == "stopped"

        async def retry_auto_inject_loop(_runtime):
            await asyncio.Event().wait()

        monkeypatch.setattr(manager, "_auto_inject_loop", retry_auto_inject_loop)

        status = await manager.start_session("live-a")
        broadcast = await asyncio.wait_for(queue.get(), timeout=1)

        assert status["running"] is True
        assert manager._runtimes.get("live-a") is existing_runtime
        assert broadcast["type"] == "status"
        assert broadcast["status"] == "running"
    finally:
        await _cancel_tasks(leaked_tasks or (_pending_lifecycle_runtime_tasks() - baseline_tasks))
        if manager is not None:
            await manager.stop_all()
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_stop_runtime_background_tasks_for_closing_cancels_audience_preprocess_task():
    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "enabled": True,
        })
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())
        runtime = LiveRuntime(session_id="live-a")
        cancelled = asyncio.Event()

        async def pending_audience_preprocess():
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        runtime.audience_preprocess_task = asyncio.create_task(pending_audience_preprocess())
        await asyncio.sleep(0)

        await manager._stop_runtime_background_tasks_for_closing(runtime)

        assert runtime.audience_preprocess_task is None
        assert cancelled.is_set()
    finally:
        await manager.stop_all()
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_start_session_creates_and_stops_enabled_audience_preprocessing_worker(monkeypatch):
    tmp_dir = _tmp_dir()
    try:
        trace_path = tmp_dir / "runtime" / "llm_trace.jsonl"
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(bridge_engine, "DEFAULT_LLM_TRACE_PATH", trace_path)
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "enabled": True,
        })
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "display_name": "QA Live",
            "auto_inject": True,
        })
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())
        worker_started = asyncio.Event()
        worker_cancelled = asyncio.Event()

        async def fake_audience_preprocessing_loop(_runtime):
            worker_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                worker_cancelled.set()
                raise

        monkeypatch.setattr(manager, "_audience_preprocessing_enabled", lambda _session: True, raising=False)
        monkeypatch.setattr(manager, "_audience_preprocessing_loop", fake_audience_preprocessing_loop, raising=False)

        await manager.start_session("live-a")
        runtime = manager._runtimes["live-a"]
        await asyncio.wait_for(worker_started.wait(), timeout=1)

        assert runtime.audience_preprocess_task is not None

        await manager.stop_session("live-a")

        assert runtime.audience_preprocess_task is None
        assert worker_cancelled.is_set()
    finally:
        await manager.stop_all()
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_start_session_auto_enables_single_connector_from_legacy_disabled_state(monkeypatch):
    tmp_dir = _tmp_dir()
    try:
        trace_path = tmp_dir / "runtime" / "llm_trace.jsonl"
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(bridge_engine, "DEFAULT_LLM_TRACE_PATH", trace_path)
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "youtube-main",
            "display_name": "YouTube Main",
            "enabled": False,
        })
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "youtube-main",
            "display_name": "QA Live",
        })
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())

        status = await manager.start_session("live-a")

        connector = storage.get_connector("youtube-main")
        assert status["running"] is True
        assert connector["enabled"] is True
        await manager.stop_session("live-a")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

@pytest.mark.asyncio
async def test_autostart_skips_finalized_session():
    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "api_key": "key",
            "enabled": True,
        })
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "video_id": "video-a",
            "live_chat_id": "chat-a",
            "auto_connect": True,
        })
        storage.update_session_summary_state(
            "live-a",
            summary_status="completed",
            summary_id=1,
            finalized_at="2026-05-03T10:00:00",
        )
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())

        await manager.sync_autostart()

        assert manager.get_status("live-a")["running"] is False
        assert storage.get_session("live-a")["summary_status"] == "completed"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

@pytest.mark.asyncio
async def test_autostart_marks_unavailable_live_session_stopped_without_crashing():
    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "api_key": "key",
            "enabled": True,
        })
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "video_id": "ended-video",
            "auto_connect": True,
            "status": "running",
        })
        manager = YouTubeBridgeManager(storage, youtube_client=ResolveLiveChatFailedClient())

        await manager.sync_autostart()

        assert manager.get_status("live-a")["running"] is False
        assert storage.get_session("live-a")["status"] == "stopped"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

@pytest.mark.asyncio
async def test_real_youtube_session_blocks_manual_and_auto_test_events():
    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "enabled": True,
        })
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "display_name": "Real YouTube Live",
            "video_id": "real-video",
            "auto_test_events_enabled": True,
            "test_event_use_llm": True,
        })
        manager = YouTubeBridgeManager(storage)

        with pytest.raises(ValueError, match="真實 YouTube 直播不允許插入測試留言"):
            await manager.generate_test_events("live-a", count=1, use_llm=True)

        with pytest.raises(ValueError, match="真實 YouTube 直播不允許插入測試留言"):
            await manager.start_auto_test_events("live-a")

        assert storage.get_session("live-a")["auto_test_events_enabled"] is False
        assert storage.list_events("live-a") == []
        assert manager.get_status("live-a")["auto_test_events_running"] is False
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

@pytest.mark.asyncio
async def test_autostart_finalizes_stale_running_interactions_before_resume():
    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "enabled": True,
        })
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "display_name": "QA Live",
            "auto_connect": True,
            "status": "running",
            "auto_inject": False,
            "auto_test_events_enabled": False,
        })
        stale = storage.create_interaction({
            "session_id": "live-a",
            "source": "super_chat",
            "priority": 260,
            "status": "running",
            "event_ids": [1, 2, 3],
            "memoria_session_id": "mem-a",
            "content": "舊 process 未完成的回應。",
        })
        manager = YouTubeBridgeManager(storage, memoria_client_factory=FakeClosingMemoriaClient)

        await manager.sync_autostart()

        interaction = storage.get_interaction(stale["job_id"])
        assert interaction["status"] == "interrupted"
        assert interaction["reason"] == "server_restarted"
        assert interaction["metadata"]["finalized_by"] == "sync_autostart"
        assert storage.get_active_interaction("live-a") is None
        assert manager.get_status("live-a")["running"] is True
    finally:
        await manager.stop_all()
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_autostart_finalizes_stale_prefetch_interactions_before_resume():
    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "enabled": True,
        })
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "display_name": "QA Live",
            "auto_connect": True,
            "status": "running",
            "auto_inject": False,
            "auto_test_events_enabled": False,
        })
        stale = storage.create_interaction({
            "session_id": "live-a",
            "source": "director_prefetch",
            "priority": 40,
            "status": "prefetching",
            "memoria_session_id": "mem-a",
            "content": "前一個 server process 還沒完成的預載。",
        })
        manager = YouTubeBridgeManager(storage, memoria_client_factory=FakeClosingMemoriaClient)

        await manager.sync_autostart()

        interaction = storage.get_interaction(stale["job_id"])
        assert interaction["status"] == "interrupted"
        assert interaction["reason"] == "server_restarted"
        assert interaction["metadata"]["finalized_by"] == "sync_autostart"
        assert storage.get_active_interaction("live-a") is None
        assert manager.get_status("live-a")["running"] is True
    finally:
        await manager.stop_all()
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_autostart_finalizes_closing_session_left_by_restart():
    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "enabled": True,
        })
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "display_name": "QA Live",
            "auto_connect": True,
            "status": "closing",
            "auto_inject": True,
            "auto_test_events_enabled": True,
        })
        storage.update_director_state(
            "live-a",
            director_enabled=True,
            status="duration_closing_waiting_active",
        )
        stale = storage.create_interaction({
            "session_id": "live-a",
            "source": "director_prefetch",
            "priority": 40,
            "status": "prefetching",
        })
        manager = YouTubeBridgeManager(storage, memoria_client_factory=FakeClosingMemoriaClient)

        await manager.sync_autostart()

        session = storage.get_session("live-a")
        director_state = storage.get_director_state("live-a")
        interaction = storage.get_interaction(stale["job_id"])
        assert manager.get_status("live-a")["running"] is False
        assert session["status"] == "ended"
        assert session["auto_inject"] is False
        assert session["auto_test_events_enabled"] is False
        assert session["finalized_at"]
        assert director_state["director_enabled"] is False
        assert director_state["status"] == "ended"
        assert director_state["metadata"]["server_restarted_during_closing"] is True
        assert interaction["status"] == "interrupted"
        assert interaction["reason"] == "server_restarted_during_closing"
        assert interaction["metadata"]["finalized_by"] == "sync_autostart"
        assert storage.get_active_interaction("live-a") is None
    finally:
        await manager.stop_all()
        shutil.rmtree(tmp_dir, ignore_errors=True)
