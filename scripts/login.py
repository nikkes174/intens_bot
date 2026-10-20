from __future__ import annotations

import asyncio

from app.userbot.client import create_telethon_client


async def main() -> None:
    client = create_telethon_client()
    await client.start()
    print("Telethon authorization completed.")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
