import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


BRIDGE_ROOT = Path(__file__).resolve().parents[1]
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))


def _request(host: str, key: str = "", path: str = ""):
    return SimpleNamespace(
        client=SimpleNamespace(host=host),
        headers={"X-Bridge-Key": key} if key else {},
        url=SimpleNamespace(path=path) if path else None,
    )


def test_server_security_module_matches_bridge_key_contract(monkeypatch):
    from server_security import require_bridge_key

    monkeypatch.setenv("YOUTUBE_BRIDGE_API_KEY", "secret")

    require_bridge_key(_request("127.0.0.1", path="/ui-assets/app.js"))

    with pytest.raises(HTTPException) as exc:
        require_bridge_key(_request("203.0.113.10", path="/ui-assets/app.js"))

    assert exc.value.status_code == 403


def test_server_presenter_hides_interaction_hidden_context():
    from server_presenters import sanitize_interaction

    public = sanitize_interaction({
        "job_id": "job-a",
        "session_id": "live-a",
        "status": "completed",
        "request_text": "<external_chat_context>secret</external_chat_context>",
        "response_text": "公開回答",
        "metadata": {
            "external_context": {"raw": "hidden"},
            "visible_events": [{"message_text": "hello"}],
        },
    })

    assert public["request_text"] == "[hidden context]"
    assert public["response_text"] == "公開回答"
    assert public["metadata"]["external_context"] == "[hidden]"
    assert public["metadata"]["visible_events"] == [{"message_text": "hello"}]
