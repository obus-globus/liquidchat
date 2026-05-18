"""``liquidchat login`` — run the full Microsoft → Mojang → AxoChat
authentication chain and persist a fresh JWT under a named profile.

Three MSA flows are supported (pick with ``--flow``):

* ``device-code`` (default) — terminal-friendly, prints a code + URL.
  Talks to ``login.microsoftonline.com/consumers/oauth2/v2.0/*`` with
  the Prism Launcher client_id.
* ``browser`` — opens the user's browser to the same v2 endpoints
  with a localhost redirect catcher (PKCE).
* ``browser-v1`` — opens the user's browser to the legacy
  ``login.live.com/oauth20_*.srf`` endpoints with the compressed
  Minecraft Launcher client_id (``00000000402b5328``) and the
  ``MBI_SSL`` scope. No PKCE (v1 predates it). Useful when the v2
  endpoints reject your account / tenant.

The rest of the chain (Xbox Live → XSTS → ``loginWithXbox`` →
chat-server ``LoginMojang`` + ``RequestJWT``) is identical regardless
of the MSA flow you pick.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import secrets
from pathlib import Path
from typing import Literal

import websockets
from mcapi_auth import (
    KNOWN_CLIENT_IDS,
    FileTokenStorage,
    is_browser_unsupported,
    is_v1_client_id,
    join_server,
    login_browser_v1,
    login_browser_v2,
    login_device_code_v2,
    resolve_browser_redirect,
    resolve_client_id,
)
from mcapi_auth.auth.msa import DeviceCodePrompt

from liquidchat import MojangInfo, NewJWT, Success
from liquidchat.exceptions import LoginFailedError, ProtocolError
from liquidchat.protocol import DEFAULT_WS_URL, build_ssl_context, decode, encode

from ._common import (
    console,
    err_console,
    jwt_path,
    liquidchat_home,
    profile_dir,
    read_default_profile,
    refresh_token_path,
    write_default_profile,
)

type FlowName = Literal["device-code", "browser", "browser-v1"]

_MOJANG_INFO_TIMEOUT = 15.0
_LOGIN_ACK_TIMEOUT = 20.0
_JWT_TIMEOUT = 15.0


def _on_device_code(prompt: DeviceCodePrompt) -> None:
    console.print(
        "\n[bold yellow]Microsoft login required[/bold yellow]\n"
        f"  Visit:  [link={prompt.verification_uri}]{prompt.verification_uri}[/link]\n"
        f"  Code:   [bold cyan]{prompt.user_code}[/bold cyan]\n"
        f"  Expires in {prompt.expires_in}s\n"
    )


def _announce_browser(url: str) -> None:
    """Browser-flow callback — print the URL before opening it.

    The default ``webbrowser.open`` is also called so users with a
    desktop session get the page automatically. SSH sessions / headless
    boxes can copy the printed URL instead.
    """
    import webbrowser

    console.print(
        "\n[bold yellow]Microsoft login required[/bold yellow]\n"
        f"  Open in your browser: [link={url}]{url}[/link]\n"
    )
    with contextlib.suppress(Exception):
        webbrowser.open(url)


async def _recv_decoded(ws: websockets.ClientConnection, timeout: float) -> object:
    raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
    return decode(raw)


async def _run_login(
    *,
    allow_messages: bool,
    insecure: bool,
    refresh_storage_path: Path | None,
    flow: FlowName,
    client_id: str,
    force_flow: bool,
    bind_host: str | None,
    bind_port: int | None,
    redirect_path: str | None,
) -> tuple[str, str, str]:
    """Run the auth chain. Returns ``(jwt, username, uuid)``."""
    is_v1 = is_v1_client_id(client_id)
    effective_flow: FlowName = flow if force_flow else ("browser-v1" if is_v1 else flow)
    console.print(
        f"[dim]running Microsoft → Minecraft auth "
        f"(flow={effective_flow}, client_id={client_id}"
        f"{', force_flow=True' if force_flow else ''})...[/dim]"
    )
    storage = FileTokenStorage(path=refresh_storage_path) if refresh_storage_path else None

    if is_v1 and not force_flow:
        # v1 client_ids only work against login.live.com/oauth20_*.srf
        # which doesn't support a device-code endpoint we talk to.
        # Force the OOB browser flow.
        if flow != "device-code" and flow != "browser-v1":
            # User explicitly passed --flow browser with a v1 client_id;
            # the closest equivalent is browser-v1 (which we're using).
            console.print(
                f"[yellow]note:[/yellow] --flow {flow} is not supported for v1 "
                f"client_id {client_id!r}; using browser-v1 instead. "
                f"Pass --force-flow to disable this auto-dispatch."
            )
        if bind_host is not None or bind_port is not None or redirect_path is not None:
            console.print(
                "[yellow]note:[/yellow] --bind-host / --bind-port / "
                "--redirect-path are ignored for v1 client_ids (the OOB "
                "paste-back flow doesn't use a local listener)."
            )
        session = await login_browser_v1(
            client_id=client_id,
            storage=storage,
            open_browser=_announce_browser,
        )
    elif force_flow and flow == "browser-v1":
        # Explicit v1 browser flow regardless of client_id format. With
        # a v2 GUID client_id this will probably fail at the v1 authorize
        # endpoint, but that's the user's experiment to run.
        if bind_host is not None or bind_port is not None or redirect_path is not None:
            console.print(
                "[yellow]note:[/yellow] --bind-host / --bind-port / "
                "--redirect-path are ignored for --flow browser-v1 (OOB "
                "paste-back flow doesn't use a local listener)."
            )
        session = await login_browser_v1(
            client_id=client_id,
            storage=storage,
            open_browser=_announce_browser,
        )
    elif flow == "device-code":
        if bind_host is not None or bind_port is not None or redirect_path is not None:
            console.print(
                "[yellow]note:[/yellow] --bind-host / --bind-port / "
                "--redirect-path are ignored for --flow device-code "
                "(no local listener)."
            )
        session = await login_device_code_v2(
            client_id=client_id, on_device_code=_on_device_code, storage=storage
        )
    elif flow in ("browser", "browser-v1"):
        # v2 client_id + browser → PKCE+localhost flow.
        # browser-v1 with a v2 client_id is a contradiction; we silently
        # use the v2 browser flow (the user picked a GUID).
        if flow == "browser-v1" and not force_flow:
            console.print(
                "[yellow]note:[/yellow] browser-v1 requested with a v2 (GUID) "
                f"client_id {client_id!r}; using the v2 browser flow instead. "
                f"Pass --force-flow to disable this auto-dispatch."
            )
        if is_browser_unsupported(client_id) and not force_flow:
            console.print(
                f"[yellow]warning:[/yellow] client_id {client_id!r} has no "
                "loopback reply URL registered on its Azure-AD app; the "
                "browser flow will fail at the authorize step. Use "
                "--flow device-code instead, or pass --force-flow to try anyway."
            )
        # Resolve the redirect URI in priority order:
        # 1. explicit --bind-host / --redirect-path CLI overrides
        # 2. per-client default from mcapi_auth.KNOWN_CLIENT_REDIRECTS
        # 3. the library defaults (127.0.0.1, /callback)
        override = resolve_browser_redirect(client_id)
        eff_bind_host = bind_host
        eff_redirect_path = redirect_path
        if eff_bind_host is None and override is not None:
            eff_bind_host = override[0]
        if eff_redirect_path is None and override is not None:
            eff_redirect_path = override[1]
        if eff_bind_host is not None or eff_redirect_path is not None or bind_port is not None:
            session = await login_browser_v2(
                client_id=client_id,
                storage=storage,
                open_browser=_announce_browser,
                bind_host=eff_bind_host if eff_bind_host is not None else "127.0.0.1",
                bind_port=bind_port if bind_port is not None else 0,
                redirect_path=eff_redirect_path if eff_redirect_path is not None else "/callback",
            )
        else:
            session = await login_browser_v2(
                client_id=client_id,
                storage=storage,
                open_browser=_announce_browser,
            )
    else:  # pragma: no cover - guarded at CLI layer
        msg = f"unknown MSA flow {flow!r}"
        raise ValueError(msg)
    console.print(f"[green]signed in as[/green] [bold]{session.username}[/bold] ({session.uuid})")

    ssl_ctx = build_ssl_context(insecure=insecure)
    async with websockets.connect(DEFAULT_WS_URL, ssl=ssl_ctx) as ws:
        console.print("[dim]requesting mojang challenge...[/dim]")
        await ws.send(encode("RequestMojangInfo"))
        msg = await _recv_decoded(ws, _MOJANG_INFO_TIMEOUT)
        body = getattr(msg, "c", None)
        if not isinstance(body, MojangInfo):
            raise ProtocolError(f"expected MojangInfo, got {msg!r}")
        server_id: str = body.session_hash
        console.print(f"[dim]session_hash = {server_id}[/dim]")

        console.print("[dim]joining sessionserver...[/dim]")
        await join_server(session, server_id=server_id)

        console.print("[dim]sending LoginMojang...[/dim]")
        await ws.send(
            encode(
                "LoginMojang",
                {
                    "name": session.username,
                    "uuid": session.uuid,
                    "allow_messages": allow_messages,
                },
            )
        )
        msg = await _recv_decoded(ws, _LOGIN_ACK_TIMEOUT)
        body = getattr(msg, "c", None)
        if not isinstance(body, Success):
            raise LoginFailedError(f"server rejected LoginMojang: {msg!r}")
        if body.reason != "Login":
            raise LoginFailedError(f"unexpected Success reason: {body.reason!r}")
        console.print("[green]chat server accepted login.[/green]")

        console.print("[dim]requesting JWT...[/dim]")
        await ws.send(encode("RequestJWT"))
        msg = await _recv_decoded(ws, _JWT_TIMEOUT)
        body = getattr(msg, "c", None)
        if not isinstance(body, NewJWT):
            raise ProtocolError(f"expected NewJWT, got {msg!r}")
        return body.token, session.username, session.uuid


def login_cmd(
    *,
    account: str | None = None,
    allow_messages: bool = True,
    insecure: bool = True,
    remember: bool = True,
    set_default: bool | None = None,
    print_token: bool = False,
    flow: FlowName = "device-code",
    client_id: str = "prism",
    force_flow: bool = False,
    bind_host: str | None = None,
    bind_port: int | None = None,
    redirect_path: str | None = None,
) -> None:
    """Sign in via Microsoft → Mojang → AxoChat and store creds per profile.

    The resulting JWT and (optionally) MSA refresh token are written
    under ``$LIQUIDCHAT_HOME/profiles/<name>/``. The profile name
    defaults to the Minecraft username returned by the MSA flow; pass
    ``--account NAME`` to override (must match ``[A-Za-z0-9._-]+``).

    Pick the MSA authentication flow with ``--flow``:

    * ``device-code`` (default): terminal-friendly device-code prompt
      against the v2 endpoints. Only works with v2 (GUID) client_ids.
    * ``browser``: opens the browser. For v2 client_ids uses
      localhost-redirect + PKCE; for v1 client_ids uses the OOB
      ``oauth20_desktop.srf`` paste-back UX.
    * ``browser-v1`` (alias for ``browser`` when a v1 client_id is in
      use). Kept for backwards compatibility — the auth library
      auto-picks v1 vs v2 based on ``--client-id``.

    Pick the Microsoft OAuth client_id with ``--client-id``. Accepts
    either a friendly alias (see the table below) or a literal
    client_id string. Default is ``prism``.

    Aliases (from ``mcapi_auth.KNOWN_CLIENT_IDS``):

    * v2 (Azure-AD GUID, ``XboxLive.signin`` scope):
      ``prism`` (default), ``edu``, ``office365``.
    * v1 (Live-Connect compressed, ``MBI_SSL`` scope):
      ``java``, ``bedrock-win32``, ``bedrock-android``, ``bedrock-ios``,
      ``bedrock-nintendo``, ``bedrock-playstation``, ``xbox-app-ios``,
      ``xbox-gamepass-ios``.

    Picking a v1 client_id forces the OOB browser flow regardless of
    ``--flow``. Note that Bedrock client_ids will still complete the
    chain (XBL/XSTS/loginWithXbox) but only succeed for accounts where
    the Bedrock title is entitled.

    The first profile created in a fresh home dir is auto-promoted to
    the default; subsequent logins leave the default alone unless
    ``--set-default`` is explicitly passed.

    Args:
        account: Profile name to store credentials under. Defaults to
            the Minecraft username from the auth flow.
        allow_messages: Whether to accept private messages.
        insecure: Skip TLS verification on the websocket. Default
            ``True`` against the cert-expired public deployment.
        remember: If True (default) persist the MSA refresh token so
            subsequent logins skip the browser step.
        set_default: ``None`` (default) → promote this profile to
            default only if there isn't one yet. ``True`` forces it,
            ``False`` leaves the existing default alone.
        print_token: Also echo the JWT to stdout.
        flow: MSA flow to use. See above.
        client_id: Microsoft OAuth client_id (alias or literal).
            Default ``"prism"``.
        force_flow: When ``True``, disable the auto-dispatch from
            ``--flow`` + ``--client-id``. Useful for testing
            non-standard combinations (e.g. ``--flow browser`` with a
            v1 client_id, or ``--flow browser-v1`` with a v2 GUID).
            Most such combinations will fail at Microsoft's authorize
            endpoint, which is the point — you'll see the real error.
            Default ``False``.
        bind_host: Override the local listener host for ``--flow browser``
            (default: per-client from ``mcapi_auth.KNOWN_CLIENT_REDIRECTS``,
            then ``127.0.0.1``). Must match the Azure app's registered
            reply URI host.
        bind_port: Override the local listener port for ``--flow browser``
            (default: ``0`` — let the OS pick a free ephemeral port).
            Pin this only if your Azure app registration requires an
            exact port (rare; most accept ``http://host:*/path``).
        redirect_path: Override the local listener path for ``--flow
            browser`` (default: per-client, then ``/callback``). Must match
            the Azure app's registered reply URI path.
    """
    resolved_client_id = resolve_client_id(client_id)
    if resolved_client_id == client_id and client_id.lower() not in KNOWN_CLIENT_IDS:
        # Passed-through raw client_id — make sure it looks plausible.
        console.print(
            f"[dim]using literal client_id {resolved_client_id!r} (no matching alias)[/dim]"
        )
    # If the caller pre-picked --account, write refresh straight into
    # the final destination. Otherwise stage in a temp path and rename
    # after we learn the Minecraft username.
    home = liquidchat_home()
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    refresh_path: Path | None
    staged: Path | None = None
    if not remember:
        refresh_path = None
    elif account is not None:
        profile_dir(account).mkdir(parents=True, exist_ok=True, mode=0o700)
        refresh_path = refresh_token_path(account)
    else:
        staged = home / f".staging-refresh-{secrets.token_hex(8)}.json"
        refresh_path = staged

    try:
        token, username, _uuid = asyncio.run(
            _run_login(
                allow_messages=allow_messages,
                insecure=insecure,
                refresh_storage_path=refresh_path,
                flow=flow,
                client_id=resolved_client_id,
                force_flow=force_flow,
                bind_host=bind_host,
                bind_port=bind_port,
                redirect_path=redirect_path,
            ),
        )
    except LoginFailedError as exc:
        if staged is not None:
            staged.unlink(missing_ok=True)
        err_console.print(f"[red]login failed:[/red] {exc}")
        raise SystemExit(1) from exc
    except BaseException:
        if staged is not None:
            staged.unlink(missing_ok=True)
        raise

    chosen_name = account if account is not None else username
    profile_dir(chosen_name).mkdir(parents=True, exist_ok=True, mode=0o700)

    if staged is not None:
        final_refresh = refresh_token_path(chosen_name)
        try:
            staged.replace(final_refresh)
        except OSError as e:
            err_console.print(f"[yellow]warning:[/yellow] could not move staged refresh token: {e}")
            staged.unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            os.chmod(final_refresh, 0o600)

    out = jwt_path(chosen_name)
    out.write_text(token + "\n", encoding="utf-8")
    with contextlib.suppress(OSError):
        os.chmod(out, 0o600)

    promote = set_default if set_default is not None else (read_default_profile() is None)
    if promote:
        write_default_profile(chosen_name)

    err_console.print(f"[green]JWT saved to[/green] {out}  [dim](profile: {chosen_name})[/dim]")
    if remember:
        rt = refresh_token_path(chosen_name)
        if rt.is_file():
            err_console.print(f"[green]MSA refresh token saved to[/green] {rt}")
    if promote:
        err_console.print(f"[green]default profile set to[/green] {chosen_name}")
    if print_token:
        print(token)


__all__ = ["login_cmd"]
