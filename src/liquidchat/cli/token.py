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

from ._common import console, err_console, resolve_token, token_file_path

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
def validate(*, token: str | None = None, strict: bool = False, insecure: bool = True) -> None:
    """Open a one-shot websocket and ask the server to validate the JWT.

    By default a network failure is reported as "could not validate".
    Pass ``--strict`` to let connection errors propagate instead. Pass
    ``--insecure`` to skip TLS verification (required against the
    official ``chat.liquidbounce.net`` deployment).
    """
    jwt = resolve_token(token)
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
    token: str | None = None,
    timeout: float = 10.0,
    insecure: bool = True,
) -> None:
    """Open a fresh connection, request a new JWT, print it to stdout.

    Pipe it into a file::

        liquidchat token refresh > ~/.config/liquidchat/token

    Pass ``--insecure`` to skip TLS verification.
    """
    from liquidchat import PersistentClient

    jwt = resolve_token(token)

    async def _run() -> str:
        async with PersistentClient(token=jwt, insecure_ssl=insecure) as client:
            await client.wait_until_logged_in(timeout=timeout)
            return await client.request_new_jwt(timeout=timeout)

    new = asyncio.run(_run())
    # Plain print so the token is easy to redirect; the friendly note
    # goes to stderr so it doesn't pollute the captured output.
    err_console.print("[green]New JWT issued:[/green]")
    print(new)


@token_app.command(name="path")
def path() -> None:
    """Print the on-disk locations used for credentials.

    Shows both the liquidchat JWT file (resolved against
    ``$LIQUIDCHAT_TOKEN_FILE`` with the standard XDG-config fallback)
    and the mcapi-auth MSA refresh-token file (XDG-state). Each line
    is prefixed with a one-word label so the output is easy to grep
    or feed to other commands.
    """
    jwt_path = token_file_path()
    jwt_status = "(exists)" if jwt_path.is_file() else "(missing)"
    console.print(f"[bold cyan]jwt[/bold cyan]    {jwt_path}  [dim]{jwt_status}[/dim]")

    try:
        from mcapi_auth.auth.storage import default_storage_path
    except ImportError:
        return
    rt_path = default_storage_path()
    rt_status = "(exists)" if rt_path.is_file() else "(missing)"
    console.print(f"[bold cyan]refresh[/bold cyan] {rt_path}  [dim]{rt_status}[/dim]")


@token_app.command(name="clear")
def clear(*, jwt_only: bool = False, refresh_only: bool = False, yes: bool = False) -> None:
    """Delete the on-disk JWT (and optionally the MSA refresh token).

    By default both files are removed. Pass ``--jwt-only`` to keep the
    MSA refresh token (so the next ``liquidchat login`` won't need a
    browser round-trip), or ``--refresh-only`` to keep the JWT.

    Pass ``--yes`` to skip the confirmation prompt.
    """
    if jwt_only and refresh_only:
        err_console.print("[red]--jwt-only and --refresh-only are mutually exclusive[/red]")
        raise SystemExit(2)

    targets: list[tuple[str, Path]] = []
    if not refresh_only:
        targets.append(("jwt", token_file_path()))
    if not jwt_only:
        try:
            from mcapi_auth.auth.storage import default_storage_path

            targets.append(("refresh", default_storage_path()))
        except ImportError:
            pass

    existing = [(label, p) for label, p in targets if p.is_file()]
    if not existing:
        console.print("[dim]nothing to clear[/dim]")
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
