"""Unit tests for liquidchat.mojang (no real network calls)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from liquidchat.mojang import (
    DEFAULT_PROFILE_URL,
    DEFAULT_SESSION_URL,
    MojangClient,
    MojangHTTPError,
    MojangProfile,
    format_uuid,
    resolve_username,
    resolve_uuid,
    strip_uuid,
)

NOTCH_NAME = "Notch"
NOTCH_UUID_PLAIN = "069a79f444e94726a5befca90e38aaf5"
NOTCH_UUID_DASHED = "069a79f4-44e9-4726-a5be-fca90e38aaf5"


def _make_client(handler: httpx.MockTransport) -> MojangClient:
    raw = httpx.AsyncClient(transport=handler)
    return MojangClient(client=raw)


def test_strip_uuid_accepts_both_forms() -> None:
    assert strip_uuid(NOTCH_UUID_DASHED) == NOTCH_UUID_PLAIN
    assert strip_uuid(NOTCH_UUID_PLAIN) == NOTCH_UUID_PLAIN
    assert strip_uuid(NOTCH_UUID_DASHED.upper()) == NOTCH_UUID_PLAIN


def test_strip_uuid_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        strip_uuid("not-a-uuid")
    with pytest.raises(ValueError):
        strip_uuid("")


def test_format_uuid_canonical() -> None:
    assert format_uuid(NOTCH_UUID_PLAIN) == NOTCH_UUID_DASHED
    assert format_uuid(NOTCH_UUID_DASHED) == NOTCH_UUID_DASHED


def test_profile_undashed() -> None:
    p = MojangProfile(uuid=NOTCH_UUID_DASHED, name=NOTCH_NAME)
    assert p.uuid_undashed == NOTCH_UUID_PLAIN


@pytest.mark.asyncio
async def test_resolve_uuid_success() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.host == "api.mojang.com"
        assert req.url.path == f"/users/profiles/minecraft/{NOTCH_NAME}"
        return httpx.Response(200, json={"id": NOTCH_UUID_PLAIN, "name": NOTCH_NAME})

    async with _make_client(httpx.MockTransport(handler)) as mojang:
        assert await mojang.resolve_uuid(NOTCH_NAME) == NOTCH_UUID_DASHED


@pytest.mark.asyncio
async def test_resolve_uuid_not_found_returns_none() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"path": "/users/profiles/minecraft/ghost"})

    async with _make_client(httpx.MockTransport(handler)) as mojang:
        assert await mojang.resolve_uuid("ghost") is None


@pytest.mark.asyncio
async def test_resolve_uuid_invalid_name_raises_locally() -> None:
    # No transport call expected — input validation happens client-side.
    calls: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        return httpx.Response(200, json={})

    async with _make_client(httpx.MockTransport(handler)) as mojang:
        with pytest.raises(ValueError):
            await mojang.resolve_uuid("not a valid name!")
        with pytest.raises(ValueError):
            await mojang.resolve_uuid("waaaaaaaaaaaaaaaaaaaaay-too-long")
    assert calls == []


@pytest.mark.asyncio
async def test_resolve_username_success() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.host == "sessionserver.mojang.com"
        assert req.url.path == f"/session/minecraft/profile/{NOTCH_UUID_PLAIN}"
        return httpx.Response(
            200,
            json={"id": NOTCH_UUID_PLAIN, "name": NOTCH_NAME, "properties": []},
        )

    async with _make_client(httpx.MockTransport(handler)) as mojang:
        assert await mojang.resolve_username(NOTCH_UUID_DASHED) == NOTCH_NAME
        assert await mojang.resolve_username(NOTCH_UUID_PLAIN) == NOTCH_NAME


@pytest.mark.asyncio
async def test_resolve_username_no_content_returns_none() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(204)

    async with _make_client(httpx.MockTransport(handler)) as mojang:
        assert await mojang.resolve_username(NOTCH_UUID_DASHED) is None


@pytest.mark.asyncio
async def test_resolve_username_invalid_uuid_raises_locally() -> None:
    async with _make_client(httpx.MockTransport(lambda _r: httpx.Response(200))) as mojang:
        with pytest.raises(ValueError):
            await mojang.resolve_username("definitely-not-a-uuid")


@pytest.mark.asyncio
async def test_http_error_surfaces() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    async with _make_client(httpx.MockTransport(handler)) as mojang:
        with pytest.raises(MojangHTTPError) as excinfo:
            await mojang.resolve_uuid(NOTCH_NAME)
    assert excinfo.value.status_code == 500
    assert "boom" in excinfo.value.body


@pytest.mark.asyncio
async def test_lookup_by_name_returns_full_profile() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": NOTCH_UUID_PLAIN, "name": "NOTCH"})

    async with _make_client(httpx.MockTransport(handler)) as mojang:
        profile = await mojang.lookup_by_name(NOTCH_NAME)
    assert profile == MojangProfile(uuid=NOTCH_UUID_DASHED, name="NOTCH")


@pytest.mark.asyncio
async def test_default_urls_are_mojang() -> None:
    # Defensive: make sure we haven't accidentally pointed at staging.
    assert DEFAULT_PROFILE_URL == "https://api.mojang.com"
    assert DEFAULT_SESSION_URL == "https://sessionserver.mojang.com"


@pytest.mark.asyncio
async def test_user_agent_is_sent() -> None:
    seen_ua: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen_ua.append(req.headers["user-agent"])
        return httpx.Response(200, json={"id": NOTCH_UUID_PLAIN, "name": NOTCH_NAME})

    raw = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"User-Agent": "liquidchat/test"},
    )
    async with MojangClient(client=raw) as mojang:
        await mojang.resolve_uuid(NOTCH_NAME)
    await raw.aclose()
    assert seen_ua == ["liquidchat/test"]


@pytest.mark.asyncio
async def test_module_level_resolve_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    # Force the module-level convenience funcs to use a mocked transport.
    def handler(req: httpx.Request) -> httpx.Response:
        if "/users/profiles/minecraft/" in req.url.path:
            return httpx.Response(200, json={"id": NOTCH_UUID_PLAIN, "name": NOTCH_NAME})
        if "/session/minecraft/profile/" in req.url.path:
            return httpx.Response(200, json={"id": NOTCH_UUID_PLAIN, "name": NOTCH_NAME})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def fake_async_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr("liquidchat.mojang.httpx.AsyncClient", fake_async_client)

    assert await resolve_uuid(NOTCH_NAME) == NOTCH_UUID_DASHED
    assert await resolve_username(NOTCH_UUID_DASHED) == NOTCH_NAME


def test_repr_smoke() -> None:
    p = MojangProfile(uuid=NOTCH_UUID_DASHED, name=NOTCH_NAME)
    assert NOTCH_NAME in repr(p)
    # JSON round-trip via dataclass-style dict
    payload = {"uuid": p.uuid, "name": p.name}
    assert json.loads(json.dumps(payload)) == payload


@pytest.mark.asyncio
async def test_lookup_by_name_malformed_200_raises_http_error() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"name": "Notch"})  # missing 'id'

    async with _make_client(httpx.MockTransport(handler)) as mojang:
        with pytest.raises(MojangHTTPError) as excinfo:
            await mojang.lookup_by_name("Notch")
        assert "malformed" in str(excinfo.value).lower()


@pytest.mark.asyncio
async def test_lookup_by_uuid_malformed_200_raises_http_error() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "not-a-uuid"})  # missing 'name', bad id

    async with _make_client(httpx.MockTransport(handler)) as mojang:
        with pytest.raises(MojangHTTPError) as excinfo:
            await mojang.lookup_by_uuid("069a79f4-44e9-4726-a5be-fca90e38aaf5")
        assert "malformed" in str(excinfo.value).lower()


@pytest.mark.asyncio
async def test_lookup_by_name_array_response_raises_http_error() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"id": "x", "name": "y"}])

    async with _make_client(httpx.MockTransport(handler)) as mojang:
        with pytest.raises(MojangHTTPError):
            await mojang.lookup_by_name("Notch")
