"""One-shot LiquidChat client (single websocket per operation).

A single :class:`Client` exposes every operation the server understands:
JWT validation, chat sending, and moderation (ban / unban / batch ban).
Each call opens a fresh websocket, performs the action, and closes — use
:class:`liquidchat.PersistentClient` instead for sustained workloads.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import websockets

from .exceptions import LoginFailedError, MissingTokenError, ProtocolError
from .models import Error, LiquidChatMessage, Success
from .protocol import DEFAULT_WS_URL, build_ssl_context, decode, encode

logger = logging.getLogger(__name__)

_WS_KWARGS: dict[str, Any] = {
    "close_timeout": 5,
    "max_size": 10_485_760,
    "compression": None,
    "ping_interval": None,
    "ping_timeout": None,
    "proxy": None,
}


@asynccontextmanager
async def _open(
    url: str, *, insecure_ssl: bool = False, **overrides: Any
) -> AsyncIterator[websockets.ClientConnection]:
    kwargs = {**_WS_KWARGS, **overrides}
    if url.startswith("wss://"):
        kwargs["ssl"] = build_ssl_context(insecure=insecure_ssl)
    async with websockets.connect(url, **kwargs) as ws:
        yield ws


async def _wait_for(
    ws: websockets.ClientConnection,
    predicate: Callable[[LiquidChatMessage], bool],
    *,
    timeout: float,
) -> LiquidChatMessage:
    """Read messages until ``predicate`` returns True or timeout elapses.

    Ignores anything not matching the predicate (chat noise during a
    request/response cycle).
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise TimeoutError("liquidchat response timeout")
        raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        try:
            msg = decode(raw)
        except ProtocolError:
            logger.debug("ignoring unparseable message: %r", raw)
            continue
        if predicate(msg):
            return msg


async def _login(
    ws: websockets.ClientConnection, token: str, *, accept_private_messages: bool
) -> None:
    """Send a ``LoginJWT`` and wait for the server's ``Success``/``Error``."""
    await ws.send(encode("LoginJWT", {"token": token, "allow_messages": accept_private_messages}))
    msg = await _wait_for(
        ws,
        lambda m: isinstance(m.c, (Success, Error)),
        timeout=10.0,
    )
    if isinstance(msg.c, Error):
        raise LoginFailedError(msg.c.message)
    if not isinstance(msg.c, Success) or msg.c.reason != "Login":
        raise LoginFailedError(f"unexpected login response: {msg!r}")


async def _send_action(
    ws: websockets.ClientConnection,
    action: str,
    uuid: str,
    expected_reason: str,
) -> bool:
    """Send one moderation action; return whether the server confirmed it."""
    await ws.send(encode(action, {"user": uuid}))
    try:
        msg = await _wait_for(
            ws,
            lambda m: isinstance(m.c, (Success, Error)),
            timeout=5.0,
        )
    except TimeoutError:
        logger.error("%s for %s timed out", action, uuid)
        return False
    if isinstance(msg.c, Error):
        logger.error("%s for %s failed: %s", action, uuid, msg.c.message)
        return False
    return isinstance(msg.c, Success) and msg.c.reason == expected_reason


ProgressCallback = Callable[[int, int, dict[str, bool]], Awaitable[None]]


class Session:
    """A single live websocket on which multiple actions can be run.

    Obtain via :meth:`Client.session`. Do not instantiate directly.
    Methods raise :class:`websockets.exceptions.ConnectionClosed` if the
    underlying connection dies mid-session.
    """

    def __init__(self, ws: websockets.ClientConnection) -> None:
        self._ws = ws

    async def send_message(self, content: str) -> None:
        """Send a chat message on the active session."""
        await self._ws.send(encode("Message", {"content": content}))

    async def send_private_message(self, receiver: str, content: str) -> None:
        """Send a private message to ``receiver`` (a username or UUID)."""
        await self._ws.send(encode("PrivateMessage", {"receiver": receiver, "content": content}))

    async def ban_user(self, uuid: str) -> bool:
        """Ban a user. Returns whether the server confirmed."""
        return await _send_action(self._ws, "BanUser", uuid, "Ban")

    async def unban_user(self, uuid: str) -> bool:
        """Unban a user. Returns whether the server confirmed."""
        return await _send_action(self._ws, "UnbanUser", uuid, "Unban")


class Client:
    """One-shot LiquidChat client.

    Each call opens a fresh websocket, logs in, performs the operation and
    closes — suitable for cron jobs, validation checks, batch moderation,
    or any low-frequency operation. For sustained chat or moderation,
    instantiate :class:`liquidchat.PersistentClient` instead.

    Moderation methods (``ban_user`` / ``unban_user`` / ``ban_users_batch``)
    require the configured JWT to belong to a user listed in the server's
    moderators file; otherwise the server returns ``Error NotPermitted`` and
    the method returns ``False``.
    """

    PROGRESS_UPDATE_FREQUENCY = 5

    def __init__(
        self,
        *,
        url: str = DEFAULT_WS_URL,
        token: str | None = None,
        insecure_ssl: bool = False,
    ) -> None:
        self._url = url
        self._insecure_ssl = insecure_ssl
        self._token: str | None = token

    def set_jwt_token(self, token: str) -> None:
        """Configure the JWT token used by subsequent operations."""
        self._token = token

    # ---------- validation ----------

    async def validate(self, token: str | None = None) -> bool:
        """Return ``True`` if the token successfully logs in.

        Returns ``False`` when the server explicitly rejects the credentials
        OR the server is unreachable. See :meth:`validate_strict` if you need
        to distinguish "wrong creds" from "server down".

        If ``token`` is omitted, the token configured via the constructor /
        :meth:`set_jwt_token` is used.
        """
        try:
            return await self.validate_strict(token)
        except OSError as e:
            logger.info("liquidchat token validation: server unreachable: %s", e)
            return False

    async def validate_strict(self, token: str | None = None) -> bool:
        """Like :meth:`validate` but lets infrastructure errors propagate.

        Returns ``False`` only when the server explicitly rejects the
        credentials. Raises ``OSError`` / ``websockets.WebSocketException`` if
        the server is unreachable; lets ``TimeoutError`` propagate.
        """
        tok = self._resolve_token(token)
        try:
            async with _open(self._url, insecure_ssl=self._insecure_ssl) as ws:
                await _login(ws, tok, accept_private_messages=False)
            return True
        except LoginFailedError as e:
            logger.info("liquidchat token validation: credentials rejected: %s", e)
            return False

    # ---------- chat ----------

    async def send_message(self, content: str) -> None:
        """Send a single chat message. Raises on failure."""
        async with self.session(accept_private_messages=False) as s:
            await s.send_message(content)

    # ---------- moderation ----------

    async def ban_user(self, uuid: str) -> bool:
        """Ban a single user. Returns whether the server confirmed the action."""
        async with self.session(accept_private_messages=False) as s:
            return await s.ban_user(uuid)

    async def unban_user(self, uuid: str) -> bool:
        """Unban a single user. Returns whether the server confirmed the action."""
        async with self.session(accept_private_messages=False) as s:
            return await s.unban_user(uuid)

    async def ban_users_batch(
        self,
        uuids: list[str],
        *,
        progress: ProgressCallback | None = None,
    ) -> dict[str, bool]:
        """Ban many users over a single websocket. Returns ``{uuid: success}``."""
        results: dict[str, bool] = {}
        try:
            try:
                async with self.session(accept_private_messages=False) as s:
                    for idx, uuid in enumerate(uuids, 1):
                        try:
                            results[uuid] = await s.ban_user(uuid)
                        except (TimeoutError, ProtocolError) as e:
                            logger.error("batch ban failed for %s: %s", uuid, e)
                            results[uuid] = False
                        if progress and (
                            idx % self.PROGRESS_UPDATE_FREQUENCY == 0 or idx == len(uuids)
                        ):
                            try:
                                await progress(idx, len(uuids), dict(results))
                            except Exception:
                                logger.exception("progress callback raised")
            except (LoginFailedError, OSError, websockets.WebSocketException) as e:
                logger.error("batch ban session failed: %s", e)
        finally:
            # Runs on success, error, and cancellation: anyone we never reached
            # is marked as failed.
            for uuid in uuids:
                results.setdefault(uuid, False)
        return results

    # ---------- chained / multi-op sessions ----------

    @asynccontextmanager
    async def session(self, *, accept_private_messages: bool = False) -> AsyncIterator[Session]:
        """Open one websocket and run multiple actions on it.

        Example::

            async with client.session() as s:
                await s.send_message("about to clean up...")
                await s.ban_user("<uuid>")
                await s.unban_user("<other-uuid>")

        The ``accept_private_messages`` flag controls whether the server
        will forward inbound private messages to this connection (defaults
        to ``False`` since one-shot sessions rarely consume them). Set it
        to ``True`` if you plan to read responses on the session.
        """
        tok = self._resolve_token(None)
        async with _open(self._url, insecure_ssl=self._insecure_ssl) as ws:
            await _login(ws, tok, accept_private_messages=accept_private_messages)
            yield Session(ws)

    # ---------- internals ----------

    def _resolve_token(self, override: str | None) -> str:
        tok = override if override is not None else self._token
        if tok is None:
            raise MissingTokenError(
                "no JWT token configured; pass token=... to the constructor "
                "or call set_jwt_token() first"
            )
        return tok


__all__ = [
    "Client",
    "ProgressCallback",
    "Session",
]
