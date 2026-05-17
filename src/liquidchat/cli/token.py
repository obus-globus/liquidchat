"""Token subcommands: inspect, validate, refresh, path, clear."""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
from pathlib import Path

from cyclopts import App
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from liquidchat import Client, InvalidTokenError, decode_unverified_payload, inspect_token

from ._common import (
    console,
    err_console,
    jwt_path,
    refresh_token_path,
    resolve_profile,
    resolve_token,
)

token_app: App = App(name="token", help="JWT inspection, validation, and rotation.")


def _fmt_ts(ts: float | None) -> str:
    if ts is None:
        return "(missing)"
    when = _dt.datetime.fromtimestamp(ts, tz=_dt.UTC)
    return f"{when.isoformat()}  ({ts:.0f})"


@token_app.command(name="info")
def info(
    *,
    account: str | None = None,
    token: str | None = None,
    raw: bool = False,
) -> None:
    """Decode the JWT and print its claims (header + payload)."""
    jwt = resolve_token(token, account)
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
def validate(
    *,
    account: str | None = None,
    token: str | None = None,
    strict: bool = False,
    insecure: bool = True,
) -> None:
    """Open a one-shot websocket and ask the server to validate the JWT."""
    jwt = resolve_token(token, account)
    client = Client(token=jwt, insecure_ssl=insecure)

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
    account: str | None = None,
    token: str | None = None,
    timeout: float = 10.0,
    insecure: bool = True,
    save: bool = True,
) -> None:
    """Open a fresh connection, request a new JWT, persist it.

    By default the new JWT is written back to the profile's JWT file
    (the same one the chat / send / mod commands would read from).
    Pass ``--no-save`` to print to stdout only (useful for piping the
    token into something else).
    """
    from liquidchat import PersistentClient

    profile_name = resolve_profile(account)
    jwt = resolve_token(token, account)

    async def _run() -> str:
        async with PersistentClient(token=jwt, insecure_ssl=insecure) as client:
            await client.wait_until_logged_in(timeout=timeout)
            return await client.request_new_jwt(timeout=timeout)

    new = asyncio.run(_run())
    err_console.print("[green]New JWT issued.[/green]")
    if save:
        target = jwt_path(profile_name)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        target.write_text(new + "\n", encoding="utf-8")
        err_console.print(f"[green]wrote JWT to[/green] {target}")
    else:
        print(new)


@token_app.command(name="path")
def path(*, account: str | None = None) -> None:
    """Print the on-disk locations used for credentials.

    Without ``--account`` the resolved (current/default) profile is
    printed. Each line is one-token-label-prefixed for easy grepping.
    """
    name = resolve_profile(account)
    j = jwt_path(name)
    r = refresh_token_path(name)
    j_status = "(exists)" if j.is_file() else "(missing)"
    r_status = "(exists)" if r.is_file() else "(missing)"
    console.print(f"[bold cyan]profile[/bold cyan] {name}")
    console.print(f"[bold cyan]jwt[/bold cyan]     {j}  [dim]{j_status}[/dim]")
    console.print(f"[bold cyan]refresh[/bold cyan] {r}  [dim]{r_status}[/dim]")


@token_app.command(name="clear")
def clear(
    *,
    account: str | None = None,
    jwt_only: bool = False,
    refresh_only: bool = False,
    yes: bool = False,
) -> None:
    """Delete the on-disk JWT (and optionally the MSA refresh token).

    Both files are removed by default for the resolved profile.
    ``--jwt-only`` keeps the refresh token (so next ``liquidchat
    login`` skips the browser step); ``--refresh-only`` keeps the JWT.

    Pass ``--yes`` to skip confirmation. Does *not* remove the
    profile directory or the default-profile pointer — use
    ``liquidchat account remove`` for that.
    """
    if jwt_only and refresh_only:
        err_console.print("[red]--jwt-only and --refresh-only are mutually exclusive[/red]")
        raise SystemExit(2)

    name = resolve_profile(account)
    targets: list[tuple[str, Path]] = []
    if not refresh_only:
        targets.append(("jwt", jwt_path(name)))
    if not jwt_only:
        targets.append(("refresh", refresh_token_path(name)))

    existing = [(label, p) for label, p in targets if p.is_file()]
    if not existing:
        console.print(f"[dim]nothing to clear for profile {name!r}[/dim]")
        return

    for label, p in existing:
        console.print(f"would remove [bold cyan]{label}[/bold cyan]: {p}")

    if not yes:
        try:
            answer = input("proceed? [y/N] ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            console.print("[dim]aborted.[/dim]")
            raise SystemExit(1)

    for label, p in existing:
        try:
            p.unlink()
            console.print(f"[green]removed[/green] {label}: {p}")
        except OSError as e:
            err_console.print(f"[red]failed to remove[/red] {label} ({p}): {e}")


__all__ = ["token_app"]
