"""One-shot LiquidChat clients (single websocket per operation).

These are the modern equivalents of the original ``MinimalLiquidChatClient``,
``JWTValidationClient`` and ``ModeratorClient`` — useful for low-frequency
operations like sending a single message or a bulk ban.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, Literal

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

    Ignores anything not matching the predicate (chat noise during a request/response cycle).
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


async def _login(ws: websockets.ClientConnection, token: str, *, allow_messages: bool) -> None:
    """Send a ``LoginJWT`` and wait for the server's ``Success``/``Error``."""
    await ws.send(encode("LoginJWT", {"token": token, "allow_messages": allow_messages}))
    msg = await _wait_for(
        ws,
        lambda m: isinstance(m.c, (Success, Error)),
        timeout=10.0,
    )
    if isinstance(msg.c, Error):
        raise LoginFailedError(msg.c.message)
    if not isinstance(msg.c, Success) or msg.c.reason != "Login":
        raise LoginFailedError(f"unexpected login response: {msg!r}")


class MinimalClient:
    """Open a single connection, log in, send one message, disconnect."""

    def __init__(self, *, url: str = DEFAULT_WS_URL, insecure_ssl: bool = False) -> None:
        self._url = url
        self._insecure_ssl = insecure_ssl
        self._token: str | None = None

    def set_jwt_token(self, token: str) -> None:
        self._token = token

    async def send_message(self, content: str) -> None:
        """Send a single chat message. Raises on failure."""
        if not self._token:
            raise MissingTokenError("call set_jwt_token() first")
        async with _open(self._url, insecure_ssl=self._insecure_ssl) as ws:
            await _login(ws, self._token, allow_messages=True)
            await ws.send(encode("Message", {"content": content}))
            logger.info("liquidchat message sent")


class JWTValidationClient:
    """Validate that a JWT token still successfully logs into the server."""

    def __init__(self, *, url: str = DEFAULT_WS_URL, insecure_ssl: bool = False) -> None:
        self._url = url
        self._insecure_ssl = insecure_ssl

    async def validate(self, token: str) -> bool:
        """Return True if ``token`` is accepted by the server.

        Returns False when the server explicitly rejects the credentials
        (``LoginFailedError``) or the login times out. Network-level errors
        (connection refused, DNS failure) also return False so this function
        is safe to call as a single boolean check — see :meth:`validate_strict`
        if you need to distinguish "wrong creds" from "server unreachable".
        """
        try:
            return await self.validate_strict(token)
        except OSError as e:
            logger.info("liquidchat token validation: server unreachable: %s", e)
            return False

    async def validate_strict(self, token: str) -> bool:
        """Like :meth:`validate` but lets infrastructure errors propagate.

        Returns False only when the server explicitly rejects the credentials.
        Raises ``OSError`` / ``websockets.WebSocketException`` if the server is
        unreachable, lets ``TimeoutError`` propagate.
        """
        try:
            async with _open(self._url, insecure_ssl=self._insecure_ssl) as ws:
                await _login(ws, token, allow_messages=False)
            return True
        except LoginFailedError as e:
            logger.info("liquidchat token validation: credentials rejected: %s", e)
            return False


ProgressCallback = Callable[[int, int, dict[str, bool]], Awaitable[None]]


class ModeratorClient:
    """Single-connection moderator client (ban / unban / batch ban).

    Each call opens a fresh websocket, logs in, performs the action(s) and
    disconnects. For high-frequency moderation use :class:`PersistentClient`
    instead.
    """

    PROGRESS_UPDATE_FREQUENCY = 5

    def __init__(self, *, url: str = DEFAULT_WS_URL, insecure_ssl: bool = False) -> None:
        self._url = url
        self._insecure_ssl = insecure_ssl
        self._token: str | None = None

    def set_jwt_token(self, token: str) -> None:
        self._token = token

    async def ban_user(self, uuid: str) -> bool:
        return await self._action("BanUser", uuid, "Ban")

    async def unban_user(self, uuid: str) -> bool:
        return await self._action("UnbanUser", uuid, "Unban")

    async def _action(
        self, action: Literal["BanUser", "UnbanUser"], uuid: str, expected: str
    ) -> bool:
        if not self._token:
            raise MissingTokenError("call set_jwt_token() first")
        async with _open(self._url, insecure_ssl=self._insecure_ssl) as ws:
            await _login(ws, self._token, allow_messages=False)
            return await _send_action(ws, action, uuid, expected)

    async def ban_users_batch(
        self,
        uuids: list[str],
        *,
        progress: ProgressCallback | None = None,
    ) -> dict[str, bool]:
        """Ban many users over a single websocket. Returns ``{uuid: success}``."""
        if not self._token:
            raise MissingTokenError("call set_jwt_token() first")

        results: dict[str, bool] = {}
        try:
            try:
                async with _open(self._url, insecure_ssl=self._insecure_ssl) as ws:
                    await _login(ws, self._token, allow_messages=False)
                    for idx, uuid in enumerate(uuids, 1):
                        try:
                            results[uuid] = await _send_action(ws, "BanUser", uuid, "Ban")
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


__all__ = [
    "JWTValidationClient",
    "MinimalClient",
    "ModeratorClient",
    "ProgressCallback",
]
