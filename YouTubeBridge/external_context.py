"""External context builder Module for YouTubeBridge live injection."""
from __future__ import annotations

from typing import Any, Callable


class ExternalContextBuilder:
    def __init__(
        self,
        *,
        storage,
        event_line: Callable[[dict[str, Any]], str],
        visible_event: Callable[[dict[str, Any]], dict[str, Any]],
        is_public_live_event_displayable: Callable[[dict[str, Any]], bool],
        query_context_for_events: Callable[[dict[str, Any], list[dict[str, Any]], list[str]], tuple[str, dict[str, Any]]],
        presentation_enabled: Callable[[dict[str, Any]], bool],
        attach_live_persona_overrides: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
    ) -> None:
        self.storage = storage
        self._event_line = event_line
        self._visible_event = visible_event
        self._is_public_live_event_displayable = is_public_live_event_displayable
        self._query_context_for_events = query_context_for_events
        self._presentation_enabled = presentation_enabled
        self._attach_live_persona_overrides = attach_live_persona_overrides

    def build(
        self,
        session_id: str,
        *,
        event_ids: list[int] | None = None,
        max_events: int | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        session = self.storage.get_session(session_id)
        if not session:
            raise ValueError("live session 不存在")
        limit = max(1, min(int(max_events or session.get("max_context_messages", 50)), 100))
        if event_ids:
            events = self.storage.get_events_by_ids(session_id, event_ids, limit=limit)
            events = [event for event in events if not event.get("injected_at")]
        else:
            events = self.storage.list_events(session_id, limit=limit, uninjected_only=True)
        active_events = [
            event
            for event in events
            if event.get("status") == "active"
            and event.get("message_text")
            and event.get("safety_status") == "completed"
            and self._is_public_live_event_displayable(event)
        ]
        hidden_event_ids = [
            int(event["id"])
            for event in events
            if event.get("status") == "active"
            and event.get("message_text")
            and event.get("safety_status") in {"completed", "failed"}
            and not self._is_public_live_event_displayable(event)
        ]
        if hidden_event_ids:
            self.storage.mark_events_injected(session_id, hidden_event_ids)

        lines: list[str] = []
        used_ids: list[int] = []
        visible_events: list[dict[str, Any]] = []
        max_chars = int(session.get("max_context_chars", 8000) or 8000)
        presentation_mode = self._presentation_enabled(session)
        if presentation_mode:
            max_chars = min(max_chars, 1200)
        used_chars = 0
        for event in active_events:
            line = self._event_line(event)
            next_len = len(line) + 1
            if lines and used_chars + next_len > max_chars:
                break
            lines.append(line)
            used_ids.append(int(event["id"]))
            if self._is_public_live_event_displayable(event):
                visible_events.append(self._visible_event(event))
            used_chars += next_len
        if not lines:
            raise ValueError("沒有可注入的直播留言")

        summary = {
            "source": "youtube_live",
            "source_session_id": session_id,
            "connector_id": session["connector_id"],
            "video_id": session.get("video_id", ""),
            "live_chat_id": session.get("live_chat_id", ""),
            "event_ids": used_ids,
            "event_count": len(used_ids),
            "hidden_unsafe_count": len(hidden_event_ids),
            "dropped_count": max(0, len(active_events) - len(used_ids)),
        }
        if presentation_mode:
            summary["presentation_enabled"] = True
            summary["group_turn_limit"] = 1
        topic_context, query_resolution = self._query_context_for_events(session, active_events, lines)
        summary["query_resolution"] = query_resolution
        context_parts = ["\n".join(lines), topic_context]
        if presentation_mode:
            context_parts.append(
                "直播輸出模式：請只產生一個短 spoken beat；避免多角色連續接話，讓前端播放完成後再進入下一輪。"
            )
        payload = {
            "source": "youtube_live",
            "source_session_id": session_id,
            "connector_id": session["connector_id"],
            "video_id": session.get("video_id", ""),
            "live_chat_id": session.get("live_chat_id", ""),
            "context_text": "\n".join([part for part in context_parts if part]),
            "event_ids": used_ids,
            "visible_events": visible_events,
            "max_chars": max_chars,
            "summary": summary,
        }
        if presentation_mode:
            payload["group_turn_limit"] = 1
        return self._attach_live_persona_overrides(session, payload), summary
