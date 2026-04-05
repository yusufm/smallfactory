from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _docker_enabled() -> bool:
    return shutil.which("docker") is not None and os.getenv("SF_RUN_DOCKER_TESTS") == "1"


pytestmark = pytest.mark.skipif(
    not _docker_enabled(),
    reason="Docker integration tests require docker plus SF_RUN_DOCKER_TESTS=1",
)


def _run(cmd: list[str], *, cwd: Path = ROOT, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise AssertionError(
            f"command failed ({result.returncode}): {' '.join(cmd)}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _http_request(
    url: str,
    *,
    method: str = "GET",
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=5.0) as resp:
            return int(resp.status), dict(resp.headers.items()), resp.read()
    except urllib.error.HTTPError as exc:
        return int(exc.code), dict(exc.headers.items()), exc.read()


def _wait_for_ok(url: str, *, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    last_status = None
    last_body = b""
    while time.time() < deadline:
        try:
            status, _, body = _http_request(url)
            last_status = status
            last_body = body
            if status == 200:
                return
        except Exception as exc:
            last_status = None
            last_body = str(exc).encode("utf-8", errors="ignore")
        time.sleep(0.25)
    raise AssertionError(f"timed out waiting for {url}; last_status={last_status}, body={last_body[:200]!r}")


def _docker_logs(name: str) -> str:
    result = _run(["docker", "logs", name], check=False)
    return (result.stdout or "") + (result.stderr or "")


@pytest.fixture(scope="session")
def docker_image_tag() -> str:
    info = _run(["docker", "info"], check=False)
    if info.returncode != 0:
        pytest.skip(f"docker daemon unavailable: {info.stderr or info.stdout}")

    tag = f"smallfactory-test:{uuid.uuid4().hex[:8]}"
    _run(["docker", "build", "-t", tag, "."])
    return tag


def test_docker_web_mcp_persistence_and_host_file_workflow(tmp_path: Path, docker_image_tag: str):
    data_dir = tmp_path / "data"
    work_dir = tmp_path / "work"
    data_dir.mkdir(parents=True)
    work_dir.mkdir(parents=True)
    payload_path = work_dir / "payload.txt"
    payload_path.write_text("captured from host", encoding="utf-8")

    name1 = f"sf-web-{uuid.uuid4().hex[:8]}"
    name2 = f"sf-web-{uuid.uuid4().hex[:8]}"
    port1 = _free_port()
    port2 = _free_port()
    base1 = f"http://127.0.0.1:{port1}"
    base2 = f"http://127.0.0.1:{port2}"

    try:
        _run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                name1,
                "-p",
                f"{port1}:8080",
                "-e",
                "SF_WEB_AUTOPUSH=0",
                "-v",
                f"{data_dir}:/data",
                docker_image_tag,
            ]
        )
        _wait_for_ok(f"{base1}/")

        init_payload = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "pytest", "version": "0"},
                },
            }
        ).encode("utf-8")
        status, headers, body = _http_request(
            f"{base1}/mcp",
            method="POST",
            data=init_payload,
            headers={
                "content-type": "application/json",
                "accept": "application/json, text/event-stream",
            },
        )
        assert status == 200, _docker_logs(name1)
        assert headers.get("mcp-session-id"), headers
        assert b"smallfactory" in body

        _run(
            [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{data_dir}:/data",
                docker_image_tag,
                "entities",
                "add",
                "p_widget",
                "name=Widget",
            ]
        )
        _run(
            [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{data_dir}:/data",
                "-v",
                f"{work_dir}:/work",
                docker_image_tag,
                "entities",
                "files",
                "add",
                "p_widget",
                "/work/payload.txt",
                "payloads/payload.txt",
            ]
        )

        attached = data_dir / "datarepo" / "entities" / "p_widget" / "files" / "payloads" / "payload.txt"
        assert attached.read_text(encoding="utf-8") == "captured from host"

        _run(["docker", "rm", "-f", name1], check=False)

        _run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                name2,
                "-p",
                f"{port2}:8080",
                "-e",
                "SF_WEB_AUTOPUSH=0",
                "-v",
                f"{data_dir}:/data",
                docker_image_tag,
            ]
        )
        _wait_for_ok(f"{base2}/entities/p_widget")
        status, _, body = _http_request(f"{base2}/entities/p_widget")
        assert status == 200, _docker_logs(name2)
        assert b"Widget" in body
        assert attached.is_file()
    finally:
        _run(["docker", "rm", "-f", name1], check=False)
        _run(["docker", "rm", "-f", name2], check=False)
