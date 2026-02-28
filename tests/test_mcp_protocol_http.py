from __future__ import annotations

import json
from pathlib import Path

from starlette.testclient import TestClient

from conftest import init_git_repo
from smallfactory.core.v1.entities import create_entity
from smallfactory.core.v1.inventory import inventory_post
from smallfactory.mcp_server import build_mcp_server


def _extract_sse_json(text: str) -> dict:
    for line in (text or "").splitlines():
        if line.startswith("data: "):
            payload = line[len("data: ") :].strip()
            if payload:
                return json.loads(payload)
    raise AssertionError(f"No SSE JSON payload found in response: {text!r}")


def _init_session(client: TestClient) -> str:
    resp = client.post(
        "/mcp",
        headers={
            "content-type": "application/json",
            "accept": "application/json, text/event-stream",
        },
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0"},
            },
        },
    )
    assert resp.status_code == 200
    sid = resp.headers.get("mcp-session-id")
    assert sid
    body = _extract_sse_json(resp.text)
    assert body.get("result", {}).get("serverInfo", {}).get("name") == "smallfactory"
    return sid


def test_streamable_http_tools_flow(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    init_git_repo(repo)
    create_entity(repo, "l_main", {"name": "Main"})
    create_entity(repo, "p_demo", {"name": "Demo Part"})
    inventory_post(repo, "p_demo", 3, l_sfid="l_main")

    mcp = build_mcp_server(datarepo_path=repo, streamable_http_path="/mcp")
    app = mcp.streamable_http_app()
    with TestClient(app, base_url="http://127.0.0.1:8080") as client:
        sid = _init_session(client)

        tools_resp = client.post(
            "/mcp",
            headers={
                "content-type": "application/json",
                "accept": "application/json, text/event-stream",
                "mcp-session-id": sid,
            },
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        assert tools_resp.status_code == 200
        tools_body = _extract_sse_json(tools_resp.text)
        names = [t.get("name") for t in tools_body.get("result", {}).get("tools", [])]
        assert "repo_info" in names
        assert "parts_inventory_list" in names

        repo_info_resp = client.post(
            "/mcp",
            headers={
                "content-type": "application/json",
                "accept": "application/json, text/event-stream",
                "mcp-session-id": sid,
            },
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "repo_info", "arguments": {}},
            },
        )
        assert repo_info_resp.status_code == 200
        repo_info_body = _extract_sse_json(repo_info_resp.text)
        path = (
            repo_info_body.get("result", {})
            .get("structuredContent", {})
            .get("result", {})
            .get("datarepo_path")
        )
        assert str(repo) == path


def test_streamable_http_resources_flow(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    init_git_repo(repo)

    mcp = build_mcp_server(datarepo_path=repo, streamable_http_path="/mcp")
    app = mcp.streamable_http_app()
    with TestClient(app, base_url="http://127.0.0.1:8080") as client:
        sid = _init_session(client)

        resources_resp = client.post(
            "/mcp",
            headers={
                "content-type": "application/json",
                "accept": "application/json, text/event-stream",
                "mcp-session-id": sid,
            },
            json={"jsonrpc": "2.0", "id": 10, "method": "resources/list", "params": {}},
        )
        assert resources_resp.status_code == 200
        resources_body = _extract_sse_json(resources_resp.text)
        uris = [r.get("uri") for r in resources_body.get("result", {}).get("resources", [])]
        assert "smallfactory://repo_info" in uris
        assert "smallfactory://status" in uris

        read_resp = client.post(
            "/mcp",
            headers={
                "content-type": "application/json",
                "accept": "application/json, text/event-stream",
                "mcp-session-id": sid,
            },
            json={
                "jsonrpc": "2.0",
                "id": 11,
                "method": "resources/read",
                "params": {"uri": "smallfactory://repo_info"},
            },
        )
        assert read_resp.status_code == 200
        read_body = _extract_sse_json(read_resp.text)
        contents = read_body.get("result", {}).get("contents", [])
        assert contents and "datarepo_path" in str(contents[0].get("text", ""))
