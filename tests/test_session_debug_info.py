from api.routers import session


def test_visible_debug_info_strips_llm_trace_for_non_admin():
    message = {
        "debug_info": {
            "llm_trace": {"llm_call_id": "call-1"},
            "perf_timing": {"total_ms": 12, "steps": []},
        }
    }

    visible = session._visible_debug_info(message, {"role": "user"})

    assert visible == {"perf_timing": {"total_ms": 12, "steps": []}}
    assert "llm_trace" in message["debug_info"]


def test_visible_debug_info_keeps_llm_trace_for_admin():
    message = {
        "debug_info": {
            "llm_trace": {"llm_call_id": "call-1"},
            "perf_timing": {"total_ms": 12, "steps": []},
        }
    }

    visible = session._visible_debug_info(message, {"role": "admin"})

    assert visible == message["debug_info"]
