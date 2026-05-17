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

Requires **Python 3.14+**.

For the interactive CLI (`liquidchat chat`, `liquidchat token info`,
…), install with the `cli` extra:

```bash
pip install 'liquidchat[cli]'         # adds cyclopts + prompt_toolkit + rich
# or, in a uv project:
uv add 'liquidchat[cli]'
```

The console script `liquidchat` is registered automatically. See
[`## CLI`](#cli) below.

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

- Strict typing (basedpyright `strict` mode on src/).
- Pydantic v2 frozen models for the wire format.
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
uv run basedpyright
```

## Username / UUID lookup

`PersistentClient.get_username(uuid)` and `get_uuid(name)` consult a
**local cache** populated from inbound chat traffic — no Mojang API
call is made. They return `None` until the user has been observed in
chat.

For lookups beyond the cache (or in CLI/one-shot scripts), use the
`liquidchat.mojang` helpers, which call Mojang's public profile API
via [`mcapi-auth`](https://github.com/clawdbot-silly-waddle/mcapi-auth):

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
`httpx.RequestError`. For the full Mojang/Microsoft surface (auth chain,
textures, blocked-servers, piston-meta, skin/cape management, …) reach
into `mcapi-auth` directly — it's a runtime dependency.

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

## CLI

The optional `liquidchat` console script (install with the `cli` extra)
gives you a chat REPL plus the same operations the library exposes,
straight from your shell. It uses **Cyclopts** for the command tree,
**prompt_toolkit** for the bottom-anchored chat prompt, and **Rich**
for pretty token output.

```text
$ liquidchat --help
Usage: liquidchat COMMAND

Commands:
  ban       Ban a player by UUID or username.
  chat      Open an interactive LiquidChat session.
  mojang    Public Mojang profile lookups.
  send      Send a single chat message and exit.
  token     JWT inspection, validation, and rotation.
  unban     Unban a player by UUID or username.
```

### Token resolution

Every subcommand accepts `--token <jwt>`. If you don't pass one
explicitly the CLI looks in this order:

1. `LIQUIDCHAT_TOKEN` env var
2. The file at `$LIQUIDCHAT_TOKEN_FILE` (default
   `~/.config/liquidchat/token`)

> **Heads up:** the official `chat.liquidbounce.net` deployment has
> been serving an expired TLS certificate since 2020. Every subcommand
> that opens the chat websocket (`login`, `chat`, `send`,
> `token validate`, `token refresh`, `ban`, `unban`) accepts
> `--insecure` to skip cert verification. Use it against the public
> server.

So a typical setup is:

```bash
mkdir -p ~/.config/liquidchat
echo "eyJhbGc..." > ~/.config/liquidchat/token
chmod 600 ~/.config/liquidchat/token
liquidchat token info   # uses the file automatically
```

### Logging in (no token? start here)

```bash
liquidchat login
```

Runs the full Microsoft → Mojang → AxoChat auth chain end-to-end:

1. Opens the websocket and asks for a `MojangInfo` challenge.
2. Walks you through MSA device-code authentication via `mcapi-auth`
   (first run only — the refresh token is cached at the standard
   XDG state path, so subsequent logins are silent).
3. Calls `sessionserver.mojang.com/session/minecraft/join` to prove
   account ownership.
4. Sends `LoginMojang` to the chat server, then `RequestJWT`, then
   writes the resulting JWT to `~/.config/liquidchat/token` (or
   `$LIQUIDCHAT_TOKEN_FILE` / `--out PATH`).

After that, every other subcommand picks the token up automatically.
`liquidchat token refresh` rotates it on the same connection without
re-running the MSA flow.

### Interactive chat

```bash
liquidchat chat
```

Opens a `PersistentClient`, prints inbound chat (with timestamps +
colour) to the scrollback, and reads from a bottom-anchored prompt
that survives reconnects and pretty-printed lifecycle events. Slash
commands:

| Command | Effect |
| --- | --- |
| `/help` | Show the in-session command list. |
| `/quit`, `/exit`, `Ctrl-D` | Close the connection and exit. |
| `/ban <user\|uuid>` | Ban — usernames are resolved via Mojang. |
| `/unban <user\|uuid>` | Unban — same resolution. |
| `/pm <user> <text>` | Send a private message (server-side support varies). |
| `/count` | Request a user-count broadcast. |
| `/whois <user>` | Look up a username in the local UUID cache. |
| `/refresh-jwt` | Send `RequestJWT` and print the new token. |

Anything that doesn't start with `/` is sent as a public chat message.

### One-shot subcommands

```bash
liquidchat send "deploy went out, watching graphs"
liquidchat token info             # pretty table: name / uuid / exp / status
liquidchat token info --raw       # raw header + payload JSON
liquidchat token validate         # round-trip with the server
liquidchat token refresh > ~/.config/liquidchat/token   # rotate
liquidchat ban CheaterMcCheatface
liquidchat unban 069a79f444e94726a5befca90e38aaf5
liquidchat mojang uuid Notch      # 069a79f4-44e9-4726-a5be-fca90e38aaf5
liquidchat mojang name 069a79f4-44e9-4726-a5be-fca90e38aaf5
```

## More examples

See the [`examples/`](./examples/) directory for runnable snippets
grouped by theme: [`basic.py`](./examples/basic.py) (one-shot
send/validate), [`moderation.py`](./examples/moderation.py) (batch
ban + automod), [`bot.py`](./examples/bot.py) (chat bots, custom
reconnect, user lookup), and [`mojang.py`](./examples/mojang.py)
(Mojang API fallback). The [`examples/README.md`](./examples/README.md)
also documents the ban/unban return-value contract in detail.
