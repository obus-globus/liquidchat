# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
