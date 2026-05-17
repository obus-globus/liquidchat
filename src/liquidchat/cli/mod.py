"""One-shot moderation subcommands."""

from __future__ import annotations

import asyncio

from liquidchat import Client

from ._common import console, err_console, resolve_token, resolve_uuid


def ban(target: str, /, *, token: str | None = None, insecure: bool = True) -> None:
    """Ban a player by UUID or username.

    Usernames are resolved against the public Mojang API before the
    moderation action is sent. Pass ``--insecure`` to skip TLS
    verification against the chat server (the official deployment has
    an expired cert).
    """
    jwt = resolve_token(token)

    async def _run() -> bool:
        uuid = await resolve_uuid(target)
        client = Client(token=jwt, insecure_ssl=insecure)
        return await client.ban_user(uuid)

    if asyncio.run(_run()):
        console.print(f"[green]banned[/green] {target}")
    else:
        err_console.print(f"[red]server did not confirm ban of[/red] {target}")
        raise SystemExit(1)


def unban(target: str, /, *, token: str | None = None, insecure: bool = True) -> None:
    """Unban a player by UUID or username.

    Pass ``--insecure`` to skip TLS verification.
    """
    jwt = resolve_token(token)

    async def _run() -> bool:
        uuid = await resolve_uuid(target)
        client = Client(token=jwt, insecure_ssl=insecure)
        return await client.unban_user(uuid)

    if asyncio.run(_run()):
        console.print(f"[green]unbanned[/green] {target}")
    else:
        err_console.print(f"[red]server did not confirm unban of[/red] {target}")
        raise SystemExit(1)


__all__ = ["ban", "unban"]
