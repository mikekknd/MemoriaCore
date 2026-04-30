"""對話歷史格式化工具（向後相容包裝層）。

實作已移至 dialogue_format.py — 集中清洗、群組標籤、citation metadata 讀取。
保留此模組是為了維持舊 import 路徑不破壞。
"""
from core.chat_orchestrator.dialogue_format import (
    format_history_for_llm,
    speaker_label as _speaker_label,
)

__all__ = ["format_history_for_llm", "_speaker_label"]
