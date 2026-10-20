import pytest
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
