"""``liquidchat account`` subcommands — manage credential profiles."""

from __future__ import annotations

import shutil

from cyclopts import App
from rich.table import Table

from ._common import (
    clear_default_profile,
    console,
    err_console,
    jwt_path,
    list_profiles,
    profile_dir,
    read_default_profile,
    refresh_token_path,
    write_default_profile,
)

account_app: App = App(name="account", help="Manage liquidchat credential profiles.")


@account_app.command(name="list")
def list_cmd() -> None:
    """List known profiles with JWT/refresh-token presence markers."""
    names = list_profiles()
    default = read_default_profile()
    if not names:
        console.print("[dim]no profiles yet — run [bold]liquidchat login[/bold].[/dim]")
        return

    t = Table(box=None, padding=(0, 1))
    t.add_column("default", style="bold yellow")
    t.add_column("profile", style="bold cyan")
    t.add_column("jwt")
    t.add_column("refresh")
    for name in names:
        marker = "*" if name == default else " "
        j = "[green]✓[/green]" if jwt_path(name).is_file() else "[red]✗[/red]"
        r = "[green]✓[/green]" if refresh_token_path(name).is_file() else "[red]✗[/red]"
        t.add_row(marker, name, j, r)
    console.print(t)
    if default and default not in names:
        err_console.print(
            f"[yellow]warning:[/yellow] default points to {default!r} but no such profile exists"
        )


@account_app.command(name="use")
def use(name: str, /) -> None:
    """Set ``NAME`` as the default profile."""
    if name not in list_profiles():
        err_console.print(
            f"[red]no such profile {name!r}.[/red] Existing: {', '.join(list_profiles()) or '(none)'}"
        )
        raise SystemExit(2)
    write_default_profile(name)
    console.print(f"[green]default profile set to[/green] {name}")


@account_app.command(name="remove")
def remove(name: str, /, *, yes: bool = False) -> None:
    """Delete a profile directory (JWT + refresh token).

    Also clears the default-profile pointer if ``NAME`` was the
    default. Pass ``--yes`` to skip confirmation.
    """
    pdir = profile_dir(name)
    if not pdir.is_dir():
        err_console.print(f"[red]no such profile {name!r}[/red]")
        raise SystemExit(2)

    console.print(f"would remove profile dir: {pdir}")
    if not yes:
        try:
            answer = input("proceed? [y/N] ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            console.print("[dim]aborted.[/dim]")
            raise SystemExit(1)

    shutil.rmtree(pdir, ignore_errors=True)
    if read_default_profile() == name:
        clear_default_profile()
        console.print("[dim]cleared default-profile pointer[/dim]")
    console.print(f"[green]removed profile[/green] {name}")


__all__ = ["account_app"]
