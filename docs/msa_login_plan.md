# Microsoft Account login for axochat — design notes

Status: **plan only**, no code yet.

This document plans the full Microsoft-account → Minecraft → axochat
authentication flow, so that a `liquidchat` user can bootstrap a JWT
from scratch (without running the LiquidBounce client and using
`.chatjwt`). Once implemented, the package would let you do something
like:

```python
from liquidchat.msa import login_with_msa

session = await login_with_msa()  # opens browser device-code flow
jwt = await session.fetch_axochat_jwt()
# session.jwt is now a long-lived axochat token
```

## Why this is non-trivial

The "JWT" that axochat hands out via `RequestJWT` requires the holder
to first prove they own a paid Minecraft account. That proof is
produced by a 5-stage chain Mojang inherited from Microsoft, plus one
final axochat-specific step. Every stage has its own request/response
shape, its own error modes, and a few of them rotate their endpoints
or response keys every couple of years.

End-to-end shape of the flow:

```
┌─────────────────────────────────────────────────────────────────┐
│ 1. MSA device-code   → user signs in in a browser                │
│ 2. MSA OAuth token   → access_token + refresh_token              │
│ 3. Xbox Live (XBL)   → "XBL" token + userhash                    │
│ 4. Xbox STS (XSTS)   → "XSTS" token (rejects users <18 without   │
│                         family-pack, banned, unowned accounts)   │
│ 5. Mojang services   → minecraftservices.com/auth/login_with_xbox│
│                         → minecraft access_token + UUID          │
│ 6. axochat           → RequestMojangInfo → joinServer            │
│                         → LoginMojang → RequestJWT → JWT         │
└─────────────────────────────────────────────────────────────────┘
```

## Stage-by-stage

### Stage 1: Microsoft device-code flow

We can't use the more common Authorization-Code flow because that
needs an HTTP redirect listener (browser-side callback). Device-code
works headless: we display a short code + URL, user logs in on any
device, we poll.

```
POST https://login.microsoftonline.com/consumers/oauth2/v2.0/devicecode
  client_id=00000000-402b-4cd3-a82b-c45ab2f1d3f7  ← Minecraft Launcher's
                                                    public client id
  scope=XboxLive.signin offline_access
→ { user_code, device_code, verification_uri, interval, expires_in }
```

Show `user_code` + `verification_uri` to the user. Then poll:

```
POST https://login.microsoftonline.com/consumers/oauth2/v2.0/token
  client_id=...
  device_code=...
  grant_type=urn:ietf:params:oauth:grant-type:device_code
→ 400 authorization_pending  (keep polling)
→ 200 { access_token, refresh_token, expires_in }
```

**Library options:** `msal` (Microsoft's official lib) or roll our own
with httpx. MSAL adds ~7MB of deps and is overkill — the device-code
flow is ~50 lines hand-rolled. I'd avoid the dep.

### Stage 2: Refresh-token rotation

Save `refresh_token` to disk (XDG state dir, or a user-supplied path).
Next invocation, hit the same /token endpoint with
`grant_type=refresh_token` to skip stages 1-2 entirely. Refresh tokens
last ~90 days of inactivity.

### Stage 3: Xbox Live authentication

```
POST https://user.auth.xboxlive.com/user/authenticate
  Content-Type: application/json
  {
    "Properties": {
      "AuthMethod": "RPS",
      "SiteName": "user.auth.xboxlive.com",
      "RpsTicket": "d=<msa-access-token>"
    },
    "RelyingParty": "http://auth.xboxlive.com",
    "TokenType": "JWT"
  }
→ {
    "Token": "<xbl-token>",
    "DisplayClaims": { "xui": [ { "uhs": "<userhash>" } ] }
  }
```

### Stage 4: Xbox STS

```
POST https://xsts.auth.xboxlive.com/xsts/authorize
  {
    "Properties": {
      "SandboxId": "RETAIL",
      "UserTokens": [ "<xbl-token>" ]
    },
    "RelyingParty": "rp://api.minecraftservices.com/",
    "TokenType": "JWT"
  }
→ 200 { Token: "<xsts-token>", DisplayClaims: ... }
→ 401 { XErr: 2148916233 }   (no Xbox account)
→ 401 { XErr: 2148916235 }   (region blocked)
→ 401 { XErr: 2148916238 }   (child account; needs Family Pack)
→ 401 { XErr: 2148916236 }   (verify age)
```

Map these XErr codes to specific exceptions (`MSAAccountNeedsXboxError`,
`MSAChildAccountError`, etc.) — they're the #1 reason real users
fail.

### Stage 5: Mojang exchange

```
POST https://api.minecraftservices.com/authentication/login_with_xbox
  { "identityToken": "XBL3.0 x=<userhash>;<xsts-token>" }
→ { "username": "<not actually minecraft username>", "access_token": "<mc-token>", "expires_in": ... }
```

Then fetch the profile to get the real username + UUID:

```
GET https://api.minecraftservices.com/minecraft/profile
  Authorization: Bearer <mc-token>
→ { "id": "<undashed-uuid>", "name": "<username>" }
→ 404                 (account has no Minecraft attached / not paid)
```

### Stage 6: axochat Mojang flow

Now we have everything axochat needs. The existing chat connection
flow (already supported by our package's wire protocol — we just don't
expose a high-level helper) is:

1. Connect to `wss://chat.liquidbounce.net:7886/ws`.
2. Send `RequestMojangInfo {}`.
3. Receive `MojangInfo { session_hash: <20-byte hex> }`.
4. Call **Mojang's joinServer**:

   ```
   POST https://sessionserver.mojang.com/session/minecraft/join
     Authorization: Bearer <mc-token>
     { "accessToken": "<mc-token>", "selectedProfile": "<undashed-uuid>",
       "serverId": "<session_hash>" }
   → 204 No Content
   ```

5. Send `LoginMojang { name, uuid, allow_messages: true }`.
6. Receive `Success { reason: "Login" }`.
7. Send `RequestJWT {}`.
8. Receive `NewJWT { token: "..." }` — **this is the long-lived JWT
   we save**. Done.

## Proposed module layout

```
src/liquidchat/
  msa/
    __init__.py          # public API: login_with_msa, MsaSession
    device_code.py       # stages 1-2 (Microsoft OAuth)
    xbox.py              # stages 3-4 (XBL + XSTS)
    minecraft.py         # stage 5 (login_with_xbox + profile)
    axochat_bridge.py    # stage 6 (drives our existing wire protocol)
    storage.py           # refresh-token persistence (XDG)
    exceptions.py        # MsaAuthError + 8-10 subclasses for XErr codes
```

Public API sketch:

```python
@dataclass
class MsaSession:
    access_token: str          # Microsoft
    refresh_token: str
    minecraft_token: str
    minecraft_uuid: str
    minecraft_username: str

    async def fetch_axochat_jwt(self) -> str: ...
    async def refresh(self) -> None: ...

async def login_with_msa(
    *,
    state_path: Path | None = None,         # ~/.local/state/liquidchat/msa.json
    client_id: str = MINECRAFT_LAUNCHER_CLIENT_ID,
    on_user_code: Callable[[str, str], None] | None = None,  # display the device code
    http_client: httpx.AsyncClient | None = None,
) -> MsaSession:
    """Run the full MSA → XBL → XSTS → Mojang flow.

    Uses cached refresh token if state_path is provided and not expired,
    otherwise kicks off the device-code flow.
    """
```

## Dependencies

- `httpx` (already in deps)
- Nothing else. Skip `msal` for the reasons above.

## Testing strategy

- Unit tests: mock each HTTP endpoint with `httpx.MockTransport`,
  including all known XErr codes and the no-Minecraft-profile 404.
- We **cannot** integration-test stages 1-5 without a real MS account.
  Workaround: gate end-to-end tests behind `LIQUIDCHAT_MSA_REFRESH=...`
  env var, skip otherwise.
- Stage 6 we can test against our own axochat_server fixture by
  swapping the joinServer URL. But it's still mostly an integration
  test of the wire protocol, which we already cover indirectly.

## Risks / gotchas

1. **Endpoint churn.** Microsoft and Mojang both rotate URLs / response
   shapes occasionally. Last big break was Mojang dropping name-history
   in 2022; Microsoft moves slower but still hits us once every 2-3
   years. We should keep the per-stage code small + isolated.
2. **Client ID ownership.** We're using the public Minecraft Launcher
   client_id `00000000-402b-4cd3-a82b-c45ab2f1d3f7`. This is what every
   open-source launcher (PolyMC/Prism, Lunar's open parts, etc.) uses;
   Microsoft has tolerated it for ~5 years. They could revoke it; if
   they do, every Minecraft launcher on Earth breaks the same day, so
   we'd rev together.
3. **Rate limits.** XSTS gets cranky if you mash refresh. Cache the XSTS
   token; it's valid ~24h.
4. **Refresh-token storage.** Plaintext file with 0600 perms in XDG
   state dir is the typical pattern. Better would be keyring, but that
   pulls in `keyring` (~20 deps, breaks on headless). Default to file
   with a doc-warning; offer pluggable storage callback.
5. **Headless device-code UX.** The user has to manually type the code
   into a browser somewhere. That's annoying for true daemons. There's
   no good fix; refresh-token persistence makes it a one-time pain.

## Effort estimate

- Stage 1-2 (MSA device-code): ~80 lines + tests
- Stage 3-4 (XBL+XSTS): ~70 lines + tests (mostly the XErr mapping)
- Stage 5 (Mojang exchange): ~40 lines + tests
- Stage 6 (axochat bridge): ~60 lines, glues into existing PersistentClient
- Refresh-token storage + CLI helper: ~50 lines
- Tests: ~400 lines (most of the effort is mocking)
- Docs + examples: ~30 lines

Total: probably 700-900 lines, ~1-2 focused days.

## Open questions

- Do we want this in core `liquidchat` or a `liquidchat[msa]` extra?
  I lean **extra** so users who already have a JWT aren't shipping
  Microsoft-oauth code they'll never run. The package already optional-
  installs `mojang.py` features; extras-only would keep import-time
  surface small.
- Should the JWT we mint be auto-rotated by `PersistentClient` when it
  notices `is_token_expired(...)` is true? Tempting, but couples two
  concerns. Better: expose `MsaSession.fetch_axochat_jwt()` plus
  the new `PersistentClient.request_new_jwt()` (already shipped) and
  let callers compose.
- The auth flow is the same for the LiquidBounce *site* login (where
  `chat.liquidbounce.net` is hosted). If they ever switch to OAuth
  proper (auth-code with localhost redirect), we'd add a second
  bootstrap option. Out of scope for now.
