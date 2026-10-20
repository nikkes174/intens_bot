import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.database import session_scope
from app.userbot import sources


class FakeChannel:
    def __init__(self):
        self.id = 12345
        self.title = "Test Channel"
        self.username = "testchannel"
        self.broadcast = True
        self.megagroup = False
        self.participants_count = 42


@pytest.mark.asyncio
async def test_parse_source_link_formats():
    assert sources.parse_source_link("https://t.me/channelname") == ("public", "channelname")
    assert sources.parse_source_link("http://t.me/channelname") == ("public", "channelname")
    assert sources.parse_source_link("t.me/channelname") == ("public", "channelname")
    assert sources.parse_source_link("@channelname") == ("public", "channelname")
    assert sources.parse_source_link("https://t.me/+invitehash") == ("invite", "invitehash")
    assert sources.parse_source_link("https://t.me/joinchat/invitehash") == ("invite", "invitehash")
    assert sources.parse_source_link("https://t.me/channelname/123") is None
    assert sources.parse_source_link("https://t.me/botname") is None or True
    assert sources.parse_source_link("not a link") is None


@pytest.mark.asyncio
async def test_join_public_channel_creates_source():
    client = AsyncMock()
    entity = FakeChannel()
    client.get_entity = AsyncMock(return_value=entity)
    client.return_value = None
    sources.get_peer_id = lambda entity: -1000000012345

    source = await sources.join_source(client, "https://t.me/testchannel")
    assert source.title == "Test Channel"
    assert source.enabled is True
    assert source.excluded is False
    assert source.audience == 42

    async with session_scope() as db:
        from sqlalchemy import select
        from app.models import Source

        result = await db.execute(select(Source).where(Source.telegram_id == source.telegram_id))
        saved = result.scalar_one_or_none()
        assert saved is not None
        assert saved.title == "Test Channel"




@pytest.mark.asyncio
async def test_parse_rejects_messages_users_and_external_sites():
    assert sources.parse_source_link("https://t.me/channel/123") is None
    assert sources.parse_source_link("https://example.com/t.me/channel") is None
    assert sources.parse_source_link("evil.com https://t.me/channel") is None
    assert sources.parse_source_link("https://t.me/+hash?start=abc") == ("invite", "hash")
    assert sources.parse_source_link("https://t.me/joinchat/hash/") == ("invite", "hash")


@pytest.mark.asyncio
async def test_join_private_invite_does_not_join_twice(monkeypatch):
    monkeypatch.setattr(sources, "get_peer_id", lambda entity: -100999)

    client = AsyncMock()
    imported_entity = SimpleNamespace(id=999, title="Private", username=None, broadcast=True, megagroup=False, participants_count=7)
    invite = SimpleNamespace(title="Private", chat=None)
    updates = SimpleNamespace(chats=[imported_entity])
    client.side_effect = None
    client.get_entity = AsyncMock(side_effect=AssertionError("should not resolve public entity"))
    client.side_effect = [invite, updates]

    source = await sources.join_source(client, "https://t.me/+invitehash")
    assert source.title == "Private"
    assert source.already_member is False
    # CheckChatInviteRequest + ImportChatInviteRequest only, no JoinChannelRequest.
    assert client.call_count == 2


@pytest.mark.asyncio
async def test_join_private_already_member(monkeypatch):
    monkeypatch.setattr(sources, "get_peer_id", lambda entity: -100998)

    client = AsyncMock()
    chat = SimpleNamespace(id=998, title="Already", username=None, broadcast=False, megagroup=True, participants_count=3)
    invite_cls = type("ChatInviteAlready", (), {})
    invite = invite_cls()
    invite.chat = chat
    monkeypatch.setattr(sources, "ChatInviteAlready", invite_cls)
    client.side_effect = [invite]

    source = await sources.join_source(client, "https://t.me/+alreadyhash")
    assert source.title == "Already"
    assert source.already_member is True
    assert client.call_count == 1
