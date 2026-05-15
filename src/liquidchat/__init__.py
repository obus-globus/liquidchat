"""LiquidChat client library.

A modern, typed websocket client for the LiquidChat protocol used by
``chat.liquidbounce.net``. Two clients cover every operation:

- :class:`Client` — one-shot. Opens a fresh websocket, performs an
  operation (validate / send_message / ban / unban / batch ban) and
  closes. For low-frequency work or CLI tools.
- :class:`PersistentClient` — long-lived. Auto-reconnects, delivers
  inbound messages to :class:`Handlers` callbacks, sends chat, and
  performs ban / unban actions on the same connection.

Example::

    import asyncio
    from liquidchat import Client

    async def main() -> None:
        client = Client(token="...")
        await client.send_message("hello world")

    asyncio.run(main())
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

from .client import Client, ProgressCallback
from .exceptions import (
    LiquidChatError,
    LoginFailedError,
    MissingTokenError,
    ProtocolError,
)
from .models import (
    AuthorInfo,
    Error,
    LiquidChatMessage,
    MessageBody,
    MessageContent,
    MojangInfo,
    NewJWT,
    Success,
    SuccessReason,
    UserCount,
    parse_message,
)
from .persistent import (
    ErrorHandler,
    Handlers,
    LifecycleHandler,
    MessageHandler,
    PersistentClient,
    PrivateMessageHandler,
    ReconnectPolicy,
    UserCountHandler,
)
from .protocol import DEFAULT_WS_URL, build_ssl_context, decode, encode

try:
    __version__ = _pkg_version("liquidchat")
except PackageNotFoundError:  # editable install before any package metadata is built
    __version__ = "0.0.0+unknown"

__all__ = [
    "DEFAULT_WS_URL",
    "AuthorInfo",
    "Client",
    "Error",
    "ErrorHandler",
    "Handlers",
    "LifecycleHandler",
    "LiquidChatError",
    "LiquidChatMessage",
    "LoginFailedError",
    "MessageBody",
    "MessageContent",
    "MessageHandler",
    "MissingTokenError",
    "MojangInfo",
    "NewJWT",
    "PersistentClient",
    "PrivateMessageHandler",
    "ProgressCallback",
    "ProtocolError",
    "ReconnectPolicy",
    "Success",
    "SuccessReason",
    "UserCount",
    "UserCountHandler",
    "__version__",
    "build_ssl_context",
    "decode",
    "encode",
    "parse_message",
]
