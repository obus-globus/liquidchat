"""Shared helpers for the liquidchat CLI subcommands.

Kept tiny — credential loading, a Rich console singleton, and a small
helper that resolves a ``uuid-or-username`` argument against the public
Mojang API.
"""

from __future__ import annotations

import os
from pathlib import Path

from rich.console import Console

from liquidchat.mojang import MojangClient

_TOKEN_ENV = "LIQUIDCHAT_TOKEN"
_TOKEN_FILE_ENV = "LIQUIDCHAT_TOKEN_FILE"
_DEFAULT_TOKEN_FILE = Path.home() / ".config" / "liquidchat" / "token"


console: Console = Console()
err_console: Console = Console(stderr=True)


def resolve_token(token: str | None) -> str:
    """Find the JWT to use.

    Resolution order:
    1. Explicit ``--token`` flag (passed in by the caller).
    2. ``LIQUIDCHAT_TOKEN`` environment variable.
    3. The file at ``$LIQUIDCHAT_TOKEN_FILE`` (or
       ``~/.config/liquidchat/token`` if unset). The file's contents
       are stripped — trailing newlines are fine.

    Raises :class:`SystemExit` with a friendly message if nothing was
    found, so subcommands can call this unconditionally.
    """
    if token:
        return token.strip()
    env = os.environ.get(_TOKEN_ENV)
    if env:
        return env.strip()
    token_file = Path(os.environ.get(_TOKEN_FILE_ENV) or _DEFAULT_TOKEN_FILE)
    if token_file.is_file():
        return token_file.read_text(encoding="utf-8").strip()
    err_console.print(
        "[red]No token found.[/red] Provide --token, set "
        f"{_TOKEN_ENV}, or write the JWT to {token_file}."
    )
    raise SystemExit(2)


async def resolve_uuid(target: str) -> str:
    """Accept either an undashed UUID (32 hex chars) or a username.

    Names are resolved through the public Mojang API. Returns the
    undashed UUID. Exits the process with a friendly message if the
    name doesn't resolve.
    """
    stripped = target.replace("-", "").lower()
    if len(stripped) == 32 and all(c in "0123456789abcdef" for c in stripped):
        return stripped
    async with MojangClient() as mojang:
        uuid = await mojang.resolve_uuid(target)
    if uuid is None:
        err_console.print(f"[red]No account currently owns the username {target!r}[/red]")
        raise SystemExit(2)
    return uuid.replace("-", "").lower()


__all__ = ["console", "err_console", "resolve_token", "resolve_uuid"]
