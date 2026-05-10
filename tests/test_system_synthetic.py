import pytest

from api.models.requests import SyntheticRequest
from api.routers import system


def test_system_synthetic_router_exposes_analyzer_dependency():
    assert hasattr(system, "get_analyzer")


@pytest.mark.asyncio
async def test_synthetic_data_uses_tools_package_module(monkeypatch):
    calls = []

    def fake_generate_synthetic_data(topic, turns, memory_sys, analyzer, router, sim_timestamp=None):
        calls.append({
            "topic": topic,
            "turns": turns,
            "memory_sys": memory_sys,
            "analyzer": analyzer,
            "router": router,
            "sim_timestamp": sim_timestamp,
        })
        return True, "ok overview", {"ignored": True}

    import tools.synthetic as synthetic

    monkeypatch.setattr(system, "require_db_writes_enabled", lambda: None)
    monkeypatch.setattr(system, "get_memory_sys", lambda: "memory")
    monkeypatch.setattr(system, "get_analyzer", lambda: "analyzer", raising=False)
    monkeypatch.setattr(system, "get_router", lambda: "router")
    monkeypatch.setattr(synthetic, "generate_synthetic_data", fake_generate_synthetic_data)

    response = await system.synthetic_data(
        SyntheticRequest(topic="測試主題", turns=3, sim_timestamp="2026-05-07T00:00:00")
    )

    assert response == {"status": "success", "overview": "ok overview"}
    assert calls == [{
        "topic": "測試主題",
        "turns": 3,
        "memory_sys": "memory",
        "analyzer": "analyzer",
        "router": "router",
        "sim_timestamp": "2026-05-07T00:00:00",
    }]
