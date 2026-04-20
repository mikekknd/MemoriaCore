"""coordinator 測試：run_dual_layer_orchestration 雙層 Agent 編排"""
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
        from core.chat_orchestrator import run_dual_layer_orchestration

        result = run_dual_layer_orchestration(
            session_messages=[{"role": "user", "content": "你好"}],
            last_entities=[],
            user_prompt="你好",
            user_prefs=sample_user_prefs,
        )

        assert isinstance(result, tuple)
        assert len(result) == 11

    def test_reply_in_result(self, mock_deps, mock_router_with_tools, sample_user_prefs):
        """11-tuple 的第一個元素應是回覆文字"""
        from core.chat_orchestrator import run_dual_layer_orchestration

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
        from core.chat_orchestrator import run_dual_layer_orchestration

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
        from core.chat_orchestrator import run_dual_layer_orchestration

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
        from core.chat_orchestrator import run_dual_layer_orchestration

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
        from core.chat_orchestrator import run_dual_layer_orchestration

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
        from core.chat_orchestrator import run_dual_layer_orchestration

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
        from core.chat_orchestrator import run_dual_layer_orchestration

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
        assert len(result) == 11


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
    def test_handles_11_tuple(self):
        """11-tuple 應直接回傳"""
        from api.routers.chat.orchestration import _unpack_orchestration_result

        result = tuple(range(11))
        unpacked = _unpack_orchestration_result(result)

        assert len(unpacked) == 11
        assert unpacked == result

    def test_handles_10_tuple_pads_cited_uids(self):
        """10-tuple 應補足 cited_uids=[]"""
        from api.routers.chat.orchestration import _unpack_orchestration_result

        result = tuple(range(10))
        unpacked = _unpack_orchestration_result(result)

        assert len(unpacked) == 11
        assert unpacked[-1] == []

    def test_handles_9_tuple_pads_thinking_speech_and_cited_uids(self):
        """9-tuple 應補足 thinking_speech="" 和 cited_uids=[]"""
        from api.routers.chat.orchestration import _unpack_orchestration_result

        result = tuple(range(9))
        unpacked = _unpack_orchestration_result(result)

        assert len(unpacked) == 11
        assert unpacked[-2] == ""
        assert unpacked[-1] == []
