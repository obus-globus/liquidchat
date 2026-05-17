"""``liquidchat`` command-line entrypoint.

Run ``liquidchat --help`` after installing the ``cli`` extra::

    pip install 'liquidchat[cli]'
    liquidchat --help

Subcommands:

- ``liquidchat chat`` — interactive REPL (inbound chat streams while you
  type in a bottom-anchored prompt). Supports slash-commands for ban /
  unban / private message / token refresh / quit.
- ``liquidchat send`` — one-shot chat send.
- ``liquidchat token info`` / ``validate`` / ``refresh`` — JWT
  inspection, validation, and rotation via ``RequestJWT``.
- ``liquidchat ban`` / ``unban`` — one-shot moderation.
- ``liquidchat mojang`` — UUID/username lookups via the
  :mod:`liquidchat.mojang` helpers.

The CLI deps are optional. Importing this module without
``prompt_toolkit`` / ``cyclopts`` / ``rich`` raises a clear error
pointing at ``pip install 'liquidchat[cli]'``.
"""

from __future__ import annotations

try:
    from cyclopts import App
except ImportError as exc:  # pragma: no cover - exercised manually
    msg = "liquidchat CLI dependencies are missing. Install with: pip install 'liquidchat[cli]'"
    raise ImportError(msg) from exc

from . import account as _account
from . import chat as _chat
from . import login as _login
from . import mod as _mod
from . import mojang as _mojang
from . import send as _send
from . import token as _token

app: App = App(
    name="liquidchat",
    help=(
        "Interact with chat.liquidbounce.net from the terminal: chat REPL, "
        "moderation, JWT management, Mojang lookups."
    ),
)

app.command(_login.login_cmd, name="login")
app.command(_account.account_app)
app.command(_chat.chat)
app.command(_send.send)
app.command(_token.token_app)
app.command(_mod.ban)
app.command(_mod.unban)
app.command(_mojang.mojang_app)


def main() -> None:
    """Console-script entrypoint registered via ``[project.scripts]``."""
    app()


__all__ = ["app", "main"]
