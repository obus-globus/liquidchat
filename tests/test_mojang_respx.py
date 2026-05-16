"""respx-based tests for liquidchat.mojang.

This file exists alongside ``test_mojang.py`` to demonstrate the
respx idiom for httpx-aware mocking. The legacy file uses
``httpx.MockTransport`` directly, which is fine for tests that
inspect request payloads in custom ways; respx is the cleaner choice
for "stub this URL, return this response" cases like these.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from liquidchat.mojang import (
    MojangClient,
    MojangHTTPError,
    MojangProfile,
    MojangRateLimitError,
)

NOTCH_NAME = "Notch"
NOTCH_UUID_PLAIN = "069a79f444e94726a5befca90e38aaf5"
NOTCH_UUID_DASHED = "069a79f4-44e9-4726-a5be-fca90e38aaf5"


@pytest.mark.asyncio
@respx.mock(base_url="https://api.mojang.com")
async def test_lookup_by_name_with_respx(respx_mock: respx.Router) -> None:
    route = respx_mock.get(f"/users/profiles/minecraft/{NOTCH_NAME}").mock(
        return_value=httpx.Response(200, json={"id": NOTCH_UUID_PLAIN, "name": NOTCH_NAME})
    )
    async with MojangClient() as mojang:
        profile = await mojang.lookup_by_name(NOTCH_NAME)
    assert profile == MojangProfile(uuid=NOTCH_UUID_DASHED, name=NOTCH_NAME)
    assert route.called and route.call_count == 1


@pytest.mark.asyncio
@respx.mock(base_url="https://api.mojang.com")
async def test_lookup_by_name_404_returns_none_with_respx(respx_mock: respx.Router) -> None:
    respx_mock.get("/users/profiles/minecraft/Ghost").mock(return_value=httpx.Response(404))
    async with MojangClient() as mojang:
        assert await mojang.lookup_by_name("Ghost") is None


@pytest.mark.asyncio
@respx.mock(base_url="https://sessionserver.mojang.com")
async def test_lookup_by_uuid_with_respx(respx_mock: respx.Router) -> None:
    respx_mock.get(f"/session/minecraft/profile/{NOTCH_UUID_PLAIN}").mock(
        return_value=httpx.Response(
            200, json={"id": NOTCH_UUID_PLAIN, "name": NOTCH_NAME, "properties": []}
        )
    )
    async with MojangClient() as mojang:
        profile = await mojang.lookup_by_uuid(NOTCH_UUID_DASHED)
    assert profile == MojangProfile(uuid=NOTCH_UUID_DASHED, name=NOTCH_NAME)


@pytest.mark.asyncio
@respx.mock(base_url="https://api.mojang.com")
async def test_server_error_surfaces_with_respx(respx_mock: respx.Router) -> None:
    respx_mock.get(f"/users/profiles/minecraft/{NOTCH_NAME}").mock(
        return_value=httpx.Response(
            500,
            text="upstream went sideways",
            headers={"x-minecraft-rate-limit-result": "UNDER_LIMIT"},
        )
    )
    async with MojangClient() as mojang:
        with pytest.raises(MojangHTTPError) as excinfo:
            await mojang.lookup_by_name(NOTCH_NAME)
    assert excinfo.value.status_code == 500
    assert "upstream went sideways" in excinfo.value.body
    assert excinfo.value.rate_limit_result == "UNDER_LIMIT"


@pytest.mark.asyncio
@respx.mock(base_url="https://api.mojang.com")
async def test_rate_limit_429_raises_dedicated_error(respx_mock: respx.Router) -> None:
    respx_mock.get(f"/users/profiles/minecraft/{NOTCH_NAME}").mock(
        return_value=httpx.Response(
            429,
            text="slow down",
            headers={
                "x-minecraft-rate-limit-result": "OVER_LIMIT",
                "retry-after": "42",
            },
        )
    )
    async with MojangClient() as mojang:
        with pytest.raises(MojangRateLimitError) as excinfo:
            await mojang.lookup_by_name(NOTCH_NAME)
    assert excinfo.value.status_code == 429
    assert excinfo.value.retry_after == 42.0
    assert excinfo.value.rate_limit_result == "OVER_LIMIT"
    # MojangRateLimitError is still a MojangHTTPError, so generic
    # except clauses keep working.
    assert isinstance(excinfo.value, MojangHTTPError)


@pytest.mark.asyncio
@respx.mock(base_url="https://api.mojang.com")
async def test_cache_honours_max_age(respx_mock: respx.Router) -> None:
    route = respx_mock.get(f"/users/profiles/minecraft/{NOTCH_NAME}").mock(
        return_value=httpx.Response(
            200,
            json={"id": NOTCH_UUID_PLAIN, "name": NOTCH_NAME},
            headers={"cache-control": "max-age=300"},
        )
    )
    async with MojangClient() as mojang:
        first = await mojang.lookup_by_name(NOTCH_NAME)
        second = await mojang.lookup_by_name(NOTCH_NAME)
        # Case-insensitive cache key — second call with different case
        # still hits cache.
        third = await mojang.lookup_by_name(NOTCH_NAME.lower())
    assert first == second == third
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock(base_url="https://api.mojang.com")
async def test_cache_disabled_via_constructor(respx_mock: respx.Router) -> None:
    route = respx_mock.get(f"/users/profiles/minecraft/{NOTCH_NAME}").mock(
        return_value=httpx.Response(
            200,
            json={"id": NOTCH_UUID_PLAIN, "name": NOTCH_NAME},
            headers={"cache-control": "max-age=300"},
        )
    )
    async with MojangClient(cache=False) as mojang:
        await mojang.lookup_by_name(NOTCH_NAME)
        await mojang.lookup_by_name(NOTCH_NAME)
    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock(base_url="https://api.mojang.com")
async def test_cache_skipped_on_no_store(respx_mock: respx.Router) -> None:
    route = respx_mock.get(f"/users/profiles/minecraft/{NOTCH_NAME}").mock(
        return_value=httpx.Response(
            200,
            json={"id": NOTCH_UUID_PLAIN, "name": NOTCH_NAME},
            headers={"cache-control": "no-store"},
        )
    )
    async with MojangClient() as mojang:
        await mojang.lookup_by_name(NOTCH_NAME)
        await mojang.lookup_by_name(NOTCH_NAME)
    # no-store → both requests hit the network.
    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock(base_url="https://sessionserver.mojang.com")
async def test_uuid_cache_works(respx_mock: respx.Router) -> None:
    route = respx_mock.get(f"/session/minecraft/profile/{NOTCH_UUID_PLAIN}").mock(
        return_value=httpx.Response(
            200,
            json={"id": NOTCH_UUID_PLAIN, "name": NOTCH_NAME, "properties": []},
            headers={"cache-control": "max-age=20"},
        )
    )
    async with MojangClient() as mojang:
        first = await mojang.lookup_by_uuid(NOTCH_UUID_DASHED)
        # Cached lookup via the *other* UUID form should hit the cache.
        second = await mojang.lookup_by_uuid(NOTCH_UUID_PLAIN)
    assert first == second
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock(base_url="https://api.mojang.com")
async def test_clear_cache_forces_refetch(respx_mock: respx.Router) -> None:
    route = respx_mock.get(f"/users/profiles/minecraft/{NOTCH_NAME}").mock(
        return_value=httpx.Response(
            200,
            json={"id": NOTCH_UUID_PLAIN, "name": NOTCH_NAME},
            headers={"cache-control": "max-age=300"},
        )
    )
    async with MojangClient() as mojang:
        await mojang.lookup_by_name(NOTCH_NAME)
        await mojang.clear_cache()
        await mojang.lookup_by_name(NOTCH_NAME)
    assert route.call_count == 2
