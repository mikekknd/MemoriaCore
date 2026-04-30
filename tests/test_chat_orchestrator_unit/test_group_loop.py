"""群組對話 loop 測試。"""
import pytest

from api.session_manager import SessionState, session_manager
from core.chat_orchestrator.dataclasses import GroupRouterResult, SharedToolState


@pytest.mark.asyncio
async def test_group_loop_passes_target_character_id(monkeypatch):
    from api.routers.chat import group_loop

    session = SessionState(
        session_id="sid-group",
        messages=[{"role": "user", "content": "聊聊這件事"}],
        user_id="user-1",
        character_id="char-a",
        active_character_ids=["char-a", "char-b"],
        session_mode="group",
        persona_face="public",
        channel="dashboard",
    )
    session_manager._sessions["sid-group"] = session

    class FakeCharacterManager:
        def get_character(self, character_id):
            return {
                "character_id": character_id,
                "name": "角色A" if character_id == "char-a" else "角色B",
                "system_prompt": "測試角色",
                "tts_language": "",
                "tts_rules": "",
            }

    route_results = [
        GroupRouterResult(True, "char-a", "first"),
        GroupRouterResult(True, "char-b", "second"),
        GroupRouterResult(False, None, "done"),
    ]

    def fake_group_router(*args, **kwargs):
        return route_results.pop(0)

    captured_character_ids = []

    def fake_orchestration(*args, **kwargs):
        captured_character_ids.append(kwargs["session_ctx"]["character_id"])
        return (
            f"回覆 {kwargs['session_ctx']['character_id']}",
            [],
            {},
            False,
            None,
            "內在想法",
            None,
            None,
            None,
            "",
            [],
        )

    monkeypatch.setattr(group_loop, "get_character_manager", lambda: FakeCharacterManager())
    monkeypatch.setattr(group_loop, "get_router", lambda: object())
    monkeypatch.setattr(group_loop, "run_group_router", fake_group_router)

    try:
        turns = await group_loop.run_group_chat_loop(
            session=session,
            user_prompt="聊聊這件事",
            user_prefs={"group_chat_max_bot_turns": 3, "group_chat_turn_delay_seconds": 0},
            orchestration_fn=fake_orchestration,
        )
    finally:
        session_manager._sessions.clear()

    assert captured_character_ids == ["char-a", "char-b"]
    assert [t["character_id"] for t in turns] == ["char-a", "char-b"]
    assert turns[-1]["is_final"] is True
    assert session.messages[-2]["character_id"] == "char-a"
    assert session.messages[-1]["character_id"] == "char-b"


@pytest.mark.asyncio
async def test_group_loop_emits_each_turn_before_next_route(monkeypatch):
    from api.routers.chat import group_loop

    session = SessionState(
        session_id="sid-group-stream",
        messages=[{"role": "user", "content": "依序聊"}],
        user_id="user-1",
        character_id="char-a",
        active_character_ids=["char-a", "char-b"],
        session_mode="group",
        persona_face="public",
        channel="dashboard",
    )
    session_manager._sessions["sid-group-stream"] = session

    class FakeCharacterManager:
        def get_character(self, character_id):
            return {
                "character_id": character_id,
                "name": character_id,
                "system_prompt": "測試角色",
                "tts_language": "",
                "tts_rules": "",
            }

    emitted_turn_ids = []
    route_calls = []

    route_results = [
        GroupRouterResult(True, "char-a", "first"),
        GroupRouterResult(True, "char-b", "second"),
    ]

    def fake_group_router(*args, **kwargs):
        route_calls.append(list(emitted_turn_ids))
        return route_results.pop(0)

    def fake_orchestration(*args, **kwargs):
        character_id = kwargs["session_ctx"]["character_id"]
        return (
            f"回覆 {character_id}",
            [],
            {},
            False,
            None,
            "",
            None,
            None,
            None,
            "",
            [],
        )

    async def fake_sleep(_seconds):
        return None

    async def on_turn(turn):
        emitted_turn_ids.append(turn["character_id"])

    monkeypatch.setattr(group_loop, "get_character_manager", lambda: FakeCharacterManager())
    monkeypatch.setattr(group_loop, "get_router", lambda: object())
    monkeypatch.setattr(group_loop, "run_group_router", fake_group_router)
    monkeypatch.setattr(group_loop.asyncio, "sleep", fake_sleep)

    try:
        await group_loop.run_group_chat_loop(
            session=session,
            user_prompt="依序聊",
            user_prefs={"group_chat_max_bot_turns": 2, "group_chat_turn_delay_seconds": 0},
            orchestration_fn=fake_orchestration,
            on_turn=on_turn,
        )
    finally:
        session_manager._sessions.clear()

    assert emitted_turn_ids == ["char-a", "char-b"]
    assert route_calls == [[], ["char-a"]]


@pytest.mark.asyncio
async def test_group_loop_uses_configured_turn_delay(monkeypatch):
    from api.routers.chat import group_loop

    session = SessionState(
        session_id="sid-group-delay",
        messages=[{"role": "user", "content": "延遲測試"}],
        user_id="user-1",
        character_id="char-a",
        active_character_ids=["char-a", "char-b"],
        session_mode="group",
        persona_face="public",
        channel="dashboard",
    )
    session_manager._sessions["sid-group-delay"] = session

    class FakeCharacterManager:
        def get_character(self, character_id):
            return {
                "character_id": character_id,
                "name": character_id,
                "system_prompt": "測試角色",
                "tts_language": "",
                "tts_rules": "",
            }

    route_results = [
        GroupRouterResult(True, "char-a", "first"),
        GroupRouterResult(True, "char-b", "second"),
    ]

    def fake_group_router(*args, **kwargs):
        return route_results.pop(0)

    def fake_orchestration(*args, **kwargs):
        character_id = kwargs["session_ctx"]["character_id"]
        return (
            f"回覆 {character_id}",
            [],
            {},
            False,
            None,
            "",
            None,
            None,
            None,
            "",
            [],
        )

    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(group_loop, "get_character_manager", lambda: FakeCharacterManager())
    monkeypatch.setattr(group_loop, "get_router", lambda: object())
    monkeypatch.setattr(group_loop, "run_group_router", fake_group_router)
    monkeypatch.setattr(group_loop.asyncio, "sleep", fake_sleep)

    try:
        await group_loop.run_group_chat_loop(
            session=session,
            user_prompt="延遲測試",
            user_prefs={"group_chat_max_bot_turns": 2, "group_chat_turn_delay_seconds": 1.5},
            orchestration_fn=fake_orchestration,
        )
    finally:
        session_manager._sessions.clear()

    assert slept == [1.5]


@pytest.mark.asyncio
async def test_group_loop_emits_typing_event_before_orchestration(monkeypatch):
    from api.routers.chat import group_loop

    session = SessionState(
        session_id="sid-group-typing",
        messages=[{"role": "user", "content": "誰要回？"}],
        user_id="user-1",
        character_id="char-a",
        active_character_ids=["char-a", "char-b"],
        session_mode="group",
        persona_face="public",
        channel="dashboard",
    )
    session_manager._sessions["sid-group-typing"] = session

    class FakeCharacterManager:
        def get_character(self, character_id):
            return {
                "character_id": character_id,
                "name": "角色A",
                "system_prompt": "測試角色",
                "tts_language": "",
                "tts_rules": "",
            }

    events = []

    def fake_group_router(*args, **kwargs):
        return GroupRouterResult(True, "char-a", "first")

    def fake_orchestration(*args, **kwargs):
        assert events[0]["type"] == "typing"
        assert events[0]["character_id"] == "char-a"
        return (
            "回覆 char-a",
            [],
            {},
            False,
            None,
            "",
            None,
            None,
            None,
            "",
            [],
        )

    monkeypatch.setattr(group_loop, "get_character_manager", lambda: FakeCharacterManager())
    monkeypatch.setattr(group_loop, "get_router", lambda: object())
    monkeypatch.setattr(group_loop, "run_group_router", fake_group_router)

    try:
        await group_loop.run_group_chat_loop(
            session=session,
            user_prompt="誰要回？",
            user_prefs={"group_chat_max_bot_turns": 1},
            orchestration_fn=fake_orchestration,
            on_event=events.append,
        )
    finally:
        session_manager._sessions.clear()

    assert events == [{
        "type": "typing",
        "session_id": "sid-group-typing",
        "turn_index": 0,
        "character_id": "char-a",
        "character_name": "角色A",
    }]


@pytest.mark.asyncio
async def test_group_loop_shares_tool_state_across_turns(monkeypatch):
    """群組接力：turn 0 的工具結果應由 session_ctx 注入後續 turn，避免重複呼叫。"""
    from api.routers.chat import group_loop

    session = SessionState(
        session_id="sid-tool-share",
        messages=[{"role": "user", "content": "外面下大雨"}],
        user_id="user-1",
        character_id="char-a",
        active_character_ids=["char-a", "char-b"],
        session_mode="group",
        persona_face="public",
        channel="dashboard",
    )
    session_manager._sessions["sid-tool-share"] = session

    class FakeCharacterManager:
        def get_character(self, character_id):
            return {
                "character_id": character_id,
                "name": character_id,
                "system_prompt": "測試角色",
                "tts_language": "",
                "tts_rules": "",
            }

    route_results = [
        GroupRouterResult(True, "char-a", "first"),
        GroupRouterResult(True, "char-b", "second"),
        GroupRouterResult(False, None, "done"),
    ]

    def fake_group_router(*args, **kwargs):
        return route_results.pop(0)

    captured_shared_states = []
    captured_followups = []

    def fake_orchestration(*args, **kwargs):
        ctx = kwargs["session_ctx"]
        captured_shared_states.append(ctx.get("shared_tool_state"))
        captured_followups.append(ctx.get("followup_instruction"))
        # turn 0 export 一個 executed=True 的 SharedToolState；turn 1 export 空 state
        export = SharedToolState(
            tool_results=[{"tool_name": "get_weather", "result": "晴 25°C"}],
            tool_results_formatted="[get_weather] 晴 25°C",
            thinking_speech_sent="稍等。",
            executed=True,
        )
        return (
            f"回覆 {ctx['character_id']}",
            [], {}, False, None,
            "想法", None, None, None,
            "", [], export,
        )

    async def fake_sleep(_seconds):
        return None

    monkeypatch.setattr(group_loop, "get_character_manager", lambda: FakeCharacterManager())
    monkeypatch.setattr(group_loop, "get_router", lambda: object())
    monkeypatch.setattr(group_loop, "run_group_router", fake_group_router)
    monkeypatch.setattr(group_loop.asyncio, "sleep", fake_sleep)

    try:
        await group_loop.run_group_chat_loop(
            session=session,
            user_prompt="外面下大雨",
            user_prefs={"group_chat_max_bot_turns": 3, "group_chat_turn_delay_seconds": 0},
            orchestration_fn=fake_orchestration,
        )
    finally:
        session_manager._sessions.clear()

    # turn 0 收到 None；turn 1 收到 turn 0 export 的 SharedToolState
    assert captured_shared_states[0] is None
    assert isinstance(captured_shared_states[1], SharedToolState)
    assert captured_shared_states[1].executed is True
    assert captured_shared_states[1].tool_results[0]["tool_name"] == "get_weather"

    # turn 0 沒 follow-up；turn 1+ 帶 follow-up dict
    assert captured_followups[0] is None
    assert captured_followups[1] is not None
    assert captured_followups[1]["last_character_name"] == "char-a"
    assert "user_prompt_original" in captured_followups[1]


@pytest.mark.asyncio
async def test_group_loop_does_not_inject_followup_into_messages(monkeypatch):
    """接力指令文字不應出現在 generation_messages（避免污染 expand/pipeline）。"""
    from api.routers.chat import group_loop

    session = SessionState(
        session_id="sid-no-followup-msg",
        messages=[{"role": "user", "content": "原始問題"}],
        user_id="user-1",
        character_id="char-a",
        active_character_ids=["char-a", "char-b"],
        session_mode="group",
        persona_face="public",
        channel="dashboard",
    )
    session_manager._sessions["sid-no-followup-msg"] = session

    class FakeCharacterManager:
        def get_character(self, character_id):
            return {
                "character_id": character_id,
                "name": character_id,
                "system_prompt": "測試角色",
                "tts_language": "",
                "tts_rules": "",
            }

    route_results = [
        GroupRouterResult(True, "char-a", "first"),
        GroupRouterResult(True, "char-b", "second"),
    ]

    def fake_group_router(*args, **kwargs):
        return route_results.pop(0)

    captured_messages_per_turn = []
    captured_user_prompt_per_turn = []

    def fake_orchestration(*args, **kwargs):
        # orchestration_fn(messages, last_entities, user_prompt, user_prefs, ...)
        messages = args[0]
        user_prompt = args[2]
        captured_messages_per_turn.append([dict(m) for m in messages])
        captured_user_prompt_per_turn.append(user_prompt)
        return (
            f"回覆 {kwargs['session_ctx']['character_id']}",
            [], {}, False, None,
            "想法", None, None, None,
            "", [], SharedToolState(executed=False),
        )

    async def fake_sleep(_seconds):
        return None

    monkeypatch.setattr(group_loop, "get_character_manager", lambda: FakeCharacterManager())
    monkeypatch.setattr(group_loop, "get_router", lambda: object())
    monkeypatch.setattr(group_loop, "run_group_router", fake_group_router)
    monkeypatch.setattr(group_loop.asyncio, "sleep", fake_sleep)

    try:
        await group_loop.run_group_chat_loop(
            session=session,
            user_prompt="原始問題",
            user_prefs={"group_chat_max_bot_turns": 2, "group_chat_turn_delay_seconds": 0},
            orchestration_fn=fake_orchestration,
        )
    finally:
        session_manager._sessions.clear()

    # 任何 turn 的 messages 都不應包含【群組接力指令】
    for msgs in captured_messages_per_turn:
        for m in msgs:
            assert "【群組接力指令】" not in m.get("content", "")

    # user_prompt 永遠是原 user 訊息，不被替換成 followup 字串
    for prompt in captured_user_prompt_per_turn:
        assert prompt == "原始問題"
