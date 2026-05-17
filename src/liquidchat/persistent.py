"""Long-running, auto-reconnecting LiquidChat client."""

import asyncio
import contextlib
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Literal

import websockets
from websockets.exceptions import ConnectionClosed

from .exceptions import LiquidChatError, LoginFailedError, MissingTokenError, ProtocolError
from .models import (
    AuthorInfo,
    Error,
    LiquidChatMessage,
    MessageContent,
    NewJWT,
    Success,
    UserCount,
)
from .protocol import DEFAULT_WS_URL, build_ssl_context, decode, encode

logger = logging.getLogger(__name__)


type MessageHandler = Callable[[AuthorInfo, str], Awaitable[object]]
type PrivateMessageHandler = Callable[[AuthorInfo, str], Awaitable[object]]
type UserCountHandler = Callable[[int, int], Awaitable[object]]
type ErrorHandler = Callable[[str | dict[str, Any]], Awaitable[object]]
type LifecycleHandler = Callable[[], Awaitable[object]]


@dataclass(slots=True)
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


@dataclass(slots=True, frozen=True)
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


@dataclass(slots=True, frozen=True)
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
        heartbeat_interval: float | None = 60.0,
    ) -> None:
        self._url = url
        self._accept_private_messages = accept_private_messages
        self._insecure_ssl = insecure_ssl
        self.handlers = handlers or Handlers()
        self.reconnect = reconnect or ReconnectPolicy()
        self._heartbeat_interval = heartbeat_interval

        self._token: str | None = token
        self._task: asyncio.Task[None] | None = None
        self._exit = asyncio.Event()
        self._logged_in = asyncio.Event()
        self._enabled = False
        self._login_failed = False
        self._login_failed_event = asyncio.Event()
        self._ws: websockets.ClientConnection | None = None
        self._outgoing: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._uuid_to_username: dict[str, str] = {}
        self._username_to_uuid: dict[str, str] = {}

        # Mod-action plumbing: a single in-flight action at a time.
        self._action_lock = asyncio.Lock()
        self._pending_action: _PendingAction | None = None
        self._pending_jwt: asyncio.Future[str] | None = None

    # ----- public API ----------------------------------------------------

    def set_jwt_token(self, token: str) -> None:
        self._token = token

    @property
    def connected(self) -> bool:
        return self._ws is not None

    async def wait_until_logged_in(self, timeout: float | None = None) -> None:
        """Block until the current websocket connection has logged in.

        Useful for tests and bot startup. Resolves immediately if already
        logged in; cleared on disconnect and re-set on next successful
        login.

        Raises :class:`LoginFailedError` if the server has rejected our
        token (the run loop has stopped retrying). In that case the
        client is permanently disabled — call :meth:`set_jwt_token` and
        :meth:`start` again to retry with a fresh token.
        """
        logged_in = asyncio.create_task(self._logged_in.wait())
        failed = asyncio.create_task(self._login_failed_event.wait())
        try:
            try:
                async with asyncio.timeout(timeout):
                    await asyncio.wait([logged_in, failed], return_when=asyncio.FIRST_COMPLETED)
            except TimeoutError:
                raise TimeoutError("login did not complete within timeout") from None
            if self._login_failed:
                raise LoginFailedError("server rejected token")
        finally:
            for t in (logged_in, failed):
                if not t.done():
                    t.cancel()
                    with contextlib.suppress(BaseException):
                        await t

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
        tb: TracebackType | None,
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

    async def request_new_jwt(self, *, timeout: float = 10.0) -> str:
        """Ask the server for a fresh JWT on the current session.

        Sends ``RequestJWT`` over the live websocket and awaits the
        matching ``NewJWT`` response. Useful for token rotation before
        the current JWT expires (see :mod:`liquidchat.jwt`).

        Returns the new token string. Raises:

        - :class:`RuntimeError` if not currently connected / logged in
        - :class:`TimeoutError` if the server doesn't respond within
          ``timeout`` seconds
        - :class:`LiquidChatError` if the server replies with an
          ``Error`` (e.g. ``NotSupported`` when the server has no
          authenticator configured)

        Serialised against ban/unban via the same action lock; only one
        request-response action is in flight at a time.
        """
        ws = self._ws
        if ws is None or not self._logged_in.is_set():
            raise RuntimeError("not connected / not logged in")
        async with self._action_lock:
            ws = self._ws
            if ws is None:
                raise RuntimeError("not connected")
            loop = asyncio.get_running_loop()
            future: asyncio.Future[str] = loop.create_future()
            self._pending_jwt = future
            try:
                await ws.send(encode("RequestJWT"))
                return await asyncio.wait_for(future, timeout=timeout)
            except ConnectionClosed as e:
                raise RuntimeError("connection closed before NewJWT") from e
            finally:
                self._pending_jwt = None

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
            ws = self._ws
            if ws is None:
                return False
            loop = asyncio.get_running_loop()
            future: asyncio.Future[bool] = loop.create_future()
            self._pending_action = _PendingAction(expected=expected, future=future)
            try:
                await ws.send(encode(action, {"user": uuid}))
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
        pj = self._pending_jwt
        if pj is not None and not pj.done():
            pj.set_exception(LiquidChatError("connection lost during RequestJWT"))
        self._pending_jwt = None

    # ----- internals -----------------------------------------------------

    async def _run(self) -> None:
        try:
            attempt = 0
            while self._enabled and attempt < self.reconnect.max_attempts:
                try:
                    connect_kwargs: dict[str, Any] = dict(
                        close_timeout=5,
                        ping_interval=None,
                        ping_timeout=None,
                        proxy=None,
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
                        except LoginFailedError:
                            logger.error("liquidchat login rejected by server; will not retry")
                            self._login_failed = True
                            self._login_failed_event.set()
                            self._enabled = False
                            raise

                        self._logged_in.set()
                        if self.handlers.on_login_success:
                            await _safe_call(self.handlers.on_login_success())

                        sender = asyncio.create_task(self._sender_loop(ws))
                        heartbeat = (
                            asyncio.create_task(self._heartbeat_loop(ws))
                            if self._heartbeat_interval and self._heartbeat_interval > 0
                            else None
                        )
                        try:
                            await self._receiver_loop(ws)
                        finally:
                            self._logged_in.clear()
                            self._fail_pending_action()
                            sender.cancel()
                            if heartbeat is not None:
                                heartbeat.cancel()
                            with contextlib.suppress(asyncio.CancelledError, Exception):
                                await sender
                            if heartbeat is not None:
                                with contextlib.suppress(asyncio.CancelledError, Exception):
                                    await heartbeat
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

    async def _heartbeat_loop(self, ws: websockets.ClientConnection) -> None:
        """Periodically send ``RequestMojangInfo`` to keep the path alive.

        The axochat protocol has no application-level heartbeat. Stateful
        NATs / firewalls between the client and the server can silently
        drop the TCP flow's conntrack entry after a few minutes of idle,
        which leaves the connection wedged until the next outbound packet.

        ``RequestMojangInfo`` is the cheapest server-side roundtrip: it
        requires no authentication, has no side effects, and the
        resulting ``MojangInfo`` frame is harmless to receive at any
        time — :meth:`_dispatch` simply ignores it.
        """
        interval = self._heartbeat_interval or 0
        if interval <= 0:
            return
        try:
            while True:
                await asyncio.sleep(interval)
                try:
                    await ws.send(encode("RequestMojangInfo"))
                except ConnectionClosed:
                    return
        except asyncio.CancelledError:
            raise

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
        # Resolve in-flight JWT request first (NewJWT has no other consumers).
        if self._pending_jwt is not None and isinstance(msg.c, NewJWT):
            if not self._pending_jwt.done():
                self._pending_jwt.set_result(msg.c.token)
            return
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
        # An Error received while a JWT request is pending must surface
        # to the pending future so the caller doesn't hang.
        if self._pending_jwt is not None and isinstance(msg.c, Error):
            if not self._pending_jwt.done():
                self._pending_jwt.set_exception(
                    LiquidChatError(f"RequestJWT rejected: {msg.c.message}")
                )
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


async def _safe_call(awaitable: Awaitable[object]) -> None:
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
