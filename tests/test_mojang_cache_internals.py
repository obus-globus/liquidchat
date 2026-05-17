"""Direct unit tests for the in-process TTL/LRU cache.

These poke at the private ``_TTLCache`` because exercising LRU
eviction end-to-end through ``MojangClient`` would require synthesising
thousands of distinct respx-routed responses. Keeping these here lets
us assert the invariants directly.
"""

import asyncio
import time

import pytest

from liquidchat.mojang import (
    MojangProfile,
    _TTLCache,
)

NOTCH_DASHED = "069a79f4-44e9-4726-a5be-fca90e38aaf5"


def _profile(name: str = "Notch") -> MojangProfile:
    return MojangProfile(uuid=NOTCH_DASHED, name=name)


@pytest.mark.asyncio
async def test_ttl_cache_lru_evicts_oldest() -> None:
    cache = _TTLCache(maxsize=3)
    await cache.set("a", _profile("a"), ttl=300)
    await cache.set("b", _profile("b"), ttl=300)
    await cache.set("c", _profile("c"), ttl=300)
    assert len(cache) == 3
    # Inserting a 4th must drop "a" (oldest).
    await cache.set("d", _profile("d"), ttl=300)
    assert len(cache) == 3
    assert await cache.get("a") is None
    assert (await cache.get("b")) is not None
    assert (await cache.get("c")) is not None
    assert (await cache.get("d")) is not None


@pytest.mark.asyncio
async def test_ttl_cache_get_refreshes_lru_position() -> None:
    cache = _TTLCache(maxsize=3)
    await cache.set("a", _profile("a"), ttl=300)
    await cache.set("b", _profile("b"), ttl=300)
    await cache.set("c", _profile("c"), ttl=300)
    # Touch "a" → it's now most-recently-used.
    await cache.get("a")
    # Inserting "d" must now evict "b" (the new oldest), not "a".
    await cache.set("d", _profile("d"), ttl=300)
    assert (await cache.get("a")) is not None
    assert await cache.get("b") is None
    assert (await cache.get("c")) is not None
    assert (await cache.get("d")) is not None


@pytest.mark.asyncio
async def test_ttl_cache_skips_zero_ttl() -> None:
    cache = _TTLCache(maxsize=10)
    await cache.set("a", _profile("a"), ttl=0)
    await cache.set("b", _profile("b"), ttl=-1)
    assert len(cache) == 0
    assert await cache.get("a") is None


@pytest.mark.asyncio
async def test_ttl_cache_drops_expired_entry_on_read() -> None:
    cache = _TTLCache(maxsize=10)
    await cache.set("a", _profile("a"), ttl=0.05)
    assert (await cache.get("a")) is not None
    await asyncio.sleep(0.1)
    assert await cache.get("a") is None
    # And the expired entry should be removed, not just hidden.
    assert len(cache) == 0


@pytest.mark.asyncio
async def test_ttl_cache_clear() -> None:
    cache = _TTLCache(maxsize=10)
    await cache.set("a", _profile("a"), ttl=300)
    await cache.set("b", _profile("b"), ttl=300)
    await cache.clear()
    assert len(cache) == 0
    assert await cache.get("a") is None


@pytest.mark.asyncio
async def test_ttl_cache_monotonic_clock_not_wall_clock() -> None:
    """Cache uses time.monotonic, so it shouldn't be affected by
    wall-clock jumps. Sanity-check the invariant."""
    cache = _TTLCache(maxsize=10)
    before = time.monotonic()
    await cache.set("a", _profile("a"), ttl=1.0)
    after = time.monotonic()
    # The expiry timestamp lives in monotonic-time and is in the
    # near future, regardless of system wall clock.
    assert after - before < 1.0
    assert (await cache.get("a")) is not None
