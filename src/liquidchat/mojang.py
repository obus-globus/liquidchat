"""Mojang public-API helpers.

The liquidchat ``PersistentClient`` keeps a *local* username↔UUID cache
populated from inbound chat traffic. That cache only knows users who
have actually spoken since the client connected — it can't answer
questions about players that have been silent.

This module provides asynchronous helpers that fall back to Mojang's
public profile API for the unseen-user case. It is intentionally
separate from the chat client — no automatic calls are made by the
``PersistentClient`` itself; you opt in explicitly.

Endpoints used (all unauthenticated, lightly rate-limited by Mojang):

- ``GET https://api.mojang.com/users/profiles/minecraft/<name>``
  → ``{"id": "<undashed-uuid>", "name": "<canonical-name>"}`` (HTTP 200)
  → HTTP 404 when the name does not exist.
- ``GET https://sessionserver.mojang.com/session/minecraft/profile/<uuid>``
  → ``{"id": "...", "name": "...", "properties": [...]}`` (HTTP 200)
  → HTTP 204 / 404 when the UUID does not exist.

Quick start::

    from liquidchat.mojang import resolve_uuid, resolve_username

    uuid = await resolve_uuid("Notch")              # "069a79f4..."
    name = await resolve_username(uuid)             # "Notch"

For repeated lookups, reuse a single client::

    async with MojangClient() as mojang:
        for name in names:
            print(await mojang.resolve_uuid(name))

The module is opt-in: importing it pulls in :mod:`httpx`, which is
listed under the ``mojang`` extra in ``pyproject.toml``.
"""

from __future__ import annotations

import re
from types import TracebackType
from typing import Final, Self

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from .exceptions import LiquidChatError

__all__ = [
    "DEFAULT_PROFILE_URL",
    "DEFAULT_SESSION_URL",
    "MojangClient",
    "MojangError",
    "MojangHTTPError",
    "MojangProfile",
    "format_uuid",
    "resolve_username",
    "resolve_uuid",
    "strip_uuid",
]

DEFAULT_PROFILE_URL: Final = "https://api.mojang.com"
DEFAULT_SESSION_URL: Final = "https://sessionserver.mojang.com"

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{1,16}$")
_UUID_HYPHEN_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_UUID_PLAIN_RE = re.compile(r"^[0-9a-f]{32}$", re.IGNORECASE)


class MojangError(LiquidChatError):
    """Base class for Mojang-API errors."""


class MojangHTTPError(MojangError):
    """Raised when Mojang returns an unexpected (non-404) HTTP status."""

    def __init__(self, status_code: int, url: str, body: str) -> None:
        super().__init__(f"Mojang API {url} returned HTTP {status_code}: {body[:200]}")
        self.status_code = status_code
        self.url = url
        self.body = body


def strip_uuid(uuid: str) -> str:
    """Return the 32-char undashed form of a UUID. Accepts either form."""
    if _UUID_HYPHEN_RE.match(uuid):
        return uuid.replace("-", "").lower()
    if _UUID_PLAIN_RE.match(uuid):
        return uuid.lower()
    raise ValueError(f"not a valid UUID: {uuid!r}")


def format_uuid(uuid: str) -> str:
    """Return the canonical hyphenated 8-4-4-4-12 form. Accepts either form."""
    plain = strip_uuid(uuid)
    return f"{plain[0:8]}-{plain[8:12]}-{plain[12:16]}-{plain[16:20]}-{plain[20:32]}"


class MojangProfile(BaseModel):
    """A resolved Minecraft profile."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    uuid: str
    """Canonical hyphenated UUID (e.g. ``069a79f4-44e9-4726-a5be-fca90e38aaf5``)."""

    name: str
    """Current case-correct username."""

    @property
    def uuid_undashed(self) -> str:
        """32-char undashed UUID, the form Mojang uses on the wire."""
        return self.uuid.replace("-", "")


class MojangClient:
    """Async wrapper around Mojang's public profile API.

    Uses :class:`httpx.AsyncClient` under the hood. Safe to share across
    coroutines. Use as an ``async with`` block or call :meth:`close`
    explicitly. Module-level :func:`resolve_uuid` / :func:`resolve_username`
    create a throwaway client per call — fine for one-offs, but reuse
    a single instance for batches.

    All resolution methods return ``None`` on a clean "not found"
    (HTTP 404 or 204). Other HTTP failures raise :class:`MojangHTTPError`;
    network/timeout errors propagate as :class:`httpx.RequestError`.
    """

    def __init__(
        self,
        *,
        profile_url: str = DEFAULT_PROFILE_URL,
        session_url: str = DEFAULT_SESSION_URL,
        timeout: float = 10.0,
        client: httpx.AsyncClient | None = None,
        user_agent: str = "liquidchat/0.1 (+https://github.com/sokripon/olotldiscordbotnew)",
    ) -> None:
        self._profile_url = profile_url.rstrip("/")
        self._session_url = session_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=timeout,
            headers={"User-Agent": user_agent, "Accept": "application/json"},
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the underlying HTTP client (only if we created it)."""
        if self._owns_client:
            await self._client.aclose()

    async def resolve_uuid(self, username: str) -> str | None:
        """Return canonical hyphenated UUID for ``username``, or ``None``.

        Raises :class:`ValueError` on syntactically invalid usernames
        (letters / digits / underscore, 1-16 chars).
        """
        profile = await self.lookup_by_name(username)
        return profile.uuid if profile else None

    async def resolve_username(self, uuid: str) -> str | None:
        """Return canonical-case username for ``uuid``, or ``None``.

        Accepts either dashed or undashed UUID form.
        """
        profile = await self.lookup_by_uuid(uuid)
        return profile.name if profile else None

    async def lookup_by_name(self, username: str) -> MojangProfile | None:
        """Full profile lookup by name. Returns ``None`` on 404."""
        if not _USERNAME_RE.match(username):
            raise ValueError(f"not a valid Minecraft username: {username!r}")
        url = f"{self._profile_url}/users/profiles/minecraft/{username}"
        resp = await self._client.get(url)
        if resp.status_code in (204, 404):
            return None
        if resp.status_code != 200:
            raise MojangHTTPError(resp.status_code, url, resp.text)
        try:
            data = resp.json()
            return MojangProfile(uuid=format_uuid(data["id"]), name=data["name"])
        except (KeyError, TypeError, ValueError, ValidationError) as e:
            raise MojangHTTPError(resp.status_code, url, f"malformed response: {e}") from e

    async def lookup_by_uuid(self, uuid: str) -> MojangProfile | None:
        """Full profile lookup by UUID. Returns ``None`` on 204/404."""
        undashed = strip_uuid(uuid)
        url = f"{self._session_url}/session/minecraft/profile/{undashed}"
        resp = await self._client.get(url)
        if resp.status_code in (204, 404):
            return None
        if resp.status_code != 200:
            raise MojangHTTPError(resp.status_code, url, resp.text)
        try:
            data = resp.json()
            return MojangProfile(uuid=format_uuid(data["id"]), name=data["name"])
        except (KeyError, TypeError, ValueError, ValidationError) as e:
            raise MojangHTTPError(resp.status_code, url, f"malformed response: {e}") from e


async def resolve_uuid(username: str, *, timeout: float = 10.0) -> str | None:
    """Convenience: one-shot UUID lookup. Creates and tears down a client."""
    async with MojangClient(timeout=timeout) as client:
        return await client.resolve_uuid(username)


async def resolve_username(uuid: str, *, timeout: float = 10.0) -> str | None:
    """Convenience: one-shot username lookup. Creates and tears down a client."""
    async with MojangClient(timeout=timeout) as client:
        return await client.resolve_username(uuid)
