"""YouTubeBridge FastAPI app state。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass
class BridgeAppState:
    storage: Any
    manager: Any
    summary_manager: Any
    chat_preview_cache: dict[str, dict[str, Any]] = field(default_factory=dict)
    static_root: Path = Path()
    ui_assets_root: Path = Path()
    studio_avatar_root: Path = Path()
    free_talk_topic_root: Path = Path()
    episode_plan_root: Path = Path()
    e2e_checkpoint_path: Path = Path()
    apply_memoria_config: Callable[[], None] | None = None
