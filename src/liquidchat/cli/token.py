"""Token subcommands: inspect, validate, refresh."""

from __future__ import annotations

import asyncio
import datetime as _dt
import json

from cyclopts import App
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from liquidchat import Client, InvalidTokenError, decode_unverified_payload, inspect_token

from ._common import console, err_console, resolve_token

token_app: App = App(name="token", help="JWT inspection, validation, and rotation.")


def _fmt_ts(ts: float | None) -> str:
    if ts is None:
        return "(missing)"
    when = _dt.datetime.fromtimestamp(ts, tz=_dt.UTC)
    return f"{when.isoformat()}  ({ts:.0f})"


@token_app.command(name="info")
def info(
    *,
    token: str | None = None,
    raw: bool = False,
) -> None:
    """Decode the JWT and print its claims (header + payload).

    Pass ``--raw`` to get the original JSON instead of a pretty table.
    """
    jwt = resolve_token(token)
    try:
        meta = inspect_token(jwt)
        header, payload = decode_unverified_payload(jwt)
    except InvalidTokenError as exc:
        err_console.print(f"[red]Invalid token:[/red] {exc}")
        raise SystemExit(2) from exc

    if raw:
        console.print(
            Syntax(
                json.dumps({"header": header, "payload": payload}, indent=2, sort_keys=True),
                "json",
                theme="ansi_dark",
            )
        )
        return

    t = Table(show_header=False, box=None, padding=(0, 1))
    t.add_column(style="bold cyan")
    t.add_column()
    t.add_row("Name", meta.name)
    t.add_row("UUID", meta.uuid)
    t.add_row("Expires at", _fmt_ts(meta.expires_at))
    t.add_row(
        "Status",
        "[red]expired[/red]" if meta.is_expired() else "[green]valid[/green]",
    )
    t.add_row("Algorithm", meta.algorithm)
    console.print(Panel(t, title="liquidchat JWT", border_style="cyan"))


@token_app.command(name="validate")
def validate(*, token: str | None = None, strict: bool = False) -> None:
    """Open a one-shot websocket and ask the server to validate the JWT.

    By default a network failure is reported as "could not validate".
    Pass ``--strict`` to let connection errors propagate instead.
    """
    jwt = resolve_token(token)
    client = Client(token=jwt)

    async def _run() -> bool:
        if strict:
            return await client.validate_strict()
        return await client.validate()

    ok = asyncio.run(_run())
    if ok:
        console.print("[green]Server accepted the token.[/green]")
    else:
        err_console.print("[red]Server rejected the token (or was unreachable).[/red]")
        raise SystemExit(1)


@token_app.command(name="refresh")
def refresh(
    *,
    token: str | None = None,
    timeout: float = 10.0,
) -> None:
    """Open a fresh connection, request a new JWT, print it to stdout.

    Pipe it into a file::

        liquidchat token refresh > ~/.config/liquidchat/token
    """
    from liquidchat import PersistentClient

    jwt = resolve_token(token)

    async def _run() -> str:
        async with PersistentClient(token=jwt) as client:
            await client.wait_until_logged_in(timeout=timeout)
            return await client.request_new_jwt(timeout=timeout)

    new = asyncio.run(_run())
    # Plain print so the token is easy to redirect; the friendly note
    # goes to stderr so it doesn't pollute the captured output.
    err_console.print("[green]New JWT issued:[/green]")
    print(new)


__all__ = ["token_app"]
