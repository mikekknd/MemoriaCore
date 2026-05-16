"""YouTubeBridge live event safety classification mixin。"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from bridge_contracts import SAFETY_CLASSIFIER_BATCH_LIMIT, SAFETY_CLASSIFIER_SCHEMA
from bridge_runtime import LiveRuntime
from engine_public_events import single_line


logger = logging.getLogger("youtube_bridge")


class EventSafetyManagerMixin:
    async def classify_pending_events_serialized(self, session_id: str, *, limit: int = 50) -> dict[str, Any]:
        runtime = self._runtimes.setdefault(session_id, LiveRuntime(session_id=session_id))
        async with runtime.safety_lock:
            return await self.classify_pending_events(session_id, limit=limit)

    async def classify_event_ids_serialized(self, session_id: str, event_ids: list[int]) -> dict[str, Any]:
        runtime = self._runtimes.setdefault(session_id, LiveRuntime(session_id=session_id))
        async with runtime.safety_lock:
            ids = []
            for raw_id in event_ids:
                try:
                    event_id = int(raw_id)
                except (TypeError, ValueError):
                    continue
                if event_id not in ids:
                    ids.append(event_id)
            if not ids:
                return {"session_id": session_id, "classified_count": 0, "failed_count": 0, "events": []}
            events = self.storage.get_events_by_ids(session_id, ids, limit=len(ids))
            pending_events = [
                event
                for event in events
                if str(event.get("status") or "active") == "active"
                and str(event.get("message_text") or "").strip()
                and str(event.get("safety_status") or "pending") in {"pending", "failed_retryable"}
            ]
            return await self._classify_event_batch(session_id, pending_events)

    def _schedule_pending_event_classification(self, runtime: LiveRuntime, *, limit: int = 50) -> None:
        if runtime.safety_task and not runtime.safety_task.done():
            return

        async def _run() -> None:
            try:
                await self.classify_pending_events_serialized(runtime.session_id, limit=limit)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("live event safety classification failed session_id=%s error=%s", runtime.session_id, exc)

        runtime.safety_task = asyncio.create_task(_run())

    async def classify_pending_events(self, session_id: str, *, limit: int = 50) -> dict[str, Any]:
        session = self.storage.get_session(session_id)
        if not session:
            raise ValueError("live session 不存在")
        batch_limit = min(max(1, int(limit or SAFETY_CLASSIFIER_BATCH_LIMIT)), SAFETY_CLASSIFIER_BATCH_LIMIT)
        events = self.storage.list_events_pending_safety(session_id, limit=batch_limit)
        return await self._classify_event_batch(session_id, events)

    async def _classify_event_batch(self, session_id: str, events: list[dict[str, Any]]) -> dict[str, Any]:
        if not events:
            return {"session_id": session_id, "classified_count": 0, "failed_count": 0, "events": []}

        request_events = [
            {
                "event_id": int(event["id"]),
                "author_display_name": event.get("author_display_name", ""),
                "message_text": event.get("message_text", ""),
            }
            for event in events
        ]
        try:
            result = await asyncio.to_thread(
                self._memoria_client().generate_prompt_json,
                prompt_key="youtube_live_safety_classifier_prompt",
                variables={"events_json": json.dumps(request_events, ensure_ascii=False, indent=2)},
                task_key="router",
                temperature=0.0,
                schema=SAFETY_CLASSIFIER_SCHEMA,
            )
        except Exception as exc:
            failed_events: list[dict[str, Any]] = []
            for event in events:
                updated = self.storage.update_event_safety(
                    int(event["id"]),
                    status="failed",
                    label="unclassified",
                    safe_message_text="安全檢查未完成，暫不顯示原始留言。",
                    safety_summary="安全檢查失敗，留言未注入。",
                    reason=str(exc)[:300],
                    confidence=0.0,
                )
                if updated:
                    failed_events.append(self._public_event(updated))
                    await self._broadcast(
                        session_id,
                        {
                            "type": "safety_classified",
                            "event_id": int(updated.get("id") or 0),
                            "displayed": False,
                            "event": None,
                        },
                    )
            return {
                "session_id": session_id,
                "classified_count": 0,
                "failed_count": len(failed_events),
                "events": failed_events,
                "error": str(exc),
            }

        by_id = self._normalize_safety_classifications(result, events)
        updated_events: list[dict[str, Any]] = []
        failed_count = 0
        for event in events:
            classification = by_id.get(int(event["id"]))
            if not classification:
                classification = {
                    "status": "failed",
                    "label": "unclassified",
                    "safe_text": "安全檢查未完成，暫不顯示原始留言。",
                    "safe_summary": "SafetyLLM 未回傳此留言的分類。",
                    "reason": "missing classification",
                    "confidence": 0.0,
                }
            if classification.get("status") == "failed":
                failed_count += 1
            updated = self.storage.update_event_safety(
                int(event["id"]),
                status=str(classification.get("status") or "completed"),
                label=str(classification.get("label") or "unclassified"),
                safe_message_text=str(classification.get("safe_text") or ""),
                safety_summary=str(classification.get("safe_summary") or ""),
                reason=str(classification.get("reason") or ""),
                confidence=float(classification.get("confidence") or 0.0),
            )
            if updated:
                public_event = self._public_event(updated)
                updated_events.append(public_event)
                display_event = self._public_live_event(updated)
                await self._broadcast(
                    session_id,
                    {
                        "type": "safety_classified",
                        "event_id": int(updated.get("id") or 0),
                        "displayed": bool(display_event),
                        "event": display_event,
                    },
                )
                if display_event:
                    await self._broadcast(session_id, {"type": "youtube_live_event", "event": display_event})
        return {
            "session_id": session_id,
            "classified_count": len(updated_events) - failed_count,
            "failed_count": failed_count,
            "events": updated_events,
        }

    @staticmethod
    def _normalize_safety_classifications(result: dict[str, Any], events: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
        raw_items = result.get("classifications") if isinstance(result, dict) else None
        if not isinstance(raw_items, list):
            raw_items = []
        known_ids = {int(event["id"]) for event in events}
        out: dict[int, dict[str, Any]] = {}
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            try:
                event_id = int(item.get("event_id"))
            except (TypeError, ValueError):
                continue
            if event_id not in known_ids:
                continue
            label = str(item.get("label") or "unclassified").strip() or "unclassified"
            safe_text = single_line(item.get("safe_text") or "")
            safe_summary = single_line(item.get("safe_summary") or safe_text)
            reason = single_line(item.get("reason") or "")
            try:
                confidence = max(0.0, min(float(item.get("confidence", 0) or 0), 1.0))
            except (TypeError, ValueError):
                confidence = 0.0
            if not safe_text and label == "clean":
                label = "unclassified" if label == "clean" else label
                safe_text = "安全檢查未完成，暫不顯示原始留言。"
            if label != "clean" and not safe_summary:
                safe_summary = "可疑留言已忽略。"
            out[event_id] = {
                "status": "completed" if label != "unclassified" else "failed",
                "label": label,
                "safe_text": safe_text[:500],
                "safe_summary": safe_summary[:500],
                "reason": reason[:500],
                "confidence": confidence,
            }
        return out
