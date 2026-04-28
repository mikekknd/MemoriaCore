from types import SimpleNamespace

from api.discord_bot import (
    DISCORD_MESSAGE_SAFE_LIMIT,
    _clean_bot_mentions,
    _is_guild_trigger,
    _session_key,
    _split_message,
)


def _message(author_id="42", guild_id=None, channel_id="chan-1", content="hello", mentions=None):
    guild = SimpleNamespace(id=guild_id) if guild_id is not None else None
    return SimpleNamespace(
        author=SimpleNamespace(id=author_id, bot=False),
        guild=guild,
        channel=SimpleNamespace(id=channel_id),
        content=content,
        mentions=mentions or [],
        reference=None,
        webhook_id=None,
    )


def test_split_message_uses_discord_safe_limit_and_skips_empty_chunks():
    text = ("測試。" * 800).strip()

    chunks = _split_message(text)

    assert chunks
    assert all(0 < len(chunk) <= DISCORD_MESSAGE_SAFE_LIMIT for chunk in chunks)


def test_session_key_separates_dm_and_guild_for_same_author():
    dm = _message(author_id="123", guild_id=None, channel_id="dm-channel")
    guild = _message(author_id="123", guild_id="guild-1", channel_id="guild-channel")

    assert _session_key("bot-a", dm) != _session_key("bot-a", guild)
    assert _session_key("bot-a", dm)[1] == "discord_private"
    assert _session_key("bot-a", guild)[1] == "discord_public"


def test_session_key_separates_guild_channels_for_same_author():
    left = _message(author_id="123", guild_id="guild-1", channel_id="channel-a")
    right = _message(author_id="123", guild_id="guild-1", channel_id="channel-b")

    assert _session_key("bot-a", left) != _session_key("bot-a", right)


def test_guild_trigger_requires_mention_or_reply():
    bot_user = SimpleNamespace(id="999")
    plain = _message(author_id="123", guild_id="guild-1", channel_id="channel-a")
    mentioned = _message(
        author_id="123",
        guild_id="guild-1",
        channel_id="channel-a",
        mentions=[SimpleNamespace(id="999")],
    )

    assert _is_guild_trigger(plain, bot_user) is False
    assert _is_guild_trigger(mentioned, bot_user) is True


def test_clean_bot_mentions_removes_discord_mention_forms():
    assert _clean_bot_mentions("<@999> 你好", 999) == "你好"
    assert _clean_bot_mentions("<@!999> 你好", 999) == "你好"
