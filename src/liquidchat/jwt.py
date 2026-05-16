"""Offline JWT inspection helpers.

These functions parse a JWT *without* verifying the signature — they
cannot tell whether the token was actually issued by the LiquidChat
server. What they CAN check, with no network round-trip:

- Is the string well-formed (three base64url segments)?
- Is the header sensible (``alg`` set, not ``none``)?
- Is the payload JSON-decodable?
- Are the LiquidChat-specific claims present (``user.name``,
  ``user.uuid``, ``exp``)?
- Has ``exp`` already passed (with an optional clock-skew margin)?

Use this for fast preflight ("don't bother opening a websocket if the
token is obviously stale") and UX feedback. For *real* validation —
proving the token was signed by axochat and the claims weren't
tampered with — use :meth:`liquidchat.Client.validate` / :meth:`validate_strict`,
which round-trip the server.

Example::

    from liquidchat.jwt import inspect_token, is_token_expired

    info = inspect_token(jwt)
    print(info.name, info.uuid, info.expires_at)

    if is_token_expired(jwt, leeway=30.0):
        await refresh()
"""

from __future__ import annotations

import base64
import binascii
import json
import time
from typing import Any, Final, cast

from pydantic import BaseModel, ConfigDict

from .exceptions import LiquidChatError

__all__ = [
    "InvalidTokenError",
    "TokenInfo",
    "decode_unverified_payload",
    "inspect_token",
    "is_token_expired",
    "seconds_until_expiry",
]


class InvalidTokenError(LiquidChatError):
    """Raised when a JWT is structurally malformed.

    Distinct from :class:`liquidchat.LoginFailedError`, which is for
    server-side rejection.
    """


# axochat issues tokens whose payload looks like ``{exp, user: {name, uuid}}``.
# See axochat_server/src/auth.rs (Claims struct).
_REQUIRED_USER_FIELDS: Final = ("name", "uuid")


class TokenInfo(BaseModel):
    """Decoded JWT payload (signature NOT verified)."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    name: str
    """Minecraft username embedded in ``claims.user.name``."""

    uuid: str
    """Minecraft UUID embedded in ``claims.user.uuid`` (dashed)."""

    expires_at: float
    """``exp`` claim as a unix timestamp (seconds)."""

    algorithm: str
    """JWT header ``alg`` field — axochat uses ``HS256``."""

    raw_header: dict[str, Any]
    raw_payload: dict[str, Any]

    def is_expired(self, *, now: float | None = None, leeway: float = 0.0) -> bool:
        """``True`` if ``expires_at`` has passed (minus ``leeway`` seconds)."""
        current = now if now is not None else time.time()
        return self.expires_at - leeway <= current

    def seconds_until_expiry(self, *, now: float | None = None) -> float:
        """Signed: positive while valid, negative once expired."""
        current = now if now is not None else time.time()
        return self.expires_at - current


def _b64url_decode(segment: str) -> bytes:
    # JWT uses base64url *without* padding; we restore it before decoding.
    padding = "=" * (-len(segment) % 4)
    try:
        return base64.urlsafe_b64decode(segment + padding)
    except (binascii.Error, ValueError) as e:
        raise InvalidTokenError(f"segment is not valid base64url: {e}") from e


def decode_unverified_payload(token: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return ``(header, payload)`` from a JWT without verifying the signature.

    Raises :class:`InvalidTokenError` on any structural problem.
    """
    if not token:
        raise InvalidTokenError("token must be a non-empty string")
    parts = token.split(".")
    if len(parts) != 3:
        raise InvalidTokenError(f"expected 3 dot-separated segments, got {len(parts)}")
    try:
        header_raw: Any = json.loads(_b64url_decode(parts[0]))
        payload_raw: Any = json.loads(_b64url_decode(parts[1]))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise InvalidTokenError(f"segment is not valid JSON: {e}") from e
    if not isinstance(header_raw, dict) or not isinstance(payload_raw, dict):
        raise InvalidTokenError("header / payload must decode to JSON objects")
    header = cast(dict[str, Any], header_raw)
    payload = cast(dict[str, Any], payload_raw)
    return header, payload


def inspect_token(token: str) -> TokenInfo:
    """Parse a JWT and return a :class:`TokenInfo` describing its claims.

    Does NOT verify the signature — only the server can do that. Raises
    :class:`InvalidTokenError` if the token is malformed, lacks the
    LiquidChat-specific claims, or uses the ``none`` algorithm.
    """
    header, payload = decode_unverified_payload(token)

    alg = header.get("alg")
    if not isinstance(alg, str) or alg.lower() == "none":
        raise InvalidTokenError(f"refusing token with alg={alg!r}")

    exp = payload.get("exp")
    if not isinstance(exp, int | float):
        raise InvalidTokenError("payload is missing numeric 'exp' claim")

    user_raw = payload.get("user")
    if not isinstance(user_raw, dict):
        raise InvalidTokenError("payload is missing 'user' object")
    user = cast(dict[str, Any], user_raw)
    extracted: dict[str, str] = {}
    for field in _REQUIRED_USER_FIELDS:
        value = user.get(field)
        if not isinstance(value, str) or not value:
            raise InvalidTokenError(f"payload.user is missing string field {field!r}")
        extracted[field] = value

    return TokenInfo(
        name=extracted["name"],
        uuid=extracted["uuid"],
        expires_at=float(exp),
        algorithm=alg,
        raw_header=header,
        raw_payload=payload,
    )


def is_token_expired(
    token: str,
    *,
    now: float | None = None,
    leeway: float = 0.0,
) -> bool:
    """Convenience: return ``True`` iff the token's ``exp`` has passed.

    ``leeway`` (seconds) shifts the deadline earlier — use a small
    positive value to refresh proactively before the token actually
    expires. Raises :class:`InvalidTokenError` on malformed input.
    """
    return inspect_token(token).is_expired(now=now, leeway=leeway)


def seconds_until_expiry(token: str, *, now: float | None = None) -> float:
    """Convenience: signed seconds until expiry. Negative once expired."""
    return inspect_token(token).seconds_until_expiry(now=now)
