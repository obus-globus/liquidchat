"""Probe whether a Microsoft v1 client_id accepts a loopback redirect end-to-end.

Used to verify the findings from the redirect_uri probe — in particular, that
``bedrock-playstation`` (``000000004827c78e``) accepts arbitrary
``http://{127.0.0.1,localhost}:*/<anything>`` redirects, while the other v1
Bedrock client_ids only accept the OOB ``oauth20_desktop.srf`` redirect.

This is an experimental tool — kept on a branch, not on master. Run via::

    uvx --from "git+https://github.com/clawdbot-silly-waddle/liquidchat@experiment/loopback-probe" liquidchat-loopback-probe

Or with explicit args::

    uvx --from "git+...@experiment/loopback-probe" liquidchat-loopback-probe \\
        --client-id 000000004827c78e --bind-host 127.0.0.1 --path /cb
"""

from __future__ import annotations

import argparse
import asyncio
import secrets
import sys
import webbrowser
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from mcapi_auth._constants import (
    LIVE_CONNECT_AUTHORIZE_URL,
    LIVE_CONNECT_SCOPE_MBI_SSL,
    LIVE_CONNECT_TOKEN_URL,
)

BEDROCK_PS_CLIENT_ID = "000000004827c78e"


async def _run(args: argparse.Namespace) -> int:
    state = secrets.token_urlsafe(16)
    received: asyncio.Future[dict[str, str]] = asyncio.get_running_loop().create_future()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await reader.readline()
            request_line = line.decode("latin-1", errors="replace").strip()
            try:
                _, full_path, _ = request_line.split(" ", 2)
            except ValueError:
                full_path = "/"
            parsed = urlparse(full_path)
            params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            body = b"<html><body><h2>OK, you can close this tab.</h2></body></html>"
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n"
                b"Content-Length: " + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body,
            )
            await writer.drain()
            if ("code" in params or "error" in params) and not received.done():
                received.set_result(params)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    server = await asyncio.start_server(handle, host=args.bind_host, port=args.port)
    actual_port = int((server.sockets or [])[0].getsockname()[1])
    redirect_uri = f"http://{args.bind_host}:{actual_port}{args.path}"

    auth_url = (
        LIVE_CONNECT_AUTHORIZE_URL
        + "?"
        + urlencode(
            {
                "client_id": args.client_id,
                "response_type": "code",
                "redirect_uri": redirect_uri,
                "scope": LIVE_CONNECT_SCOPE_MBI_SSL,
                "state": state,
            },
        )
    )

    print(f"[*] listener bound: {redirect_uri}")
    print(f"[*] auth URL:\n    {auth_url}")
    if not args.no_browser:
        try:
            _ = webbrowser.open(auth_url)
        except (RuntimeError, OSError) as e:
            print(f"[!] couldn't open browser ({e}); copy the URL above")
    print("[*] waiting for Microsoft to redirect back (Ctrl-C to abort)...")

    async with server:
        try:
            params = await asyncio.wait_for(received, timeout=args.timeout)
        except TimeoutError:
            print("[FAIL] timed out waiting for callback")
            return 1
        finally:
            server.close()
            try:
                await server.wait_closed()
            except (ConnectionError, OSError):
                pass

    if "error" in params:
        print(f"[FAIL] authorize error: {params.get('error')} — {params.get('error_description', '')}")
        return 2
    if params.get("state") != state:
        print(f"[FAIL] state mismatch: sent {state!r}, got {params.get('state')!r}")
        return 3
    code = params.get("code")
    if not code:
        print(f"[FAIL] no code in callback: {params}")
        return 4

    print(f"[ OK ] got authorization code ({len(code)} chars). Exchanging for token...")

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            LIVE_CONNECT_TOKEN_URL,
            data={
                "client_id": args.client_id,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
                "scope": LIVE_CONNECT_SCOPE_MBI_SSL,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    if r.status_code != 200:
        print(f"[FAIL] token endpoint returned {r.status_code}")
        print(f"        body: {r.text[:500]}")
        return 5

    token = r.json()
    print(f"[ OK ] token exchange succeeded! keys={sorted(token)}")
    if "access_token" in token:
        print(f"        access_token: {str(token['access_token'])[:40]}...")
    if "refresh_token" in token:
        print(f"        refresh_token: present (length {len(str(token['refresh_token']))})")
    print(f"\n>>> CONFIRMED: client_id {args.client_id} accepts loopback redirect end-to-end. <<<")
    return 0


def main() -> None:
    """Entry point for the ``liquidchat-loopback-probe`` console script."""
    ap = argparse.ArgumentParser(
        prog="liquidchat-loopback-probe",
        description="Probe a v1 Microsoft client_id for loopback-redirect support end-to-end.",
    )
    _ = ap.add_argument("--client-id", default=BEDROCK_PS_CLIENT_ID,
                        help=f"default: {BEDROCK_PS_CLIENT_ID} (bedrock-playstation)")
    _ = ap.add_argument("--bind-host", default="127.0.0.1", help="127.0.0.1 or localhost")
    _ = ap.add_argument("--port", type=int, default=0, help="0 = OS-picked ephemeral port")
    _ = ap.add_argument("--path", default="/cb", help="redirect URI path (any works for PS per the probe)")
    _ = ap.add_argument("--no-browser", action="store_true", help="don't open a browser; print URL only")
    _ = ap.add_argument("--timeout", type=float, default=300.0, help="seconds to wait for callback")
    args = ap.parse_args()
    sys.exit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
