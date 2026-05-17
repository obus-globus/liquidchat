"""Mojang public-API helpers.

The liquidchat ``PersistentClient`` keeps a *local* username↔UUID cache
populated from inbound chat traffic. That cache only knows users who
have actually spoken since the client connected — it can't answer
questions about players that have been silent.

This module provides asynchronous helpers that fall back to Mojang's
public profile API for the unseen-user case. It is intentionally
separate from the chat client — no automatic calls are made by the
``PersistentClient`` itself; you opt in explicitly.

Under the hood the HTTP work is delegated to ``mcapi-auth``; this
module adds a small process-local TTL cache and single-flight dedup
on top, both of which mcapi-auth deliberately leaves to the caller.

Endpoints used (all unauthenticated, lightly rate-limited by Mojang):

- ``GET https://api.mojang.com/users/profiles/minecraft/<name>``
- ``GET https://sessionserver.mojang.com/session/minecraft/profile/<uuid>``

Quick start::

    from liquidchat.mojang import resolve_uuid, resolve_username

    uuid = await resolve_uuid("Notch")              # "069a79f4..."
    name = await resolve_username(uuid)             # "Notch"

For repeated lookups, reuse a single client::

    async with MojangClient() as mojang:
        for name in names:
            print(await mojang.resolve_uuid(name))
"""

import asyncio
import re
import time
from collections import OrderedDict
from types import TracebackType
from typing import Final, Self

import httpx
from mcapi_auth import (
    BadRequestError as _McBadRequest,
)
from mcapi_auth import (
    HttpError as _McHttpError,
)
from mcapi_auth import (
    NotFoundError as _McNotFound,
)
from mcapi_auth import (
    RateLimitedError as _McRateLimited,
)
from mcapi_auth import (
    get_profile_by_uuid as _mcapi_get_profile_by_uuid,
)
from mcapi_auth import (
    get_uuid_by_name as _mcapi_get_uuid_by_name,
)
from pydantic import BaseModel, ConfigDict

from .exceptions import LiquidChatError

__all__ = [
    "MojangClient",
    "MojangError",
    "MojangHTTPError",
    "MojangProfile",
    "MojangRateLimitError",
    "format_uuid",
    "resolve_username",
    "resolve_uuid",
    "strip_uuid",
]

DEFAULT_PROFILE_TTL: Final = 300.0
DEFAULT_SESSION_TTL: Final = 20.0
DEFAULT_CACHE_MAXSIZE: Final = 10_000

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{1,16}$")
_UUID_HYPHEN_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_UUID_PLAIN_RE = re.compile(r"^[0-9a-f]{32}$", re.IGNORECASE)


class MojangError(LiquidChatError):
    """Base class for Mojang-API errors."""


class MojangHTTPError(MojangError):
    """Raised when Mojang returns an unexpected (non-404) HTTP status.

    .. note::

       ``rate_limit_result`` (the ``X-Minecraft-Rate-Limit-Result``
       header) is no longer populated since the actual HTTP work moved
       into ``mcapi-auth``, which doesn't surface that header. The
       attribute is kept for back-compat and is always ``None``.
    """

    def __init__(
        self,
        status_code: int,
        url: str | httpx.URL,
        body: str,
        *,
        rate_limit_result: str | None = None,
    ) -> None:
        super().__init__(f"Mojang API {url} returned HTTP {status_code}: {body[:200]}")
        self.status_code = status_code
        self.url = str(url)
        self.body = body
        self.rate_limit_result = rate_limit_result


class MojangRateLimitError(MojangHTTPError):
    """Raised on HTTP 429.

    Mojang does not currently send a ``Retry-After`` header, but we
    parse it if present. Callers should otherwise back off for ~60s
    (Mojang's documented limit is around 600 requests / 10 min per IP).
    """

    def __init__(
        self,
        url: str | httpx.URL,
        body: str,
        *,
        retry_after: float | None = None,
        rate_limit_result: str | None = None,
    ) -> None:
        super().__init__(429, url, body, rate_limit_result=rate_limit_result)
        self.retry_after = retry_after


_NAME_LOOKUP_URL = "https://api.mojang.com/users/profiles/minecraft"
_SESSION_LOOKUP_URL = "https://sessionserver.mojang.com/session/minecraft/profile"


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


class _TTLCache:
    """Tiny async-safe TTL cache with LRU eviction. Keys → ``MojangProfile``.

    Expiry is computed against :func:`time.monotonic` at insert time.
    Expired entries are dropped lazily on read. When the cache is full,
    the least-recently-used entry is evicted on insert to keep memory
    bounded — caller-controlled keys (usernames, UUIDs) can't grow the
    cache without limit.
    """

    __slots__ = ("_data", "_lock", "_maxsize")

    def __init__(self, maxsize: int = DEFAULT_CACHE_MAXSIZE) -> None:
        self._data: OrderedDict[str, tuple[float, MojangProfile]] = OrderedDict()
        self._lock = asyncio.Lock()
        self._maxsize = maxsize

    async def get(self, key: str) -> MojangProfile | None:
        async with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            expiry, value = entry
            if expiry < time.monotonic():
                del self._data[key]
                return None
            self._data.move_to_end(key)
            return value

    async def set(self, key: str, value: MojangProfile, ttl: float) -> None:
        if ttl <= 0:
            return
        async with self._lock:
            self._data[key] = (time.monotonic() + ttl, value)
            self._data.move_to_end(key)
            while len(self._data) > self._maxsize:
                self._data.popitem(last=False)

    async def clear(self) -> None:
        async with self._lock:
            self._data.clear()

    def __len__(self) -> int:
        return len(self._data)


def _translate_mcapi_error(
    exc: _McHttpError | _McRateLimited | _McBadRequest, fallback_url: str
) -> MojangHTTPError:
    """Map an mcapi-auth REST error onto the liquidchat exception tree.

    ``mcapi_auth.NotFoundError`` is intentionally not handled here —
    callers translate "no such account" into ``None`` directly.
    """
    if isinstance(exc, _McRateLimited):
        return MojangRateLimitError(fallback_url, "", retry_after=exc.retry_after)
    if isinstance(exc, _McBadRequest):
        # mcapi-auth raises this for a syntactically-bad name as well;
        # callers see a fully-formed HTTP error rather than a ValueError.
        return MojangHTTPError(400, fallback_url, str(exc))
    status = getattr(exc, "status_code", 0) or 0
    body = getattr(exc, "body", "") or ""
    url = getattr(exc, "url", None) or fallback_url
    # mcapi-auth wraps a Pydantic ValidationError on a 2xx response in
    # HttpError(status=200, body=raw); surface that as a malformed-body
    # error so callers can distinguish it from a real server failure.
    if 200 <= status < 300:
        return MojangHTTPError(status, url, f"malformed response body: {body[:200]}")
    return MojangHTTPError(status, url, body)


class MojangClient:
    """Async wrapper around Mojang's public profile API.

    Uses :class:`httpx.AsyncClient` under the hood. Safe to share across
    coroutines. Use as an ``async with`` block or call :meth:`close`
    explicitly. Module-level :func:`resolve_uuid` / :func:`resolve_username`
    create a throwaway client per call — fine for one-offs, but reuse
    a single instance for batches.

    All resolution methods return ``None`` on a clean "not found"
    (HTTP 404 or 204). HTTP 429 raises :class:`MojangRateLimitError`
    (a subclass of :class:`MojangHTTPError`). Other HTTP failures raise
    :class:`MojangHTTPError`. Network/timeout errors propagate as
    :class:`httpx.RequestError`.

    Successful (200) responses are cached in-process with a fixed
    default TTL. Pass ``cache=False`` to disable, or call
    :meth:`clear_cache` to reset. Caching is per-client — sharing one
    client across your app maximises hit-rate.
    """

    def __init__(
        self,
        *,
        timeout: float = 10.0,
        client: httpx.AsyncClient | None = None,
        user_agent: str = "liquidchat/0.1 (+https://github.com/sokripon/olotldiscordbotnew)",
        cache: bool = True,
    ) -> None:
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=timeout,
            headers={"User-Agent": user_agent, "Accept": "application/json"},
        )
        self._name_cache: _TTLCache | None = _TTLCache() if cache else None
        self._uuid_cache: _TTLCache | None = _TTLCache() if cache else None
        # Single-flight: dedupes concurrent identical lookups so that a
        # cold-cache burst (e.g. N coroutines all asking for "Notch")
        # hits Mojang once, not N times. Kept independent of the cache
        # because it's useful even when cache=False.
        self._name_inflight: dict[str, asyncio.Future[MojangProfile | None]] = {}
        self._uuid_inflight: dict[str, asyncio.Future[MojangProfile | None]] = {}

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

    async def clear_cache(self) -> None:
        """Drop all cached profile entries."""
        if self._name_cache is not None:
            await self._name_cache.clear()
        if self._uuid_cache is not None:
            await self._uuid_cache.clear()

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
        key = username.lower()
        if self._name_cache is not None:
            cached = await self._name_cache.get(key)
            if cached is not None:
                return cached
        existing = self._name_inflight.get(key)
        if existing is not None:
            return await asyncio.shield(existing)
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[MojangProfile | None] = loop.create_future()
        self._name_inflight[key] = fut
        try:
            result = await self._fetch_by_name(username, key)
        except BaseException as e:
            if not fut.done():
                fut.set_exception(e)
            raise
        else:
            if not fut.done():
                fut.set_result(result)
            return result
        finally:
            self._name_inflight.pop(key, None)
            if not fut.done():
                fut.cancel()

    async def _fetch_by_name(self, username: str, key: str) -> MojangProfile | None:
        try:
            lookup = await _mcapi_get_uuid_by_name(username, http_client=self._client)
        except _McNotFound:
            return None
        except (_McRateLimited, _McBadRequest, _McHttpError) as e:
            raise _translate_mcapi_error(e, f"{_NAME_LOOKUP_URL}/{username}") from e
        profile = MojangProfile(uuid=format_uuid(lookup.uuid), name=lookup.name)
        if self._name_cache is not None:
            await self._name_cache.set(key, profile, DEFAULT_PROFILE_TTL)
        return profile

    async def lookup_by_uuid(self, uuid: str) -> MojangProfile | None:
        """Full profile lookup by UUID. Returns ``None`` on 204/404."""
        undashed = strip_uuid(uuid)
        if self._uuid_cache is not None:
            cached = await self._uuid_cache.get(undashed)
            if cached is not None:
                return cached
        existing = self._uuid_inflight.get(undashed)
        if existing is not None:
            return await asyncio.shield(existing)
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[MojangProfile | None] = loop.create_future()
        self._uuid_inflight[undashed] = fut
        try:
            result = await self._fetch_by_uuid(undashed)
        except BaseException as e:
            if not fut.done():
                fut.set_exception(e)
            raise
        else:
            if not fut.done():
                fut.set_result(result)
            return result
        finally:
            self._uuid_inflight.pop(undashed, None)
            if not fut.done():
                fut.cancel()

    async def _fetch_by_uuid(self, undashed: str) -> MojangProfile | None:
        try:
            public = await _mcapi_get_profile_by_uuid(undashed, http_client=self._client)
        except _McNotFound:
            return None
        except (_McRateLimited, _McBadRequest, _McHttpError) as e:
            raise _translate_mcapi_error(e, f"{_SESSION_LOOKUP_URL}/{undashed}") from e
        profile = MojangProfile(uuid=format_uuid(public.uuid), name=public.name)
        if self._uuid_cache is not None:
            await self._uuid_cache.set(undashed, profile, DEFAULT_SESSION_TTL)
        return profile


async def resolve_uuid(username: str, *, timeout: float = 10.0) -> str | None:
    """Convenience: one-shot UUID lookup. Creates and tears down a client."""
    async with MojangClient(timeout=timeout) as client:
        return await client.resolve_uuid(username)


async def resolve_username(uuid: str, *, timeout: float = 10.0) -> str | None:
    """Convenience: one-shot username lookup. Creates and tears down a client."""
    async with MojangClient(timeout=timeout) as client:
        return await client.resolve_username(uuid)
