import sys
from pathlib import Path


BRIDGE_ROOT = Path(__file__).resolve().parents[1]
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))


def test_bridge_contracts_are_reexported_by_bridge_engine():
    import bridge_engine
    import bridge_contracts

    assert bridge_engine.DIRECTOR_SCHEMA is bridge_contracts.DIRECTOR_SCHEMA
    assert bridge_engine.AUDIENCE_QUERY_CLASSIFIER_SCHEMA is bridge_contracts.AUDIENCE_QUERY_CLASSIFIER_SCHEMA
    assert bridge_contracts.SAFETY_CLASSIFIER_BATCH_LIMIT == 20


def test_live_runtime_is_reexported_by_bridge_engine():
    import bridge_engine
    from bridge_runtime import LiveRuntime

    runtime = bridge_engine.LiveRuntime(session_id="live-a")

    assert isinstance(runtime, LiveRuntime)
    assert runtime.session_id == "live-a"
    assert runtime.subscribers == set()
    assert runtime.audience_research_tasks == {}


def test_engine_public_events_match_manager_facade():
    from bridge_engine import YouTubeBridgeManager
    from engine_public_events import public_event

    event = {
        "id": 7,
        "status": "active",
        "message_text": "原始留言",
        "safe_message_text": "安全留言",
        "safety_status": "completed",
        "safety_label": "clean",
        "author_display_name": "觀眾",
        "author_channel_id": "secret-channel",
        "author_profile_image_url": "https://example.invalid/avatar.png",
        "metadata": {"prompt": "hidden", "topic_hint": "secret", "source": "test"},
    }

    assert YouTubeBridgeManager._public_event(event) == public_event(event)


def test_engine_test_events_match_manager_facade():
    import random

    from bridge_engine import YouTubeBridgeManager
    from engine_test_events import generate_test_super_chats

    session = {
        "session_id": "live-a",
        "display_name": "四月新番直播",
        "director_guidance": "聊四月新番",
    }
    random.seed(42)
    via_manager = YouTubeBridgeManager._generate_test_super_chats(
        session,
        4,
        "四月新番",
        include_malicious_sc=True,
        sc_burst=True,
    )
    random.seed(42)
    via_module = generate_test_super_chats(
        session,
        4,
        "四月新番",
        include_malicious_sc=True,
        sc_burst=True,
        public_test_topic=YouTubeBridgeManager._public_test_topic,
        sanitize_test_comment_text=YouTubeBridgeManager._sanitize_test_comment_text,
    )

    assert via_manager == via_module


def test_bridge_manager_uses_topic_pack_mixin():
    from bridge_engine import YouTubeBridgeManager
    from engine_topic_packs import TopicPackManagerMixin

    assert issubclass(YouTubeBridgeManager, TopicPackManagerMixin)


def test_bridge_manager_uses_director_mixin():
    from bridge_engine import YouTubeBridgeManager
    from engine_director import DirectorManagerMixin

    assert issubclass(YouTubeBridgeManager, DirectorManagerMixin)


def test_bridge_manager_uses_runtime_lifecycle_mixin():
    from bridge_engine import YouTubeBridgeManager
    from engine_runtime_lifecycle import RuntimeLifecycleManagerMixin

    assert issubclass(YouTubeBridgeManager, RuntimeLifecycleManagerMixin)


def test_bridge_manager_uses_injection_mixin():
    from bridge_engine import YouTubeBridgeManager
    from engine_injection import InjectionManagerMixin

    assert issubclass(YouTubeBridgeManager, InjectionManagerMixin)


def test_bridge_manager_uses_closing_mixin():
    from bridge_engine import YouTubeBridgeManager
    from engine_closing import ClosingManagerMixin

    assert issubclass(YouTubeBridgeManager, ClosingManagerMixin)
