# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `MojangClient` now caches successful profile lookups in-process,
  honouring the upstream ``Cache-Control: max-age=N`` header (Mojang
  returns 300s for name→UUID and 20s for UUID→profile). Falls back to
  those defaults when the header is missing; respects ``no-store`` /
  ``no-cache`` by skipping the cache entirely. Pass ``cache=False`` to
  disable, or call ``await client.clear_cache()`` to reset.
- New `MojangRateLimitError` (subclass of `MojangHTTPError`) raised on
  HTTP 429. Surfaces `retry_after` (parsed from the standard
  ``Retry-After`` header — Mojang doesn't currently send one, but the
  hook is there) and `rate_limit_result` (from Mojang's bespoke
  ``X-Minecraft-Rate-Limit-Result`` header).
- `MojangHTTPError` gained a `rate_limit_result` attribute populated
  for all error responses, useful for distinguishing "throttled by
  Mojang" from "Mojang had a 500".
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

- Modernised for Python 3.13: `MessageHandler` / `PrivateMessageHandler`
  / `UserCountHandler` / `ErrorHandler` / `LifecycleHandler` /
  `ProgressCallback` are now PEP 695 `type` aliases. `Handlers` gained
  `slots=True`. `ReconnectPolicy` and `_PendingAction` are now
  `slots=True, frozen=True` — immutable as their usage intended.
- `PersistentClient.wait_until_logged_in` rewritten on top of
  `asyncio.timeout()` and a flat task-cleanup pass, replacing the
  earlier `asyncio.wait(timeout=...)` + suppressed-await dance.
- `MojangClient` now builds URLs via `httpx.URL.copy_with(path=...)`
  instead of f-string concatenation + manual `urllib.parse.quote` +
  `rstrip("/")` workaround. `profile_url` / `session_url` constructor
  args accept `str | httpx.URL`. Percent-encoding and trailing-slash
  handling are now httpx's problem.

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

### Changed

- `examples.py` reorganised into an `examples/` directory grouped by
  theme (`basic.py`, `moderation.py`, `bot.py`, `mojang.py`) with an
  index README documenting the ban/unban return-value contract.

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
