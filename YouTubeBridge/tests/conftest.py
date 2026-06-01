from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolate_youtube_oauth_runtime(monkeypatch, tmp_path: Path) -> None:
    # 測試預設不讀本機真實 OAuth token，避免開發機設定改變測試分支。
    oauth_dir = tmp_path / "youtube_oauth"
    oauth_dir.mkdir()
    monkeypatch.setenv("YOUTUBE_BRIDGE_OAUTH_DIR", str(oauth_dir))
