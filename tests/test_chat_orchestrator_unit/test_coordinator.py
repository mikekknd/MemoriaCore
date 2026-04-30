"""coordinator 測試：run_dual_layer_orchestration 雙層 Agent 編排"""
import json
import pytest
from unittest.mock import patch, MagicMock
import api.dependencies


@pytest.fixture
def mock_deps(
    mock_router_with_tools,
    mock_memory_system,
    mock_storage,
    mock_analyzer,
    mock_character_manager,
):
    """
    置換 api.dependencies 所有 getter 的 fixture。
    使用 context manager 確保每次測試後單例狀態被還原。
    """
    with (
        patch.object(api.dependencies, 'get_memory_sys', return_value=mock_memory_system),
        patch.object(api.dependencies, 'get_storage', return_value=mock_storage),
        patch.object(api.dependencies, 'get_router', return_value=mock_router_with_tools),
        patch.object(api.dependencies, 'get_analyzer', return_value=mock_analyzer),
        patch.object(api.dependencies, 'get_character_manager', return_value=mock_character_manager),
        patch.object(api.dependencies, 'get_embed_model', return_value="bge-m3:latest"),
        patch('api.routers.chat.timer.StepTimer') as mock_timer,
    ):
        mock_timer.return_value._steps = []
        mock_timer.return_value.step = MagicMock()
        mock_timer.return_value.summary.return_value = {"total_ms": 100, "steps": []}
        yield


@pytest.fixture
def sample_user_prefs():
    return {
        "temperature": 0.7,
        "shift_threshold": 0.55,
        "ui_alpha": 0.6,
        "memory_hard_base": 0.55,
        "memory_threshold": 0.5,
        "context_window": 10,
        "active_character_id": "default",
        "dual_layer_enabled": True,
        "tavily_api_key": "",
        "openweather_api_key": "",
    }


class TestDualLayerCoordinator:
    def test_returns_11_tuple(self, mock_deps, sample_user_prefs):
        """雙層編排應回傳 11-tuple"""
        from core.chat_orchestrator.coordinator import run_dual_layer_orchestration

        result = run_dual_layer_orchestration(
            session_messages=[{"role": "user", "content": "你好"}],
            last_entities=[],
            user_prompt="你好",
            user_prefs=sample_user_prefs,
        )

        assert isinstance(result, tuple)
        assert len(result) == 12

    def test_reply_in_result(self, mock_deps, mock_router_with_tools, sample_user_prefs):
        """12-tuple 的第一個元素應是回覆文字"""
        from core.chat_orchestrator.coordinator import run_dual_layer_orchestration

        result = run_dual_layer_orchestration(
            session_messages=[{"role": "user", "content": "你好"}],
            last_entities=[],
            user_prompt="你好",
            user_prefs=sample_user_prefs,
        )

        reply_text = result[0]
        assert isinstance(reply_text, str)
        assert len(reply_text) > 0

    def test_retrieval_ctx_is_dict(self, mock_deps, sample_user_prefs):
        """retrieval_ctx (index 2) 應為 dict"""
        from core.chat_orchestrator.coordinator import run_dual_layer_orchestration

        result = run_dual_layer_orchestration(
            session_messages=[{"role": "user", "content": "你好"}],
            last_entities=[],
            user_prompt="你好",
            user_prefs=sample_user_prefs,
        )

        retrieval_ctx = result[2]
        assert isinstance(retrieval_ctx, dict)

    def test_retrieval_ctx_has_required_fields(self, mock_deps, sample_user_prefs):
        """retrieval_ctx 應包含所有必要欄位"""
        from core.chat_orchestrator.coordinator import run_dual_layer_orchestration

        result = run_dual_layer_orchestration(
            session_messages=[{"role": "user", "content": "你好"}],
            last_entities=[],
            user_prompt="你好",
            user_prefs=sample_user_prefs,
        )

        retrieval_ctx = result[2]
        required_fields = [
            "original_query", "expanded_keywords", "has_memory",
            "block_count", "threshold", "context_messages_count",
        ]
        for field in required_fields:
            assert field in retrieval_ctx, f"缺少欄位: {field}"

    def test_topic_shifted_flag_reflects_analyzer(self, mock_deps, mock_analyzer, sample_user_prefs):
        """topic_shifted (index 3) 應反映 analyzer.detect_topic_shift 的回傳值"""
        from core.chat_orchestrator.coordinator import run_dual_layer_orchestration

        # 模擬偵測到話題偏移
        mock_analyzer.detect_topic_shift.return_value = (True, 0.3)

        result = run_dual_layer_orchestration(
            session_messages=[
                {"role": "user", "content": "拉麵好吃"},
                {"role": "assistant", "content": "是嗎"},
                {"role": "user", "content": "比特幣走勢如何"},
            ],
            last_entities=[],
            user_prompt="比特幣走勢如何",
            user_prefs=sample_user_prefs,
        )

        topic_shifted = result[3]
        assert topic_shifted is True

    def test_no_shift_when_below_threshold(self, mock_deps, mock_analyzer, sample_user_prefs):
        """話題連貫時 topic_shifted 應為 False"""
        from core.chat_orchestrator.coordinator import run_dual_layer_orchestration

        mock_analyzer.detect_topic_shift.return_value = (False, 0.8)

        result = run_dual_layer_orchestration(
            session_messages=[
                {"role": "user", "content": "Python 非同步"},
                {"role": "assistant", "content": "async/await"},
                {"role": "user", "content": "那 asyncio 呢"},
            ],
            last_entities=[],
            user_prompt="asyncio",
            user_prefs=sample_user_prefs,
        )

        topic_shifted = result[3]
        assert topic_shifted is False

    def test_pipeline_data_set_when_shifted(self, mock_deps, mock_analyzer, sample_user_prefs):
        """話題偏移時 pipeline_data (index 4) 不為 None"""
        from core.chat_orchestrator.coordinator import run_dual_layer_orchestration

        mock_analyzer.detect_topic_shift.return_value = (True, 0.3)

        result = run_dual_layer_orchestration(
            session_messages=[
                {"role": "user", "content": "舊話題"},
                {"role": "assistant", "content": "回覆"},
                {"role": "user", "content": "新話題"},
            ],
            last_entities=[],
            user_prompt="新話題",
            user_prefs=sample_user_prefs,
        )

        pipeline_data = result[4]
        assert pipeline_data is not None

    def test_active_uids_extracted_from_history(self, mock_deps, sample_user_prefs):
        """session_messages 中的 [Ref: uid] 應被正確解析"""
        from core.chat_orchestrator.coordinator import run_dual_layer_orchestration

        session_messages = [
            {"role": "user", "content": "上次說的 [Ref: abc123]"},
            {"role": "assistant", "content": "記得"},
            {"role": "user", "content": "比特幣"},
        ]

        result = run_dual_layer_orchestration(
            session_messages=session_messages,
            last_entities=["標籤1"],
            user_prompt="比特幣",
            user_prefs=sample_user_prefs,
        )

        # 只要不拋例外即通過
        assert isinstance(result, tuple)
        assert len(result) == 12

    def test_group_followup_appended_when_history_ends_with_assistant(
        self, mock_deps, mock_router_with_tools, sample_user_prefs
    ):
        """群組接力時只追加最後一則 user control，避免 system prompt 變動。"""
        from core.chat_orchestrator.coordinator import run_dual_layer_orchestration

        run_dual_layer_orchestration(
            session_messages=[
                {"role": "user", "content": "兩位早安阿"},
                {"role": "assistant", "content": "[可可|char-a]: 早安呀"},
            ],
            last_entities=[],
            user_prompt="兩位早安阿",
            user_prefs=sample_user_prefs,
            session_ctx={
                "session_mode": "group",
                "active_character_ids": ["char-a", "default"],
                "character_id": "default",
                "followup_instruction": {
                    "user_prompt_original": "兩位早安阿",
                    "last_character_name": "可可",
                    "last_reply": "早安呀",
                },
            },
        )

        chat_call = [c for c in mock_router_with_tools.generate_calls if c["task_key"] == "chat"][-1]
        messages = chat_call["messages"]
        assert messages[0]["role"] == "system"
        assert "<group_followup_instruction" not in messages[0]["content"]
        assert messages[-1]["role"] == "user"
        assert '<group_followup_instruction source="system_control">' in messages[-1]["content"]
        assert messages[-1]["content"].count("<group_followup_instruction") == 1
        assert "<group_followup_control" not in messages[-1]["content"]
        assert "上一位發言者" not in messages[0]["content"]
        assert "早安呀" not in messages[0]["content"]
        assert "早安呀" not in messages[-1]["content"]

    def test_group_followup_turn_skips_tool_router(
        self, mock_deps, mock_router_with_tools, sample_user_prefs
    ):
        """群組接力 turn 1+ 不應重新跑意圖路由，避免原始 user_prompt 被重複送入 router。"""
        from core.chat_orchestrator.coordinator import run_dual_layer_orchestration

        prefs = {**sample_user_prefs, "tavily_api_key": "test-key"}
        run_dual_layer_orchestration(
            session_messages=[
                {"role": "user", "content": "我在檢查 bug"},
                {"role": "assistant", "content": "[可可|char-a]: 原來是在測試呀"},
            ],
            last_entities=[],
            user_prompt="我在檢查 bug",
            user_prefs=prefs,
            session_ctx={
                "session_mode": "group",
                "active_character_ids": ["char-a", "default"],
                "character_id": "default",
                "followup_instruction": {
                    "user_prompt_original": "我在檢查 bug",
                    "last_character_name": "可可",
                    "last_reply": "原來是在測試呀",
                },
            },
        )

        router_calls = [
            c for c in mock_router_with_tools.generate_calls
            if c["task_key"] == "router"
        ]
        assert router_calls == []

    def test_group_latest_user_message_is_marked_as_human(
        self, mock_deps, mock_router_with_tools, sample_user_prefs
    ):
        """群組對話的最新 user 內容需明確標為真人，避免被誤解成另一位 AI。"""
        from core.chat_orchestrator.coordinator import run_dual_layer_orchestration

        run_dual_layer_orchestration(
            session_messages=[
                {"role": "user", "content": "喵嗚!!"},
                {
                    "role": "assistant",
                    "content": "白蓮姐姐下午好喵！",
                    "character_id": "char-a",
                    "character_name": "可可",
                    "persona_state": {"internal_thought": "看到白蓮姐姐在測試聊天室裡"},
                },
                {
                    "role": "assistant",
                    "content": "哼，勉為其難地回應你吧。",
                    "character_id": "default",
                    "character_name": "白蓮",
                },
                {"role": "user", "content": "嗚嗚，可可都無視我拉!"},
            ],
            last_entities=[],
            user_prompt="嗚嗚，可可都無視我拉!",
            user_prefs=sample_user_prefs,
            session_ctx={
                "session_id": "sid-group-human",
                "session_mode": "group",
                "group_name": "測試聊天",
                "user_id": "user-1",
                "user_name": "mikekknd",
                "active_character_ids": ["char-a", "default"],
                "character_id": "char-a",
            },
        )

        chat_call = [c for c in mock_router_with_tools.generate_calls if c["task_key"] == "chat"][-1]
        latest_user = chat_call["messages"][-1]
        assert latest_user["role"] == "user"
        assert '<latest_user_message speaker="human_user" user_name="mikekknd" user_id="user-1">' in latest_user["content"]
        assert "嗚嗚，可可都無視我拉!" in latest_user["content"]

    def test_single_layer_group_followup_turn_skips_tool_router(
        self,
        monkeypatch,
        mock_router_with_tools,
        mock_memory_system,
        mock_storage,
        mock_analyzer,
        mock_character_manager,
        sample_user_prefs,
    ):
        """單層編排的群組接力 turn 1+ 也不應重新跑意圖路由。"""
        from api.routers.chat import orchestration

        monkeypatch.setattr(orchestration, "get_memory_sys", lambda: mock_memory_system)
        monkeypatch.setattr(orchestration, "get_storage", lambda: mock_storage)
        monkeypatch.setattr(orchestration, "get_router", lambda: mock_router_with_tools)
        monkeypatch.setattr(orchestration, "get_analyzer", lambda: mock_analyzer)
        monkeypatch.setattr(orchestration, "get_embed_model", lambda: "bge-m3")
        monkeypatch.setattr(orchestration, "get_character_manager", lambda: mock_character_manager)

        prefs = {**sample_user_prefs, "dual_layer_enabled": False, "tavily_api_key": "test-key"}
        orchestration._run_chat_orchestration(
            session_messages=[
                {"role": "user", "content": "我在檢查 bug"},
                {"role": "assistant", "content": "[可可|char-a]: 原來是在測試呀"},
            ],
            last_entities=[],
            user_prompt="我在檢查 bug",
            user_prefs=prefs,
            session_ctx={
                "session_mode": "group",
                "active_character_ids": ["char-a", "default"],
                "character_id": "default",
                "followup_instruction": {
                    "user_prompt_original": "我在檢查 bug",
                    "last_character_name": "可可",
                    "last_reply": "原來是在測試呀",
                },
            },
        )

        router_calls = [
            c for c in mock_router_with_tools.generate_calls
            if c["task_key"] == "router"
        ]
        assert router_calls == []

    def test_dual_layer_group_followup_reuses_image_without_reappending(
        self, mock_deps, mock_router_with_tools, sample_user_prefs
    ):
        """群組接力復用生圖結果時，第二位 AI 不應再次附上同一張圖片。"""
        from core.chat_orchestrator.coordinator import run_dual_layer_orchestration
        from core.chat_orchestrator.dataclasses import SharedToolState

        image_markdown = "![generated image](/api/v1/chat/generated-images/s/abc.jpeg)"
        shared_state = SharedToolState(
            tool_results=[
                {
                    "tool_name": "generate_image",
                    "result": json.dumps(
                        {
                            "generated_images": [{
                                "url": "/api/v1/chat/generated-images/s/abc.jpeg",
                                "markdown": image_markdown,
                            }]
                        },
                        ensure_ascii=False,
                    ),
                }
            ],
            tool_results_formatted="<tool_results>generated image</tool_results>",
            executed=True,
        )
        mock_router_with_tools.set_chat_response({
            "internal_thought": "沿用結果",
            "reply": f"我沿用剛剛那張圖來看。\n\n{image_markdown}",
            "extracted_entities": [],
        })

        result = run_dual_layer_orchestration(
            session_messages=[
                {"role": "user", "content": "畫一張貓"},
                {"role": "assistant", "content": f"[可可|char-a]: 好，完成了。\n\n{image_markdown}"},
            ],
            last_entities=[],
            user_prompt="畫一張貓",
            user_prefs=sample_user_prefs,
            session_ctx={
                "session_mode": "group",
                "active_character_ids": ["char-a", "default"],
                "character_id": "default",
                "shared_tool_state": shared_state,
                "followup_instruction": {
                    "user_prompt_original": "畫一張貓",
                    "last_character_name": "可可",
                    "last_reply": f"好，完成了。\n\n{image_markdown}",
                },
            },
        )

        assert result[0] == "我沿用剛剛那張圖來看。"

    def test_single_layer_group_followup_reuses_image_without_reappending(
        self,
        monkeypatch,
        mock_router_with_tools,
        mock_memory_system,
        mock_storage,
        mock_analyzer,
        mock_character_manager,
        sample_user_prefs,
    ):
        """單層編排復用生圖結果時，也不應自動重貼同一張圖片。"""
        from api.routers.chat import orchestration
        from core.chat_orchestrator.dataclasses import SharedToolState

        monkeypatch.setattr(orchestration, "get_memory_sys", lambda: mock_memory_system)
        monkeypatch.setattr(orchestration, "get_storage", lambda: mock_storage)
        monkeypatch.setattr(orchestration, "get_router", lambda: mock_router_with_tools)
        monkeypatch.setattr(orchestration, "get_analyzer", lambda: mock_analyzer)
        monkeypatch.setattr(orchestration, "get_embed_model", lambda: "bge-m3")
        monkeypatch.setattr(orchestration, "get_character_manager", lambda: mock_character_manager)

        image_markdown = "![generated image](/api/v1/chat/generated-images/s/abc.jpeg)"
        shared_state = SharedToolState(
            tool_results=[
                {
                    "tool_name": "generate_image",
                    "result": json.dumps(
                        {
                            "generated_images": [{
                                "url": "/api/v1/chat/generated-images/s/abc.jpeg",
                                "markdown": image_markdown,
                            }]
                        },
                        ensure_ascii=False,
                    ),
                }
            ],
            tool_results_formatted="<tool_results>generated image</tool_results>",
            executed=True,
        )
        mock_router_with_tools.set_chat_response({
            "internal_thought": "沿用結果",
            "reply": f"我沿用剛剛那張圖。\n\n{image_markdown}",
            "extracted_entities": [],
        })

        result = orchestration._run_chat_orchestration(
            session_messages=[
                {"role": "user", "content": "畫一張貓"},
                {"role": "assistant", "content": f"[可可|char-a]: 好，完成了。\n\n{image_markdown}"},
            ],
            last_entities=[],
            user_prompt="畫一張貓",
            user_prefs={
                **sample_user_prefs,
                "dual_layer_enabled": False,
                "image_generation_enabled": True,
                "minimax_api_key": "test-key",
            },
            session_ctx={
                "session_mode": "group",
                "active_character_ids": ["char-a", "default"],
                "character_id": "default",
                "shared_tool_state": shared_state,
                "followup_instruction": {
                    "user_prompt_original": "畫一張貓",
                    "last_character_name": "可可",
                    "last_reply": f"好，完成了。\n\n{image_markdown}",
                },
            },
        )

        assert result[0] == "我沿用剛剛那張圖。"

    def test_dual_layer_updates_opening_penalty_state_by_character(
        self, mock_deps, sample_user_prefs
    ):
        """雙層編排成功解析 reply 後，短期開場狀態依 session/character 隔離更新。"""
        from core.chat_orchestrator.coordinator import run_dual_layer_orchestration
        from core.opening_penalty import get_opening_penalty_manager

        mgr = get_opening_penalty_manager()
        mgr.clear()

        run_dual_layer_orchestration(
            session_messages=[{"role": "user", "content": "你好"}],
            last_entities=[],
            user_prompt="你好",
            user_prefs=sample_user_prefs,
            session_ctx={
                "session_id": "sid-opening",
                "character_id": "default",
                "persona_face": "public",
            },
        )
        run_dual_layer_orchestration(
            session_messages=[{"role": "user", "content": "你好"}],
            last_entities=[],
            user_prompt="你好",
            user_prefs=sample_user_prefs,
            session_ctx={
                "session_id": "sid-opening",
                "character_id": "char-coco",
                "persona_face": "public",
            },
        )

        assert mgr.get_blocked_openings(
            session_id="sid-opening",
            character_id="default",
            persona_face="public",
        ) == ("測試回應",)
        assert mgr.get_blocked_openings(
            session_id="sid-opening",
            character_id="char-coco",
            persona_face="public",
        ) == ("測試回應",)
        assert mgr.get_blocked_openings(
            session_id="sid-other",
            character_id="default",
            persona_face="public",
        ) == ()
        mgr.clear()

    def test_single_layer_updates_opening_penalty_state(
        self,
        monkeypatch,
        mock_router_with_tools,
        mock_memory_system,
        mock_storage,
        mock_analyzer,
        mock_character_manager,
        sample_user_prefs,
    ):
        """單層編排也會在成功解析 reply 後更新開場狀態。"""
        from api.routers.chat import orchestration
        from core.opening_penalty import get_opening_penalty_manager

        monkeypatch.setattr(orchestration, "get_memory_sys", lambda: mock_memory_system)
        monkeypatch.setattr(orchestration, "get_storage", lambda: mock_storage)
        monkeypatch.setattr(orchestration, "get_router", lambda: mock_router_with_tools)
        monkeypatch.setattr(orchestration, "get_analyzer", lambda: mock_analyzer)
        monkeypatch.setattr(orchestration, "get_embed_model", lambda: "bge-m3")
        monkeypatch.setattr(orchestration, "get_character_manager", lambda: mock_character_manager)

        mgr = get_opening_penalty_manager()
        mgr.clear()
        prefs = {**sample_user_prefs, "dual_layer_enabled": False}
        orchestration._run_chat_orchestration(
            session_messages=[{"role": "user", "content": "你好"}],
            last_entities=[],
            user_prompt="你好",
            user_prefs=prefs,
            session_ctx={
                "session_id": "sid-single-opening",
                "character_id": "default",
                "persona_face": "public",
            },
        )

        assert mgr.get_blocked_openings(
            session_id="sid-single-opening",
            character_id="default",
            persona_face="public",
        ) == ("測試回應",)
        mgr.clear()


class TestSelectOrchestration:
    def test_selects_dual_layer_when_enabled(self):
        """dual_layer_enabled=True 時應選擇雙層編排"""
        from api.routers.chat.orchestration import _select_orchestration

        fn = _select_orchestration({"dual_layer_enabled": True})
        assert fn.__name__ == "run_dual_layer_orchestration"

    def test_selects_single_layer_when_disabled(self):
        """dual_layer_enabled=False 時應選擇單層編排"""
        from api.routers.chat.orchestration import _select_orchestration

        fn = _select_orchestration({"dual_layer_enabled": False})
        assert fn.__name__ == "_run_chat_orchestration"

    def test_defaults_to_single_layer(self):
        """無 dual_layer_enabled 鍵時預設單層"""
        from api.routers.chat.orchestration import _select_orchestration

        fn = _select_orchestration({})
        assert fn.__name__ == "_run_chat_orchestration"


class TestUnpackOrchestrationResult:
    def test_handles_12_tuple(self):
        """12-tuple（最新）應直接回傳"""
        from api.routers.chat.orchestration import _unpack_orchestration_result

        result = tuple(range(12))
        unpacked = _unpack_orchestration_result(result)

        assert len(unpacked) == 12
        assert unpacked == result

    def test_handles_11_tuple_pads_tool_state(self):
        """舊 11-tuple 應補足 tool_state_export=None"""
        from api.routers.chat.orchestration import _unpack_orchestration_result

        result = tuple(range(11))
        unpacked = _unpack_orchestration_result(result)

        assert len(unpacked) == 12
        assert unpacked[-1] is None

    def test_handles_10_tuple_pads_cited_uids_and_tool_state(self):
        """10-tuple 應補足 cited_uids=[] 和 tool_state_export=None"""
        from api.routers.chat.orchestration import _unpack_orchestration_result

        result = tuple(range(10))
        unpacked = _unpack_orchestration_result(result)

        assert len(unpacked) == 12
        assert unpacked[-2] == []
        assert unpacked[-1] is None

    def test_handles_9_tuple_pads_full(self):
        """9-tuple 應補足 thinking_speech=""、cited_uids=[]、tool_state_export=None"""
        from api.routers.chat.orchestration import _unpack_orchestration_result

        result = tuple(range(9))
        unpacked = _unpack_orchestration_result(result)

        assert len(unpacked) == 12
        assert unpacked[-3] == ""
        assert unpacked[-2] == []
        assert unpacked[-1] is None
