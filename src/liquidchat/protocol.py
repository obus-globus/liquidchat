"""Lightweight helpers around the wire protocol."""

import json
import ssl
from typing import Any

from .models import LiquidChatMessage, parse_message

DEFAULT_WS_URL = "wss://chat.liquidbounce.net:7886/ws"


def build_ssl_context(*, insecure: bool = False) -> ssl.SSLContext:
    """Return an ``SSLContext`` for the chat websocket.

    By default the server's certificate is verified. Set ``insecure=True`` to
    disable verification (only useful for local testing).
    """
    if insecure:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return ssl.create_default_context()


def encode(message_type: str, content: dict[str, Any] | None = None) -> str:
    """Serialize a ``{"m": message_type, "c": content}`` envelope."""
    payload: dict[str, Any] = {"m": message_type}
    if content is not None:
        payload["c"] = content
    return json.dumps(payload)


def decode(raw: str | bytes) -> LiquidChatMessage:
    """Decode and parse a wire message.

    :raises ProtocolError: on invalid JSON or any structural problem.
    """
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        from .exceptions import ProtocolError

        raise ProtocolError(f"invalid JSON on the wire: {e}") from e
    return parse_message(data)


__all__ = ["DEFAULT_WS_URL", "build_ssl_context", "decode", "encode"]
