"""Integration tests against a real axochat_server instance."""

from __future__ import annotations

import asyncio
import subprocess

import pytest

from liquidchat import (
    AuthorInfo,
    Client,
    Handlers,
    MissingTokenError,
    PersistentClient,
    ReconnectPolicy,
)
from tests.conftest import AxochatServer

pytestmark = pytest.mark.asyncio


# ---------- Client ----------


async def test_validate_returns_true_for_valid_token(
    axochat_server: AxochatServer, jwt_user_a: str
) -> None:
    ok = await Client(url=axochat_server.url).validate(jwt_user_a)
    assert ok is True


async def test_validate_returns_false_for_garbage_token(axochat_server: AxochatServer) -> None:
    ok = await Client(url=axochat_server.url).validate("not-a-jwt")
    assert ok is False


async def test_validate_returns_false_for_empty_token(axochat_server: AxochatServer) -> None:
    ok = await Client(url=axochat_server.url).validate("")
    assert ok is False


async def test_validate_returns_false_when_server_unreachable() -> None:
    ok = await Client(url="ws://127.0.0.1:1/ws").validate("x")
    assert ok is False


# ---------- Client ----------


async def test_minimal_send_message_round_trip(
    axochat_server: AxochatServer, jwt_user_a: str, jwt_user_b: str
) -> None:
    """Send via Client, observe broadcast via PersistentClient as a second user."""
    received: list[tuple[str, str]] = []
    got = asyncio.Event()

    async def on_message(author: AuthorInfo, content: str) -> None:
        received.append((author.name, content))
        got.set()

    listener = PersistentClient(
        url=axochat_server.url,
        handlers=Handlers(on_message=on_message),
        reconnect=ReconnectPolicy(base_delay=0.1, max_delay=0.5),
    )
    listener.set_jwt_token(jwt_user_b)
    await listener.start()
    try:
        await listener.wait_until_logged_in(timeout=5.0)

        client = Client(url=axochat_server.url)
        client.set_jwt_token(jwt_user_a)
        await client.send_message("hello there")

        await asyncio.wait_for(got.wait(), timeout=3.0)
    finally:
        await listener.stop()

    assert ("user_a", "hello there") in received


async def test_minimal_send_message_missing_token_raises(axochat_server: AxochatServer) -> None:
    client = Client(url=axochat_server.url)
    with pytest.raises(MissingTokenError):
        await client.send_message("nope")


# ---------- Client (one-shot) ----------


async def test_moderator_ban_without_perms_returns_false(
    axochat_server: AxochatServer, jwt_user_a: str
) -> None:
    mod = Client(url=axochat_server.url)
    mod.set_jwt_token(jwt_user_a)
    from tests.conftest import TARGET_UUID

    assert await mod.ban_user(TARGET_UUID) is False


async def test_moderator_ban_with_perms_succeeds(
    axochat_server: AxochatServer, jwt_mod: str
) -> None:
    mod = Client(url=axochat_server.url)
    mod.set_jwt_token(jwt_mod)
    from tests.conftest import TARGET_UUID

    # Pre-clean: server persists bans across calls in this session.
    await mod.unban_user(TARGET_UUID)
    assert await mod.ban_user(TARGET_UUID) is True
    assert await mod.unban_user(TARGET_UUID) is True
    # Unbanning twice -> NotBanned error from server -> False.
    assert await mod.unban_user(TARGET_UUID) is False


async def test_moderator_batch_ban_progress_callback(
    axochat_server: AxochatServer, jwt_mod: str
) -> None:
    mod = Client(url=axochat_server.url)
    mod.set_jwt_token(jwt_mod)
    mod.PROGRESS_UPDATE_FREQUENCY = 2  # type: ignore[misc]

    uuids = [f"33333333-3333-3333-3333-{i:012d}" for i in range(5)]
    # Clean slate
    for u in uuids:
        await mod.unban_user(u)

    progress_calls: list[tuple[int, int]] = []

    async def on_progress(done: int, total: int, _results: dict[str, bool]) -> None:
        progress_calls.append((done, total))

    results = await mod.ban_users_batch(uuids, progress=on_progress)
    assert set(results) == set(uuids)
    assert all(results.values())
    # Progress should have fired at least at completion
    assert progress_calls
    assert progress_calls[-1] == (5, 5)

    # Cleanup
    for u in uuids:
        await mod.unban_user(u)


async def test_moderator_missing_token_raises(axochat_server: AxochatServer) -> None:
    mod = Client(url=axochat_server.url)
    with pytest.raises(MissingTokenError):
        await mod.ban_user("11111111-1111-1111-1111-111111111111")
    with pytest.raises(MissingTokenError):
        await mod.ban_users_batch(["11111111-1111-1111-1111-111111111111"])


# ---------- PersistentClient ----------


async def test_persistent_client_lifecycle_callbacks(
    axochat_server: AxochatServer, jwt_user_a: str
) -> None:
    states: list[str] = []

    async def push(name: str) -> None:
        states.append(name)

    client = PersistentClient(
        url=axochat_server.url,
        handlers=Handlers(
            on_connect=lambda: push("connect"),
            on_login_success=lambda: push("login"),
            on_disconnect=lambda: push("disconnect"),
        ),
    )
    client.set_jwt_token(jwt_user_a)
    await client.start()
    await client.wait_until_logged_in(timeout=5.0)
    assert client.connected
    await client.stop()
    assert not client.connected
    assert states[0] == "connect"
    assert states[1] == "login"
    assert "disconnect" in states


async def test_persistent_client_start_without_token_raises(axochat_server: AxochatServer) -> None:
    client = PersistentClient(url=axochat_server.url)
    with pytest.raises(MissingTokenError):
        await client.start()


async def test_persistent_client_send_and_receive_own_message(
    axochat_server: AxochatServer, jwt_user_a: str
) -> None:
    received: list[tuple[AuthorInfo, str]] = []
    got = asyncio.Event()

    async def on_message(author: AuthorInfo, content: str) -> None:
        received.append((author, content))
        got.set()

    client = PersistentClient(
        url=axochat_server.url,
        handlers=Handlers(on_message=on_message),
    )
    client.set_jwt_token(jwt_user_a)
    await client.start()
    try:
        await client.wait_until_logged_in(timeout=5.0)
        await client.send_chat("echo me")
        await asyncio.wait_for(got.wait(), timeout=3.0)
    finally:
        await client.stop()

    assert any(content == "echo me" for _, content in received)
    author = received[0][0]
    assert author.name == "user_a"


async def test_persistent_client_username_lookup(
    axochat_server: AxochatServer, jwt_user_a: str, jwt_user_b: str
) -> None:
    sender_seen = asyncio.Event()
    captured: list[AuthorInfo] = []

    async def on_message(author: AuthorInfo, content: str) -> None:
        captured.append(author)
        sender_seen.set()

    listener = PersistentClient(
        url=axochat_server.url,
        handlers=Handlers(on_message=on_message),
    )
    listener.set_jwt_token(jwt_user_b)
    await listener.start()
    try:
        await listener.wait_until_logged_in(timeout=5.0)

        sender = Client(url=axochat_server.url)
        sender.set_jwt_token(jwt_user_a)
        await sender.send_message("hi from a")

        await asyncio.wait_for(sender_seen.wait(), timeout=3.0)

        author = captured[0]
        assert listener.get_username(author.uuid) == author.name
        assert listener.get_uuid(author.name.upper()) == author.uuid
        assert listener.get_username("no-such-uuid") is None
        assert listener.get_uuid("ghost") is None
    finally:
        await listener.stop()


async def test_persistent_client_request_user_count(
    axochat_server: AxochatServer, jwt_mod: str
) -> None:
    counts: list[tuple[int, int]] = []
    got = asyncio.Event()

    async def on_user_count(connections: int, logged_in: int) -> None:
        counts.append((connections, logged_in))
        got.set()

    client = PersistentClient(
        url=axochat_server.url,
        handlers=Handlers(on_user_count=on_user_count),
    )
    client.set_jwt_token(jwt_mod)
    await client.start()
    try:
        await client.wait_until_logged_in(timeout=5.0)
        await client.request_user_count()
        await asyncio.wait_for(got.wait(), timeout=3.0)
    finally:
        await client.stop()

    assert counts
    connections, logged_in = counts[0]
    assert connections >= 1
    assert logged_in >= 1


async def test_persistent_client_stop_is_idempotent(
    axochat_server: AxochatServer, jwt_user_a: str
) -> None:
    client = PersistentClient(url=axochat_server.url)
    client.set_jwt_token(jwt_user_a)
    await client.start()
    await client.wait_until_logged_in(timeout=5.0)
    await client.stop()
    # Calling stop again must not raise.
    await client.stop()
    assert not client.connected


async def test_persistent_client_buffers_sends_before_connect(
    axochat_server: AxochatServer, jwt_user_a: str
) -> None:
    """Sends queued before ``start()`` should be flushed once connected, not raise."""
    received = asyncio.Event()
    seen: list[str] = []

    async def on_message(_author: AuthorInfo, content: str) -> None:
        seen.append(content)
        received.set()

    client = PersistentClient(
        url=axochat_server.url,
        handlers=Handlers(on_message=on_message),
    )
    client.set_jwt_token(jwt_user_a)
    # Queue *before* the loop is running.
    await client.send_chat("buffered before start")
    await client.start()
    try:
        await asyncio.wait_for(received.wait(), timeout=5.0)
    finally:
        await client.stop()
    assert "buffered before start" in seen


# ---------- PersistentClient ----------


async def test_persistent_moderator_ban_unban(axochat_server: AxochatServer, jwt_mod: str) -> None:
    from tests.conftest import TARGET_UUID

    mod = PersistentClient(url=axochat_server.url, allow_messages=False)
    mod.set_jwt_token(jwt_mod)
    await mod.start()
    try:
        await mod.wait_until_logged_in(timeout=5.0)
        assert mod.connected
        # ensure clean state
        await mod.unban_user(TARGET_UUID)
        assert await mod.ban_user(TARGET_UUID) is True
        assert await mod.unban_user(TARGET_UUID) is True
        # Already unbanned -> NotBanned -> False.
        assert await mod.unban_user(TARGET_UUID) is False
    finally:
        await mod.stop()


async def test_persistent_moderator_rejects_when_no_perm(
    axochat_server: AxochatServer, jwt_user_a: str
) -> None:
    from tests.conftest import TARGET_UUID

    mod = PersistentClient(url=axochat_server.url, allow_messages=False)
    mod.set_jwt_token(jwt_user_a)
    await mod.start()
    try:
        await mod.wait_until_logged_in(timeout=5.0)
        assert mod.connected
        assert await mod.ban_user(TARGET_UUID) is False
    finally:
        await mod.stop()


async def test_persistent_moderator_drops_when_disconnected(axochat_server: AxochatServer) -> None:
    """If never started, an action immediately returns False."""
    mod = PersistentClient(url=axochat_server.url, allow_messages=False)
    # Token set but not started.
    mod.set_jwt_token("does-not-matter")
    assert mod.connected is False
    assert await mod.ban_user("11111111-1111-1111-1111-111111111111") is False


async def test_persistent_moderator_start_without_token_raises(
    axochat_server: AxochatServer,
) -> None:
    mod = PersistentClient(url=axochat_server.url, allow_messages=False)
    with pytest.raises(MissingTokenError):
        await mod.start()


# ---------- Reconnect behaviour ----------


async def test_persistent_client_reconnects_after_server_restart(
    axochat_server: AxochatServer, jwt_user_a: str
) -> None:
    """Kill the server and bring it back; the persistent client should re-establish."""
    connected_events: list[str] = []
    login_event = asyncio.Event()
    reconnect_event = asyncio.Event()

    async def on_login() -> None:
        connected_events.append("login")
        login_event.set()

    async def on_reconnect() -> None:
        connected_events.append("reconnect")
        reconnect_event.set()

    client = PersistentClient(
        url=axochat_server.url,
        handlers=Handlers(on_login_success=on_login, on_reconnect=on_reconnect),
        reconnect=ReconnectPolicy(base_delay=0.2, max_delay=1.0),
    )
    client.set_jwt_token(jwt_user_a)
    await client.start()
    try:
        await asyncio.wait_for(login_event.wait(), timeout=3.0)
        login_event.clear()

        # Drop the server.
        axochat_server.proc.terminate()
        try:
            axochat_server.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            axochat_server.proc.kill()
            axochat_server.proc.wait()
        # Wait until our client notices.
        await asyncio.wait_for(reconnect_event.wait(), timeout=5.0)

        # Restart server on same port.
        axochat_server.restart()

        await asyncio.wait_for(login_event.wait(), timeout=10.0)
        assert "reconnect" in connected_events
    finally:
        await client.stop()


# ---------- validate_strict ----------


async def test_validate_strict_returns_false_for_invalid_token(
    axochat_server: AxochatServer,
) -> None:
    """Bad credentials → False, no exception."""
    ok = await Client(url=axochat_server.url).validate_strict("garbage")
    assert ok is False


async def test_validate_strict_returns_true_for_valid_token(
    axochat_server: AxochatServer, jwt_user_a: str
) -> None:
    ok = await Client(url=axochat_server.url).validate_strict(jwt_user_a)
    assert ok is True


async def test_validate_strict_raises_when_unreachable() -> None:
    """Network errors propagate from validate_strict."""
    import websockets.exceptions

    with pytest.raises((OSError, websockets.exceptions.WebSocketException)):
        await Client(url="ws://127.0.0.1:1/ws").validate_strict("x")


# ---------- cancellation safety ----------


async def test_persistent_client_task_cancel_cleans_up(
    axochat_server: AxochatServer, jwt_user_a: str
) -> None:
    """Cancelling the run task externally should not leak resources."""
    client = PersistentClient(url=axochat_server.url)
    client.set_jwt_token(jwt_user_a)
    task = await client.start()
    await client.wait_until_logged_in(timeout=5.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not client.connected


async def test_minimal_client_send_cancel_safe(
    axochat_server: AxochatServer, jwt_user_a: str
) -> None:
    """Cancelling Client.send_message mid-flight must not leak the websocket."""
    client = Client(url=axochat_server.url)
    client.set_jwt_token(jwt_user_a)
    task = asyncio.create_task(client.send_message("racing"))
    # Yield once so the task starts the handshake, then cancel.
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ---------- PrivateMessage ----------


async def test_persistent_client_receives_private_message(
    axochat_server: AxochatServer, jwt_user_a: str, jwt_user_b: str
) -> None:
    """user_a sends a PrivateMessage to user_b; user_b's handler fires."""
    got = asyncio.Event()
    captured: list[tuple[AuthorInfo, str]] = []

    async def on_private(author: AuthorInfo, content: str) -> None:
        captured.append((author, content))
        got.set()

    receiver = PersistentClient(
        url=axochat_server.url,
        allow_messages=True,
        handlers=Handlers(on_private_message=on_private),
    )
    receiver.set_jwt_token(jwt_user_b)
    await receiver.start()

    sender = PersistentClient(url=axochat_server.url)
    sender.set_jwt_token(jwt_user_a)
    await sender.start()

    try:
        await receiver.wait_until_logged_in(timeout=5.0)
        await sender.wait_until_logged_in(timeout=5.0)
        # Server keys receiver by username.
        await sender.send("PrivateMessage", {"receiver": "user_b", "content": "psst"})
        await asyncio.wait_for(got.wait(), timeout=5.0)
    finally:
        await sender.stop()
        await receiver.stop()

    assert captured
    author, content = captured[0]
    assert author.name == "user_a"
    assert content == "psst"


# ---------- Error.message dict form ----------


async def test_error_message_dict_shape_does_not_crash() -> None:
    """The protocol parser accepts Error messages whose `message` is a dict
    (Rust enum tuple variant) without crashing."""
    from liquidchat import parse_message

    msg = parse_message({"m": "Error", "c": {"message": {"InvalidCharacter": "@"}}})
    assert msg.m == "Error"
    from liquidchat import Error

    assert isinstance(msg.c, Error)
    assert msg.c.message == {"InvalidCharacter": "@"}


# ---------- chained one-shot session ----------


async def test_session_chains_send_then_ban(
    axochat_server: AxochatServer, jwt_mod: str
) -> None:
    """A session lets us send a chat message and then ban a user on the same ws."""
    from tests.conftest import TARGET_UUID

    client = Client(url=axochat_server.url, token=jwt_mod)
    async with client.session() as s:
        await s.send_message("about to clean up")
        assert await s.ban_user(TARGET_UUID) is True
        assert await s.unban_user(TARGET_UUID) is True


async def test_session_reuses_single_connection(
    axochat_server: AxochatServer, jwt_user_a: str, jwt_user_b: str
) -> None:
    """A second user observes both messages sent from one session — proves it's one login."""
    received: list[str] = []
    got_two = asyncio.Event()

    async def on_message(_author: AuthorInfo, content: str) -> None:
        received.append(content)
        if len(received) >= 2:
            got_two.set()

    listener = PersistentClient(
        url=axochat_server.url,
        token=jwt_user_b,
        handlers=Handlers(on_message=on_message),
    )
    await listener.start()
    try:
        await listener.wait_until_logged_in(timeout=5.0)
        async with Client(url=axochat_server.url, token=jwt_user_a).session() as s:
            await s.send_message("first")
            await s.send_message("second")
        await asyncio.wait_for(got_two.wait(), timeout=5.0)
    finally:
        await listener.stop()

    assert "first" in received
    assert "second" in received


async def test_session_requires_token(axochat_server: AxochatServer) -> None:
    client = Client(url=axochat_server.url)
    with pytest.raises(MissingTokenError):
        async with client.session():
            pass


async def test_session_send_private_message(
    axochat_server: AxochatServer, jwt_user_a: str, jwt_user_b: str
) -> None:
    """Session.send_private_message reaches the recipient's PrivateMessage handler."""
    got = asyncio.Event()
    captured: list[tuple[AuthorInfo, str]] = []

    async def on_private(author: AuthorInfo, content: str) -> None:
        captured.append((author, content))
        got.set()

    receiver = PersistentClient(
        url=axochat_server.url,
        token=jwt_user_b,
        allow_messages=True,
        handlers=Handlers(on_private_message=on_private),
    )
    await receiver.start()
    try:
        await receiver.wait_until_logged_in(timeout=5.0)
        async with Client(url=axochat_server.url, token=jwt_user_a).session() as s:
            await s.send_private_message("user_b", "session-pm")
        await asyncio.wait_for(got.wait(), timeout=5.0)
    finally:
        await receiver.stop()

    assert captured[0][1] == "session-pm"


# ---------- async with PersistentClient ----------


async def test_persistent_client_context_manager(
    axochat_server: AxochatServer, jwt_user_a: str
) -> None:
    """``async with PersistentClient(...) as c`` should start + wait_until_logged_in
    on entry and stop on exit."""
    seen_disconnect = asyncio.Event()

    async def on_disconnect() -> None:
        seen_disconnect.set()

    async with PersistentClient(
        url=axochat_server.url,
        token=jwt_user_a,
        handlers=Handlers(on_disconnect=on_disconnect),
    ) as client:
        assert client.connected
        await client.send_chat("hello from ctx mgr")

    # After exit the client is stopped and on_disconnect has fired.
    assert not client.connected
    await asyncio.wait_for(seen_disconnect.wait(), timeout=2.0)
