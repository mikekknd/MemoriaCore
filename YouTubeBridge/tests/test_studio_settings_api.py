import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from test_live_episode_plan_contract import sample_plan


BRIDGE_ROOT = Path(__file__).resolve().parents[1]
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))

spec = importlib.util.spec_from_file_location("youtube_bridge_server_for_studio_settings", BRIDGE_ROOT / "server.py")
server_module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(server_module)

from storage import BridgeStorage


class FakeManager:
    def __init__(self):
        self.reset_count = 0

    def reset_memoria_client(self):
        self.reset_count += 1


class FakeStorage:
    def __init__(self, plan_dir: Path):
        self.plan_dir = plan_dir

    def get_episode_plan(self, plan_id: str):
        if plan_id != "ep1":
            return None
        return {"plan_id": plan_id, "source_path": str(self.plan_dir / "episode-plan.json")}


def _install_temp_state(monkeypatch, tmp_path):
    storage = BridgeStorage(tmp_path / "bridge.db")
    manager = FakeManager()
    summary_manager = SimpleNamespace(memoria_client="old-client")
    monkeypatch.setattr(server_module, "storage", storage)
    monkeypatch.setattr(server_module, "manager", manager)
    monkeypatch.setattr(server_module, "summary_manager", summary_manager)
    return storage, manager, summary_manager


@pytest.mark.asyncio
async def test_studio_free_talk_topics_lists_global_packs_and_episode_sidecar(tmp_path):
    topic_root = tmp_path / "freeTalkTopics"
    topic_root.mkdir()
    (topic_root / "anime.json").write_text(
        '{"name":"Anime","topics":[{"title":"新番近況","prompt":"聊本季新番。"}]}',
        encoding="utf-8",
    )
    plan_dir = tmp_path / "plans" / "ep1"
    plan_dir.mkdir(parents=True)
    (plan_dir / "free-talk-topics.json").write_text(
        '{"name":"EP1 Sidecar","topics":[{"title":"幕後延伸","prompt":"延伸企劃後續。"}]}',
        encoding="utf-8",
    )
    fake_state = SimpleNamespace(
        storage=FakeStorage(plan_dir),
        manager=FakeManager(),
        summary_manager=SimpleNamespace(),
        free_talk_topic_root=topic_root,
    )
    server_module._studio_settings_routes.configure(fake_state)

    data = await server_module._studio_settings_routes.list_studio_free_talk_topics("ep1")

    assert data["packs"][0]["pack_id"] == "anime"
    assert data["sidecar"]["found"] is True
    assert data["sidecar"]["topic_count"] == 1
    assert data["total_topic_count"] == 2


@pytest.mark.asyncio
async def test_studio_free_talk_topics_uses_bridge_storage_episode_plan_source(tmp_path):
    storage = BridgeStorage(tmp_path / "bridge.db")
    topic_root = tmp_path / "freeTalkTopics"
    topic_root.mkdir()
    (topic_root / "anime.json").write_text(
        '{"name":"Anime","topics":[{"title":"新番近況","prompt":"聊本季新番。"}]}',
        encoding="utf-8",
    )
    plan_dir = tmp_path / "plans" / "ep1"
    plan_dir.mkdir(parents=True)
    (plan_dir / "free-talk-topics.json").write_text(
        '{"name":"EP1 Sidecar","topics":[{"title":"幕後延伸","prompt":"延伸企劃後續。"}]}',
        encoding="utf-8",
    )
    plan = sample_plan()
    plan_id = str(plan["plan_id"])
    storage.upsert_live_episode_plan(plan, source_path=str(plan_dir / "episode-plan.json"))
    fake_state = SimpleNamespace(
        storage=storage,
        manager=FakeManager(),
        summary_manager=SimpleNamespace(),
        free_talk_topic_root=topic_root,
    )
    server_module._studio_settings_routes.configure(fake_state)

    data = await server_module._studio_settings_routes.list_studio_free_talk_topics(plan_id)

    assert data["packs"][0]["pack_id"] == "anime"
    assert data["sidecar"]["found"] is True
    assert data["sidecar"]["topic_count"] == 1
    assert data["total_topic_count"] == 2


def test_studio_free_talk_topic_root_is_project_runtime_path():
    assert server_module.app_state.free_talk_topic_root == (
        server_module.PROJECT_ROOT / "runtime" / "YouTubeBridge" / "freeTalkTopics"
    )


@pytest.mark.asyncio
async def test_studio_free_talk_topics_resolves_relative_episode_plan_source_path(tmp_path):
    storage = BridgeStorage(tmp_path / "bridge.db")
    topic_root = tmp_path / "freeTalkTopics"
    topic_root.mkdir()
    (topic_root / "anime.json").write_text(
        '{"name":"Anime","topics":[{"title":"新番近況","prompt":"聊本季新番。"}]}',
        encoding="utf-8",
    )
    episode_plan_root = tmp_path / "runtime" / "YouTubeBridge" / "EpisodePlans"
    plan_dir = episode_plan_root / "SomePlan"
    plan_dir.mkdir(parents=True)
    (plan_dir / "free-talk-topics.json").write_text(
        '{"name":"Relative Sidecar","topics":[{"title":"相對路徑延伸","prompt":"從 EpisodePlans root 找 sidecar。"}]}',
        encoding="utf-8",
    )
    plan = sample_plan()
    plan_id = str(plan["plan_id"])
    storage.upsert_live_episode_plan(plan, source_path="SomePlan/episode-plan.json")
    fake_state = SimpleNamespace(
        storage=storage,
        manager=FakeManager(),
        summary_manager=SimpleNamespace(),
        free_talk_topic_root=topic_root,
        episode_plan_root=episode_plan_root,
    )
    server_module._studio_settings_routes.configure(fake_state)

    data = await server_module._studio_settings_routes.list_studio_free_talk_topics(plan_id)

    assert data["sidecar"]["found"] is True
    assert data["sidecar"]["topic_count"] == 1
    assert data["total_topic_count"] == 2


def test_studio_episode_plan_root_is_project_runtime_path():
    assert server_module.app_state.episode_plan_root == (
        server_module.PROJECT_ROOT / "runtime" / "YouTubeBridge" / "EpisodePlans"
    )


@pytest.mark.asyncio
async def test_studio_settings_reports_free_talk_topic_pack_ids_presence(monkeypatch, tmp_path):
    storage, _manager, _summary_manager = _install_temp_state(monkeypatch, tmp_path)

    data = await server_module.get_studio_settings()
    assert data["live_defaults"]["post_plan_free_talk_topic_pack_ids"] == []
    assert data["live_defaults"]["post_plan_free_talk_topic_pack_ids_configured"] is False

    storage.upsert_studio_settings("live_defaults", {
        "post_plan_free_talk_enabled": True,
        "post_plan_free_talk_minutes": 20,
    })
    data = await server_module.get_studio_settings()
    assert data["live_defaults"]["post_plan_free_talk_topic_pack_ids"] == []
    assert data["live_defaults"]["post_plan_free_talk_topic_pack_ids_configured"] is False

    storage.upsert_studio_settings("live_defaults", {
        "post_plan_free_talk_enabled": True,
        "post_plan_free_talk_minutes": 20,
        "post_plan_free_talk_topic_pack_ids": [],
    })
    data = await server_module.get_studio_settings()
    assert data["live_defaults"]["post_plan_free_talk_topic_pack_ids"] == []
    assert data["live_defaults"]["post_plan_free_talk_topic_pack_ids_configured"] is True


@pytest.mark.asyncio
async def test_episode_plan_characters_resolve_memoria_roles_for_studio(monkeypatch, tmp_path):
    storage, _manager, _summary_manager = _install_temp_state(monkeypatch, tmp_path)
    plan = sample_plan()
    storage.upsert_live_episode_plan(plan)

    class FakeMemoriaClient:
        def list_characters(self):
            return [
                {"character_id": "host-a", "name": "主持A", "nickname": "Host A", "avatar_url": "https://example.invalid/host-a.png"},
                {"character_id": "analyst-b", "name": "分析B"},
                {"character_id": "skeptic-c", "name": "質疑C"},
            ]

    monkeypatch.setattr(server_module._episode_plans_routes, "MemoriaClient", FakeMemoriaClient)

    data = await server_module.get_episode_plan_characters("plan-general-panel")

    assert data["plan_id"] == "plan-general-panel"
    assert [item["character_id"] for item in data["characters"]] == ["host-a", "analyst-b", "skeptic-c"]
    assert data["characters"][0]["participant_id"] == "host-a"
    assert data["characters"][0]["participant_display_name"] == "主持A"
    assert data["characters"][0]["name"] == "主持A"
    assert data["characters"][0]["avatar_url"] == "https://example.invalid/host-a.png"
    assert data["characters"][0]["role_function"] == ["host", "energy_driver"]


@pytest.mark.asyncio
async def test_episode_plan_characters_reports_memoria_connection_failure(monkeypatch, tmp_path):
    storage, _manager, _summary_manager = _install_temp_state(monkeypatch, tmp_path)
    storage.upsert_live_episode_plan(sample_plan())

    class FakeMemoriaClient:
        def list_characters(self):
            raise ConnectionError("MemoriaCore offline")

    monkeypatch.setattr(server_module._episode_plans_routes, "MemoriaClient", FakeMemoriaClient)

    with pytest.raises(HTTPException) as exc_info:
        await server_module.get_episode_plan_characters("plan-general-panel")

    assert exc_info.value.status_code == 502
    assert "MemoriaCore 角色清單讀取失敗" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_studio_settings_defaults_hide_secrets(monkeypatch, tmp_path):
    storage, _manager, _summary_manager = _install_temp_state(monkeypatch, tmp_path)
    storage.upsert_single_connector({"api_key": "youtube-secret"})
    storage.upsert_memoria_config({
        "base_url": "http://127.0.0.1:8088/api/v1",
        "username": "admin",
        "password": "memoria-secret",
        "admin_bypass": False,
    })

    data = await server_module.get_studio_settings()

    assert data["connector"]["api_key"] == ""
    assert data["connector"]["api_key_configured"] is True
    assert data["memoria_auth"]["password_configured"] is True
    assert "password" not in data["memoria_auth"]
    assert data["test_settings"]["auto_comment_enabled"] is False
    assert data["test_settings"]["normal_comment_count"] == 8
    assert data["test_settings"].get("summary_preview", "") == ""
    assert "AI 助理工具" not in str(data["test_settings"])
    assert data["display_settings"]["show_live_events_enabled"] is False
    assert data["live_defaults"]["auto_inject_pending_enabled"] is True
    assert data["live_defaults"]["planned_duration_minutes"] == 52
    assert data["live_defaults"]["super_chat_batch_limit"] == 3
    assert data["persona_overlays"] == []
    assert data["tts_profiles"] == []
    assert "sources" in data["tts_sources"]


@pytest.mark.asyncio
async def test_studio_settings_patch_preserves_omitted_sections(monkeypatch, tmp_path):
    _storage, _manager, _summary_manager = _install_temp_state(monkeypatch, tmp_path)

    await server_module.update_studio_settings(server_module.StudioSettingsPatch(
        test_settings=server_module.StudioTestSettings(normal_comment_count=12),
    ))
    await server_module.update_studio_settings(server_module.StudioSettingsPatch(
        display_settings=server_module.StudioDisplaySettings(show_live_events_enabled=True),
    ))
    data = await server_module.get_studio_settings()

    assert data["test_settings"]["normal_comment_count"] == 12
    assert data["display_settings"]["show_live_events_enabled"] is True
    assert data["live_defaults"]["planned_duration_minutes"] == 52


@pytest.mark.asyncio
async def test_studio_settings_patch_preserves_unset_free_talk_topic_selection(monkeypatch, tmp_path):
    storage, _manager, _summary_manager = _install_temp_state(monkeypatch, tmp_path)
    storage.upsert_studio_settings("live_defaults", {
        "post_plan_free_talk_enabled": True,
        "post_plan_free_talk_minutes": 20,
        "post_plan_free_talk_topic_pack_ids": ["casual"],
    })

    data = await server_module.update_studio_settings(server_module.StudioSettingsPatch(
        live_defaults=server_module.StudioLiveDefaults(post_plan_free_talk_minutes=30),
    ))

    assert data["live_defaults"]["post_plan_free_talk_minutes"] == 30
    assert data["live_defaults"]["post_plan_free_talk_topic_pack_ids"] == ["casual"]
    assert data["live_defaults"]["post_plan_free_talk_topic_pack_ids_configured"] is True


@pytest.mark.asyncio
async def test_studio_settings_patch_does_not_create_topic_selection_when_unset(monkeypatch, tmp_path):
    _storage, _manager, _summary_manager = _install_temp_state(monkeypatch, tmp_path)

    data = await server_module.update_studio_settings(server_module.StudioSettingsPatch(
        live_defaults=server_module.StudioLiveDefaults(post_plan_free_talk_minutes=30),
    ))

    assert data["live_defaults"]["post_plan_free_talk_minutes"] == 30
    assert data["live_defaults"]["post_plan_free_talk_topic_pack_ids"] == []
    assert data["live_defaults"]["post_plan_free_talk_topic_pack_ids_configured"] is False


@pytest.mark.asyncio
async def test_studio_settings_patch_preserves_connector_api_key_when_blank(monkeypatch, tmp_path):
    storage, _manager, _summary_manager = _install_temp_state(monkeypatch, tmp_path)
    storage.upsert_single_connector({"api_key": "kept-secret"})

    data = await server_module.update_studio_settings(server_module.StudioSettingsPatch(
        connector=server_module.ConnectorConfig(api_key=""),
    ))

    assert data["connector"]["api_key"] == ""
    assert data["connector"]["api_key_configured"] is True
    assert storage.ensure_single_connector()["api_key"] == "kept-secret"


@pytest.mark.asyncio
async def test_studio_settings_patch_preserves_memoria_password_and_resets_clients(monkeypatch, tmp_path):
    storage, manager, summary_manager = _install_temp_state(monkeypatch, tmp_path)
    storage.upsert_memoria_config({
        "base_url": "http://127.0.0.1:8088/api/v1",
        "username": "admin",
        "password": "kept-password",
        "admin_bypass": False,
    })

    data = await server_module.update_studio_settings(server_module.StudioSettingsPatch(
        memoria_auth=server_module.MemoriaAuthConfig(
            base_url="http://127.0.0.1:8088/api/v1",
            username="admin2",
            password="",
            admin_bypass=True,
        ),
    ))

    assert data["memoria_auth"]["username"] == "admin2"
    assert data["memoria_auth"]["password_configured"] is True
    assert storage.get_memoria_config()["password"] == "kept-password"
    assert manager.reset_count == 1
    assert summary_manager.memoria_client != "old-client"


@pytest.mark.asyncio
async def test_studio_role_overlay_autosave_uses_replace_mode(tmp_path):
    from models import LivePersonaOverlayRequest
    from server_routes import persona_overlays

    storage = BridgeStorage(tmp_path / "bridge.db")
    persona_overlays.configure(SimpleNamespace(storage=storage))

    saved = await persona_overlays.update_persona_overlay(
        "host_sakura",
        LivePersonaOverlayRequest(
            enabled=True,
            mode="replace",
            system_prompt="直播專用 prompt",
            self_address="我",
            opening_intro="大家好。",
            reply_rules="自然接話。",
        ),
    )

    assert saved["mode"] == "replace"
    assert storage.get_live_persona_overlay("host_sakura")["mode"] == "replace"


@pytest.mark.asyncio
async def test_studio_avatar_asset_upload_lists_and_serves_local_file(monkeypatch, tmp_path):
    _storage, _manager, _summary_manager = _install_temp_state(monkeypatch, tmp_path)
    monkeypatch.setattr(server_module, "STUDIO_AVATAR_ROOT", tmp_path / "avatars", raising=False)

    saved = await server_module.upload_studio_avatar_asset(server_module.StudioAvatarUploadRequest(
        filename="../Coco Face.PNG",
        data_url="data:image/png;base64,YXZhdGFyLWJ5dGVz",
    ))

    assert saved["name"].endswith(".png")
    assert ".." not in saved["name"]
    assert saved["url"] == f"/studio/avatar-assets/{saved['name']}"

    listed = await server_module.list_studio_avatar_assets()
    assert listed["avatars"][0]["name"] == saved["name"]
    assert listed["avatars"][0]["url"] == saved["url"]
    assert listed["avatars"][0]["content_type"] == "image/png"

    response = await server_module.get_studio_avatar_asset(saved["name"])
    assert Path(response.path).read_bytes() == b"avatar-bytes"
    assert response.media_type == "image/png"


@pytest.mark.asyncio
async def test_studio_avatar_asset_upload_rejects_non_image_data_url(monkeypatch, tmp_path):
    _storage, _manager, _summary_manager = _install_temp_state(monkeypatch, tmp_path)
    monkeypatch.setattr(server_module, "STUDIO_AVATAR_ROOT", tmp_path / "avatars", raising=False)

    with pytest.raises(HTTPException) as exc_info:
        await server_module.upload_studio_avatar_asset(server_module.StudioAvatarUploadRequest(
            filename="note.txt",
            data_url="data:text/plain;base64,SGVsbG8=",
        ))

    assert exc_info.value.status_code == 400
    assert "支援 PNG/JPEG/WebP/GIF" in str(exc_info.value.detail)
