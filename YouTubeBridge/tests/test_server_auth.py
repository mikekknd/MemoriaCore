import sys
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


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
        for name in ("index.css", "core.js", "selectors.js", "topic-packs.js", "control.js", "app.js"):
            path = ui_root / name
            if path.exists():
                parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def test_bridge_key_is_required_even_for_loopback(monkeypatch):
    monkeypatch.setenv("YOUTUBE_BRIDGE_API_KEY", "secret")

    with pytest.raises(HTTPException) as exc:
        require_bridge_key(_request("127.0.0.1"))

    assert exc.value.status_code == 403


def test_bridge_key_accepts_matching_loopback_header(monkeypatch):
    monkeypatch.setenv("YOUTUBE_BRIDGE_API_KEY", "secret")

    require_bridge_key(_request("127.0.0.1", key="secret"))


def test_ui_config_bypasses_key_only_for_loopback(monkeypatch):
    monkeypatch.setenv("YOUTUBE_BRIDGE_API_KEY", "secret")

    require_bridge_key(_request("127.0.0.1", path="/ui-config"))

    with pytest.raises(HTTPException) as exc:
        require_bridge_key(_request("203.0.113.10", path="/ui-config"))

    assert exc.value.status_code == 403


def test_ui_assets_bypass_key_only_for_loopback(monkeypatch):
    monkeypatch.setenv("YOUTUBE_BRIDGE_API_KEY", "secret")

    require_bridge_key(_request("127.0.0.1", path="/ui-assets/app.js"))

    with pytest.raises(HTTPException) as exc:
        require_bridge_key(_request("203.0.113.10", path="/ui-assets/app.js"))

    assert exc.value.status_code == 403


def test_live_page_static_files_are_registered():
    static_root = Path(server_module.STATIC_ROOT)

    assert (static_root / "live.html").exists()
    assert (static_root / "live_chat.html").exists()


def test_memoriacore_launcher_uses_windows_selector_policy_before_uvicorn_import():
    source = (BRIDGE_ROOT.parent / "run_server.py").read_text(encoding="utf-8")

    assert "WindowsSelectorEventLoopPolicy" in source
    assert source.index("WindowsSelectorEventLoopPolicy") < source.index("import uvicorn")


def test_bridge_server_uses_windows_selector_policy_before_uvicorn_import():
    source = (BRIDGE_ROOT / "server.py").read_text(encoding="utf-8")

    assert "WindowsSelectorEventLoopPolicy" in source
    assert source.index("WindowsSelectorEventLoopPolicy") < source.index("import uvicorn")


def test_bridge_hot_reload_does_not_watch_factcard_markdown_files():
    source = (BRIDGE_ROOT / "run_server_hot_reload.py").read_text(encoding="utf-8")

    assert '"*.md"' not in source
    assert '"*.py", "*.html", "*.js", "*.css", "*.json"' in source


def test_bridge_hot_reload_launcher_uses_full_process_tree_cleanup():
    start_script = (BRIDGE_ROOT / "start_hot_reload.bat").read_text(encoding="utf-8")
    stop_script_path = BRIDGE_ROOT / "stop_8091.bat"

    assert stop_script_path.exists()
    assert 'call "%~dp0stop_8091.bat"' in start_script
    assert start_script.index('call "%~dp0stop_8091.bat"') < start_script.index('run_server_hot_reload.py')
    assert "Get-NetTCPConnection -LocalPort %API_PORT% -State Listen" not in start_script
    assert "Stop-Process -Id $_ -Force" not in start_script


def test_stop_8091_script_kills_listener_wrappers_and_worker_tree():
    batch_source = (BRIDGE_ROOT / "stop_8091.bat").read_text(encoding="utf-8")
    source = (BRIDGE_ROOT / "stop_8091.ps1").read_text(encoding="utf-8")

    assert 'set "BRIDGE_ROOT=%~dp0."' in batch_source
    assert "stop_8091.ps1" in batch_source
    assert "Get-NetTCPConnection -LocalPort $Port -State Listen" in source
    assert "Win32_Process" in source
    assert "start_hot_reload.bat" in source
    assert "run_server_hot_reload.py" in source
    assert "ParentProcessId" in source
    assert "taskkill.exe" in source
    assert "/T" in source
    assert "/F" in source
    assert "[KILL]" in source
    assert "[REMAINING]" in source


def test_live_page_propagates_requested_session_id_to_live_chat_frame():
    live_html = (Path(server_module.STATIC_ROOT) / "live.html").read_text(encoding="utf-8")

    assert 'id="liveChatFrame"' in live_html
    assert "URLSearchParams(location.search)" in live_html
    assert "session_id" in live_html


def test_control_ui_honors_requested_session_id_on_initial_load():
    index_html = _control_ui_source()

    assert "function requestedSessionIdFromUrl()" in index_html
    assert "loadSessions(requestedSessionIdFromUrl())" in index_html


def test_control_ui_loads_external_css_and_module_script():
    index_html = (Path(server_module.STATIC_ROOT) / "index.html").read_text(encoding="utf-8")

    assert '<link rel="stylesheet" href="/ui-assets/index.css">' in index_html
    assert '<script type="module" src="/ui-assets/app.js"></script>' in index_html
    assert "<style>" not in index_html
    assert "<script>\n" not in index_html


@pytest.mark.asyncio
async def test_ui_asset_route_serves_split_css_and_js():
    css_response = await server_module.bridge_ui_asset("index.css")
    js_response = await server_module.bridge_ui_asset("app.js")

    assert Path(css_response.path).name == "index.css"
    assert Path(js_response.path).name == "app.js"


@pytest.mark.asyncio
async def test_ui_asset_route_rejects_traversal_and_non_assets():
    with pytest.raises(HTTPException) as traversal_exc:
        await server_module.bridge_ui_asset("../index.html")

    with pytest.raises(HTTPException) as html_exc:
        await server_module.bridge_ui_asset("index.html")

    assert traversal_exc.value.status_code == 404
    assert html_exc.value.status_code == 404


def test_control_ui_delete_session_clears_selection_instead_of_auto_selecting_next_session():
    index_html = _control_ui_source()

    assert 'async function loadSessions(preferredId = "", options = {})' in index_html
    assert "const selectDefault = options.selectDefault !== false" in index_html
    assert 'await loadSessions("", { selectDefault: false })' in index_html
    delete_block = index_html[index_html.index("async function deleteSession"):index_html.index("async function updateSessionSettings")]
    chat_preview_block = index_html[index_html.index("async function refreshChatPreview"):index_html.index("function scheduleChatPreviewRefresh")]
    assert 'id="deleteSessionConfirmText"' not in index_html
    assert 'id="confirmDeleteSession"' not in index_html
    assert "requestDeleteSessionConfirmation" not in index_html
    assert "confirm(" not in delete_block
    assert "prompt(" not in delete_block
    assert "session_id_confirm_mismatch" not in delete_block
    assert "deleteSessionConfirmText" not in delete_block
    assert "newSessionDraft()" in index_html[index_html.index("async function deleteSession"):index_html.index("async function updateSessionSettings")]
    assert '$("deleteSession").onclick = () => deleteSession().catch((error) => log("刪除失敗", String(error)));' in index_html
    assert "defaultLiveSession()" not in chat_preview_block
    assert "fallback.session_id" not in chat_preview_block


def test_control_ui_exposes_fact_cards_folder_import_for_anime_topic_flow():
    index_html = _control_ui_source()

    assert 'id="importFactCardsFolder"' in index_html
    assert 'id="generateGeminiFactCards"' in index_html
    assert 'id="topicAutoBuildControls"' in index_html
    assert 'id="updateTopicPack"' in index_html
    assert 'id="deleteTopicPack"' in index_html
    assert 'id="deleteAllTopicPacks"' in index_html
    assert 'id="updateTopicEntry"' in index_html
    assert 'id="cancelTopicEntryEdit"' in index_html
    assert 'data-delete-topic-entry=' in index_html
    assert 'id="topicEntrySelect"' in index_html
    assert 'class="topic-workspace"' in index_html
    assert 'class="topic-panel topic-pack-panel"' in index_html
    assert 'class="topic-panel topic-entry-panel"' in index_html
    assert 'class="topic-panel topic-ops-panel"' in index_html
    assert 'id="topicPackUsageState"' in index_html
    assert 'data-testid="director-idle-seconds"' in index_html
    assert "PUT" in index_html
    assert "DELETE" in index_html
    assert "/topic-packs/fact-cards/import-folder" in index_html
    assert "/topic-packs/fact-cards/generate" in index_html
    assert "/topic-packs/${packId}" in index_html
    assert 'api("/topic-packs", { method: "DELETE" })' in index_html
    assert "/topic-packs/${packId}/entries/${entryId}" in index_html
    assert "/topic-packs/${packId}/search" in index_html
    assert "/topic-packs/usage" in index_html
    assert "Research Gate" in index_html
    assert "管理備註" in index_html
    assert "生成主題（執行時使用，不會自動儲存）" in index_html
    assert "自動建立張數" in index_html
    assert "依主題自動建立資料卡" in index_html
    assert "依主題生成 Fact Cards" in index_html
    assert "匯入 FactCards 資料夾" in index_html
    assert "初始化預設 Fact Cards" not in index_html
    assert "自動資料卡主題" not in index_html
    assert 'id="researchQuery"' not in index_html
    assert 'id="runResearch"' not in index_html
    assert "Research Gate 查詢" not in index_html
    assert "degraded" in index_html
    assert "可手動重試" in index_html
    topic_pack_delete_block = index_html[
        index_html.index("async function deleteTopicPack"):
        index_html.index("async function linkTopicPack")
    ]
    topic_entry_delete_block = index_html[
        index_html.index("async function deleteTopicEntry"):
        index_html.index("async function autoBuildTopicPack")
    ]
    assert "confirm(" not in topic_pack_delete_block
    assert "window.confirm" not in topic_pack_delete_block
    delete_all_block = index_html[
        index_html.index("async function deleteAllTopicPacks"):
        index_html.index("async function linkTopicPack")
    ]
    assert "confirm(" not in delete_all_block
    assert "window.confirm" not in delete_all_block
    assert "prompt(" not in delete_all_block
    assert "confirm(" not in topic_entry_delete_block
    assert "window.confirm" not in topic_entry_delete_block
    assert "已召回" in index_html
    assert "未使用" in index_html
    assert "最近補卡" in index_html
    assert "四月新番最新話細節、作畫與劇情討論" in index_html
    assert "LLM 基礎、美食直播話題" not in index_html


def test_topic_pack_buttons_are_contextual_in_control_ui():
    index_html = _control_ui_source()

    assert ".is-hidden { display: none !important; }" in index_html
    assert "function updateTopicActionVisibility()" in index_html
    assert 'setTopicActionVisible("createTopicPack", !hasPack);' in index_html
    assert 'setTopicActionVisible("updateTopicPack", hasPack);' in index_html
    assert 'setTopicActionVisible("deleteTopicPack", hasPack);' in index_html
    assert 'setTopicActionVisible("deleteAllTopicPacks", state.topicPacks.length > 0);' in index_html
    assert 'setTopicActionVisible("linkTopicPack", hasPack && hasSession);' in index_html
    assert 'setTopicActionVisible("addTopicEntry", hasPack && !hasEntry);' in index_html
    assert 'setTopicActionVisible("updateTopicEntry", hasPack && hasEntry);' in index_html
    assert 'setTopicActionVisible("cancelTopicEntryEdit", hasPack && hasEntry);' in index_html
    assert 'setTopicActionVisible("deleteTopicEntry", hasPack && hasEntry);' not in index_html
    assert 'setTopicActionVisible("rebuildTopicEmbeddings", hasPack);' in index_html
    assert 'setTopicActionVisible("topicAutoBuildControls", hasSession);' in index_html
    assert 'setTopicActionVisible("autoBuildTopicPack", hasSession);' in index_html
    assert 'setTopicActionVisible("generateGeminiFactCards", true);' in index_html
    assert 'setTopicActionVisible("generateGeminiFactCards", hasSession);' not in index_html
    assert 'setTopicActionVisible("importFactCardsFolder", true);' in index_html
    assert 'setTopicActionVisible("importFactCardsFolder", hasSession);' not in index_html
    assert 'setTopicActionVisible("runResearch", hasSession);' not in index_html
    assert 'setTopicActionVisible("searchTopicPack", hasPack);' in index_html
    assert 'setTopicActionVisible("restoreTopicEntries", hasPack && state.topicEntrySearchActive);' in index_html
    assert '$("topicPackTitle").addEventListener("input", updateTopicActionVisibility);' in index_html
    assert '$("topicEntryTitle").addEventListener("input", updateTopicActionVisibility);' in index_html
    assert '$("topicEntryBody").addEventListener("input", updateTopicActionVisibility);' in index_html
    assert '<button id="createTopicPack" class="primary" disabled>建立</button>' in index_html
    assert '<button id="updateTopicPack" class="is-hidden">儲存</button>' in index_html
    assert '<button id="linkTopicPack" class="blue is-hidden">綁定本場</button>' in index_html
    assert '<button id="deleteAllTopicPacks" class="danger is-hidden">清空所有資料包</button>' in index_html
    assert '<button id="addTopicEntry" class="primary is-hidden">新增</button>' in index_html
    assert '<button id="cancelTopicEntryEdit" class="is-hidden">取消</button>' in index_html
    assert 'id="deleteTopicEntry"' not in index_html
    assert '<button id="searchTopicPack" class="is-hidden">測試向量檢索</button>' in index_html
    assert '<button id="restoreTopicEntries" class="is-hidden">顯示全部</button>' in index_html
    assert '<button id="autoBuildTopicPack" class="primary is-hidden">依主題自動建立資料卡</button>' in index_html
    assert '<button id="generateGeminiFactCards" class="primary is-hidden">依主題生成 Fact Cards</button>' in index_html
    assert '<button id="importFactCardsFolder" class="blue is-hidden">匯入 FactCards 資料夾</button>' in index_html
    init_start = index_html.index("installTestIds();")
    init_block = index_html[init_start:index_html.index("initBridgeKey()", init_start)]
    assert "updateTopicActionVisibility();" in init_block


def test_topic_pack_vector_search_can_restore_full_entry_list():
    index_html = _control_ui_source()
    search_block = index_html[
        index_html.index("async function searchTopicPack"):
        index_html.index("function subscribeEvents")
    ]

    assert "topicEntrySearchActive: false" in index_html
    assert "async function restoreTopicEntries()" in index_html
    assert "state.topicEntrySearchActive = true;" in search_block
    assert "state.topicEntrySearchActive = false;" in search_block
    assert "await refreshTopicEntries();" in search_block
    assert '$("restoreTopicEntries").onclick = () => restoreTopicEntries()' in index_html


def test_topic_pack_rebuild_embeddings_action_lives_with_pack_controls():
    index_html = _control_ui_source()
    pack_panel = index_html[
        index_html.index('<div class="topic-panel topic-pack-panel">'):
        index_html.index('<div class="topic-panel topic-entry-panel">')
    ]
    entry_panel = index_html[
        index_html.index('<div class="topic-panel topic-entry-panel">'):
        index_html.index('<div class="topic-panel topic-ops-panel">')
    ]

    assert '<button id="rebuildTopicEmbeddings" class="is-hidden">重建向量</button>' in pack_panel
    assert 'id="rebuildTopicEmbeddings"' not in entry_panel


def test_topic_pack_entry_list_drives_edit_and_delete_actions():
    index_html = _control_ui_source()

    assert "currentTopicEntryId: 0" in index_html
    assert "function currentTopicEntryId()" in index_html
    assert "function topicEntryById(entryId)" in index_html
    assert "function selectTopicEntryForEditing(entryId)" in index_html
    assert 'data-edit-topic-entry="${escapeHtml(entry.id)}"' in index_html
    assert 'data-delete-topic-entry="${escapeHtml(entry.id)}"' in index_html
    assert "function bindTopicEntryCardButtons()" in index_html
    assert "button.dataset.deleteTopicEntry" in index_html
    assert "deleteTopicEntry(entryId)" in index_html
    assert "const entryId = currentTopicEntryId();" in index_html
    assert "const entry = topicEntryById(entryId) || selectedTopicEntry();" in index_html
    assert "$(\"updateTopicEntry\").onclick = () => updateTopicEntry()" in index_html
    assert "$(\"deleteTopicEntry\").onclick" not in index_html


def test_topic_pack_entry_editor_can_cancel_editing():
    index_html = _control_ui_source()

    assert "function cancelTopicEntryEdit()" in index_html
    assert "fillTopicEntryForm(null);" in index_html[
        index_html.index("function cancelTopicEntryEdit"):
        index_html.index("function topicEntryPreviewText")
    ]
    assert '$("cancelTopicEntryEdit").onclick = () => cancelTopicEntryEdit();' in index_html


def test_topic_pack_entry_save_locks_editor_while_request_is_running():
    index_html = _control_ui_source()
    update_block = index_html[
        index_html.index("async function updateTopicEntry"):
        index_html.index("async function deleteTopicEntry")
    ]

    assert "topicEntryEditorBusy: false" in index_html
    assert "function setTopicEntryEditorBusy(isBusy)" in index_html
    assert '$("topicEntryTitle").disabled = busy;' in index_html
    assert '$("topicEntryBody").disabled = busy;' in index_html
    assert '$("updateTopicEntry").textContent = busy ? "儲存中..." : "儲存";' in index_html
    assert "setTopicEntryEditorBusy(true);" in update_block
    assert "finally {" in update_block
    assert "setTopicEntryEditorBusy(false);" in update_block


def test_fact_card_generation_shows_blocking_progress_and_clears_topic_on_success():
    index_html = _control_ui_source()
    generate_block = index_html[
        index_html.index("async function generateGeminiFactCards"):
        index_html.index("async function rebuildTopicEmbeddings")
    ]

    assert 'id="factCardGenerationOverlay"' in index_html
    assert 'id="factCardGenerationMessage"' in index_html
    assert 'role="progressbar"' in index_html
    assert "factCardGenerationBusy: false" in index_html
    assert "function setFactCardGenerationBusy(isBusy" in index_html
    assert '$("autoBuildTopic").disabled = busy;' in index_html
    assert '$("generateGeminiFactCards").textContent = busy ? "生成中..." : "依主題生成 Fact Cards";' in index_html
    assert "setFactCardGenerationBusy(true" in generate_block
    assert 'log("Gemini FactCards 開始產生"' in generate_block
    assert '$("autoBuildTopic").value = "";' in generate_block
    assert "finally {" in generate_block
    assert "setFactCardGenerationBusy(false);" in generate_block


def test_topic_pack_entry_save_clears_editor_after_success():
    index_html = _control_ui_source()
    update_block = index_html[
        index_html.index("async function updateTopicEntry"):
        index_html.index("async function deleteTopicEntry")
    ]

    assert 'log("fact card 已更新，已清空編輯區", data);' in update_block
    assert "await refreshTopicEntries();" in update_block
    assert "fillTopicEntryForm(null);" in update_block
    assert "selectTopicEntryForEditing(entryId);" not in update_block
    assert update_block.index("await refreshTopicEntries();") < update_block.index("fillTopicEntryForm(null);")


def test_topic_pack_entry_editor_hides_system_metadata_fields():
    index_html = _control_ui_source()

    assert 'id="topicEntrySelectorRow" class="is-hidden"' in index_html
    assert 'id="topicEntryMetadataFields" class="is-hidden" aria-hidden="true"' in index_html
    assert 'id="topicEntrySourceType" type="hidden"' in index_html
    assert 'id="topicEntryTags" type="hidden"' in index_html
    assert 'id="topicEntrySourceUrl" type="hidden"' in index_html
    assert "<label>類型" not in index_html
    assert "<label>標籤" not in index_html
    assert "<label>來源" not in index_html
    assert "function topicEntryPreviewText(entry)" in index_html
    assert "${escapeHtml(entry.body)}</p>" not in index_html
    assert "topicEntryPreviewText(entry)" in index_html


@pytest.mark.asyncio
async def test_delete_session_endpoint_returns_deleted_session_id(monkeypatch, tmp_path):
    storage = server_module.BridgeStorage(tmp_path / "bridge.db")
    storage.upsert_connector({
        "connector_id": "yt-main",
        "display_name": "YouTube Main",
        "api_key": "key",
        "enabled": True,
    })
    storage.upsert_session({
        "session_id": "live-a",
        "connector_id": "yt-main",
    })
    monkeypatch.setattr(server_module, "storage", storage)

    stopped: list[str] = []

    class FakeManager:
        async def stop_session(self, session_id: str):
            stopped.append(session_id)

    monkeypatch.setattr(server_module, "manager", FakeManager())

    result = await server_module.delete_session("live-a")

    assert result == {"deleted": True, "session_id": "live-a"}
    assert stopped == ["live-a"]
    assert storage.get_session("live-a") is None


@pytest.mark.asyncio
async def test_topic_pack_edit_endpoints_update_entry_and_reindex(monkeypatch, tmp_path):
    storage = server_module.BridgeStorage(tmp_path / "bridge.db")
    storage.upsert_connector({
        "connector_id": "yt-main",
        "display_name": "YouTube Main",
        "api_key": "key",
        "enabled": True,
    })
    storage.upsert_session({
        "session_id": "live-a",
        "connector_id": "yt-main",
    })
    pack = storage.create_topic_pack({"title": "舊資料包", "description": "舊描述"})
    entry = storage.create_topic_pack_entry(pack["id"], {
        "title": "舊標題",
        "body": "舊內容",
        "source_type": "manual",
    })
    storage.upsert_topic_pack_entry_embedding(entry["id"], [1.0, 0.0], model="old", content_hash="old")
    monkeypatch.setattr(server_module, "storage", storage)

    indexed: list[int] = []

    class FakeManager:
        def index_topic_pack_entry(self, entry_id: int):
            indexed.append(entry_id)
            return storage.upsert_topic_pack_entry_embedding(entry_id, [0.0, 1.0], model="fake")

    monkeypatch.setattr(server_module, "manager", FakeManager())

    updated_pack = await server_module.update_topic_pack(
        pack["id"],
        server_module.TopicPackUpdateRequest(title="新資料包", description="新描述"),
    )
    updated_entry = await server_module.update_topic_pack_entry(
        pack["id"],
        entry["id"],
        server_module.TopicPackEntryUpdateRequest(
            title="新標題",
            body="新內容",
            source_url="",
            source_type="edited",
            tags=["anime"],
        ),
    )

    assert updated_pack["title"] == "新資料包"
    assert updated_pack["description"] == "新描述"
    assert updated_entry["title"] == "新標題"
    assert updated_entry["embedding_status"] == "indexed"
    assert "embedding" not in updated_entry
    assert indexed == [entry["id"]]
    assert storage.get_topic_pack_entry_embedding(entry["id"])["embedding_model"] == "fake"


@pytest.mark.asyncio
async def test_topic_pack_delete_entry_endpoint_removes_entry(monkeypatch, tmp_path):
    storage = server_module.BridgeStorage(tmp_path / "bridge.db")
    pack = storage.create_topic_pack({"title": "資料包"})
    entry = storage.create_topic_pack_entry(pack["id"], {
        "title": "標題",
        "body": "內容",
    })
    storage.upsert_topic_pack_entry_embedding(entry["id"], [1.0, 0.0], model="old", content_hash="old")
    monkeypatch.setattr(server_module, "storage", storage)

    result = await server_module.delete_topic_pack_entry(pack["id"], entry["id"])

    assert result == {"status": "deleted", "pack_id": pack["id"], "entry_id": entry["id"]}
    assert storage.get_topic_pack_entry(entry["id"]) is None
    assert storage.get_topic_pack_entry_embedding(entry["id"]) is None


@pytest.mark.asyncio
async def test_topic_pack_delete_endpoint_removes_pack_and_related_rows(monkeypatch, tmp_path):
    storage = server_module.BridgeStorage(tmp_path / "bridge.db")
    storage.upsert_connector({
        "connector_id": "yt-main",
        "display_name": "YouTube Main",
        "api_key": "key",
        "enabled": True,
    })
    storage.upsert_session({
        "session_id": "live-a",
        "connector_id": "yt-main",
    })
    pack = storage.create_topic_pack({"title": "可刪除資料包", "description": "測試刪除"})
    entry = storage.create_topic_pack_entry(pack["id"], {
        "title": "可刪除 fact card",
        "body": "刪除資料包時應一起移除。",
        "source_type": "manual",
    })
    storage.link_topic_pack_to_session("live-a", pack["id"])
    storage.upsert_topic_pack_entry_embedding(entry["id"], [1.0, 0.0], model="fake", content_hash="hash")
    storage.record_topic_pack_entry_usages(
        "live-a",
        [{"id": entry["id"], "pack_id": pack["id"], "similarity": 0.75}],
        query_text="刪除測試",
        usage_source="manual_search",
    )
    storage.create_research_request(
        "live-a",
        "刪除資料包 research link",
        status="completed_with_results",
        result_entry_id=entry["id"],
    )
    monkeypatch.setattr(server_module, "storage", storage)

    result = await server_module.delete_topic_pack(pack["id"])

    assert result == {"status": "deleted", "pack_id": pack["id"], "entry_count": 1}
    assert storage.get_topic_pack(pack["id"]) is None
    assert storage.get_topic_pack_entry(entry["id"]) is None
    assert storage.get_topic_pack_entry_embedding(entry["id"]) is None
    assert storage.list_session_topic_packs("live-a") == []
    assert storage.get_topic_pack_usage_stats("live-a")["entries"] == []
    research = storage.list_research_requests("live-a", limit=5)[0]
    assert research["result_entry_id"] is None


@pytest.mark.asyncio
async def test_topic_pack_delete_all_endpoint_removes_every_pack_and_related_rows(monkeypatch, tmp_path):
    storage = server_module.BridgeStorage(tmp_path / "bridge.db")
    storage.upsert_connector({
        "connector_id": "yt-main",
        "display_name": "YouTube Main",
        "api_key": "key",
        "enabled": True,
    })
    storage.upsert_session({
        "session_id": "live-a",
        "connector_id": "yt-main",
    })
    first_pack = storage.create_topic_pack({"title": "第一包"})
    second_pack = storage.create_topic_pack({"title": "第二包"})
    first_entry = storage.create_topic_pack_entry(first_pack["id"], {"title": "一", "body": "內容一"})
    second_entry = storage.create_topic_pack_entry(second_pack["id"], {"title": "二", "body": "內容二"})
    storage.link_topic_pack_to_session("live-a", first_pack["id"])
    storage.link_topic_pack_to_session("live-a", second_pack["id"])
    storage.upsert_topic_pack_entry_embedding(first_entry["id"], [1.0, 0.0], model="fake", content_hash="one")
    storage.upsert_topic_pack_entry_embedding(second_entry["id"], [0.0, 1.0], model="fake", content_hash="two")
    storage.record_topic_pack_entry_usages(
        "live-a",
        [
            {"id": first_entry["id"], "pack_id": first_pack["id"], "similarity": 0.8},
            {"id": second_entry["id"], "pack_id": second_pack["id"], "similarity": 0.7},
        ],
        query_text="清空測試",
        usage_source="manual_search",
    )
    storage.create_research_request(
        "live-a",
        "清空所有資料包 research link",
        status="completed_with_results",
        result_entry_id=first_entry["id"],
    )
    monkeypatch.setattr(server_module, "storage", storage)

    result = await server_module.delete_all_topic_packs()

    assert result == {"status": "deleted", "pack_count": 2, "entry_count": 2}
    assert storage.list_topic_packs() == []
    assert storage.get_topic_pack_entry(first_entry["id"]) is None
    assert storage.get_topic_pack_entry(second_entry["id"]) is None
    assert storage.get_topic_pack_entry_embedding(first_entry["id"]) is None
    assert storage.get_topic_pack_entry_embedding(second_entry["id"]) is None
    assert storage.list_session_topic_packs("live-a") == []
    assert storage.get_topic_pack_usage_stats("live-a")["entries"] == []
    research = storage.list_research_requests("live-a", limit=5)[0]
    assert research["result_entry_id"] is None


@pytest.mark.asyncio
async def test_topic_pack_search_endpoint_searches_selected_pack_without_live_session(monkeypatch, tmp_path):
    storage = server_module.BridgeStorage(tmp_path / "bridge.db")
    pack = storage.create_topic_pack({"title": "可測試資料包"})
    anime = storage.create_topic_pack_entry(pack["id"], {
        "title": "四月新番",
        "body": "動畫新番、作畫與最新一話劇情討論。",
        "source_type": "manual",
    })
    food = storage.create_topic_pack_entry(pack["id"], {
        "title": "美食",
        "body": "拉麵與甜點討論。",
        "source_type": "manual",
    })
    storage.upsert_topic_pack_entry_embedding(anime["id"], [1.0, 0.0], model="fake", content_hash="anime")
    storage.upsert_topic_pack_entry_embedding(food["id"], [0.0, 1.0], model="fake", content_hash="food")
    monkeypatch.setattr(server_module, "storage", storage)

    class FakeManager:
        def _embed_text(self, text: str, *, timeout_seconds: int = 20):
            return {"dense": [0.95, 0.05], "model": "fake-query"}

    monkeypatch.setattr(server_module, "manager", FakeManager())

    result = await server_module.search_topic_pack(pack["id"], query="最新一話 作畫", limit=1)

    assert result["pack_id"] == pack["id"]
    assert result["embedding_model"] == "fake-query"
    assert len(result["entries"]) == 1
    assert result["entries"][0]["id"] == anime["id"]
    assert result["entries"][0]["similarity"] > 0.99


@pytest.mark.asyncio
async def test_fact_cards_folder_import_endpoint_initializes_pack_without_live_session(monkeypatch):
    calls: list[dict] = []

    class FakeManager:
        def import_fact_cards_folder_to_pack(self, *, pack_id: int | None = None, max_files: int = 50):
            calls.append({"pack_id": pack_id, "max_files": max_files})
            return {
                "status": "completed",
                "pack_id": pack_id or 42,
                "created_count": 3,
                "embedding_count": 3,
            }

    monkeypatch.setattr(server_module, "manager", FakeManager())

    result = await server_module.import_fact_cards_folder_to_pack(
        server_module.FactCardImportRequest(pack_id=None, max_files=25)
    )

    assert calls == [{"pack_id": None, "max_files": 25}]
    assert result["pack_id"] == 42
    assert result["created_count"] == 3


@pytest.mark.asyncio
async def test_fact_cards_generate_endpoint_initializes_pack_without_live_session(monkeypatch):
    calls: list[dict] = []

    class FakeManager:
        def generate_fact_cards_with_gemini_to_pack(
            self,
            *,
            topic: str,
            pack_id: int | None = None,
            output_name: str | None = None,
            timeout_seconds: int = 300,
        ):
            calls.append({
                "topic": topic,
                "pack_id": pack_id,
                "output_name": output_name,
                "timeout_seconds": timeout_seconds,
            })
            return {
                "status": "completed",
                "topic": topic,
                "file_name": "anime-topic.md",
                "import": {"pack_id": pack_id or 77, "created_count": 2},
            }

    monkeypatch.setattr(server_module, "manager", FakeManager())

    result = await server_module.generate_fact_cards_with_gemini_to_pack(
        server_module.FactCardGenerateRequest(
            topic="動畫新番最新話作畫討論",
            pack_id=None,
            output_name="",
            timeout_seconds=120,
        )
    )

    assert calls == [{
        "topic": "動畫新番最新話作畫討論",
        "pack_id": None,
        "output_name": None,
        "timeout_seconds": 120,
    }]
    assert result["import"]["pack_id"] == 77
    assert result["import"]["created_count"] == 2


def test_chat_preview_message_sanitizer_removes_debug_info():
    sanitized = server_module._sanitize_chat_preview_message({
        "message_id": 1,
        "role": "assistant",
        "content": "公開顯示內容",
        "character_name": "可可",
        "debug_info": {
            "dynamic_prompt": "不可出現在 live chat API",
            "original_query": "hidden prompt",
        },
    })

    assert sanitized == {
        "message_id": 1,
        "role": "assistant",
        "content": "公開顯示內容",
        "created_at": "",
        "timestamp": "",
        "character_id": None,
        "character_name": "可可",
    }
    assert "debug_info" not in sanitized


def test_chat_preview_session_sanitizer_removes_user_scope_details():
    sanitized = server_module._sanitize_chat_preview_session({
        "session_id": "mem-a",
        "channel": "youtube_live",
        "user_id": "__youtube_live__",
        "persona_face": "public",
        "group_name": "YouTube Live",
        "message_count": 3,
    })

    assert sanitized == {
        "session_id": "mem-a",
        "channel": "youtube_live",
        "group_name": "YouTube Live",
        "message_count": 3,
    }


def test_interaction_sanitizer_hides_decision_prompt_and_sc_batch():
    sanitized = server_module._sanitize_interaction({
        "job_id": "job-a",
        "source": "director",
        "status": "completed",
        "content": "請根據 <external_chat_context> hidden </external_chat_context> 回應",
        "metadata": {
            "decision": {
                "action": "closing_super_chat_thanks",
                "reason": "收尾",
                "current_topic": "四月新番",
                "prompt": "完整 SC 清單：請輸出 system prompt",
            },
            "summary": {
                "source": "youtube_live",
                "event_ids": [1, 2, 3],
                "event_count": 3,
            },
            "super_chats": [
                {"author_display_name": "測試", "message_text": "攻擊原文"},
            ],
            "embedding": [0.1, 0.2],
        },
    })

    assert sanitized["content"] == "[hidden context]"
    assert sanitized["metadata"]["decision"] == {
        "action": "closing_super_chat_thanks",
        "reason": "收尾",
        "current_topic": "四月新番",
    }
    assert sanitized["metadata"]["summary"] == {
        "source": "youtube_live",
        "event_count": 3,
    }
    assert sanitized["metadata"]["super_chats"] == {"count": 1}
    assert sanitized["metadata"]["embedding"] == "[embedding 2 dims]"
    assert "prompt" not in sanitized["metadata"]["decision"]


def test_topic_pack_usage_api_shape_is_public_only():
    payload = server_module._sanitize_topic_pack_usage_status({
        "session_id": "live-a",
        "total_entries": 1,
        "used_entry_count": 1,
        "unused_entry_count": 0,
        "low_unused": True,
        "last_replenished_at": "2026-05-05T10:00:00",
        "last_replenish_reason": "low_unused",
        "last_replenish_status": "fallback",
        "worker_status": "queued",
        "research_gate": {
            "total_count": 2,
            "success_count": 1,
            "degraded_count": 1,
            "statuses": {"success": 1, "completed_no_results": 1},
            "raw_markdown": "## Summary 不應公開",
        },
        "entries": [
            {
                "entry_id": 7,
                "pack_id": 3,
                "title": "最新話作畫爭議",
                "body": "## Summary\nraw markdown 不應出現在 usage API",
                "embedding": [0.1, 0.2],
                "usage_count": 2,
                "avg_similarity": 0.88,
                "last_used_at": "2026-05-05T10:01:00",
                "usage_sources": ["external_context"],
            }
        ],
        "recent_usage": [
            {
                "entry_id": 7,
                "query_text": "<topic_pack_fact_cards>raw</topic_pack_fact_cards>",
                "similarity": 0.9,
                "usage_source": "external_context",
                "created_at": "2026-05-05T10:01:00",
            }
        ],
    })

    dumped = str(payload)
    assert "raw markdown" not in dumped
    assert "embedding" not in dumped
    assert "<topic_pack_fact_cards>" not in dumped
    assert "不應公開" not in dumped
    assert payload["worker_status"] == "queued"
    assert payload["research_gate"] == {
        "total_count": 2,
        "success_count": 1,
        "degraded_count": 1,
        "statuses": {"success": 1, "completed_no_results": 1},
    }
    assert payload["entries"][0] == {
        "entry_id": 7,
        "pack_id": 3,
        "title": "最新話作畫爭議",
        "source_type": "",
        "usage_count": 2,
        "avg_similarity": 0.88,
        "last_used_at": "2026-05-05T10:01:00",
        "usage_sources": ["external_context"],
    }


def test_manual_research_endpoint_bypasses_auto_build_cooldown():
    source = Path(server_module.__file__).read_text(encoding="utf-8")

    assert "enforce_cooldown=False" in source


def test_e2e_checkpoint_helper_tracks_resume_fields(tmp_path):
    storage = server_module.BridgeStorage(tmp_path / "bridge.db")
    storage.upsert_connector({
        "connector_id": "yt-main",
        "display_name": "YouTube Main",
        "api_key": "key",
        "enabled": True,
    })
    storage.upsert_session({
        "session_id": "live-a",
        "connector_id": "yt-main",
        "video_id": "video-a",
        "live_chat_id": "chat-a",
        "status": "running",
    })
    pack = storage.create_topic_pack({"title": "動畫新番資料包"})
    storage.link_topic_pack_to_session("live-a", pack["id"])
    event = storage.save_event({
        "bridge_session_id": "live-a",
        "message_text": "最新一話作畫如何？",
        "author_display_name": "viewer",
        "amount_micros": 0,
        "priority_class": "normal",
    })
    storage.mark_events_injected("live-a", [int(event["id"])])

    checkpoint = server_module._build_e2e_checkpoint(storage, "live-a")

    assert checkpoint["session_id"] == "live-a"
    assert checkpoint["topic_pack_id"] == pack["id"]
    assert checkpoint["status"] == "running"
    assert checkpoint["last_message_count"] == 1
    assert checkpoint["last_sc_count"] == 0
    assert checkpoint["can_resume"] is True
