"""Shared helpers for the liquidchat CLI subcommands.

Profile model
-------------

Credentials live under one directory per Minecraft account::

    $LIQUIDCHAT_HOME/                       (default ~/.config/liquidchat)
    ├── default                             plain-text: name of default profile
    └── profiles/
        ├── hanimetv/
        │   ├── jwt                         liquidchat JWT (chmod 0600)
        │   └── refresh_token.json          MSA refresh token (mcapi-auth)
        └── ...

Helpers exposed:

* :func:`liquidchat_home` — root config dir.
* :func:`profiles_dir` — ``<home>/profiles``.
* :func:`profile_dir(name)` — ``<home>/profiles/<name>``.
* :func:`jwt_path(name)` / :func:`refresh_token_path(name)` — concrete files.
* :func:`list_profiles` — profile names with on-disk JWTs.
* :func:`read_default_profile` / :func:`write_default_profile` — manage
  the ``default`` pointer.
* :func:`resolve_profile(account_arg)` — flag → env → default file.
* :func:`resolve_token(token, account)` — backwards-compatible JWT
  resolution for the CLI subcommands.
* :func:`resolve_uuid(target)` — username → UUID via Mojang API.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from rich.console import Console

from liquidchat.mojang import MojangClient

_HOME_ENV = "LIQUIDCHAT_HOME"
_TOKEN_ENV = "LIQUIDCHAT_TOKEN"
_ACCOUNT_ENV = "LIQUIDCHAT_ACCOUNT"

_VALID_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


console: Console = Console()
err_console: Console = Console(stderr=True)


def liquidchat_home() -> Path:
    """Root directory for liquidchat's per-user state."""
    return Path(os.environ.get(_HOME_ENV) or Path.home() / ".config" / "liquidchat")


def profiles_dir() -> Path:
    return liquidchat_home() / "profiles"


def profile_dir(name: str) -> Path:
    _validate_profile_name(name)
    return profiles_dir() / name


def jwt_path(name: str) -> Path:
    return profile_dir(name) / "jwt"


def refresh_token_path(name: str) -> Path:
    return profile_dir(name) / "refresh_token.json"


def _default_pointer_file() -> Path:
    return liquidchat_home() / "default"


def _validate_profile_name(name: str) -> None:
    if not name or not _VALID_NAME_RE.match(name):
        err_console.print(
            f"[red]invalid profile name {name!r}[/red] — use letters, digits, '.', '-', '_'"
        )
        raise SystemExit(2)


def list_profiles() -> list[str]:
    """Return profile names that have at least a JWT or refresh file."""
    pdir = profiles_dir()
    if not pdir.is_dir():
        return []
    names: list[str] = []
    for child in sorted(pdir.iterdir()):
        if (
            child.is_dir()
            and _VALID_NAME_RE.match(child.name)
            and ((child / "jwt").is_file() or (child / "refresh_token.json").is_file())
        ):
            names.append(child.name)
    return names


def read_default_profile() -> str | None:
    p = _default_pointer_file()
    if not p.is_file():
        return None
    name = p.read_text(encoding="utf-8").strip()
    return name or None


def write_default_profile(name: str) -> None:
    _validate_profile_name(name)
    home = liquidchat_home()
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    _default_pointer_file().write_text(name + "\n", encoding="utf-8")


def clear_default_profile() -> None:
    p = _default_pointer_file()
    if p.is_file():
        p.unlink()


def resolve_profile(account: str | None) -> str:
    """Pick a profile name.

    Resolution order:
      1. Explicit ``--account`` flag (passed in).
      2. ``LIQUIDCHAT_ACCOUNT`` env var.
      3. ``<home>/default`` pointer file.

    Raises :class:`SystemExit(2)` if none of those resolve.
    """
    if account:
        _validate_profile_name(account)
        return account
    env = os.environ.get(_ACCOUNT_ENV)
    if env:
        _validate_profile_name(env)
        return env
    default = read_default_profile()
    if default:
        return default
    err_console.print(
        "[red]No profile configured.[/red] Run [bold]liquidchat login[/bold] first, "
        "or pass [bold]--account NAME[/bold]."
    )
    raise SystemExit(2)


def resolve_token(token: str | None, account: str | None = None) -> str:
    """Find the JWT to use.

    Resolution order:
      1. Explicit ``--token`` flag (passed in by the caller).
      2. ``LIQUIDCHAT_TOKEN`` environment variable.
      3. Profile JWT file for the resolved profile name (see
         :func:`resolve_profile`).
    """
    if token:
        return token.strip()
    env = os.environ.get(_TOKEN_ENV)
    if env:
        return env.strip()
    name = resolve_profile(account)
    jp = jwt_path(name)
    if jp.is_file():
        return jp.read_text(encoding="utf-8").strip()
    err_console.print(
        f"[red]No JWT for profile {name!r}.[/red] Run "
        f"[bold]liquidchat login --account {name}[/bold] or write a JWT to {jp}."
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


__all__ = [
    "clear_default_profile",
    "console",
    "err_console",
    "jwt_path",
    "liquidchat_home",
    "list_profiles",
    "profile_dir",
    "profiles_dir",
    "read_default_profile",
    "refresh_token_path",
    "resolve_profile",
    "resolve_token",
    "resolve_uuid",
    "write_default_profile",
]
