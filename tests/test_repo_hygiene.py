from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_deprecated_streamlit_chat_pages_are_removed():
    assert not (PROJECT_ROOT / "ui" / "chat.py").exists()
    assert not (PROJECT_ROOT / "ui" / "history.py").exists()
