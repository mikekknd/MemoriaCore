"""router_hints 測試：地點偵測與多源線索整合。"""
from unittest.mock import patch

from core.chat_orchestrator.router_hints import (
    _extract_locations,
    build_router_context_hints,
)


class TestExtractLocations:
    def test_chinese_city(self):
        msgs = [{"role": "user", "content": "我在台中"}]
        assert "台中" in _extract_locations(msgs)

    def test_english_city(self):
        msgs = [{"role": "user", "content": "I'm at Taichung now"}]
        assert "台中" in _extract_locations(msgs)

    def test_multiple_cities_dedup_keeps_order(self):
        msgs = [
            {"role": "user", "content": "上週去了台中"},
            {"role": "user", "content": "現在到了高雄"},
            {"role": "user", "content": "再回台中"},
        ]
        out = _extract_locations(msgs)
        assert out == ["台中", "高雄"]

    def test_no_match_returns_empty(self):
        msgs = [{"role": "user", "content": "今天好累"}]
        assert _extract_locations(msgs) == []

    def test_traditional_taipei_synonyms(self):
        msgs = [{"role": "user", "content": "搬到臺北"}]
        assert "台北" in _extract_locations(msgs)


class TestBuildRouterContextHints:
    def test_profile_location_extracted(self):
        msgs = [{"role": "user", "content": "你好"}]
        profile = [
            {"category": "basic_info", "fact_key": "location", "fact_value": "台中"},
        ]
        hints = build_router_context_hints(msgs, user_prefs={}, session_ctx={}, profile_facts=profile)
        assert hints.get("user_profile_location") == "台中"

    def test_recent_mentions_picked_up(self):
        msgs = [
            {"role": "user", "content": "上週去了高雄"},
            {"role": "user", "content": "外面下大雨"},
        ]
        hints = build_router_context_hints(msgs, user_prefs={}, session_ctx={})
        assert "高雄" in hints.get("recent_mentions", "")

    def test_su_weather_city_only_for_su_private(self):
        # 非 SU private face：不暴露 weather_city
        with patch("core.chat_orchestrator.router_hints._is_su_private_weather_context", return_value=False):
            hints = build_router_context_hints(
                session_messages=[],
                user_prefs={"weather_city": "Taipei"},
                session_ctx={"user_id": "regular", "persona_face": "public"},
            )
        assert "su_weather_city" not in hints

        # SU private face：暴露 weather_city
        with patch("core.chat_orchestrator.router_hints._is_su_private_weather_context", return_value=True):
            hints = build_router_context_hints(
                session_messages=[],
                user_prefs={"weather_city": "Taipei"},
                session_ctx={"user_id": "su", "persona_face": "private"},
            )
        assert hints.get("su_weather_city") == "Taipei"

    def test_empty_inputs_returns_empty_dict(self):
        hints = build_router_context_hints(session_messages=[], user_prefs=None, session_ctx=None)
        assert hints == {}

    def test_combined_profile_and_recent(self):
        msgs = [{"role": "user", "content": "在宜蘭吃飯"}]
        profile = [
            {"category": "basic_info", "fact_key": "city", "fact_value": "台北"},
        ]
        hints = build_router_context_hints(msgs, user_prefs={}, session_ctx={}, profile_facts=profile)
        assert hints.get("user_profile_location") == "台北"
        assert "宜蘭" in hints.get("recent_mentions", "")

    def test_profile_fact_unrelated_key_ignored(self):
        msgs = [{"role": "user", "content": "嗨"}]
        profile = [
            {"category": "basic_info", "fact_key": "favorite_food", "fact_value": "壽司"},
        ]
        hints = build_router_context_hints(msgs, user_prefs={}, session_ctx={}, profile_facts=profile)
        assert "user_profile_location" not in hints
