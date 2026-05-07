import asyncio
import contextlib
import json
import shutil
import subprocess
import sys
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


def test_bridge_engine_loaded_from_subproject_can_import_root_tools():
    script = """
import os
import sys
from pathlib import Path
workspace = Path.cwd().resolve()
os.chdir(workspace / "YouTubeBridge")
filtered = []
for path in sys.path:
    if not path:
        filtered.append(path)
        continue
    resolved = Path(path).resolve()
    if "site-packages" in resolved.parts:
        filtered.append(path)
        continue
    try:
        is_repo_path = resolved == workspace or resolved.is_relative_to(workspace)
    except ValueError:
        is_repo_path = False
    if not is_repo_path:
        filtered.append(path)
sys.path = [os.getcwd()] + filtered
import bridge_engine
from tools.tavily import search_web
print(search_web.__name__)
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=BRIDGE_ROOT.parent,
        text=True,
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert "search_web" in result.stdout

def test_embed_text_uses_short_timeout_when_requested():
    tmp_dir = _tmp_dir()
    captured: list[float | None] = []

    class TimeoutAwareClient:
        def __init__(self, timeout: float | None = None):
            captured.append(timeout)

        def embed_text(self, text: str, model: str = ""):
            return {"dense": [1.0, 0.0], "model": "timeout-aware"}

    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        manager = YouTubeBridgeManager(storage, memoria_client_factory=TimeoutAwareClient)

        result = manager._embed_text("動畫新番 search", timeout_seconds=20)

        assert result["dense"] == [1.0, 0.0]
        assert captured == [20.0]
    finally:
        import shutil

        shutil.rmtree(tmp_dir, ignore_errors=True)
