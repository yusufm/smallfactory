# smallfactory

A lightweight, Git-native PLM (Product Lifecycle Management) system built for simplicity, transparency, and extensibility.

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
  Smallfactory defines strict defaults so you don’t have to invent your own workflows or structure.

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
- **Single-file CLI:** The main tool is a single Python script (`sf.py`), runnable directly (`python sf.py ...`) or made executable (`./sf`). No build steps or complex install required.
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
- Initialized with `sf init`
- Stores PLM data in a **clearly organized directory structure**, where each major concept (e.g. parts, boms, releases, inventory) lives in its own folder (e.g. `parts`, `boms`, `releases`, `inventory`).

- As new capabilities (like inventory management, procurement, etc.) are added, they are always introduced as new **top-level directories** in the datarepo.

> 📌 You control your PLM data — smallfactory just helps you manage it safely and consistently.

---

## 🔍 What You Can Do

- Define and manage parts with part numbers, revisions, and descriptions
- Build and update BOMs with tracked dependencies
- Record releases tied to specific sets of parts and BOMs
- View and diff changes using Git
- Validate structure and rules (e.g. no unapproved parts in a release)
- Write your own tools or scripts that interact with the data via the API

---

## 🧪 Example Workflow

```sh
$ sf init
$ sf part new motor-001 --rev A --desc "BLDC Motor 2205"
$ sf bom edit quadcopter-001
$ sf release create quadcopter-001-v1.0
$ git commit -am "Release: quadcopter-001 v1.0"
```

---

## 📦 Inventory Management

smallfactory lets you track and manage inventory directly in your datarepo. Each inventory item is stored as a YAML file in the `inventory/` directory.

### Add a New Inventory Item

```sh
$ sf inventory-add sku=mot-001 name="BLDC Motor 2205" quantity=100 location="bin A1"
```
Add a new item. All fields should be specified as key=value pairs. The SKU is used as the filename (e.g. `mot-001.yml`).

### Update a Field on an Inventory Item

```sh
$ sf inventory-update mot-001 quantity 120
```
Update a single field (e.g. `quantity`) for an existing item by SKU.

### Adjust Quantity

```sh
$ sf inventory-adjust mot-001 -5
```
Increment or decrement the quantity by a delta (e.g. -5 for usage, +10 for restock).

### View an Inventory Item

```sh
$ sf inventory-view mot-001
```
Display all fields for a given SKU. Use `--output json` or `--output yaml` for machine-readable formats.

### List All Inventory Items

```sh
$ sf inventory-list
```
Show a table of all inventory items. Use `--output json` or `--output yaml` for machine-readable formats.

### Delete an Inventory Item

```sh
$ sf inventory-delete mot-001
```
Remove an inventory item by SKU. Prompts for confirmation in human mode.

---

See `python sf.py --help` for full CLI options and argument details.