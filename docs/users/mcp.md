# MCP Integration Guide

Use this guide to connect AI clients (Windsurf, Cursor, Claude Desktop, Codex) to smallFactory via MCP.

## Run smallFactory with MCP enabled

From the project root:

```bash
python3 sf.py web --repo /ABS/PATH/TO/YOUR/DATAREPO --port 8080
```

By default:
- Web UI: `http://127.0.0.1:8080`
- MCP endpoint: `http://127.0.0.1:8080/mcp`

Quick health check:

```bash
curl -i -X POST http://127.0.0.1:8080/mcp \
  -H 'content-type: application/json' \
  -H 'accept: application/json, text/event-stream' \
  -d '{}'
```

A JSON-RPC validation error (HTTP 400) is expected for `{}` and confirms MCP is reachable.

## Client configuration examples

### Windsurf

Use streamable HTTP:

```json
{
  "mcpServers": {
    "smallfactory": {
      "disabled": false,
      "transport": "streamable-http",
      "serverUrl": "http://127.0.0.1:8080/mcp"
    }
  }
}
```

Notes:
- Use a tool-enabled chat mode (for example, Code mode) if you want `tools/call`.
- Some modes may only use resources; smallFactory exposes compatibility resources for those clients.

### Cursor

```json
{
  "mcpServers": {
    "smallfactory": {
      "transport": "streamable-http",
      "serverUrl": "http://127.0.0.1:8080/mcp"
    }
  }
}
```

### Claude Desktop

```json
{
  "mcpServers": {
    "smallfactory": {
      "transport": "streamable-http",
      "serverUrl": "http://127.0.0.1:8080/mcp"
    }
  }
}
```

### Codex App

In Codex app settings, add MCP server:
- transport: `streamable-http`
- URL: `http://127.0.0.1:8080/mcp`

## Recommended first calls

Tool-first clients:
1. `server_status`
2. `repo_info`
3. `parts_inventory_list`

Resource-first clients:
1. `smallfactory://status`
2. `smallfactory://repo_info`
3. `smallfactory://parts/quantities`

## Common issues

- `POST /mcp 404`:
  - Usually means app was started with `python3 web/app.py` (Flask only).
  - Start with `python3 sf.py web ...` instead.

- Connected but tool calls fail:
  - Client/session is likely resource-only.
  - Use resource URIs above or switch to a tool-enabled mode/session.

- Wrong repo appears in results:
  - Always verify with `repo_info` or `smallfactory://repo_info`.
  - Pin repo explicitly when starting: `--repo /ABS/PATH/...`.
