"""Smoke tests for the wire protocol parser."""

from __future__ import annotations

import json

import pytest

from liquidchat import (
    AuthorInfo,
    Error,
    LiquidChatMessage,
    MessageContent,
    NewJWT,
    ProtocolError,
    Success,
    UserCount,
    decode,
    encode,
    parse_message,
)


def test_encode_envelope() -> None:
    assert json.loads(encode("Ping")) == {"m": "Ping"}
    assert json.loads(encode("Message", {"content": "hi"})) == {
        "m": "Message",
        "c": {"content": "hi"},
    }


def test_decode_round_trip_message() -> None:
    raw = json.dumps(
        {
            "m": "Message",
            "c": {"author_info": {"name": "alice", "uuid": "xx"}, "content": "hello"},
        }
    )
    msg = decode(raw)
    assert isinstance(msg, LiquidChatMessage)
    assert msg.m == "Message"
    assert isinstance(msg.c, MessageContent)
    assert msg.c.author_info == AuthorInfo(name="alice", uuid="xx")
    assert msg.c.content == "hello"


def test_decode_user_count() -> None:
    msg = parse_message({"m": "UserCount", "c": {"connections": 12, "logged_in": 7}})
    assert isinstance(msg.c, UserCount)
    assert msg.c.connections == 12 and msg.c.logged_in == 7


def test_decode_success_and_error() -> None:
    succ = parse_message({"m": "Success", "c": {"reason": "Login"}})
    assert isinstance(succ.c, Success) and succ.c.reason == "Login"
    err = parse_message({"m": "Error", "c": {"message": "bad token"}})
    assert isinstance(err.c, Error) and err.c.message == "bad token"


def test_decode_new_jwt() -> None:
    msg = parse_message({"m": "NewJWT", "c": {"token": "abc"}})
    assert isinstance(msg.c, NewJWT) and msg.c.token == "abc"


def test_request_types_allow_missing_body() -> None:
    msg = parse_message({"m": "RequestUserCount"})
    assert msg.c is None


def test_unknown_type_raises() -> None:
    with pytest.raises(ProtocolError):
        parse_message({"m": "WhoKnows", "c": {}})


def test_missing_body_raises() -> None:
    with pytest.raises(ProtocolError):
        parse_message({"m": "Message"})


def test_malformed_payload_raises() -> None:
    with pytest.raises(ProtocolError):
        parse_message({"m": "UserCount", "c": {"connections": 1}})  # missing logged_in


def test_non_dict_envelope_raises() -> None:
    # malicious / buggy server sends a JSON primitive instead of an object
    for bad in (123, None, True, "string", [1, 2, 3]):
        with pytest.raises(ProtocolError):
            parse_message(bad)  # type: ignore[arg-type]


def test_unhashable_m_raises() -> None:
    # 'm' value isn't a string — must not crash with TypeError
    with pytest.raises(ProtocolError):
        parse_message({"m": ["Message"], "c": {}})
    with pytest.raises(ProtocolError):
        parse_message({"m": {"nested": True}, "c": {}})
    with pytest.raises(ProtocolError):
        parse_message({"m": 42, "c": {}})


def test_decode_non_dict_json_raises() -> None:
    # decode() must reject JSON that parses to a primitive
    with pytest.raises(ProtocolError):
        decode("123")
    with pytest.raises(ProtocolError):
        decode("null")
    with pytest.raises(ProtocolError):
        decode('"hello"')


def test_decode_invalid_json_raises() -> None:
    with pytest.raises(ProtocolError):
        decode("not json at all{")
