"""Unit tests for liquidchat.jwt (offline, no axochat or network)."""

from __future__ import annotations

import base64
import json
import time
from typing import Any

import pytest

from liquidchat.jwt import (
    InvalidTokenError,
    TokenInfo,
    decode_unverified_payload,
    inspect_token,
    is_token_expired,
    seconds_until_expiry,
)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _make_token(
    *,
    header: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    signature: bytes = b"x" * 32,
) -> str:
    h = header if header is not None else {"alg": "HS256", "typ": "JWT"}
    p = (
        payload
        if payload is not None
        else {
            "exp": int(time.time()) + 3600,
            "user": {"name": "Notch", "uuid": "069a79f4-44e9-4726-a5be-fca90e38aaf5"},
        }
    )
    return ".".join(
        [
            _b64url(json.dumps(h).encode()),
            _b64url(json.dumps(p).encode()),
            _b64url(signature),
        ]
    )


def test_inspect_valid_token() -> None:
    tok = _make_token()
    info = inspect_token(tok)
    assert isinstance(info, TokenInfo)
    assert info.name == "Notch"
    assert info.uuid == "069a79f4-44e9-4726-a5be-fca90e38aaf5"
    assert info.algorithm == "HS256"
    assert info.expires_at > time.time()
    assert info.raw_header["typ"] == "JWT"


def test_inspect_rejects_none_algorithm() -> None:
    tok = _make_token(header={"alg": "none", "typ": "JWT"})
    with pytest.raises(InvalidTokenError, match="alg="):
        inspect_token(tok)


def test_inspect_rejects_missing_alg() -> None:
    tok = _make_token(header={"typ": "JWT"})
    with pytest.raises(InvalidTokenError):
        inspect_token(tok)


def test_inspect_rejects_missing_exp() -> None:
    tok = _make_token(payload={"user": {"name": "n", "uuid": "u"}})
    with pytest.raises(InvalidTokenError, match="exp"):
        inspect_token(tok)


def test_inspect_rejects_non_numeric_exp() -> None:
    tok = _make_token(payload={"exp": "soon", "user": {"name": "n", "uuid": "u"}})
    with pytest.raises(InvalidTokenError, match="exp"):
        inspect_token(tok)


def test_inspect_rejects_missing_user() -> None:
    tok = _make_token(payload={"exp": 9999999999})
    with pytest.raises(InvalidTokenError, match="user"):
        inspect_token(tok)


def test_inspect_rejects_user_without_name() -> None:
    tok = _make_token(payload={"exp": 9999999999, "user": {"uuid": "u"}})
    with pytest.raises(InvalidTokenError, match="name"):
        inspect_token(tok)


def test_inspect_rejects_user_without_uuid() -> None:
    tok = _make_token(payload={"exp": 9999999999, "user": {"name": "n"}})
    with pytest.raises(InvalidTokenError, match="uuid"):
        inspect_token(tok)


def test_inspect_rejects_empty_name() -> None:
    tok = _make_token(payload={"exp": 9999999999, "user": {"name": "", "uuid": "u"}})
    with pytest.raises(InvalidTokenError):
        inspect_token(tok)


def test_inspect_rejects_malformed_token() -> None:
    with pytest.raises(InvalidTokenError):
        inspect_token("not.a.jwt.too.many.parts")
    with pytest.raises(InvalidTokenError):
        inspect_token("only-one-part")
    with pytest.raises(InvalidTokenError):
        inspect_token("")
    with pytest.raises(InvalidTokenError):
        inspect_token("a.b.c")  # not valid base64 JSON


def test_inspect_rejects_non_string() -> None:
    with pytest.raises(InvalidTokenError):
        inspect_token(123)  # type: ignore[arg-type]


def test_inspect_rejects_non_object_payload() -> None:
    tok = ".".join(
        [
            _b64url(json.dumps({"alg": "HS256"}).encode()),
            _b64url(json.dumps([1, 2, 3]).encode()),
            _b64url(b"sig"),
        ]
    )
    with pytest.raises(InvalidTokenError):
        inspect_token(tok)


def test_decode_unverified_payload_returns_both() -> None:
    tok = _make_token()
    header, payload = decode_unverified_payload(tok)
    assert header["alg"] == "HS256"
    assert payload["user"]["name"] == "Notch"


def test_is_token_expired_future_token() -> None:
    tok = _make_token(payload={"exp": int(time.time()) + 3600, "user": {"name": "n", "uuid": "u"}})
    assert is_token_expired(tok) is False


def test_is_token_expired_past_token() -> None:
    tok = _make_token(payload={"exp": int(time.time()) - 1, "user": {"name": "n", "uuid": "u"}})
    assert is_token_expired(tok) is True


def test_is_token_expired_with_leeway() -> None:
    # Token expires in 30s; with 60s leeway, it counts as expired now.
    tok = _make_token(payload={"exp": int(time.time()) + 30, "user": {"name": "n", "uuid": "u"}})
    assert is_token_expired(tok, leeway=60.0) is True
    assert is_token_expired(tok, leeway=10.0) is False


def test_is_token_expired_with_fixed_now() -> None:
    tok = _make_token(payload={"exp": 1000, "user": {"name": "n", "uuid": "u"}})
    assert is_token_expired(tok, now=999.0) is False
    assert is_token_expired(tok, now=1001.0) is True


def test_seconds_until_expiry_signed() -> None:
    tok = _make_token(payload={"exp": 2000, "user": {"name": "n", "uuid": "u"}})
    assert seconds_until_expiry(tok, now=1000.0) == 1000.0
    assert seconds_until_expiry(tok, now=2500.0) == -500.0


def test_token_info_methods() -> None:
    info = TokenInfo(
        name="n",
        uuid="u",
        expires_at=1000.0,
        algorithm="HS256",
        raw_header={},
        raw_payload={},
    )
    assert info.is_expired(now=999.9) is False
    assert info.is_expired(now=1000.0) is True
    assert info.seconds_until_expiry(now=900.0) == 100.0


def test_token_info_is_frozen() -> None:
    info = inspect_token(_make_token())
    with pytest.raises(AttributeError):
        info.name = "other"  # type: ignore[misc]


def test_unpadded_base64_is_accepted() -> None:
    # JWT spec says padding is stripped; we must restore it.
    payload = {"exp": 9999999999, "user": {"name": "n", "uuid": "u"}}
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode("ascii")
    # Build with no padding regardless of length.
    parts = [
        _b64url(json.dumps({"alg": "HS256"}).encode()),
        raw.rstrip("="),
        _b64url(b"sig"),
    ]
    inspect_token(".".join(parts))  # should not raise


def test_inspect_real_axochat_token(jwt_user_a: str) -> None:
    """Sanity: a real token from the test axochat server parses cleanly."""
    info = inspect_token(jwt_user_a)
    assert info.name == "user_a"
    assert info.uuid == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    assert info.algorithm == "HS256"
    assert not info.is_expired()
    assert info.seconds_until_expiry() > 24 * 3600  # 30d validity in conftest
