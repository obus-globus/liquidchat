"""One-shot moderation subcommands."""

from __future__ import annotations

import asyncio

from liquidchat import Client

from ._common import console, err_console, resolve_token, resolve_uuid


def ban(
    target: str,
    /,
    *,
    account: str | None = None,
    token: str | None = None,
    insecure: bool = True,
) -> None:
    """Ban a player by UUID or username (via the active profile's JWT)."""
    jwt = resolve_token(token, account)

    async def _run() -> bool:
        uuid = await resolve_uuid(target)
        client = Client(token=jwt, insecure_ssl=insecure)
        return await client.ban_user(uuid)

    if asyncio.run(_run()):
        console.print(f"[green]banned[/green] {target}")
    else:
        err_console.print(f"[red]server did not confirm ban of[/red] {target}")
        raise SystemExit(1)


def unban(
    target: str,
    /,
    *,
    account: str | None = None,
    token: str | None = None,
    insecure: bool = True,
) -> None:
    """Unban a player by UUID or username (via the active profile's JWT)."""
    jwt = resolve_token(token, account)

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
