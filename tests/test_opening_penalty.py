from core.opening_penalty import OpeningPenaltyManager


def test_extract_opening_skips_wrappers_and_speaker_tags():
    mgr = OpeningPenaltyManager(opening_chars=4)

    assert mgr.extract_opening("  「哼...荒謬。」") == "哼..."
    assert mgr.extract_opening("[白蓮|char-a]: （皺眉）荒謬！這不對。") == "荒謬！"
    assert mgr.extract_opening("   ...") == ""


def test_state_is_scoped_by_session_character_and_face():
    mgr = OpeningPenaltyManager()

    mgr.record_reply(
        session_id="s1",
        character_id="lotus",
        persona_face="public",
        reply_text="哼...本座知道。",
    )

    assert mgr.get_blocked_openings(
        session_id="s1",
        character_id="lotus",
        persona_face="public",
    ) == ("哼...",)
    assert mgr.get_blocked_openings(
        session_id="s1",
        character_id="coco",
        persona_face="public",
    ) == ()
    assert mgr.get_blocked_openings(
        session_id="s2",
        character_id="lotus",
        persona_face="public",
    ) == ()


def test_recent_openings_are_limited_and_newest_first():
    mgr = OpeningPenaltyManager(recent_limit=3, opening_chars=4)

    for text in ["哼...一", "呵...二", "荒謬！三", "那個...四"]:
        mgr.record_reply(
            session_id="s1",
            character_id="c1",
            persona_face="public",
            reply_text=text,
        )

    assert mgr.get_blocked_openings(
        session_id="s1",
        character_id="c1",
        persona_face="public",
    ) == ("那個..", "荒謬！", "呵...")


def test_ttl_prunes_old_state(monkeypatch):
    mgr = OpeningPenaltyManager(ttl_seconds=10)
    now = [1000.0]
    monkeypatch.setattr("core.opening_penalty.time.time", lambda: now[0])

    mgr.record_reply(
        session_id="s1",
        character_id="c1",
        persona_face="public",
        reply_text="嗚...好難。",
    )
    now[0] = 1011.0

    assert mgr.get_blocked_openings(
        session_id="s1",
        character_id="c1",
        persona_face="public",
    ) == ()


def test_find_violation_uses_cleaned_reply_start():
    mgr = OpeningPenaltyManager(opening_chars=4)
    mgr.record_reply(
        session_id="s1",
        character_id="c1",
        persona_face="public",
        reply_text="哼...舊回覆。",
    )
    plan = mgr.build_plan(
        session_id="s1",
        character_id="c1",
        persona_face="public",
        user_prefs={"opening_penalty_enabled": True},
    )

    assert mgr.find_violation("[白蓮|c1]: 哼...新回覆。", plan) == "哼..."
    assert mgr.find_violation("換個開頭，這次不重複。", plan) == ""
