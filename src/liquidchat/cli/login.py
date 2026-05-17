"""``liquidchat login`` — run the full Microsoft → Mojang → AxoChat
authentication chain and persist a fresh JWT.

End-to-end flow:

1. Open the chat websocket and send ``RequestMojangInfo``; the server
   replies with a per-session ``session_hash`` (the "serverId" used in
   the Yggdrasil joinServer handshake).
2. Run ``mcapi_auth.login`` to obtain a Minecraft access token (MSA
   device-code flow on first run, refresh-token reuse on subsequent
   runs).
3. POST that token + UUID + session_hash to
   ``sessionserver.mojang.com/session/minecraft/join``
   (``mcapi_auth.join_server``).
4. Send ``LoginMojang { name, uuid, allow_messages }`` to the chat
   server, wait for ``Success { reason: "Login" }``.
5. Send ``RequestJWT``, capture ``NewJWT.token``, persist to the token
   file (or print to stdout).

No state from the existing ``--token`` path is required — this is the
"I have nothing, log me in" entrypoint.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from pathlib import Path

import websockets
from mcapi_auth import FileTokenStorage, join_server, login
from mcapi_auth.auth.msa import DeviceCodePrompt

from liquidchat import MojangInfo, NewJWT, Success
from liquidchat.exceptions import LoginFailedError, ProtocolError
from liquidchat.protocol import DEFAULT_WS_URL, build_ssl_context, decode, encode

from ._common import console, err_console

_DEFAULT_TOKEN_PATH = Path.home() / ".config" / "liquidchat" / "token"

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
    remember: bool,
) -> str:
    # Run the Microsoft → Minecraft auth chain BEFORE opening the
    # websocket: device-code flow can take minutes (user has to walk
    # to a browser) and chat.liquidbounce.net's keepalive will close
    # an idle websocket long before the user finishes.
    console.print("[dim]running Microsoft → Minecraft auth...[/dim]")
    storage = FileTokenStorage() if remember else None
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
        return body.token


def login_cmd(
    *,
    allow_messages: bool = True,
    insecure: bool = True,
    remember: bool = True,
    out: Path | None = None,
    print_token: bool = False,
) -> None:
    """Sign in via Microsoft → Mojang → AxoChat and persist the JWT.

    Steps the user through MSA device-code authentication, then
    completes the Yggdrasil joinServer / LoginMojang handshake against
    ``chat.liquidbounce.net``, and finally calls ``RequestJWT`` to
    obtain a persistent token.

    Args:
        allow_messages: If True (default) other clients may send you
            private messages while you're online with this token. The
            flag is encoded into the LoginMojang payload.
        insecure: Skip TLS verification on the websocket. Default
            ``True`` because the public ``chat.liquidbounce.net``
            deployment serves an expired cert. Pass ``--no-insecure``
            against a private deployment with a valid cert.
        remember: If True (default) cache the MSA refresh token at
            ``$XDG_STATE_HOME/mcapi_auth/refresh_token.json`` so the
            next ``liquidchat login`` skips the browser step. Pass
            ``--no-remember`` to keep the device-code flow ephemeral
            — mcapi-auth itself no longer writes anything to disk
            unless this flag (or an explicit storage object) opts in.
        out: Where to write the JWT. Defaults to
            ``~/.config/liquidchat/token`` (or
            ``$LIQUIDCHAT_TOKEN_FILE`` when set). The parent directory
            is created with mode 0700 if missing.
        print_token: Also echo the token to stdout (useful for piping
            into a different store). The success line goes to stderr,
            so ``liquidchat login --print-token > tokenfile`` only
            captures the JWT itself.
    """
    try:
        token = asyncio.run(
            _run_login(allow_messages=allow_messages, insecure=insecure, remember=remember),
        )
    except LoginFailedError as exc:
        err_console.print(f"[red]login failed:[/red] {exc}")
        raise SystemExit(1) from exc

    if out is None:
        env_path = os.environ.get("LIQUIDCHAT_TOKEN_FILE")
        out = Path(env_path) if env_path else _DEFAULT_TOKEN_PATH
    out.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    out.write_text(token + "\n", encoding="utf-8")
    with contextlib.suppress(OSError):
        # Best-effort on platforms (Windows) that don't honour POSIX modes.
        os.chmod(out, 0o600)
    err_console.print(f"[green]JWT saved to[/green] {out}")
    if print_token:
        print(token)


__all__ = ["login_cmd"]
