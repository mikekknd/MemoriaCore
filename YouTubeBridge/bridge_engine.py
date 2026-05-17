"""YouTubeBridge polling manager。"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import random
import re
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import engine_public_events
from engine_closing import ClosingManagerMixin
from engine_director import DirectorManagerMixin
from engine_director_runtime import DirectorRuntimeManagerMixin
from engine_episode_plans import EpisodePlanManagerMixin
from engine_event_safety import EventSafetyManagerMixin
from engine_phase_pipeline import PhasePipelineManagerMixin
from engine_injection import InjectionManagerMixin
from engine_runtime_lifecycle import RuntimeLifecycleManagerMixin
from engine_test_runtime import TestRuntimeManagerMixin
from engine_topic_packs import TopicPackManagerMixin
from bridge_contracts import (
    AUDIENCE_QUERY_CLASSIFIER_SCHEMA,
    AUDIENCE_QUERY_FACT_CARD_MIN_GAP,
    AUDIENCE_QUERY_FACT_CARD_MIN_SCORE,
    AUDIENCE_QUERY_FACT_CARD_STRONG_SCORE,
    CONTROLLED_CONTEXT_CONTENT,
    DEFAULT_INJECT_CONTENT,
    DIRECTOR_SCHEMA,
    FACT_CARDS_PACK_DESCRIPTION,
    FACT_CARDS_PACK_TITLE,
    SAFETY_CLASSIFIER_BATCH_LIMIT,
    SAFETY_CLASSIFIER_SCHEMA,
    TEST_COMMENT_SCHEMA,
)
from bridge_runtime import LiveRuntime
from fact_cards import (
    DEFAULT_FACT_CARDS_DIR,
    iter_fact_card_files,
    parse_fact_card_markdown,
)
from memoria_client import MemoriaClient
from storage import BridgeStorage, infer_super_chat_tier
from tts_gpt_sovits import GptSoVitsTTSProvider, TTSResult
from youtube_client import YouTubeClient, normalize_message


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger("youtube_bridge")
DEFAULT_LLM_TRACE_PATH = PROJECT_ROOT / "runtime" / "llm_trace.jsonl"


def clear_llm_trace_log(path: Path | None = None) -> dict[str, Any]:
    target = Path(path or DEFAULT_LLM_TRACE_PATH)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("", encoding="utf-8")
    return {"cleared": True, "path": str(target)}


class YouTubeBridgeManager(
    EpisodePlanManagerMixin,
    PhasePipelineManagerMixin,
    DirectorRuntimeManagerMixin,
    ClosingManagerMixin,
    InjectionManagerMixin,
    RuntimeLifecycleManagerMixin,
    EventSafetyManagerMixin,
    TestRuntimeManagerMixin,
    DirectorManagerMixin,
    TopicPackManagerMixin,
):
    def __init__(
        self,
        storage: BridgeStorage,
        youtube_client: YouTubeClient | None = None,
        memoria_client_factory=None,
        tts_provider_factory=None,
    ):
        self.storage = storage
        self.youtube_client = youtube_client or YouTubeClient()
        self.memoria_client_factory = memoria_client_factory or MemoriaClient
        self.tts_provider_factory = tts_provider_factory or GptSoVitsTTSProvider
        self._memoria_client_cache = None
        self._tts_provider_cache = None
        self.auto_finalize_archive_callback = None
        self.phase_summary_callback = None
        self.phase_cleanup_callback = None
        self._runtimes: dict[str, LiveRuntime] = {}
        self._lock = asyncio.Lock()

    def _memoria_client(self):
        if self._memoria_client_cache is None:
            self._memoria_client_cache = self.memoria_client_factory()
        return self._memoria_client_cache

    def reset_memoria_client(self) -> None:
        self._memoria_client_cache = None

    def _tts_provider(self):
        if self._tts_provider_cache is None:
            self._tts_provider_cache = self.tts_provider_factory()
        return self._tts_provider_cache

    @staticmethod
    def _presentation_enabled(session: dict[str, Any] | None) -> bool:
        return bool((session or {}).get("presentation_enabled"))

    @staticmethod
    def _presentation_ack_timeout(session: dict[str, Any] | None) -> int:
        try:
            value = int((session or {}).get("presentation_ack_timeout_seconds", 120) or 120)
        except (TypeError, ValueError):
            value = 120
        return max(1, min(value, 600))

    @staticmethod
    def _split_presentation_utterances(text: str) -> list[str]:
        clean = " ".join(str(text or "").replace("\r", "\n").split())
        if not clean:
            return []
        parts: list[str] = []
        start = 0
        for index, char in enumerate(clean):
            if char in "。！？!?":
                part = clean[start:index + 1].strip()
                if part:
                    parts.append(part)
                start = index + 1
        tail = clean[start:].strip()
        if tail:
            parts.append(tail)
        return parts or [clean]

    @staticmethod
    def _presentation_audio_root() -> Path:
        return PROJECT_ROOT / "runtime" / "YouTubeBridge" / "TTSAudio"

    @staticmethod
    def _presentation_debug_value(value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, dict):
            return {
                str(key)[:80]: YouTubeBridgeManager._presentation_debug_value(item)
                for key, item in list(value.items())[:40]
            }
        if isinstance(value, (list, tuple, set)):
            return [YouTubeBridgeManager._presentation_debug_value(item) for item in list(value)[:40]]
        return str(value)[:500]

    @staticmethod
    def _presentation_debug_payload(
        session_id: str,
        phase: str,
        item: dict[str, Any] | None = None,
        **details: Any,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "phase": phase,
            "session_id": session_id,
            "at": datetime.now().isoformat(),
        }
        if item:
            text = str(item.get("text") or "")
            payload.update({
                "item_id": item.get("item_id") or "",
                "interaction_job_id": item.get("interaction_job_id") or "",
                "message_id": item.get("message_id") or "",
                "character_id": item.get("character_id") or "",
                "character_name": item.get("character_name") or "",
                "sequence_index": item.get("sequence_index"),
                "status": item.get("status") or "",
                "has_audio": bool(item.get("audio_path")),
                "audio_format": item.get("audio_format") or "",
                "text_chars": len(text),
                "text_preview": text[:80],
                "presented_at": item.get("presented_at") or "",
                "acked_at": item.get("acked_at") or "",
                "error": item.get("error") or "",
            })
        for key, value in details.items():
            if value is not None:
                payload[str(key)] = YouTubeBridgeManager._presentation_debug_value(value)
        return payload

    @staticmethod
    def _log_presentation_debug_event(payload: dict[str, Any]) -> None:
        logger.warning(
            "PRESENTATION_QUEUE %s",
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
        )

    async def _emit_presentation_debug(
        self,
        session_id: str,
        phase: str,
        item: dict[str, Any] | None = None,
        **details: Any,
    ) -> dict[str, Any]:
        payload = self._presentation_debug_payload(session_id, phase, item, **details)
        self._log_presentation_debug_event(payload)
        await self._broadcast(
            session_id,
            {
                "type": "presentation_debug",
                "event": payload,
            },
        )
        return payload

    def report_presentation_client_debug(self, session_id: str, data: dict[str, Any]) -> dict[str, Any]:
        phase = str(data.get("phase") or "client_event")[:80]
        item_id = str(data.get("item_id") or "")
        item = self.storage.get_presentation_item(item_id) if item_id else None
        if item and item.get("session_id") != session_id:
            item = None
        details = data.get("details") if isinstance(data.get("details"), dict) else {}
        payload = self._presentation_debug_payload(
            session_id,
            phase,
            item,
            source="studio_client",
            item_id=item_id if not item else None,
            client_status=str(data.get("status") or "")[:80],
            details=details,
        )
        self._log_presentation_debug_event(payload)
        return payload

    def _presentation_item_public(self, item: dict[str, Any]) -> dict[str, Any]:
        audio_url = ""
        if item.get("audio_path"):
            audio_url = (
                f"/sessions/{item['session_id']}/presentation/"
                f"{item['item_id']}/audio"
            )
        return {
            "item_id": item.get("item_id"),
            "message_id": item.get("message_id"),
            "interaction_job_id": item.get("interaction_job_id"),
            "character_id": item.get("character_id"),
            "character_name": item.get("character_name"),
            "sequence_index": item.get("sequence_index"),
            "text": item.get("text") or "",
            "audio_url": audio_url,
            "audio_format": item.get("audio_format") or "wav",
            "status": item.get("status") or "",
        }

    async def present_stream_result(
        self,
        session_id: str,
        event: dict[str, Any],
        *,
        source: str,
        interaction_job_id: str = "",
    ) -> None:
        session = self.storage.get_session(session_id)
        message = self._chat_message_from_stream_result(event, source=source)
        if not message:
            return
        if not self._presentation_enabled(session):
            await self._broadcast(
                session_id,
                {
                    "type": "chat_message",
                    "message": message,
                    "source": source,
                },
            )
            return
        runtime = self._runtimes.setdefault(session_id, LiveRuntime(session_id=session_id))
        utterances = self._split_presentation_utterances(str(message.get("content") or ""))
        async with runtime.presentation_sequence_condition:
            presentation_sequence = runtime.presentation_next_sequence
            runtime.presentation_next_sequence += 1
        prepare_task: asyncio.Task | None = None
        try:
            if not utterances:
                return
            prepare_task = asyncio.create_task(self._prepare_presentation_item(
                session or {},
                message,
                utterances[0],
                index=0,
                source=source,
                interaction_job_id=interaction_job_id,
                runtime=runtime,
            ))
            async with runtime.presentation_sequence_condition:
                await runtime.presentation_sequence_condition.wait_for(
                    lambda: runtime.presentation_present_sequence == presentation_sequence
                )
            async with runtime.presentation_lock:
                for index, text in enumerate(utterances):
                    if prepare_task is None:
                        prepare_task = asyncio.create_task(self._prepare_presentation_item(
                            session or {},
                            message,
                            text,
                            index=index,
                            source=source,
                            interaction_job_id=interaction_job_id,
                            runtime=runtime,
                        ))
                    item = await prepare_task
                    prepare_task = None
                    next_index = index + 1
                    if next_index < len(utterances):
                        prepare_task = asyncio.create_task(self._prepare_presentation_item(
                            session or {},
                            message,
                            utterances[next_index],
                            index=next_index,
                            source=source,
                            interaction_job_id=interaction_job_id,
                            runtime=runtime,
                        ))
                    await self._present_prepared_item(
                        session or {},
                        message,
                        item,
                        source=source,
                        interaction_job_id=interaction_job_id,
                        runtime=runtime,
                    )
        except Exception:
            if prepare_task and not prepare_task.done():
                prepare_task.cancel()
            raise
        finally:
            async with runtime.presentation_sequence_condition:
                if runtime.presentation_present_sequence == presentation_sequence:
                    runtime.presentation_present_sequence += 1
                    while runtime.presentation_present_sequence in runtime.presentation_skipped_sequences:
                        runtime.presentation_skipped_sequences.remove(runtime.presentation_present_sequence)
                        runtime.presentation_present_sequence += 1
                else:
                    runtime.presentation_skipped_sequences.add(presentation_sequence)
                runtime.presentation_sequence_condition.notify_all()

    async def prepare_stream_result(
        self,
        session_id: str,
        event: dict[str, Any],
        *,
        source: str,
        interaction_job_id: str = "",
    ) -> dict[str, Any] | None:
        session = self.storage.get_session(session_id)
        message = self._chat_message_from_stream_result(event, source=source)
        if not message:
            return None
        if not self._presentation_enabled(session):
            return {"message": message, "items": []}
        runtime = self._runtimes.setdefault(session_id, LiveRuntime(session_id=session_id))
        items: list[dict[str, Any]] = []
        for index, text in enumerate(self._split_presentation_utterances(str(message.get("content") or ""))):
            items.append(await self._prepare_presentation_item(
                session or {},
                message,
                text,
                index=index,
                source=source,
                interaction_job_id=interaction_job_id,
                runtime=runtime,
            ))
        return {"message": message, "items": items}

    async def present_prepared_stream_results(
        self,
        session_id: str,
        prepared_results: list[dict[str, Any]],
        *,
        source: str,
        interaction_job_id: str = "",
    ) -> None:
        if not prepared_results:
            return
        session = self.storage.get_session(session_id)
        if not self._presentation_enabled(session):
            for prepared in prepared_results:
                message = prepared.get("message") if isinstance(prepared, dict) else None
                if isinstance(message, dict):
                    await self._broadcast(
                        session_id,
                        {
                            "type": "chat_message",
                            "message": message,
                            "source": source,
                        },
                    )
            return
        runtime = self._runtimes.setdefault(session_id, LiveRuntime(session_id=session_id))
        async with runtime.presentation_sequence_condition:
            presentation_sequence = runtime.presentation_next_sequence
            runtime.presentation_next_sequence += 1
        try:
            async with runtime.presentation_sequence_condition:
                await runtime.presentation_sequence_condition.wait_for(
                    lambda: runtime.presentation_present_sequence == presentation_sequence
                )
            async with runtime.presentation_lock:
                for prepared in prepared_results:
                    if not isinstance(prepared, dict):
                        continue
                    message = prepared.get("message") if isinstance(prepared.get("message"), dict) else {}
                    for item in prepared.get("items") or []:
                        if isinstance(item, dict):
                            await self._present_prepared_item(
                                session or {},
                                message,
                                item,
                                source=source,
                                interaction_job_id=interaction_job_id,
                                runtime=runtime,
                            )
        finally:
            async with runtime.presentation_sequence_condition:
                if runtime.presentation_present_sequence == presentation_sequence:
                    runtime.presentation_present_sequence += 1
                    while runtime.presentation_present_sequence in runtime.presentation_skipped_sequences:
                        runtime.presentation_skipped_sequences.remove(runtime.presentation_present_sequence)
                        runtime.presentation_present_sequence += 1
                else:
                    runtime.presentation_skipped_sequences.add(presentation_sequence)
                runtime.presentation_sequence_condition.notify_all()

    async def _prepare_presentation_item(
        self,
        session: dict[str, Any],
        message: dict[str, Any],
        text: str,
        *,
        index: int,
        source: str,
        interaction_job_id: str,
        runtime: LiveRuntime,
    ) -> dict[str, Any]:
        session_id = str(session.get("session_id") or runtime.session_id)
        item = self.storage.create_presentation_item({
            "session_id": session_id,
            "interaction_job_id": interaction_job_id,
            "message_id": f"{message.get('message_id') or ''}:{index}",
            "character_id": message.get("character_id") or "",
            "character_name": message.get("character_name") or "",
            "sequence_index": index,
            "text": text,
            "audio_format": "wav",
            "metadata": {"source": source},
        })
        await self._emit_presentation_debug(session_id, "item_created", item, source=source)
        item = self.storage.update_presentation_item(item["item_id"], status="synthesizing") or item
        await self._emit_presentation_debug(session_id, "item_synthesizing", item, source=source)
        try:
            tts_result = await self._synthesize_presentation_audio(session, item)
            update_fields: dict[str, Any] = {"status": "ready"}
            if tts_result.ok and tts_result.audio_bytes:
                audio_dir = self._presentation_audio_root() / session_id
                audio_dir.mkdir(parents=True, exist_ok=True)
                audio_format = tts_result.audio_format or "wav"
                audio_path = audio_dir / f"{item['item_id']}.{audio_format}"
                audio_path.write_bytes(tts_result.audio_bytes)
                update_fields.update({
                    "audio_path": str(audio_path),
                    "audio_format": audio_format,
                    "error": "",
                })
            else:
                update_fields.update({
                    "audio_format": tts_result.audio_format or item.get("audio_format") or "wav",
                    "error": tts_result.error,
                })
        except Exception as exc:
            update_fields = {
                "status": "ready",
                "error": str(exc)[:500],
            }
        item = self.storage.update_presentation_item(item["item_id"], **update_fields) or item
        ready_phase = "item_prefetch_ready" if source == "director_prefetch" else "item_ready"
        await self._emit_presentation_debug(session_id, ready_phase, item, source=source)
        return item

    async def _present_prepared_item(
        self,
        session: dict[str, Any],
        message: dict[str, Any],
        item: dict[str, Any],
        *,
        source: str,
        interaction_job_id: str,
        runtime: LiveRuntime,
    ) -> None:
        session_id = str(session.get("session_id") or runtime.session_id)
        update_fields: dict[str, Any] = {
            "status": "presenting",
            "presented_at": datetime.now().isoformat(),
        }
        if not item.get("audio_path") and item.get("error"):
            update_fields["status"] = "failed"
        item = self.storage.update_presentation_item(item["item_id"], **update_fields) or item
        await self._emit_presentation_debug(session_id, "item_presenting", item, source=source)
        if interaction_job_id:
            self.storage.update_interaction(interaction_job_id, status="presenting")
        ack_event = asyncio.Event()
        runtime.presentation_ack_events[item["item_id"]] = ack_event
        await self._emit_presentation_debug(
            session_id,
            "ack_wait_start",
            item,
            source=source,
            timeout_seconds=self._presentation_ack_timeout(session),
        )
        await self._broadcast(
            session_id,
            {
                "type": "presentation_item_ready",
                "item": self._presentation_item_public(item),
            },
        )
        self._mark_interaction_message_visible(
            interaction_job_id,
            {
                **message,
                "message_id": item.get("message_id") or message.get("message_id"),
                "content": item.get("text") or message.get("content") or "",
                "created_at": item.get("presented_at") or message.get("created_at"),
                "timestamp": item.get("presented_at") or message.get("timestamp"),
            },
            source=source,
        )
        try:
            await asyncio.wait_for(
                ack_event.wait(),
                timeout=self._presentation_ack_timeout(session),
            )
        except asyncio.TimeoutError:
            item = self.storage.update_presentation_item(
                item["item_id"],
                status="skipped",
                error="presentation ack timeout",
            ) or item
            await self._emit_presentation_debug(session_id, "ack_timeout", item, source=source)
        finally:
            runtime.presentation_ack_events.pop(item["item_id"], None)
        if item.get("status") != "skipped":
            chat_message = {
                **message,
                "message_id": item.get("message_id") or message.get("message_id"),
                "content": item.get("text") or "",
                "created_at": item.get("presented_at") or message.get("created_at"),
                "timestamp": item.get("presented_at") or message.get("timestamp"),
            }
            await self._broadcast(
                session_id,
                {
                    "type": "chat_message",
                    "message": chat_message,
                    "source": source,
                },
            )
            self._mark_interaction_message_visible(
                interaction_job_id,
                chat_message,
                source=source,
            )

    async def _synthesize_presentation_audio(
        self,
        session: dict[str, Any],
        item: dict[str, Any],
    ) -> TTSResult:
        if not session.get("tts_enabled"):
            return TTSResult(ok=False, audio_format="wav", error="tts disabled")
        profile = self.storage.get_tts_profile(str(item.get("character_id") or "")) or {}
        if not profile or not profile.get("enabled"):
            return TTSResult(ok=False, audio_format="wav", error="tts profile missing")
        provider = self._tts_provider()
        return await asyncio.to_thread(provider.synthesize, item.get("text") or "", profile)

    async def ack_presentation_item(self, session_id: str, item_id: str) -> dict | None:
        item = self.storage.get_presentation_item(item_id)
        if not item or item.get("session_id") != session_id:
            return None
        update_fields = {"acked_at": datetime.now().isoformat()}
        if item.get("status") != "failed":
            update_fields["status"] = "played"
        updated = self.storage.update_presentation_item(
            item_id,
            **update_fields,
        )
        runtime = self._runtimes.setdefault(session_id, LiveRuntime(session_id=session_id))
        ack_event = runtime.presentation_ack_events.get(item_id)
        await self._emit_presentation_debug(
            session_id,
            "ack_received",
            updated or item,
            ack_event_found=bool(ack_event),
        )
        if ack_event:
            ack_event.set()
        return updated

    async def skip_current_presentation_item(self, session_id: str) -> dict | None:
        runtime = self._runtimes.setdefault(session_id, LiveRuntime(session_id=session_id))
        items = self.storage.list_presentation_items(session_id, statuses={"presenting", "failed"}, limit=1)
        if not items:
            return None
        item = items[-1]
        updated = self.storage.update_presentation_item(
            item["item_id"],
            status="skipped",
            acked_at=datetime.now().isoformat(),
        )
        ack_event = runtime.presentation_ack_events.get(item["item_id"])
        await self._emit_presentation_debug(
            session_id,
            "item_skipped",
            updated or item,
            ack_event_found=bool(ack_event),
        )
        if ack_event:
            ack_event.set()
        return updated

    async def _run_auto_finalize_archive_callback(
        self,
        session_id: str,
        *,
        finalized_by: str,
        finalized: dict[str, Any],
    ) -> dict[str, Any] | None:
        callback = self.auto_finalize_archive_callback
        if not callback:
            return None
        result = callback(session_id, finalized_by=finalized_by, finalized=finalized)
        if inspect.isawaitable(result):
            return await result
        return result

    @staticmethod
    def _clear_llm_trace_log() -> dict[str, Any]:
        return clear_llm_trace_log()

    @staticmethod
    def _public_decision(decision: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(decision, dict):
            return {}
        return {
            "action": decision.get("action"),
            "reason": decision.get("reason"),
            "current_topic": decision.get("current_topic"),
        }

    @staticmethod
    def _public_director_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(metadata, dict):
            return {}
        public: dict[str, Any] = {}
        for key, value in metadata.items():
            key_str = str(key)
            key_lower = key_str.lower()
            if key_lower in {"opening_decision", "last_decision", "decision"} and isinstance(value, dict):
                public[key_str] = YouTubeBridgeManager._public_decision(value)
                continue
            if "prompt" in key_lower:
                continue
            if key_lower in {"hidden_context", "external_context", "context_text", "raw_context"}:
                public[key_str] = "[hidden]"
                continue
            if key_lower in {"events", "event_ids", "super_chats", "comments"} and isinstance(value, list):
                public[key_str] = {"count": len(value)}
                continue
            if key_lower == "interaction" and isinstance(value, dict):
                public[key_str] = YouTubeBridgeManager._public_interaction_status(value)
                continue
            if isinstance(value, dict):
                public[key_str] = YouTubeBridgeManager._public_director_metadata(value)
                continue
            public[key_str] = value
        return public

    @staticmethod
    def _public_director_state(state: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(state, dict):
            return state
        public = dict(state)
        public["metadata"] = YouTubeBridgeManager._public_director_metadata(public.get("metadata"))
        return public

    @staticmethod
    def _public_interaction_status(interaction: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(interaction, dict):
            return interaction
        public = dict(interaction)
        for field in ("content", "reply_text", "closure_text"):
            public[field] = YouTubeBridgeManager._public_interaction_text(public.get(field))
        metadata = public.get("metadata") if isinstance(public.get("metadata"), dict) else {}
        public_metadata: dict[str, Any] = {}
        for key, value in metadata.items():
            key_str = str(key)
            key_lower = key_str.lower()
            if key_lower == "decision" and isinstance(value, dict):
                public_metadata["decision"] = YouTubeBridgeManager._public_decision(value)
            elif "prompt" in key_lower:
                continue
            elif key_lower in {"hidden_context", "external_context", "context_text", "raw_context"}:
                public_metadata[key_str] = "[hidden]"
            elif key_lower in {"events", "event_ids", "super_chats", "comments"} and isinstance(value, list):
                public_metadata[key_str] = {"count": len(value)}
            elif key_lower in {"summary"} and isinstance(value, dict):
                public_metadata[key_str] = {
                    summary_key: value.get(summary_key)
                    for summary_key in ("source", "source_session_id", "event_count", "dropped_count")
                    if summary_key in value
                }
            elif isinstance(value, dict):
                public_metadata[key_str] = YouTubeBridgeManager._public_director_metadata(value)
            else:
                public_metadata[key_str] = value
        public["metadata"] = public_metadata
        return public

    @staticmethod
    def _public_interaction_text(value: Any) -> str:
        text = str(value or "")
        hidden_markers = (
            "<external_chat_context",
            "<topic_pack_fact_cards",
            "hidden external context",
            "完整 SC 清單",
        )
        if any(marker in text for marker in hidden_markers):
            return "[hidden context]"
        if len(text) > 800:
            return f"{text[:800]}... [truncated {len(text)} chars]"
        return text

    def _attach_live_persona_overrides(
        self,
        session: dict[str, Any] | None,
        external_context: dict[str, Any],
    ) -> dict[str, Any]:
        """把直播角色 overlay 附加到 trusted external_context。

        這份 raw prompt 只送往 MemoriaCore final prompt path，不進 public
        event/status sanitizer。
        """
        context = dict(external_context)
        if not session:
            return context
        character_ids = session.get("character_ids") or []
        overrides = self.storage.live_persona_prompt_overrides_for(character_ids)
        if overrides:
            context["character_prompt_overrides"] = overrides
        return context

    @staticmethod
    def _chat_message_from_stream_result(event: dict[str, Any], *, source: str) -> dict[str, Any] | None:
        if not isinstance(event, dict):
            return None
        content = str(event.get("reply") or event.get("content") or "").strip()
        if not content:
            return None
        now = datetime.now().isoformat()
        return {
            "message_id": event.get("message_id"),
            "role": "assistant",
            "content": YouTubeBridgeManager._public_interaction_text(content),
            "created_at": event.get("created_at") or event.get("timestamp") or now,
            "timestamp": event.get("timestamp") or event.get("created_at") or now,
            "character_id": event.get("character_id"),
            "character_name": event.get("character_name"),
            "turn_index": event.get("turn_index"),
            "source": source,
        }

    def _mark_interaction_message_visible(
        self,
        interaction_job_id: str,
        message: dict[str, Any],
        *,
        source: str,
    ) -> None:
        if not interaction_job_id or not isinstance(message, dict):
            return
        content = str(message.get("content") or "").strip()
        if not content:
            return
        current = self.storage.get_interaction(interaction_job_id)
        if not current:
            return
        visible_message = {
            "message_id": message.get("message_id"),
            "role": message.get("role") or "assistant",
            "content": content,
            "created_at": message.get("created_at") or message.get("timestamp") or "",
            "timestamp": message.get("timestamp") or message.get("created_at") or "",
            "character_id": message.get("character_id"),
            "character_name": message.get("character_name"),
            "source": source,
        }
        self.storage.append_interaction_visible_message(interaction_job_id, visible_message)

    @staticmethod
    def _log_stream_broadcast_future(future: Any) -> None:
        try:
            future.result()
        except Exception as exc:
            logger.warning("stream chat broadcast task failed error=%s", exc, exc_info=True)

    def _stream_interaction_is_stale(self, interaction_job_id: str) -> bool:
        if not interaction_job_id:
            return False
        current = self.storage.get_interaction(interaction_job_id)
        return bool(current and current.get("status") in {"interrupt_requested", "interrupted", "discarded"})

    def _broadcast_stream_chat_message(
        self,
        loop: asyncio.AbstractEventLoop,
        session_id: str,
        event: dict[str, Any],
        *,
        source: str,
        interaction_job_id: str = "",
    ) -> None:
        message = self._chat_message_from_stream_result(event, source=source)
        if not message:
            return
        async def broadcast_if_current() -> None:
            if self._stream_interaction_is_stale(interaction_job_id):
                logger.warning(
                    "stale_generation_dropped_before_broadcast session_id=%s job_id=%s source=%s",
                    session_id,
                    interaction_job_id,
                    source,
                )
                return
            await self._broadcast(
                session_id,
                {
                    "type": "chat_message",
                    "message": message,
                    "source": source,
                },
            )
            self._mark_interaction_message_visible(
                interaction_job_id,
                message,
                source=source,
            )

        future = asyncio.run_coroutine_threadsafe(
            broadcast_if_current(),
            loop,
        )
        future.add_done_callback(self._log_stream_broadcast_future)

    def _dispatch_stream_chat_result(
        self,
        loop: asyncio.AbstractEventLoop,
        session_id: str,
        event: dict[str, Any],
        *,
        source: str,
        interaction_job_id: str = "",
        wait_for_completion: bool = True,
    ):
        if interaction_job_id:
            current = self.storage.get_interaction(interaction_job_id)
            if current and current.get("status") in {"interrupt_requested", "interrupted", "discarded"}:
                logger.warning(
                    "stale_generation_dropped session_id=%s job_id=%s source=%s status=%s",
                    session_id,
                    interaction_job_id,
                    source,
                    current.get("status"),
                )
                return None
        session = self.storage.get_session(session_id)
        if not self._presentation_enabled(session):
            self._broadcast_stream_chat_message(
                loop,
                session_id,
                event,
                source=source,
                interaction_job_id=interaction_job_id,
            )
            return None
        future = asyncio.run_coroutine_threadsafe(
            self.present_stream_result(
                session_id,
                event,
                source=source,
                interaction_job_id=interaction_job_id,
            ),
            loop,
        )
        if not wait_for_completion:
            return future
        message = self._chat_message_from_stream_result(event, source=source) or {}
        utterance_count = max(1, len(self._split_presentation_utterances(str(message.get("content") or ""))))
        future.result(timeout=(self._presentation_ack_timeout(session) * utterance_count) + 120)
        return future

    async def _poll_loop(self, runtime: LiveRuntime) -> None:
        while runtime.running:
            session = self.storage.get_session(runtime.session_id)
            if not session:
                runtime.status = "missing"
                runtime.running = False
                return
            if runtime.status == "closing" or session.get("status") == "closing":
                await asyncio.sleep(1.0)
                continue
            if self._duration_reached(session):
                await self._finalize_for_duration(runtime, session)
                return
            connector = self.storage.get_connector(session["connector_id"])
            if not connector:
                runtime.status = "connector_missing"
                runtime.running = False
                return
            try:
                data = await asyncio.to_thread(
                    self.youtube_client.fetch_live_chat_messages,
                    api_key=connector["api_key"],
                    live_chat_id=session["live_chat_id"],
                    page_token=runtime.next_page_token,
                )
                runtime.next_page_token = data.get("nextPageToken") or runtime.next_page_token
                runtime.status = "running"
                runtime.last_error = None
                saved_any = False
                for item in data.get("items") or []:
                    event = normalize_message(item, session=session, connector=connector)
                    if not event.get("youtube_message_id"):
                        continue
                    metadata = dict(event.get("metadata") if isinstance(event.get("metadata"), dict) else {})
                    metadata.setdefault("phase", self._event_phase_for_session(runtime.session_id))
                    event["metadata"] = metadata
                    saved = self.storage.save_event(event)
                    if saved:
                        saved_any = True
                        public_event = self._public_live_event(saved)
                        if public_event:
                            await self._broadcast(runtime.session_id, {"type": "youtube_live_event", "event": public_event})
                if saved_any:
                    self._schedule_pending_event_classification(runtime)
                interval_ms = int(data.get("pollingIntervalMillis") or 5000)
                await asyncio.sleep(max(2.0, min(interval_ms / 1000, 30.0)))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self._is_live_chat_ended_error(exc):
                    finalized_at = datetime.now().isoformat()
                    runtime.status = "ended"
                    runtime.running = False
                    runtime.last_error = str(exc)
                    self.storage.update_session_fields(
                        runtime.session_id,
                        status="ended",
                        finalized_at=finalized_at,
                        summary_status=session.get("summary_status") or "pending",
                    )
                    await self._broadcast(
                        runtime.session_id,
                        {
                            "type": "status",
                            "status": "ended",
                            "message": "YouTube live chat ended",
                            "finalized_at": finalized_at,
                        },
                    )
                    try:
                        await self._run_auto_finalize_archive_callback(
                            runtime.session_id,
                            finalized_by="youtube_live_chat_ended",
                            finalized={
                                **(self.storage.get_session(runtime.session_id) or session),
                                "runtime_status": self.get_status(runtime.session_id),
                            },
                        )
                    except Exception as archive_exc:
                        logger.warning(
                            "auto finalize archive failed session_id=%s error=%s",
                            runtime.session_id,
                            archive_exc,
                        )
                    return
                runtime.status = "error"
                runtime.last_error = str(exc)
                self.storage.update_session_fields(runtime.session_id, status="error")
                logger.error("YouTube polling error session_id=%s error=%s", runtime.session_id, exc, exc_info=True)
                await self._broadcast(
                    runtime.session_id,
                    {"type": "status", "status": "error", "message": str(exc)},
                )
                await asyncio.sleep(15)

    @staticmethod
    def _is_live_chat_ended_error(exc: Exception) -> bool:
        message = str(exc).lower()
        return "livechatended" in message or "live chat is no longer live" in message

    def _live_query_context_for_events(
        self,
        session: dict[str, Any],
        events: list[dict[str, Any]],
        lines: list[str],
    ) -> tuple[str, dict[str, Any]]:
        session_id = str(session.get("session_id") or "")
        query_intent = self._audience_query_intent_from_events(events)
        query_text = str(query_intent.get("sanitized_query") or "").strip()
        resolution: dict[str, Any] = {
            "query": query_text,
            "query_intent": query_intent,
            "local_answerable": False,
            "local_entry_count": 0,
            "local_rejected_by_topic_count": 0,
            "local_top_similarity": None,
            "research_status": "not_needed" if not query_text else "not_attempted",
            "research_error": "",
        }
        base_query = "\n".join([*lines, str(session.get("director_guidance") or "")])
        if not query_text:
            context = self._topic_pack_sequence_context_for_session(
                session_id,
                base_query,
                usage_source="external_context",
            )
            return context, resolution

        entries, search_status = self._topic_pack_entries_for_query(
            session_id,
            query_text,
            limit=6,
            min_score=AUDIENCE_QUERY_FACT_CARD_MIN_SCORE,
            allow_fallback=False,
        )
        resolution["local_entry_count"] = len(entries)
        resolution["local_top_similarity"] = search_status.get("top_similarity")
        query_terms = self._audience_query_topic_terms(query_text)
        topic_matched_entries = entries
        if query_terms:
            topic_matched_entries = [
                entry for entry in entries
                if self._topic_pack_entry_matches_query_terms(entry, query_terms)
            ]
            resolution["local_rejected_by_topic_count"] = max(0, len(entries) - len(topic_matched_entries))
        if self._topic_pack_entries_can_answer(topic_matched_entries, query_text=query_text):
            resolution["local_answerable"] = True
            resolution["research_status"] = "not_needed"
            context_entries = self._topic_graph_context_entries_for_hits(
                session_id,
                topic_matched_entries[:1],
                query_text,
                "external_context",
                max_entries=4,
            )
            return self._topic_pack_context_text(context_entries), resolution

        if not session.get("research_enabled"):
            resolution["research_status"] = "disabled"
            return "", resolution
        if not query_intent.get("needs_external_search") or not query_intent.get("safe_search_allowed"):
            resolution["research_status"] = "not_allowed"
            return "", resolution

        completed_context, completed_status = self._completed_audience_research_context(session_id, query_text)
        if completed_context:
            resolution["research_status"] = completed_status or "completed"
            return completed_context, resolution

        worker = self._ensure_audience_research_worker(
            session,
            query_text,
            pack_id=self._first_session_topic_pack_id(session_id),
        )
        resolution["research_status"] = str(worker.get("status") or "queued")
        if worker.get("error"):
            resolution["research_error"] = str(worker.get("error") or "")[:300]
        if resolution["research_status"] in {"queued", "running"}:
            resolution["fallback_reason"] = "research_incomplete"
            return (
                "觀眾查詢資料狀態：相關查證仍在背景處理；"
                "本輪只能根據已知直播脈絡安全回應，不得宣稱已查到最新資料或具體排名。",
                resolution,
            )
        return "", resolution

    @staticmethod
    def _topic_pack_entries_can_answer(entries: list[dict[str, Any]], *, query_text: str = "") -> bool:
        if not entries:
            return False
        top_score = float(entries[0].get("similarity") or 0.0)
        if top_score >= AUDIENCE_QUERY_FACT_CARD_STRONG_SCORE:
            return True
        if top_score < AUDIENCE_QUERY_FACT_CARD_MIN_SCORE:
            return False
        query_terms = YouTubeBridgeManager._audience_query_topic_terms(query_text)
        if len(entries) == 1:
            if not query_terms:
                return True
            return YouTubeBridgeManager._topic_pack_entry_matches_query_terms(entries[0], query_terms)
        second_score = float(entries[1].get("similarity") or 0.0)
        return (top_score - second_score) >= AUDIENCE_QUERY_FACT_CARD_MIN_GAP

    @staticmethod
    def _normalize_topic_match_text(value: Any) -> str:
        text = str(value or "").lower()
        text = re.sub(r"[《》〈〉「」『』【】\[\]（）()]", " ", text)
        text = re.sub(r"[\s\r\n\t_\-／/・:：,，.。!！?？;；、]+", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _audience_query_topic_terms(query_text: str) -> list[str]:
        normalized = YouTubeBridgeManager._normalize_topic_match_text(query_text)
        if not normalized:
            return []
        generic_terms = {
            "劇情",
            "解說",
            "介紹",
            "分析",
            "評價",
            "看點",
            "心得",
            "整理",
            "動畫",
            "作品",
            "這部",
            "這部動畫",
        }
        terms: list[str] = []

        def add_term(value: str) -> None:
            term = value.strip()
            if not term or term in generic_terms or len(term) < 2:
                return
            if term not in terms:
                terms.append(term)

        for token in normalized.split():
            add_term(token.replace("的", ""))

        compact = "".join(normalized.split()).replace("的", "")
        compact = re.sub(r"^(請問|想知道|想補一下|幫我查|可以講一下|可以說一下)", "", compact)
        for phrase in (
            "可以講一下嗎",
            "可以說一下嗎",
            "可以講一下",
            "可以說一下",
            "有什麼看點",
            "有哪些看點",
            "是誰",
            "嗎",
            "呢",
        ):
            compact = compact.replace(phrase, "")
        for suffix in (
            "劇情解說",
            "劇情介紹",
            "劇情分析",
            "畫風解說",
            "評價解說",
            "解說",
            "介紹",
            "分析",
            "評價",
            "看點",
            "心得",
        ):
            if compact.endswith(suffix):
                compact = compact[: -len(suffix)]
                break
        add_term(compact)
        topicish = compact
        for phrase in ("有什麼", "有哪些", "可以", "深入", "比較", "細節", "查證", "資料"):
            topicish = topicish.replace(phrase, " ")
        for segment in re.split(r"\s+", topicish):
            segment = segment.strip()
            if not segment:
                continue
            add_term(segment)
            for size in (4, 3):
                if len(segment) <= size:
                    continue
                for index in range(0, len(segment) - size + 1):
                    add_term(segment[index:index + size])
        return sorted(terms, key=len, reverse=True)

    @staticmethod
    def _topic_pack_entry_matches_query_terms(entry: dict[str, Any], terms: list[str]) -> bool:
        if not terms:
            return True
        values: list[Any] = [
            entry.get("title"),
            entry.get("body"),
            entry.get("summary"),
        ]
        tags = entry.get("tags")
        if isinstance(tags, list):
            values.extend(tags)
        entry_text = YouTubeBridgeManager._normalize_topic_match_text(" ".join(str(value or "") for value in values))
        entry_compact = "".join(entry_text.split()).replace("的", "")
        for term in terms:
            normalized_term = YouTubeBridgeManager._normalize_topic_match_text(term)
            compact_term = "".join(normalized_term.split()).replace("的", "")
            if compact_term and compact_term in entry_compact:
                return True
        return False

    def _audience_query_text_from_events(self, events: list[dict[str, Any]]) -> str:
        return str(self._audience_query_intent_from_events(events).get("sanitized_query") or "").strip()

    def _audience_query_intent_from_events(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        request_events: list[dict[str, Any]] = []
        for event in events:
            text = self._event_safe_text(event)
            if not text:
                continue
            request_events.append({
                "author_display_name": str(event.get("author_display_name") or "匿名觀眾")[:80],
                "priority_class": str(event.get("priority_class") or "normal"),
                "message_text": text[:500],
            })
        default = {
            "is_factual_question": False,
            "needs_external_search": False,
            "safe_search_allowed": False,
            "sanitized_query": "",
            "topic_scope": "",
            "risk_label": "unknown",
            "reason": "沒有可分類的安全觀眾留言。",
        }
        if not request_events:
            return default
        try:
            try:
                client = self.memoria_client_factory(timeout=15.0)
            except TypeError:
                client = self._memoria_client()
            result = client.generate_prompt_json(
                prompt_key="youtube_live_audience_query_classifier_prompt",
                variables={"events_json": json.dumps(request_events, ensure_ascii=False, indent=2)},
                task_key="router",
                temperature=0.0,
                schema=AUDIENCE_QUERY_CLASSIFIER_SCHEMA,
            )
        except Exception as exc:
            logger.warning("audience query classifier failed error=%s", exc)
            return {**default, "reason": f"query classifier failed: {str(exc)[:180]}"}
        if not isinstance(result, dict):
            return default
        factual = bool(result.get("is_factual_question"))
        safe = bool(result.get("safe_search_allowed"))
        query = self._single_line(result.get("sanitized_query") or "")[:240]
        if not factual:
            query = ""
        return {
            "is_factual_question": factual,
            "needs_external_search": bool(result.get("needs_external_search")) and bool(query),
            "safe_search_allowed": safe and bool(query),
            "sanitized_query": query if safe else "",
            "topic_scope": self._single_line(result.get("topic_scope") or "")[:80],
            "risk_label": self._single_line(result.get("risk_label") or "unknown")[:80],
            "reason": self._single_line(result.get("reason") or "")[:240],
        }

    def _ensure_audience_research_worker(
        self,
        session: dict[str, Any],
        query_text: str,
        *,
        pack_id: int | None = None,
    ) -> dict[str, Any]:
        session_id = str(session.get("session_id") or "")
        query_key = self._audience_query_key(session_id, query_text)
        runtime = self._runtimes.setdefault(session_id, LiveRuntime(session_id=session_id))
        thread = runtime.audience_research_tasks.get(query_key)
        thread_alive = bool(thread and thread.is_alive())
        existing = self._audience_research_job(session_id, query_key)
        if existing.get("in_progress") and thread_alive:
            return {**existing, "status": str(existing.get("status") or "running")}
        if str(existing.get("status") or "") in {"completed", "completed_with_results", "completed_no_results", "degraded", "failed"}:
            return existing
        if thread_alive:
            return {"status": "running", "query_key": query_key, "query": query_text}
        if thread:
            runtime.audience_research_tasks.pop(query_key, None)
        started_at = datetime.now().isoformat()
        self._update_audience_research_job(session_id, query_key, {
            "status": "queued",
            "in_progress": True,
            "query": query_text,
            "pack_id": int(pack_id) if pack_id else 0,
            "started_at": started_at,
            "updated_at": started_at,
            "error": "",
        })
        thread = threading.Thread(
            target=self._run_audience_research_worker,
            args=(session_id, query_key, query_text),
            kwargs={"pack_id": pack_id},
            name=f"audience-research-{session_id[:12]}",
            daemon=True,
        )
        runtime.audience_research_tasks[query_key] = thread
        thread.start()
        return {"status": "queued", "query_key": query_key, "query": query_text}

    def _run_audience_research_worker(
        self,
        session_id: str,
        query_key: str,
        query_text: str,
        *,
        pack_id: int | None = None,
    ) -> None:
        self._update_audience_research_job(session_id, query_key, {
            "status": "running",
            "in_progress": True,
            "query": query_text,
            "pack_id": int(pack_id) if pack_id else 0,
            "updated_at": datetime.now().isoformat(),
            "error": "",
        })
        try:
            result = self._research_request_sync(
                session_id,
                query_text,
                pack_id=pack_id,
                enforce_cooldown=True,
            )
            entry = result.get("entry") if isinstance(result, dict) else {}
            record = result.get("record") if isinstance(result, dict) else {}
            status = str((record or {}).get("status") or result.get("status") or "completed")
            self._update_audience_research_job(session_id, query_key, {
                "status": status,
                "in_progress": False,
                "query": query_text,
                "pack_id": int(pack_id) if pack_id else int((entry or {}).get("pack_id") or 0),
                "entry_id": int((entry or {}).get("id") or 0),
                "updated_at": datetime.now().isoformat(),
                "error": "",
            })
        except Exception as exc:
            self._update_audience_research_job(session_id, query_key, {
                "status": "failed",
                "in_progress": False,
                "query": query_text,
                "pack_id": int(pack_id) if pack_id else 0,
                "updated_at": datetime.now().isoformat(),
                "error": str(exc)[:500],
            })
            logger.warning("audience research worker failed session_id=%s error=%s", session_id, exc)
        finally:
            runtime = self._runtimes.get(session_id)
            if runtime:
                runtime.audience_research_tasks.pop(query_key, None)

    @staticmethod
    def _audience_query_key(session_id: str, query_text: str) -> str:
        return uuid.uuid5(uuid.NAMESPACE_URL, f"youtube-live:{session_id}:{query_text}").hex

    def _audience_research_job(self, session_id: str, query_key: str) -> dict[str, Any]:
        state = self.storage.get_director_state(session_id)
        metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
        jobs = metadata.get("audience_query_research") if isinstance(metadata.get("audience_query_research"), dict) else {}
        job = jobs.get(query_key) if isinstance(jobs.get(query_key), dict) else {}
        return dict(job)

    def _update_audience_research_job(self, session_id: str, query_key: str, fields: dict[str, Any]) -> dict[str, Any]:
        state = self.storage.get_director_state(session_id)
        metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
        jobs = dict(metadata.get("audience_query_research") or {})
        current = dict(jobs.get(query_key) or {})
        current.update(fields)
        current["query_key"] = query_key
        jobs[query_key] = current
        self.storage.update_director_state(session_id, metadata={"audience_query_research": jobs})
        return current

    def _first_session_topic_pack_id(self, session_id: str) -> int | None:
        packs = self.storage.list_session_topic_packs(session_id)
        if not packs:
            return None
        return int(packs[0]["id"])

    def _completed_audience_research_context(self, session_id: str, query_text: str) -> tuple[str, str]:
        query_key = self._audience_query_key(session_id, query_text)
        job = self._audience_research_job(session_id, query_key)
        status = str(job.get("status") or "")
        if status not in {"completed", "completed_with_results", "degraded"}:
            return "", status
        entry_id = int(job.get("entry_id") or 0)
        if not entry_id:
            return "", status
        entry = self.storage.get_topic_pack_entry(entry_id)
        if not entry:
            return "", status
        self._record_topic_pack_usage(session_id, [entry], query_text, "external_context")
        return self._topic_pack_context_text([entry]), status


    @classmethod
    def _research_gate_usage_status(
        cls,
        entries: list[dict[str, Any]],
        research_requests: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        statuses: dict[str, int] = {}
        total = 0
        success = 0
        degraded = 0
        entry_ids = set()
        for entry in entries:
            if str(entry.get("source_type") or "") != "research_gate":
                continue
            entry_ids.add(int(entry.get("id") or entry.get("entry_id") or 0))
            total += 1
            status = cls._classify_research_gate_entry(entry)
            statuses[status] = statuses.get(status, 0) + 1
            if status == "success":
                success += 1
            else:
                degraded += 1
        for request in research_requests or []:
            status = str(request.get("status") or "").strip() or "unknown"
            result_entry_id = int(request.get("result_entry_id") or 0)
            if result_entry_id and result_entry_id in entry_ids:
                continue
            if status == "completed_with_results":
                continue
            statuses[status] = statuses.get(status, 0) + 1
            total += 1
            degraded += 1
        return {
            "total_count": total,
            "success_count": success,
            "degraded_count": degraded,
            "statuses": statuses,
        }

    @staticmethod
    def _classify_research_gate_entry(entry: dict[str, Any]) -> str:
        body = str(entry.get("body") or "").strip()
        body_lower = body.lower()
        if not body:
            return "degraded"
        if body.startswith(("{", "[")) or '"search_results"' in body_lower or "'search_results'" in body_lower:
            return "raw_dump"
        if "completed_no_results" in body_lower:
            return "completed_no_results"
        if "completed_with_results" in body_lower:
            return "success" if str(entry.get("source_url") or "").strip() else "degraded"
        if "confidence: low" in body_lower:
            return "degraded"
        return "degraded"

















    async def research_request(
        self,
        session_id: str,
        query: str,
        *,
        pack_id: int | None = None,
        enforce_cooldown: bool = True,
    ) -> dict[str, Any]:
        result = await asyncio.to_thread(
            self._research_request_sync,
            session_id,
            query,
            pack_id=pack_id,
            enforce_cooldown=enforce_cooldown,
        )
        await self._broadcast(session_id, {
            "type": "research_card_created",
            "session_id": session_id,
            "entry": result.get("entry"),
            "research": result.get("research") or result.get("record"),
            "embedding": result.get("embedding"),
        })
        return result

    def _research_request_sync(
        self,
        session_id: str,
        query: str,
        *,
        pack_id: int | None = None,
        enforce_cooldown: bool = True,
    ) -> dict[str, Any]:
        session = self.storage.get_session(session_id)
        if not session:
            raise ValueError("live session 不存在")
        if not session.get("research_enabled"):
            raise ValueError("本場直播未啟用 Research Gate")
        query = str(query or "").strip()
        if not query:
            raise ValueError("research query 不可為空")
        cooldown = max(0, int(session.get("research_cooldown_seconds", 300) or 300))
        session_limit = max(0, int(session.get("research_max_per_session", 12) or 12))
        if session_limit and self.storage.count_research_requests(session_id) >= session_limit:
            raise ValueError("Research Gate 已達本場查詢上限")
        if enforce_cooldown and cooldown:
            since = (datetime.now() - timedelta(seconds=cooldown)).isoformat()
            if self.storage.count_research_requests(session_id, since_iso=since) >= 2:
                raise ValueError("Research Gate 冷卻中，稍後再查")
        target_pack_id = pack_id
        if target_pack_id is None:
            packs = self.storage.list_session_topic_packs(session_id)
            if packs:
                target_pack_id = int(packs[0]["id"])
            else:
                pack = self.storage.create_topic_pack({
                    "title": f"{session.get('display_name') or session_id} Research",
                    "description": "Bridge Research Gate 自動建立的直播 fact cards。",
                })
                self.storage.link_topic_pack_to_session(session_id, int(pack["id"]))
                target_pack_id = int(pack["id"])
        try:
            from tools.tavily import search_web

            raw_result = search_web(query=query, topic="general")
        except Exception as exc:
            self.storage.create_research_request(session_id, query, status="failed", metadata={"error": str(exc)[:500]})
            raise
        body = self._research_result_to_fact_card(query, raw_result)
        research_meta = self._research_result_metadata(raw_result)
        entry = self.storage.create_topic_pack_entry(int(target_pack_id), {
            "title": query[:120],
            "body": body,
            "source_url": research_meta["source_urls"][0] if research_meta["source_urls"] else "",
            "source_type": "research_gate",
            "tags": ["research_gate"],
        })
        embedding = None
        try:
            embedding = self.index_topic_pack_entry(int(entry["id"]))
        except Exception as exc:
            logger.warning("research fact card embedding failed session_id=%s entry_id=%s error=%s", session_id, entry["id"], exc)
        record = self.storage.create_research_request(
            session_id,
            query,
            status=research_meta["status"],
            result_entry_id=int(entry["id"]),
            metadata={
                "pack_id": int(target_pack_id),
                "status": research_meta["status"],
                "source_count": len(research_meta["source_urls"]),
                "source_urls": research_meta["source_urls"],
                "source_titles": research_meta["source_titles"],
            },
        )
        return {
            "status": research_meta["status"],
            "source_count": len(research_meta["source_urls"]),
            "source_urls": research_meta["source_urls"],
            "entry": entry,
            "research": record,
            "record": record,
            "embedding": embedding,
        }

    @staticmethod
    def _research_items(raw_result: Any) -> list[dict[str, str]]:
        raw = raw_result
        if isinstance(raw_result, str):
            stripped = raw_result.strip()
            try:
                raw = json.loads(stripped)
            except Exception:
                raw = {"search_results": [{"title": "Research Gate result", "url": "", "content": stripped}]}
        if isinstance(raw, dict):
            candidates = (
                raw.get("results")
                or raw.get("search_results")
                or raw.get("items")
                or raw.get("data")
                or []
            )
        elif isinstance(raw, list):
            candidates = raw
        else:
            candidates = []
        if isinstance(candidates, str):
            candidates = YouTubeBridgeManager._legacy_research_text_items(candidates)

        items: list[dict[str, str]] = []
        for item in candidates[:8]:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or item.get("name") or item.get("source") or "").strip()
            url = str(item.get("url") or item.get("source_url") or item.get("link") or "").strip()
            content = str(item.get("content") or item.get("snippet") or item.get("summary") or item.get("body") or "").strip()
            if not any((title, url, content)):
                continue
            items.append({
                "title": title[:180],
                "url": url[:1000],
                "content": " ".join(content.replace("\r", " ").split())[:700],
            })
        return items

    @staticmethod
    def _legacy_research_text_items(text: str) -> list[dict[str, str]]:
        """解析舊版 Tavily wrapper 的純文字 search_results。"""
        blocks = [block.strip() for block in str(text or "").split("\n\n") if block.strip()]
        items: list[dict[str, str]] = []
        for block in blocks[:8]:
            lines = [line.strip() for line in block.splitlines() if line.strip()]
            if not lines:
                continue
            title = lines[0]
            if title.startswith("[") and "]" in title:
                title = title.split("]", 1)[1].strip()
            content = " ".join(lines[1:]).strip()
            items.append({
                "title": title[:180],
                "url": "",
                "content": content[:700],
            })
        return items

    @staticmethod
    def _research_result_metadata(raw_result: Any) -> dict[str, Any]:
        items = YouTubeBridgeManager._research_items(raw_result)
        source_titles = [item["title"] for item in items if item.get("title")][:5]
        source_urls = [item["url"] for item in items if item.get("url")][:5]
        return {
            "status": "completed_with_results" if items else "completed_no_results",
            "source_titles": source_titles,
            "source_urls": source_urls,
        }

    @staticmethod
    def _research_result_to_fact_card(query: str, raw_result: Any) -> str:
        items = YouTubeBridgeManager._research_items(raw_result)
        if not items:
            return (
                f"summary: Research Gate 查詢「{query}」沒有取得可用摘要。\n"
                "facts:\n"
                "- 目前沒有可引用的外部資料。\n"
                "source_titles:\n"
                "- none\n"
                "source_urls:\n"
                "- none\n"
                "confidence: low\n"
                "status: completed_no_results"
            )
        trusted_hosts = ("official", "anime", "news", "wikipedia", "wiki", "ann", "crunchyroll")
        ranked = sorted(
            items,
            key=lambda item: (
                0 if any(token in (item.get("url", "") + " " + item.get("title", "")).lower() for token in trusted_hosts) else 1,
                len(item.get("content", "")) * -1,
            ),
        )
        top = ranked[:4]
        facts = []
        for item in top:
            content = item.get("content") or item.get("title") or item.get("url") or ""
            if content:
                facts.append(content[:240])
        source_titles = [item.get("title") or "untitled" for item in top if item.get("title") or item.get("url")]
        source_urls = [item.get("url") for item in top if item.get("url")]
        summary_text = facts[0] if facts else f"Research Gate 查詢「{query}」取得 {len(items)} 筆來源。"
        lines = [
            f"summary: {summary_text}",
            "facts:",
            *[f"- {fact}" for fact in facts[:5]],
            "source_titles:",
            *[f"- {title}" for title in source_titles[:5]],
            "source_urls:",
            *[f"- {url}" for url in source_urls[:5]],
            "confidence: medium" if source_urls else "confidence: low",
            "status: completed_with_results",
        ]
        return "\n".join(lines)

    async def _broadcast(self, session_id: str, payload: dict[str, Any]) -> None:
        runtime = self._runtimes.get(session_id)
        if not runtime:
            return
        stale: list[asyncio.Queue] = []
        for queue in list(runtime.subscribers):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                stale.append(queue)
        for queue in stale:
            runtime.subscribers.discard(queue)

    @staticmethod
    def _single_line(value: Any) -> str:
        return engine_public_events.single_line(value)

    def build_external_context(
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
        topic_context, query_resolution = self._live_query_context_for_events(session, active_events, lines)
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

    @staticmethod
    def _event_line(event: dict[str, Any]) -> str:
        author = (event.get("author_display_name") or "匿名觀眾").strip()
        text = YouTubeBridgeManager._event_safe_text(event)
        if event.get("priority_class") == "super_chat":
            amount = str(event.get("amount_display_string") or "SC").strip()
            label = str(event.get("safety_label") or "unclassified")
            if label != "clean":
                safe_label = YouTubeBridgeManager._safe_label_text(label)
                return f"- [{amount}][安全標記: {safe_label}] {author or '匿名觀眾'}: {text}"
            return f"- [{amount}] {author or '匿名觀眾'}: {text}"
        if str(event.get("safety_label") or "unclassified") != "clean":
            safe_label = YouTubeBridgeManager._safe_label_text(str(event.get("safety_label") or "unclassified"))
            return f"- [安全標記: {safe_label}] {author or '匿名觀眾'}: {text}"
        return f"- {author or '匿名觀眾'}: {text}"

    @staticmethod
    def _should_block_director_for_pending_inject(event: dict[str, Any]) -> bool:
        """只有已通過安全檢查、可公開注入的留言會暫停 director idle。"""
        return YouTubeBridgeManager._is_public_live_event_displayable(event)

    @staticmethod
    def _test_comment_event_line(event: dict[str, Any]) -> str:
        if not YouTubeBridgeManager._is_public_live_event_displayable(event):
            return ""
        return YouTubeBridgeManager._visible_event_display_line(event)

    @staticmethod
    def _test_comment_interaction_line(item: dict[str, Any]) -> str:
        if str(item.get("status") or "") != "completed":
            return ""
        text = YouTubeBridgeManager._single_line(item.get("reply_text") or item.get("closure_text") or "")
        if not text:
            return ""
        source = str(item.get("source") or "")
        labels = {
            "director": "AI 回覆",
            "youtube_injection": "AI 回覆",
            "manual_inject": "AI 回覆",
            "auto_inject": "AI 回覆",
            "super_chat": "SC 回覆",
            "closing_super_chat_thanks": "SC 感謝",
        }
        label = labels.get(source, "AI 回覆")
        clean_text = YouTubeBridgeManager._sanitize_test_comment_text(text, "目前直播內容")
        return f"- {label}: {clean_text[:180]}"

    @staticmethod
    def _display_content_from_external_context(external_context: dict[str, Any]) -> str:
        lines: list[str] = []
        for event in external_context.get("visible_events") or []:
            if not isinstance(event, dict):
                continue
            line = YouTubeBridgeManager._visible_event_display_line(event)
            if line:
                lines.append(line)
        return "\n".join(lines)

    @staticmethod
    def _visible_event_display_line(event: dict[str, Any]) -> str:
        return engine_public_events.visible_event_display_line(event)

    @staticmethod
    def _visible_event(event: dict[str, Any]) -> dict[str, Any]:
        return engine_public_events.visible_event(event)

    @staticmethod
    def _event_safe_text(event: dict[str, Any]) -> str:
        return engine_public_events.event_safe_text(event)

    @staticmethod
    def _is_public_live_event_displayable(event: dict[str, Any]) -> bool:
        return engine_public_events.is_public_live_event_displayable(event)

    @staticmethod
    def _public_live_event(event: dict[str, Any]) -> dict[str, Any] | None:
        return engine_public_events.public_live_event(event)

    @staticmethod
    def _public_event(event: dict[str, Any]) -> dict[str, Any]:
        return engine_public_events.public_event(event)

    @staticmethod
    def _public_event_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
        return engine_public_events.public_event_metadata(metadata)

    @staticmethod
    def _safe_label_text(label: str) -> str:
        mapping = {
            "suspicious_prompt_injection": "prompt injection 測試",
            "suspicious_secret_request": "祕密/憑證要求",
            "suspicious_url_or_token": "可疑 URL 或 token",
            "spam_or_duplicate": "重複或洗版",
            "unclassified": "尚未通過安全檢查",
            "unsafe_other": "可疑內容",
        }
        return mapping.get(str(label or ""), "可疑內容")

    @staticmethod
    def _topic_pack_context_text(entries: list[dict[str, Any]]) -> str:
        if not entries:
            return ""
        lines = ["", "<topic_pack_fact_cards>"]
        strategy = str(entries[0].get("topic_graph_strategy") or "").strip()
        if strategy:
            lines.append(f"召回策略：{strategy}")
        role_labels = {
            "entry": "入口",
            "detail": "深挖",
            "related": "關聯",
        }
        char_budget = 2200
        for entry in entries[-8:]:
            role = str(entry.get("topic_graph_role") or "").strip()
            label = f"[{role_labels[role]}] " if role in role_labels else ""
            entry_lines = [f"- {label}{entry.get('title')}".strip()]
            entry_lines.extend(YouTubeBridgeManager._topic_pack_evidence_lines(entry))
            next_text = "\n".join([*lines, *entry_lines, "</topic_pack_fact_cards>"])
            if len(next_text) > char_budget and len(lines) > 2:
                break
            if len(next_text) > char_budget:
                entry_lines = entry_lines[:2]
                if len(entry_lines) == 1:
                    entry_lines.append("  - 可驗證事實：資料卡內容過長，請只依標題與本輪問題保守回應。")
            lines.extend(entry_lines)
        lines.append("</topic_pack_fact_cards>")
        return "\n".join(lines)

    @staticmethod
    def _topic_pack_evidence_lines(entry: dict[str, Any]) -> list[str]:
        body = str(entry.get("body") or "").replace("\r", "\n").strip()
        if not body:
            return ["  - 可驗證事實：資料卡未提供可用正文，請只依標題保守回應。"]
        blocked_labels = {
            "正方觀點",
            "反方觀點",
            "第三種觀點",
            "觀眾互動問題",
            "延伸話題",
            "爆點句",
            "資料邊界",
            "可用切角",
            "不可主張",
            "來源提示",
        }
        label_map = {
            "基礎背景": "可驗證事實",
            "背景細節": "可驗證事實",
            "核心進展": "可驗證事實",
            "具體事實": "可驗證事實",
            "summary": "可驗證事實",
            "facts": "可驗證事實",
            "可驗證事實": "可驗證事實",
            "社群討論角度": "網路意見看法",
            "網路意見": "網路意見看法",
            "網路意見看法": "網路意見看法",
            "網路看法": "網路意見看法",
            "公開評論": "網路意見看法",
            "討論氛圍": "網路意見看法",
        }
        buckets: dict[str, list[str]] = {
            "可驗證事實": [],
            "網路意見看法": [],
        }
        for raw_line in body.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("- "):
                line = line[2:].strip()
            label, separator, content = line.partition("：")
            if not separator:
                label, separator, content = line.partition(":")
            clean_label = label.strip()
            clean_content = content.strip() if separator else line
            if clean_label in blocked_labels:
                continue
            mapped = label_map.get(clean_label)
            if not mapped:
                if not any(bad in line for bad in blocked_labels):
                    buckets["可驗證事實"].append(line)
                continue
            if clean_content:
                buckets[mapped].append(clean_content)
        if not any(buckets.values()):
            buckets["可驗證事實"].append(body)
        lines: list[str] = []
        for label in ("可驗證事實", "網路意見看法"):
            values = buckets[label]
            if not values:
                continue
            content = "；".join(values)
            lines.append(f"  - {label}：{YouTubeBridgeManager._truncate_topic_pack_line(content)}")
        return lines[:2] or ["  - 可驗證事實：資料卡未提供可用 evidence 欄位。"]

    @staticmethod
    def _truncate_topic_pack_line(text: str, max_chars: int = 260) -> str:
        compact = " ".join(str(text or "").split())
        if len(compact) <= max_chars:
            return compact
        return compact[: max(0, max_chars - 1)].rstrip() + "…"
