"""Moderation examples — batch bans and automod.

These require a JWT whose user is listed in the axochat server's
moderators file. See ``examples/README.md`` for the contract around
ban/unban return values.
"""

from __future__ import annotations

import asyncio

from liquidchat import AuthorInfo, Client, Handlers, PersistentClient


async def batch_ban(mod_jwt: str, uuids: list[str]) -> None:
    """Ban a list of UUIDs over one connection, with progress reporting."""
    client = Client(token=mod_jwt)

    async def on_progress(done: int, total: int, results: dict[str, bool]) -> None:
        ok = sum(results.values())
        print(f"  progress: {done}/{total} ({ok} succeeded)")

    results = await client.ban_users_batch(uuids, progress=on_progress)
    for uuid, success in results.items():
        print(f"  {uuid}: {'banned' if success else 'failed'}")


async def automod(mod_jwt: str, banned_words: set[str]) -> None:
    """React to incoming messages with a moderator action on the same connection."""

    async def screen(author: AuthorInfo, content: str) -> None:
        if any(w in content.lower() for w in banned_words):
            print(f"banning {author.name} for: {content!r}")
            ok = await client.ban_user(author.uuid)
            if not ok:
                # See README — ban_user returns False on timeout / refusal / disconnect.
                print(f"  ban for {author.uuid} did not confirm")

    async with PersistentClient(
        token=mod_jwt,
        handlers=Handlers(on_message=screen),
    ) as client:
        await asyncio.Event().wait()  # run forever


async def main() -> None:
    print("Edit examples/moderation.py and call batch_ban() or automod() from main.")


if __name__ == "__main__":
    asyncio.run(main())
