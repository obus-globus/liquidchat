"""LiquidChat client library.

A modern, typed websocket client for the LiquidChat protocol used by
``chat.liquidbounce.net``. Includes both one-shot helpers (send a message,
validate a JWT, ban a user) and long-running clients with automatic
reconnection.

Example::

    import asyncio
    from liquidchat import MinimalClient

    async def main() -> None:
        client = MinimalClient()
        client.set_jwt_token("...")
        await client.send_message("hello world")

    asyncio.run(main())
"""

from __future__ import annotations

from .client import JWTValidationClient, MinimalClient, ModeratorClient, ProgressCallback
from .exceptions import (
    LiquidChatError,
    LoginFailedError,
    MissingTokenError,
    NotAuthenticatedError,
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
    PersistentModeratorClient,
    PrivateMessageHandler,
    ReconnectPolicy,
    UserCountHandler,
)
from .protocol import DEFAULT_WS_URL, build_ssl_context, decode, encode

__version__ = "0.1.0"

__all__ = [
    "DEFAULT_WS_URL",
    "AuthorInfo",
    "Error",
    "ErrorHandler",
    "Handlers",
    "JWTValidationClient",
    "LifecycleHandler",
    "LiquidChatError",
    "LiquidChatMessage",
    "LoginFailedError",
    "MessageBody",
    "MessageContent",
    "MessageHandler",
    "MinimalClient",
    "MissingTokenError",
    "ModeratorClient",
    "MojangInfo",
    "NewJWT",
    "NotAuthenticatedError",
    "PersistentClient",
    "PersistentModeratorClient",
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
