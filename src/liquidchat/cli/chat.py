"""``liquidchat chat`` — interactive REPL.

A single-window chat client that:

- Connects via :class:`liquidchat.PersistentClient` (auto-reconnect,
  username↔UUID cache, JWT rotation).
- Streams inbound messages to the terminal with timestamps + colour.
- Reads from a bottom-anchored prompt powered by ``prompt_toolkit``,
  so new messages scroll above the line you're typing without
  clobbering your input.
- Accepts slash-commands for moderation and connection control —
  type ``/help`` once running for the full list.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as _dt
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.patch_stdout import patch_stdout

from liquidchat import (
    AuthorInfo,
    Handlers,
    LiquidChatError,
    PersistentClient,
)

from ._common import console, err_console, resolve_token, resolve_uuid


def _stamp() -> str:
    return _dt.datetime.now().astimezone().strftime("%H:%M:%S")


_HELP = """\
[bold]liquidchat chat commands[/bold]
  /help                  show this help
  /quit, /exit, Ctrl-D   leave the chat
  /ban <user|uuid>       ban (resolves usernames via Mojang)
  /unban <user|uuid>     unban
  /pm <user> <text>      send a private message (server-side support varies)
  /count                 request current user count
  /whois <user>          look up a user's UUID in the local cache
  /refresh-jwt           rotate the JWT in-place
  anything else          sent as a public chat message
"""


async def _run_chat(jwt: str) -> None:
    loop = asyncio.get_running_loop()
    stop_event: asyncio.Event = asyncio.Event()

    async def on_message(author: AuthorInfo, content: str) -> None:
        console.print(
            f"[dim]{_stamp()}[/dim] [bold cyan]<{author.name}>[/bold cyan] {content}",
            highlight=False,
        )

    async def on_private(author: AuthorInfo, content: str) -> None:
        console.print(
            f"[dim]{_stamp()}[/dim] [bold magenta](DM {author.name})[/bold magenta] {content}",
            highlight=False,
        )

    async def on_user_count(connections: int, users: int) -> None:
        console.print(
            f"[dim]{_stamp()}[/dim] [yellow]* {connections} connections, {users} users[/yellow]",
            highlight=False,
        )

    async def on_connect() -> None:
        console.print(f"[dim]{_stamp()}[/dim] [green]connected[/green]")

    async def on_login_success() -> None:
        console.print(f"[dim]{_stamp()}[/dim] [green]logged in[/green]")

    async def on_disconnect() -> None:
        console.print(f"[dim]{_stamp()}[/dim] [yellow]disconnected[/yellow]")

    async def on_reconnect() -> None:
        console.print(f"[dim]{_stamp()}[/dim] [yellow]reconnecting...[/yellow]")

    async def on_error(error: str | dict[str, Any]) -> None:
        console.print(f"[dim]{_stamp()}[/dim] [red]error:[/red] {error!r}")

    handlers = Handlers(
        on_message=on_message,
        on_private_message=on_private,
        on_user_count=on_user_count,
        on_connect=on_connect,
        on_login_success=on_login_success,
        on_disconnect=on_disconnect,
        on_reconnect=on_reconnect,
        on_error=on_error,
    )

    async with PersistentClient(token=jwt, handlers=handlers) as client:
        try:
            await client.wait_until_logged_in(timeout=15.0)
        except TimeoutError:
            err_console.print("[red]Login did not complete in 15s — exiting.[/red]")
            return

        session: PromptSession[str] = PromptSession(
            HTML("<ansicyan><b>></b></ansicyan> "),
            enable_history_search=True,
        )

        async def _handle(line: str) -> None:
            line = line.strip()
            if not line:
                return
            if line in {"/quit", "/exit"}:
                stop_event.set()
                return
            if line == "/help":
                console.print(_HELP)
                return
            if line == "/count":
                await client.request_user_count()
                return
            if line == "/refresh-jwt":
                try:
                    new = await client.request_new_jwt(timeout=10.0)
                except LiquidChatError as exc:
                    console.print(f"[red]refresh failed:[/red] {exc}")
                    return
                console.print("[green]new JWT issued[/green]")
                # Print the token via plain stdout under patch_stdout so it
                # can still be copy-pasted from the scrollback. Don't
                # rotate the active session — the token is shown so the
                # operator can persist it.
                print(new)
                return
            if line.startswith("/whois "):
                target = line[len("/whois ") :].strip()
                uuid = client.get_uuid(target)
                if uuid:
                    console.print(f"[cyan]{target}[/cyan] -> {uuid}")
                else:
                    console.print(f"[yellow]no cached UUID for {target!r}[/yellow]")
                return
            if line.startswith(("/ban ", "/unban ")):
                op, _, rest = line.partition(" ")
                target = rest.strip()
                if not target:
                    console.print(f"[yellow]usage: {op} <user|uuid>[/yellow]")
                    return
                try:
                    uuid = await resolve_uuid(target)
                except SystemExit:
                    return
                action = client.ban_user if op == "/ban" else client.unban_user
                ok = await action(uuid)
                verb = "banned" if op == "/ban" else "unbanned"
                console.print(
                    f"[green]{verb}[/green] {target}"
                    if ok
                    else f"[red]server did not confirm {op[1:]} of {target}[/red]"
                )
                return
            if line.startswith("/pm "):
                rest = line[len("/pm ") :]
                target, _, body = rest.partition(" ")
                if not body:
                    console.print("[yellow]usage: /pm <user> <message>[/yellow]")
                    return
                # The persistent client doesn't expose a typed PM helper;
                # fall back to the raw send() with the wire-format the
                # server expects for private messages.
                await client.send(
                    "PrivateMessage",
                    {"receiver": target, "content": body},
                )
                console.print(
                    f"[dim]{_stamp()}[/dim] [magenta](you -> {target}, DM)[/magenta] {body}",
                    highlight=False,
                )
                return
            if line.startswith("/"):
                console.print(f"[yellow]unknown command {line.split()[0]!r} — try /help[/yellow]")
                return
            # Plain chat message.
            await client.send_chat(line)

        async def _input_loop() -> None:
            while not stop_event.is_set():
                try:
                    with patch_stdout(raw=True):
                        line = await session.prompt_async()
                except EOFError, KeyboardInterrupt:
                    stop_event.set()
                    return
                try:
                    await _handle(line)
                except LiquidChatError as exc:
                    console.print(f"[red]command failed:[/red] {exc}")

        input_task = loop.create_task(_input_loop())
        try:
            await stop_event.wait()
        finally:
            input_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, EOFError):
                await input_task

    console.print("[dim]bye.[/dim]")


def chat(*, token: str | None = None) -> None:
    """Open an interactive LiquidChat session.

    Connects with :class:`PersistentClient`, then runs a
    ``prompt_toolkit`` REPL until you type ``/quit`` (or hit Ctrl-D).
    Anything that's not a slash-command is sent as a public chat
    message.
    """
    jwt = resolve_token(token)

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_run_chat(jwt))


__all__ = ["chat"]
