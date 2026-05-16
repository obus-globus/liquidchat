"""Long-running, auto-reconnecting LiquidChat client."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

import websockets
from websockets.exceptions import ConnectionClosed

from .exceptions import LoginFailedError, MissingTokenError, ProtocolError
from .models import (
    AuthorInfo,
    Error,
    LiquidChatMessage,
    MessageContent,
    Success,
    UserCount,
)
from .protocol import DEFAULT_WS_URL, build_ssl_context, decode, encode

logger = logging.getLogger(__name__)


MessageHandler = Callable[[AuthorInfo, str], Awaitable[Any]]
PrivateMessageHandler = Callable[[AuthorInfo, str], Awaitable[Any]]
UserCountHandler = Callable[[int, int], Awaitable[Any]]
ErrorHandler = Callable[[str | dict[str, Any]], Awaitable[Any]]
LifecycleHandler = Callable[[], Awaitable[Any]]


@dataclass
class Handlers:
    """Container for ``PersistentClient`` event callbacks."""

    on_message: MessageHandler | None = None
    on_private_message: PrivateMessageHandler | None = None
    on_user_count: UserCountHandler | None = None
    on_error: ErrorHandler | None = None
    on_connect: LifecycleHandler | None = None
    on_login_success: LifecycleHandler | None = None
    on_disconnect: LifecycleHandler | None = None
    on_reconnect: LifecycleHandler | None = None


@dataclass
class ReconnectPolicy:
    """Exponential backoff with jitter."""

    base_delay: float = 5.0
    max_delay: float = 60.0
    max_attempts: int = 500_000

    def delay(self, attempt: int) -> float:
        base: float = min(self.base_delay * (2**attempt), self.max_delay)
        jitter = base * 0.2
        offset: float = random.random() * jitter - jitter / 2
        return base + offset


@dataclass
class _PendingAction:
    expected: Literal["Ban", "Unban"]
    future: asyncio.Future[bool]


class PersistentClient:
    """A long-lived LiquidChat client with automatic reconnection.

    Handles every operation the server understands over a single
    long-lived connection: receives chat / private messages / user-count
    updates (delivered to :class:`Handlers` callbacks), sends chat
    messages, and performs ban / unban moderation actions.

    Moderation calls (:meth:`ban_user`, :meth:`unban_user`) require the
    configured JWT to belong to a user listed in the server's moderators
    file; otherwise the server returns ``Error NotPermitted`` and the call
    returns ``False``.

    The ``accept_private_messages`` constructor flag controls whether the
    server will forward inbound private messages (``PrivateMessage``) to
    this connection. It does *not* affect outbound chat or moderation —
    public ``Message`` broadcasts always reach every logged-in connection.
    Defaults to ``True`` since persistent clients typically want both.
    """

    _ACTION_RESPONSE_TIMEOUT = 10.0

    def __init__(
        self,
        *,
        url: str = DEFAULT_WS_URL,
        token: str | None = None,
        accept_private_messages: bool = True,
        insecure_ssl: bool = False,
        handlers: Handlers | None = None,
        reconnect: ReconnectPolicy | None = None,
    ) -> None:
        self._url = url
        self._accept_private_messages = accept_private_messages
        self._insecure_ssl = insecure_ssl
        self.handlers = handlers or Handlers()
        self.reconnect = reconnect or ReconnectPolicy()

        self._token: str | None = token
        self._task: asyncio.Task[None] | None = None
        self._exit = asyncio.Event()
        self._logged_in = asyncio.Event()
        self._enabled = False
        self._ws: websockets.ClientConnection | None = None
        self._outgoing: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._uuid_to_username: dict[str, str] = {}
        self._username_to_uuid: dict[str, str] = {}

        # Mod-action plumbing: a single in-flight action at a time.
        self._action_lock = asyncio.Lock()
        self._pending_action: _PendingAction | None = None

    # ----- public API ----------------------------------------------------

    def set_jwt_token(self, token: str) -> None:
        self._token = token

    @property
    def connected(self) -> bool:
        return self._ws is not None

    async def wait_until_logged_in(self, timeout: float | None = None) -> None:
        """Block until the current websocket connection has logged in.

        Useful for tests and bot startup. Resolves immediately if already
        logged in; cleared on disconnect and re-set on next successful login.
        """
        if timeout is None:
            await self._logged_in.wait()
        else:
            await asyncio.wait_for(self._logged_in.wait(), timeout=timeout)

    def get_username(self, uuid: str) -> str | None:
        """Look up a username by UUID from the local cache.

        Returns ``None`` if the user hasn't been observed yet. The cache
        is populated purely from inbound ``Message`` / ``PrivateMessage``
        packets — no Mojang API call is ever made. To pre-warm the
        cache, let the client run for a bit or call
        :meth:`request_user_count`.
        """
        return self._uuid_to_username.get(uuid)

    def get_uuid(self, username: str) -> str | None:
        """Look up a UUID by username (case-insensitive) from the local cache.

        Returns ``None`` if the user hasn't been observed yet. See
        :meth:`get_username` for caching semantics.
        """
        return self._username_to_uuid.get(username.lower())

    async def start(self) -> asyncio.Task[None]:
        """Start the background run loop. Returns the task."""
        if self._task and not self._task.done():
            return self._task
        if not self._token:
            raise MissingTokenError("call set_jwt_token() or pass token= before start()")
        self._enabled = True
        self._exit.clear()
        self._task = asyncio.create_task(self._run(), name="liquidchat-persistent")
        return self._task

    async def stop(self) -> None:
        """Stop the client. Idempotent."""
        self._enabled = False
        self._exit.set()
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None
        self._ws = None
        self._logged_in.clear()
        self._fail_pending_action()
        while not self._outgoing.empty():
            self._outgoing.get_nowait()
            self._outgoing.task_done()

    async def __aenter__(self) -> PersistentClient:
        """Start the client and block until logged in."""
        await self.start()
        await self.wait_until_logged_in()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: Any,
    ) -> None:
        await self.stop()

    async def send(self, message_type: str, content: dict[str, Any] | None = None) -> None:
        """Queue an arbitrary outbound message (chat / private message / etc).

        For responses to your own request, use the corresponding helper
        (e.g. :meth:`ban_user`) instead.
        """
        payload: dict[str, Any] = {"m": message_type}
        if content is not None:
            payload["c"] = content
        await self._outgoing.put(payload)

    async def send_chat(self, content: str) -> None:
        await self.send("Message", {"content": content})

    async def request_user_count(self) -> None:
        await self.send("RequestUserCount")

    # ----- moderation ----------------------------------------------------

    async def ban_user(self, uuid: str) -> bool:
        """Ban a user via the active connection. Returns server confirmation.

        Returns ``False`` (rather than raising) if:

        - not connected (no in-flight websocket)
        - the server replies with an ``Error`` (e.g. ``NotPermitted``,
          ``NotBanned``) — the error is also forwarded to ``on_error``
        - no response arrives within 10 seconds
        - the websocket closes before a response is received

        A late response that arrives after the 10s timeout is silently
        discarded (the ``_pending_action`` slot has already been cleared).
        Callers that need certainty should retry; the persistent
        connection keeps the cost low.
        """
        return await self._submit_action("BanUser", uuid, "Ban")

    async def unban_user(self, uuid: str) -> bool:
        """Unban a user via the active connection.

        Same ``False`` semantics as :meth:`ban_user`.
        """
        return await self._submit_action("UnbanUser", uuid, "Unban")

    async def _submit_action(
        self,
        action: Literal["BanUser", "UnbanUser"],
        uuid: str,
        expected: Literal["Ban", "Unban"],
    ) -> bool:
        ws = self._ws
        if ws is None:
            logger.warning("liquidchat not connected; %s for %s dropped", action, uuid)
            return False
        async with self._action_lock:
            if self._ws is None:
                return False
            loop = asyncio.get_running_loop()
            future: asyncio.Future[bool] = loop.create_future()
            self._pending_action = _PendingAction(expected=expected, future=future)
            try:
                await self._ws.send(encode(action, {"user": uuid}))
                try:
                    return await asyncio.wait_for(future, timeout=self._ACTION_RESPONSE_TIMEOUT)
                except TimeoutError:
                    logger.error("%s for %s timed out", action, uuid)
                    return False
            except ConnectionClosed:
                logger.warning("%s for %s lost connection", action, uuid)
                return False
            finally:
                self._pending_action = None

    def _fail_pending_action(self) -> None:
        pa = self._pending_action
        if pa is not None and not pa.future.done():
            pa.future.set_result(False)
        self._pending_action = None

    # ----- internals -----------------------------------------------------

    async def _run(self) -> None:
        try:
            attempt = 0
            while self._enabled and attempt < self.reconnect.max_attempts:
                try:
                    connect_kwargs: dict[str, Any] = dict(
                        close_timeout=5, ping_interval=30, ping_timeout=10, proxy=None
                    )
                    if self._url.startswith("wss://"):
                        connect_kwargs["ssl"] = build_ssl_context(insecure=self._insecure_ssl)
                    async with websockets.connect(self._url, **connect_kwargs) as ws:
                        self._ws = ws
                        attempt = 0
                        if self.handlers.on_connect:
                            await _safe_call(self.handlers.on_connect())

                        try:
                            await self._login(ws)
                        except LoginFailedError as e:
                            raise RuntimeError(f"login failed: {e}") from e

                        self._logged_in.set()
                        if self.handlers.on_login_success:
                            await _safe_call(self.handlers.on_login_success())

                        sender = asyncio.create_task(self._sender_loop(ws))
                        try:
                            await self._receiver_loop(ws)
                        finally:
                            self._logged_in.clear()
                            self._fail_pending_action()
                            sender.cancel()
                            with contextlib.suppress(asyncio.CancelledError, Exception):
                                await sender
                except ConnectionClosed as e:
                    logger.warning("liquidchat connection closed: %s", e)
                except Exception:
                    logger.exception("liquidchat unexpected error")

                self._ws = None
                self._fail_pending_action()
                if not self._enabled:
                    break
                attempt += 1
                delay = self.reconnect.delay(attempt)
                logger.info("liquidchat reconnecting in %.1fs (attempt %d)", delay, attempt)
                if self.handlers.on_reconnect:
                    await _safe_call(self.handlers.on_reconnect())
                try:
                    await asyncio.wait_for(self._exit.wait(), timeout=delay)
                    break  # exit_event fired
                except TimeoutError:
                    pass
        finally:
            self._ws = None
            if self.handlers.on_disconnect:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await asyncio.shield(_safe_call(self.handlers.on_disconnect()))

    async def _login(self, ws: websockets.ClientConnection) -> None:
        assert self._token is not None
        await ws.send(
            encode(
                "LoginJWT", {"token": self._token, "allow_messages": self._accept_private_messages}
            )
        )
        deadline = asyncio.get_running_loop().time() + 10.0
        loop = asyncio.get_running_loop()
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise LoginFailedError("login timeout")
            raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            try:
                msg = decode(raw)
            except ProtocolError:
                continue
            if isinstance(msg.c, Success) and msg.c.reason == "Login":
                return
            if isinstance(msg.c, Error):
                raise LoginFailedError(msg.c.message)

    async def _sender_loop(self, ws: websockets.ClientConnection) -> None:
        while True:
            payload = await self._outgoing.get()
            try:
                await ws.send(encode(payload["m"], payload.get("c")))
            except ConnectionClosed:
                # Re-queue so it retries on the next connection
                await self._outgoing.put(payload)
                self._outgoing.task_done()
                return
            else:
                self._outgoing.task_done()

    async def _receiver_loop(self, ws: websockets.ClientConnection) -> None:
        async for raw in ws:
            if self._exit.is_set() or not self._enabled:
                break
            try:
                msg = decode(raw)
            except ProtocolError as e:
                logger.debug("liquidchat parse error: %s", e)
                continue
            await self._dispatch(msg)

    async def _dispatch(self, msg: LiquidChatMessage) -> None:
        h = self.handlers
        # Resolve in-flight mod action: server response is Success(Ban|Unban)
        # or Error (e.g. NotPermitted / NotBanned).
        if self._pending_action is not None:
            pa = self._pending_action
            if isinstance(msg.c, Success) and msg.c.reason in ("Ban", "Unban"):
                if not pa.future.done():
                    pa.future.set_result(msg.c.reason == pa.expected)
                return
            if isinstance(msg.c, Error):
                if not pa.future.done():
                    pa.future.set_result(False)
                # also surface to on_error for visibility
                if h.on_error:
                    await _safe_call(h.on_error(msg.c.message))
                return

        if isinstance(msg.c, MessageContent):
            self._uuid_to_username[msg.c.author_info.uuid] = msg.c.author_info.name
            self._username_to_uuid[msg.c.author_info.name.lower()] = msg.c.author_info.uuid
            if msg.m == "Message" and h.on_message:
                await _safe_call(h.on_message(msg.c.author_info, msg.c.content))
            elif msg.m == "PrivateMessage" and h.on_private_message:
                await _safe_call(h.on_private_message(msg.c.author_info, msg.c.content))
        elif isinstance(msg.c, UserCount) and h.on_user_count:
            await _safe_call(h.on_user_count(msg.c.connections, msg.c.logged_in))
        elif isinstance(msg.c, Error) and h.on_error:
            await _safe_call(h.on_error(msg.c.message))


async def _safe_call(awaitable: Awaitable[Any]) -> None:
    """Run a user-supplied coroutine, swallowing/logging any exception."""
    try:
        await awaitable
    except Exception:
        logger.exception("liquidchat callback raised")


__all__ = [
    "ErrorHandler",
    "Handlers",
    "LifecycleHandler",
    "MessageHandler",
    "PersistentClient",
    "PrivateMessageHandler",
    "ReconnectPolicy",
    "UserCountHandler",
]
