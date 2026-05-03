"""YouTube Data API client for live chat polling。"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests


YOUTUBE_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
YOUTUBE_LIVE_MESSAGES_URL = "https://www.googleapis.com/youtube/v3/liveChat/messages"


def extract_video_id(value: str) -> str:
    """接受 YouTube URL 或純 video_id，回傳可交給 YouTube Data API 的 video_id。"""
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        return raw

    parsed = urlparse(raw)
    host = parsed.netloc.lower()
    path_parts = [part for part in parsed.path.strip("/").split("/") if part]

    if host.endswith("youtu.be") and path_parts:
        return path_parts[0]
    if "youtube.com" in host:
        if path_parts and path_parts[0] == "watch":
            return (parse_qs(parsed.query).get("v") or [""])[0].strip()
        if len(path_parts) >= 2 and path_parts[0] in {"live", "shorts", "embed"}:
            return path_parts[1]
    return raw


class YouTubeClient:
    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout

    def _get(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        response = requests.get(url, params=params, timeout=self.timeout)
        if response.status_code >= 400:
            try:
                detail = response.json()
            except Exception:
                detail = response.text[:500]
            raise RuntimeError(f"YouTube API HTTP {response.status_code}: {detail}")
        return response.json()

    def resolve_live_chat_id(self, *, api_key: str, video_id: str) -> str:
        data = self._get(
            YOUTUBE_VIDEOS_URL,
            {
                "part": "liveStreamingDetails",
                "id": video_id,
                "key": api_key,
            },
        )
        items = data.get("items") or []
        if not items:
            raise RuntimeError("找不到指定 YouTube video_id")
        details = items[0].get("liveStreamingDetails") or {}
        live_chat_id = details.get("activeLiveChatId") or ""
        if not live_chat_id:
            raise RuntimeError("指定影片目前沒有 activeLiveChatId，可能尚未開播或已結束")
        return live_chat_id

    def fetch_live_chat_messages(
        self,
        *,
        api_key: str,
        live_chat_id: str,
        page_token: str | None = None,
        max_results: int = 200,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "liveChatId": live_chat_id,
            "part": "id,snippet,authorDetails",
            "maxResults": max(1, min(int(max_results or 200), 2000)),
            "key": api_key,
        }
        if page_token:
            params["pageToken"] = page_token
        return self._get(YOUTUBE_LIVE_MESSAGES_URL, params)


def normalize_message(item: dict[str, Any], *, session: dict, connector: dict) -> dict[str, Any]:
    snippet = item.get("snippet") or {}
    author = item.get("authorDetails") or {}
    super_chat = snippet.get("superChatDetails") or {}
    message_text = (
        snippet.get("displayMessage")
        or snippet.get("textMessageDetails", {}).get("messageText")
        or ""
    )
    return {
        "bridge_session_id": session["session_id"],
        "connector_id": connector["connector_id"],
        "video_id": session.get("video_id", ""),
        "live_chat_id": session.get("live_chat_id", ""),
        "youtube_message_id": str(item.get("id") or ""),
        "message_type": str(snippet.get("type") or "message"),
        "author_channel_id": str(author.get("channelId") or ""),
        "author_display_name": str(author.get("displayName") or ""),
        "author_profile_image_url": str(author.get("profileImageUrl") or ""),
        "message_text": str(message_text or ""),
        "published_at": str(snippet.get("publishedAt") or ""),
        "received_at": datetime.now().isoformat(),
        "status": "active",
        "amount_display_string": str(super_chat.get("amountDisplayString") or ""),
        "currency": str(super_chat.get("currency") or ""),
        "metadata": {
            "is_chat_owner": bool(author.get("isChatOwner")),
            "is_chat_sponsor": bool(author.get("isChatSponsor")),
            "is_chat_moderator": bool(author.get("isChatModerator")),
        },
    }
