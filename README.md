# smallFactory
 
## What is smallFactory
A Git-native toolset for small teams to manage the things they make.

- Design
  - Manage part/assembly metadata, drawings/CAD/notes files, BOMs, etc.
- Snapshot
  - 1-click revision control of parts.
- Build
  - Track built parts, serial numbers, test results, photos, etc.
  - Manage inventory.

## Why smallFactory

- Built for small teams: minimal setup, low overhead, fast workflows.
- Git-native and portable: plain files under Git; diffs, reviews, history. No database, no lock‑in.
- Opinionated, simple standard: Entities, BOMs, Revisions, Inventory, Files in a consistent layout.
- Tools that fit your flow: CLI and lightweight web UI; human/JSON/YAML output; commits locally, pushes if origin exists.
- Extensible by design: readable YAML/JSON so you can script, automate, and integrate.

## Features
- **Entities & metadata** — parts, assemblies, locations; attributes, tags
- **Revisions & releases** — numeric (1, 2, ...), immutable snapshots; released pointer
- **BOM** — alternates, recursion, cycle detection; in-app editor
- **Inventory** — per-location quantities; add/adjust; default location; mobile Quick Adjust (QR scan)
- **Stickers** — QR-only for entities/locations; batch sticker sheets; configurable fields
- **Files workspace** — upload/move/delete; folders; zip/download; revisions snapshot entire entity folder
- **Web UI** — Flask + Tailwind; responsive; search/filter; inline editing; manage BOM/Revisions
- **Vision-assisted intake** — parse invoices; batch-create parts
- **Git-native workflow** — plain files; auto-commits with ::sfid::<...>; optional push to remote
- **CLI + API** — human/JSON/YAML outputs; entities, inventory, BOM, revisions, validate, web

## Quickstart

Get up and running with smallFactory inventory management in a few simple steps:

```sh
# Setup (once)
# Prereqs: Python 3 and Git installed
# Clone the smallFactory core repo
$ git clone https://github.com/yusufm/smallfactory.git smallfactory
$ cd smallfactory

# Optional: create and activate a virtual environment
$ python3 -m venv .venv && source .venv/bin/activate

# Install CLI dependencies
$ python3 -m pip install -r requirements.txt
# Optional: Web UI dependencies (if you plan to run the web app)
$ python3 -m pip install -r web/requirements.txt

# Initialize by cloning the example datarepo
$ python3 sf.py init --github-url git@github.com:yusufm/smallfactory_test_datarepo.git

# Start the web server
$ python3 sf.py web

# Access the web UI
http://127.0.0.1:8080

# Note: All mutating CLI operations automatically create a Git commit (and push if an origin exists).
# Commit messages include machine-readable tokens like ::sfid::<SFID>.
```

---

## What to read next
- [Web UI docs](web/README.md)
- [Core spec](smallfactory/core/v1/SPECIFICATION.md)
- [CLI docs](smallfactory/README.md)
