# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.8.8] - 2026-05-17

### Changed

- Bump ``mcapi-auth`` floor to 0.7.4. ``BROWSER_UNSUPPORTED_CLIENT_IDS``
  now also covers every v1 / Live-Connect client_id in the catalog,
  so ``--force-flow --flow browser --client-id <v1>`` (e.g. a
  ``bedrock-*`` ID) now prints a useful warning instead of letting the
  user wait for the v2 endpoint to reject the request as
  ``AADSTS70001 (client_not_found)``.

## [0.8.7] - 2026-05-17

### Fixed

- ``liquidchat login --client-id prism --flow browser`` previously
  failed at the Microsoft authorize step with
  ``invalid_request: ... redirect_uri ... not valid``. Prism's
  Azure-AD app registers the loopback URI with the *root* path
  (``http://127.0.0.1:*/``), not ``/callback``. mcapi-auth 0.7.3 now
  carries that override in ``KNOWN_CLIENT_REDIRECTS`` and liquidchat
  picks it up automatically.

### Added

- Warn when ``--flow browser`` is used with a client_id known to have
  no loopback reply URL registered (currently ``edu`` and
  ``office365``). Suggests ``--flow device-code`` or ``--force-flow``.

### Changed

- Bump ``mcapi-auth`` floor to 0.7.3 (for the prism redirect override
  and ``is_browser_unsupported`` helper).

## [0.8.6] - 2026-05-17

### Added

- ``liquidchat login --force-flow`` (default ``False``). When set,
  disables the auto-dispatch from ``--flow`` + ``--client-id`` so the
  requested flow is invoked literally regardless of client_id format.
  Useful for testing non-standard combinations like ``--flow browser``
  with a v1 client_id (which talks to the v2 OAuth endpoint with a v1
  ID — most likely fails, exposing Microsoft's real error). The
  fallback notes now mention ``--force-flow`` so users know the escape
  hatch exists.

## [0.8.5] - 2026-05-17

### Added

- ``liquidchat login --bind-port PORT`` flag. By default the local
  listener uses port ``0`` (OS picks a free ephemeral port);
  ``--bind-port`` lets you pin a specific one if your Azure app
  registration requires an exact port. Ignored for ``--flow
  device-code`` and v1 client_ids.

## [0.8.4] - 2026-05-17

### Added

- ``liquidchat login --bind-host HOST`` and ``--redirect-path PATH``
  CLI overrides for the ``--flow browser`` local listener. Use these
  to match a non-standard reply URI registered on your own Azure-AD
  app. When unset, the per-client default from
  ``mcapi_auth.KNOWN_CLIENT_REDIRECTS`` is used; otherwise it falls
  back to ``127.0.0.1`` / ``/callback``.
- Both flags are ignored (with a note) for ``--flow device-code`` and
  for v1 client_ids — neither uses a local listener.

## [0.8.3] - 2026-05-17

### Fixed

- ``--client-id liquidlauncher`` / ``--client-id liquidbounce`` with
  ``--flow browser`` now uses the redirect URI registered on their Azure
  app (``http://localhost:*/login``) instead of our default
  ``http://127.0.0.1:*/callback`` — previously Microsoft rejected the
  authorize request with ``invalid_request: ... redirect_uri ... not valid``.
  Powered by ``mcapi_auth.resolve_browser_redirect()`` (new in 0.7.2).

## [0.8.2] - 2026-05-17

### Added

- ``liquidchat login --client-id liquidlauncher`` (alias also:
  ``liquidbounce``) — authenticates against the Azure-AD app shared by
  LiquidLauncher and the in-game LiquidBounce client
  (``0add8caf-2cc6-4546-b798-c3d171217dd9``). v2 GUID with
  ``XboxLive.signin offline_access``. Requires mcapi-auth ≥ 0.7.1.

## [0.8.1] - 2026-05-17

### Changed

- ``liquidchat login`` now prints the *effective* MSA flow in its banner
  rather than the requested one — picking a v1 client_id with the default
  ``--flow device-code`` no longer prints a confusing "falling back to
  browser-v1" note (the banner already shows ``flow=browser-v1``). The
  override note is now only printed when ``--flow`` is incompatible with
  the chosen v1 client_id in a way the user couldn't have predicted.

## [0.8.0] - 2026-05-17

### Added

- ``liquidchat login --client-id NAME_OR_ID`` selects which Microsoft
  OAuth client_id to authenticate as. Accepts an alias (resolved via
  ``mcapi_auth.KNOWN_CLIENT_IDS``) or a literal client_id. Default is
  ``"prism"``. Available aliases:
  * v2 (Azure-AD GUID): ``prism`` (default), ``edu``, ``office365``.
  * v1 (Live-Connect compressed, MBI_SSL): ``java``, ``bedrock-win32``,
    ``bedrock-android``, ``bedrock-ios``, ``bedrock-nintendo``,
    ``bedrock-playstation``, ``xbox-app-ios``, ``xbox-gamepass-ios``.
- v1/v2 dispatch is now automatic based on the resolved client_id
  format. Passing a v1 client_id forces the OOB browser-paste-back
  flow regardless of ``--flow``; v2 client_ids honour ``--flow``
  (``device-code`` or ``browser``).

### Changed

- ``--flow browser-v1`` is now a back-compat alias that resolves to
  ``--flow browser`` when paired with a v1 client_id (which is the
  common case). Pass an explicit ``--client-id java`` to keep the
  legacy ``00000000402b5328`` flow you used to get with
  ``--flow browser-v1``.
- Bumped ``mcapi-auth`` pin to ``>=0.7.0`` (introduces
  ``KNOWN_CLIENT_IDS`` + the bedrock/edu/xbox-app client_id constants).

## [0.7.2] - 2026-05-17

### Fixed

- ``liquidchat login --flow browser-v1`` now successfully completes
  the XBL ``/authenticate`` step. Bumps ``mcapi-auth`` to ``>=0.6.2``
  which sends the MBI_SSL access token to XBL without the ``d=``
  prefix (MBI_SSL tokens are pre-formed RPS tickets).

## [0.7.1] - 2026-05-17

### Changed

- Bumped ``mcapi-auth`` pin to ``>=0.6.1``. The ``browser-v1`` flow
  now uses an out-of-band paste-back redirect
  (``oauth20_desktop.srf``) instead of a localhost listener — the
  legacy ``00000000402b5328`` client_id only accepts the OOB
  redirect. After signing in, paste the resulting URL (or just the
  ``code=`` value) into the terminal when prompted.

## [0.7.0] - 2026-05-17

### Added

- ``liquidchat login --flow {device-code,browser,browser-v1}`` lets
  you pick which Microsoft Account flow to use:
  * ``device-code`` (default, unchanged) — terminal-friendly device
    code against the v2 endpoints with the Prism Launcher client_id.
  * ``browser`` — opens the user's browser to the same v2 endpoints
    with a localhost-redirect listener (PKCE).
  * ``browser-v1`` — opens the browser to the legacy Live-Connect
    ``login.live.com/oauth20_*.srf`` endpoints with the compressed
    Minecraft Launcher client_id (``00000000402b5328``) and the
    ``MBI_SSL`` scope. Useful when the v2 endpoints reject your
    account or tenant.

### Changed

- Bumped ``mcapi-auth`` pin to ``>=0.6.0`` (adds
  ``login_via_browser``, ``login_via_browser_v1``).

## [0.6.1] - 2026-05-17

### Added

- ``liquidchat chat --anonymous`` opens a read-only connection
  without logging in: incoming public chat is streamed to the
  terminal, but typing a message (or running ``/ban``, ``/unban``,
  ``/pm``, ``/count``, ``/refresh-jwt``) is blocked with a friendly
  notice. No JWT / profile is required. Useful for spectating.
- ``PersistentClient(anonymous=True)`` skips the ``LoginJWT`` step
  entirely; ``token=`` becomes optional in that mode.

## [0.6.0] - 2026-05-17

### Changed (breaking)

- Credentials are now organized by **profile**. Each Minecraft account
  gets its own directory under ``$LIQUIDCHAT_HOME/profiles/<name>/``
  (default ``~/.config/liquidchat/profiles/<name>/``) containing
  ``jwt`` and ``refresh_token.json``. A ``$LIQUIDCHAT_HOME/default``
  pointer file tracks the active profile.
- Old single-file locations (``$LIQUIDCHAT_TOKEN_FILE``,
  ``~/.config/liquidchat/token``, and the mcapi-auth XDG state path)
  are no longer used or read. There is no automatic migration —
  re-run ``liquidchat login`` to populate the new layout.

### Added

- Global ``--account NAME`` flag on every subcommand that consumes
  the JWT (``chat``, ``send``, ``ban``, ``unban``, ``token info /
  validate / refresh / path / clear``). Profile resolution order:
  flag → ``$LIQUIDCHAT_ACCOUNT`` env → ``default`` pointer file.
- ``liquidchat account list / use NAME / remove NAME`` to manage
  profiles. ``list`` shows JWT and refresh-token presence per
  profile and marks the default.
- ``liquidchat login --account NAME`` chooses the profile name
  up front. When omitted, the Minecraft username returned by the
  MSA flow is used as the profile name; the refresh token is staged
  to a temp file and moved into place once the name is known.
- First profile created in a fresh home dir auto-promotes to
  default; pass ``--set-default`` / ``--no-set-default`` to override.
- ``liquidchat token refresh`` now writes the new JWT back to the
  profile by default (``--no-save`` to print to stdout instead).

## [0.5.1] - 2026-05-17

### Added

- ``liquidchat login`` now prints the on-disk location of the MSA
  refresh token whenever ``--remember`` (the default) is in effect,
  alongside the existing JWT-saved line.

## [0.5.0] - 2026-05-17

### Changed (breaking)

- Bumped ``mcapi-auth`` pin to ``v0.5.0``, which no longer persists
  the MSA refresh token by default. ``liquidchat login`` now opts in
  explicitly via the new ``--remember`` flag (defaults to ``True``
  for parity with previous behavior; pass ``--no-remember`` for an
  ephemeral one-shot login that leaves no on-disk artifact from
  mcapi-auth). Library consumers using ``mcapi_auth.login`` directly
  must now pass ``storage=FileTokenStorage()`` to keep the old
  behavior.

## [0.4.7] - 2026-05-17

### Added

- ``liquidchat token path`` — prints the on-disk locations of the
  liquidchat JWT (``$LIQUIDCHAT_TOKEN_FILE`` or
  ``~/.config/liquidchat/token``) and the mcapi-auth MSA refresh-
  token store (``$XDG_STATE_HOME/mcapi_auth/refresh_token.json``,
  default ``~/.local/state/mcapi_auth/refresh_token.json``), with
  an exists/missing marker for each.
- ``liquidchat token clear`` — removes both credential files (with a
  confirmation prompt). ``--jwt-only`` keeps the MSA refresh token so
  the next ``liquidchat login`` skips the browser step; ``--refresh-
  only`` keeps the JWT; ``--yes`` skips the prompt.

## [0.4.6] - 2026-05-17

### Changed

- Application-level heartbeat in ``PersistentClient`` now defaults to
  **off**. Set ``heartbeat_interval=60.0`` (or pass
  ``liquidchat chat --heartbeat 60``) to opt in.

## [0.4.5] - 2026-05-17

### Added

- ``PersistentClient`` now supports an opt-in application-level
  heartbeat (``RequestMojangInfo``). Useful for long-running sessions
  behind stateful NATs / firewalls that idle-drop TCP conntrack.
  Disabled by default. Enable via the new ``heartbeat_interval``
  constructor argument (seconds, e.g. ``60.0``); ``None`` or ``0``
  leaves it off. ``liquidchat chat`` exposes it as
  ``--heartbeat <seconds>``.

## [0.4.4] - 2026-05-17

### Changed

- `--insecure` now defaults to **on** for every CLI subcommand that
  opens a chat websocket. The public ``chat.liquidbounce.net``
  deployment has been serving an expired TLS cert for ~6 years, so
  this matches reality. Pass ``--no-insecure`` to opt back into cert
  verification (useful when pointing the CLI at a private deployment
  with a valid cert).
- ``PersistentClient`` no longer pings the server every 30s. The
  axochat server doesn't emit pong frames, so the client-side ping
  timeout would tear the connection down after the first idle window.
  Disabled `ping_interval`/`ping_timeout` entirely; TCP keepalive is
  left to the kernel. Reconnect logic still handles real drops.

## [0.4.3] - 2026-05-17

### Fixed

- `liquidchat login` no longer holds an idle websocket open during
  the Microsoft device-code wait. Previously the chat server would
  kill the connection with a keepalive ping-timeout while the user
  was still completing browser auth, so the subsequent `LoginMojang`
  frame went out on a corpse connection. Now we run the full MSA →
  Mojang auth chain first, then open the websocket and run the
  `RequestMojangInfo` → `joinServer` → `LoginMojang` → `RequestJWT`
  exchange back-to-back.

## [0.4.2] - 2026-05-17

### Changed

- Bumped ``mcapi-auth`` pin to ``v0.4.2`` so ``liquidchat login``
  uses the Prism Launcher MSA client_id. The historical Minecraft
  launcher client_id was decommissioned by Microsoft and the device
  code endpoint now rejects it with ``AADSTS700016``.

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
