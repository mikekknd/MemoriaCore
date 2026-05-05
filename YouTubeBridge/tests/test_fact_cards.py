import sys
import uuid
import subprocess
from pathlib import Path


BRIDGE_ROOT = Path(__file__).resolve().parents[1]
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))

import fact_cards
from bridge_engine import YouTubeBridgeManager
from fact_cards import (
    DEFAULT_FACT_CARDS_DIR,
    build_gemini_fact_card_prompt,
    generate_fact_card_markdown_with_gemini,
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


def test_default_fact_cards_dir_lives_under_runtime_tree():
    expected = BRIDGE_ROOT.parent / "runtime" / "YouTubeBridge" / "FactCards"

    assert DEFAULT_FACT_CARDS_DIR == expected
    assert not DEFAULT_FACT_CARDS_DIR.is_relative_to(BRIDGE_ROOT)


def test_resolve_gemini_executable_does_not_probe_machine_specific_d_drive(monkeypatch):
    checked: list[str] = []
    machine_specific_prefix = "\\".join(["D:", "AppData", "Roaming", "npm", "gemini"])

    def fake_which(candidate: str) -> str:
        checked.append(str(candidate))
        if str(candidate).startswith(machine_specific_prefix):
            return str(candidate)
        return ""

    monkeypatch.delenv("GEMINI_CLI_PATH", raising=False)
    monkeypatch.setenv("APPDATA", r"C:\Users\alice\AppData\Roaming")
    monkeypatch.setattr(fact_cards.Path, "home", staticmethod(lambda: Path(r"C:\Users\alice")))
    monkeypatch.setattr(fact_cards.Path, "exists", lambda _path: False)
    monkeypatch.setattr(fact_cards.shutil, "which", fake_which)

    resolved = fact_cards._resolve_gemini_executable("gemini")

    assert resolved == "gemini"
    assert all(not path.startswith(machine_specific_prefix) for path in checked)


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
    assert "## Summary" in entries[0]["body"]
    assert "## Facts" in entries[0]["body"]
    assert "耐熱陶瓷內襯" in entries[0]["body"]
    assert "Live Usage" not in entries[0]["body"]
    assert "Keywords" not in entries[0]["body"]


def test_gemini_fact_card_prompt_requires_direct_file_output_and_deep_anime_details():
    prompt = build_gemini_fact_card_prompt(
        topic="動畫新番最新話作畫與劇情討論",
        output_name="anime-detail.md",
        session_title="動畫新番測試台",
        director_guidance="固定討論動畫新番。",
    )

    assert "anime-detail.md" in prompt
    assert "直接建立或覆寫" in prompt
    assert "## Summary" in prompt
    assert "## Facts" in prompt
    assert "最新一話" in prompt
    assert "作畫崩壞" in prompt
    assert "動畫新番" in prompt
    assert "目前工作目錄就是 FactCards 資料夾" in prompt
    assert "禁止提問" in prompt
    assert "console fallback" in prompt
    assert "SourceUrl" in prompt
    assert "Live Usage" in prompt


def test_generate_fact_card_with_gemini_includes_factcards_workspace(monkeypatch):
    tmp_dir = _tmp_dir()
    try:
        cards_dir = tmp_dir / "FactCards"
        calls: list[dict] = []

        def fake_resolve(_executable: str) -> str:
            return "gemini"

        def fake_run(command, *, cwd, text, encoding, errors, capture_output, timeout, input=None):
            calls.append({
                "command": list(command),
                "cwd": Path(cwd),
                "input": input,
                "text": text,
                "encoding": encoding,
                "errors": errors,
                "capture_output": capture_output,
                "timeout": timeout,
            })
            return subprocess.CompletedProcess(command, 0, stdout=_sample_markdown(), stderr="")

        monkeypatch.setattr(fact_cards, "_resolve_gemini_executable", fake_resolve)
        monkeypatch.setattr(fact_cards.subprocess, "run", fake_run)

        result = generate_fact_card_markdown_with_gemini(
            topic="動畫新番最新話細節",
            output_dir=cards_dir,
            output_name="anime-detail.md",
        )

        assert result["file_name"] == "anime-detail.md"
        assert calls
        assert calls[0]["encoding"] == "utf-8"
        assert calls[0]["errors"] == "replace"
        command = calls[0]["command"]
        assert command[command.index("--approval-mode") + 1] == "plan"
        assert command[command.index("--prompt") + 1] == ""
        assert "不要使用檔案工具" in calls[0]["input"]
        assert calls[0]["cwd"] == cards_dir.resolve()
        assert "--include-directories" not in command
    finally:
        import shutil

        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_generate_fact_card_with_gemini_prefers_stdout_markdown(monkeypatch):
    tmp_dir = _tmp_dir()
    try:
        cards_dir = tmp_dir / "FactCards"
        cards_dir.mkdir(parents=True)
        calls: list[dict] = []

        def fake_resolve(_executable: str) -> str:
            return "gemini"

        def fake_run(command, *, cwd, text, encoding, errors, capture_output, timeout, input=None):
            calls.append({
                "command": list(command),
                "cwd": Path(cwd),
                "input": input,
                "encoding": encoding,
                "errors": errors,
            })
            return subprocess.CompletedProcess(command, 0, stdout=_sample_markdown(), stderr="")

        monkeypatch.setattr(fact_cards, "_resolve_gemini_executable", fake_resolve)
        monkeypatch.setattr(fact_cards.subprocess, "run", fake_run)

        result = generate_fact_card_markdown_with_gemini(
            topic="動畫新番最新話細節",
            output_dir=cards_dir,
            output_name="anime-detail.md",
        )

        assert result["file_name"] == "anime-detail.md"
        assert result["fallback_mode"] == "stdout"
        assert (cards_dir / "anime-detail.md").read_text(encoding="utf-8").startswith("# ")
        assert len(calls) == 1
        command = calls[0]["command"]
        assert command[command.index("--approval-mode") + 1] == "plan"
        assert command[command.index("--prompt") + 1] == ""
        assert "--include-directories" not in command
        assert "不要使用檔案工具" in calls[0]["input"]
    finally:
        import shutil

        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_generate_fact_card_with_gemini_strips_cli_warnings_after_stdout_markdown(monkeypatch):
    tmp_dir = _tmp_dir()
    try:
        cards_dir = tmp_dir / "FactCards"
        cards_dir.mkdir(parents=True)

        def fake_resolve(_executable: str) -> str:
            return "gemini"

        def fake_run(command, *, cwd, text, encoding, errors, capture_output, timeout, input=None):
            stdout = (
                _sample_markdown()
                + "\nWarning: 256-color support not detected.\n"
                + "Ripgrep is not available. Falling back to GrepTool.\n"
            )
            return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

        monkeypatch.setattr(fact_cards, "_resolve_gemini_executable", fake_resolve)
        monkeypatch.setattr(fact_cards.subprocess, "run", fake_run)

        result = generate_fact_card_markdown_with_gemini(
            topic="動畫新番最新話細節",
            output_dir=cards_dir,
            output_name="anime-detail.md",
        )

        markdown = (cards_dir / "anime-detail.md").read_text(encoding="utf-8")
        assert result["fallback_mode"] == "stdout"
        assert "Warning:" not in markdown
        assert "Ripgrep is not available" not in markdown
        assert all("Warning:" not in fact.body for fact in result["document"].facts)
    finally:
        import shutil

        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_generate_fact_card_with_gemini_recovers_wrong_output_name(monkeypatch):
    tmp_dir = _tmp_dir()
    try:
        cards_dir = tmp_dir / "FactCards"
        cards_dir.mkdir(parents=True)
        calls: list[list[str]] = []
        monkeypatch.setenv("YOUTUBE_BRIDGE_GEMINI_DIRECT_FILE_FALLBACK", "1")

        def fake_resolve(_executable: str) -> str:
            return "gemini"

        def fake_run(command, *, cwd, text, encoding, errors, capture_output, timeout, input=None):
            calls.append(list(command))
            if command[command.index("--approval-mode") + 1] == "plan":
                return subprocess.CompletedProcess(command, 0, stdout="請問要查哪部作品？", stderr="")
            wrong_path = Path(cwd) / "2026-may-anime-trends-gemini.md"
            nested_path = Path(cwd) / "FactCards" / "2026-may-anime-trends-gemini.md"
            nested_path.parent.mkdir(parents=True, exist_ok=True)
            wrong_path.write_text(_sample_markdown(), encoding="utf-8")
            nested_path.write_text(_sample_markdown(), encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="已寫入其他檔名", stderr="")

        monkeypatch.setattr(fact_cards, "_resolve_gemini_executable", fake_resolve)
        monkeypatch.setattr(fact_cards.subprocess, "run", fake_run)

        result = generate_fact_card_markdown_with_gemini(
            topic="動畫新番最新話細節",
            output_dir=cards_dir,
            output_name="anime-detail.md",
        )

        assert result["file_name"] == "anime-detail.md"
        assert (cards_dir / "anime-detail.md").exists()
        assert not (cards_dir / "2026-may-anime-trends-gemini.md").exists()
        assert not (cards_dir / "FactCards" / "2026-may-anime-trends-gemini.md").exists()
        assert calls[0][calls[0].index("--approval-mode") + 1] == "plan"
        assert calls[1][calls[1].index("--approval-mode") + 1] == "auto_edit"
        assert str(cards_dir.resolve()) in calls[1]
        assert str(cards_dir.resolve().parent) not in calls[1]
    finally:
        import shutil

        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_generate_fact_card_with_gemini_writes_stdout_fallback(monkeypatch):
    tmp_dir = _tmp_dir()
    try:
        cards_dir = tmp_dir / "FactCards"
        cards_dir.mkdir(parents=True)
        calls: list[list[str]] = []
        monkeypatch.setenv("YOUTUBE_BRIDGE_GEMINI_DIRECT_FILE_FALLBACK", "1")

        def fake_resolve(_executable: str) -> str:
            return "gemini"

        def fake_run(command, *, cwd, text, encoding, errors, capture_output, timeout, input=None):
            calls.append(list(command))
            if len(calls) == 1:
                return subprocess.CompletedProcess(command, 0, stdout="我準備好了，但尚未寫檔。", stderr="")
            return subprocess.CompletedProcess(command, 0, stdout=_sample_markdown(), stderr="")

        monkeypatch.setattr(fact_cards, "_resolve_gemini_executable", fake_resolve)
        monkeypatch.setattr(fact_cards.subprocess, "run", fake_run)

        result = generate_fact_card_markdown_with_gemini(
            topic="動畫新番最新話細節",
            output_dir=cards_dir,
            output_name="anime-detail.md",
        )

        assert result["file_name"] == "anime-detail.md"
        assert (cards_dir / "anime-detail.md").exists()
        assert len(calls) == 2
        assert calls[0][calls[0].index("--approval-mode") + 1] == "plan"
        assert calls[1][calls[1].index("--approval-mode") + 1] == "auto_edit"
    finally:
        import shutil

        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_generate_fact_card_with_gemini_retries_invalid_direct_markdown(monkeypatch):
    tmp_dir = _tmp_dir()
    try:
        cards_dir = tmp_dir / "FactCards"
        cards_dir.mkdir(parents=True)
        calls: list[list[str]] = []
        monkeypatch.setenv("YOUTUBE_BRIDGE_GEMINI_DIRECT_FILE_FALLBACK", "1")

        def fake_resolve(_executable: str) -> str:
            return "gemini"

        def fake_run(command, *, cwd, text, encoding, errors, capture_output, timeout, input=None):
            calls.append(list(command))
            if len(calls) == 1:
                return subprocess.CompletedProcess(command, 0, stdout="已寫入", stderr="")
            if len(calls) == 2:
                (Path(cwd) / "anime-detail.md").write_text("# 只有標題，沒有必要欄位", encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, stdout="已寫入", stderr="")
            return subprocess.CompletedProcess(command, 0, stdout=_sample_markdown(), stderr="")

        monkeypatch.setattr(fact_cards, "_resolve_gemini_executable", fake_resolve)
        monkeypatch.setattr(fact_cards.subprocess, "run", fake_run)

        result = generate_fact_card_markdown_with_gemini(
            topic="動畫新番最新話細節",
            output_dir=cards_dir,
            output_name="anime-detail.md",
        )

        assert result["file_name"] == "anime-detail.md"
        assert result["fallback_mode"] == "stdout"
        assert len(calls) == 3
        assert calls[0][calls[0].index("--approval-mode") + 1] == "plan"
        assert calls[1][calls[1].index("--approval-mode") + 1] == "auto_edit"
        assert calls[2][calls[2].index("--approval-mode") + 1] == "plan"
        assert len(result["document"].facts) >= 1
    finally:
        import shutil

        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_generate_fact_card_with_gemini_uses_local_template_when_cli_refuses(monkeypatch):
    tmp_dir = _tmp_dir()
    try:
        cards_dir = tmp_dir / "FactCards"
        cards_dir.mkdir(parents=True)
        calls: list[list[str]] = []

        def fake_resolve(_executable: str) -> str:
            return "gemini"

        def fake_run(command, *, cwd, text, encoding, errors, capture_output, timeout, input=None):
            calls.append(list(command))
            return subprocess.CompletedProcess(command, 0, stdout="請問要針對哪部作品？", stderr="")

        monkeypatch.setattr(fact_cards, "_resolve_gemini_executable", fake_resolve)
        monkeypatch.setattr(fact_cards.subprocess, "run", fake_run)

        result = generate_fact_card_markdown_with_gemini(
            topic="《最新話作畫爭議》角色動作與社群討論",
            output_dir=cards_dir,
            output_name="auto-replenish-test.md",
        )

        assert result["file_name"] == "auto-replenish-test.md"
        assert result["fallback_mode"] == "local_template"
        markdown = (cards_dir / "auto-replenish-test.md").read_text(encoding="utf-8")
        assert markdown.startswith("# ")
        assert "## Summary" in markdown
        assert "## Facts" in markdown
        assert "Gemini CLI 未產生可解析 Markdown" in markdown
        assert len(calls) == 1
        assert calls[0][calls[0].index("--approval-mode") + 1] == "plan"
        assert len(result["document"].facts) >= 5
    finally:
        import shutil

        shutil.rmtree(tmp_dir, ignore_errors=True)


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
