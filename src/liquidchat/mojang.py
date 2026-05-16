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

import asyncio
import re
import time
from collections import OrderedDict
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
    "MojangRateLimitError",
    "format_uuid",
    "resolve_username",
    "resolve_uuid",
    "strip_uuid",
]

DEFAULT_PROFILE_URL: Final = "https://api.mojang.com"
DEFAULT_SESSION_URL: Final = "https://sessionserver.mojang.com"
DEFAULT_PROFILE_TTL: Final = 300.0
DEFAULT_SESSION_TTL: Final = 20.0
DEFAULT_CACHE_MAXSIZE: Final = 10_000
# Cap any single Cache-Control max-age at 7 days to avoid permanent
# poisoning if an upstream/proxy ever returns a wildly large value.
MAX_CACHE_TTL: Final = 7 * 24 * 60 * 60.0

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{1,16}$")
_UUID_HYPHEN_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_UUID_PLAIN_RE = re.compile(r"^[0-9a-f]{32}$", re.IGNORECASE)
_MAX_AGE_RE = re.compile(r"max-age\s*=\s*(\d+)", re.IGNORECASE)


def _cache_ttl_from_response(cache_control: str | None, default_ttl: float) -> float:
    """Return the TTL to apply for a successful response.

    Returns ``0`` when the response carries ``no-store`` / ``no-cache``
    (caller should skip caching). Returns ``max-age`` when present
    (capped at :data:`MAX_CACHE_TTL` to avoid permanent caching from
    malformed upstream values), otherwise falls back to ``default_ttl``.
    """
    if cache_control:
        cc = cache_control.lower()
        if "no-store" in cc or "no-cache" in cc:
            return 0.0
        m = _MAX_AGE_RE.search(cache_control)
        if m:
            return min(float(m.group(1)), MAX_CACHE_TTL)
    return default_ttl


def _parse_retry_after(retry_after: str | None) -> float | None:
    """Parse a ``Retry-After`` header. Returns seconds, or None on failure."""
    if not retry_after:
        return None
    try:
        return float(retry_after.strip())
    except ValueError:
        return None


class MojangError(LiquidChatError):
    """Base class for Mojang-API errors."""


class MojangHTTPError(MojangError):
    """Raised when Mojang returns an unexpected (non-404) HTTP status.

    Mojang exposes its rate-limit verdict via the
    ``X-Minecraft-Rate-Limit-Result`` header (e.g. ``UNDER_LIMIT`` /
    ``OVER_LIMIT``). When present, it's surfaced on
    :attr:`rate_limit_result` for easier triage.
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

    By default, successful (200) responses are cached in-process,
    honouring the upstream ``Cache-Control: max-age=N`` header.
    Pass ``cache=False`` to disable, or call :meth:`clear_cache`
    to reset. Caching is per-client — sharing one client across your
    app maximises hit-rate.
    """

    def __init__(
        self,
        *,
        profile_url: str | httpx.URL = DEFAULT_PROFILE_URL,
        session_url: str | httpx.URL = DEFAULT_SESSION_URL,
        timeout: float = 10.0,
        client: httpx.AsyncClient | None = None,
        user_agent: str = "liquidchat/0.1 (+https://github.com/sokripon/olotldiscordbotnew)",
        cache: bool = True,
    ) -> None:
        self._profile_url = httpx.URL(profile_url)
        self._session_url = httpx.URL(session_url)
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

    @staticmethod
    def _raise_for_status(resp: httpx.Response, url: httpx.URL) -> None:
        rate_limit_result = resp.headers.get("x-minecraft-rate-limit-result")
        if resp.status_code == 429:
            raise MojangRateLimitError(
                url,
                resp.text,
                retry_after=_parse_retry_after(resp.headers.get("retry-after")),
                rate_limit_result=rate_limit_result,
            )
        raise MojangHTTPError(resp.status_code, url, resp.text, rate_limit_result=rate_limit_result)

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
        url = self._profile_url.copy_with(path=f"/users/profiles/minecraft/{username}")
        resp = await self._client.get(url)
        if resp.status_code in (204, 404):
            return None
        if resp.status_code != 200:
            self._raise_for_status(resp, url)
        try:
            data = resp.json()
            profile = MojangProfile(uuid=format_uuid(data["id"]), name=data["name"])
        except (KeyError, TypeError, ValueError, ValidationError) as e:
            raise MojangHTTPError(
                resp.status_code,
                url,
                f"malformed response: {e}",
                rate_limit_result=resp.headers.get("x-minecraft-rate-limit-result"),
            ) from e
        if self._name_cache is not None:
            ttl = _cache_ttl_from_response(resp.headers.get("cache-control"), DEFAULT_PROFILE_TTL)
            await self._name_cache.set(key, profile, ttl)
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
        url = self._session_url.copy_with(path=f"/session/minecraft/profile/{undashed}")
        resp = await self._client.get(url)
        if resp.status_code in (204, 404):
            return None
        if resp.status_code != 200:
            self._raise_for_status(resp, url)
        try:
            data = resp.json()
            profile = MojangProfile(uuid=format_uuid(data["id"]), name=data["name"])
        except (KeyError, TypeError, ValueError, ValidationError) as e:
            raise MojangHTTPError(
                resp.status_code,
                url,
                f"malformed response: {e}",
                rate_limit_result=resp.headers.get("x-minecraft-rate-limit-result"),
            ) from e
        if self._uuid_cache is not None:
            ttl = _cache_ttl_from_response(resp.headers.get("cache-control"), DEFAULT_SESSION_TTL)
            await self._uuid_cache.set(undashed, profile, ttl)
        return profile


async def resolve_uuid(username: str, *, timeout: float = 10.0) -> str | None:
    """Convenience: one-shot UUID lookup. Creates and tears down a client."""
    async with MojangClient(timeout=timeout) as client:
        return await client.resolve_uuid(username)


async def resolve_username(uuid: str, *, timeout: float = 10.0) -> str | None:
    """Convenience: one-shot username lookup. Creates and tears down a client."""
    async with MojangClient(timeout=timeout) as client:
        return await client.resolve_username(uuid)
