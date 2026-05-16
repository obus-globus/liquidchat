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
    mod.PROGRESS_UPDATE_FREQUENCY = 2

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

    mod = PersistentClient(url=axochat_server.url, accept_private_messages=False)
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

    mod = PersistentClient(url=axochat_server.url, accept_private_messages=False)
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
    mod = PersistentClient(url=axochat_server.url, accept_private_messages=False)
    # Token set but not started.
    mod.set_jwt_token("does-not-matter")
    assert mod.connected is False
    assert await mod.ban_user("11111111-1111-1111-1111-111111111111") is False


async def test_persistent_moderator_start_without_token_raises(
    axochat_server: AxochatServer,
) -> None:
    mod = PersistentClient(url=axochat_server.url, accept_private_messages=False)
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
        accept_private_messages=True,
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


async def test_session_chains_send_then_ban(axochat_server: AxochatServer, jwt_mod: str) -> None:
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
        accept_private_messages=True,
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


# ---------- duplicate user (same JWT / same UUID) ----------


async def test_two_connections_same_jwt_both_receive_broadcasts(
    axochat_server: AxochatServer, jwt_user_a: str
) -> None:
    """axochat allows the same user to log in twice over JWT; both
    connections should receive broadcasts and be able to send.

    Confirmed against `axochat_server/src/chat/handler/jwt.rs`: JWT
    login does NOT reject duplicate sessions (unlike the legacy
    MojangInfo flow, which returns AlreadyLoggedIn). Both connections
    end up in `UserSession.connections: HashSet<InternalId>` and the
    rate limiter is shared per-user.
    """
    recv_a: list[tuple[str, str]] = []
    recv_b: list[tuple[str, str]] = []
    got_on_a = asyncio.Event()
    got_on_b = asyncio.Event()

    async def on_msg_a(author: AuthorInfo, content: str) -> None:
        recv_a.append((author.name, content))
        if content == "from-conn-b":
            got_on_a.set()

    async def on_msg_b(author: AuthorInfo, content: str) -> None:
        recv_b.append((author.name, content))
        if content == "from-conn-a":
            got_on_b.set()

    conn_a = PersistentClient(
        url=axochat_server.url,
        token=jwt_user_a,
        handlers=Handlers(on_message=on_msg_a),
    )
    conn_b = PersistentClient(
        url=axochat_server.url,
        token=jwt_user_a,
        handlers=Handlers(on_message=on_msg_b),
    )

    async with conn_a, conn_b:
        # Both must be fully logged in before sending; the autouse
        # context-manager fixture awaits wait_until_logged_in on __aenter__.
        assert conn_a.connected and conn_b.connected

        await conn_a.send_chat("from-conn-a")
        await conn_b.send_chat("from-conn-b")

        await asyncio.wait_for(got_on_a.wait(), timeout=3.0)
        await asyncio.wait_for(got_on_b.wait(), timeout=3.0)

    # Each connection received BOTH messages (server broadcasts to all
    # sessions, including the sender's own connections). Author name is
    # the same on both connections because they're the same user.
    contents_a = sorted(c for _, c in recv_a)
    contents_b = sorted(c for _, c in recv_b)
    assert contents_a == ["from-conn-a", "from-conn-b"], contents_a
    assert contents_b == ["from-conn-a", "from-conn-b"], contents_b
    assert {name for name, _ in recv_a} == {"user_a"}
    assert {name for name, _ in recv_b} == {"user_a"}


async def test_two_connections_same_jwt_private_message_goes_to_one(
    axochat_server: AxochatServer, jwt_user_a: str, jwt_user_b: str
) -> None:
    """**Documented axochat quirk:** a PrivateMessage addressed to a user
    with multiple sessions is delivered to **only one** of them.

    See ``axochat_server/src/chat/handler/message.rs`` lines 80-83: after
    the first successful ``do_send`` the function ``return``\\ s, so the
    iteration over ``receiver_user.connections`` stops. Which session
    wins is HashSet-order, i.e. effectively non-deterministic.

    This test pins the behaviour so that if axochat ever fixes the loop
    (so PMs fan out to every session) we notice and update our docs.
    """
    seen: list[str] = []
    delivered = asyncio.Event()

    async def on_pm_a1(_author: AuthorInfo, content: str) -> None:
        seen.append(f"a1:{content}")
        delivered.set()

    async def on_pm_a2(_author: AuthorInfo, content: str) -> None:
        seen.append(f"a2:{content}")
        delivered.set()

    a1 = PersistentClient(
        url=axochat_server.url,
        token=jwt_user_a,
        accept_private_messages=True,
        handlers=Handlers(on_private_message=on_pm_a1),
    )
    a2 = PersistentClient(
        url=axochat_server.url,
        token=jwt_user_a,
        accept_private_messages=True,
        handlers=Handlers(on_private_message=on_pm_a2),
    )
    b = PersistentClient(url=axochat_server.url, token=jwt_user_b, handlers=Handlers())

    async with a1, a2, b:
        await b.send("PrivateMessage", {"receiver": "user_a", "content": "secret"})
        await asyncio.wait_for(delivered.wait(), timeout=3.0)
        # Give the server time to (NOT) fan out — if it ever did, the
        # second handler would also fire.
        await asyncio.sleep(0.5)

    assert len(seen) == 1, f"expected exactly one delivery, got {seen}"
    assert seen[0].endswith(":secret")


async def test_two_connections_same_jwt_no_crosstalk_on_close(
    axochat_server: AxochatServer, jwt_user_a: str
) -> None:
    """Closing one of two same-user sessions must not break the other.

    Regression guard: confirms ``UserSession.connections`` only removes
    the closing connection's id, leaving the surviving session able to
    send and receive.
    """
    received: list[str] = []
    got = asyncio.Event()

    async def on_msg(_author: AuthorInfo, content: str) -> None:
        if content == "after-close":
            received.append(content)
            got.set()

    survivor = PersistentClient(
        url=axochat_server.url,
        token=jwt_user_a,
        handlers=Handlers(on_message=on_msg),
    )
    doomed = PersistentClient(url=axochat_server.url, token=jwt_user_a)

    async with survivor:
        await doomed.__aenter__()
        await doomed.__aexit__(None, None, None)
        # Survivor should still work post-close of its sibling.
        await survivor.send_chat("after-close")
        await asyncio.wait_for(got.wait(), timeout=3.0)

    assert received == ["after-close"]


# ---------- auth-failure handling ----------


async def test_persistent_client_invalid_token_fails_fast(
    axochat_server: AxochatServer,
) -> None:
    """A rejected JWT should surface as ``LoginFailedError`` quickly
    and stop the reconnect loop, not retry forever.
    """
    from liquidchat import LoginFailedError

    client = PersistentClient(
        url=axochat_server.url,
        token="not-a-real-jwt",
        handlers=Handlers(),
        reconnect=ReconnectPolicy(base_delay=0.1, max_delay=0.5, max_attempts=50),
    )
    await client.start()
    try:
        with pytest.raises(LoginFailedError):
            await client.wait_until_logged_in(timeout=5.0)
        assert client._login_failed
        assert not client._enabled
    finally:
        await client.stop()
    assert not client.connected


# ---------- RequestJWT (token rotation) ----------


async def test_persistent_client_request_new_jwt(
    axochat_server: AxochatServer, jwt_user_a: str
) -> None:
    """``request_new_jwt()`` should return a fresh, valid JWT for the
    same user that we logged in with."""
    from liquidchat.jwt import inspect_token

    async with PersistentClient(url=axochat_server.url, token=jwt_user_a) as client:
        new_token = await client.request_new_jwt(timeout=5.0)

    assert isinstance(new_token, str) and new_token
    # Token may be identical to the input (same user, same exp-second);
    # what matters is that it parses and is server-issued.

    info = inspect_token(new_token)
    assert info.name == "user_a"
    assert info.uuid == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    assert info.algorithm == "HS256"
    assert not info.is_expired()


async def test_request_new_jwt_round_trips_through_login(
    axochat_server: AxochatServer, jwt_user_a: str
) -> None:
    """The rotated token must itself be accepted by the server."""
    async with PersistentClient(url=axochat_server.url, token=jwt_user_a) as client:
        fresh = await client.request_new_jwt(timeout=5.0)

    # Open a brand-new connection with the freshly minted token.
    async with PersistentClient(url=axochat_server.url, token=fresh) as client2:
        assert client2.connected


async def test_request_new_jwt_serialised_with_ban(
    axochat_server: AxochatServer, jwt_mod: str
) -> None:
    """RequestJWT and ban_user must not deadlock or interleave (share
    the same action lock)."""
    from tests.conftest import TARGET_UUID

    async with PersistentClient(url=axochat_server.url, token=jwt_mod) as mod:
        # Fire both at the same time; the lock should serialise them.
        tok_task = asyncio.create_task(mod.request_new_jwt(timeout=5.0))
        ban_task = asyncio.create_task(mod.ban_user(TARGET_UUID))
        new_tok, banned = await asyncio.gather(tok_task, ban_task)
    assert isinstance(new_tok, str) and new_tok
    assert banned is True


async def test_request_new_jwt_not_connected() -> None:
    """Calling on a non-started client must raise, not hang."""
    client = PersistentClient(url="ws://127.0.0.1:1/ws", token="x")
    with pytest.raises(RuntimeError, match="not connected"):
        await client.request_new_jwt(timeout=1.0)
