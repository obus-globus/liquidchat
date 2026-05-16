"""Property-based tests for the wire-protocol and JWT parsers.

These tests use Hypothesis to generate arbitrary inputs and assert
that the parsers either return a well-typed result or raise the
documented protocol-level exception — never a raw ``TypeError`` /
``KeyError`` / ``UnicodeDecodeError`` etc.

The bug-review pass found two such leaks (non-dict envelope and
unhashable ``m`` in ``parse_message``); these tests would have caught
both.
"""

from __future__ import annotations

import base64
import json

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from liquidchat import LiquidChatMessage, ProtocolError, parse_message
from liquidchat.jwt import InvalidTokenError, decode_unverified_payload, inspect_token

# ---------------------------------------------------------------------------
# parse_message: never crashes outside the documented exception type
# ---------------------------------------------------------------------------

_json_scalars = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(),
    st.floats(allow_nan=False, allow_infinity=False),
    st.text(),
)

_json_values = st.recursive(
    _json_scalars,
    lambda children: st.one_of(
        st.lists(children, max_size=4),
        st.dictionaries(st.text(max_size=8), children, max_size=4),
    ),
    max_leaves=20,
)


@given(_json_values)
@settings(suppress_health_check=[HealthCheck.too_slow], max_examples=400)
def test_parse_message_never_raises_unexpected(payload: object) -> None:
    """For any JSON-ish input, ``parse_message`` either succeeds or raises ``ProtocolError``."""
    try:
        result = parse_message(payload)  # type: ignore[arg-type]
    except ProtocolError:
        return
    assert isinstance(result, LiquidChatMessage)


@given(st.text(max_size=32), _json_values)
@settings(max_examples=300)
def test_parse_message_with_unknown_type(m: str, c: object) -> None:
    """Random ``{"m": ..., "c": ...}`` envelopes either parse or raise ``ProtocolError``."""
    try:
        result = parse_message({"m": m, "c": c})
    except ProtocolError:
        return
    assert isinstance(result, LiquidChatMessage)


# ---------------------------------------------------------------------------
# JWT parser: structurally arbitrary tokens never crash uncleanly
# ---------------------------------------------------------------------------


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


@given(st.text(max_size=256))
@settings(max_examples=300)
def test_decode_unverified_payload_never_raises_unexpected(token: str) -> None:
    """For any string, ``decode_unverified_payload`` raises ``InvalidTokenError`` cleanly or succeeds."""
    try:
        header, payload = decode_unverified_payload(token)
    except InvalidTokenError:
        return
    # If parsing succeeded, both must be dicts (documented invariant).
    assert isinstance(header, dict)
    assert isinstance(payload, dict)


@given(st.binary(max_size=64), st.binary(max_size=64), st.binary(max_size=64))
@settings(max_examples=200)
def test_decode_unverified_payload_arbitrary_bytes(h: bytes, p: bytes, s: bytes) -> None:
    """Three random base64url segments must not produce a non-``InvalidTokenError``."""
    token = f"{_b64url(h)}.{_b64url(p)}.{_b64url(s)}"
    try:
        header, payload = decode_unverified_payload(token)
    except InvalidTokenError:
        return
    assert isinstance(header, dict)
    assert isinstance(payload, dict)


@given(st.dictionaries(st.text(max_size=16), _json_values, max_size=8))
@settings(max_examples=200)
def test_inspect_token_never_raises_unexpected(payload: dict[str, object]) -> None:
    """``inspect_token`` either returns a ``TokenInfo`` or raises ``InvalidTokenError``."""
    header_b = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload_b = _b64url(json.dumps(payload).encode())
    sig_b = _b64url(b"signature-doesnt-matter")
    token = f"{header_b}.{payload_b}.{sig_b}"
    with pytest.raises(InvalidTokenError):
        # Almost no random dict matches the strict shape liquidchat expects
        # (exp numeric, user.{name, uuid} non-empty strings). The few that do
        # would also succeed — but the contract is just "never anything but
        # InvalidTokenError or success".
        info = inspect_token(token)
        # If we *did* succeed, make sure the basic invariants hold.
        assert info.name
        assert info.uuid
        # Suppress the "expected to raise" branch when the rare valid case slips through.
        raise InvalidTokenError("test fixture: valid token accidentally generated")
