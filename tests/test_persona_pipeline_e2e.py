"""人格演化 Path D — Pipeline E2E 測試。

覆蓋 ``_run_probe_sync`` 的完整流程（mock 3 次 LLM + 假 conversation.db）：
- V1 分支：無活躍 trait → 走 ``build_trait_v1_prompt`` → 套 ``TRAIT_V1_SCHEMA``
- Vn 分支：已有活躍 trait → 走 ``build_trait_vn_prompt`` → 套 ``TRAIT_VN_SCHEMA``
- 驗證 builder → parser → store 整條鏈路（不叫 run_sync，繞開 asyncio / CharacterManager）

Mock 策略：
- ``llm_client.LLMClient`` 換成 ``FakeLLMClient``，按 queue 順序吐回應
- ``api.dependencies.get_storage`` 換成 ``tmp_path`` 後端的真 StorageManager
- ``core.persona_sync._PROBE_DIR`` 暫時指到 tmp_path，避免污染真實 PersonaProbe/result
"""
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_PROBE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "PersonaProbe")
if _PROBE_DIR not in sys.path:
    sys.path.insert(0, _PROBE_DIR)

from core.storage_manager import StorageManager


CHAR = "char-e2e-test"


# ──────────────────────────────────────────────
# 假 LLM Client — 按 queue 順序吐預先排好的 response
# ──────────────────────────────────────────────

class FakeLLMClient:
    def __init__(self, config, responses: list[str]):
        self.config = config
        self._queue = list(responses)
        self.calls: list[dict] = []

    def chat(self, messages, stream=False, temperature=None,
             max_tokens=None, response_format=None):
        self.calls.append({
            "messages": messages,
            "response_format": response_format,
        })
        if not self._queue:
            raise RuntimeError("FakeLLMClient response queue 已空")
        return self._queue.pop(0)


# ──────────────────────────────────────────────
# 建假 conversation.db（PersonaProbe load_fragments_from_db 會讀）
# ──────────────────────────────────────────────

def _seed_conversation_db(path: str):
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute(
        "CREATE TABLE IF NOT EXISTS conversation_messages ("
        " msg_id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " session_id TEXT,"
        " role TEXT,"
        " content TEXT"
        ")"
    )
    rows = [
        ("sess", "user", "我最近工作壓力很大，常常睡不好"),
        ("sess", "assistant", "聽起來是長期累積的焦慮"),
        ("sess", "user", "你不要離開我就好"),
    ]
    cur.executemany(
        "INSERT INTO conversation_messages (session_id, role, content) VALUES (?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


# ──────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────

@pytest.fixture
def storage(tmp_path):
    return StorageManager(
        prefs_file=str(tmp_path / "prefs.json"),
        history_file=str(tmp_path / "history.json"),
        persona_snapshot_db_path=str(tmp_path / "persona.db"),
    )


@pytest.fixture
def db_path(tmp_path):
    p = tmp_path / "conversation.db"
    _seed_conversation_db(str(p))
    return str(p)


@pytest.fixture
def patch_env(monkeypatch, storage, tmp_path):
    """統一 patch：LLMClient 工廠 / get_storage / _PROBE_DIR 輸出目錄。

    回傳 factory：``patch_env(responses) → list[FakeLLMClient]``。
    呼叫 factory 後的第一個（通常唯一一個）FakeLLMClient 才會被 _run_probe_sync 使用。
    """
    import api.dependencies as deps
    import core.persona_sync as psync
    import llm_client

    # 隔離輸出目錄到 tmp_path，不污染真實 PersonaProbe/result
    monkeypatch.setattr(psync, "_PROBE_DIR", str(tmp_path))

    # get_storage 換成 tmp storage
    monkeypatch.setattr(deps, "get_storage", lambda: storage)

    instances: list[FakeLLMClient] = []

    def _factory(responses: list[str]):
        def _ctor(config):
            fake = FakeLLMClient(config, responses)
            instances.append(fake)
            return fake

        monkeypatch.setattr(llm_client, "LLMClient", _ctor)
        return instances

    return _factory


# ──────────────────────────────────────────────
# V1 分支
# ──────────────────────────────────────────────

class TestV1Pipeline:
    def test_v1_flow_returns_three_root_traits(self, db_path, patch_env, storage):
        """V1：LLM 回 3 個 root trait → TraitDiff 的 new_traits 應有 3 筆、updates 為空。"""
        from core.persona_sync import _run_probe_sync

        trait_json = json.dumps({
            "new_traits": [
                {"name": "壓力依附", "description": "壓力下主動索取情感支持", "confidence": "high"},
                {"name": "焦慮睡眠", "description": "情緒緊繃影響睡眠品質", "confidence": "medium"},
                {"name": "分離恐懼", "description": "擔心重要他人離去", "confidence": "high"},
            ]
        }, ensure_ascii=False)

        patch_env([trait_json, "## 報告\n此次觀察到三種模式。", "# Persona\nbody"])

        result = _run_probe_sync(
            db_path=db_path,
            existing_persona="# 舊 Persona",
            llm_provider="ollama",
            model_name="test-model",
            ollama_url="http://localhost:11434",
            or_key="",
            active_character_id=CHAR,
        )

        assert result["persona"].startswith("# Persona")
        td = result["trait_diff"]
        assert len(td.updates) == 0
        assert len(td.new_traits) == 3
        assert {t.name for t in td.new_traits} == {"壓力依附", "焦慮睡眠", "分離恐懼"}
        # V1 時 pipeline 看到的 active_traits 為空
        assert result["active_traits"] == []
        assert result["summary"]  # 首段非空
        # _run_probe_sync 不負責 save，DB 仍應乾淨
        assert storage.get_active_traits(CHAR) == []

    def test_v1_first_llm_call_uses_v1_schema(self, db_path, patch_env):
        """第 1 次 LLM 呼叫應帶 ``TRAIT_V1_SCHEMA``；prompt 不含活躍清單區塊。"""
        from core.persona_evolution.extractor import TRAIT_V1_SCHEMA
        from core.persona_sync import _run_probe_sync

        trait_json = json.dumps({
            "new_traits": [{"name": "A", "description": "a", "confidence": "high"}]
        }, ensure_ascii=False)

        instances = patch_env([trait_json, "report", "# Persona"])

        _run_probe_sync(
            db_path=db_path, existing_persona="", llm_provider="ollama",
            model_name="m", ollama_url="", or_key="",
            active_character_id=CHAR,
        )

        client = instances[0]
        assert len(client.calls) == 3  # trait diff + report + persona.md
        first = client.calls[0]
        assert first["response_format"] == TRAIT_V1_SCHEMA
        user_content = first["messages"][1]["content"]
        assert "活躍 trait 清單" not in user_content

    def test_v1_writes_output_files(self, db_path, patch_env, tmp_path):
        """persona.md / probe-report.md / fragment-input.md 都應寫到 tmp_path/result/ 下。"""
        from core.persona_sync import _run_probe_sync

        trait_json = json.dumps({
            "new_traits": [{"name": "X", "description": "x", "confidence": "low"}]
        }, ensure_ascii=False)

        patch_env([trait_json, "REPORT-BODY-uniq", "# PERSONA-BODY-uniq"])

        result = _run_probe_sync(
            db_path=db_path, existing_persona="", llm_provider="ollama",
            model_name="m", ollama_url="", or_key="",
            active_character_id=CHAR,
        )

        out_dir = Path(result["output_dir"])
        assert out_dir.exists()
        assert out_dir.is_relative_to(tmp_path)
        assert "PERSONA-BODY-uniq" in (out_dir / "persona.md").read_text(encoding="utf-8")
        assert "REPORT-BODY-uniq" in (out_dir / "probe-report.md").read_text(encoding="utf-8")
        assert (out_dir / "fragment-input.md").exists()


# ──────────────────────────────────────────────
# Vn 分支
# ──────────────────────────────────────────────

class TestVnPipeline:
    def test_vn_flow_uses_active_traits(self, db_path, patch_env, storage):
        """預 seed 一個活躍 trait → Vn 分支應帶活躍清單進 prompt、用 ``TRAIT_VN_SCHEMA``。"""
        from core.persona_evolution.extractor import TRAIT_VN_SCHEMA
        from core.persona_evolution.snapshot_store import PersonaSnapshotStore
        from core.persona_evolution.trait_diff import NewTrait, TraitDiff
        from core.persona_sync import _run_probe_sync

        # 用 store 預寫 V1（實際 DB 操作，不經 _run_probe_sync）
        store = PersonaSnapshotStore(storage)
        store.save_snapshot(
            character_id=CHAR,
            trait_diff=TraitDiff(new_traits=[
                NewTrait(name="既有依附", description="既有依附傾向", confidence="medium"),
            ]),
            summary="seed",
            evolved_prompt="# persona v1",
        )
        active = store.list_active_traits(CHAR)
        assert len(active) == 1
        existing_key = active[0]["trait_key"]

        vn_json = json.dumps({
            "updates": [{"trait_key": existing_key, "confidence": "high"}],
            "new_traits": [{
                "name": "追問式依附",
                "description": "會反覆確認對方不會離開",
                "parent_key": existing_key,
                "confidence": "high",
            }],
        }, ensure_ascii=False)

        instances = patch_env([vn_json, "report v2", "# persona v2"])

        result = _run_probe_sync(
            db_path=db_path,
            existing_persona="# persona v1",
            llm_provider="openrouter",
            model_name="anthropic/claude",
            ollama_url="",
            or_key="sk-test",
            active_character_id=CHAR,
        )

        td = result["trait_diff"]
        assert len(td.updates) == 1
        assert td.updates[0].trait_key == existing_key
        assert td.updates[0].confidence == "high"
        assert len(td.new_traits) == 1
        assert td.new_traits[0].parent_key == existing_key
        assert len(result["active_traits"]) == 1

        client = instances[0]
        assert len(client.calls) == 3
        first = client.calls[0]
        assert first["response_format"] == TRAIT_VN_SCHEMA
        user_content = first["messages"][1]["content"]
        assert "活躍 trait 清單" in user_content
        assert existing_key in user_content  # trait_key 出現在 prompt（供 LLM 引用）

    def test_vn_persona_md_receives_report_content(self, db_path, patch_env, storage):
        """第 3 次 LLM (persona.md) 的 user content 應含第 2 次 report 回傳內容。"""
        from core.persona_evolution.snapshot_store import PersonaSnapshotStore
        from core.persona_evolution.trait_diff import NewTrait, TraitDiff
        from core.persona_sync import _run_probe_sync

        store = PersonaSnapshotStore(storage)
        store.save_snapshot(
            character_id=CHAR,
            trait_diff=TraitDiff(new_traits=[
                NewTrait(name="A", description="a", confidence="medium")
            ]),
            summary="s", evolved_prompt="# p",
        )

        vn_json = json.dumps({"updates": [], "new_traits": []}, ensure_ascii=False)
        unique_report = "## 報告\nUNIQUE-MARKER-xyz123\n內容"

        instances = patch_env([vn_json, unique_report, "# persona v2"])

        _run_probe_sync(
            db_path=db_path, existing_persona="", llm_provider="ollama",
            model_name="m", ollama_url="", or_key="",
            active_character_id=CHAR,
        )

        persona_call = instances[0].calls[2]
        persona_user = persona_call["messages"][1]["content"]
        assert "UNIQUE-MARKER-xyz123" in persona_user

    def test_vn_report_call_receives_trait_diff_json(self, db_path, patch_env, storage):
        """第 2 次 LLM (report) 應把 TraitDiff 以 JSON 形式帶入 prompt。"""
        from core.persona_evolution.snapshot_store import PersonaSnapshotStore
        from core.persona_evolution.trait_diff import NewTrait, TraitDiff
        from core.persona_sync import _run_probe_sync

        store = PersonaSnapshotStore(storage)
        store.save_snapshot(
            character_id=CHAR,
            trait_diff=TraitDiff(new_traits=[
                NewTrait(name="既有", description="既有描述", confidence="medium"),
            ]),
            summary="s", evolved_prompt="# p",
        )
        existing_key = store.list_active_traits(CHAR)[0]["trait_key"]

        vn_json = json.dumps({
            "updates": [],
            "new_traits": [{
                "name": "UNIQUE-TRAIT-NAME-Q42",
                "description": "UNIQUE-DESC-marker",
                "parent_key": existing_key,
                "confidence": "low",
            }],
        }, ensure_ascii=False)

        instances = patch_env([vn_json, "report", "# persona"])

        _run_probe_sync(
            db_path=db_path, existing_persona="", llm_provider="ollama",
            model_name="m", ollama_url="", or_key="",
            active_character_id=CHAR,
        )

        report_call = instances[0].calls[1]
        report_user = report_call["messages"][1]["content"]
        assert "UNIQUE-TRAIT-NAME-Q42" in report_user
        assert "UNIQUE-DESC-marker" in report_user


# ──────────────────────────────────────────────
# 邊界 / 錯誤路徑
# ──────────────────────────────────────────────

class TestEdgeCases:
    def test_empty_conversation_db_raises(self, tmp_path, patch_env):
        """conversation_messages 為空 → ValueError。"""
        from core.persona_sync import _run_probe_sync

        db = tmp_path / "empty.db"
        conn = sqlite3.connect(str(db))
        conn.execute(
            "CREATE TABLE conversation_messages ("
            " msg_id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " session_id TEXT, role TEXT, content TEXT)"
        )
        conn.commit()
        conn.close()

        patch_env(["never-called"])

        with pytest.raises(ValueError, match="沒有對話記錄"):
            _run_probe_sync(
                db_path=str(db), existing_persona="", llm_provider="ollama",
                model_name="m", ollama_url="", or_key="",
                active_character_id=CHAR,
            )

    def test_summary_skips_markdown_headers(self, db_path, patch_env):
        """summary 應跳過 ``#`` 開頭與空行，取第一段純文字。"""
        from core.persona_sync import _run_probe_sync

        trait_json = json.dumps({
            "new_traits": [{"name": "X", "description": "x", "confidence": "low"}]
        }, ensure_ascii=False)

        report = "# 標題\n\n## 子標題\n\n這是第一段純文字敘述\n\n第二段"
        patch_env([trait_json, report, "# Persona"])

        result = _run_probe_sync(
            db_path=db_path, existing_persona="", llm_provider="ollama",
            model_name="m", ollama_url="", or_key="",
            active_character_id=CHAR,
        )

        assert result["summary"] == "這是第一段純文字敘述"
