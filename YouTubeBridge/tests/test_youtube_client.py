import sys
from pathlib import Path


BRIDGE_ROOT = Path(__file__).resolve().parents[1]
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))

from youtube_client import YouTubeClient, extract_video_id


def test_extract_video_id_accepts_common_youtube_urls():
    assert extract_video_id("abc123") == "abc123"
    assert extract_video_id("https://www.youtube.com/watch?v=abc123&feature=share") == "abc123"
    assert extract_video_id("https://youtu.be/abc123?t=10") == "abc123"
    assert extract_video_id("https://www.youtube.com/live/abc123?si=xyz") == "abc123"


class RecordingYouTubeClient(YouTubeClient):
    def __init__(self):
        super().__init__()
        self.calls = []

    def _post(self, url, data):
        self.calls.append(("post", url, data))
        return {"access_token": "oauth-access", "expires_in": 3600}

    def _get(self, url, params, headers=None):
        self.calls.append(("get", url, params, headers or {}))
        if "liveBroadcasts" in url:
            assert headers == {"Authorization": "Bearer oauth-access"}
            assert "mine" not in params
            return {
                "items": [
                    {"id": "missing-chat", "snippet": {}},
                    {
                        "id": "oauth-video",
                        "snippet": {
                            "liveChatId": "oauth-chat",
                            "title": "目前直播",
                            "channelId": "channel-a",
                        },
                        "status": {"lifeCycleStatus": "live"},
                    },
                ]
            }
        raise AssertionError(f"unexpected GET {url}")


def test_resolve_current_live_source_uses_oauth_active_broadcast():
    client = RecordingYouTubeClient()

    result = client.resolve_current_live_source(
        oauth_credentials={
            "client_id": "client-id",
            "client_secret": "client-secret",
            "refresh_token": "refresh-token",
            "token_uri": "https://oauth2.googleapis.com/token",
        },
        api_key="",
        fallback_channel_id="",
    )

    assert result["video_id"] == "oauth-video"
    assert result["live_chat_id"] == "oauth-chat"
    assert result["title"] == "目前直播"
    assert result["channel_id"] == "channel-a"
    assert result["auth_method"] == "oauth"
    assert result["fallback_used"] is False
    assert client.calls[0][0] == "post"


class FallbackYouTubeClient(YouTubeClient):
    def __init__(self):
        super().__init__()
        self.calls = []

    def _post(self, url, data):
        self.calls.append(("post", url, data))
        raise RuntimeError("refresh token expired")

    def _get(self, url, params, headers=None):
        self.calls.append(("get", url, params, headers or {}))
        if "search" in url:
            assert params["channelId"] == "fallback-channel"
            return {
                "items": [
                    {"id": {"videoId": "fallback-video"}, "snippet": {"title": "Fallback Live"}},
                ]
            }
        if "videos" in url:
            assert params["id"] == "fallback-video"
            assert params["key"] == "api-key"
            return {"items": [{"liveStreamingDetails": {"activeLiveChatId": "fallback-chat"}}]}
        raise AssertionError(f"unexpected GET {url}")


def test_resolve_current_live_source_falls_back_to_api_key_channel():
    client = FallbackYouTubeClient()

    result = client.resolve_current_live_source(
        oauth_credentials={
            "client_id": "client-id",
            "client_secret": "client-secret",
            "refresh_token": "refresh-token",
            "token_uri": "https://oauth2.googleapis.com/token",
        },
        api_key="api-key",
        fallback_channel_id="fallback-channel",
    )

    assert result["video_id"] == "fallback-video"
    assert result["live_chat_id"] == "fallback-chat"
    assert result["auth_method"] == "api_key"
    assert result["fallback_used"] is True
    assert "refresh token expired" in result["fallback_reason"]
