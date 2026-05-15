"""Long-running, auto-reconnecting LiquidChat clients."""

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


class PersistentClient:
    """A long-lived LiquidChat client with automatic reconnection.

    Replaces ``LiquidChatClientReworked`` from the original code. Not a
    singleton — instantiate one per logical chat connection.
    """

    def __init__(
        self,
        *,
        url: str = DEFAULT_WS_URL,
        allow_messages: bool = True,
        insecure_ssl: bool = False,
        handlers: Handlers | None = None,
        reconnect: ReconnectPolicy | None = None,
    ) -> None:
        self._url = url
        self._allow_messages = allow_messages
        self._insecure_ssl = insecure_ssl
        self.handlers = handlers or Handlers()
        self.reconnect = reconnect or ReconnectPolicy()

        self._token: str | None = None
        self._task: asyncio.Task[None] | None = None
        self._exit = asyncio.Event()
        self._logged_in = asyncio.Event()
        self._enabled = False
        self._ws: websockets.ClientConnection | None = None
        self._outgoing: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._uuid_to_username: dict[str, str] = {}
        self._username_to_uuid: dict[str, str] = {}

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
        return self._uuid_to_username.get(uuid)

    def get_uuid(self, username: str) -> str | None:
        return self._username_to_uuid.get(username.lower())

    async def start(self) -> asyncio.Task[None]:
        """Start the background run loop. Returns the task."""
        if self._task and not self._task.done():
            return self._task
        if not self._token:
            raise MissingTokenError("call set_jwt_token() before start()")
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
        while not self._outgoing.empty():
            self._outgoing.get_nowait()
            self._outgoing.task_done()

    async def send(self, message_type: str, content: dict[str, Any] | None = None) -> None:
        """Queue an outbound message. Drops silently if not connected."""
        payload: dict[str, Any] = {"m": message_type}
        if content is not None:
            payload["c"] = content
        await self._outgoing.put(payload)

    async def send_chat(self, content: str) -> None:
        await self.send("Message", {"content": content})

    async def request_user_count(self) -> None:
        await self.send("RequestUserCount")

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
                            sender.cancel()
                            with contextlib.suppress(asyncio.CancelledError, Exception):
                                await sender
                except ConnectionClosed as e:
                    logger.warning("liquidchat connection closed: %s", e)
                except Exception:
                    logger.exception("liquidchat unexpected error")

                self._ws = None
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
            encode("LoginJWT", {"token": self._token, "allow_messages": self._allow_messages})
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


# ---------------------------------------------------------------------------
# Persistent moderator
# ---------------------------------------------------------------------------


@dataclass
class _PendingAction:
    action: Literal["BanUser", "UnbanUser"]
    uuid: str
    future: asyncio.Future[bool]


class PersistentModeratorClient:
    """Long-lived moderation connection that processes ban / unban actions from a queue.

    Suitable for high-frequency automod work — actions cost only one round-trip
    each, and a broken connection triggers an exponential-backoff reconnect.
    """

    MAX_QUEUE_SIZE = 1000

    def __init__(
        self,
        *,
        url: str = DEFAULT_WS_URL,
        insecure_ssl: bool = False,
        reconnect: ReconnectPolicy | None = None,
    ) -> None:
        self._url = url
        self._insecure_ssl = insecure_ssl
        self.reconnect = reconnect or ReconnectPolicy()

        self._token: str | None = None
        self._task: asyncio.Task[None] | None = None
        self._exit = asyncio.Event()
        self._logged_in = asyncio.Event()
        self._enabled = False
        self._ws: websockets.ClientConnection | None = None
        self._queue: asyncio.Queue[_PendingAction] = asyncio.Queue(maxsize=self.MAX_QUEUE_SIZE)

    def set_jwt_token(self, token: str) -> None:
        self._token = token

    @property
    def connected(self) -> bool:
        return self._ws is not None

    async def wait_until_logged_in(self, timeout: float | None = None) -> None:
        """Block until the current websocket connection has logged in."""
        if timeout is None:
            await self._logged_in.wait()
        else:
            await asyncio.wait_for(self._logged_in.wait(), timeout=timeout)

    async def start(self) -> asyncio.Task[None]:
        if self._task and not self._task.done():
            return self._task
        if not self._token:
            raise MissingTokenError("call set_jwt_token() before start()")
        self._enabled = True
        self._exit.clear()
        self._task = asyncio.create_task(self._run(), name="liquidchat-mod-persistent")
        return self._task

    async def stop(self) -> None:
        self._enabled = False
        self._exit.set()
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None
        self._ws = None
        self._logged_in.clear()
        while not self._queue.empty():
            pending = self._queue.get_nowait()
            if not pending.future.done():
                pending.future.set_result(False)
            self._queue.task_done()

    async def ban_user(self, uuid: str) -> bool:
        return await self._enqueue("BanUser", uuid)

    async def unban_user(self, uuid: str) -> bool:
        return await self._enqueue("UnbanUser", uuid)

    async def _enqueue(self, action: Literal["BanUser", "UnbanUser"], uuid: str) -> bool:
        if not self.connected:
            logger.warning("liquidchat moderator not connected; %s for %s dropped", action, uuid)
            return False
        loop = asyncio.get_running_loop()
        future: asyncio.Future[bool] = loop.create_future()
        try:
            self._queue.put_nowait(_PendingAction(action=action, uuid=uuid, future=future))
        except asyncio.QueueFull:
            logger.error("liquidchat moderator queue full; dropping %s for %s", action, uuid)
            return False
        try:
            return await asyncio.wait_for(future, timeout=30.0)
        except TimeoutError:
            return False

    async def _run(self) -> None:
        attempt = 0
        while self._enabled and attempt < self.reconnect.max_attempts:
            try:
                connect_kwargs: dict[str, Any] = dict(
                    close_timeout=5,
                    max_size=10_485_760,
                    compression=None,
                    ping_interval=30,
                    ping_timeout=10,
                    proxy=None,
                )
                if self._url.startswith("wss://"):
                    connect_kwargs["ssl"] = build_ssl_context(insecure=self._insecure_ssl)
                async with websockets.connect(self._url, **connect_kwargs) as ws:
                    self._ws = ws
                    attempt = 0
                    try:
                        await self._login(ws)
                    except LoginFailedError as e:
                        raise RuntimeError(f"moderator login failed: {e}") from e
                    self._logged_in.set()
                    try:
                        await self._process_queue(ws)
                    finally:
                        self._logged_in.clear()
            except ConnectionClosed as e:
                logger.warning("liquidchat moderator closed: %s", e)
            except Exception:
                logger.exception("liquidchat moderator error")

            self._ws = None
            if not self._enabled:
                break
            attempt += 1
            delay = self.reconnect.delay(attempt)
            logger.info("liquidchat moderator reconnecting in %.1fs", delay)
            try:
                await asyncio.wait_for(self._exit.wait(), timeout=delay)
                break
            except TimeoutError:
                pass

    async def _login(self, ws: websockets.ClientConnection) -> None:
        assert self._token is not None
        await ws.send(encode("LoginJWT", {"token": self._token, "allow_messages": False}))
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

    async def _process_queue(self, ws: websockets.ClientConnection) -> None:
        while self._enabled:
            try:
                pending = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except TimeoutError:
                if self._exit.is_set():
                    return
                continue
            try:
                success = await self._perform(ws, pending.action, pending.uuid)
                if not pending.future.done():
                    pending.future.set_result(success)
            except ConnectionClosed:
                if not pending.future.done():
                    pending.future.set_result(False)
                raise
            except Exception:
                logger.exception("liquidchat moderator action error")
                if not pending.future.done():
                    pending.future.set_result(False)
            finally:
                self._queue.task_done()

    async def _perform(
        self, ws: websockets.ClientConnection, action: Literal["BanUser", "UnbanUser"], uuid: str
    ) -> bool:
        expected = "Ban" if action == "BanUser" else "Unban"
        await ws.send(encode(action, {"user": uuid}))
        deadline = asyncio.get_running_loop().time() + 5.0
        loop = asyncio.get_running_loop()
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return False
            raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            try:
                msg = decode(raw)
            except ProtocolError:
                continue
            if isinstance(msg.c, Success):
                if msg.c.reason == expected:
                    return True
                continue
            if isinstance(msg.c, Error):
                logger.error("%s for %s failed: %s", action, uuid, msg.c.message)
                return False


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
    "PersistentModeratorClient",
    "PrivateMessageHandler",
    "ReconnectPolicy",
    "UserCountHandler",
]
