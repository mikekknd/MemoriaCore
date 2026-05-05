"""YouTubeBridge event classification helpers。"""
from __future__ import annotations


def classify_live_event_safety(text: str) -> str:
    """舊 API 相容用：安全分類改由 SafetyLLM 負責。"""
    return "unclassified" if str(text or "").strip() else "clean"


def infer_super_chat_tier(amount_micros: int, explicit_tier: int = 0) -> int:
    if explicit_tier > 0:
        return min(explicit_tier, 10)
    amount = max(0, int(amount_micros or 0)) / 1_000_000
    if amount >= 3000:
        return 5
    if amount >= 1500:
        return 4
    if amount >= 750:
        return 3
    if amount >= 150:
        return 2
    if amount > 0:
        return 1
    return 0
