"""``liquidchat send`` — one-shot chat send via :class:`Client`."""

from __future__ import annotations

import asyncio

from liquidchat import Client

from ._common import console, resolve_token


def send(message: str, /, *, token: str | None = None, insecure: bool = True) -> None:
    """Send a single chat message and exit.

    The connection is opened, the message is sent, and the websocket is
    closed — suitable for cron jobs or one-off announcements.

    Pass ``--insecure`` to skip TLS verification (required against the
    official ``chat.liquidbounce.net`` deployment whose cert is
    expired).
    """
    jwt = resolve_token(token)
    client = Client(token=jwt, insecure_ssl=insecure)
    asyncio.run(client.send_message(message))
    console.print(f"[green]sent:[/green] {message}")


__all__ = ["send"]
