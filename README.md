# smallFactory

A lightweight, Git-native PLM (Product Lifecycle Management) system built for simplicity, transparency, and extensibility.

---

## 📏 Specification & Versioning

- API: v1.0 (DRAFT). We use Semantic Versioning; breaking changes require a MAJOR bump.
- Authoritative Core API Specification lives at [smallfactory/core/v1/SPECIFICATION.md](smallfactory/core/v1/SPECIFICATION.md).
- All changes must comply with the specification; if not, update the specification and version accordingly.

## ⚡ Quickstart

Get up and running with smallfactory inventory management in a few simple steps:

```sh
# 1. Initialize a new PLM data repository
$ python3 sf.py init

# 2. (Recommended) Create canonical entities for the location and item
$ python3 sf.py entities add l_a1 name="Shelf A1"
$ python3 sf.py entities add p_m3x10 name="M3x10 socket cap screw"

# 3. Add inventory for the item at the location (sfid, location, quantity are required)
$ python3 sf.py inventory add --sfid p_m3x10 --l_sfid l_a1 --quantity 10
 # custom fields like 'notes' are optional
$ python3 sf.py inventory add --sfid p_prop --l_sfid l_b2 --quantity 20 --set notes="High-performance racing prop"

# 4. View your inventory
$ python3 sf.py inventory ls

# 5. View details of a specific item
$ python3 sf.py inventory show p_m3x10

# 6. Adjust inventory when using parts
$ python3 sf.py inventory adjust l_a1 p_m3x10 -2

# 7. Update entity metadata (canonical)
$ python3 sf.py entities set p_m3x10 name "M3x10 SHCS (DIN 912)"

# 8. Check updated inventory status
$ python3 sf.py inventory ls

# Note: All changes are automatically committed to git!
```

---

## 🧠 Philosophy

Every decision in smallfactory is guided by this rule:

> _“If a 1–2 person team finds it confusing or burdensome, it doesn’t belong.”_

We believe powerful tools can be simple — and that PLM data should be understandable, accessible, and controlled by you.

---

## 📐 What is smallfactory?

smallfactory is:

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

smallfactory is designed for global usability with minimal friction. Our approach:

- **Plain Python (≥3.7):** Runs anywhere Python is available—no special environment or package manager required.
- **requirements.txt:** All dependencies are listed in a single, standard file. Install everything with one command: `pip install -r requirements.txt`.
- **YAML for data:** Human-friendly, easy to edit, and readable in any text editor. JSON is supported for machine-readability if needed.
- **Single-file CLI:** The main tool is a single Python script (`sf.py`), runnable directly (`python3 sf.py ...`) or made executable (`./sf`). No build steps or complex install required.
- **Zero infrastructure:** No databases, servers, or cloud dependencies—just files in your Git repo.
- **Optional dev tools:** Linting and testing tools (like `pytest`, `flake8`) are included for contributors, but not required for end users.

This means anyone, anywhere, can get started in seconds—clone, install, run. No virtualenvs or extra setup unless you want them.

---

## 🧱 How It Works

### 1. The `smallfactory` Core Repository (this one)
- Provides the data spec and conventions
- Contains the CLI (`sf`) and programmatic API
- Offers documentation and reference implementations

### 2. Your PLM Data Repository
- A normal Git repo (public or private)
- Initialized with `python3 sf.py init`
- Stores PLM data in a **clearly organized directory structure**, where each major concept (e.g. parts, boms, releases, inventory) lives in its own folder (e.g. `parts`, `boms`, `releases`, `inventory`).

- As new capabilities (like inventory management, procurement, etc.) are added, they are always introduced as new **top-level directories** in the datarepo.

> 📌 You control your PLM data — smallfactory just helps you manage it safely and consistently.

---

## 🔍 What You Can Do

- **Inventory Management**: Add, view, update, and delete inventory items with ID tracking
- **Stock Control**: Adjust inventory quantities for usage and restocking
- **Data Organization**: Store inventory data in human-readable YAML files
- **Version Control**: Track all inventory changes using Git
- **Flexible Fields**: Add custom fields beyond the required id, name, quantity, and location
- **Multiple Output Formats**: View data in human-readable tables, JSON, or YAML formats
- **Git Integration**: Automatic commits with detailed metadata for inventory changes

---



## 📦 Inventory Management

smallFactory lets you track and manage inventory.

### Add Inventory for an Item at a Location

```sh
$ python3 sf.py inventory add --sfid p_m3x10 --l_sfid l_a1 --quantity 100
```
Adds or stages inventory for an existing entity at a specific location. The file is stored under `inventory/<l_*>/<SFID>.yml` and holds operational quantity state (non-canonical).

> **Required fields:** `sfid`, `location` (must start with `l_`), and `quantity` (integer ≥ 0).
> **Canonical metadata:** Item names/attributes live under `entities/<sfid>/entity.yml` and can be set via `sf entities add/set`.


### Adjust Quantity

```sh
$ python3 sf.py inventory adjust l_a1 p_m3x10 -5
```
Increment or decrement the on-hand quantity at a specific location.

### View an Inventory Item

```sh
$ python3 sf.py inventory show p_m3x10
```
Display all fields for a given `sfid`. Use `-F json` or `-F yaml` for machine-readable formats.

### List All Inventory Items

```sh
$ python3 sf.py inventory ls
```
Show a table of all inventory items. Use `-F json` or `-F yaml` for machine-readable formats.

### Delete an Inventory Item

```sh
$ python3 sf.py inventory rm p_m3x10
```
Remove all inventory entries for an `sfid` across all locations. Prompts for confirmation in human mode.

---

See `python3 sf.py --help` for full CLI options and argument details.