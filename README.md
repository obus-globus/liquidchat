# liquidchat

A modern, typed Python client for the **LiquidChat** websocket protocol used by
`chat.liquidbounce.net`. Ported and modernized from the original
`olotldiscordbot/liquidchat/` package.

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

## PersistentClient

```python
import asyncio
from liquidchat import Handlers, PersistentClient

async def on_message(author, content):
    print(f"<{author.name}> {content}")

async def main() -> None:
    client = PersistentClient(
        token="<jwt>",
        handlers=Handlers(on_message=on_message),
    )
    await client.start()
    await client.wait_until_logged_in(timeout=5.0)

    await client.send_chat("hi everyone")

    # Moderation works on the same connection (if the JWT has perms)
    await client.ban_user("<uuid>")

    try:
        await asyncio.sleep(3600)
    finally:
        await client.stop()

asyncio.run(main())
```

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
