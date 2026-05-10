import sys
import uuid
from pathlib import Path


BRIDGE_ROOT = Path(__file__).resolve().parents[1]
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))

from bridge_engine import YouTubeBridgeManager
from fact_cards import (
    DEFAULT_FACT_CARDS_DIR,
    parse_fact_card_markdown,
)
from storage import BridgeStorage


class FakeEmbeddingMemoriaClient:
    def embed_text(self, text: str, model: str = ""):
        return {"dense": [1.0, 0.25, 0.5], "model": model or "fake-embed"}


def _tmp_dir() -> Path:
    path = Path(".pyTestTemp") / "youtube-bridge" / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=False)
    return path


def _sample_markdown() -> str:
    return """# 2026 四月新番最新話細節

## Summary
本檔案整理四月新番最新話的劇情、製作與社群討論焦點。

## Facts
### 第 6 集火箭引擎測試與特殊材料
- 劇情細節：科學王國開始進行小型燃燒室測試，先用耐熱陶瓷內襯降低噴嘴燒蝕。
- 材料看點：鋁土礦、石灰石與高純度碳源被拆成三條收集線，讓角色分工自然產生衝突。
- 可展開觀點：直播可以討論動畫如何把工程流程拆成角色戲，而不是只做名詞解說。

### 最新話作畫崩壞爭議
- 畫面細節：遠景人物線條簡化，跑動 cut 的手部比例在社群截圖中被放大討論。
- 可展開觀點：可以比較崩壞是排程壓力、演出取捨，還是觀眾用截圖脫離動態觀看造成的誤差。

## Live Usage
- 這段不應進入資料卡。

## Keywords
- 這段也不應進入資料卡。
"""


def _overview_graph_markdown() -> str:
    return """# 2026 春番 5 月初最新熱門話題

## Summary
本卡整理春番最新熱門話題，先用大分類帶出作品主軸。

## Facts
### 《魔法帽的工作室》（Witch Hat Atelier）：精緻奇幻新作正式攻頂
- 排名細節：Anime Corner 第 4 週以 9.20% 拿下第 1，第一次把《Re:從零開始的異世界生活 第四季》從週榜冠軍位置拉下來。
- 製作亮點：第 5 話前後仍維持高討論度。
- 社群討論角度：新作是否真的能挑戰續作霸權。
- 可展開觀點：你覺得新作攻頂代表觀眾口味改變嗎？

### 《Re:從零開始的異世界生活 第四季》：續作霸權仍然是春番基本盤
- 排名細節：前三週連續拿下冠軍，第 4 週仍保住前段班位置。
- 製作亮點：長篇續作靠角色痛苦與伏筆回收維持討論中心。
- 社群討論角度：老粉穩定追，動畫新觀眾可能有門檻。
- 可展開觀點：長篇續作一定要每週都有爆點嗎？
"""


def _deep_graph_markdown() -> str:
    return """# 2026-05-07《魔法帽的工作室》第 5 話資料

## Summary
本卡以《魔法帽的工作室》在 5 月初攻頂後的深度討論為核心。

## Facts
### 第 5 話「巨鱗龍迷宮」：龍、迷宮與可可的臨場創意成為事件核心
- 劇情細節：可可、阿加特、緹緹亞與莉潔被困在不可思議空間中，巨鱗龍阻擋她們靠近魔法陣。
- 製作亮點：官方公開約 6 分鐘高潮片段，集中展示迷宮、龍與奇弗利登場。
- 社群討論角度：這段同時展示孩子們靠規則解題，以及老師級角色收束危機。
- 可展開觀點：你更喜歡可可她們靠創意解危機，還是奇弗利出手的師父感？
"""


def test_default_fact_cards_dir_lives_under_runtime_tree():
    expected = BRIDGE_ROOT.parent / "runtime" / "YouTubeBridge" / "FactCards"

    assert DEFAULT_FACT_CARDS_DIR == expected
    assert not DEFAULT_FACT_CARDS_DIR.is_relative_to(BRIDGE_ROOT)


def test_parse_fact_card_markdown_keeps_only_summary_and_facts():
    document = parse_fact_card_markdown(_sample_markdown(), source_name="anime-detail.md")

    assert document.title == "2026 四月新番最新話細節"
    assert document.summary == "本檔案整理四月新番最新話的劇情、製作與社群討論焦點。"
    assert [fact.title for fact in document.facts] == [
        "第 6 集火箭引擎測試與特殊材料",
        "最新話作畫崩壞爭議",
    ]

    entries = document.to_topic_pack_entries()

    assert entries[0]["title"] == "第 6 集火箭引擎測試與特殊材料"
    assert entries[0]["source_url"] == ""
    assert entries[0]["source_type"] == "factcards_folder"
    assert "anime_new_release" in entries[0]["tags"]
    assert "## Summary" not in entries[0]["body"]
    assert "## Facts" not in entries[0]["body"]
    assert "耐熱陶瓷內襯" in entries[0]["body"]
    assert "跑動 cut 的手部比例" not in entries[0]["body"]
    assert "跑動 cut 的手部比例" in entries[1]["body"]
    assert entries[0]["body"] != entries[1]["body"]
    assert "Live Usage" not in entries[0]["body"]
    assert "Keywords" not in entries[0]["body"]


def test_import_fact_cards_folder_creates_linked_topic_pack_entries_and_embeddings():
    tmp_dir = _tmp_dir()
    try:
        cards_dir = tmp_dir / "FactCards"
        cards_dir.mkdir()
        (cards_dir / "anime-detail.md").write_text(_sample_markdown(), encoding="utf-8")

        storage = BridgeStorage(tmp_dir / "youtube_live.db")
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
            "display_name": "動畫新番測試台",
            "director_guidance": "固定討論動畫新番。",
        })

        result = YouTubeBridgeManager(
            storage,
            memoria_client_factory=FakeEmbeddingMemoriaClient,
        ).import_fact_cards_folder("live-a", fact_cards_dir=cards_dir)

        assert result["status"] == "completed"
        assert result["created_count"] == 2
        assert result["embedding_count"] == 2
        assert result["failed_count"] == 0
        assert result["file_count"] == 1

        packs = storage.list_session_topic_packs("live-a")
        assert len(packs) == 1
        assert packs[0]["title"] == "動畫新番 FactCards"

        entries = storage.list_session_topic_pack_entries("live-a")
        assert [entry["title"] for entry in entries] == [
            "第 6 集火箭引擎測試與特殊材料",
            "最新話作畫崩壞爭議",
        ]
        assert entries[0]["source_type"] == "factcards_folder"
        assert storage.get_topic_pack_entry_embedding(entries[0]["id"])["embedding_dim"] == 3
    finally:
        import shutil

        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_import_fact_cards_folder_builds_topic_graph_from_overview_and_deep_dive_cards():
    tmp_dir = _tmp_dir()
    try:
        cards_dir = tmp_dir / "FactCards"
        cards_dir.mkdir()
        (cards_dir / "index-20260506-overview.md").write_text(_overview_graph_markdown(), encoding="utf-8")
        (cards_dir / "20260507-magic-hat-episode5.md").write_text(_deep_graph_markdown(), encoding="utf-8")

        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        manager = YouTubeBridgeManager(
            storage,
            memoria_client_factory=FakeEmbeddingMemoriaClient,
        )
        result = manager.import_fact_cards_folder_to_pack(fact_cards_dir=cards_dir)

        assert result["graph"]["node_count"] >= 7
        assert result["graph"]["edge_count"] >= 5
        graph = storage.get_topic_graph(result["pack_id"])
        nodes = graph["nodes"]
        edges = graph["edges"]
        node_by_id = {node["id"]: node for node in nodes}

        assert any(node["node_type"] == "document" and node["source_name"] == "index-20260506-overview.md" for node in nodes)
        assert any(node["node_type"] == "category" and "春番" in node["title"] for node in nodes)
        assert any(node["node_type"] == "topic" and "魔法帽" in node["title"] for node in nodes)
        assert any(node["node_type"] == "topic" and "Re:從零" in node["title"] for node in nodes)
        assert any(node["node_type"] == "detail" and "巨鱗龍迷宮" in node["title"] for node in nodes)
        assert any(node["node_type"] == "entity" and "魔法帽" in node["title"] for node in nodes)
        category_node = next(node for node in nodes if node["node_type"] == "category")
        document_nodes = [node for node in nodes if node["node_type"] == "document"]
        source_file_targets = {
            edge["target_node_id"]
            for edge in edges
            if edge["edge_type"] == "source_file" and edge["source_node_id"] == category_node["id"]
        }
        assert source_file_targets >= {node["id"] for node in document_nodes}
        entries = storage.list_topic_pack_entries(result["pack_id"], limit=20)
        magic_entry = next(entry for entry in entries if "魔法帽" in entry["title"])
        detail_entry = next(entry for entry in entries if "巨鱗龍迷宮" in entry["title"])
        assert "topic_graph_role:entry" in magic_entry["tags"]
        assert "topic_graph_role:detail" in detail_entry["tags"]

        detail_edges = [
            edge for edge in edges
            if edge["edge_type"] == "detail_of"
            and "巨鱗龍迷宮" in node_by_id[edge["source_node_id"]]["title"]
        ]
        assert detail_edges
        assert any("魔法帽" in node_by_id[edge["target_node_id"]]["title"] for edge in detail_edges)
        assert any(
            edge["edge_type"] == "source_of"
            and node_by_id[edge["source_node_id"]]["node_type"] == "document"
            and "magic-hat" in node_by_id[edge["source_node_id"]]["source_name"]
            and "魔法帽" in node_by_id[edge["target_node_id"]]["title"]
            for edge in edges
        )

        assert any(
            edge["edge_type"] == "mentions"
            and "魔法帽" in node_by_id[edge["source_node_id"]]["title"]
            and "Re:從零" in node_by_id[edge["target_node_id"]]["title"]
            for edge in edges
        )
        assert any(
            edge["edge_type"] == "compare_with"
            and "魔法帽" in node_by_id[edge["source_node_id"]]["title"]
            and "Re:從零" in node_by_id[edge["target_node_id"]]["title"]
            for edge in edges
        )

        rebuilt = manager.rebuild_topic_graph_for_pack(result["pack_id"])
        assert rebuilt["node_count"] >= 6
        rebuilt_graph = storage.get_topic_graph(result["pack_id"])
        assert any(
            node["node_type"] == "topic" and "魔法帽" in node["title"]
            for node in rebuilt_graph["nodes"]
        )
        assert any(
            node["node_type"] == "detail" and "巨鱗龍迷宮" in node["title"]
            for node in rebuilt_graph["nodes"]
        )
    finally:
        import shutil

        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_import_fact_cards_folder_creates_dedicated_pack_when_session_has_other_pack():
    tmp_dir = _tmp_dir()
    try:
        cards_dir = tmp_dir / "FactCards"
        cards_dir.mkdir()
        (cards_dir / "anime-detail.md").write_text(_sample_markdown(), encoding="utf-8")

        storage = BridgeStorage(tmp_dir / "youtube_live.db")
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
            "display_name": "動畫新番測試台",
            "director_guidance": "固定討論動畫新番。",
        })
        generic_pack = storage.create_topic_pack({
            "title": "泛用直播資料包",
            "description": "不是 FactCards 專用包。",
        })
        storage.link_topic_pack_to_session("live-a", int(generic_pack["id"]))

        result = YouTubeBridgeManager(
            storage,
            memoria_client_factory=FakeEmbeddingMemoriaClient,
        ).import_fact_cards_folder("live-a", fact_cards_dir=cards_dir)

        assert result["status"] == "completed"
        assert result["created_count"] == 2
        assert storage.list_topic_pack_entries(int(generic_pack["id"])) == []

        packs = storage.list_session_topic_packs("live-a")
        factcards_pack = next(pack for pack in packs if pack["title"] == "動畫新番 FactCards")
        entries = storage.list_topic_pack_entries(int(factcards_pack["id"]))
        assert [entry["title"] for entry in entries] == [
            "第 6 集火箭引擎測試與特殊材料",
            "最新話作畫崩壞爭議",
        ]
    finally:
        import shutil

        shutil.rmtree(tmp_dir, ignore_errors=True)
