"""群組對話 loop 測試。"""
import pytest

from api.session_manager import SessionState, session_manager
from core.chat_orchestrator.dataclasses import GroupRouterResult


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
