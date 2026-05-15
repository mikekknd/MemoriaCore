import sys
from pathlib import Path


BRIDGE_ROOT = Path(__file__).resolve().parents[1]
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))

from free_talk_low_signal import classify_low_signal_comment, free_talk_closing_batch_size


def test_classify_low_signal_comment_filters_empty_short_symbols_and_repeated_noise():
    assert classify_low_signal_comment("") == "empty"
    assert classify_low_signal_comment("  ") == "empty"
    assert classify_low_signal_comment("哈") == "too_short"
    assert classify_low_signal_comment("😂😂😂😂😂") == "emoji_or_symbol_only"
    assert classify_low_signal_comment("？！？？") == "emoji_or_symbol_only"
    assert classify_low_signal_comment("666666") == "repeated_short_token"
    assert classify_low_signal_comment("wwwwwwww") == "repeated_short_token"


def test_classify_low_signal_comment_preserves_normal_chinese_questions():
    assert classify_low_signal_comment("這個工具適合團隊共用嗎？") == ""
    assert classify_low_signal_comment("可以補充實際案例嗎？") == ""


def test_free_talk_closing_batch_size_uses_target_batches_with_clamp():
    assert free_talk_closing_batch_size(20, target_batches=10, min_batch_size=5, max_batch_size=30) == 5
    assert free_talk_closing_batch_size(80, target_batches=10, min_batch_size=5, max_batch_size=30) == 8
    assert free_talk_closing_batch_size(500, target_batches=10, min_batch_size=5, max_batch_size=30) == 30
    assert free_talk_closing_batch_size(0, target_batches=10, min_batch_size=5, max_batch_size=30) == 5
