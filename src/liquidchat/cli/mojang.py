"""``liquidchat mojang`` — username ↔ UUID lookups via the public API.

Thin CLI wrapper around :mod:`liquidchat.mojang`.
"""

from __future__ import annotations

import asyncio

from cyclopts import App

from liquidchat.mojang import MojangClient

from ._common import console, err_console

mojang_app: App = App(name="mojang", help="Public Mojang profile lookups.")


@mojang_app.command(name="uuid")
def uuid_cmd(name: str, /) -> None:
    """Resolve a Minecraft username to its dashed UUID."""

    async def _run() -> str | None:
        async with MojangClient() as mojang:
            return await mojang.resolve_uuid(name)

    result = asyncio.run(_run())
    if result is None:
        err_console.print(f"[red]No account currently owns username[/red] {name!r}")
        raise SystemExit(1)
    console.print(result)


@mojang_app.command(name="name")
def name_cmd(uuid: str, /) -> None:
    """Resolve a UUID (with or without dashes) to its current username."""

    async def _run() -> str | None:
        async with MojangClient() as mojang:
            return await mojang.resolve_username(uuid)

    result = asyncio.run(_run())
    if result is None:
        err_console.print(f"[red]No profile found for UUID[/red] {uuid!r}")
        raise SystemExit(1)
    console.print(result)


__all__ = ["mojang_app"]
