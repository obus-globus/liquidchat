# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.4.1] - 2026-05-17

### Added

- `--insecure` flag on every CLI subcommand that opens a chat
  websocket (`login`, `chat`, `send`, `token validate`,
  `token refresh`, `ban`, `unban`). Required against the public
  `chat.liquidbounce.net` deployment, whose TLS certificate expired
  in 2020 and has never been renewed. The flag maps to
  `Client(insecure_ssl=True)` / `PersistentClient(insecure_ssl=True)`,
  which were already supported on the library side.

## [0.4.0] - 2026-05-17

### Added

- **`liquidchat login` subcommand.** Runs the full Microsoft → Mojang
  → AxoChat authentication chain end-to-end and persists the
  resulting JWT to `~/.config/liquidchat/token` (or
  `$LIQUIDCHAT_TOKEN_FILE` / `--out PATH`). Steps:
  1. `RequestMojangInfo` over the chat websocket.
  2. `mcapi_auth.login` for MSA device-code authentication (cached
     refresh token on subsequent runs).
  3. `mcapi_auth.join_server` to satisfy the Yggdrasil
     joinServer handshake.
  4. `LoginMojang` to the chat server, then `RequestJWT` →
     `NewJWT`.
- `--allow-messages / --no-allow-messages` flag controls whether the
  resulting session accepts private messages.
- `--print-token` mirrors the JWT to stdout in addition to writing
  it to disk (so `liquidchat login --print-token > token.txt`
  captures cleanly).

## [0.3.0] - 2026-05-17

### Added

- **Interactive CLI** — `liquidchat` console script (install with the
  optional `cli` extra: `pip install 'liquidchat[cli]'`). Subcommands:
  - `liquidchat chat` — interactive REPL backed by
    `PersistentClient`. `prompt_toolkit`'s `patch_stdout` keeps the
    input line anchored to the bottom while inbound chat streams
    above it. Slash-commands: `/help`, `/quit`, `/ban`, `/unban`,
    `/pm`, `/count`, `/whois`, `/refresh-jwt`.
  - `liquidchat send <message>` — one-shot chat send.
  - `liquidchat token info` / `validate` / `refresh` — JWT
    inspection (Rich table or raw JSON), round-trip validation,
    `RequestJWT` rotation.
  - `liquidchat ban` / `unban <user|uuid>` — one-shot moderation
    with Mojang resolution.
  - `liquidchat mojang uuid` / `name` — public profile lookups.
- Token resolution order for every subcommand: `--token` flag →
  `LIQUIDCHAT_TOKEN` env → `$LIQUIDCHAT_TOKEN_FILE` (default
  `~/.config/liquidchat/token`).
- New optional dependency group `cli`: `cyclopts>=3`,
  `prompt_toolkit>=3.0.50`, `rich>=13`.

## [0.2.1] - 2026-05-17

### Added

- **Restored `MojangRateLimitError.rate_limit_result`.** When Mojang
  returns a 429 with an `X-Minecraft-Rate-Limit-Result` header
  (observed value in the wild: `"OVER_LIMIT"`), the field is now
  populated again. Requires `mcapi-auth >= 0.4.1`.

### Changed

- Bumped `mcapi-auth` dependency floor to `>= 0.4.1`.

## [0.2.0] - 2026-05-17

### Changed (breaking)

- **Bumped minimum Python to 3.14.** `liquidchat` now uses
  `mcapi-auth >= 0.4` for its Mojang REST calls and that library is
  3.14-only.
- **`liquidchat.mojang` now delegates all HTTP work to `mcapi-auth`.**
  Behaviour you should expect to differ:
  - The `DEFAULT_PROFILE_URL` / `DEFAULT_SESSION_URL` module constants
    and the `profile_url=` / `session_url=` constructor arguments on
    `MojangClient` have been removed — endpoint selection lives in
    `mcapi-auth` now.
  - The `Cache-Control: max-age` header from Mojang is no longer
    honoured. The in-process cache always uses the fixed
    `DEFAULT_PROFILE_TTL` (300s) / `DEFAULT_SESSION_TTL` (20s).
    `MAX_CACHE_TTL` and the `_cache_ttl_from_response` helper are gone.
  - The `X-Minecraft-Rate-Limit-Result` header is no longer surfaced;
    `MojangHTTPError.rate_limit_result` is kept for back-compat but is
    always `None`.

### Added

- New `mcapi-auth` dependency (sourced from
  `github.com/clawdbot-silly-waddle/mcapi-auth` at tag `v0.4.0` for
  now; will track PyPI once published). The full
  Microsoft/Mojang auth chain and the wider REST surface
  (`get_profile_by_uuid`, `extract_textures`, `fetch_blocked_servers`,
  …) are available directly to callers via `import mcapi_auth`.
- Mojang lookup cache is now **bounded** (`maxsize=10_000`, LRU
  eviction) — user-controlled cache keys can no longer grow memory
  without limit.
- **Single-flight** request deduplication: N concurrent identical
  lookups (`lookup_by_name` / `lookup_by_uuid`) now share a single
  in-flight fetch. Both errors and successes are propagated to every
  waiter. Works independently of caching, so it helps on cold-start
  bursts even when `cache=False`.
- `MojangClient` now caches successful profile lookups in-process.
  Pass ``cache=False`` to disable, or call ``await client.clear_cache()``
  to reset.
- New `MojangRateLimitError` (subclass of `MojangHTTPError`) raised on
  HTTP 429. Surfaces `retry_after` (parsed from the standard
  ``Retry-After`` header by ``mcapi-auth``).
- Property-based tests via `hypothesis` (`tests/test_property.py`)
  covering `parse_message` and the JWT decoders. Asserts that arbitrary
  inputs only ever raise the documented `ProtocolError` /
  `InvalidTokenError` — would have caught the two `TypeError` paths
  fixed in the previous release.
- `respx`-based example tests for `MojangClient`
  (`tests/test_mojang_respx.py`) demonstrating the recommended idiom
  for mocking `httpx.AsyncClient` calls. The original `test_mojang.py`
  (MockTransport-based) is retained for tests that introspect raw
  requests.
- Coverage reporting wired through `pytest-cov`; `[tool.coverage.*]`
  configured in `pyproject.toml` (branch coverage, `src/liquidchat`
  scope). CI now uploads a `coverage.xml` artifact.
- CI dependency-hygiene step via `deptry` and supply-chain CVE scan
  via `pip-audit` (skips editable installs so the project itself
  doesn't error).

### Changed

- Modernised for Python 3.13/3.14: `MessageHandler` /
  `PrivateMessageHandler` / `UserCountHandler` / `ErrorHandler` /
  `LifecycleHandler` / `ProgressCallback` are PEP 695 `type` aliases.
  `Handlers` has `slots=True`. `ReconnectPolicy` and `_PendingAction`
  are `slots=True, frozen=True` — immutable as their usage intended.
- Dropped `from __future__ import annotations` everywhere (PEP 749
  makes annotations lazy by default on 3.14).
- `PersistentClient.wait_until_logged_in` rewritten on top of
  `asyncio.timeout()` and a flat task-cleanup pass, replacing the
  earlier `asyncio.wait(timeout=...)` + suppressed-await dance.
- `examples.py` reorganised into an `examples/` directory grouped by
  theme (`basic.py`, `moderation.py`, `bot.py`, `mojang.py`) with an
  index README documenting the ban/unban return-value contract.

### Fixed

- `parse_message` now raises `ProtocolError` (not `TypeError`) on
  non-dict envelopes or non-string `m` values, closing two paths where
  a malformed server payload could uncleanly crash the read loop.
- `protocol.decode` wraps invalid-JSON / non-UTF-8 input in
  `ProtocolError` instead of leaking `json.JSONDecodeError`.
- `PersistentClient._submit_action` and `request_new_jwt` now capture
  the websocket inside the action lock, eliminating a TOCTOU race
  where a concurrent reconnect could turn the documented
  `False`/`RuntimeError` return into an `AttributeError`.
- `jwt.decode_unverified_payload` catches `UnicodeDecodeError` from
  non-UTF-8 base64 segments and reports it as `InvalidTokenError`.
- `MojangClient.lookup_by_name` / `lookup_by_uuid` wrap malformed 200
  responses (missing fields, wrong shape) in `MojangHTTPError`
  instead of leaking `KeyError`/`TypeError`.

### Documentation

- README no longer references mypy (we switched to basedpyright in
  v0.1.0-pre).
- `examples/moderation.py` docstring now points at `examples/README.md`
  instead of a non-existent `MODERATION.md`.

## [0.1.0] - 2026-05-16

Initial alpha release.

### Added

- `Client` — one-shot async API for sending chat messages, private
  messages, user-count requests, and moderator ban / unban actions
  against an axochat server.
- `PersistentClient` — long-lived session with automatic reconnect,
  exponential backoff, in-memory username↔UUID cache, async handler
  registration, and a `wait_until_logged_in()` primitive.
- `PersistentClient.request_new_jwt()` — rotate the active JWT via
  the `RequestJWT`/`NewJWT` round-trip while the session is logged in.
- `liquidchat.jwt` — offline JWT inspection (`inspect_token`,
  `is_token_expired`, `seconds_until_expiry`,
  `decode_unverified_payload`). Does *not* verify the signature.
- `liquidchat.mojang` — async helpers built on `httpx` for the
  Mojang public profile API: `resolve_uuid`, `resolve_username`,
  `MojangClient`, `MojangProfile`.
- `validate` / `validate_strict` — server-side JWT validation by
  driving a real LoginJWT handshake.
- Pydantic-backed protocol models with `model_validate`-driven
  envelope parsing.
- `py.typed` marker so downstream type-checkers honor our hints.
- `docs/msa_login_plan.md` — design notes for a future Microsoft
  Account → axochat login bootstrap (not yet implemented).

### Known issues

- Integration tests require a locally built `axochat_server` binary;
  CI skips them automatically when the binary is absent.
- Multi-session JWT logins receive private messages on only one
  session (axochat-server-side behaviour; documented in tests).

[Unreleased]: https://github.com/clawdbot-silly-waddle/liquidchat/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/clawdbot-silly-waddle/liquidchat/releases/tag/v0.1.0
