# smallFactory

### What is smallFactory
A Git-native toolset for small teams to manage the things they make.

- Design
  - Manage part/assembly metadata, drawings/CAD/notes files, BOMs, etc.
- Snapshot
  - 1-click revision control of parts.
- Build
  - Track built parts, serial numbers, test results, photos, etc.
  - Manage inventory.

### Why smallFactory

- Built for small teams: minimal setup, low overhead, fast workflows.
- Git-native and portable: plain files under Git; diffs, reviews, history. No database, no lock‑in.
- Opinionated, simple standard: Entities, BOMs, Revisions, Inventory, Files in a consistent layout.
- Tools that fit your flow: CLI and lightweight web UI; human/JSON/YAML output; commits locally, pushes if origin exists.
- Extensible by design: readable YAML/JSON so you can script, automate, and integrate.


## ⚡ Quickstart

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

### What to read next
- Web UI docs: `smallfactory/web/README.md`
- Core spec: `smallfactory/core/v1/SPECIFICATION.md`
- CLI docs: `smallfactory/README.md`

---

### 📐 What is smallFactory?

#### 1. A set of conventions (*the standard*)
A simple, structured way to organize and store PLM data in Git — including parts, BOMs, revisions, and releases. All files are human-readable (e.g. YAML or JSON) and follow a consistent layout.

#### 2. A CLI + API (*the coretools*)
A minimal set of tools to safely create, edit, and validate PLM data using the standard format — ensuring data integrity and avoiding manual errors.

#### 3. A sync-aware, Git-first workflow
The tooling pulls from and pushes to your Git remote automatically (if connected), so collaborators stay in sync by default.

---

## 🧱 How It Works

#### 1. The smallFactory Core Repository (this one)
- Provides the data spec and conventions
- Contains the CLI (`sf`) and programmatic API
- Offers documentation and reference implementations

#### 2. Your PLM Data Repository
- A normal Git repo (public or private)
- Initialized with `python3 sf.py init`
- Stores PLM data in a **clearly organized directory structure**, where each major concept (e.g. parts, boms, releases, inventory) lives in its own folder (e.g. `parts`, `boms`, `releases`, `inventory`).

> 📌 You control your PLM data — smallFactory just helps you manage it safely and consistently.
