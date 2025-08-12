# smallFactory PLM: Minimal Spec (v0.1)

Status: DRAFT — breaking changes permitted until PROD.

## Goals
- Single, flat `entities/` namespace — no separate parts vs assemblies.
- **Parts** are entities with an optional `bom` (i.e., assemblies) — one schema.
- **No standalone BOM files** — BOM is inferred from `bom`.
- **Revisions** are immutable snapshots inside each part; a `released` pointer selects the current one.
- **Finished goods/SKUs** reference a top part + optional config; serials are created at build time.

---

## Global Identifiers (SFIDs)

The smallFactory ID (`sfid`) is the canonical identifier for every entity.

- Globally unique and never reused (temporal uniqueness).
- Filesystem-safe across Windows/macOS/Linux.
- Authoritative regex (pattern):
  ```regex
  ^(?=.{3,64}$)[a-z]+_[a-z0-9_-]*[a-z0-9]$
  ```
- Prefixes (v0.1): `p_` → part, `l_` → location, `sup_` → supplier.
- Identity is the directory path `entities/<sfid>/`; do not include an `sfid` key in `entity.yml`.
- Do not include a `kind` field; kind is inferred from the `sfid` prefix.
- See Appendix: SFID naming conventions (recommended) for human-friendly patterns.

---

## Repository layout (top-level)
```
entities/                 # canonical source of truth for all entities
finished_goods/           # SKUs and build records (no per-unit data here)
inventory/                # per-part journals and generated on-hand caches
workorders/               # work orders (optional, but recommended)
serials/                  # per-unit records (one file per unit)
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

• **`finished_goods/`** — SKUs and build records. Recommended layout:

  • `finished_goods/<sku>/sku.yml` — Defines the top part and optional config; `rev` defaults to `released` if omitted.

  • `finished_goods/<sku>/builds/<date-or-id>/build.lock.yml` — Resolved, reproducible BOM lock captured at build start; referenced by work orders and serials.

• **`inventory/`** — Per‑part journals and derived on‑hand caches:

  • `inventory/p_*/journal.ndjson` — Append‑only quantity deltas by location.

  • `inventory/p_*/onhand.generated.yml` — Derived on‑hand totals by location (tooling writes/updates).

• **`workorders/`** — One directory per work order, at minimum containing `order.yml` (qty/site/status, etc.).

• **`serials/`** — One file per built unit at `serials/<sku>/<year>/<ULID>.yml`, recording status and events over the unit’s lifecycle.

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
- Recognized prefixes (v0.1): `p_` → part, `l_` → location, `sup_` → supplier. More may be added later.
- If a `kind` field appears, the linter errors; kinds are prefix-inferred only.
- For parts (explicit or inferred), `uom` is optional and defaults to 'ea'. Only parts may define `bom`, `files/`, `revisions/`, and `refs/`.
- No legacy aliases: `children` is invalid; only `bom` is accepted.

BOM defaults (to minimize boilerplate):

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

## Finished goods (SKUs)
```
finished_goods/<sku>/
  sku.yml
  builds/
    2025-08-10/
      build.lock.yml      # generated: exact revs used for this build
```

### `sku.yml`
```yaml
top_part: p_toaster
rev: released              # selector; can be explicit label if you want a frozen SKU
config:                    # optional config passed to resolver (used by `when` rules)
  voltage: 120
sku:
  upc: 123456789012
  color: black
  region: US
```

---

## Work orders & serials (where per-unit data lives)
**Work order (optional but recommended):**
```
workorders/workorder-000123/
  order.yml
  build.lock.yml           # copy of the lock used for this work order
```

```yaml
# workorders/workorder-000123/order.yml
workorder: workorder-000123
sku: fg_toaster_black_120v
qty: 3
site: l_sanjose
opened_at: 2025-08-10T19:40:00Z
```

**Serials (canonical, one file per unit):**
```
serials/<sku>/<year>/<ULID>.yml
```
```yaml
serial: 01J9Z9Q6H3J6NRS4K1YV3M8U5K
label: TOAST-25-223-0001
sku: fg_toaster_black_120v
workorder: workorder-000123
lockfile: ../../../../finished_goods/fg_toaster_black_120v/builds/2025-08-10/build.lock.yml
status: built
events:
  - ts: 2025-08-10T20:12:33Z
    action: test
    result: pass
```

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
- For serialized parts, prefer `qty_delta` ∈ {+1, −1} with a `serial` pointer.
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
{{ ... }}
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
**Input:** a path to `finished_goods/<sku>` (and repo state/commit).  
**Output:** a fully resolved BOM with exact part SFIDs and revision labels.

Algorithm (conceptual):
1. Load `sku.yml` → get `top_part`, SKU `rev` selector (e.g., `released`), and `config`.
2. Depth-first walk from `entities/<top_part>/entity.yml`.
3. For each bom line:
   - Evaluate `when` against `config`; skip if it doesn’t match.
   - Determine target revision:
     - If `rev` is a **label** (e.g., "B"), use it.
     - If `rev` is **`released`**, read `entities/<use>/refs/released`. If this file is missing and the part has `policy: buy` with no `revisions/`, treat it as an implicit released snapshot.
   - If the chosen rev does not exist or `status` ≠ `released`:
     - Try `alternates` in order, then `alternates_group` (pick any **released** member).
     - If none valid → error.
4. Accumulate quantities (respecting nested assemblies) and return the resolved tree + a flattened list.
5. Optionally write `build.lock.yml` with all `{use, rev, qty}` (and artifact hashes if desired).

**Note:** There are no separate BOM files. The BOM is the `bom` list present in each part's `entity.yml`.

---

## Commands (minimal surface)
```
sf part revision cut <sfid> <revision> --include exports docs --note "..."
sf part revision release <sfid> <revision>
sf resolve finished_goods/<sku>
sf lock finished_goods/<sku> [--output <path>]
sf serial mint --workorder <workorder> --qty <n>
sf inventory post --part <sfid> --qty-delta <n> [--location <sfid>] [--reason <text>]
sf inventory onhand [--part <sfid>] [--location <sfid>]
sf inventory rebuild
sf lint   # validate schema + referential integrity + allowed fields by kind
```

---

## Conventions & constraints
- `entities/<sfid>/entity.yml` is **required** and must include:
  - Do not include `sfid`. Identity is derived from the directory name, which MUST be a valid SFID and use a recognized prefix (e.g., `p_`, `l_`, `sup_`). The prefix determines the kind.
  - For parts (explicit or inferred), `uom` is optional and defaults to 'ea'.
  - Only parts (explicit or inferred) may define `bom`, `files/`, `revisions/`, and `refs/`.
  - No legacy aliases: the `children` key MUST NOT appear.
  - For `policy: buy` parts, `revisions/` and `refs/` may be omitted; such parts are treated as having an implicit released snapshot.
- Revision directories under `revisions/` are **immutable** once released.
- `refs/released` is the **only pointer** you flip to advance the world.
- Large binaries (`*.step`, `*.stl`, `*.pdf`) should be tracked with **Git LFS**.
- SFIDs MUST be globally unique and never reused; prefixes recommended (e.g., `p_`, `l_`, `sup_`).

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
- Output modes: CLI and API support `human`, `json`, and `yaml` outputs; field shapes are stable within a major version.
- Determinism: Given the same repo state and inputs, operations produce the same results.
- Branding: User-facing name is "smallFactory" (lowercase s, uppercase F).
- Predictable layout: Top-level directories (e.g., `entities/`, `inventory/`, `finished_goods/`, `workorders/`) are stable; new capabilities add new top-level dirs.

- Single source of truth API:
  - All tools and interfaces (CLI, Web, scripts, integrations) MUST call the Core API for all reads and writes.
  - Direct file mutations are not supported; the API performs validation, defaulting, linting, and writes with required commit metadata.

Terminology note: `sfid` refers to the smallFactory identifier for an entity (e.g., `p_...`, `l_...`, `sup_...`). External identifiers keep their native names, e.g., manufacturer part numbers (`mpn`), change record `eco` ID, or supplier-provided IDs.

---

## Ergonomics & Defaults

- Minimal required fields:
  - All entities: omit the `sfid` field; identity is the directory name and must be a valid SFID with a recognized prefix. Do not include a `kind` field.
  - Parts (explicit or inferred): `uom` is optional; default 'ea'.
- Kind inference:
  - Prefixes (v0.1): `p_` → part, `l_` → location, `sup_` → supplier.
- BOM defaults (applied by resolver and validated by linter):
  - `qty` defaults to `1` if omitted.
  - `rev` defaults to `released` if omitted.
  - Omitted `when` means the line is always included.
  - `bom` is only allowed on parts.
- Resolver defaults and constraints:
  - Traverse `bom` only for parts (explicit or inferred).
  - Treat missing `qty` as `1` and missing `rev` as `released`.
  - If the chosen revision is not released, try `alternates` then `alternates_group`; error if none are valid.
  - `policy: buy` parts with no `revisions/` and no `refs/released` are allowed; treat as implicit released. In `build.lock.yml`, record `rev: implicit` for such parts.
- Finished goods defaults:
  - In `finished_goods/<sku>/sku.yml`, `rev` defaults to `released` if omitted.
- Linter behavior (friendly but strict):
  - Explain inferred kinds and defaulted fields; error on kind/prefix mismatch, invalid keys, `bom` on non-part, and any legacy keys. Do not error on missing `uom`; default to 'ea' at read time.

---

## Tiny end-to-end example (happy path)
1) Edit CAD for **p_adapter**, export into `files/`, commit.
2) `sf part revision cut p_adapter B && sf part revision release p_adapter B`
   - Only `entities/p_adapter/refs/released` changes to `"B"`.
3) `sf resolve finished_goods/fg_toaster_black_120v`
   - Uses `rev: released` pointers; no product files edited.
4) `sf lock finished_goods/fg_toaster_black_120v`
   - Produces a reproducible build recipe; work orders and serials reference it.

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
