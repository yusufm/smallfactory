# CLI Documentation

This section documents the smallFactory CLI (`sf`).

Use `python3 sf.py ...` or, if you installed a launcher, `sf ...`.

## Quick Start

- __Initialize a datarepo__

```bash
python3 sf.py init datarepos/my-repo
# or clone and initialize from GitHub
python3 sf.py init --github-url https://github.com/you/your-repo.git --name my-repo
```

- __Start the Web UI__

```bash
python3 sf.py web --port 8080 --host 0.0.0.0 --debug
```

- __Print stickers (batch PDF)__

```bash
python3 sf.py stickers --sfids p_widget,p_case --out labels.pdf --fields=name,rev --size=2x1 --dpi=300 --text-size=24
```

## Global Options and Environment

- **-R, --repo PATH**: override datarepo path (defaults to user config or `SF_REPO`)
- **-F, --format {human,json,yaml}**: output format (default from `SF_FORMAT` or `human`)
- **-q, --quiet**: decrease verbosity (repeatable)
- **-v, --verbose**: increase verbosity (repeatable)
- **--version**: print CLI version

Environment variables:

- **SF_REPO**: default datarepo path
- **SF_FORMAT**: default output format: `human`, `json`, or `yaml`

## Command Overview

- **init** — Initialize a new datarepo or clone an existing one
- **repo** — Repository compatibility and upgrade operations
  - status, upgrade, validate
- **web** — Start the web UI server
- **inventory** — Inventory journal and reports
  - post, onhand, rebuild
- **entities** — Canonical metadata operations
  - add, ls, show, set, retire
  - build: serial, datetime
  - revision: bump, release
  - files: ls, mkdir, rmdir, add, rm, mv
  - events: ls, append, update, tags, link-file
- **bom** — Bill of Materials operations
  - ls, add, rm (remove), set, alt-add, alt-rm
- **stickers** — Generate a PDF of QR labels (batch)

Use `python3 sf.py <command> --help` for built-in help on any command.

---

## init

Initialize a local datarepo directory, or clone from GitHub and scaffold it.

```bash
# Local repo at datarepos/my-repo
python3 sf.py init datarepos/my-repo

# Clone from GitHub, auto-scaffold, and set default
python3 sf.py init --github-url https://github.com/you/your-repo.git --name my-repo
```

Notes:

- Creates or clones the repo, writes `sfdatarepo.yml`, sets it as default in user config, and makes an initial commit.
- Ensures default inventory location `l_inbox` exists and is configured in `sfdatarepo.yml`.

## repo

Inspect compatibility/migration state and apply repository upgrades.

```bash
# Show compat + pending migration status
python3 sf.py repo status

# Preview migration plan only
python3 sf.py repo upgrade --dry-run

# Apply all pending migrations and commit changes
python3 sf.py repo upgrade

# Validate datarepo against PLM SPEC
python3 sf.py repo validate
```

Notes:

- `repo upgrade` always runs post-upgrade validation (normal workflow).

## web

Start the Flask-based web UI. By default this also starts the read-only MCP
server in the same runtime on the same port under `/mcp`, using the same resolved datarepo.

```bash
python3 sf.py web --port 8080 --host 0.0.0.0 --debug
```

- Flags: `--port`, `--host`, `--debug` (auto-reload when MCP integration is disabled)
- MCP is enabled by default:
  - Web UI: `http://<host>:<port>`
  - MCP (streamable HTTP): `http://<host>:<port>/mcp`
- MCP env controls:
  - `SF_WEB_ENABLE_MCP` (default `1`)
  - `SF_MCP_PATH` (default `/mcp`)

Windsurf MCP config example:

```json
{
  "mcpServers": {
    "smallfactory": {
      "serverUrl": "http://127.0.0.1:8080/mcp"
    }
  }
}
```

Default available tools:
- `server_status`
- `repo_info`
- `data_model_guide`
- `entities_search`
- `entity_get`
- `inventory_onhand`
- `parts_inventory_list` (bulk part table; supports `query`, `status_bucket`, `qty_gte/qty_lte`, `sort_by`, `sort_dir`, cursor pagination)
- `bom_resolved`
- `build_events_list`
- `analytics_query`

Compatibility resources (for resource-first MCP clients):
- `mcp://smallfactory`
- `smallfactory://status` (alias: `mcp://smallfactory/status`)
- `smallfactory://repo_info` (alias: `mcp://smallfactory/repo_info`)
- `smallfactory://data_model_guide` (alias: `mcp://smallfactory/data_model_guide`)
- `smallfactory://inventory/summary` (alias: `mcp://smallfactory/inventory_summary`)
- `smallfactory://parts/quantities` (alias: `mcp://smallfactory/parts_quantities`)

## repo validate

Validate the datarepo against the PLM SPEC.

```bash
python3 sf.py repo validate
python3 sf.py repo validate --strict  # non-zero exit on warnings too
python3 sf.py repo validate --no-git  # skip commit metadata checks
python3 sf.py repo validate --no-entities --no-inventory  # only run git checks
python3 sf.py repo validate --git-commits 50  # scan only the last 50 commits
```

Outputs human/json/yaml based on `-F/--format`. With `--strict`, warnings trigger non-zero exit.

Flags:

- `--strict` — exit non-zero on warnings as well as errors.
- `--no-entities` — skip validation of `entities/`.
- `--no-inventory` — skip validation of `inventory/`.
- `--no-git` — skip Git commit metadata checks.
- `--git-commits <N>` — limit number of recent commits scanned for required `::sfid::<SFID>` tokens (`0` = scan all).

## inventory

Inventory is modeled as an append-only journal; on-hand is computed from journals.

### post

Append a journal entry for a part.

```bash
python3 sf.py inventory post \
  --part p_m3x10 \
  --qty-delta +5 \
  --l_sfid l_inbox \
  --reason "cycle count adjustment"
```

- `--part` is required. `--l_sfid` optional; defaults to `inventory.default_location` in `sfdatarepo.yml`.

### onhand

Report on-hand quantities. Filter by part or location.

```bash
# Summary across all parts and locations
python3 sf.py inventory onhand

# For a specific part
python3 sf.py inventory onhand --part p_m3x10

# For a specific location
python3 sf.py inventory onhand --l_sfid l_inbox

# Machine-readable
python3 sf.py -F json inventory onhand --part p_m3x10

# Read-only computation (no cache writes)
python3 sf.py inventory onhand --readonly

# Read-only for a specific part
python3 sf.py inventory onhand --part p_m3x10 --readonly
```

Note: By default, computing on-hand may write cache files. Use `--readonly` to prevent any writes (pure read).

### rebuild

Rebuild on-hand caches from the journal (idempotent).

```bash
python3 sf.py inventory rebuild
```

## entities

Canonical metadata operations for entities (parts, builds, locations, etc.).

### add

```bash
python3 sf.py entities add p_widget name="Widget" uom=ea
```

### ls

```bash
python3 sf.py entities ls
```

### show

```bash
python3 sf.py entities show p_widget
```

### set

Update fields on an entity.

```bash
# General fields
python3 sf.py entities set p_widget description="A handy widget" category=fastener

# Build metadata
python3 sf.py entities set b_2024_0001 serialnumber=SN12345 datetime=2024-06-01T12:00:00Z
```

### retire

```bash
python3 sf.py entities retire p_widget --reason "obsolete"
```

### build

Set build-specific fields.

```bash
python3 sf.py entities build serial b_2024_0001 SN12345
python3 sf.py entities build datetime b_2024_0001 2024-06-01T12:00:00Z
```

### revision

Manage part revisions (PLM SPEC-compliant). `bump` cuts and immediately releases a revision (default: next numeric label); `release` releases a specific label and flips the `released` pointer.

```bash
# Create and release next revision with optional notes
python3 sf.py entities revision bump p_widget --notes "Initial release"

# Create and release a specific custom label
python3 sf.py entities revision bump p_widget --rev A01 --notes "Alpha release"

# Release a specific revision label
python3 sf.py entities revision release p_widget 1 --notes "Hotfix"
```
Additional timing examples:

```bash
# Bump and set a specific released-at timestamp
python3 sf.py entities revision bump p_widget --released-at 2024-06-01T09:00:00Z --notes "Production cutover"

# Release label 2 with an explicit timestamp
python3 sf.py entities revision release p_widget 2 --released-at 2024-06-15T17:30:00Z --notes "ECN-42"
```

### files

Manage working files under an entity's `files/` folder.

```bash
# List
python3 sf.py entities files ls p_widget --path drawings --recursive --glob "**/*.pdf"

# Create/remove folders
python3 sf.py entities files mkdir p_widget drawings
python3 sf.py entities files rmdir p_widget drawings

# Upload/delete/move files
python3 sf.py entities files add p_widget ./local/file.pdf drawings/file.pdf --overwrite
python3 sf.py entities files rm p_widget drawings/file.pdf
python3 sf.py entities files mv p_widget drawings/file.pdf drawings/file_rename.pdf --overwrite

# Move a directory
python3 sf.py entities files mv p_widget drawings drawings_v2 --dir --overwrite
```

### events

Manage build events for `b_*` entities.

```bash
# List events
python3 sf.py entities events ls b_2024_0001

# Append an event with tags
python3 sf.py entities events append b_2024_0001 --message "Bench test failed" --tags repair,task_open

# Append and link existing files/ paths
python3 sf.py entities events append b_2024_0001 --message "Added evidence" \
  --file "event_attachments/evt_demo/log.txt" \
  --file "event_attachments/evt_demo/scope.png"

# Append and upload local files in one command
python3 sf.py entities events append b_2024_0001 --message "Created with uploads" \
  --upload ./captures/log.txt \
  --upload ./captures/scope.png

# Update fields on an event
python3 sf.py entities events update b_2024_0001 evt_20260227193308 --message "Reworked and retested"

# Replace tags on an event
python3 sf.py entities events tags b_2024_0001 evt_20260227193308 --tags repair,measurement

# Link a files/ path to an existing event
python3 sf.py entities events link-file b_2024_0001 evt_20260227193308 "event_attachments/evt_20260227193308/photo.png"
```

Notes:
- Event storage is `entities/<b_sfid>/events.jsonl` (JSON Lines).
- Allowed event fields are fixed: `id`, `ts`, `tags`, `message`, `files`.

## bom (Bill of Materials)

Operate on a parent part's BOM.

### ls

```bash
# Full tree
python3 sf.py bom ls p_widget

# Limit recursion depth
python3 sf.py bom ls p_widget --max-depth 1
```

### add

```bash
python3 sf.py bom add p_widget --use p_screw --qty 4 --rev released \
  --index 0 --alt p_screw_alt --alternates-group screws
```

Notes:

- Use `--no-check-exists` on `bom add` to skip verifying that the child and alternates exist (advanced).

### rm (remove)

```bash
# By index
python3 sf.py bom rm p_widget --index 0

# By child SFID (first match)
python3 sf.py bom rm p_widget --use p_screw

# Remove all matching uses
python3 sf.py bom rm p_widget --use p_screw --all
```

### set

```bash
python3 sf.py bom set p_widget --index 0 --qty 2 --rev released --alternates-group screws
```

Notes:

- `bom set` also supports `--no-check-exists` to bypass child existence checks when changing `--use`.

### alt-add / alt-rm

```bash
python3 sf.py bom alt-add p_widget --index 0 --use p_screw_alt
python3 sf.py bom alt-rm p_widget --index 0 --alt-index 0
# or by alternate SFID
python3 sf.py bom alt-rm p_widget --index 0 --alt-use p_screw_alt
```

Notes:

- `bom alt-add` supports `--no-check-exists` to skip verifying the alternate exists.

## stickers

Generate a multi-page PDF, one sticker per page, with QR codes and optional text fields.

Dependencies: `qrcode[pil]`, `pillow`, `reportlab`.

```bash
# Provide SFIDs directly
python3 sf.py stickers --sfids p_widget,p_case --out labels.pdf --fields=name,rev --size=2x1 --dpi=300 --text-size=24

# From a file (one-per-line or comma-separated)
python3 sf.py stickers --file sfids.txt --out labels.pdf

# From stdin
cat sfids.txt | python3 sf.py stickers --sfids - --out labels.pdf
```

Options (same for `stickers` and `stickers batch`):

- `--sfids` Comma/newline separated SFIDs (use `-` to read from stdin)
- `--file` File containing SFIDs
- `--fields` Extra fields to print (comma-separated), in addition to name/SFID
- `--size` Sticker size in inches `WIDTHxHEIGHT` (default `2x1`)
- `--dpi` Dots per inch (default `300`)
- `--text-size` Base text size in px (default `24`)
- `-o, --out` Output PDF filename (default `stickers.pdf`)

---

## Formatting and Output

- Use `-F json` or `-F yaml` to get machine-readable outputs.
- Human mode prints friendly summaries; some commands print YAML by default.

## Repo and Location Notes

- To target a specific datarepo, use `-R /path/to/repo` or set `SF_REPO`.
- Default inventory location is configured in `sfdatarepo.yml` under `inventory.default_location` (scaffolded as `l_inbox`).
