# MCP Integration Guide

Use this guide to connect AI clients (Windsurf, Cursor, Claude Desktop, Codex) to smallFactory via MCP.

## Run smallFactory with MCP enabled

Install web dependencies first (includes MCP runtime):

```bash
python3 -m pip install -r web/requirements.txt
```

From the project root:

```bash
python3 sf.py web --port 8080
```

By default:
- Web UI: `http://127.0.0.1:8080`
- MCP endpoint: `http://127.0.0.1:8080/mcp`

Repo selection is resolved automatically in this order:
1. `--repo`
2. `SF_DATAREPO`
3. `.smallfactory.yml` `default_datarepo`

Use `--repo /ABS/PATH/...` only when you want to pin a specific datarepo explicitly.

### Endpoint mapping note

Your MCP URL depends on the web bind settings:
- If you run `python3 sf.py web --host 0.0.0.0 --port 8080`, local clients still usually connect to `http://127.0.0.1:8080/mcp`.
- If you change port, update client URL to match (`http://127.0.0.1:<PORT>/mcp`).
- If you set `SF_MCP_PATH`, replace `/mcp` with that path.

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

## Example queries

Use these as copy/paste prompts in your MCP client.

### Tool-first query examples

- `Call repo_info and return only datarepo_path.`
- `Use parts_inventory_list with {"limit": 200, "sort_by": "qty", "sort_dir": "asc"} and return a table of part, qty, status_bucket.`
- `Show parts likely to run out soon using parts_inventory_list with {"status_bucket":"critical","limit":200}.`
- `For part p_abc, list recent repair-related events from build_events_list with {"part_sfid":"p_abc","tags":["repair"],"limit":100}.`
- `What are the most common repair tags in the last 30 days? Use analytics_query grouped by tag.`
- `Resolve BOM for p_widget at max depth 3 using bom_resolved and summarize leaf parts.`

### Resource-first query examples

- `Read resource smallfactory://status`
- `Read resource smallfactory://repo_info`
- `Read resource smallfactory://parts/quantities and show a low-stock table`

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
