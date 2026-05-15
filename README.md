# liquidchat

A modern, typed Python client for the **LiquidChat** websocket protocol used by
`chat.liquidbounce.net`. Ported and modernized from the original
`olotldiscordbot/liquidchat/` package.

## Quick start

### Send one message
```python
import asyncio
from liquidchat import MinimalClient

async def main() -> None:
    client = MinimalClient()
    client.set_jwt_token("<jwt>")
    await client.send_message("hello, chat!")

asyncio.run(main())
```

### Validate a JWT
```python
import asyncio
from liquidchat import JWTValidationClient

async def main() -> None:
    ok = await JWTValidationClient().validate("<jwt>")
    print("token valid:", ok)

asyncio.run(main())
```

### Moderation (one-shot)
```python
from liquidchat import ModeratorClient
mod = ModeratorClient()
mod.set_jwt_token("<jwt>")
await mod.ban_user("<uuid>")
results = await mod.ban_users_batch(["<uuid>", "..."], progress=my_callback)
```

### Long-running chat consumer
```python
from liquidchat import Handlers, PersistentClient

async def on_message(author, content):
    print(f"<{author.name}> {content}")

client = PersistentClient(handlers=Handlers(on_message=on_message))
client.set_jwt_token("<jwt>")
await client.start()
await client.send_chat("hi everyone")
await client.stop()
```

### Long-running moderator
```python
from liquidchat import PersistentModeratorClient
mod = PersistentModeratorClient()
mod.set_jwt_token("<jwt>")
await mod.start()
await mod.ban_user("<uuid>")
```

## Differences from the original

- Strict typing (mypy `strict = true`).
- Dataclass-based, slotted, frozen models.
- Tagged-union parsing via `parse_message()` returning `LiquidChatMessage`.
- No silent `ssl.CERT_NONE` — verified TLS by default; opt-in `insecure_ssl=True`.
- Singletons removed; instantiate clients explicitly.
- Reconnection extracted into a `ReconnectPolicy` dataclass.

## Development
```bash
uv sync
uv run pytest
uv run ruff check .
```
