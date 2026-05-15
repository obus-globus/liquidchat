"""Integration test fixtures: spin up a real axochat_server subprocess."""

from __future__ import annotations

import os
import pathlib
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
AXOCHAT_BIN = _REPO_ROOT / "axochat_server" / "target" / "release" / "axochat"

MOD_UUID = "11111111-1111-1111-1111-111111111111"
USER_A_UUID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
USER_B_UUID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
TARGET_UUID = "22222222-2222-2222-2222-222222222222"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_for_port(host: str, port: int, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError(f"server did not start on {host}:{port} within {timeout}s")


@dataclass
class AxochatServer:
    url: str
    host: str
    port: int
    workdir: pathlib.Path
    proc: subprocess.Popen[bytes]
    gen_token: Callable[[str, str], str]
    config_path: pathlib.Path

    def restart(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        env = {
            **os.environ,
            "CONFIG_PATH": str(self.config_path),
            "RUST_LOG": "warn",
        }
        log = (self.workdir / "axochat.log").open("a")
        self.proc = subprocess.Popen(
            [str(AXOCHAT_BIN), "start"],
            cwd=self.workdir,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        _wait_for_port(self.host, self.port)


@pytest.fixture(scope="session")
def axochat_server() -> Iterator[AxochatServer]:
    if not AXOCHAT_BIN.exists():
        pytest.skip(f"axochat binary missing: {AXOCHAT_BIN}")

    workdir_str = tempfile.mkdtemp(prefix="axochat-test-")
    workdir = pathlib.Path(workdir_str)
    data = workdir / "data"
    data.mkdir()
    (data / "jwt.key").write_bytes(secrets.token_bytes(64))
    (data / "moderators.txt").write_text(MOD_UUID + "\n")
    (data / "banned.txt").write_text("")
    port = _free_port()
    cfg = workdir / "axochat.toml"
    cfg.write_text(
        f"""[net]
address = "127.0.0.1:{port}"

[message]
max_length = 256
max_messages = 9999
count_duration = "60s"

[moderation]
moderators = "data/moderators.txt"
banned = "data/banned.txt"

[auth]
key_file = "data/jwt.key"
algorithm = "HS256"
valid_time = "30d"
allow_anonymous = true
"""
    )
    env = {**os.environ, "CONFIG_PATH": str(cfg), "RUST_LOG": "warn"}
    log_path = workdir / "axochat.log"
    log = log_path.open("w")
    proc = subprocess.Popen(
        [str(AXOCHAT_BIN), "start"],
        cwd=workdir,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    try:
        _wait_for_port("127.0.0.1", port)
    except Exception:
        proc.terminate()
        raise

    def gen_token(name: str, uuid: str) -> str:
        r = subprocess.run(
            [str(AXOCHAT_BIN), "generate", name, uuid],
            cwd=workdir,
            env=env,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        return r.stdout.strip()

    info = AxochatServer(
        url=f"ws://127.0.0.1:{port}/ws",
        host="127.0.0.1",
        port=port,
        workdir=workdir,
        proc=proc,
        gen_token=gen_token,
        config_path=cfg,
    )
    try:
        yield info
    finally:
        info.proc.terminate()
        try:
            info.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            info.proc.kill()
            info.proc.wait()
        # Keep the workdir + log on disk for post-mortem; gc'd by /tmp later.
        if os.environ.get("LIQUIDCHAT_KEEP_TESTDIR") != "1":
            shutil.rmtree(workdir, ignore_errors=True)
        else:
            print(f"\n[conftest] kept test workdir: {workdir}")


@pytest.fixture(autouse=True)
def _ensure_server_alive(axochat_server: AxochatServer) -> None:
    """Restart axochat if a previous test crashed it (server is fragile)."""
    if axochat_server.proc.poll() is not None:
        axochat_server.restart()
        return
    # Process still alive — verify port still accepts connections.
    try:
        with socket.create_connection(
            (axochat_server.host, axochat_server.port), timeout=0.5
        ):
            pass
    except OSError:
        axochat_server.restart()


@pytest.fixture
def jwt_user_a(axochat_server: AxochatServer) -> str:
    return axochat_server.gen_token("user_a", USER_A_UUID)


@pytest.fixture
def jwt_user_b(axochat_server: AxochatServer) -> str:
    return axochat_server.gen_token("user_b", USER_B_UUID)


@pytest.fixture
def jwt_mod(axochat_server: AxochatServer) -> str:
    return axochat_server.gen_token("moduser", MOD_UUID)
