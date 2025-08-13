# smallFactory

### What is smallFactory
- smallFactory is a Git-native toolset for small teams to manage their small factories.
- Your product data lives as plain files in a Git repo, with a simple Web UI and CLI on top.
- No database. No lock-in. Everything is portable, reviewable, and versioned.

### Why it clicks
- Powered by Git: secure, reviewable, and versioned.
- One simple model: Entities, BOMs, Revisions, Inventory, Files.
- Optimized design for small teams: low overhead, low friction, and low complexity.

### Key journeys

- Design
  - Manage part/assembly metadata, drawings/CAD/notes files, BOMs, etc.
- Snapshot
  - 1-click revision control of parts.
- Build
  - Track built parts, serial numbers, test results, photos, etc.
  - Manage inventory.

### Why it matters
- Own your data: everything lives in your repo as plain files.
- Large payoffs for small teams: low overhead, low friction, and low complexity.
- Inventory stays fast and practical: scan, count, move by location.

### What to read next
- Start here (non‑technical overview): `docs/START_HERE.md`
- Core spec and details: `smallfactory/core/v1/SPECIFICATION.md`
- Quickstart: see the Quickstart section below

---

## ⚡ Quickstart

Get up and running with smallFactory inventory management in a few simple steps:

```sh
# Setup (once)
# Prereqs: Python 3 and Git installed
# Clone the smallFactory core repo (replace <ORG> with your org/user)
$ git clone https://github.com/yusufm/smallfactory.git smallfactory
$ cd smallfactory
# Optional: create and activate a virtual environment
$ python3 -m venv .venv && source .venv/bin/activate
# Install CLI dependencies
$ python3 -m pip install -r requirements.txt
# Optional: Web UI dependencies (if you plan to run the web app)
$ python3 -m pip install -r web/requirements.txt

# Initialize a new PLM data repository
$ python3 sf.py init
# (optional) Set a default location in sfdatarepo.yml under inventory.default_location

# Create canonical entities for the location and item
$ python3 sf.py entities add l_a1 name="Shelf A1"
$ python3 sf.py entities add p_m3x10 name="M3x10 socket cap screw"

# Post initial on-hand for the item at the location
$ python3 sf.py inventory post --part p_m3x10 --qty-delta 10 --l_sfid l_a1
# If --l_sfid is omitted, uses sfdatarepo.yml: inventory.default_location
# optional: include a reason for traceability
$ python3 sf.py inventory post --part p_prop --qty-delta 20 --l_sfid l_b2 --reason "Initial stock"

# View on-hand inventory (summary)
$ python3 sf.py inventory onhand

# View on-hand for a specific part or location
$ python3 sf.py inventory onhand --part p_m3x10
# or
$ python3 sf.py inventory onhand --l_sfid l_a1

# Adjust inventory when using parts
$ python3 sf.py inventory post --part p_m3x10 --qty-delta -2 --l_sfid l_a1 --reason "Used in build"

# Update entity metadata (canonical)
$ python3 sf.py entities set p_m3x10 name "M3x10 SHCS (DIN 912)"

# Check updated inventory status
$ python3 sf.py inventory onhand

# Note: All mutating CLI operations automatically create a Git commit (and push if an origin exists).
# Commit messages include machine-readable tokens like ::sfid::<SFID>.
```

---

## 🧠 Philosophy

Every decision in smallFactory is guided by this rule:

> _“If a 1–2 person team finds it confusing or burdensome, it doesn’t belong.”_

We believe powerful tools can be simple — and that PLM data should be understandable, accessible, and controlled by you.

---

## 📐 What is smallFactory?

smallFactory is:

### 1. A set of conventions (*the standard*)
A simple, structured way to organize and store PLM data in Git — including parts, BOMs, revisions, and releases. All files are human-readable (e.g. YAML or JSON) and follow a consistent layout.

### 2. A CLI + API (*the coretools*)
A minimal set of tools to safely create, edit, and validate PLM data using the standard format — ensuring data integrity and avoiding manual errors.

### 3. A sync-aware, Git-first workflow
The tooling pulls from and pushes to your Git remote automatically (if connected), so collaborators stay in sync by default.

---

## 🔑 Core Principles

- **🧰 Zero infrastructure**  
  No servers. No databases. Just a Git repo and a CLI tool.

- **🌱 Git-native**  
  All PLM data lives in your Git repo in readable, version-controlled files.

- **🧭 Opinionated conventions**  
  smallFactory defines strict defaults so you don’t have to invent your own workflows or structure.

- **♻️ Backward compatible**  
  Formats and tooling evolve carefully, with minimal breaking changes.

- **⚙️ Extensible and open**  
  Anyone can build their own tools on top of the coretools and data standard.

- **🔄 Sync by default**  
  All operations try to sync with remote data repo as much as necessary. Unless offline, then will sync when connection is restored.

---

## 🚀 Portability & Minimal Setup

smallFactory is designed for global usability with minimal friction. Our approach:

- **Plain Python (≥3.7):** Runs anywhere Python is available—no special environment or package manager required.
- **requirements.txt:** All dependencies are listed in a single, standard file. Install everything with one command: `pip install -r requirements.txt`.
- **YAML for data:** Human-friendly, easy to edit, and readable in any text editor. JSON is supported for machine-readability if needed.
- **Single-file CLI:** The main tool is a single Python script (`sf.py`), runnable directly (`python3 sf.py ...`) or made executable (`./sf`). No build steps or complex install required.
- **Zero infrastructure:** No databases, servers, or cloud dependencies—just files in your Git repo.
- **Optional dev tools:** Linting and testing tools (like `pytest`, `flake8`) are included for contributors, but not required for end users.

This means anyone, anywhere, can get started in seconds—clone, install, run. No virtualenvs or extra setup unless you want them.

---

## 🧱 How It Works

### 1. The smallFactory Core Repository (this one)
- Provides the data spec and conventions
- Contains the CLI (`sf`) and programmatic API
- Offers documentation and reference implementations

### 2. Your PLM Data Repository
- A normal Git repo (public or private)
- Initialized with `python3 sf.py init`
- Stores PLM data in a **clearly organized directory structure**, where each major concept (e.g. parts, boms, releases, inventory) lives in its own folder (e.g. `parts`, `boms`, `releases`, `inventory`).

- As new capabilities (like inventory management, procurement, etc.) are added, they are always introduced as new **top-level directories** in the datarepo.

> 📌 You control your PLM data — smallFactory just helps you manage it safely and consistently.

---

## 🔍 What You Can Do

- **Inventory Management**: Post quantity deltas and view on-hand by part or location
- **Stock Control**: Adjust inventory quantities for usage and restocking
- **Data Organization**: Store PLM data in human-readable files
- **Version Control**: Track all changes using Git
- **Flexible Fields**: Add custom fields to entities; on-hand is computed from journal entries
- **Multiple Output Formats**: Human, JSON, or YAML (`--format` or `SF_FORMAT`)
- **Git Integration**: Automatic commits with detailed metadata for changes

---

## 📦 Inventory Management

smallFactory lets you track and manage inventory.

### Post Inventory for a Part at a Location

```sh
$ python3 sf.py inventory post --part p_m3x10 --qty-delta 100 --l_sfid l_a1
```
Posts an inventory journal entry and updates on-hand totals.

> **Required flags:** `--part`, `--qty-delta`. `--l_sfid` is optional if `inventory.default_location` is set in `sfdatarepo.yml`.
> **Canonical metadata:** Item names/attributes live under `entities/<sfid>/entity.yml` and can be set via `sf entities add/set`.

### Adjust Quantity

```sh
$ python3 sf.py inventory post --part p_m3x10 --qty-delta -5 --l_sfid l_a1
```
Increment or decrement on-hand at a specific location with a signed delta. Use `--reason` for traceability.

### View On-hand for a Part

```sh
$ python3 sf.py inventory onhand --part p_m3x10
```
Show computed on-hand quantity for a part across locations.

### View On-hand Summary

```sh
$ python3 sf.py inventory onhand
```
Show on-hand summary. Use `--format json` or `--format yaml` for machine-readable formats.

> Note: Inventory is modeled as journal entries with computed on-hand totals; there is no `inventory rm` command.

## 🧱 Build Entities (Finished Goods)

Use dedicated subcommands to set build-specific fields with validation.

### Set Serial Number

```sh
$ python3 sf.py entities set b_2024_0001 serialnumber=SN123
```

### Set Built-at Datetime (ISO 8601)

```sh
$ python3 sf.py entities set b_2024_0001 datetime=2024-06-01T12:00:00Z
# also accepted: 2024-06-01T12:00:00+00:00
```

- Validates ISO 8601 format (supports trailing `Z`).
- Supports output formats: `--format human` (default), `--format json`, `--format yaml`.
- Automatically commits changes to Git with required metadata tokens.

---

See `python3 sf.py --help` for full CLI options and argument details.