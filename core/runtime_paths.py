"""Runtime 資料路徑集中管理。

所有執行期會寫入或累積的檔案都應放在專案根目錄的 ``runtime/`` 底下。
原始碼、靜態資源、內建預設檔與模型資產仍保留在專案根目錄。
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


PROJECT_ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
RUNTIME_DIR = Path(os.getenv("MEMORIACORE_RUNTIME_DIR", PROJECT_ROOT / "runtime")).resolve()


def ensure_runtime_dir() -> Path:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    return RUNTIME_DIR


def runtime_path(*parts: str | os.PathLike[str]) -> Path:
    """回傳 runtime 底下的路徑，並確保父層目錄存在。"""
    path = ensure_runtime_dir().joinpath(*map(Path, parts))
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def runtime_file(name: str) -> str:
    return str(runtime_path(name))


def generated_images_dir() -> Path:
    path = runtime_path("generated_images")
    path.mkdir(parents=True, exist_ok=True)
    return path


def persona_probe_result_dir() -> Path:
    path = runtime_path("PersonaProbe", "result")
    path.mkdir(parents=True, exist_ok=True)
    return path


def legacy_root_path(name: str) -> Path:
    return PROJECT_ROOT / name


_ROOT_RUNTIME_FILES = (
    ".memoriacore_jwt_secret",
    "ai_personality.md",
    "bot_configs.json",
    "characters.json",
    "chat_history.json",
    "conversation.db",
    "llm_trace.jsonl",
    "persona_snapshots.db",
    "persona_sync_state.json",
    "prompts.json",
    "user_prefs.json",
    "users.db",
    "weather_cache.json",
)

_ROOT_RUNTIME_DIRS = (
    "generated_images",
)


def _legacy_memory_db_paths() -> list[Path]:
    return sorted(PROJECT_ROOT.glob("memory_db_*.db*"))


def _legacy_sqlite_sidecar_paths() -> list[Path]:
    db_names = [
        "conversation.db",
        "persona_snapshots.db",
        "users.db",
    ]
    sidecars: list[Path] = []
    for db_name in db_names:
        for suffix in ("-wal", "-shm", "-journal"):
            path = legacy_root_path(f"{db_name}{suffix}")
            if path.exists():
                sidecars.append(path)
    return sidecars


def _move_if_needed(src: Path, dst: Path) -> str | None:
    if not src.exists():
        return None
    if src.resolve() == dst.resolve():
        return None
    if src.is_dir() and dst.is_dir():
        moved_count = 0
        skipped_count = 0
        for child in src.iterdir():
            child_dst = dst / child.name
            if child_dst.exists():
                skipped_count += 1
                continue
            shutil.move(str(child), str(child_dst))
            moved_count += 1
        try:
            src.rmdir()
        except OSError:
            pass
        if moved_count or skipped_count:
            return f"merged:{src.name}:moved={moved_count}:skipped={skipped_count}"
        return None
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        return f"skip_exists:{src.name}"
    shutil.move(str(src), str(dst))
    return f"moved:{src.name}"


def migrate_legacy_runtime_data() -> list[str]:
    """將舊版根目錄 runtime 資料搬到 ``runtime/``。

    若目的地已存在同名檔案，為避免覆寫使用者資料，會保留來源並回報
    ``skip_exists:<name>``。
    """
    ensure_runtime_dir()
    results: list[str] = []

    for name in _ROOT_RUNTIME_FILES:
        moved = _move_if_needed(legacy_root_path(name), runtime_path(name))
        if moved:
            results.append(moved)

    for src in _legacy_memory_db_paths():
        moved = _move_if_needed(src, runtime_path(src.name))
        if moved:
            results.append(moved)

    for src in _legacy_sqlite_sidecar_paths():
        moved = _move_if_needed(src, runtime_path(src.name))
        if moved:
            results.append(moved)

    for name in _ROOT_RUNTIME_DIRS:
        moved = _move_if_needed(legacy_root_path(name), runtime_path(name))
        if moved:
            results.append(moved)

    probe_result = PROJECT_ROOT / "PersonaProbe" / "result"
    moved = _move_if_needed(probe_result, runtime_path("PersonaProbe", "result"))
    if moved:
        results.append(f"PersonaProbe/{moved}")

    return results
