"""記憶分析的對話格式化單元測試（不依賴 LLM）。

驗證 memory_analyzer.process_memory_pipeline / extract_user_facts 與
core_memory.expand_query 在群組對話下會把 assistant 訊息標上 [name|character_id]:
前綴，且接力指令訊息會被清洗。
"""
from unittest.mock import MagicMock


def _make_router_capture():
    """組裝一個 mock router，捕捉每次 generate / generate_json 收到的 prompt。"""
    captured = {"prompts": []}

    def _record(*args, **kwargs):
        # generate(task_key, messages, ...) 與 generate_json(task_key, messages, ...) 同型
        if len(args) >= 2 and isinstance(args[1], list) and args[1]:
            content = args[1][0].get("content", "")
            captured["prompts"].append(content)
        return '{"new_memories": [], "facts": [], "expanded_keywords": [], "entity_confidence": 0.0}'

    router = MagicMock()
    router.generate = MagicMock(side_effect=_record)
    router.generate_json = MagicMock(side_effect=lambda *a, **k: (_record(*a, **k) and None) or {})
    return router, captured


def test_memory_pipeline_dialogue_text_contains_group_labels(monkeypatch):
    """群組對話 messages_to_extract 經 process_memory_pipeline 後，
    dialogue_text 內 assistant 訊息應帶 [name|character_id]: 前綴。"""
    from core.memory_analyzer import MemoryAnalyzer
    from core.prompt_manager import get_prompt_manager

    # 取真實的 prompt 模板（含 {dialogue_text} placeholder）
    pm_real = get_prompt_manager()
    template = pm_real.get("memory_pipeline").format(
        current_time="2026-04-30 12:00",
        last_overview="無",
        dialogue_text="<<DIALOGUE_PLACEHOLDER>>",
    )
    # 替換回去：實際呼叫時 dialogue_text 會被填入；此測試用 mock router 捕捉送進去的字
    router = MagicMock()
    captured_prompt = {"text": ""}

    def _record(task, messages, *args, **kwargs):
        captured_prompt["text"] = messages[0]["content"]
        return '{"new_memories": []}'

    router.generate = MagicMock(side_effect=_record)

    msgs = [
        {"role": "user", "content": "我下午想去吃壽司"},
        {"role": "assistant", "content": "好啊，要約嗎？",
         "character_name": "白蓮", "character_id": "char_lotus"},
        {"role": "assistant", "content": "我也想吃！",
         "character_name": "可可", "character_id": "char_coco"},
    ]

    analyzer = MemoryAnalyzer(memory_sys=MagicMock(embed_provider=None))
    analyzer.process_memory_pipeline(msgs, last_block=None, router=router, embed_model="bge")

    prompt_text = captured_prompt["text"]
    assert "[白蓮|char_lotus]: 好啊，要約嗎？" in prompt_text
    assert "[可可|char_coco]: 我也想吃！" in prompt_text
    # user 訊息保留 user: 前綴
    assert "user: 我下午想去吃壽司" in prompt_text


def test_extract_user_facts_dialogue_text_skips_followup(monkeypatch):
    """extract_user_facts 看到的 dialogue_text 不應包含【群組接力指令】訊息。"""
    from core.memory_analyzer import MemoryAnalyzer

    captured = {"prompt": ""}

    def _record(task, messages, *args, **kwargs):
        captured["prompt"] = messages[0]["content"]
        return {"facts": []}

    router = MagicMock()
    router.generate_json = MagicMock(side_effect=_record)

    msgs = [
        {"role": "user", "content": "我叫小明，住在台中"},
        {"role": "user", "content": "【群組接力指令】\n使用者原始話題：xxx\n上一位..."},
        {"role": "assistant", "content": "你好",
         "character_name": "白蓮", "character_id": "char_lotus"},
    ]

    analyzer = MemoryAnalyzer(memory_sys=MagicMock(embed_provider=None))
    analyzer.extract_user_facts(msgs, current_profile=None, router=router)

    prompt_text = captured["prompt"]
    # 接力指令整則被清洗掉，不出現在 prompt
    assert "【群組接力指令】" not in prompt_text
    # user 真正內容保留
    assert "我叫小明" in prompt_text
    # assistant 帶群組標籤（單一 character_id 也不一定有標籤，看 _is_group_session 判定）
    # 這裡只有一個 character_id，所以不加群組標籤；但內容仍應保留
    assert "你好" in prompt_text


def test_expand_query_history_text_strips_ref_tag(monkeypatch):
    """expand_query 的 history_text 不應含 [Ref: uid]（被 sanitize 移除）。"""
    from core.core_memory import MemorySystem

    captured = {"prompt": ""}

    def _record(task, messages, *args, **kwargs):
        captured["prompt"] = messages[0]["content"]
        return {"expanded_keywords": [], "entity_confidence": 0.0}

    router = MagicMock()
    router.generate_json = MagicMock(side_effect=_record)

    # 用最小化 instance 避免初始化所有依賴
    ms = MemorySystem.__new__(MemorySystem)

    history = [
        {"role": "user", "content": "上次說的"},
        {"role": "assistant", "content": "記得 [Ref: uid-old-record]"},
        {"role": "user", "content": "比特幣"},
    ]
    ms.expand_query("比特幣", history, router)

    prompt_text = captured["prompt"]
    assert "[Ref:" not in prompt_text
    assert "記得" in prompt_text
