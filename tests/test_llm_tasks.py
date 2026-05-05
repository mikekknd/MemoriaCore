import threading

import pytest

from api.models.requests import PromptJsonRequest
from api.routers import llm_tasks


class _Template:
    def format(self, **variables):
        return variables["content"]


class _PromptManager:
    def get(self, key):
        assert key == "test_prompt"
        return _Template()


class _ThreadRecordingRouter:
    def __init__(self):
        self.thread_ids = []
        self.calls = []

    def generate_json(self, task_key, messages, schema=None, temperature=0.1, log_context=None):
        self.thread_ids.append(threading.get_ident())
        self.calls.append({
            "task_key": task_key,
            "messages": messages,
            "schema": schema,
            "temperature": temperature,
            "log_context": log_context,
        })
        return {"ok": True}


@pytest.mark.asyncio
async def test_prompt_json_runs_router_generation_off_event_loop(monkeypatch):
    router = _ThreadRecordingRouter()
    event_loop_thread_id = threading.get_ident()
    monkeypatch.setattr(llm_tasks, "get_prompt_manager", lambda: _PromptManager())
    monkeypatch.setattr(llm_tasks, "get_router", lambda: router)

    response = await llm_tasks.generate_prompt_json(
        PromptJsonRequest(
            prompt_key="test_prompt",
            variables={"content": "請輸出 JSON"},
            task_key="router",
            temperature=0.0,
            schema={"type": "object"},
        ),
        _current_user={"user_id": "admin"},
    )

    assert response["result"] == {"ok": True}
    assert router.thread_ids
    assert router.thread_ids[0] != event_loop_thread_id
    assert router.calls[0]["messages"] == [{"role": "user", "content": "請輸出 JSON"}]
    assert router.calls[0]["log_context"]["prompt_key"] == "test_prompt"
