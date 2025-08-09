# smallFactory Core API Specification (v1.0 — DRAFT)

Status: DRAFT (changes permitted until PROD)

This document defines the Core Specification (the unbreakable tenets/conventions) for the smallFactory Core API v1.0 and its data model. All changes MUST comply with this specification. If a change would violate this specification, either:
- Do not make the change, or
- Update this specification consciously and bump the API MAJOR version per Semantic Versioning.

---

## Core Philosophies

- Simplicity first for 1–4 person teams.
  If it’s confusing or burdensome, it doesn’t belong.

- Git-native and file-based.
  All data is plain files in a Git repo; no hidden state, no required server/database.

- Human-readable data formats.
  YAML is the primary storage format; JSON is supported for machine I/O. Outputs may be human, YAML, or JSON.

- Single source of truth API.
  All tools and interfaces (CLI, Web, scripts, integrations) MUST call the Core API for all reads and writes to ensure that this specification is maintained.

- Backward compatibility by default.
  Within a major version, changes are additive and non-breaking. Breaking changes require a major version bump.

- Stable identifiers.
  Every entity MUST have a globally unique, never-reused `sfid` (smallFactory ID).

- Transparent Git history.
  All mutating operations auto-commit with clear messages and metadata (including `::sfid::<SFID>` for entity-affecting changes).

- Deterministic behavior.
  Given the same inputs and repository state, operations produce the same results.

- Consistent UX contract.
  Supported output modes (`human`, `json`, `yaml`) and required fields (`sfid`, `name`, `quantity`, `location`) are stable within the major version.

- Predictable repository layout.
  Top-level directories (e.g., `inventory/`, future: `prototypes/`, `finished_goods/`) are stable. New capabilities are added as new top-level dirs, not by reshuffling existing ones.

- Branding consistency.
  User-facing name is "smallFactory" (lowercase "s", uppercase "F").

## Technical Specifications

### Global Identifiers: sfid

The smallFactory ID (`sfid`) is the canonical identifier for every entity in smallFactory.

- Purpose
  - `sfid` is globally unique across all entities and never reused (temporal uniqueness).
  - `sfid` MUST be safe as a file or directory name across Windows/macOS/Linux.

  - Format
    - Regex (authoritative pattern):
      ```regex
      ^(?=.{3,64}$)[a-z]+_[a-z0-9_-]*[a-z0-9]$
      ```

- Commit metadata
  - Commits that affect an entity MUST include a machine-parsable token: `::sfid::<SFID>`. It should be concise and provide enough information to undo the change.

- Entity store and lifecycle
  - The data repository MUST contain a root directory `entities/`.
  - Each `sfid` MUST have a canonical entity file at `entities/<SFID>.yml` that persists forever, even if the entity is retired.
  - This file is the canonical metadata for the entity and enforces temporal uniqueness.

  Example `entities/l_a1.yml`:

  ```yaml
  status: active # or 'retired'
  notes: "Shelf A1 in aisle A"
  ```

  | Entity Type | Prefix | Example sfid | Notes |
  | --- | --- | --- | --- |
  | Location | `l_` | `l_a1` | Physical storage/location (e.g., shelf, bin, room) |
  | Part | `p_` | `p_m3x10` | Discrete part/stock item |

  Additional prefixes will be added here as new entity types are defined.

### sfid Naming Conventions (Recommended)

- Purpose: Improve searchability, interchangeability grouping, and lot/serial tracking. These conventions are HIGHLY RECOMMENDED but not required.
- Parts (`p_`):
  - Structure: `p_<part-number>[ _<postfix> ]`
    - `<part-number>`: use lowercase letters/digits; `-` may separate subcodes (e.g., `stm32-c`, `m3x10`). Prefer hyphens in the base; reserve `_` for classification separators.
    - `<postfix>` (optional): one or more classifications separated by `_` to differentiate instances, e.g. `lot23`, `sn39402`, `sn39404`.
    - Classification order and specificity: classifications MUST progress from general → specific left-to-right. Do not reorder once established.
    - Prefix search rule: prefix matches at classification boundaries MUST find all more-specific variants. Example: `p_1kr` matches `p_1kr_lot21` and `p_1kr_lot21_sn39402`.
    - Classification charset: within a classification, use `[a-z0-9-]`; do not use `_` inside classifications (only as the classification delimiter).
  - Examples:
    - `p_1kr`
    - `p_1kr_lot21` `p_1kr_lot52` (two otherwise identical parts tracked separately)
    - `p_m3x10`
    - `p_m3x10_lot23`
    - `p_stm32-c_sn39402`, `p_stm32-c_sn59404`

### Module: inventory

- Top-level directory: `inventory/`
- Purpose: Represent on-hand quantities per location (`l_*`) by entity `sfid`. These files are operational state; canonical entity metadata lives in `entities/<SFID>.yml`.
- Scope: Location (`l_*`) and any storable entity (`p_*` typically, but any valid `sfid` allowed).

#### Repository layout

```
inventory/
  l_a1/                      # each subdirectory MUST be a location sfid (prefix `l_`)
    p_m3x10.yml              # file name is the stored entity sfid
    p_m3x10_lot23.yml
    p_1kr.yml
    tool_caliper.yml         # non-part entity also permitted
  l_bin7/
    p_m3x10.yml
  l_qc/
    p_stm32-c_sn39402.yml
```

Rules:
- Every direct child of `inventory/` MUST be a directory named exactly as a location `sfid` (e.g., `l_a1`, `l_bin7`).
- Inside each location directory, each YAML file MUST be named exactly as the stored entity `sfid` with a `.yml` extension.
- There is at most one file per entity per location. The file encodes the current on-hand quantity at that location.
- Inventory files are non-canonical; history/audit is derived from Git commits.

#### File schema (per `inventory/<l_*>/<SFID>.yml`)

Required fields:
- `quantity` (integer, >= 0): the on-hand count at this location. Counts SHOULD be in the entity's base unit (typically `ea`).

Other fields:
- Implementations MAY include additional optional keys as needed for workflows (e.g., labels, thresholds, notes).
- Readers can expect additional optional keys, but should ignore unknown keys.
- Writers SHOULD preserve optional keys when updating `quantity` (read-modify-write behavior).

Example (`inventory/l_shelf_a1/p_m3x10.yml`):

```yaml
quantity: 125
```

Example (non-part entity, `inventory/l_qc_desk/tool_caliper.yml`):

```yaml
quantity: 1
```

Additional guidance:
- Zero quantities are permitted to stage planned placements, but it is RECOMMENDED to remove the file to represent no stock at a location.
- Do not duplicate entity metadata here (name, description, manufacturer, lot/serial); that lives in `entities/<SFID>.yml`.
- Git history is the audit trail; avoid redundant timestamps. Commit history provides change chronology.
- Commit messages for inventory changes MUST include both tokens: `::sfid::<ENTITY_SFID>` and `::sfid::<LOCATION_SFID>`.

---

## Versioning Policy (SemVer)

- We use Semantic Versioning for the Core API: MAJOR.MINOR.PATCH.
  - MAJOR: incompatible API or specification changes.
  - MINOR: backward-compatible additions (new fields, endpoints, outputs).
  - PATCH: backward-compatible fixes and internal improvements.

- Stability gates for a major line:
  - DRAFT → RC → PROD. While DRAFT, we may refine this specification. Once marked PROD, SemVer compatibility is strictly enforced.

---

## Change Management Requirements

- Every change MUST be assessed against this specification.
- If a change modifies this specification or conflicts with it:
  - Update this file in the same PR.
  - Bump the API version appropriately.
  - Provide migration notes and deprecation path (where feasible).
- All PRs should explicitly state: "Specification compliant? Yes/No" and link to this file.

---

## Scope of Applicability

This specification applies to:
- Core API v1 implementation under `smallfactory/core/v1/`
- CLI behavior that delegates to Core v1
- Web UI features backed by Core v1
- Data repository structure and file formats governed by Core v1

---

## Current Status

- API: v1.0 (DRAFT)
- This document: v1.0-DRAFT, created on 2025-08-08 (local time).

Once promoted to PROD, any breaking changes will require bumping to v2.0.
