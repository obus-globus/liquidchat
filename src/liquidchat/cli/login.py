"""``liquidchat login`` — run the full Microsoft → Mojang → AxoChat
authentication chain and persist a fresh JWT under a named profile.

End-to-end flow:

1. Run ``mcapi_auth.login`` to obtain a Minecraft access token. The
   MSA refresh token is written to a per-profile path under
   ``$LIQUIDCHAT_HOME/profiles/<name>/refresh_token.json``. If the
   caller didn't pre-pick a profile name, we stage the refresh token
   in a temp path under ``$LIQUIDCHAT_HOME/.staging-*`` and move it
   into the final profile dir once the MSA flow reveals the username.
2. Open the chat websocket, send ``RequestMojangInfo`` →
   ``MojangInfo``.
3. ``join_server`` (Yggdrasil session/minecraft/join).
4. Send ``LoginMojang`` → ``Success``.
5. Send ``RequestJWT`` → ``NewJWT.token``; write to
   ``profiles/<name>/jwt``.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import secrets
from pathlib import Path

import websockets
from mcapi_auth import FileTokenStorage, join_server, login
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


async def _recv_decoded(ws: websockets.ClientConnection, timeout: float) -> object:
    raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
    return decode(raw)


async def _run_login(
    *,
    allow_messages: bool,
    insecure: bool,
    refresh_storage_path: Path | None,
) -> tuple[str, str, str]:
    """Run the auth chain. Returns ``(jwt, username, uuid)``."""
    console.print("[dim]running Microsoft → Minecraft auth...[/dim]")
    storage = FileTokenStorage(path=refresh_storage_path) if refresh_storage_path else None
    session = await login(on_device_code=_on_device_code, storage=storage)
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
        await join_server(
            access_token=session.access_token,
            uuid=session.uuid,
            server_id=server_id,
        )

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
) -> None:
    """Sign in via Microsoft → Mojang → AxoChat and store creds per profile.

    The resulting JWT and (optionally) MSA refresh token are written
    under ``$LIQUIDCHAT_HOME/profiles/<name>/``. The profile name
    defaults to the Minecraft username returned by the MSA flow; pass
    ``--account NAME`` to override (must match ``[A-Za-z0-9._-]+``).

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
    """
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
