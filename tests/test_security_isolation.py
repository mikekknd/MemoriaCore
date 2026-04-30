import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from core.background_gatherer import _resolve_background_gather_scope
from core.storage_manager import GLOBAL_TOPIC_CHARACTER_ID, StorageManager
from PersonaProbe.probe_engine import _messages_to_text, load_fragments_from_db


def _test_dir() -> Path:
    path = Path(".pyTestTemp") / "security-isolation" / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=True)
    return path


def _slot(city_temp: float) -> dict:
    return {
        "time": datetime.now().strftime("%Y-%m-%d %H:00"),
        "weather": "晴",
        "temp": city_temp,
        "humidity": 60,
        "wind": 2.0,
        "pop": 10,
    }


def test_load_fragments_keeps_other_assistants_as_context_in_group_session():
    base = _test_dir()
    db_path = base / "conversation.db"
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        "CREATE TABLE conversation_sessions ("
        "session_id TEXT PRIMARY KEY, user_id TEXT, character_id TEXT, "
        "channel_class TEXT, session_mode TEXT)"
    )
    cur.execute(
        "CREATE TABLE conversation_session_participants ("
        "session_id TEXT, character_id TEXT, is_active INTEGER DEFAULT 1)"
    )
    cur.execute(
        "CREATE TABLE conversation_messages ("
        "msg_id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, "
        "content TEXT, character_id TEXT)"
    )
    cur.execute(
        "INSERT INTO conversation_sessions VALUES (?, ?, ?, ?, ?)",
        ("sid", "user-1", "char-a", "public", "group"),
    )
    cur.executemany(
        "INSERT INTO conversation_session_participants VALUES (?, ?, 1)",
        [("sid", "char-a"), ("sid", "char-b")],
    )
    cur.executemany(
        "INSERT INTO conversation_messages (session_id, role, content, character_id) VALUES (?, ?, ?, ?)",
        [
            ("sid", "user", "使用者問題", None),
            ("sid", "assistant", "A 的回答", "char-a"),
            ("sid", "assistant", "B 的回答", "char-b"),
            ("sid", "user", "追問", None),
        ],
    )
    conn.commit()
    conn.close()

    messages = load_fragments_from_db(
        str(db_path),
        channel_class_filter=["public"],
        character_id="char-b",
    )

    assert [(m["role"], m["content"]) for m in messages] == [
        ("user", "使用者問題"),
        ("context", "A 的回答"),
        ("assistant", "B 的回答"),
        ("user", "追問"),
    ]
    assert messages[1]["context_type"] == "other_ai"
    assert messages[1]["character_id"] == "char-a"

    text = _messages_to_text(messages)
    assert "上下文（char-a，非分析對象）：A 的回答" in text
    assert "AI：B 的回答" in text


def test_load_fragments_includes_exited_character_by_message_speaker_id():
    base = _test_dir()
    db_path = base / "conversation.db"
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        "CREATE TABLE conversation_sessions ("
        "session_id TEXT PRIMARY KEY, user_id TEXT, character_id TEXT, "
        "channel_class TEXT, session_mode TEXT)"
    )
    cur.execute(
        "CREATE TABLE conversation_session_participants ("
        "session_id TEXT, character_id TEXT, is_active INTEGER DEFAULT 1)"
    )
    cur.execute(
        "CREATE TABLE conversation_messages ("
        "msg_id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, "
        "content TEXT, character_id TEXT)"
    )
    cur.execute(
        "INSERT INTO conversation_sessions VALUES (?, ?, ?, ?, ?)",
        ("sid", "user-1", "char-a", "public", "single"),
    )
    cur.executemany(
        "INSERT INTO conversation_session_participants VALUES (?, ?, ?)",
        [("sid", "char-a", 1), ("sid", "char-b", 0)],
    )
    cur.executemany(
        "INSERT INTO conversation_messages (session_id, role, content, character_id) VALUES (?, ?, ?, ?)",
        [
            ("sid", "user", "使用者問題", None),
            ("sid", "assistant", "A 的回答", "char-a"),
            ("sid", "system_event", "AI 成員變更：退出 角色 B", None),
            ("sid", "assistant", "B 退出前的回答", "char-b"),
        ],
    )
    conn.commit()
    conn.close()

    messages = load_fragments_from_db(
        str(db_path),
        channel_class_filter=["public"],
        character_id="char-b",
    )

    assert [(m["role"], m["content"]) for m in messages] == [
        ("user", "使用者問題"),
        ("context", "A 的回答"),
        ("assistant", "B 退出前的回答"),
    ]


def test_storage_counts_only_target_assistant_messages():
    base = _test_dir()
    storage = StorageManager(
        prefs_file=str(base / "prefs.json"),
        history_file=str(base / "history.json"),
        persona_snapshot_db_path=str(base / "persona.db"),
    )
    storage._CONV_DB = str(base / "conversation.db")
    storage.create_conversation_session(
        "sid",
        user_id="1",
        character_id="char-a",
        character_ids=["char-a", "char-b"],
        channel_class="public",
        session_mode="group",
    )
    storage.save_conversation_message("sid", "user", "hi")
    storage.save_conversation_message("sid", "assistant", "a", character_id="char-a")
    storage.save_conversation_message("sid", "assistant", "b", character_id="char-b")

    assert storage.count_messages_since_by_character_and_channel_class(
        "1970-01-01T00:00:00", "char-a", "public"
    ) == 1
    assert storage.count_messages_since_by_character_and_channel_class(
        "1970-01-01T00:00:00", "char-b", "public"
    ) == 1
    assert storage.get_last_message_time_by_character_and_channel_class("char-b", "public") is not None
    assert set(storage.list_conversation_character_ids()) == {"char-a", "char-b"}
    assert set(storage.list_recent_conversation_character_ids()) == {"char-a", "char-b"}


def test_run_bash_requires_admin_runtime_context(monkeypatch):
    from tools.bash_tool import run_bash

    class FakeStorage:
        def load_prefs(self):
            return {"bash_tool_allowed_commands": ["echo"]}

        def get_user_by_id(self, user_id):
            if str(user_id) == "1":
                return {"id": 1, "role": "admin"}
            return {"id": user_id, "role": "user"}

    monkeypatch.setattr("core.storage_manager.StorageManager", lambda: FakeStorage())

    denied = json.loads(run_bash("echo hello", runtime_context={"user_id": "2"}))
    assert "權限不足" in denied["error"]

    allowed = json.loads(run_bash("echo hello", runtime_context={"user_id": "1"}))
    assert "hello" in allowed["output"].lower()


def test_execute_tool_call_passes_runtime_context_to_bash(monkeypatch):
    import tools.bash_tool
    from tools.tavily import execute_tool_call

    captured = {}

    def fake_run_bash(command, runtime_context=None):
        captured["command"] = command
        captured["runtime_context"] = runtime_context
        return "{}"

    monkeypatch.setattr(tools.bash_tool, "run_bash", fake_run_bash)
    execute_tool_call(
        {"function": {"name": "run_bash", "arguments": {"command": "echo hi"}}},
        runtime_context={"user_id": "1"},
    )

    assert captured == {"command": "echo hi", "runtime_context": {"user_id": "1"}}


def test_background_gather_scope_uses_first_admin_private():
    class FakeStorage:
        def get_first_admin_user(self):
            return {"id": 7, "role": "admin"}

    assert _resolve_background_gather_scope(FakeStorage()) == ("7", GLOBAL_TOPIC_CHARACTER_ID, "private")


def test_background_gather_scope_skips_when_no_admin():
    class FakeStorage:
        def get_first_admin_user(self):
            return None

    assert _resolve_background_gather_scope(FakeStorage()) is None


def test_proactive_topics_include_global_pool_for_character():
    base = _test_dir()
    storage = StorageManager(
        prefs_file=str(base / "prefs.json"),
        history_file=str(base / "history.json"),
        persona_snapshot_db_path=str(base / "persona.db"),
    )
    db_path = str(base / "memory.db")
    storage.insert_topic_cache(
        db_path, "global-topic", "tea", "global summary",
        user_id="7", character_id=GLOBAL_TOPIC_CHARACTER_ID, visibility="private",
    )
    storage.insert_topic_cache(
        db_path, "char-topic", "coffee", "char summary",
        user_id="7", character_id="char-a", visibility="private",
    )
    storage.insert_topic_cache(
        db_path, "other-topic", "books", "other summary",
        user_id="7", character_id="char-b", visibility="private",
    )

    topics = storage.get_unmentioned_topics(
        db_path, user_id="7", character_id="char-a",
        visibility_filter=["private"], include_global=True, limit=10,
    )

    assert [t["topic_id"] for t in topics] == ["char-topic", "global-topic"]


def test_weather_cache_keeps_multiple_cities(monkeypatch):
    from tools.weather_cache import WeatherCache

    base = _test_dir()
    wc = WeatherCache(str(base / "weather_cache.json"))

    def fake_fetch(city, api_key):
        return [_slot(25.0 if city == "Taipei" else 18.0)], "TW" if city == "Taipei" else "JP"

    monkeypatch.setattr(wc, "_fetch_today_forecast", fake_fetch)

    assert wc.ensure_today("Taipei", "key") is True
    assert wc.ensure_today("Tokyo", "key") is True

    data = json.loads((base / "weather_cache.json").read_text(encoding="utf-8"))
    assert set(data["cities"]) == {"Taipei", "Tokyo"}
    assert wc.get_full_today("Taipei")[0]["temp"] == 25.0
    assert wc.get_full_today("Tokyo")[0]["temp"] == 18.0


def test_weather_cache_reads_legacy_single_city_format():
    from tools.weather_cache import WeatherCache

    base = _test_dir()
    cache_file = base / "weather_cache.json"
    cache_file.write_text(
        json.dumps(
            {
                "date": datetime.now().strftime("%Y-%m-%d"),
                "city": "Taipei",
                "country": "TW",
                "fetched_at": "",
                "slots": [_slot(25.0)],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    wc = WeatherCache(str(cache_file))
    assert wc.get_full_today("Taipei")[0]["temp"] == 25.0
    assert "Taipei" in wc.get_current_slot("Taipei")
