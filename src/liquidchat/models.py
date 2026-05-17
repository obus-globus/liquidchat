"""Wire-format models for the LiquidChat protocol.

The server uses tiny tagged JSON envelopes::

    {"m": "<message-type>", "c": {<payload>}}

Some message types (``RequestMojangInfo``, ``RequestJWT``, ``RequestUserCount``)
have no ``c`` body. :func:`parse_message` returns a tagged-union model.
"""

from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, ValidationError

from .exceptions import ProtocolError

SuccessReason = Literal["Login", "Ban", "Unban"]
"""Server-defined ``Success.reason`` values."""


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")


class AuthorInfo(_Frozen):
    """Identity of a chat message author."""

    name: str
    uuid: str


class MessageContent(_Frozen):
    """Body of a ``Message`` or ``PrivateMessage``."""

    author_info: AuthorInfo
    content: str


class UserCount(_Frozen):
    """Body of a ``UserCount`` message."""

    connections: int
    logged_in: int


class MojangInfo(_Frozen):
    """Body of a ``MojangInfo`` message — a server-issued challenge hash."""

    session_hash: str


class NewJWT(_Frozen):
    """Body of a ``NewJWT`` message."""

    token: str


class Error(_Frozen):
    """Body of an ``Error`` message.

    The server's ``ClientError`` is a Rust enum serialized by serde:

    * Unit variants come through as bare strings (e.g. ``"NotPermitted"``).
    * Tuple variants come through as objects
      (e.g. ``{"InvalidCharacter": "x"}``).

    We keep the original shape so callers can pattern-match against it.
    """

    message: str | dict[str, Any]


class Success(_Frozen):
    """Body of a ``Success`` message."""

    reason: SuccessReason


MessageBody = MessageContent | UserCount | MojangInfo | NewJWT | Error | Success | None


class LiquidChatMessage(_Frozen):
    """A parsed ``{"m": ..., "c": ...}`` envelope."""

    m: str
    c: MessageBody


_REQUEST_TYPES = frozenset({"RequestMojangInfo", "RequestJWT", "RequestUserCount"})

_BODY_MODELS: dict[str, type[BaseModel]] = {
    "Message": MessageContent,
    "PrivateMessage": MessageContent,
    "UserCount": UserCount,
    "MojangInfo": MojangInfo,
    "NewJWT": NewJWT,
    "Success": Success,
    "Error": Error,
}


def parse_message(data: Any) -> LiquidChatMessage:
    """Parse a decoded JSON envelope.

    Accepts ``Any`` because the input is untrusted (the wire). All
    structural validation happens here.

    :raises ProtocolError: if ``data`` is not a well-formed envelope.
    """
    if not isinstance(data, dict):
        raise ProtocolError(f"expected dict envelope, got {type(data).__name__}: {data!r}")
    data_dict = cast(dict[str, Any], data)
    if "m" not in data_dict:
        raise ProtocolError(f"missing 'm' field: {data_dict!r}")
    msg_type = data_dict["m"]
    if not isinstance(msg_type, str):
        raise ProtocolError(f"'m' must be a string, got {type(msg_type).__name__}: {msg_type!r}")

    if msg_type in _REQUEST_TYPES:
        return LiquidChatMessage(m=msg_type, c=None)

    payload = data_dict.get("c")
    if payload is None:
        raise ProtocolError(f"missing 'c' field for {msg_type!r}")

    model = _BODY_MODELS.get(msg_type)
    if model is None:
        raise ProtocolError(f"unknown message type: {msg_type!r}")

    try:
        body = cast(MessageBody, model.model_validate(payload))
    except ValidationError as e:
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
