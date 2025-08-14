# smallFactory Core v1 Specification

Status: DRAFT — breaking changes permitted until PROD.

## Goals
- Single, flat `entities/` namespace — no separate parts vs assemblies.
- **Parts** are entities with an optional `bom` (i.e., assemblies) — one schema.
- **No standalone BOM files** — BOM is inferred from `bom`.
- **Revisions** are immutable snapshots inside each part; a `released` pointer selects the current one.
- **Finished goods** are Build entities of a top part + optional config; per-unit serials are recorded within the Build.

---

## Global Identifiers (SFIDs)

The smallFactory ID (`sfid`) is the canonical identifier for every entity.

- Globally unique and never reused (temporal uniqueness).
- Filesystem-safe across Windows/macOS/Linux.
- Authoritative regex (pattern):
  ```regex
  ^(?=.{3,64}$)[a-z]+_[a-z0-9_-]*[a-z0-9]$
  ```
- Prefixes (v0.1): `p_` → part, `l_` → location, `b_` → build.
- Identity is the directory path `entities/<sfid>/`; do not include an `sfid` key in `entity.yml`.
- Do not include a `kind` field; kind is inferred from the `sfid` prefix.
- See Appendix: SFID naming conventions (recommended) for human-friendly patterns.

---

## Repository layout (top-level)
```
entities/                 # canonical source of truth for all entities
inventory/                # per-part journals and generated on-hand caches
workorders/               # work orders (optional, but recommended)
```

---

## Entity directories (flat, one per entity)
```
entities/<sfid>/
  entity.yml              # required; schema below
  files/                  # optional; working files area (copied into snapshots)
  revisions/              # optional; immutable snapshots by rev label
    1/
      meta.yml            # required for a snapshot
    2/
      ...
  refs/
    released              # text file containing the current rev label (e.g., "2")
```

### Directory intentions (what goes where)

• **`files/`** — Working area for in‑progress files (e.g., CAD and documentation). Included in snapshots when you cut a revision. There is no prescribed substructure under `files/`; organize as needed.

• **`revisions/<rev>/`** — Immutable snapshot for a specific revision label. Treat contents as read‑only once created (and especially once released).

  • **`revisions/<rev>/meta.yml`** — Snapshot metadata (rev, status, source commit, notes, artifact list, hashes, etc.).

• **`refs/`** — Small text pointers that select important revisions (e.g., `refs/released` contains the current rev label). Tooling updates these; avoid manual edits.

Top‑level directories (recap):
 
 • **`entities/`** — Canonical home of all entities; one directory per SFID.

 
 • **`inventory/`** — Per‑part journals and derived on‑hand caches:

  • `inventory/p_*/journal.ndjson` — Append‑only quantity deltas by location.

  • `inventory/p_*/onhand.generated.yml` — Derived on‑hand totals by location (tooling writes/updates).

• **`workorders/`** — One directory per work order, at minimum containing `order.yml` (qty/site/status, etc.).

 

## Layered Architecture: Core as Single Source of Truth

- The `smallfactory/core/v1/` package is the canonical source of domain logic and data access. It defines the public API for all PLM operations.
- All non-core layers (CLI, Web, integrations, scripts) MUST only call core APIs.
- Non-core layers MUST NOT:
  - Read or write repository files or directories directly (e.g., do not open `entities/<sfid>/refs/released` directly).
  - Re-implement domain logic (e.g., BOM traversal, revision snapshotting, release handling, validation).
  - Depend on core internals or private helpers (names prefixed with `_`); only public core functions are allowed.

### Required Core APIs (examples)

- Revisions (in `smallfactory/core/v1/entities.py`):
  - `cut_revision(datarepo_path, sfid, rev=None, *, notes=None)` MUST be used to create snapshots.
  - `bump_revision(...)`, `release_revision(...)`, `get_revisions(...)` MUST be used for revision flows.
- BOM (in `smallfactory/core/v1/entities.py`):
  - `resolved_bom_tree(datarepo_path, root_sfid, *, max_depth=None)` MUST be used to obtain resolved BOM nodes.
  - BOM CRUD MUST use: `bom_list(...)`, `bom_add_line(...)`, `bom_set_line(...)`, `bom_remove_line(...)`, `bom_alt_add(...)`, `bom_alt_remove(...)`.
- Inventory (in `smallfactory/core/v1/inventory.py`):
  - On-hand and journal operations MUST use the inventory core APIs.
- Files (in `smallfactory/core/v1/files.py`):
  - Design-area file operations MUST use the files core APIs.

### Resolved BOM Tree (normative)

- Consumers MUST obtain a resolved BOM tree via `resolved_bom_tree(...)`.
- The returned node schema is the contract and MUST be treated as read-only by callers:
  - `parent, use, name, qty, rev_spec, rev, level, is_alt, alternates_group, cumulative_qty, cycle`.
- UI layers MAY enrich this with presentation-only data (e.g., on-hand totals, formatting), but MUST NOT alter resolution logic.

### Revisions (normative)

- Snapshot generation (copying entity contents, artifact hashing, BOM tree snapshot `bom_tree.yml`) is implemented in core.
- Callers MUST NOT replicate snapshot logic or mutate revision directories directly.

### Versioning and Compatibility

- Core API is versioned under `core/v1`. CLI/Web MUST target the same major version.
- Any breaking change requires a new `core/vX/` and corresponding UI updates.

### Forbidden Patterns (non-exhaustive)

- Reading `entities/<sfid>/refs/released` directly in CLI/Web.
- Implementing custom BOM recursion in CLI/Web.
- Accessing private helpers (names prefixed with `_`) from outside core.

### Build entities (`b_*`)

Builds are first-class entities represented under `entities/b_*/`. A Build captures a specific batch or run that produces finished units for a given top part (optionally parameterized by config).

Example `entities/b_2025_0001/entity.yml`:
```yaml
top_part: p_toaster_assembly       # required top-level part being built
config:                            # optional config passed to resolver (used by `when` rules)
  voltage: 120
qty_planned: 100                   # optional
qty_completed: 20                  # optional; tooling may derive/update
site: l_line1                      # optional production line/location
workorder: workorder-000123        # optional external WO reference
status: open                       # open|in_progress|completed|canceled
opened_at: 2025-08-10T19:40:00Z    # optional timestamps
closed_at: 2025-08-11T02:12:00Z
notes: "First pilot run on new fixture"

# Per-unit tracking lives here; no top-level serials/ directory
units:
  - serial: 01J9Z9Q6H3J6NRS4K1YV3M8U5K   # ULID or OEM serial; unique per unit
    label: TOAST-25-223-0001             # optional human label
    status: built                        # built|shipped|scrapped|reworked|...
    events:
      - ts: 2025-08-10T20:12:33Z
        action: test
        result: pass
```

Notes:
 - Use `::sfid::<b_...>` in commit messages when a Build is created or updated.

### `entity.yml` (all entities; parts may be explicit or inferred)
```yaml
uom: ea                    # optional; defaults to 'ea' if omitted
policy: make               # optional (make|buy|phantom)
attrs:                     # free-form attributes (string|number|bool|array|object)
  voltage: [120, 240]

# Only for parts that are assemblies (dynamic BOM)
bom:
  - use: p_adapter
    qty: 1
    rev: released          # selector or explicit label (e.g., "B")
  - use: p_motor
    qty: 1
    rev: released
    when:                  # optional config gate: all conditions must match
      voltage: 120
    alternates:            # optional explicit alternates (evaluated if primary unavailable)
      - use: p_motor_alt
        rev: released
    alternates_group: ISO_M3x10  # optional family/group-based alternates
```

Note on kind inference and validation:

- Do not include a `kind` field; tooling infers kind from the `sfid` prefix.
- Recognized prefixes (v0.1): `p_` → part, `l_` → location, `b_` → build. More may be added later.
- If a `kind` field appears, the linter errors; kinds are prefix-inferred only.
- For parts (explicit or inferred), `uom` is optional and defaults to 'ea'. Only parts may define `bom`, `files/`, `revisions/`, and `refs/`.
- No legacy aliases: `children` is invalid; only `bom` is accepted.

### BOM invariants

- Within a part's `bom`, each `use` SFID MUST be unique. Do not include duplicate lines for the same child; express multiples via a higher `qty` on a single line.

### BOM defaults (to minimize boilerplate)

- On each `bom` line:
  - `qty` should not be omitted; default is `1`.
  - `rev` should not be omitted; default is `released`.
  - Omitted `when` means the line is always included.
  - `bom` is only allowed on parts.

### Minimal purchased part (no revisions)
Buy parts can be very sparse. If a part has `policy: buy` and there is no `revisions/` directory and no `refs/released`, the resolver treats it as having an implicit released snapshot.

```
entities/p_cap_10uF/
  entity.yml
```

```yaml
# entities/p_cap_10uF/entity.yml
uom: ea
policy: buy
attrs:
  mpn: ABC-123
  voltage: 10V
  tolerance: 10%
```

Notes:

- No `revisions/`, `refs/`, or `files/` are required for such parts.
- When used in a BOM with `rev: released` (or when `rev` is omitted), the resolver will accept the implicit released snapshot.

### Revision snapshot (`revisions/<rev>/meta.yml`)
```yaml
rev: B
status: released           # draft|released|obsolete (suggested)
eco: ECO-0012              # optional change record ID
source_commit: 3c2a0f4     # git SHA that produced this snapshot
generated_at: 2025-08-10T19:40:00Z
notes: "Slots +2mm; tolerance update."
artifacts:                 # files relative to this snapshot dir
  - role: cad-export
    path: adapter.step
    sha256: 1a7f...59
  - role: drawing
    path: adapter.pdf
    sha256: 4c5e...aa
```

### Released pointer (`refs/released`)
A single-line text file containing the current revision label, e.g.:
```
2
```

---

 

## Work orders & per‑unit tracking (via builds)
**Work order (optional but recommended):**
```
workorders/workorder-000123/
  order.yml
```

```yaml
# workorders/workorder-000123/order.yml
workorder: workorder-000123
sku: fg_toaster_black_120v
qty: 3
site: l_sanjose
opened_at: 2025-08-10T19:40:00Z
```
Per‑unit records (serials, events) are captured under the associated Build’s `entities/b_*/entity.yml` in the `units` list.

---

## Inventory (MVP)
 
 SFIDs: See "Global Identifiers (SFIDs)" for the authoritative regex, prefixes, and invariants. For human-friendly patterns, see Appendix: SFID naming conventions (recommended).
 
  Layout:
  ```
  inventory/
  <sfid>/
    journal.ndjson           # append-only; one JSON object per line
    onhand.generated.yml     # optional per-part cache; do not hand-edit
```

Journal entry format (NDJSON; one JSON object per line):
```
{"txn":"01J9Z6T9S2B3HQX5WAM4R2F3G6","location":"l_inbox","qty_delta":200,"reason":"receipt"}
{"txn":"01J9Z6Y9M8K7C1P2D3F4H5J6K7","location":"l_line1","qty_delta":-16,"reason":"issue"}
```

Notes:

- Time derives from the ULID embedded in `txn`; journal entries MUST NOT include a separate `ts` field. Backdating is not supported.
- Quantities in journals are always interpreted in the part’s base `uom`; journal entries MUST NOT include a `uom` field.
- Format is NDJSON (JSON Lines) for safe, line-wise appends and union merges.
- File identity is the path: `inventory/<sfid>/`. Do not repeat the part SFID inside entries.
- Use SFIDs for `location`. No `sfid` or `kind` fields inside inventory entries.
- Writes are O(1) appends; tooling updates `onhand.generated.yml` for that part.
- Global on-hand is the sum over per-part caches.

 Defaults and minimal entry (tooling fills):

 - Minimal accepted fields at write time: `qty_delta`.
 - Tooling fills if omitted:
   - `txn`: generated ULID (idempotency; ULID time is authoritative)
   - `location`: from `sfdatarepo.yml: inventory.default_location` if present

 Minimal input vs. stored example:
 ```
 # user input (conceptual)
 {"qty_delta": 5}

 # stored after tooling fills defaults
 {"txn":"01J9ZCD...","location":"l_inbox","qty_delta":5}
 ```

 Optional repo config (for defaults):
 ```yaml
 # sfdatarepo.yml
 inventory:
   default_location: l_inbox
 ```

Git merge hint (reduce conflicts on append-only logs):
```
inventory/p_*/journal.ndjson merge=union
```

CLI (full names):
```
sf inventory post --part <sfid> --qty-delta <n> [--location <sfid>] [--reason <text>]
sf inventory onhand [--part <sfid>] [--location <sfid>]
sf inventory rebuild
```

Linter rules:

- Validate that `part` (derived from path) and `location` SFIDs exist in `entities/`.
- Journal entries MUST NOT include `uom`; quantities are interpreted in the part’s base `uom`.
- For unitized flows, prefer `qty_delta` ∈ {+1, −1}.
- Generated files (`onhand.generated.yml`) must not be hand-edited.
 Optional per-location on-hand cache (reverse index):
 
 - Layout:
   - `inventory/_location/<location_sfid>/onhand.generated.yml`
  - Example:
    ```yaml
    # inventory/_location/l_inbox/onhand.generated.yml
    uom: ea
    as_of: 2025-08-10T21:15:00Z
    parts:
      p_cap_10uf: 184
      p_res_1k: 500
    total: 684
    ```
  - Behavior:
    - On each `sf inventory post`, tooling updates both:
      - `inventory/<part_sfid>/onhand.generated.yml` (by_location, total)
      - `inventory/_location/<location_sfid>/onhand.generated.yml` (parts, total)
    - `sf inventory rebuild` regenerates per-part caches from journals, then per-location caches from per-part caches.
    - The per-location cache includes only parts with nonzero on-hand at that location (omit zeros).
    - Both caches are updated in the same commit as the journal append; commit messages include `::sfid::<PART_SFID>` and `::sfid::<LOCATION_SFID>`.
    - `as_of` timestamps reflect the authoritative time derived from the journal entry’s ULID for posts; for rebuilds, `as_of` is the rebuild time.
    - Do not hand-edit generated files.

Appendix: .gitattributes (recommended)
```
# Append-only inventory journals: prefer union merges to reduce conflicts
inventory/p_*/journal.ndjson merge=union
```

Appendix: onhand.generated.yml (example)
```yaml
# inventory/<sfid>/onhand.generated.yml
uom: ea
as_of: 2025-08-10T21:15:00Z
by_location:
  l_inbox: 184
  l_shelf: 0
total: 184
```
- Structure is minimal: a single-unit-of-measure per part, map by `location` SFID, and an overall `total`.
- This file is derived; tooling updates it on each post and during `sf inventory rebuild`.

---

## Resolver behavior (deterministic)
**Input:** a top part SFID (and optional config/rev selector), plus repo state/commit.  
**Output:** a fully resolved BOM with exact part SFIDs and revision labels.

Algorithm (conceptual):
1. Depth-first walk from `entities/<top_part>/entity.yml`.
   - Use provided `rev` selector or default to `released`.
   - Apply provided `config` to evaluate `when` rules.
   - If the current part has `policy: phantom`, treat it as pass-through: do not include the phantom part itself in the flattened result and do not consider on-hand for it; traverse into its `bom` and accumulate child quantities into the parent.
3. For each bom line:
   - Evaluate `when` against `config`; skip if it doesn’t match.
   - Determine target revision:
     - If `rev` is a **label** (e.g., "B"), use it.
     - If `rev` is **`released`**, read `entities/<use>/refs/released`. If this file is missing and the part has `policy: buy` with no `revisions/`, treat it as an implicit released snapshot.
   - If the chosen rev does not exist or `status` ≠ `released`:
     - Try `alternates` in order, then `alternates_group` (pick any **released** member).
     - If none valid → error.
4. Accumulate quantities (respecting nested assemblies) and return the resolved tree + a flattened list.
5. Return the resolved result; downstream systems may persist their own representations if desired.

**Note:** There are no separate BOM files. The BOM is the `bom` list present in each part's `entity.yml`.

---

## Commands (minimal surface)
```
sf part revision cut <sfid> <revision> --include exports docs --note "..."
sf part revision release <sfid> <revision>
sf resolve <top_part> [--rev <selector|label>] [--config <kv|yaml>]
sf build units mint <b_sfid> --qty <n>
sf inventory post --part <sfid> --qty-delta <n> [--location <sfid>] [--reason <text>]
sf inventory onhand [--part <sfid>] [--location <sfid>]
sf inventory rebuild
sf lint   # validate schema + referential integrity + allowed fields by kind
 
# Build lifecycle (minimal):
sf build create <b_sfid> --top-part <p_sfid> [--rev <selector|label>] [--config <kv|yaml>] [--qty-planned <n>] [--site <l_sfid>] [--workorder <id>]
sf build update <b_sfid> [--status <open|in_progress|completed|canceled>] [--qty-completed <n>]
```

---

## Conventions & constraints
- `entities/<sfid>/entity.yml` is **required** and must include:
  - Do not include `sfid`. Identity is derived from the directory name, which MUST be a valid SFID and use a recognized prefix (e.g., `p_`, `l_`, `b_`). The prefix determines the kind.
  - For parts (explicit or inferred), `uom` is optional and defaults to 'ea'.
  - Only parts (explicit or inferred) may define `bom`, `files/`, `revisions/`, and `refs/`.
  - No legacy aliases: the `children` key MUST NOT appear.
  - For `policy: buy` parts, `revisions/` and `refs/` may be omitted; such parts are treated as having an implicit released snapshot.
- Revision directories under `revisions/` are **immutable** once released.
- `refs/released` is the **only pointer** you flip to advance the world.
- Large binaries (`*.step`, `*.stl`, `*.pdf`) should be tracked with **Git LFS**.
- SFIDs MUST be globally unique and never reused; prefixes recommended (e.g., `p_`, `l_`, `b_`).

- Auto-commit history:
  - All mutating operations auto-commit with clear messages including the required `::sfid::` tokens.

- Entity lifecycle:
  - Each `entities/<sfid>/` directory persists forever (even if retired). Prefer marking `status: retired` over deletion.
- Human-readable data formats:
  - YAML is the primary storage format; JSON is supported for machine I/O.
- Git-native and file-based:
  - The Git repository is the source of truth; history serves as the audit trail.

- Commit metadata tokens:
  - Commits that affect an entity MUST include `::sfid::<SFID>` in the message.
  - For inventory posts, include both tokens: `::sfid::<PART_SFID>` and `::sfid::<LOCATION_SFID>`.
  - For builds, include `::sfid::<BUILD_SFID>` (e.g., `::sfid::b_2025_0001`).
- Output modes: CLI and API support `human`, `json`, and `yaml` outputs; field shapes are stable within a major version.
- Determinism: Given the same repo state and inputs, operations produce the same results.
- Branding: User-facing name is "smallFactory" (lowercase s, uppercase F).
- Predictable layout: Top-level directories (e.g., `entities/`, `inventory/`, `workorders/`) are stable; new capabilities add new top-level dirs.

- Single source of truth API:
  - All tools and interfaces (CLI, Web, scripts, integrations) MUST call the Core API for all reads and writes.
  - Direct file mutations are not supported; the API performs validation, defaulting, linting, and writes with required commit metadata.

Terminology note: `sfid` refers to the smallFactory identifier for an entity (e.g., `p_...`, `l_...`, `b_...`). External identifiers keep their native names, e.g., manufacturer part numbers (`mpn`) or change record `eco` IDs.

---

## Ergonomics & Defaults

- Minimal required fields:
  - All entities: omit the `sfid` field; identity is the directory name and must be a valid SFID with a recognized prefix. Do not include a `kind` field.
  - Parts (explicit or inferred): `uom` is optional and defaults to 'ea'.
- Kind inference:
  - Prefixes (v0.1): `p_` → part, `l_` → location, `b_` → build.
- BOM defaults (applied by resolver and validated by linter):
  - `qty` defaults to `1` if omitted.
  - `rev` defaults to `released` if omitted.
  - Omitted `when` means the line is always included.
  - `bom` is only allowed on parts.
- Resolver defaults and constraints:
  - Traverse `bom` only for parts (explicit or inferred).
  - Treat missing `qty` as `1` and missing `rev` as `released`.
  - If the chosen revision is not released, try `alternates` then `alternates_group`; error if none are valid.
  - `policy: buy` parts with no `revisions/` and no `refs/released` are allowed; treat as implicit released.
- Linter behavior (friendly but strict):
  - Explain inferred kinds and defaulted fields; error on kind/prefix mismatch, invalid keys, `bom` on non-part, and any legacy keys. Do not error on missing `uom`; default to 'ea' at read time.

---

## Tiny end-to-end example (happy path)
1) Edit CAD for **p_adapter**, export into `files/`, commit.
2) `sf part revision cut p_adapter B && sf part revision release p_adapter B`
   - Only `entities/p_adapter/refs/released` changes to `"B"`.
3) `sf resolve p_toaster_assembly`
   - Uses `rev: released` pointers; no product files edited.
4) Produce units using the resolved BOM.
  - Work orders and builds reference the resolved state implicitly via repo commit and released pointers.

---

## Scope of Applicability

- Applies to Core v1 under `smallfactory/core/v1/`.
- Governs CLI behavior and Web UI features backed by Core v1.
- Defines repository structure and file formats under this spec.

---

## Versioning Policy (SemVer)

- We use Semantic Versioning: MAJOR.MINOR.PATCH.
  - MAJOR: incompatible changes to the spec or API.
  - MINOR: backward-compatible additions.
  - PATCH: backward-compatible fixes and internal improvements.
- Stability gates: DRAFT → RC → PROD.
  - While DRAFT, breaking changes are permitted.
  - Once PROD, breaking changes require a major version bump.

---

## Change Management Requirements

- Assess every change against this specification.
- If a change modifies or conflicts with this spec:
  - Update this file in the same PR and bump version appropriately.
  - Provide migration notes where feasible.
- PRs should state: "Specification compliant? Yes/No" and link to this file.

---

## Appendix: SFID naming conventions (recommended)

- Purpose: Improve searchability, interchangeability grouping, and lot/serial tracking. These conventions are HIGHLY RECOMMENDED but not required.

- Parts (`p_*`):
  - Structure: `p_<part-number>[ _<classification> ... ]`
  - `<part-number>`: use lowercase letters/digits; `-` may separate subcodes (e.g., `stm32-c`, `m3x10`). Prefer hyphens in the base; reserve `_` for classification separators.
  - Classification order and specificity: classifications SHOULD progress from general → specific left-to-right. Do not reorder once established.
  - Prefix search rule: prefix matches at classification boundaries SHOULD find all more-specific variants. Example: `p_1kr` matches `p_1kr_lot21` and `p_1kr_lot21_sn39402`.
  - Classification charset: within a classification, use `[a-z0-9-]`; do not use `_` inside classifications (only as the classification delimiter).
  - Examples:
    - `p_1kr`
    - `p_1kr_lot21`, `p_1kr_lot52`
    - `p_m3x10`
    - `p_m3x10_lot23`
    - `p_stm32-c_sn39402`, `p_stm32-c_sn59404`
 
 - Builds (`b_*`) — recommended patterns:
   - Purpose: Identify a specific production build/batch.
   - Prefer stable, sortable tokens. Good options include date, sequence, line, and/or SKU.
   - Suggested structures (pick one and keep it consistent):
     - `b_<yyyy>_<ordinal>` e.g., `b_2025_0001`
     - `b_<yyyymmdd>_<line>_<run>` e.g., `b_20250810_line1_run3`
     - `b_<top>_<yyyymmdd>` e.g., `b_p_toaster_assembly_20250810`
   - Charset: `[a-z0-9_-]`; avoid uppercase. Keep tokens general → specific left-to-right.
   - Example references:
     - Commits touching a build MUST include `::sfid::b_...`.

## Optional: `.gitattributes` for LFS
```
# CAD + docs tracked by LFS
*.step filter=lfs diff=lfs merge=lfs -text
*.stl  filter=lfs diff=lfs merge=lfs -text
*.pdf  filter=lfs diff=lfs merge=lfs -text
*.ipt  filter=lfs diff=lfs merge=lfs -text
*.sldprt filter=lfs diff=lfs merge=lfs -text
*.sldasm filter=lfs diff=lfs merge=lfs -text
```

---


---

## Entities by prefix (allowed keys)

Allowed top-level keys in `entities/<sfid>/entity.yml` by inferred kind (from prefix):

- Parts (`p_*`): `uom`, `policy`, `attrs`, `bom`.
- Locations (`l_*`): `attrs` (free-form descriptive metadata).
- Builds (`b_*`): `top_part`, `config`, `qty_planned`, `qty_completed`, `site`, `workorder`, `status`, `opened_at`, `closed_at`, `notes`, `units`.

Notes:

- Do not include `sfid` or `kind`; both are inferred from the directory name.
- Only parts may define `bom`, `files/`, `revisions/`, and `refs/` directories.

---

## Alternates catalog (optional)

An optional, explicit registry of alternates groups may be maintained for deterministic selection when `alternates_group` is specified in a BOM line.

- Layout:
  - `catalog/alternates/<group>.yml`
- Example:
  ```yaml
  # catalog/alternates/ISO_M3x10.yml
  group: ISO_M3x10
  members:
    - p_m3x10_zinc
    - p_m3x10_ss
    - p_m3x10_blackoxide
  ```
- Resolver behavior with `alternates_group`:
  - Consider `members` in listed order and select the first member whose `refs/released` exists and is valid; otherwise error.
  - If the catalog file is missing, the resolver will error when an `alternates_group` is encountered.

