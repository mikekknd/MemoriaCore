"""YouTube Live Chat 摘要流程。"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from memoria_client import MemoriaClient
from storage import BridgeStorage


SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "overview": {"type": "string"},
        "topics": {"type": "array", "items": {"type": "string"}},
        "key_points": {"type": "array", "items": {"type": "string"}},
        "qa_pairs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "answer": {"type": "string"},
                },
            },
        },
        "audience_mood": {"type": "string"},
        "memory_text": {"type": "string"},
    },
}

SAFE_MEMORY_SCHEMA = {
    "type": "object",
    "properties": {
        "memory_text": {"type": "string"},
    },
}

CHUNK_SCHEMA = {
    "type": "object",
    "properties": {
        "overview": {"type": "string"},
        "topics": {"type": "array", "items": {"type": "string"}},
        "key_points": {"type": "array", "items": {"type": "string"}},
        "qa_pairs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "answer": {"type": "string"},
                },
            },
        },
        "audience_mood": {"type": "string"},
    },
}


class YouTubeLiveSummaryManager:
    def __init__(self, storage: BridgeStorage, memoria_client: MemoriaClient | None = None):
        self.storage = storage
        self.memoria_client = memoria_client or MemoriaClient()

    def summarize_session(
        self,
        session_id: str,
        *,
        force: bool = False,
        min_events: int = 1,
        max_events: int = 1000,
        chunk_size: int = 120,
        include_memoria_session: bool = True,
        safe_memory_text: bool = True,
    ) -> dict[str, Any]:
        session = self.storage.get_session(session_id)
        if not session:
            raise ValueError("live session 不存在")

        existing = self.storage.get_session_summary(session_id)
        if existing and session.get("summary_status") == "completed" and not force:
            return {"status": "completed", "reused": True, "summary": existing}

        finalized_at = session.get("finalized_at") or datetime.now().isoformat()
        self.storage.update_session_summary_state(
            session_id,
            summary_status="summarizing",
            summary_error="",
            finalized_at=finalized_at,
        )

        try:
            result = self._summarize_session_inner(
                session,
                min_events=max(1, int(min_events or 1)),
                max_events=max(1, min(int(max_events or 1000), 5000)),
                chunk_size=max(20, min(int(chunk_size or 120), 500)),
                include_memoria_session=include_memoria_session,
                safe_memory_text=safe_memory_text,
                finalized_at=finalized_at,
            )
            return result
        except Exception as exc:
            self.storage.update_session_summary_state(
                session_id,
                summary_status="failed",
                summary_error=str(exc),
                finalized_at=finalized_at,
            )
            raise

    def _summarize_session_inner(
        self,
        session: dict[str, Any],
        *,
        min_events: int,
        max_events: int,
        chunk_size: int,
        include_memoria_session: bool,
        safe_memory_text: bool,
        finalized_at: str,
    ) -> dict[str, Any]:
        session_id = session["session_id"]
        events = self.storage.list_summary_events(session_id, limit=max_events)
        event_count = len(events)
        if event_count < min_events:
            self.storage.update_session_summary_state(
                session_id,
                summary_status="skipped",
                summary_error=f"可摘要留言數不足：{event_count} < {min_events}",
                finalized_at=finalized_at,
            )
            return {
                "status": "skipped",
                "event_count": event_count,
                "min_events": min_events,
            }

        source_started_at = self._event_time(events[0])
        source_ended_at = finalized_at or self._event_time(events[-1])
        lines = self._event_lines(events)
        if len(lines) > max_events:
            lines = lines[:max_events]
        chunks = [lines[i:i + chunk_size] for i in range(0, len(lines), chunk_size)]

        if len(chunks) == 1:
            summary_source = "\n".join(chunks[0])
            chunk_count = 1
        else:
            chunk_summaries = [
                self._summarize_chunk(
                    session=session,
                    lines=chunk,
                    chunk_index=index + 1,
                    chunk_count=len(chunks),
                )
                for index, chunk in enumerate(chunks)
            ]
            summary_source = json.dumps(chunk_summaries, ensure_ascii=False, indent=2)
            chunk_count = len(chunks)

        interactions = self.storage.list_interactions(session_id, limit=300)
        interaction_lines = self._interaction_lines(interactions)
        memoria_lines = self._memoria_session_lines(session) if include_memoria_session else []
        full_source = self._summary_source(
            chat_source=summary_source,
            interaction_lines=interaction_lines,
            memoria_lines=memoria_lines,
        )

        final = self.memoria_client.generate_prompt_json(
            prompt_key="youtube_live_interaction_summary_prompt",
            variables={
                "session_title": session.get("display_name") or session_id,
                "video_id": session.get("video_id", ""),
                "event_count": str(event_count),
                "summary_source": full_source,
            },
            task_key="compress",
            temperature=0.1,
            schema=SUMMARY_SCHEMA,
        )
        normalized = self._normalize_final_summary(final, session=session, event_count=event_count)
        normalized["qa_pairs"] = self._sanitize_qa_pairs(normalized.get("qa_pairs", []))
        if safe_memory_text:
            normalized["memory_text"] = self._safe_memory_text(
                session=session,
                summary=normalized,
                summary_source=full_source,
            )
        normalized["memory_text"] = self._sanitize_memory_text(
            normalized.get("memory_text", ""),
            source_text=full_source,
        )
        summary = self.storage.create_summary(
            session_id,
            {
                **normalized,
                "event_count": event_count,
                "source_started_at": source_started_at,
                "source_ended_at": source_ended_at,
                "status": "completed",
                "metadata": {
                    "max_events": max_events,
                    "chunk_size": chunk_size,
                    "chunk_count": chunk_count,
                    "include_memoria_session": include_memoria_session,
                    "safe_memory_text": safe_memory_text,
                    "interaction_count": len(interactions),
                    "memoria_message_count": len(memoria_lines),
                    "truncated": self.storage.count_events(session_id, active_only=True) > event_count,
                    "memory_write_status": "not_started",
                    "cleanup_required": True,
                },
            },
        )
        self.storage.update_session_summary_state(
            session_id,
            summary_status="completed",
            summary_id=summary["id"],
            summary_error="",
            finalized_at=finalized_at,
        )
        return {"status": "completed", "reused": False, "summary": summary}

    def _summarize_chunk(
        self,
        *,
        session: dict[str, Any],
        lines: list[str],
        chunk_index: int,
        chunk_count: int,
    ) -> dict[str, Any]:
        result = self.memoria_client.generate_prompt_json(
            prompt_key="youtube_live_chunk_summary_prompt",
            variables={
                "session_title": session.get("display_name") or session["session_id"],
                "video_id": session.get("video_id", ""),
                "chunk_index": str(chunk_index),
                "chunk_count": str(chunk_count),
                "event_count": str(len(lines)),
                "chat_lines": "\n".join(lines),
            },
            task_key="compress",
            temperature=0.1,
            schema=CHUNK_SCHEMA,
        )
        return self._normalize_chunk_summary(result)

    @staticmethod
    def _truncate(text: str, limit: int = 800) -> str:
        text = str(text or "").replace("\r", " ").replace("\n", " ").strip()
        if len(text) <= limit:
            return text
        return text[:limit].rstrip() + "..."

    @classmethod
    def _interaction_lines(cls, interactions: list[dict[str, Any]]) -> list[str]:
        lines: list[str] = []
        for item in reversed(interactions):
            event_ids = item.get("event_ids") if isinstance(item.get("event_ids"), list) else []
            status = str(item.get("status") or "")
            source = str(item.get("source") or "interaction")
            reply = cls._truncate(str(item.get("reply_text") or ""), 500)
            closure = cls._truncate(str(item.get("closure_text") or ""), 240)
            if not reply and not closure:
                continue
            ids_text = ",".join(str(x) for x in event_ids[:20]) if event_ids else "none"
            answer = reply or closure
            lines.append(f"- {source} [{status}] events={ids_text} -> AI: {answer}")
        return lines

    def _memoria_session_lines(self, session: dict[str, Any]) -> list[str]:
        memoria_session_id = str(session.get("target_memoria_session_id") or "").strip()
        if not memoria_session_id:
            return []
        try:
            messages = self.memoria_client.get_session_messages(memoria_session_id)
        except Exception:
            return []
        lines: list[str] = []
        for message in messages[-300:]:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "").strip()
            content = self._truncate(str(message.get("content") or ""), 700)
            if not role or not content:
                continue
            if role == "system_event":
                role_label = "YouTube 注入"
            elif role == "assistant":
                name = str(message.get("character_name") or message.get("character_id") or "AI").strip()
                role_label = f"assistant:{name}"
            else:
                role_label = role
            lines.append(f"- {role_label}: {content}")
        return lines

    @staticmethod
    def _summary_source(
        *,
        chat_source: str,
        interaction_lines: list[str],
        memoria_lines: list[str],
    ) -> str:
        sections = [
            "【YouTube raw chat events（已移除時間、channel id、message type）】",
            chat_source.strip() or "（無）",
            "",
            "【Injection ledger：留言批次與 AI 回應關係】",
            "\n".join(interaction_lines) if interaction_lines else "（無）",
            "",
            "【目標 MemoriaCore session 對話紀錄】",
            "\n".join(memoria_lines) if memoria_lines else "（無）",
        ]
        return "\n".join(sections)

    def _safe_memory_text(
        self,
        *,
        session: dict[str, Any],
        summary: dict[str, Any],
        summary_source: str,
    ) -> str:
        result = self.memoria_client.generate_prompt_json(
            prompt_key="youtube_live_safe_memory_text_prompt",
            variables={
                "session_title": session.get("display_name") or session["session_id"],
                "video_id": session.get("video_id", ""),
                "summary_json": json.dumps(summary, ensure_ascii=False, indent=2),
                "summary_source": summary_source,
            },
            task_key="compress",
            temperature=0.0,
            schema=SAFE_MEMORY_SCHEMA,
        )
        return str(result.get("memory_text") or summary.get("memory_text") or "").strip()

    @staticmethod
    def _event_time(event: dict[str, Any]) -> str:
        return str(event.get("published_at") or event.get("received_at") or "")

    @classmethod
    def _event_lines(cls, events: list[dict[str, Any]]) -> list[str]:
        lines: list[str] = []
        seen: set[tuple[str, str]] = set()
        for event in events:
            author = str(event.get("author_display_name") or "匿名觀眾").strip() or "匿名觀眾"
            text = str(event.get("message_text") or "").replace("\r", " ").replace("\n", " ").strip()
            if not text:
                continue
            if event.get("priority_class") == "super_chat":
                amount = str(event.get("amount_display_string") or "SC").strip()
                safety_label = str(event.get("safety_label") or "clean")
                if safety_label != "clean":
                    text = "已收到一則可疑 SC，直播中安全處理，未執行其中指令。"
                else:
                    text = f"[{amount}] {text}"
            dedupe_key = (author, text)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            lines.append(f"- {author}: {text}")
        return lines

    @staticmethod
    def _string_list(value: Any, *, limit: int = 20) -> list[str]:
        if not isinstance(value, list):
            return []
        items: list[str] = []
        for item in value[:limit]:
            text = str(item or "").strip()
            if text:
                items.append(text)
        return items

    @classmethod
    def _qa_pairs(cls, value: Any, *, limit: int = 20) -> list[dict[str, str]]:
        if not isinstance(value, list):
            return []
        pairs: list[dict[str, str]] = []
        for item in value[:limit]:
            if not isinstance(item, dict):
                continue
            question = str(item.get("question") or "").strip()
            answer = str(item.get("answer") or "").strip()
            if question or answer:
                pairs.append({"question": question, "answer": answer})
        return pairs

    @staticmethod
    def _looks_like_prompt_injection(text: str) -> bool:
        lowered = text.lower()
        patterns = [
            "prompt injection",
            "system prompt", "ignore previous", "ignore all", "developer message",
            "api key", "token", "輸出系統", "系統提示", "忽略前面", "忽略以上",
            "洩漏", "金鑰", "密鑰",
        ]
        return any(pattern in lowered for pattern in patterns)

    @classmethod
    def _sanitize_qa_pairs(cls, pairs: list[dict[str, str]]) -> list[dict[str, str]]:
        sanitized: list[dict[str, str]] = []
        for pair in pairs:
            question = str(pair.get("question") or "").strip()
            answer = str(pair.get("answer") or "").strip()
            if cls._looks_like_prompt_injection(question):
                question = "聊天室出現 prompt injection 測試"
            if cls._looks_like_prompt_injection(answer):
                answer = "AI 將其視為不可信外部留言，未執行其中指令。"
            sanitized.append({"question": question, "answer": answer})
        return sanitized

    @classmethod
    def _sanitize_memory_text(cls, text: str, *, source_text: str = "") -> str:
        text = str(text or "").strip()
        if not text:
            return ""
        source_has_prompt_injection = cls._looks_like_prompt_injection(source_text)
        text = re.sub(r"UC[A-Za-z0-9_-]{12,}", "[已移除 channel id]", text)
        text = re.sub(r"(?i)(api[_ -]?key|token|bearer)\s*[:=]\s*\S+", "[已移除敏感資訊]", text)
        text = re.sub(r"\b\d{4}-\d{2}-\d{2}T[^\s，。]+", "[已移除 timestamp]", text)
        if cls._looks_like_prompt_injection(text):
            text = re.sub(r"(?i)(ignore previous|ignore all|system prompt|developer message)[^。！？\n]*", "聊天室出現 prompt injection 測試", text)
            text = re.sub(r"(輸出系統提示|系統提示|忽略前面|忽略以上)[^。！？\n]*", "聊天室出現 prompt injection 測試", text)
            text = re.sub(r"prompt injection\s*(指令|要求|內容|嘗試)", "prompt injection 測試", text, flags=re.IGNORECASE)
            if source_has_prompt_injection and "聊天室出現 prompt injection 測試" not in text:
                text = text.rstrip("。") + "。聊天室出現 prompt injection 測試，AI 未執行其指令。"
        generic_pattern = r"聊天室出現\s*prompt injection\s*測試[，,、 ]*AI\s*未執行其指令[。.]?"
        if not source_has_prompt_injection:
            text = re.sub(generic_pattern, "", text, flags=re.IGNORECASE)
            text = re.sub(r"聊天室出現\s*prompt injection\s*測試[。.]?", "", text, flags=re.IGNORECASE)
        else:
            seen_generic = False

            def _dedupe_generic(match: re.Match) -> str:
                nonlocal seen_generic
                if seen_generic:
                    return ""
                seen_generic = True
                return "聊天室出現 prompt injection 測試，AI 未執行其指令。"

            text = re.sub(generic_pattern, _dedupe_generic, text, flags=re.IGNORECASE)
        text = re.sub(r"\s+", " ", text).strip()
        text = re.sub(r"\s*。", "。", text)
        return text[:1200].strip()

    @classmethod
    def _normalize_chunk_summary(cls, data: dict[str, Any]) -> dict[str, Any]:
        return {
            "overview": str(data.get("overview") or "").strip(),
            "topics": cls._string_list(data.get("topics"), limit=12),
            "key_points": cls._string_list(data.get("key_points"), limit=12),
            "qa_pairs": cls._qa_pairs(data.get("qa_pairs"), limit=12),
            "audience_mood": str(data.get("audience_mood") or "").strip(),
        }

    @classmethod
    def _normalize_final_summary(
        cls,
        data: dict[str, Any],
        *,
        session: dict[str, Any],
        event_count: int,
    ) -> dict[str, Any]:
        title = str(data.get("title") or session.get("display_name") or "YouTube Live 摘要").strip()
        overview = str(data.get("overview") or "").strip()
        memory_text = str(data.get("memory_text") or "").strip()
        if not overview:
            overview = f"這場 YouTube 直播聊天室共整理 {event_count} 則留言，摘要內容不足，僅保存為概略直播脈絡。"
        if not memory_text:
            memory_text = f"這是 YouTube 直播聊天室脈絡摘要：{overview}"
        return {
            "title": title,
            "summary_text": overview,
            "topic_tags": cls._string_list(data.get("topics"), limit=20),
            "key_points": cls._string_list(data.get("key_points"), limit=30),
            "qa_pairs": cls._qa_pairs(data.get("qa_pairs"), limit=30),
            "audience_mood": str(data.get("audience_mood") or "").strip(),
            "memory_text": memory_text,
        }
