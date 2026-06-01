import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from test_live_episode_plan_contract import sample_plan


BRIDGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRIDGE_ROOT))

spec = importlib.util.spec_from_file_location("youtube_bridge_server_for_auth", BRIDGE_ROOT / "server.py")
server_module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(server_module)
require_bridge_key = server_module.require_bridge_key


def _request(host: str, key: str = "", path: str = ""):
    return SimpleNamespace(
        client=SimpleNamespace(host=host),
        headers={"X-Bridge-Key": key} if key else {},
        url=SimpleNamespace(path=path) if path else None,
    )


def _control_ui_source() -> str:
    static_root = Path(server_module.STATIC_ROOT)
    parts = [(static_root / "index.html").read_text(encoding="utf-8")]
    ui_root = static_root / "ui"
    if ui_root.exists():
        for name in (
            "index.css",
            "base.css",
            "live-session.css",
            "topic-pack.css",
            "topic-graph.css",
            "overlays.css",
            "core.js",
            "selectors.js",
            "topic-packs.js",
            "topic-graph.js",
            "topic-pack-crud.js",
            "fact-card-import.js",
            "memoria-control.js",
            "live-persona-control.js",
            "events-control.js",
            "summary-director-control.js",
            "session-control.js",
            "control.js",
            "app.js",
        ):
            path = ui_root / name
            if path.exists():
                parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def _assert_launcher_uses_runtime_log_dir(source: str, legacy_runtime_prefix: str) -> None:
    assert r"runtime\log" in source
    assert ".foreground.log" in source or (".out.log" in source and ".err.log" in source)
    assert legacy_runtime_prefix not in source.lower()

# Split from test_server_auth.py: server, launcher, route, and UI contracts.

def test_memoriacore_launcher_uses_windows_selector_policy_before_uvicorn_import():
    source = (BRIDGE_ROOT.parent / "run_server.py").read_text(encoding="utf-8")

    assert "WindowsSelectorEventLoopPolicy" in source
    assert source.index("WindowsSelectorEventLoopPolicy") < source.index("import uvicorn")


def test_bridge_hot_reload_does_not_watch_factcard_markdown_files():
    source = (BRIDGE_ROOT / "run_server_hot_reload.py").read_text(encoding="utf-8")

    assert '"*.md"' not in source
    assert '"*.py", "*.html", "*.js", "*.css", "*.json"' in source


def test_bridge_hot_reload_launcher_uses_full_process_tree_cleanup():
    start_script = (BRIDGE_ROOT / "start_hot_reload.bat").read_text(encoding="utf-8")
    stop_script_path = BRIDGE_ROOT / "stop_8091.bat"

    assert stop_script_path.exists()
    assert 'call "%~dp0stop_8091.bat"' in start_script
    assert start_script.index('call "%~dp0stop_8091.bat"') < start_script.index('run_server_hot_reload.py')
    assert "Get-NetTCPConnection -LocalPort %API_PORT% -State Listen" not in start_script
    assert "Stop-Process -Id $_ -Force" not in start_script


def test_bridge_launchers_write_process_logs_under_runtime_log():
    start_script = (BRIDGE_ROOT / "start.bat").read_text(encoding="utf-8")
    hot_reload_script = (BRIDGE_ROOT / "start_hot_reload.bat").read_text(encoding="utf-8")

    for source in (start_script, hot_reload_script):
        _assert_launcher_uses_runtime_log_dir(source, r"runtime\youtube_bridge")


def test_bridge_launchers_point_operators_to_studio_not_legacy_live_page():
    start_script = (BRIDGE_ROOT / "start.bat").read_text(encoding="utf-8")
    hot_reload_script = (BRIDGE_ROOT / "start_hot_reload.bat").read_text(encoding="utf-8")

    assert "Studio UI" in start_script
    assert "http://localhost:%API_PORT%/studio/" in start_script
    assert "http://127.0.0.1:%API_PORT%/studio/" in hot_reload_script
    assert "Open the Studio UI after the server reports that it is running." in start_script
    deleted_adapter_terms = ("/live/", "/live-chat/", "live_chat.html", "live-chat.js", "live-chat.css")
    for source in (start_script, hot_reload_script):
        for term in deleted_adapter_terms:
            assert term not in source


def test_memoriacore_launchers_write_process_logs_under_runtime_log():
    root = BRIDGE_ROOT.parent
    scripts = [
        root / "start.bat",
        root / "start_full.bat",
        root / "startServerHotReload.bat",
    ]

    for script in scripts:
        source = script.read_text(encoding="utf-8")
        _assert_launcher_uses_runtime_log_dir(source, r"runtime\api_8088")


def test_foreground_launchers_force_utf8_without_powershell_native_stderr_wrapper():
    root = BRIDGE_ROOT.parent
    scripts = [
        root / "start.bat",
        BRIDGE_ROOT / "start.bat",
    ]

    for script in scripts:
        source = script.read_text(encoding="utf-8")
        assert "PYTHONUTF8" in source
        assert "PYTHONIOENCODING" in source
        assert "[Console]::OutputEncoding" in source
        assert "cmd /d /s /c $cmdLine | Tee-Object" in source
        assert "& '%PYTHON%'" not in source
        assert "2>&1 | Tee-Object" not in source


def test_bridge_launcher_is_api_only_without_streamlit():
    start_script = (BRIDGE_ROOT / "start.bat").read_text(encoding="utf-8")
    requirements = (BRIDGE_ROOT / "requirements.txt").read_text(encoding="utf-8").lower()

    assert not (BRIDGE_ROOT / "app.py").exists()
    assert "streamlit" not in start_script.lower()
    assert "streamlit" not in requirements
    assert "8503" not in start_script
    assert "server.py" in start_script


def test_stop_8091_script_kills_listener_wrappers_and_worker_tree():
    batch_source = (BRIDGE_ROOT / "stop_8091.bat").read_text(encoding="utf-8")
    source = (BRIDGE_ROOT / "stop_8091.ps1").read_text(encoding="utf-8")

    assert 'set "BRIDGE_ROOT=%~dp0."' in batch_source
    assert "stop_8091.ps1" in batch_source
    assert "Get-NetTCPConnection -LocalPort $Port -State Listen" in source
    assert "Win32_Process" in source
    assert "start_hot_reload.bat" in source
    assert "run_server_hot_reload.py" in source
    assert "ParentProcessId" in source
    assert "taskkill.exe" in source
    assert "/T" in source
    assert "/F" in source
    assert "[KILL]" in source
    assert "[REMAINING]" in source
