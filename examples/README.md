# liquidchat examples

Runnable usage snippets, grouped by theme. Each file is independently
runnable — fill in your JWT in ``main()`` and execute with:

```bash
uv run python examples/basic.py
```

| File | Contents |
|---|---|
| [`basic.py`](./basic.py) | One-shot `Client`: send a message, validate a JWT, chained actions on a single websocket. |
| [`moderation.py`](./moderation.py) | Batch ban with progress reporting; long-running automod that bans on keyword match. |
| [`bot.py`](./bot.py) | Long-running `PersistentClient` chat bot, custom reconnect policy, cache-backed user lookup. |
| [`mojang.py`](./mojang.py) | `liquidchat.mojang` helpers for users not in the chat cache, including a cache-then-Mojang fallback pattern. |

## Ban / unban return-value contract

`Client.ban_user`, `PersistentClient.ban_user`, and their `unban`
counterparts return `bool`:

* `True` — server replied `Success {reason: "Ban"}` (or `"Unban"`).
* `False` — any of:
  * server replied `Error` (`NotPermitted`, `NotBanned`, etc.);
  * response did not arrive within the timeout (5s for `Client`, 10s for `PersistentClient`);
  * the websocket dropped before a response came in;
  * cancellation propagated through (only in `ban_users_batch`, which
    marks unreached UUIDs as `False`).

The clients **never** raise on a "ban missed its reply" — they log the
timeout/disconnect and return `False`. Callers that need certainty
should retry, or use `PersistentClient` (the same connection stays open
across retries so transient failures are cheap to recover from).

A late response that arrives *after* the timeout window is dropped:

* **One-shot `Client`**: the websocket is already closed, so the response
  was never read.
* **`PersistentClient`**: the `_pending_action` slot was cleared on
  timeout, so the late `Success`/`Error` is ignored — but a late
  `Error` will still surface to the registered `on_error` handler.

## Getting a JWT

You need an axochat JWT to use these examples. The realistic ways to
obtain one today:

1. Inside LiquidBounce, run `.chatjwt` — it performs the full Mojang
   handshake against your real session and prints a JWT in chat.
2. For local development, the `axochat generate` CLI against the
   server's HS256 key produces test tokens (see `tests/conftest.py`).
3. A future `liquidchat[msa]` extra (see `docs/msa_login_plan.md`)
   will let you authenticate headlessly with a Microsoft account.
