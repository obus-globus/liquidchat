"""Long-running bot examples using ``PersistentClient``.

``PersistentClient`` auto-reconnects, exposes a username↔UUID cache
populated from inbound messages, and dispatches events to registered
:class:`Handlers` callbacks.
"""

from __future__ import annotations

import asyncio

from liquidchat import AuthorInfo, Handlers, PersistentClient, ReconnectPolicy


async def chat_bot(jwt: str) -> None:
    """A minimal echo / ping bot with full lifecycle logging."""

    async def on_message(author: AuthorInfo, content: str) -> None:
        print(f"<{author.name}> {content}")
        if content == "!ping":
            await client.send_chat(f"pong @{author.name}")

    async def on_private(author: AuthorInfo, content: str) -> None:
        print(f"(DM from {author.name}) {content}")

    async def _connect() -> None:
        print("[+] connected")

    async def _login_success() -> None:
        print("[+] logged in")

    async def _disconnect() -> None:
        print("[-] disconnected")

    async def _reconnect() -> None:
        print("[~] reconnecting")

    handlers = Handlers(
        on_message=on_message,
        on_private_message=on_private,
        on_connect=_connect,
        on_login_success=_login_success,
        on_disconnect=_disconnect,
        on_reconnect=_reconnect,
    )

    async with PersistentClient(token=jwt, handlers=handlers) as client:
        await client.send_chat("bot online")
        await asyncio.sleep(3600)


async def custom_reconnect(jwt: str) -> None:
    """Manual ``start()``/``stop()`` lifecycle with a tuned reconnect policy."""
    client = PersistentClient(
        token=jwt,
        reconnect=ReconnectPolicy(base_delay=2.0, max_delay=120.0, max_attempts=10),
    )
    await client.start()
    try:
        await client.wait_until_logged_in(timeout=10.0)
        await client.send_chat("hi")
    finally:
        await client.stop()


async def user_lookup(jwt: str) -> None:
    """Use the in-memory username↔UUID cache populated by inbound chat traffic."""

    async def _noop(*_: object) -> None:
        return None

    async with PersistentClient(
        token=jwt,
        handlers=Handlers(on_message=_noop),
    ) as client:
        await asyncio.sleep(5)  # let some chat traffic populate the cache
        uuid = client.get_uuid("notch")
        name = client.get_username("069a79f4-44e9-4726-a5be-fca90e38aaf5")
        print(f"notch     -> {uuid}")
        print(f"<uuid>    -> {name}")


async def main() -> None:
    print("Edit examples/bot.py and call chat_bot(), custom_reconnect(), or user_lookup().")


if __name__ == "__main__":
    asyncio.run(main())
