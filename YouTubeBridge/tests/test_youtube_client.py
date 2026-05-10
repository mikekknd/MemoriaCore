import sys
from pathlib import Path


BRIDGE_ROOT = Path(__file__).resolve().parents[1]
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))

from youtube_client import extract_video_id


def test_extract_video_id_accepts_common_youtube_urls():
    assert extract_video_id("abc123") == "abc123"
    assert extract_video_id("https://www.youtube.com/watch?v=abc123&feature=share") == "abc123"
    assert extract_video_id("https://youtu.be/abc123?t=10") == "abc123"
    assert extract_video_id("https://www.youtube.com/live/abc123?si=xyz") == "abc123"
