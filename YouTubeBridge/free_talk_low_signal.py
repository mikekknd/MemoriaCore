"""Deterministic low-signal filtering for free talk closing."""
from __future__ import annotations

import math
import re


EMOJI_SYMBOL_ONLY_RE = re.compile(r"^[\W_]+$", re.UNICODE)


def classify_low_signal_comment(text: str) -> str:
    value = str(text or "").strip()
    if not value:
        return "empty"
    compact = re.sub(r"\s+", "", value)
    if len(compact) <= 1:
        return "too_short"
    if EMOJI_SYMBOL_ONLY_RE.match(compact):
        return "emoji_or_symbol_only"
    if len(compact) >= 4 and len(set(compact.lower())) <= 2:
        return "repeated_short_token"
    if len(compact) <= 3 and not re.search(r"[\u4e00-\u9fffA-Za-z]", compact):
        return "too_low_information"
    return ""


def free_talk_closing_batch_size(
    eligible_count: int,
    *,
    target_batches: int,
    min_batch_size: int,
    max_batch_size: int,
) -> int:
    count = max(0, int(eligible_count or 0))
    minimum = max(1, int(min_batch_size or 1))
    maximum = max(minimum, int(max_batch_size or minimum))
    if count <= 0:
        return minimum
    target = max(1, int(target_batches or 1))
    return max(minimum, min(math.ceil(count / target), maximum))
