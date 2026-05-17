"""Mojang public-API examples — for users not yet in the chat cache.

``PersistentClient``'s username↔UUID cache only knows users that have
spoken since the client connected. For arbitrary lookups, fall back to
the Mojang public API via :mod:`liquidchat.mojang`.
"""

import asyncio

from liquidchat import Handlers, PersistentClient
from liquidchat.mojang import MojangClient, resolve_username, resolve_uuid


async def basic_lookups() -> None:
    """One-shot helpers — fine for a single name or UUID lookup."""
    uuid = await resolve_uuid("Notch")
    print(f"Notch    -> {uuid}")
    if uuid is not None:
        print(f"{uuid} -> {await resolve_username(uuid)}")


async def batched_lookups() -> None:
    """Reuse a single ``MojangClient`` for several lookups (connection pooling)."""
    async with MojangClient() as mojang:
        for name in ("Notch", "Dinnerbone", "ghost_user_404"):
            profile = await mojang.lookup_by_name(name)
            print(f"{name:20s} -> {profile}")


async def lookup_with_fallback(jwt: str, name: str) -> str | None:
    """Resolve a UUID by chat cache first, falling back to Mojang."""

    async def _noop(*_: object) -> None:
        return None

    async with PersistentClient(token=jwt, handlers=Handlers(on_message=_noop)) as client:
        cached = client.get_uuid(name)
        if cached is not None:
            return cached
    return await resolve_uuid(name)


async def main() -> None:
    await basic_lookups()
    print()
    await batched_lookups()


if __name__ == "__main__":
    asyncio.run(main())
