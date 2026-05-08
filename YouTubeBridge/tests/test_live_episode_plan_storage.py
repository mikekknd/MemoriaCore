import shutil
import sys
import uuid
from pathlib import Path

from test_live_episode_plan_contract import sample_plan


BRIDGE_ROOT = Path(__file__).resolve().parents[1]
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))

from storage import BridgeStorage


def _tmp_dir() -> Path:
    path = Path(".pyTestTemp") / "youtube-bridge" / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=False)
    return path


def test_episode_plan_roundtrip_and_session_binding():
    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "enabled": True,
        })
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "display_name": "Live A",
        })
        plan = sample_plan()

        saved = storage.upsert_live_episode_plan(
            plan,
            source_path="runtime/YouTubeBridge/EpisodePlans/plan-a/episode-plan.json",
        )
        bound = storage.bind_episode_plan_to_session("live-a", "plan-general-panel")

        assert saved["plan_id"] == "plan-general-panel"
        assert saved["title"] == "泛用多人節目企劃"
        assert saved["schema_version"] == "live_episode_plan.v1"
        assert saved["plan_json"]["segments"][0]["segment_id"] == "seg_01"
        assert bound["episode_plan_id"] == "plan-general-panel"
        assert storage.get_session("live-a")["episode_plan_id"] == "plan-general-panel"
        assert storage.get_live_episode_plan("plan-general-panel")["source_path"].endswith(
            "episode-plan.json"
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_unbind_episode_plan_preserves_plan_record():
    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "enabled": True,
        })
        storage.upsert_session({"session_id": "live-a", "connector_id": "yt-main"})
        storage.upsert_live_episode_plan(sample_plan())
        storage.bind_episode_plan_to_session("live-a", "plan-general-panel")

        unbound = storage.unbind_episode_plan_from_session("live-a")

        assert unbound["episode_plan_id"] == ""
        assert storage.get_live_episode_plan("plan-general-panel") is not None
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_delete_episode_plan_clears_session_binding():
    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "enabled": True,
        })
        storage.upsert_session({"session_id": "live-a", "connector_id": "yt-main"})
        storage.upsert_live_episode_plan(sample_plan())
        storage.bind_episode_plan_to_session("live-a", "plan-general-panel")

        assert storage.delete_live_episode_plan("plan-general-panel") is True

        assert storage.get_live_episode_plan("plan-general-panel") is None
        assert storage.get_session("live-a")["episode_plan_id"] == ""
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
