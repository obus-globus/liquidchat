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


def test_encode_envelope():
    assert json.loads(encode("Ping")) == {"m": "Ping"}
    assert json.loads(encode("Message", {"content": "hi"})) == {
        "m": "Message",
        "c": {"content": "hi"},
    }


def test_decode_round_trip_message():
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


def test_decode_user_count():
    msg = parse_message({"m": "UserCount", "c": {"connections": 12, "logged_in": 7}})
    assert isinstance(msg.c, UserCount)
    assert msg.c.connections == 12 and msg.c.logged_in == 7


def test_decode_success_and_error():
    succ = parse_message({"m": "Success", "c": {"reason": "Login"}})
    assert isinstance(succ.c, Success) and succ.c.reason == "Login"
    err = parse_message({"m": "Error", "c": {"message": "bad token"}})
    assert isinstance(err.c, Error) and err.c.message == "bad token"


def test_decode_new_jwt():
    msg = parse_message({"m": "NewJWT", "c": {"token": "abc"}})
    assert isinstance(msg.c, NewJWT) and msg.c.token == "abc"


def test_request_types_allow_missing_body():
    msg = parse_message({"m": "RequestUserCount"})
    assert msg.c is None


def test_unknown_type_raises():
    with pytest.raises(ProtocolError):
        parse_message({"m": "WhoKnows", "c": {}})


def test_missing_body_raises():
    with pytest.raises(ProtocolError):
        parse_message({"m": "Message"})


def test_malformed_payload_raises():
    with pytest.raises(ProtocolError):
        parse_message({"m": "UserCount", "c": {"connections": 1}})  # missing logged_in
