"""群組對話 loop 測試。"""
import asyncio

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
            None,
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
async def test_group_loop_uses_transient_turn_instruction_without_user_message(monkeypatch):
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
    captured_router_turn_instructions = []
    captured_router_turn_start_indexes = []
    captured_orchestration_messages = []
    captured_orchestration_prompts = []

    route_results = [
        GroupRouterResult(True, "char-a", "first", "new_speaker_add", "group_discussion"),
        GroupRouterResult(False, None, "done"),
    ]

    def fake_group_router(messages, *_args, **kwargs):
        captured_router_messages.append(list(messages))
        captured_router_turn_instructions.append(kwargs.get("current_turn_instruction"))
        captured_router_turn_start_indexes.append(kwargs.get("current_turn_start_index"))
        return route_results.pop(0)

    def fake_orchestration(messages, _last_entities, user_prompt, _user_prefs, **kwargs):
        captured_orchestration_messages.append(list(messages))
        captured_orchestration_prompts.append(user_prompt)
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
            None,
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

    assert all(m.get("role") != "user" for m in captured_router_messages[0])
    assert captured_router_turn_instructions[0] == "請根據已帶入的 YouTube 直播留言上下文回應。"
    assert captured_router_turn_start_indexes[0] == 1
    assert all(m.get("role") != "user" for m in captured_orchestration_messages[0])
    assert captured_orchestration_prompts == ["請根據已帶入的 YouTube 直播留言上下文回應。"]
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
async def test_group_loop_passes_live_episode_plan_to_router(monkeypatch):
    from api.routers.chat import group_loop

    session = SessionState(
        session_id="sid-live-episode-plan",
        messages=[{"role": "system_event", "content": "直播節奏提示"}],
        user_id="__youtube_live__",
        character_id="char-a",
        active_character_ids=["char-a", "char-b"],
        session_mode="group",
        persona_face="public",
        channel="youtube_live",
    )
    session_manager._sessions["sid-live-episode-plan"] = session

    class FakeCharacterManager:
        def get_character(self, character_id):
            return {
                "character_id": character_id,
                "name": character_id,
                "system_prompt": "測試角色",
                "tts_language": "",
                "tts_rules": "",
            }

    live_episode_plan = {
        "plan_id": "plan-general-panel",
        "mode": "planned_turn",
        "turn_id": "seg_01_turn_01",
        "speaker_policy": {
            "selection_mode": "router_select",
            "allowed_character_ids": ["char-b"],
        },
    }
    captured_plans = []

    def fake_group_router(*args, **kwargs):
        captured_plans.append(kwargs.get("live_episode_plan"))
        return GroupRouterResult(True, "char-b", "planned", "new_speaker_add", "group_discussion")

    def fake_orchestration(*args, **kwargs):
        return (
            "回覆 char-b",
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
            user_prefs={"group_chat_max_bot_turns": 1, "group_chat_turn_delay_seconds": 0},
            orchestration_fn=fake_orchestration,
            extra_session_ctx={
                "external_chat_context": {
                    "source": "youtube_live_director",
                    "live_episode_plan": live_episode_plan,
                }
            },
        )
    finally:
        session_manager._sessions.clear()

    assert captured_plans == [live_episode_plan]


@pytest.mark.asyncio
async def test_group_loop_adds_live_episode_reply_task_to_session_context_and_followup(monkeypatch):
    from api.routers.chat import group_loop

    session = SessionState(
        session_id="sid-live-reply-task",
        messages=[{"role": "system_event", "content": "直播節奏提示"}],
        user_id="__youtube_live__",
        character_id="char-a",
        active_character_ids=["char-a", "char-b"],
        session_mode="group",
        persona_face="public",
        channel="youtube_live",
    )
    session_manager._sessions["sid-live-reply-task"] = session

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
        GroupRouterResult(True, "char-a", "primary", "new_speaker_add", "group_discussion"),
        GroupRouterResult(True, "char-b", "handoff", "new_speaker_reply_to_ai", "continue_group_discussion"),
    ]
    captured_session_ctx = []

    def fake_group_router(*args, **kwargs):
        return route_results.pop(0)

    def fake_orchestration(*args, **kwargs):
        ctx = kwargs["session_ctx"]
        captured_session_ctx.append(dict(ctx))
        return (
            f"回覆 {ctx['character_id']}",
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
            extra_session_ctx={
                "external_chat_context": {
                    "source": "youtube_live_director",
                    "live_episode_plan": {
                        "mode": "planned_turn",
                        "turn_id": "seg_01_turn_02",
                        "turn_type": "analysis",
                        "turn_contract": {
                            "turn_id": "seg_01_turn_02",
                            "turn_type": "analysis",
                            "intent": "分析作品討論點",
                        },
                        "dialogue_policy": {
                            "min_replies": 1,
                            "max_replies": 2,
                            "autonomy": "guided",
                        },
                        "segment_memory": {
                            "covered_claims": ["Anime Corner 週榜只是即時快照"],
                        },
                        "focus_policy": {
                            "must_cover": ["台灣平台播出狀況", "續作季數脈絡", "觀眾補番成本"],
                        },
                        "evidence_policy": {
                            "allow_unverified_claims": False,
                        },
                        "forbidden_repetition": {
                            "claims": ["不要再次說週榜只是即時快照"],
                            "phrases": ["大風吹", "補番壓力", "神作", "霸權", "品質定論", "炎上"],
                        },
                    },
                }
            },
        )
    finally:
        session_manager._sessions.clear()

    first_task = captured_session_ctx[0]["live_episode_reply_task"]
    second_task = captured_session_ctx[1]["live_episode_reply_task"]
    assert first_task["stage"] == "primary_point"
    assert first_task["turn_reply_index"] == 1
    assert first_task["max_role_replies"] == 2
    assert "Anime Corner 週榜只是即時快照" in first_task["previous_claims"]
    assert second_task["stage"] == "reaction_translate_or_new_angle"
    assert second_task["turn_reply_index"] == 2
    assert second_task["previous_speaker_name"] == "角色A"
    assert second_task["previous_reply"] == "回覆 char-a"
    assert second_task["must_cover"] == ["台灣平台播出狀況", "續作季數脈絡", "觀眾補番成本"]
    assert second_task["allow_unverified_claims"] is False
    assert second_task["forbidden_claims"] == ["不要再次說週榜只是即時快照"]
    assert second_task["forbidden_phrases"] == ["大風吹", "補番壓力", "神作", "霸權", "品質定論", "炎上"]
    assert captured_session_ctx[1]["followup_instruction"]["live_episode_reply_task"] == second_task


@pytest.mark.asyncio
async def test_group_loop_planned_turn_max_turns_override_calls_router_once(monkeypatch):
    from api.routers.chat import group_loop

    session = SessionState(
        session_id="sid-planned-one-turn",
        messages=[{"role": "system_event", "content": "直播節奏提示"}],
        user_id="__youtube_live__",
        character_id="char-a",
        active_character_ids=["char-a", "char-b"],
        session_mode="group",
        persona_face="public",
        channel="youtube_live",
    )
    session_manager._sessions["sid-planned-one-turn"] = session

    class FakeCharacterManager:
        def get_character(self, character_id):
            return {
                "character_id": character_id,
                "name": character_id,
                "system_prompt": "測試角色",
                "tts_language": "",
                "tts_rules": "",
            }

    router_calls = 0

    def fake_group_router(*args, **kwargs):
        nonlocal router_calls
        router_calls += 1
        return GroupRouterResult(True, "char-a", "planned", "new_speaker_add", "group_discussion")

    def fake_orchestration(*args, **kwargs):
        return (
            "回覆 char-a",
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
            user_prompt="直播自主推進",
            user_prefs={"group_chat_max_bot_turns": 3, "group_chat_turn_delay_seconds": 0},
            orchestration_fn=fake_orchestration,
            extra_session_ctx={
                "external_chat_context": {
                    "source": "youtube_live_director",
                    "live_episode_plan": {
                        "mode": "planned_turn",
                        "max_turns_override": 1,
                    },
                }
            },
            max_turns_override=1,
        )
    finally:
        session_manager._sessions.clear()

    assert router_calls == 1
    assert len(turns) == 1


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
            None,
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
            None,
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
            None,
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


@pytest.mark.asyncio
async def test_group_loop_stops_before_followup_when_cancel_requested(monkeypatch):
    from api.routers.chat import group_loop

    session = SessionState(
        session_id="sid-cancel-before-followup",
        messages=[{"role": "system_event", "content": "直播節奏提示"}],
        user_id="__youtube_live__",
        character_id="char-a",
        active_character_ids=["char-a", "char-b"],
        session_mode="group",
        persona_face="public",
        channel="youtube_live",
    )
    session_manager._sessions[session.session_id] = session

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
        GroupRouterResult(True, "char-b", "second", "new_speaker_reply_to_ai", "continue_group_discussion"),
    ]
    cancel_event = asyncio.Event()
    orchestration_calls = []

    def fake_group_router(*_args, **_kwargs):
        return route_results.pop(0)

    def fake_orchestration(*_args, **kwargs):
        orchestration_calls.append(kwargs["session_ctx"]["character_id"])
        return (
            "第一段回覆", [], {}, False, None,
            "內心", None, None, None,
            "", [], SharedToolState(executed=False),
        )

    def cancel_after_first_turn(_turn):
        cancel_event.set()

    monkeypatch.setattr(group_loop, "get_character_manager", lambda: FakeCharacterManager())
    monkeypatch.setattr(group_loop, "get_router", lambda: object())
    monkeypatch.setattr(group_loop, "run_group_router", fake_group_router)

    try:
        turns = await group_loop.run_group_chat_loop(
            session=session,
            user_prompt="直播自主推進",
            user_prefs={"group_chat_max_bot_turns": 2, "group_chat_turn_delay_seconds": 0},
            orchestration_fn=fake_orchestration,
            on_turn=cancel_after_first_turn,
            cancel_event=cancel_event,
        )
    finally:
        session_manager._sessions.clear()

    assert [turn["character_id"] for turn in turns] == ["char-a"]
    assert orchestration_calls == ["char-a"]
    assert route_results == [
        GroupRouterResult(True, "char-b", "second", "new_speaker_reply_to_ai", "continue_group_discussion")
    ]


@pytest.mark.asyncio
async def test_group_loop_does_not_persist_reply_when_cancel_requested_before_persistence(monkeypatch):
    from api.routers.chat import group_loop

    session = SessionState(
        session_id="sid-cancel-before-persist",
        messages=[{"role": "system_event", "content": "直播節奏提示"}],
        user_id="__youtube_live__",
        character_id="char-a",
        active_character_ids=["char-a", "char-b"],
        session_mode="group",
        persona_face="public",
        channel="youtube_live",
    )
    session_manager._sessions[session.session_id] = session

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
        GroupRouterResult(True, "char-b", "second", "new_speaker_reply_to_ai", "continue_group_discussion"),
    ]
    cancel_event = asyncio.Event()
    orchestration_calls = []

    def fake_group_router(*_args, **_kwargs):
        return route_results.pop(0)

    def fake_orchestration(*_args, **kwargs):
        orchestration_calls.append(kwargs["session_ctx"]["character_id"])
        cancel_event.set()
        return (
            "過期回覆", [], {}, False, None,
            "內心", None, None, None,
            "", [], SharedToolState(executed=False),
        )

    monkeypatch.setattr(group_loop, "get_character_manager", lambda: FakeCharacterManager())
    monkeypatch.setattr(group_loop, "get_router", lambda: object())
    monkeypatch.setattr(group_loop, "run_group_router", fake_group_router)

    try:
        turns = await group_loop.run_group_chat_loop(
            session=session,
            user_prompt="直播自主推進",
            user_prefs={"group_chat_max_bot_turns": 2, "group_chat_turn_delay_seconds": 0},
            orchestration_fn=fake_orchestration,
            cancel_event=cancel_event,
        )
    finally:
        session_manager._sessions.clear()

    assert turns == []
    assert orchestration_calls == ["char-a"]
    assert [message["role"] for message in session.messages] == ["system_event"]
    assert route_results == [
        GroupRouterResult(True, "char-b", "second", "new_speaker_reply_to_ai", "continue_group_discussion")
    ]
