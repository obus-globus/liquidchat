# liquidchat

[![CI](https://github.com/clawdbot-silly-waddle/liquidchat/actions/workflows/ci.yml/badge.svg)](https://github.com/clawdbot-silly-waddle/liquidchat/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](./LICENSE)
[![Typed](https://img.shields.io/badge/typed-PEP%20561-brightgreen.svg)](https://peps.python.org/pep-0561/)

> ⚠️ **Project status: alpha.** API surface may change without warning until 1.0.

A modern, typed Python client for the **LiquidChat** websocket protocol used by
`chat.liquidbounce.net`. Ported and modernized from the original
`olotldiscordbot/liquidchat/` package.

## Installation

```bash
# From source (private repo for now)
git clone git@github.com:clawdbot-silly-waddle/liquidchat.git
cd liquidchat
uv sync          # or: pip install -e '.[dev]'
```

Requires **Python 3.13+**.

## Two clients

- [`Client`](#client) — one-shot. Opens a fresh websocket, performs an operation
  (validate a token, send a chat message, ban / unban / batch-ban a user), and
  closes. Ideal for cron jobs, validation endpoints, and one-off moderation.
- [`PersistentClient`](#persistentclient) — long-lived. Auto-reconnects, dispatches
  inbound events to `Handlers` callbacks, and exposes the full action set
  (chat sends, ban / unban) on the live connection. Use this for bots and
  sustained moderation.

Both clients require a JWT for `chat.liquidbounce.net` (obtained via
[axolotl-client.net](https://axolotl-client.net/)). Moderation actions
require the JWT user to be listed in the server's moderators file.

## Client

```python
import asyncio
from liquidchat import Client

async def main() -> None:
    client = Client(token="<jwt>")

    # Validation
    if not await client.validate():
        return

    # Chat
    await client.send_message("hello, chat!")

    # Moderation (requires moderator perms server-side)
    ok = await client.ban_user("<uuid>")
    results = await client.ban_users_batch(
        ["<uuid>", "..."], progress=lambda d, t, r: print(f"{d}/{t}")
    )

asyncio.run(main())
```

`validate()` returns `False` on bad credentials *or* an unreachable server.
Use `validate_strict()` if you need network errors to propagate instead.

### Chaining actions on one connection

`Client.session()` opens a single websocket and yields a `Session` you
can run multiple actions on, avoiding the cost of reconnecting and
re-logging-in between operations:

```python
async with client.session() as s:
    await s.send_message("about to clean up...")
    await s.ban_user("<uuid>")
    await s.unban_user("<other-uuid>")
    await s.send_private_message("victim", "you've been warned")
```

Pass `accept_private_messages=False` to the session if you don't expect
private messages in response.

## PersistentClient

```python
import asyncio
from liquidchat import Handlers, PersistentClient

async def on_message(author, content):
    print(f"<{author.name}> {content}")

async def main() -> None:
    async with PersistentClient(
        token="<jwt>",
        handlers=Handlers(on_message=on_message),
    ) as client:
        await client.send_chat("hi everyone")
        # Moderation works on the same connection (if the JWT has perms)
        await client.ban_user("<uuid>")
        await asyncio.sleep(3600)

asyncio.run(main())
```

`async with` starts the client, waits until it's logged in, and tears
it down on exit. Use `start()` / `stop()` explicitly if you need
finer-grained control.

`Handlers` accepts `on_message`, `on_private_message`, `on_user_count`,
`on_error`, plus lifecycle hooks (`on_connect`, `on_login_success`,
`on_disconnect`, `on_reconnect`).

Reconnection is governed by `ReconnectPolicy(base_delay, max_delay,
max_attempts)`; pass a custom policy via the `reconnect=` constructor
argument.

## Differences from the original

- Strict typing (mypy `strict = true`).
- Dataclass-based, slotted, frozen models.
- Tagged-union parsing via `parse_message()` returning `LiquidChatMessage`.
- No silent `ssl.CERT_NONE` — verified TLS by default; opt-in `insecure_ssl=True`.
- Singletons removed; instantiate clients explicitly.
- Reconnection extracted into a `ReconnectPolicy` dataclass.
- Two clients (one-shot + persistent) instead of the original five-class hierarchy.

## Development

```bash
uv sync
uv run pytest
uv run ruff check .
uv run mypy src/liquidchat
```

## Username / UUID lookup

`PersistentClient.get_username(uuid)` and `get_uuid(name)` consult a
**local cache** populated from inbound chat traffic — no Mojang API
call is made. They return `None` until the user has been observed in
chat.

For lookups beyond the cache (or in CLI/one-shot scripts), use the
`liquidchat.mojang` helpers, which call Mojang's public profile API
via `httpx`:

```python
from liquidchat.mojang import MojangClient, resolve_uuid, resolve_username

# One-shot (creates and tears down an httpx.AsyncClient):
uuid = await resolve_uuid("Notch")           # "069a79f4-44e9-4726-a5be-fca90e38aaf5"
name = await resolve_username(uuid)          # "Notch"

# Batched (reuse one client):
async with MojangClient() as mojang:
    for name in names:
        print(name, await mojang.resolve_uuid(name))
```

Returns `None` on a clean "not found" (HTTP 404 / 204). Other HTTP
failures raise `MojangHTTPError`; network errors propagate as
`httpx.RequestError`.

## Token validation

Two flavours, depending on what you need:

**Server-side validation** (`Client.validate` / `Client.validate_strict`)
opens a websocket and performs the real `LoginJWT` handshake. The
server checks signature, expiry, and claim structure — that's *real*
validation. `validate` returns `False` on either rejected creds or
server-unreachable; `validate_strict` distinguishes the two.

**Offline validation** (`liquidchat.jwt`) parses the JWT locally — no
network round-trip, but it cannot verify the signature (we don't have
axochat's signing key). Use this as a cheap preflight check, e.g. to
refresh proactively before opening the socket:

```python
from liquidchat.jwt import inspect_token, is_token_expired, InvalidTokenError

try:
    info = inspect_token(jwt)
    print(info.name, info.uuid, info.expires_at)
except InvalidTokenError as e:
    print("malformed token:", e)

if is_token_expired(jwt, leeway=30.0):
    jwt = await refresh_token()
```

Offline checks: well-formedness (3 base64url segments), header `alg`
present and not `none`, payload decodes to a JSON object containing
`exp` (numeric) and `user.{name, uuid}` (non-empty strings), and the
configurable `exp` clock check.

## More examples

See [`examples.py`](./examples.py) for runnable snippets covering every
workflow: one-shot send / validate / batch ban, chained sessions, chat
bots, automod, custom reconnect policies, and the username/UUID cache.
It also documents what happens when a ban gets no response from the
server.
