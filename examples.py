"""Runnable usage examples for the ``liquidchat`` package.

Each function below demonstrates one workflow. Run individual examples
by editing the ``main()`` at the bottom — or just read them as
reference snippets.
"""

from __future__ import annotations

import asyncio

from liquidchat import (
    AuthorInfo,
    Client,
    Handlers,
    PersistentClient,
    ReconnectPolicy,
)

# ---------------------------------------------------------------------------
# 1. One-shot: send a single chat message and exit.
# ---------------------------------------------------------------------------


async def example_send_one_message(jwt: str) -> None:
    client = Client(token=jwt)
    await client.send_message("hello, chat!")


# ---------------------------------------------------------------------------
# 2. One-shot: validate a JWT.
# ---------------------------------------------------------------------------


async def example_validate_token(jwt: str) -> None:
    client = Client(token=jwt)

    # Forgiving variant: bad creds OR server down both return False.
    if await client.validate():
        print("token works")

    # Strict variant: distinguishes credential rejection from network errors.
    try:
        ok = await client.validate_strict()
        print("credentials accepted" if ok else "credentials rejected")
    except OSError as e:
        print(f"server unreachable: {e}")


# ---------------------------------------------------------------------------
# 3. One-shot: batch ban with progress reporting.
# ---------------------------------------------------------------------------


async def example_batch_ban(mod_jwt: str, uuids: list[str]) -> None:
    client = Client(token=mod_jwt)

    async def on_progress(done: int, total: int, results: dict[str, bool]) -> None:
        ok = sum(results.values())
        print(f"  progress: {done}/{total} ({ok} succeeded)")

    results = await client.ban_users_batch(uuids, progress=on_progress)
    for uuid, success in results.items():
        print(f"  {uuid}: {'banned' if success else 'failed'}")


# ---------------------------------------------------------------------------
# 4. Chained one-shot: multiple actions on a single connection.
# ---------------------------------------------------------------------------


async def example_chained_actions(mod_jwt: str, target_uuid: str) -> None:
    async with Client(token=mod_jwt).session() as s:
        await s.send_message("about to clean up...")
        await s.ban_user(target_uuid)
        await asyncio.sleep(1)
        await s.unban_user(target_uuid)
        await s.send_private_message("notch", "you've been warned")


# ---------------------------------------------------------------------------
# 5. Long-running chat bot via ``async with``.
# ---------------------------------------------------------------------------


async def example_chat_bot(jwt: str) -> None:
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


# ---------------------------------------------------------------------------
# 6. Long-running automod: handlers + ban on the same connection.
# ---------------------------------------------------------------------------


async def example_automod(mod_jwt: str, banned_words: set[str]) -> None:
    async def screen(author: AuthorInfo, content: str) -> None:
        if any(w in content.lower() for w in banned_words):
            print(f"banning {author.name} for: {content!r}")
            ok = await client.ban_user(author.uuid)
            if not ok:
                # See "what happens if a ban gets no response" below.
                print(f"  ban for {author.uuid} did not confirm")

    async with PersistentClient(
        token=mod_jwt,
        handlers=Handlers(on_message=screen),
    ) as client:
        await asyncio.Event().wait()  # run forever


# ---------------------------------------------------------------------------
# 7. Manual lifecycle with a custom reconnect policy.
# ---------------------------------------------------------------------------


async def example_custom_reconnect(jwt: str) -> None:
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


# ---------------------------------------------------------------------------
# 8. Username ↔ UUID lookup (cached as messages flow through).
# ---------------------------------------------------------------------------


async def example_user_lookup(jwt: str) -> None:
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


# ---------------------------------------------------------------------------
# 9. Mojang fallback for users not yet in the local cache.
# ---------------------------------------------------------------------------


async def example_mojang_lookup() -> None:
    """Use the Mojang public API for users who haven't spoken in chat.

    The persistent client only caches users it has observed. For
    arbitrary username/UUID lookups, use the `liquidchat.mojang` helpers
    (powered by `httpx`).
    """
    from liquidchat.mojang import MojangClient, resolve_username, resolve_uuid

    # One-shot helpers — fine for single lookups:
    uuid = await resolve_uuid("Notch")
    print(f"Notch    -> {uuid}")
    if uuid is not None:
        print(f"{uuid} -> {await resolve_username(uuid)}")

    # Reuse a single client for batches:
    async with MojangClient() as mojang:
        for name in ("Notch", "Dinnerbone", "ghost_user_404"):
            profile = await mojang.lookup_by_name(name)
            print(f"{name:20s} -> {profile}")


async def example_lookup_with_mojang_fallback(jwt: str, name: str) -> str | None:
    """Resolve a UUID by chat cache first, falling back to Mojang."""
    from liquidchat.mojang import resolve_uuid

    async def _noop(*_: object) -> None:
        return None

    async with PersistentClient(token=jwt, handlers=Handlers(on_message=_noop)) as client:
        cached = client.get_uuid(name)
        if cached is not None:
            return cached
    return await resolve_uuid(name)


# ---------------------------------------------------------------------------
#
# Both `Client.ban_user` and `PersistentClient.ban_user` return `bool`:
#
#   True   -> server replied Success {reason: "Ban"}
#   False  -> any of:
#               - server replied Error (NotPermitted, NotBanned, ...)
#               - response did not arrive within the timeout
#                   (one-shot: 5s, persistent: 10s)
#               - the websocket dropped before a response came in
#               - cancellation propagated through (only in batch_ban,
#                 where it marks unreached UUIDs as False)
#
# The client never raises on a "ban missed its reply" — it logs the
# timeout/disconnect and returns False. Callers that need certainty
# should retry, or use the PersistentClient (the same connection stays
# open across retries so transient failures are cheap to recover from).
#
# A late response that arrives *after* the timeout window is dropped:
#   - One-shot: the websocket is already closed, so the response was
#     never read.
#   - Persistent: the `_pending_action` slot was cleared on timeout, so
#     the late Success/Error is ignored (an Error will still surface to
#     the `on_error` handler if one is registered).


# ---------------------------------------------------------------------------
# Entry point — pick what to run.
# ---------------------------------------------------------------------------


async def main() -> None:
    # JWT = "<your-jwt-here>"
    # await example_send_one_message(JWT)
    # await example_validate_token(JWT)
    # await example_chat_bot(JWT)
    print("Edit examples.py to pick an example to run.")


if __name__ == "__main__":
    asyncio.run(main())
