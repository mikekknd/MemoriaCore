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
        GroupRouterResult(True, "char-a", "first", "new_speaker_add", "group_discussion"),
        GroupRouterResult(True, "char-b", "second", "new_speaker_reply_to_ai", "group_discussion"),
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
async def test_group_loop_uses_transient_user_anchor_without_persisting_it(monkeypatch):
    from api.routers.chat import group_loop

    session = SessionState(
        session_id="sid-group-transient",
        messages=[{
            "role": "system_event",
            "content": "YouTube Live 留言注入：1 則\n觀眾A: 這段怎麼看？",
            "debug_info": {"event_type": "youtube_live_chat_batch", "llm_visible": False},
        }],
        user_id="user-1",
        character_id="char-a",
        active_character_ids=["char-a", "char-b"],
        session_mode="group",
        persona_face="public",
        channel="dashboard",
    )
    session_manager._sessions["sid-group-transient"] = session

    class FakeCharacterManager:
        def get_character(self, character_id):
            return {
                "character_id": character_id,
                "name": "角色A" if character_id == "char-a" else "角色B",
                "system_prompt": "測試角色",
                "tts_language": "",
                "tts_rules": "",
            }

    captured_router_messages = []
    captured_orchestration_messages = []

    route_results = [
        GroupRouterResult(True, "char-a", "first", "new_speaker_add", "group_discussion"),
        GroupRouterResult(False, None, "done"),
    ]

    def fake_group_router(messages, *_args, **_kwargs):
        captured_router_messages.append(list(messages))
        return route_results.pop(0)

    def fake_orchestration(messages, *_args, **kwargs):
        captured_orchestration_messages.append(list(messages))
        return (
            "回覆 transient anchor",
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
        await group_loop.run_group_chat_loop(
            session=session,
            user_prompt="隱藏的外部上下文控制 prompt",
            user_prefs={"group_chat_max_bot_turns": 1, "group_chat_turn_delay_seconds": 0},
            orchestration_fn=fake_orchestration,
            transient_user_content="請根據已帶入的 YouTube 直播留言上下文回應。",
        )
    finally:
        session_manager._sessions.clear()

    assert captured_router_messages[0][-1] == {
        "role": "user",
        "content": "請根據已帶入的 YouTube 直播留言上下文回應。",
        "debug_info": {"transient_external_context_anchor": True},
    }
    assert captured_orchestration_messages[0][-1]["role"] == "user"
    assert session.messages[0]["role"] == "system_event"
    assert all(m.get("content") != "請根據已帶入的 YouTube 直播留言上下文回應。" for m in session.messages)


@pytest.mark.asyncio
async def test_group_loop_passes_youtube_live_discussion_mode_to_router(monkeypatch):
    from api.routers.chat import group_loop

    session = SessionState(
        session_id="sid-live-discussion-mode",
        messages=[{"role": "system_event", "content": "直播節奏提示"}],
        user_id="__youtube_live__",
        character_id="char-a",
        active_character_ids=["char-a", "char-b"],
        session_mode="group",
        persona_face="public",
        channel="youtube_live",
    )
    session_manager._sessions["sid-live-discussion-mode"] = session

    class FakeCharacterManager:
        def get_character(self, character_id):
            return {
                "character_id": character_id,
                "name": character_id,
                "system_prompt": "測試角色",
                "tts_language": "",
                "tts_rules": "",
            }

    captured_modes = []
    captured_session_ctx = []
    route_results = [
        GroupRouterResult(True, "char-a", "first", "new_speaker_add", "group_discussion"),
        GroupRouterResult(False, None, "done", "stop_all_spoken", "continue_group_discussion"),
    ]

    def fake_group_router(*args, **kwargs):
        captured_modes.append(kwargs.get("discussion_mode"))
        return route_results.pop(0)

    def fake_orchestration(*args, **kwargs):
        captured_session_ctx.append(dict(kwargs["session_ctx"]))
        return (
            f"回覆 {kwargs['session_ctx']['character_id']}",
            [], {}, False, None,
            "", None, None, None,
            "", [], SharedToolState(executed=False),
        )

    monkeypatch.setattr(group_loop, "get_character_manager", lambda: FakeCharacterManager())
    monkeypatch.setattr(group_loop, "get_router", lambda: object())
    monkeypatch.setattr(group_loop, "run_group_router", fake_group_router)

    try:
        await group_loop.run_group_chat_loop(
            session=session,
            user_prompt="直播自主推進",
            user_prefs={"group_chat_max_bot_turns": 2, "group_chat_turn_delay_seconds": 0},
            orchestration_fn=fake_orchestration,
            extra_session_ctx={"external_chat_context": {"source": "youtube_live_director"}},
            transient_user_content="請根據已提供的直播流程提示回應。",
        )
    finally:
        session_manager._sessions.clear()

    assert captured_modes == ["youtube_live", "youtube_live"]
    assert captured_session_ctx[0]["group_discussion_mode"] == "youtube_live"


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
        GroupRouterResult(True, "char-a", "first", "new_speaker_add", "group_discussion"),
        GroupRouterResult(True, "char-b", "second", "new_speaker_reply_to_ai", "group_discussion"),
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
    assert captured_followups[1]["conversation_intent"] == "group_discussion"
    assert captured_followups[1]["routing_action"] == "new_speaker_reply_to_ai"
    assert "routing_reason" not in captured_followups[1]


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


@pytest.mark.asyncio
async def test_group_loop_first_turn_stop_fallback_avoids_previous_speaker(monkeypatch):
    from api.routers.chat import group_loop

    session = SessionState(
        session_id="sid-stop-fallback",
        messages=[
            {"role": "user", "content": "上一輪"},
            {"role": "assistant", "content": "上一輪 A 回覆", "character_id": "char-a"},
            {"role": "user", "content": "新一輪"},
        ],
        user_id="user-1",
        character_id="char-a",
        active_character_ids=["char-a", "char-b"],
        session_mode="group",
        persona_face="public",
        channel="dashboard",
    )
    session_manager._sessions["sid-stop-fallback"] = session

    class FakeCharacterManager:
        def get_character(self, character_id):
            return {
                "character_id": character_id,
                "name": character_id,
                "system_prompt": "測試角色",
                "tts_language": "",
                "tts_rules": "",
            }

    def fake_group_router(*args, **kwargs):
        assert kwargs["last_speaker_id"] == "char-a"
        return GroupRouterResult(False, None, "stop", "stop_no_new_value")

    captured_character_ids = []

    def fake_orchestration(*args, **kwargs):
        captured_character_ids.append(kwargs["session_ctx"]["character_id"])
        return (
            "回覆",
            [], {}, False, None,
            "", None, None, None,
            "", [], SharedToolState(executed=False),
        )

    monkeypatch.setattr(group_loop, "get_character_manager", lambda: FakeCharacterManager())
    monkeypatch.setattr(group_loop, "get_router", lambda: object())
    monkeypatch.setattr(group_loop, "run_group_router", fake_group_router)

    try:
        turns = await group_loop.run_group_chat_loop(
            session=session,
            user_prompt="新一輪",
            user_prefs={"group_chat_max_bot_turns": 1, "group_chat_turn_delay_seconds": 0},
            orchestration_fn=fake_orchestration,
        )
    finally:
        session_manager._sessions.clear()

    assert captured_character_ids == ["char-b"]
    assert [t["character_id"] for t in turns] == ["char-b"]


@pytest.mark.asyncio
async def test_group_loop_first_turn_stop_still_produces_one_turn(monkeypatch):
    from api.routers.chat import group_loop

    session = SessionState(
        session_id="sid-first-stop-one-turn",
        messages=[{"role": "user", "content": "有人在嗎？"}],
        user_id="user-1",
        character_id="char-a",
        active_character_ids=["char-a", "char-b"],
        session_mode="group",
        persona_face="public",
        channel="dashboard",
    )
    session_manager._sessions["sid-first-stop-one-turn"] = session

    class FakeCharacterManager:
        def get_character(self, character_id):
            return {
                "character_id": character_id,
                "name": character_id,
                "system_prompt": "測試角色",
                "tts_language": "",
                "tts_rules": "",
            }

    def fake_group_router(*args, **kwargs):
        return GroupRouterResult(False, None, "stop", "stop_no_new_value")

    def fake_orchestration(*args, **kwargs):
        return (
            f"回覆 {kwargs['session_ctx']['character_id']}",
            [], {}, False, None,
            "", None, None, None,
            "", [], SharedToolState(executed=False),
        )

    monkeypatch.setattr(group_loop, "get_character_manager", lambda: FakeCharacterManager())
    monkeypatch.setattr(group_loop, "get_router", lambda: object())
    monkeypatch.setattr(group_loop, "run_group_router", fake_group_router)

    try:
        turns = await group_loop.run_group_chat_loop(
            session=session,
            user_prompt="有人在嗎？",
            user_prefs={"group_chat_max_bot_turns": 1, "group_chat_turn_delay_seconds": 0},
            orchestration_fn=fake_orchestration,
        )
    finally:
        session_manager._sessions.clear()

    assert [t["character_id"] for t in turns] == ["char-a"]


@pytest.mark.asyncio
async def test_group_loop_second_turn_stop_no_new_value_stops(monkeypatch):
    from api.routers.chat import group_loop

    session = SessionState(
        session_id="sid-second-stop",
        messages=[{"role": "user", "content": "短句收尾"}],
        user_id="user-1",
        character_id="char-a",
        active_character_ids=["char-a", "char-b"],
        session_mode="group",
        persona_face="public",
        channel="dashboard",
    )
    session_manager._sessions["sid-second-stop"] = session

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
        GroupRouterResult(True, "char-a", "first", "new_speaker_ack"),
        GroupRouterResult(False, None, "無新增價值", "stop_no_new_value"),
    ]

    def fake_group_router(*args, **kwargs):
        return route_results.pop(0)

    def fake_orchestration(*args, **kwargs):
        return (
            f"回覆 {kwargs['session_ctx']['character_id']}",
            [], {}, False, None,
            "", None, None, None,
            "", [], SharedToolState(executed=False),
        )

    monkeypatch.setattr(group_loop, "get_character_manager", lambda: FakeCharacterManager())
    monkeypatch.setattr(group_loop, "get_router", lambda: object())
    monkeypatch.setattr(group_loop, "run_group_router", fake_group_router)

    try:
        turns = await group_loop.run_group_chat_loop(
            session=session,
            user_prompt="短句收尾",
            user_prefs={"group_chat_max_bot_turns": 3, "group_chat_turn_delay_seconds": 0},
            orchestration_fn=fake_orchestration,
        )
    finally:
        session_manager._sessions.clear()

    assert [t["character_id"] for t in turns] == ["char-a"]
    assert route_results == []


@pytest.mark.asyncio
async def test_group_loop_allows_repeat_action_in_three_character_flow(monkeypatch):
    from api.routers.chat import group_loop

    session = SessionState(
        session_id="sid-repeat-three",
        messages=[{"role": "user", "content": "你們討論一下"}],
        user_id="user-1",
        character_id="char-a",
        active_character_ids=["char-a", "char-b", "char-c"],
        session_mode="group",
        persona_face="public",
        channel="dashboard",
    )
    session_manager._sessions["sid-repeat-three"] = session

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
        GroupRouterResult(True, "char-a", "first", "new_speaker_add"),
        GroupRouterResult(True, "char-b", "second", "new_speaker_reply_to_ai"),
        GroupRouterResult(True, "char-a", "reply", "repeat_speaker_reply_to_ai"),
    ]

    def fake_group_router(*args, **kwargs):
        return route_results.pop(0)

    def fake_orchestration(*args, **kwargs):
        return (
            f"回覆 {kwargs['session_ctx']['character_id']}",
            [], {}, False, None,
            "", None, None, None,
            "", [], SharedToolState(executed=False),
        )

    monkeypatch.setattr(group_loop, "get_character_manager", lambda: FakeCharacterManager())
    monkeypatch.setattr(group_loop, "get_router", lambda: object())
    monkeypatch.setattr(group_loop, "run_group_router", fake_group_router)

    try:
        turns = await group_loop.run_group_chat_loop(
            session=session,
            user_prompt="你們討論一下",
            user_prefs={"group_chat_max_bot_turns": 3, "group_chat_turn_delay_seconds": 0},
            orchestration_fn=fake_orchestration,
        )
    finally:
        session_manager._sessions.clear()

    assert [t["character_id"] for t in turns] == ["char-a", "char-b", "char-a"]


def test_group_turn_limit_allows_deeper_testing_and_clamps_at_hard_limit():
    from api.routers.chat import group_loop

    assert group_loop._group_turn_limit({"group_chat_max_bot_turns": 12}) == 12
    assert group_loop._group_turn_limit({"group_chat_max_bot_turns": 99}) == 12
