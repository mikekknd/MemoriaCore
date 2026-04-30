"""角色開場白依賴抑制。

此模組只維護短期、記憶體內狀態；不寫入 SQLite、角色檔或人格記憶。
"""
from __future__ import annotations

import json
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque

from core.prompt_manager import get_prompt_manager
from core.system_logger import SystemLogger


OPENING_PENALTY_DEFAULT_ENABLED = True
OPENING_PENALTY_RECENT_LIMIT = 3
OPENING_PENALTY_OPENING_CHARS = 4
OPENING_PENALTY_TTL_SECONDS = 6 * 60 * 60
OPENING_PENALTY_MAX_KEYS = 512
OPENING_PENALTY_LOGIT_BIAS = -12
OPENING_PENALTY_TOKEN_LIMIT = 32

_GROUP_SPEAKER_PREFIX_RE = re.compile(r"^\s*\[[^\]\|\n]+(?:\|[^\]\n]+)?\]:\s*")
_STAGE_DIRECTION_RE = re.compile(r"^\s*[（(][^）)\n]{1,24}[）)]\s*")
_WRAPPER_CHARS = " \t\r\n\"'`「」『』“”‘’"


@dataclass(frozen=True)
class OpeningPenaltyPlan:
    enabled: bool
    key: tuple[str, str, str] | None = None
    blocked_openings: tuple[str, ...] = ()
    prompt_block: str = ""
    logit_bias: dict[str, int] = field(default_factory=dict)


@dataclass
class _OpeningState:
    openings: Deque[str]
    updated_at: float


class OpeningPenaltyManager:
    """追蹤每個角色最近用過的 reply 開頭，並產生動態抑制資訊。"""

    def __init__(
        self,
        *,
        recent_limit: int = OPENING_PENALTY_RECENT_LIMIT,
        opening_chars: int = OPENING_PENALTY_OPENING_CHARS,
        ttl_seconds: int = OPENING_PENALTY_TTL_SECONDS,
        max_keys: int = OPENING_PENALTY_MAX_KEYS,
        bias_value: int = OPENING_PENALTY_LOGIT_BIAS,
    ):
        self.recent_limit = max(1, int(recent_limit))
        self.opening_chars = max(1, int(opening_chars))
        self.ttl_seconds = max(1, int(ttl_seconds))
        self.max_keys = max(1, int(max_keys))
        self.bias_value = int(bias_value)
        self._states: dict[tuple[str, str, str], _OpeningState] = {}
        self._tokenizers: dict[str, object] = {}
        self._tokenizer_failures: dict[str, float] = {}
        self._lock = threading.RLock()

    def clear(self) -> None:
        with self._lock:
            self._states.clear()
            self._tokenizers.clear()
            self._tokenizer_failures.clear()

    def make_key(
        self,
        *,
        session_id: str | None,
        character_id: str | None,
        persona_face: str | None,
    ) -> tuple[str, str, str]:
        return (
            str(session_id or "__unknown_session__"),
            str(character_id or "default"),
            str(persona_face or "public"),
        )

    def extract_opening(self, reply_text: str | None) -> str:
        text = self._strip_opening_wrappers(reply_text)
        if not text:
            return ""
        opening = text[: self.opening_chars].strip()
        for punctuation in ("！", "？", "。", "!", "?"):
            idx = opening.find(punctuation)
            if idx != -1:
                opening = opening[:idx + 1]
                break
        if not opening:
            return ""
        if not any(ch.isalnum() or "\u4e00" <= ch <= "\u9fff" for ch in opening):
            return ""
        return opening

    def record_reply(
        self,
        *,
        session_id: str | None,
        character_id: str | None,
        persona_face: str | None,
        reply_text: str | None,
        enabled: bool = True,
    ) -> str:
        if not enabled:
            return ""
        opening = self.extract_opening(reply_text)
        if not opening:
            return ""
        key = self.make_key(
            session_id=session_id,
            character_id=character_id,
            persona_face=persona_face,
        )
        now = time.time()
        with self._lock:
            self._prune_expired_locked(now)
            state = self._states.get(key)
            if state is None:
                state = _OpeningState(
                    openings=deque(maxlen=self.recent_limit),
                    updated_at=now,
                )
                self._states[key] = state
            state.openings.append(opening)
            state.updated_at = now
            self._prune_overflow_locked()
        return opening

    def get_blocked_openings(
        self,
        *,
        session_id: str | None,
        character_id: str | None,
        persona_face: str | None,
    ) -> tuple[str, ...]:
        key = self.make_key(
            session_id=session_id,
            character_id=character_id,
            persona_face=persona_face,
        )
        now = time.time()
        with self._lock:
            self._prune_expired_locked(now)
            state = self._states.get(key)
            if not state:
                return ()
            seen: set[str] = set()
            ordered: list[str] = []
            for opening in reversed(state.openings):
                if opening and opening not in seen:
                    ordered.append(opening)
                    seen.add(opening)
            return tuple(ordered)

    def build_plan(
        self,
        *,
        session_id: str | None,
        character_id: str | None,
        persona_face: str | None,
        user_prefs: dict | None,
    ) -> OpeningPenaltyPlan:
        prefs = user_prefs or {}
        enabled = bool(prefs.get("opening_penalty_enabled", OPENING_PENALTY_DEFAULT_ENABLED))
        # 沒有 session_id 時無法保證隔離，避免把不同入口的短期狀態混在一起。
        if not enabled or not str(session_id or "").strip():
            return OpeningPenaltyPlan(enabled=False)

        blocked = self.get_blocked_openings(
            session_id=session_id,
            character_id=character_id,
            persona_face=persona_face,
        )
        if not blocked:
            return OpeningPenaltyPlan(
                enabled=True,
                key=self.make_key(
                    session_id=session_id,
                    character_id=character_id,
                    persona_face=persona_face,
                ),
            )

        blocked_json = json.dumps(list(blocked), ensure_ascii=False)
        prompt_block = get_prompt_manager().get("opening_penalty_instruction").format(
            blocked_openings_json=blocked_json,
        )
        tokenizer_ref = str(prefs.get("opening_penalty_tokenizer_ref") or "").strip()
        logit_bias = self.build_logit_bias(blocked, tokenizer_ref)
        return OpeningPenaltyPlan(
            enabled=True,
            key=self.make_key(
                session_id=session_id,
                character_id=character_id,
                persona_face=persona_face,
            ),
            blocked_openings=blocked,
            prompt_block=prompt_block,
            logit_bias=logit_bias,
        )

    def build_retry_instruction(self, plan: OpeningPenaltyPlan, violated_opening: str) -> str:
        blocked_json = json.dumps(list(plan.blocked_openings), ensure_ascii=False)
        return get_prompt_manager().get("opening_penalty_retry").format(
            blocked_openings_json=blocked_json,
            violated_opening=violated_opening,
        )

    def apply_instruction_to_messages(
        self,
        messages: list[dict],
        instruction: str,
    ) -> list[dict]:
        if not instruction:
            return messages
        if messages and messages[-1].get("role") == "user":
            messages[-1] = {
                **messages[-1],
                "content": str(messages[-1].get("content", "")) + "\n\n" + instruction,
            }
            return messages
        messages.append({"role": "user", "content": instruction})
        return messages

    def find_violation(self, reply_text: str | None, plan: OpeningPenaltyPlan) -> str:
        if not plan.enabled or not plan.blocked_openings:
            return ""
        clean = self._strip_opening_wrappers(reply_text)
        if not clean:
            return ""
        for opening in plan.blocked_openings:
            if opening and clean.startswith(opening):
                return opening
        return ""

    def extract_reply_from_response(self, raw_response: str | None) -> str | None:
        if not raw_response:
            return None
        start = raw_response.find("{")
        if start == -1:
            return None
        try:
            parsed, _ = json.JSONDecoder().raw_decode(raw_response, start)
        except Exception:
            return None
        if not isinstance(parsed, dict):
            return None
        reply = parsed.get("reply")
        return reply if isinstance(reply, str) else None

    def build_logit_bias(self, openings: tuple[str, ...], tokenizer_ref: str) -> dict[str, int]:
        if not openings or not tokenizer_ref:
            return {}
        tokenizer = self._get_tokenizer(tokenizer_ref)
        if tokenizer is None:
            return {}

        bias: dict[str, int] = {}
        for opening in openings:
            try:
                token_ids = tokenizer.encode(opening, add_special_tokens=False)
            except Exception as exc:
                SystemLogger.log_error(
                    "OpeningPenalty",
                    f"tokenizer.encode 失敗: {type(exc).__name__}: {exc}",
                    details={"tokenizer_ref": tokenizer_ref, "opening": opening},
                )
                continue
            for token_id in token_ids[:OPENING_PENALTY_TOKEN_LIMIT]:
                try:
                    bias[str(int(token_id))] = self.bias_value
                except (TypeError, ValueError):
                    continue
        return bias

    def _get_tokenizer(self, tokenizer_ref: str):
        now = time.time()
        with self._lock:
            if tokenizer_ref in self._tokenizers:
                return self._tokenizers[tokenizer_ref]
            failed_at = self._tokenizer_failures.get(tokenizer_ref)
            if failed_at and now - failed_at < 60:
                return None

        try:
            from transformers import AutoTokenizer

            tokenizer = AutoTokenizer.from_pretrained(
                tokenizer_ref,
                local_files_only=True,
                trust_remote_code=True,
            )
        except Exception as exc:
            with self._lock:
                self._tokenizer_failures[tokenizer_ref] = now
            SystemLogger.log_error(
                "OpeningPenalty",
                f"載入 tokenizer 失敗，改用 prompt/retry 降級: {type(exc).__name__}: {exc}",
                details={"tokenizer_ref": tokenizer_ref},
            )
            return None

        with self._lock:
            self._tokenizers[tokenizer_ref] = tokenizer
            self._tokenizer_failures.pop(tokenizer_ref, None)
        return tokenizer

    def _strip_opening_wrappers(self, text: str | None) -> str:
        value = str(text or "")
        while True:
            old = value
            value = value.strip(_WRAPPER_CHARS)
            match = _GROUP_SPEAKER_PREFIX_RE.match(value)
            if match:
                value = value[match.end():]
                continue
            stage = _STAGE_DIRECTION_RE.match(value)
            if stage:
                value = value[stage.end():]
                continue
            if value == old:
                return value

    def _prune_expired_locked(self, now: float) -> None:
        expired = [
            key for key, state in self._states.items()
            if now - state.updated_at > self.ttl_seconds
        ]
        for key in expired:
            self._states.pop(key, None)

    def _prune_overflow_locked(self) -> None:
        if len(self._states) <= self.max_keys:
            return
        overflow = len(self._states) - self.max_keys
        oldest = sorted(self._states.items(), key=lambda item: item[1].updated_at)
        for key, _ in oldest[:overflow]:
            self._states.pop(key, None)


_opening_penalty_manager: OpeningPenaltyManager | None = None


def get_opening_penalty_manager() -> OpeningPenaltyManager:
    global _opening_penalty_manager
    if _opening_penalty_manager is None:
        _opening_penalty_manager = OpeningPenaltyManager()
    return _opening_penalty_manager
