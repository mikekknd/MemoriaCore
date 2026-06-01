"""YouTube Data API client for live chat polling。"""
from __future__ import annotations

from datetime import datetime
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests


GOOGLE_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
YOUTUBE_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
YOUTUBE_LIVE_BROADCASTS_URL = "https://www.googleapis.com/youtube/v3/liveBroadcasts"
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
        self._oauth_access_token = ""
        self._oauth_access_token_expires_at = 0.0

    def _get(self, url: str, params: dict[str, Any], headers: dict[str, str] | None = None) -> dict[str, Any]:
        response = requests.get(url, params=params, headers=headers or None, timeout=self.timeout)
        if response.status_code >= 400:
            try:
                detail = response.json()
            except Exception:
                detail = response.text[:500]
            raise RuntimeError(f"YouTube API HTTP {response.status_code}: {detail}")
        return response.json()

    def _post(self, url: str, data: dict[str, Any]) -> dict[str, Any]:
        response = requests.post(url, data=data, timeout=self.timeout)
        if response.status_code >= 400:
            try:
                detail = response.json()
            except Exception:
                detail = response.text[:500]
            raise RuntimeError(f"YouTube OAuth HTTP {response.status_code}: {detail}")
        return response.json()

    def oauth_access_token(self, credentials: dict[str, Any]) -> str:
        now = time.time()
        if self._oauth_access_token and now + 60 < self._oauth_access_token_expires_at:
            return self._oauth_access_token
        data = self._post(
            str(credentials.get("token_uri") or GOOGLE_OAUTH_TOKEN_URL),
            {
                "client_id": str(credentials.get("client_id") or ""),
                "client_secret": str(credentials.get("client_secret") or ""),
                "refresh_token": str(credentials.get("refresh_token") or ""),
                "grant_type": "refresh_token",
            },
        )
        access_token = str(data.get("access_token") or "").strip()
        if not access_token:
            raise RuntimeError("OAuth refresh response 缺少 access_token")
        try:
            expires_in = int(data.get("expires_in") or 3600)
        except (TypeError, ValueError):
            expires_in = 3600
        self._oauth_access_token = access_token
        self._oauth_access_token_expires_at = now + max(60, expires_in)
        return access_token

    def resolve_live_chat_id(self, *, video_id: str, api_key: str = "", access_token: str = "") -> str:
        params: dict[str, Any] = {
            "part": "liveStreamingDetails",
            "id": video_id,
        }
        headers = None
        if access_token:
            headers = {"Authorization": f"Bearer {access_token}"}
        else:
            params["key"] = api_key
        data = self._get(
            YOUTUBE_VIDEOS_URL,
            params,
            headers=headers,
        )
        items = data.get("items") or []
        if not items:
            raise RuntimeError("找不到指定 YouTube video_id")
        details = items[0].get("liveStreamingDetails") or {}
        live_chat_id = details.get("activeLiveChatId") or ""
        if not live_chat_id:
            raise RuntimeError("指定影片目前沒有 activeLiveChatId，可能尚未開播或已結束")
        return live_chat_id

    def resolve_active_live_broadcast(self, *, access_token: str) -> dict[str, Any]:
        data = self._get(
            YOUTUBE_LIVE_BROADCASTS_URL,
            {
                "part": "id,snippet,status",
                "broadcastStatus": "active",
                "broadcastType": "all",
                "maxResults": 5,
            },
            headers={"Authorization": f"Bearer {access_token}"},
        )
        for item in data.get("items") or []:
            snippet = item.get("snippet") or {}
            video_id = str(item.get("id") or "").strip()
            live_chat_id = str(snippet.get("liveChatId") or "").strip()
            if video_id and live_chat_id:
                return {
                    "video_id": video_id,
                    "live_chat_id": live_chat_id,
                    "title": str(snippet.get("title") or ""),
                    "channel_id": str(snippet.get("channelId") or ""),
                }
        raise RuntimeError("OAuth 找不到目前 active live 或 liveChatId")

    def resolve_active_live_by_channel(self, *, api_key: str, channel_id: str) -> dict[str, Any]:
        data = self._get(
            YOUTUBE_SEARCH_URL,
            {
                "part": "snippet",
                "channelId": channel_id,
                "eventType": "live",
                "type": "video",
                "order": "date",
                "maxResults": 5,
                "key": api_key,
            },
        )
        last_error = ""
        for item in data.get("items") or []:
            item_id = item.get("id") or {}
            video_id = str(item_id.get("videoId") or "").strip()
            if not video_id:
                continue
            try:
                live_chat_id = self.resolve_live_chat_id(api_key=api_key, video_id=video_id)
            except Exception as exc:
                last_error = str(exc)
                continue
            snippet = item.get("snippet") or {}
            return {
                "video_id": video_id,
                "live_chat_id": live_chat_id,
                "title": str(snippet.get("title") or ""),
                "channel_id": channel_id,
            }
        if last_error:
            raise RuntimeError(f"API key fallback 找不到可用直播聊天室：{last_error}")
        raise RuntimeError("API key fallback 找不到目前 active live")

    def resolve_current_live_source(
        self,
        *,
        oauth_credentials: dict[str, Any] | None = None,
        api_key: str = "",
        fallback_channel_id: str = "",
    ) -> dict[str, Any]:
        oauth_error = ""
        credentials = oauth_credentials or {}
        client_secret_configured = bool(credentials.get("client_id") and credentials.get("client_secret"))
        refresh_token_configured = bool(credentials.get("refresh_token"))
        oauth_configured = bool(credentials.get("configured") or (client_secret_configured and refresh_token_configured))
        if oauth_configured:
            try:
                access_token = self.oauth_access_token(credentials)
                result = self.resolve_active_live_broadcast(access_token=access_token)
                return {
                    **result,
                    "auth_method": "oauth",
                    "fallback_used": False,
                    "fallback_reason": "",
                }
            except Exception as exc:
                oauth_error = str(exc)
        elif (credentials.get("client_secret_configured") or client_secret_configured) and not (
            credentials.get("refresh_token_configured") or refresh_token_configured
        ):
            oauth_error = "OAuth client_secret 已設定但缺少 refresh_token"

        if api_key and fallback_channel_id:
            result = self.resolve_active_live_by_channel(api_key=api_key, channel_id=fallback_channel_id)
            return {
                **result,
                "auth_method": "api_key",
                "fallback_used": bool(oauth_error),
                "fallback_reason": oauth_error,
            }

        if oauth_error:
            raise RuntimeError(f"OAuth 偵測失敗：{oauth_error}")
        raise RuntimeError("沒有可用的 OAuth token 或 API key fallback channel_id")

    def fetch_live_chat_messages(
        self,
        *,
        api_key: str = "",
        access_token: str = "",
        live_chat_id: str,
        page_token: str | None = None,
        max_results: int = 200,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "liveChatId": live_chat_id,
            "part": "id,snippet,authorDetails",
            "maxResults": max(1, min(int(max_results or 200), 2000)),
        }
        headers = None
        if access_token:
            headers = {"Authorization": f"Bearer {access_token}"}
        else:
            params["key"] = api_key
        if page_token:
            params["pageToken"] = page_token
        return self._get(YOUTUBE_LIVE_MESSAGES_URL, params, headers=headers)


def normalize_message(item: dict[str, Any], *, session: dict, connector: dict) -> dict[str, Any]:
    snippet = item.get("snippet") or {}
    author = item.get("authorDetails") or {}
    super_chat = snippet.get("superChatDetails") or {}
    try:
        amount_micros = int(super_chat.get("amountMicros") or 0)
    except (TypeError, ValueError):
        amount_micros = 0
    try:
        sc_tier = int(super_chat.get("tier") or 0)
    except (TypeError, ValueError):
        sc_tier = 0
    amount_display = str(super_chat.get("amountDisplayString") or "")
    priority_class = "super_chat" if str(snippet.get("type") or "") == "superChatEvent" or amount_display else "normal"
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
        "amount_display_string": amount_display,
        "currency": str(super_chat.get("currency") or ""),
        "amount_micros": amount_micros,
        "sc_tier": sc_tier,
        "priority_class": priority_class,
        "metadata": {
            "is_chat_owner": bool(author.get("isChatOwner")),
            "is_chat_sponsor": bool(author.get("isChatSponsor")),
            "is_chat_moderator": bool(author.get("isChatModerator")),
        },
    }
