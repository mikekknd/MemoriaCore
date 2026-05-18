import asyncio
import contextlib
import json
import logging
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
    FakeTTSProvider,
    LiveEndedClient,
    LiveRuntime,
    OffTopicEmbeddingMemoriaClient,
    OneMessagePollingClient,
    ResolveLiveChatFailedClient,
    YouTubeBridgeManager,
    _mark_event_clean,
    _next_queue_event,
    _tmp_dir,
    _wait_until,
    bridge_engine,
    normalize_message,
    temp_storage,
)
from live_episode_plan_contract import initial_planned_state
from test_live_episode_plan_contract import sample_plan
from tts_gpt_sovits import TTSResult


def _episode_plan_characters() -> list[dict]:
    return [
        {"character_id": "host-a", "name": "主持A"},
        {"character_id": "analyst-b", "name": "分析B"},
        {"character_id": "skeptic-c", "name": "質疑C"},
    ]

# Tests from this file were split into focused modules for director runtime behavior.
# Keep shared imports/helpers here only if a future compatibility shim needs them.
# Current split targets:
# - test_director_audience_preprocessing.py
# - test_director_loop_idle.py
# - test_director_prefetch_chain.py
# - test_director_turn_context.py
