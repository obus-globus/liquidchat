"""Wire-format models for the LiquidChat protocol.

The server uses tiny tagged JSON envelopes::

    {"m": "<message-type>", "c": {<payload>}}

Some message types (``RequestMojangInfo``, ``RequestJWT``, ``RequestUserCount``)
have no ``c`` body. :func:`parse_message` returns a tagged-union dataclass.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from .exceptions import ProtocolError

SuccessReason = Literal["Login", "Ban", "Unban"]
"""Server-defined ``Success.reason`` values."""


@dataclass(slots=True, frozen=True)
class AuthorInfo:
    """Identity of a chat message author."""

    name: str
    uuid: str


@dataclass(slots=True, frozen=True)
class MessageContent:
    """Body of a ``Message`` or ``PrivateMessage``."""

    author_info: AuthorInfo
    content: str


@dataclass(slots=True, frozen=True)
class UserCount:
    """Body of a ``UserCount`` message."""

    connections: int
    logged_in: int


@dataclass(slots=True, frozen=True)
class MojangInfo:
    """Body of a ``MojangInfo`` message — a server-issued challenge hash."""

    session_hash: str


@dataclass(slots=True, frozen=True)
class NewJWT:
    """Body of a ``NewJWT`` message."""

    token: str


@dataclass(slots=True, frozen=True)
class Error:
    """Body of an ``Error`` message.

    The server's ``ClientError`` is a Rust enum serialized by serde:

    * Unit variants come through as bare strings (e.g. ``"NotPermitted"``).
    * Tuple variants come through as objects
      (e.g. ``{"InvalidCharacter": "x"}``).

    We keep the original shape so callers can pattern-match against it.
    """

    message: str | dict[str, Any]


@dataclass(slots=True, frozen=True)
class Success:
    """Body of a ``Success`` message."""

    reason: SuccessReason


MessageBody = MessageContent | UserCount | MojangInfo | NewJWT | Error | Success | None


@dataclass(slots=True, frozen=True)
class LiquidChatMessage:
    """A parsed ``{"m": ..., "c": ...}`` envelope."""

    m: str
    c: MessageBody


_REQUEST_TYPES = frozenset({"RequestMojangInfo", "RequestJWT", "RequestUserCount"})


def parse_message(data: dict[str, Any]) -> LiquidChatMessage:
    """Parse a decoded JSON envelope.

    :raises ProtocolError: if ``data`` is not a well-formed envelope.
    """
    if not isinstance(data, dict) or "m" not in data:
        raise ProtocolError(f"missing 'm' field: {data!r}")
    msg_type = data["m"]
    payload = data.get("c")

    if msg_type in _REQUEST_TYPES:
        return LiquidChatMessage(m=msg_type, c=None)
    if payload is None:
        raise ProtocolError(f"missing 'c' field for {msg_type!r}")

    try:
        body: MessageBody
        match msg_type:
            case "Message" | "PrivateMessage":
                body = MessageContent(
                    author_info=AuthorInfo(**payload["author_info"]),
                    content=payload["content"],
                )
            case "UserCount":
                body = UserCount(**payload)
            case "MojangInfo":
                body = MojangInfo(**payload)
            case "NewJWT":
                body = NewJWT(**payload)
            case "Success":
                body = Success(**payload)
            case "Error":
                body = Error(**payload)
            case _:
                raise ProtocolError(f"unknown message type: {msg_type!r}")
    except (KeyError, TypeError) as e:
        raise ProtocolError(f"malformed {msg_type!r}: {e}") from e

    return LiquidChatMessage(m=msg_type, c=body)


__all__ = [
    "AuthorInfo",
    "Error",
    "LiquidChatMessage",
    "MessageBody",
    "MessageContent",
    "MojangInfo",
    "NewJWT",
    "Success",
    "SuccessReason",
    "UserCount",
    "parse_message",
]
