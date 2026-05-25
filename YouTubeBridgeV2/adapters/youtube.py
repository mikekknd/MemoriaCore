"""YouTube live chat adapter contracts for YouTubeBridgeV2.

This module only normalizes YouTube live chat events, carries polling cursor
state, reports stream status, and classifies adapter errors. Transport calls,
storage writes, runtime decisions, UI rendering, and final show wording are
handled by other V2 modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping


_UNSET = object()


@dataclass(frozen=True)
class SuperChatMetadata:
    """Public-safe Super Chat metadata extracted from a YouTube event."""

    super_chat_id: str
    amount_micros: int
    currency: str
    amount_display_string: str
    tier: int | None = None
    public_message: str = ""
    acknowledgement_status: str = "pending"

    def __post_init__(self) -> None:
        object.__setattr__(self, "super_chat_id", str(self.super_chat_id))
        object.__setattr__(self, "amount_micros", _coerce_int(self.amount_micros))
        object.__setattr__(self, "currency", str(self.currency))
        object.__setattr__(self, "amount_display_string", str(self.amount_display_string))
        object.__setattr__(self, "tier", _optional_int(self.tier))
        object.__setattr__(self, "public_message", _safe_public_text(self.public_message))
        object.__setattr__(self, "acknowledgement_status", str(self.acknowledgement_status))


@dataclass(frozen=True)
class NormalizedYouTubeEvent:
    """YouTube event shape consumed by runtime services and display surfaces."""

    event_id: str
    event_type: str
    author_channel_id: str
    author_display_name: str
    message_text: str
    published_at: str
    public_payload: dict[str, object] = field(default_factory=dict)
    display_event: dict[str, object] = field(default_factory=dict)
    super_chat: SuperChatMetadata | None = None
    duplicate: bool = False
    should_dispatch: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", str(self.event_id))
        object.__setattr__(self, "event_type", str(self.event_type))
        object.__setattr__(self, "author_channel_id", str(self.author_channel_id))
        object.__setattr__(self, "author_display_name", _safe_public_text(self.author_display_name))
        object.__setattr__(self, "message_text", _safe_public_text(self.message_text))
        object.__setattr__(self, "published_at", str(self.published_at))
        object.__setattr__(self, "public_payload", _redact_public_value(self.public_payload))
        object.__setattr__(self, "display_event", _redact_public_value(self.display_event))
        object.__setattr__(self, "should_dispatch", bool(self.should_dispatch) and not bool(self.duplicate))


@dataclass(frozen=True)
class YouTubePollingCursor:
    """Immutable live chat polling cursor with local duplicate tracking."""

    live_chat_id: str
    next_page_token: str | None = None
    polling_interval_millis: int | None = None
    seen_event_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "live_chat_id", str(self.live_chat_id))
        object.__setattr__(self, "next_page_token", _optional_string(self.next_page_token))
        object.__setattr__(
            self,
            "polling_interval_millis",
            _optional_int(self.polling_interval_millis),
        )
        object.__setattr__(self, "seen_event_ids", _dedupe_strings(self.seen_event_ids))

    def has_seen(self, event_id: object) -> bool:
        """Return whether an event id has already been observed."""

        return str(event_id) in set(self.seen_event_ids)

    def advance(
        self,
        *,
        next_page_token: object = _UNSET,
        polling_interval_millis: object = _UNSET,
        seen_event_ids: Iterable[object] = (),
    ) -> "YouTubePollingCursor":
        """Return an updated cursor without mutating the existing one."""

        updated_next_page_token = (
            self.next_page_token
            if next_page_token is _UNSET
            else _optional_string(next_page_token)
        )
        updated_polling_interval_millis = (
            self.polling_interval_millis
            if polling_interval_millis is _UNSET
            else _optional_int(polling_interval_millis)
        )
        return YouTubePollingCursor(
            live_chat_id=self.live_chat_id,
            next_page_token=updated_next_page_token,
            polling_interval_millis=updated_polling_interval_millis,
            seen_event_ids=(*self.seen_event_ids, *_dedupe_strings(seen_event_ids)),
        )


@dataclass(frozen=True)
class YouTubeStreamStatus:
    """Normalized stream status visible to runtime orchestration."""

    status: str
    is_live: bool
    live_chat_id: str | None = None
    video_id: str | None = None
    ended_at: str | None = None
    public_summary: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", str(self.status))
        object.__setattr__(self, "is_live", bool(self.is_live))
        object.__setattr__(self, "live_chat_id", _optional_string(self.live_chat_id))
        object.__setattr__(self, "video_id", _optional_string(self.video_id))
        object.__setattr__(self, "ended_at", _optional_string(self.ended_at))
        summary = dict(self.public_summary)
        if not summary:
            summary = {
                "status": self.status,
                "is_live": self.is_live,
            }
            if self.video_id:
                summary["video_id"] = self.video_id
            if self.is_live and self.live_chat_id:
                summary["live_chat_id"] = self.live_chat_id
        object.__setattr__(self, "public_summary", _redact_public_value(summary))

    @classmethod
    def from_raw(cls, raw_status: Mapping[str, Any]) -> "YouTubeStreamStatus":
        """Normalize YouTube video/live-stream status payload."""

        live_details = _mapping(raw_status.get("liveStreamingDetails"))
        status_details = _mapping(raw_status.get("status"))
        live_chat_id = _optional_string(live_details.get("activeLiveChatId"))
        video_id = _optional_string(raw_status.get("id"))
        ended_at = _optional_string(live_details.get("actualEndTime"))
        life_cycle = str(status_details.get("lifeCycleStatus", "")).lower()

        if ended_at or life_cycle in {"complete", "completed", "ended"}:
            return cls(
                status="ended",
                is_live=False,
                live_chat_id=None,
                video_id=video_id,
                ended_at=ended_at,
            )
        if live_chat_id:
            return cls(
                status="live",
                is_live=True,
                live_chat_id=live_chat_id,
                video_id=video_id,
            )
        return cls(
            status="not_live",
            is_live=False,
            live_chat_id=None,
            video_id=video_id,
        )


@dataclass(frozen=True)
class YouTubeAdapterError:
    """Classified adapter error with public-safe diagnostics."""

    error_type: str
    retryable: bool
    public_summary: dict[str, object] = field(default_factory=dict)
    status_code: int | None = None
    backoff_hint_seconds: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "error_type", str(self.error_type))
        object.__setattr__(self, "retryable", bool(self.retryable))
        object.__setattr__(self, "status_code", _optional_int(self.status_code))
        object.__setattr__(self, "backoff_hint_seconds", _optional_int(self.backoff_hint_seconds))
        summary = dict(self.public_summary)
        if not summary:
            summary = {
                "error_type": self.error_type,
                "retryable": self.retryable,
            }
            if self.status_code is not None:
                summary["status_code"] = self.status_code
            if self.backoff_hint_seconds is not None:
                summary["backoff_hint_seconds"] = self.backoff_hint_seconds
        object.__setattr__(self, "public_summary", _redact_public_value(summary))


def normalize_youtube_event(
    raw_event: Mapping[str, Any],
    *,
    cursor: YouTubePollingCursor | None = None,
) -> NormalizedYouTubeEvent:
    """Normalize one YouTube live chat event into the V2 public event shape."""

    snippet = _mapping(raw_event.get("snippet"))
    author = _mapping(raw_event.get("authorDetails"))
    event_id = _event_id(raw_event)
    event_type = _event_type(snippet.get("type"))
    author_channel_id = _first_text(
        author.get("channelId"),
        snippet.get("authorChannelId"),
    )
    author_display_name = _first_text(author.get("displayName"), "Unknown")
    message_text = _message_text(snippet)
    published_at = _first_text(snippet.get("publishedAt"), raw_event.get("publishedAt"))
    duplicate = cursor.has_seen(event_id) if cursor is not None else False
    badges = _author_badges(author)
    super_chat = extract_super_chat_metadata(raw_event)

    public_payload: dict[str, object] = {
        "event_id": event_id,
        "event_type": event_type,
        "author_channel_id": author_channel_id,
        "author_display_name": author_display_name,
        "message_text": message_text,
        "published_at": published_at,
        "author_badges": badges,
        "duplicate": duplicate,
        "should_dispatch": not duplicate,
    }
    if super_chat is not None:
        public_payload["super_chat"] = _super_chat_public_payload(super_chat)

    display_event = _display_event(public_payload, super_chat)
    return NormalizedYouTubeEvent(
        event_id=event_id,
        event_type=event_type,
        author_channel_id=author_channel_id,
        author_display_name=author_display_name,
        message_text=message_text,
        published_at=published_at,
        public_payload=public_payload,
        display_event=display_event,
        super_chat=super_chat,
        duplicate=duplicate,
        should_dispatch=not duplicate,
    )


def extract_super_chat_metadata(raw_event: Mapping[str, Any]) -> SuperChatMetadata | None:
    """Extract public-safe Super Chat metadata, or None for non-Super Chat events."""

    snippet = _mapping(raw_event.get("snippet"))
    if _event_type(snippet.get("type")) != "super_chat":
        return None
    details = _mapping(snippet.get("superChatDetails"))
    return SuperChatMetadata(
        super_chat_id=_event_id(raw_event),
        amount_micros=_coerce_int(details.get("amountMicros")),
        currency=_first_text(details.get("currency")),
        amount_display_string=_first_text(details.get("amountDisplayString")),
        tier=_optional_int(details.get("tier")),
        public_message=_first_text(details.get("userComment"), _message_text(snippet)),
        acknowledgement_status="pending",
    )


def classify_youtube_error(error: BaseException) -> YouTubeAdapterError:
    """Classify transport/API errors without performing retries or side effects."""

    status_code = _optional_int(getattr(error, "status_code", None))
    retry_after = _optional_int(getattr(error, "retry_after", None))

    if isinstance(error, TimeoutError):
        return YouTubeAdapterError(
            error_type="timeout",
            retryable=True,
            backoff_hint_seconds=retry_after,
        )
    if status_code in {401, 403}:
        return YouTubeAdapterError(
            error_type="auth_failure",
            retryable=False,
            status_code=status_code,
        )
    if status_code == 429:
        return YouTubeAdapterError(
            error_type="rate_limited",
            retryable=True,
            status_code=status_code,
            backoff_hint_seconds=retry_after,
        )
    if status_code is not None and status_code >= 500:
        return YouTubeAdapterError(
            error_type="transient_api_error",
            retryable=True,
            status_code=status_code,
            backoff_hint_seconds=retry_after,
        )
    if hasattr(error, "retryable"):
        retryable = bool(getattr(error, "retryable"))
        return YouTubeAdapterError(
            error_type="transient_api_error" if retryable else "terminal_api_error",
            retryable=retryable,
            status_code=status_code,
            backoff_hint_seconds=retry_after,
        )
    return YouTubeAdapterError(
        error_type="unknown",
        retryable=False,
        status_code=status_code,
    )


def _display_event(
    public_payload: Mapping[str, object],
    super_chat: SuperChatMetadata | None,
) -> dict[str, object]:
    base = {
        "event_id": public_payload["event_id"],
        "event_type": "super_chat" if super_chat is not None else "audience_message",
        "author_display_name": public_payload["author_display_name"],
        "message_text": public_payload["message_text"],
        "published_at": public_payload["published_at"],
        "author_badges": public_payload["author_badges"],
        "duplicate": public_payload["duplicate"],
        "should_dispatch": public_payload["should_dispatch"],
    }
    if super_chat is not None:
        base["super_chat"] = _super_chat_public_payload(super_chat)
    return base


def _super_chat_public_payload(super_chat: SuperChatMetadata) -> dict[str, object]:
    payload: dict[str, object] = {
        "super_chat_id": super_chat.super_chat_id,
        "amount_micros": super_chat.amount_micros,
        "currency": super_chat.currency,
        "amount_display_string": super_chat.amount_display_string,
        "public_message": super_chat.public_message,
        "acknowledgement_status": super_chat.acknowledgement_status,
    }
    if super_chat.tier is not None:
        payload["tier"] = super_chat.tier
    return payload


def _event_id(raw_event: Mapping[str, Any]) -> str:
    return _first_text(raw_event.get("id"), raw_event.get("event_id"))


def _event_type(raw_type: object) -> str:
    value = str(raw_type or "").strip()
    mapping = {
        "textMessageEvent": "text_message",
        "superChatEvent": "super_chat",
        "newSponsorEvent": "membership",
        "memberMilestoneChatEvent": "membership_milestone",
        "messageDeletedEvent": "message_deleted",
        "userBannedEvent": "user_banned",
    }
    if value in mapping:
        return mapping[value]
    return _camel_to_snake(value.removesuffix("Event")) or "unknown"


def _message_text(snippet: Mapping[str, Any]) -> str:
    text_details = _mapping(snippet.get("textMessageDetails"))
    super_chat_details = _mapping(snippet.get("superChatDetails"))
    return _safe_public_text(
        _first_text(
            text_details.get("messageText"),
            super_chat_details.get("userComment"),
            snippet.get("displayMessage"),
            snippet.get("messageText"),
        )
    )


def _author_badges(author: Mapping[str, Any]) -> list[str]:
    badges: list[str] = []
    if bool(author.get("isChatOwner", False)):
        badges.append("owner")
    if bool(author.get("isChatModerator", False)):
        badges.append("moderator")
    if bool(author.get("isChatSponsor", False)):
        badges.append("sponsor")
    return badges


def _redact_public_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _redact_public_value(inner_value)
            for key, inner_value in value.items()
            if not _is_forbidden_key(key)
        }
    if isinstance(value, list):
        return [_redact_public_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_public_value(item) for item in value)
    if isinstance(value, str):
        return _safe_public_text(value)
    return value


def _safe_public_text(value: object) -> str:
    text = "" if value is None else str(value)
    lowered = text.lower()
    if any(marker in lowered for marker in _FORBIDDEN_TEXT_PATTERNS):
        return "[redacted]"
    return text


def _is_forbidden_key(key: object) -> bool:
    normalized = _normalize_key(key)
    return any(forbidden == normalized or forbidden in normalized for forbidden in _FORBIDDEN_KEYS)


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _first_text(*values: object) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value)
        if text:
            return text
    return ""


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_int(value: object) -> int:
    return _optional_int(value) or 0


def _dedupe_strings(values: Iterable[object]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return tuple(result)


def _camel_to_snake(value: str) -> str:
    if not value:
        return ""
    chars: list[str] = []
    for index, char in enumerate(value):
        if char.isupper() and index > 0:
            chars.append("_")
        chars.append(char.lower())
    return "".join(chars)


def _normalize_key(key: object) -> str:
    text = str(key).strip()
    chars: list[str] = []
    previous_was_lower = False
    for char in text:
        if char.isupper() and previous_was_lower:
            chars.append("_")
        if char.isalnum():
            chars.append(char.lower())
            previous_was_lower = char.islower() or char.isdigit()
        else:
            chars.append("_")
            previous_was_lower = False
    return "_".join(part for part in "".join(chars).split("_") if part)


_FORBIDDEN_KEYS = {
    "access_token",
    "api_key",
    "authorization",
    "headers",
    "hidden_prompt",
    "oauth",
    "password",
    "raw_event",
    "raw_headers",
    "raw_payload",
    "raw_topic_pack",
    "raw_youtube_payload",
    "secret",
    "secret_value",
    "token",
    "topic_pack",
}

_FORBIDDEN_TEXT_PATTERNS = (
    "authorization:",
    "bearer ",
    "basic ",
    "secret-value",
    "x-api-key",
)


__all__ = [
    "NormalizedYouTubeEvent",
    "SuperChatMetadata",
    "YouTubeAdapterError",
    "YouTubePollingCursor",
    "YouTubeStreamStatus",
    "classify_youtube_error",
    "extract_super_chat_metadata",
    "normalize_youtube_event",
]
