import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from test_live_episode_plan_contract import sample_plan


BRIDGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRIDGE_ROOT))

spec = importlib.util.spec_from_file_location("youtube_bridge_server_for_auth", BRIDGE_ROOT / "server.py")
server_module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(server_module)
require_bridge_key = server_module.require_bridge_key


def _request(host: str, key: str = "", path: str = ""):
    return SimpleNamespace(
        client=SimpleNamespace(host=host),
        headers={"X-Bridge-Key": key} if key else {},
        url=SimpleNamespace(path=path) if path else None,
    )


def _control_ui_source() -> str:
    static_root = Path(server_module.STATIC_ROOT)
    parts = [(static_root / "index.html").read_text(encoding="utf-8")]
    ui_root = static_root / "ui"
    if ui_root.exists():
        for name in (
            "index.css",
            "base.css",
            "live-session.css",
            "topic-pack.css",
            "topic-graph.css",
            "overlays.css",
            "core.js",
            "selectors.js",
            "topic-packs.js",
            "topic-graph.js",
            "topic-pack-crud.js",
            "fact-card-import.js",
            "memoria-control.js",
            "live-persona-control.js",
            "events-control.js",
            "summary-director-control.js",
            "session-control.js",
            "control.js",
            "app.js",
        ):
            path = ui_root / name
            if path.exists():
                parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def _assert_launcher_uses_runtime_log_dir(source: str, legacy_runtime_prefix: str) -> None:
    assert r"runtime\log" in source
    assert ".foreground.log" in source or (".out.log" in source and ".err.log" in source)
    assert legacy_runtime_prefix not in source.lower()

# Split from test_server_auth.py: server, launcher, route, and UI contracts.

def test_control_ui_exposes_episode_plan_import_and_binding_controls():
    index_html = _control_ui_source()
    live_session_block = index_html[
        index_html.index('<div id="liveSessionPane"'):
        index_html.index('<div id="eventsPane"')
    ]

    assert 'id="episodePlanFile"' in live_session_block
    assert 'id="importEpisodePlan"' in live_session_block
    assert 'id="episodePlanSelect"' in live_session_block
    assert 'id="syncLocalEpisodePlans"' in live_session_block
    assert 'id="bindEpisodePlan"' in live_session_block
    assert 'id="unbindEpisodePlan"' in live_session_block
    assert 'id="episodePlanStatus"' in live_session_block
    assert 'id="episodePlanHandoffGapSeconds"' not in live_session_block
    assert 'id="episodePlanTurnGapSeconds"' not in live_session_block
    assert 'id="episodePlanDebugWait"' in live_session_block
    assert 'id="episodePlanDebugList"' in live_session_block
    assert "節目清單 Debug" in live_session_block
    assert "下一輪等待" in live_session_block
    assert "function refreshEpisodePlans" in index_html
    assert 'api("/episode-plans/sync-local", { method: "POST" })' in index_html
    assert "function importEpisodePlanFromFile" in index_html
    assert "function bindSelectedEpisodePlan" in index_html
    assert "function renderDirectorSegmentState" in index_html
    assert "function renderEpisodePlanDebugList" in index_html
    assert "episodePlanDebugWait" in index_html
    assert "function episodePlanSelectLabel" in index_html
    assert "${folder}/${title}" in index_html
    assert ".episode-plan-turn.active" in index_html
    assert "planned_state" in index_html
    assert "interrupt_state" in index_html


@pytest.mark.asyncio
async def test_episode_plan_import_and_bind_endpoints(monkeypatch, tmp_path):
    storage = server_module.BridgeStorage(tmp_path / "bridge.db")
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
    monkeypatch.setattr(server_module, "storage", storage)

    class FakeMemoriaClient:
        def list_characters(self):
            return [
                {"character_id": "host-a", "name": "主持A"},
                {"character_id": "analyst-b", "name": "分析B"},
                {"character_id": "skeptic-c", "name": "質疑C"},
            ]

    monkeypatch.setattr(server_module._episode_plans_routes, "MemoriaClient", FakeMemoriaClient, raising=False)

    saved = await server_module.import_episode_plan(
        server_module.EpisodePlanImportRequest(
            plan_json=sample_plan(),
            source_path="episode-plan.json",
        )
    )
    listed = await server_module.list_episode_plans()
    bound = await server_module.bind_episode_plan(
        "live-a",
        server_module.EpisodePlanBindRequest(plan_id="plan-general-panel"),
    )
    fetched = await server_module.get_episode_plan("plan-general-panel")
    unbound = await server_module.unbind_episode_plan("live-a")
    deleted = await server_module.delete_episode_plan("plan-general-panel")

    assert saved["plan_id"] == "plan-general-panel"
    assert listed[0]["plan_id"] == "plan-general-panel"
    assert bound["episode_plan_id"] == "plan-general-panel"
    assert bound["character_ids"] == ["host-a", "analyst-b", "skeptic-c"]
    assert bound["episode_plan_character_binding"]["character_ids"] == [
        "host-a",
        "analyst-b",
        "skeptic-c",
    ]
    assert fetched["plan_json"]["plan_id"] == "plan-general-panel"
    assert unbound["episode_plan_id"] == ""
    assert deleted == {"deleted": True, "plan_id": "plan-general-panel"}


@pytest.mark.asyncio
async def test_episode_plan_bind_reports_missing_character_name(monkeypatch, tmp_path):
    storage = server_module.BridgeStorage(tmp_path / "bridge.db")
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
    storage.upsert_live_episode_plan(sample_plan())
    monkeypatch.setattr(server_module, "storage", storage)

    class FakeMemoriaClient:
        def list_characters(self):
            return [
                {"character_id": "host-a", "name": "主持A"},
                {"character_id": "analyst-b", "name": "分析B"},
            ]

    monkeypatch.setattr(server_module._episode_plans_routes, "MemoriaClient", FakeMemoriaClient, raising=False)

    with pytest.raises(HTTPException) as exc:
        await server_module.bind_episode_plan(
            "live-a",
            server_module.EpisodePlanBindRequest(plan_id="plan-general-panel"),
        )

    assert exc.value.status_code == 400
    assert "找不到企劃角色「質疑C」" in exc.value.detail


@pytest.mark.asyncio
async def test_session_upsert_with_episode_plan_resolves_characters_by_name(monkeypatch, tmp_path):
    storage = server_module.BridgeStorage(tmp_path / "bridge.db")
    storage.upsert_connector({
        "connector_id": "yt-main",
        "display_name": "YouTube Main",
        "enabled": True,
    })
    storage.upsert_live_episode_plan(sample_plan())
    monkeypatch.setattr(server_module, "storage", storage)

    class FakeMemoriaClient:
        def list_characters(self):
            return [
                {"character_id": "host-a", "name": "主持A"},
                {"character_id": "analyst-b", "name": "分析B"},
                {"character_id": "skeptic-c", "name": "質疑C"},
            ]

    monkeypatch.setattr(server_module._sessions_routes, "MemoriaClient", FakeMemoriaClient, raising=False)

    saved = await server_module.upsert_session(
        server_module.LiveSessionConfig(
            session_id="live-a",
            connector_id="yt-main",
            display_name="Live A",
            episode_plan_id="plan-general-panel",
            character_ids=["wrong-manual-selection"],
        )
    )

    assert saved["episode_plan_id"] == "plan-general-panel"
    assert saved["character_ids"] == ["host-a", "analyst-b", "skeptic-c"]


@pytest.mark.asyncio
async def test_start_current_session_with_episode_plan_resolves_characters_by_name(monkeypatch, tmp_path):
    storage = server_module.BridgeStorage(tmp_path / "bridge.db")
    storage.upsert_connector({
        "connector_id": "youtube-main",
        "display_name": "YouTube Main",
        "api_key": "key",
        "enabled": True,
    })
    storage.upsert_live_episode_plan(sample_plan())
    monkeypatch.setattr(server_module, "storage", storage)

    class FakeMemoriaClient:
        def list_characters(self):
            return [
                {"character_id": "host-a", "name": "主持A"},
                {"character_id": "analyst-b", "name": "分析B"},
                {"character_id": "skeptic-c", "name": "質疑C"},
            ]

    class FakeManager:
        async def start_session(self, session_id: str):
            storage.update_session_fields(session_id, status="running", started_at="2026-05-06T10:20:00")
            return {"session_id": session_id, "status": "running", "running": True}

        async def stop_session(self, session_id: str):
            storage.update_session_fields(session_id, status="stopped")
            return self.get_status(session_id)

        def get_status(self, session_id: str):
            session = storage.get_session(session_id)
            return {
                "session_id": session_id,
                "status": session.get("status") if session else "missing",
                "running": bool(session and session.get("status") == "running"),
            }

    monkeypatch.setattr(server_module, "manager", FakeManager())
    monkeypatch.setattr(server_module._sessions_routes, "MemoriaClient", FakeMemoriaClient, raising=False)

    result = await server_module.start_current_session(
        server_module.LiveSessionConfig(
            video_id="",
            episode_plan_id="plan-general-panel",
            character_ids=[],
        )
    )

    assert result["episode_plan_id"] == "plan-general-panel"
    assert result["character_ids"] == ["host-a", "analyst-b", "skeptic-c"]
    assert storage.get_session(result["session_id"])["character_ids"] == [
        "host-a",
        "analyst-b",
        "skeptic-c",
    ]


@pytest.mark.parametrize(
    ("characters", "expected"),
    [
        ([], "角色清單為空"),
        (
            [
                {"character_id": "host-a", "name": "主持A"},
                {"character_id": "analyst-b", "name": "分析B"},
            ],
            "找不到企劃角色「質疑C」",
        ),
        (
            [
                {"character_id": "host-a", "name": "主持A"},
                {"character_id": "analyst-b", "name": "分析B"},
                {"character_id": "skeptic-a", "name": "質疑C"},
                {"character_id": "skeptic-b", "name": "質疑C"},
            ],
            "對應到多個 MemoriaCore 角色",
        ),
    ],
)
@pytest.mark.asyncio
async def test_start_current_session_reports_episode_plan_character_binding_errors(
    monkeypatch,
    tmp_path,
    characters,
    expected,
):
    storage = server_module.BridgeStorage(tmp_path / "bridge.db")
    storage.upsert_connector({
        "connector_id": "youtube-main",
        "display_name": "YouTube Main",
        "api_key": "key",
        "enabled": True,
    })
    storage.upsert_live_episode_plan(sample_plan())
    monkeypatch.setattr(server_module, "storage", storage)

    class FakeMemoriaClient:
        def list_characters(self):
            return list(characters)

    monkeypatch.setattr(server_module._sessions_routes, "MemoriaClient", FakeMemoriaClient, raising=False)

    with pytest.raises(HTTPException) as exc:
        await server_module.start_current_session(
            server_module.LiveSessionConfig(
                video_id="",
                episode_plan_id="plan-general-panel",
            )
        )

    assert exc.value.status_code == 400
    assert "企劃角色對應失敗" in str(exc.value.detail)
    assert expected in str(exc.value.detail)
    assert storage.list_sessions() == []


@pytest.mark.asyncio
async def test_episode_plan_sync_local_folder_imports_child_packages(monkeypatch, tmp_path):
    storage = server_module.BridgeStorage(tmp_path / "bridge.db")
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
    root = tmp_path / "EpisodePlans"
    plan_dir = root / "Test"
    plan_dir.mkdir(parents=True)
    (plan_dir / "episode-plan.json").write_text(
        server_module.json.dumps(sample_plan(), ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(server_module, "storage", storage)
    monkeypatch.setattr(server_module._episode_plans_routes, "storage", storage)
    monkeypatch.setattr(server_module._episode_plans_routes, "EPISODE_PLANS_ROOT", root)

    class FakeMemoriaClient:
        def list_characters(self):
            return [
                {"character_id": "host-a", "name": "主持A"},
                {"character_id": "analyst-b", "name": "分析B"},
                {"character_id": "skeptic-c", "name": "質疑C"},
            ]

    monkeypatch.setattr(server_module._episode_plans_routes, "MemoriaClient", FakeMemoriaClient, raising=False)

    synced = await server_module.sync_local_episode_plans()
    listed = await server_module.list_episode_plans()
    bound = await server_module.bind_episode_plan(
        "live-a",
        server_module.EpisodePlanBindRequest(plan_id="plan-general-panel"),
    )

    assert synced["imported_count"] == 1
    assert synced["skipped_count"] == 0
    assert synced["plans"][0]["source_path"] == "Test/episode-plan.json"
    assert listed[0]["plan_id"] == "plan-general-panel"
    assert listed[0]["source_path"] == "Test/episode-plan.json"
    assert bound["episode_plan_id"] == "plan-general-panel"


@pytest.mark.asyncio
async def test_episode_plan_sync_local_prunes_deleted_child_packages(monkeypatch, tmp_path):
    storage = server_module.BridgeStorage(tmp_path / "bridge.db")
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
    root = tmp_path / "EpisodePlans"
    plan_dir = root / "DeletedPlan"
    plan_dir.mkdir(parents=True)
    (plan_dir / "episode-plan.json").write_text(
        server_module.json.dumps(sample_plan(), ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(server_module, "storage", storage)
    monkeypatch.setattr(server_module._episode_plans_routes, "storage", storage)
    monkeypatch.setattr(server_module._episode_plans_routes, "EPISODE_PLANS_ROOT", root)

    await server_module.sync_local_episode_plans()
    storage.bind_episode_plan_to_session("live-a", "plan-general-panel")
    assert storage.get_session("live-a")["episode_plan_id"] == "plan-general-panel"

    (plan_dir / "episode-plan.json").unlink()

    synced = await server_module.sync_local_episode_plans()
    listed = await server_module.list_episode_plans()

    assert synced["removed_count"] == 1
    assert synced["removed_plan_ids"] == ["plan-general-panel"]
    assert listed == []
    assert storage.get_session("live-a")["episode_plan_id"] == ""


@pytest.mark.asyncio
async def test_episode_plan_sync_local_preserves_manual_file_import(monkeypatch, tmp_path):
    storage = server_module.BridgeStorage(tmp_path / "bridge.db")
    storage.upsert_live_episode_plan(sample_plan(), source_path="episode-plan.json")
    root = tmp_path / "EpisodePlans"
    root.mkdir(parents=True)
    monkeypatch.setattr(server_module, "storage", storage)
    monkeypatch.setattr(server_module._episode_plans_routes, "storage", storage)
    monkeypatch.setattr(server_module._episode_plans_routes, "EPISODE_PLANS_ROOT", root)

    synced = await server_module.sync_local_episode_plans()
    listed = await server_module.list_episode_plans()

    assert synced["removed_count"] == 0
    assert [item["plan_id"] for item in listed] == ["plan-general-panel"]
    assert listed[0]["source_path"] == "episode-plan.json"


@pytest.mark.asyncio
async def test_episode_plan_sync_local_prunes_replaced_plan_id_for_same_source(monkeypatch, tmp_path):
    storage = server_module.BridgeStorage(tmp_path / "bridge.db")
    root = tmp_path / "EpisodePlans"
    plan_dir = root / "ReplacedPlan"
    plan_dir.mkdir(parents=True)
    original_plan = sample_plan()
    replaced_plan = sample_plan()
    replaced_plan["plan_id"] = "plan-general-panel-replaced"
    replaced_plan["title"] = "替換後企劃"
    plan_file = plan_dir / "episode-plan.json"
    plan_file.write_text(
        server_module.json.dumps(original_plan, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(server_module, "storage", storage)
    monkeypatch.setattr(server_module._episode_plans_routes, "storage", storage)
    monkeypatch.setattr(server_module._episode_plans_routes, "EPISODE_PLANS_ROOT", root)

    await server_module.sync_local_episode_plans()
    plan_file.write_text(
        server_module.json.dumps(replaced_plan, ensure_ascii=False),
        encoding="utf-8",
    )

    synced = await server_module.sync_local_episode_plans()
    listed = await server_module.list_episode_plans()

    assert synced["removed_count"] == 1
    assert synced["removed_plan_ids"] == ["plan-general-panel"]
    assert [item["plan_id"] for item in listed] == ["plan-general-panel-replaced"]
    assert listed[0]["source_path"] == "ReplacedPlan/episode-plan.json"


@pytest.mark.asyncio
async def test_director_state_includes_episode_plan_debug_outline(monkeypatch, tmp_path):
    storage = server_module.BridgeStorage(tmp_path / "bridge.db")
    storage.upsert_connector({
        "connector_id": "yt-main",
        "display_name": "YouTube Main",
        "enabled": True,
    })
    storage.upsert_live_episode_plan(sample_plan())
    storage.upsert_session({
        "session_id": "live-a",
        "connector_id": "yt-main",
        "display_name": "Live A",
        "episode_plan_id": "plan-general-panel",
        "episode_plan_handoff_gap_seconds": 4,
        "episode_plan_turn_gap_seconds": 11,
    })
    storage.update_director_state(
        "live-a",
        director_enabled=True,
        status="running",
        last_director_action_at="2026-05-09T00:00:00",
        metadata={
            "last_decision": {
                "episode_plan": {
                    "mode": "planned_turn",
                    "turn_contract": {
                        "output_requirements": {
                            "should_handoff": True,
                            "handoff_target_function": "analyst",
                            "allow_audience_question": False,
                            "must_end_with_question": False,
                        }
                    },
                }
            },
            "planned_state": {
                "plan_id": "plan-general-panel",
                "plan_status": "running",
                "current_segment_index": 0,
                "current_turn_index": 1,
                "completed_segment_ids": [],
                "completed_turn_ids": ["seg_01_turn_01"],
                "completed_turn_types": ["hook"],
                "segment_memory": {},
            }
        },
    )
    monkeypatch.setattr(server_module, "storage", storage)
    monkeypatch.setattr(server_module._director_routes, "storage", storage)

    director = await server_module.get_director_state("live-a")

    debug = director["episode_plan_debug"]
    assert debug["plan_id"] == "plan-general-panel"
    assert debug["plan_status"] == "running"
    assert debug["segments"][0]["status"] == "active"
    assert debug["segments"][0]["turns"][0]["status"] == "completed"
    assert debug["segments"][0]["turns"][1]["status"] == "active"
    assert debug["segments"][0]["turns"][1]["reply_budget"] == {
        "min_replies": 2,
        "max_replies": 3,
        "autonomy": "guided",
    }
    assert debug["next_wait"]["delay_seconds"] == 0
    assert debug["next_wait"]["reason"] == "planned_turn_ready"
    assert debug["next_wait"]["label"] == "企劃立即推進"


@pytest.mark.asyncio
async def test_episode_plan_evidence_import_creates_linked_pack_from_plan_factcards(monkeypatch, tmp_path):
    storage = server_module.BridgeStorage(tmp_path / "bridge.db")
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
    root = tmp_path / "EpisodePlans"
    plan_dir = root / "Test"
    factcards_dir = plan_dir / "factcards"
    factcards_dir.mkdir(parents=True)
    (plan_dir / "episode-plan.json").write_text(
        server_module.json.dumps(sample_plan(), ensure_ascii=False),
        encoding="utf-8",
    )
    (factcards_dir / "evidence.md").write_text(
        "\n".join([
            "# Topic Evidence Card：春番公開排名",
            "",
            "## Summary",
            "本卡只整理可查證資料與網路意見看法，供導播在內容段落選擇資料，不替角色決定立場。",
            "",
            "## Facts",
            "",
            "### 公開週榜：新作聲量是否能支撐內容段落",
            "- 可驗證事實：Anime Corner 第 4 週公開榜單顯示特定新作取得週榜第一，這只能代表該週投票結果。",
            "- 網路意見看法：榜單留言與社群轉貼常把新作聲量視為看點，但這仍只是公開討論氛圍。",
        ]),
        encoding="utf-8",
    )
    storage.upsert_live_episode_plan(
        sample_plan(),
        source_path="Test/episode-plan.json",
    )
    monkeypatch.setattr(server_module, "storage", storage)
    monkeypatch.setattr(server_module._episode_plans_routes, "storage", storage)
    monkeypatch.setattr(server_module._episode_plans_routes, "EPISODE_PLANS_ROOT", root)

    calls: list[dict] = []

    class FakeManager:
        def get_status(self, session_id: str):
            return {"session_id": session_id, "status": "stopped", "running": False}

        def import_fact_cards_folder(self, session_id: str, *, fact_cards_dir, pack_id=None, max_files=50):
            calls.append({
                "session_id": session_id,
                "fact_cards_dir": str(fact_cards_dir),
                "pack_id": pack_id,
                "max_files": max_files,
            })
            storage.link_topic_pack_to_session(session_id, int(pack_id))
            entry = storage.create_topic_pack_entry(int(pack_id), {
                "title": "公開週榜：新作聲量是否能支撐內容段落",
                "body": "可驗證事實與網路意見看法。",
                "source_type": "episode_plan_evidence",
            })
            return {
                "status": "completed",
                "pack_id": int(pack_id),
                "file_count": 1,
                "parsed_file_count": 1,
                "created_count": 1,
                "entries": [entry],
                "graph": {"status": "completed"},
            }

    monkeypatch.setattr(server_module, "manager", FakeManager())
    monkeypatch.setattr(server_module._episode_plans_routes, "manager", server_module.manager)

    result = await server_module.import_episode_plan_evidence(
        "live-a",
        server_module.EpisodePlanEvidenceImportRequest(
            plan_id="plan-general-panel",
            max_files=25,
        ),
    )

    assert calls == [{
        "session_id": "live-a",
        "fact_cards_dir": str(factcards_dir.resolve()),
        "pack_id": result["pack_id"],
        "max_files": 25,
    }]
    pack = storage.get_topic_pack(result["pack_id"])
    assert pack["title"] == "Evidence - 泛用多人節目企劃"
    assert "plan-general-panel" in pack["description"]
    assert result["plan_id"] == "plan-general-panel"
    assert result["fact_cards_dir"] == str(factcards_dir.resolve())
    assert result["created_count"] == 1
    assert [item["id"] for item in storage.list_session_topic_packs("live-a")] == [result["pack_id"]]
