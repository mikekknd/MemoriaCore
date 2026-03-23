"""話題偏移偵測測試：驗證 detect_topic_shift 的各種邊界情境"""
import pytest
from tests.test_config import requires_ollama


@requires_ollama
class TestTopicShiftDetection:

    def test_same_topic_no_shift(self, analyzer, memory_system):
        """同一主題的連續對話不應觸發話題偏移"""
        messages = [
            {"role": "user", "content": "我最近在學 Python 烹飪食譜管理的程式"},
            {"role": "assistant", "content": "用 Python 來管理食譜是個很棒的專案，你可以用字典或資料庫來存放食材和步驟。"},
            {"role": "user", "content": "對，我想用 Python 把食譜的食材清單自動整理分類"},
            {"role": "assistant", "content": "你可以用 Python 的字典將食材按類別分組，例如肉類、蔬菜、調味料等。"},
            {"role": "user", "content": "那如果食譜的食材有重複的話，Python 程式要怎麼自動去重呢？"},
        ]
        is_shift, score = analyzer.detect_topic_shift(messages, memory_system.embed_model)
        assert not is_shift, f"同主題對話不應觸發偏移，但 score={score:.3f}"
        assert score > 0.3

    def test_abrupt_topic_change_triggers_shift(self, analyzer, memory_system):
        """突然從一個主題跳到完全無關的主題應觸發偏移"""
        messages = [
            {"role": "user", "content": "今天晚餐去吃了一家日式拉麵店"},
            {"role": "assistant", "content": "聽起來不錯！是什麼口味的拉麵呢？"},
            {"role": "user", "content": "豚骨白湯的，湯頭非常濃郁好喝"},
            {"role": "assistant", "content": "豚骨拉麵確實是經典口味，配上溏心蛋更完美。"},
            {"role": "user", "content": "對了，你覺得比特幣最近會漲還是跌？我在考慮要不要入場"},
        ]
        is_shift, score = analyzer.detect_topic_shift(messages, memory_system.embed_model)
        assert is_shift, f"突兀的主題切換應觸發偏移，但 score={score:.3f}"

    def test_qa_exemption_relaxes_threshold(self, analyzer, memory_system):
        """AI 提問後使用者的簡短回答不應觸發偏移（QA 豁免機制）"""
        messages = [
            {"role": "user", "content": "我想學彈吉他，但不知道要買木吉他還是電吉他"},
            {"role": "assistant", "content": "這取決於你想彈的音樂風格。你比較喜歡民謠還是搖滾？"},
            {"role": "user", "content": "我想彈民謠吧"},
            {"role": "assistant", "content": "那木吉他會是比較好的選擇。你有預算上的考量嗎？"},
            {"role": "user", "content": "大概五千塊左右"},
        ]
        is_shift, score = analyzer.detect_topic_shift(messages, memory_system.embed_model)
        assert not is_shift, f"QA 豁免應生效，但偵測到偏移 score={score:.3f}"

    def test_forced_cutoff_on_max_history(self, analyzer, memory_system):
        """對話超過 max_history_len 時強制切斷"""
        messages = [{"role": "user" if i % 2 == 0 else "assistant",
                      "content": f"這是第 {i} 則訊息，我們在討論測試框架的設計。"}
                     for i in range(21)]
        is_shift, score = analyzer.detect_topic_shift(messages, memory_system.embed_model)
        assert is_shift
        assert score == -1.0

    def test_short_history_never_triggers(self, analyzer, memory_system):
        """對話太短（< min_history_len）永不觸發偏移"""
        messages = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好！有什麼我可以幫助你的嗎？"},
            {"role": "user", "content": "我想問一個關於量子力學的問題"},
        ]
        is_shift, score = analyzer.detect_topic_shift(messages, memory_system.embed_model)
        assert not is_shift
        assert score == 1.0

    def test_last_message_not_user_no_shift(self, analyzer, memory_system):
        """最後一句不是 user 發言時不觸發偏移"""
        messages = [
            {"role": "user", "content": "今天天氣真好"},
            {"role": "assistant", "content": "是啊，適合出去走走"},
            {"role": "user", "content": "我想去公園跑步"},
            {"role": "assistant", "content": "運動對身體很好，記得暖身"},
            {"role": "user", "content": "好的謝謝"},
            {"role": "assistant", "content": "不客氣，祝你運動愉快！"},
        ]
        is_shift, score = analyzer.detect_topic_shift(messages, memory_system.embed_model)
        assert not is_shift
        assert score == 1.0
