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
  The CLI and Web UI must delegate to the Core API (this package) for business logic.

- Backward compatibility by default.
  Within a major version, changes are additive and non-breaking. Breaking changes require a major version bump.

- Stable identifiers.
  Every entity MUST have a globally unique, never-reused `sfid` (smallFactory ID). Entity-specific human-friendly IDs (e.g., inventory `id`) may exist but are not globally unique and are not the canonical identifier.

- Transparent Git history.
  All mutating operations auto-commit with clear messages and metadata (including `::sfid::<SFID>` for entity-affecting changes).

- Deterministic behavior.
  Given the same inputs and repository state, operations produce the same results.

- Consistent UX contract.
  Supported output modes (`human`, `json`, `yaml`) and required fields (`id`, `name`, `quantity`, `location`) are stable within the major version.

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
  - Human-friendly, entity-scoped IDs (e.g., inventory `id`) may exist but are not canonical.

  - Format
    - MUST begin with a registered lowercase prefix followed by an underscore (e.g., `loc_`).
    - After the prefix, allowed characters are: `a–z`, `0–9`, `_`, `-` (underscore, hyphen).
    - No spaces, dots `.`, slashes `/`, backslashes `\\`, or other special characters.
    - MUST be lowercase ASCII. Tools MUST enforce lowercase; non-lowercase inputs are invalid.
    - Length SHOULD be between 3 and 64 characters inclusive.
    - Regex (authoritative pattern):
      ```regex
      ^(?=.{3,64}$)[a-z]+_[a-z0-9_-]*[a-z0-9]$
      ```
      - Anchored; enforces total length 3–64.
      - Requires lowercase prefix + underscore.
      - Allows only `a–z`, `0–9`, `_`, `-` after the prefix.
      - Disallows ending with `_` or `-`.
      - Use ASCII character classes. Values MUST be lowercase.

- Commit metadata
  - Commits that affect an entity MUST include a machine-parsable token: `::sfid::<SFID>`.

- Registry and lifecycle
  - The data repository MUST contain a root directory `sfids/`.
  - Each `sfid` MUST have a registry file at `sfids/<SFID>.yml` that persists forever, even if the entity is retired.
  - The registry file provides all the metadata for the entity and enforces temporal uniqueness.

  Example `sfids/loc_a1.yml`:

  ```yaml
  sfid: loc_a1
  status: active # or 'retired'
  notes: "Shelf A1 in aisle A"
  ```

  | Entity Type | Prefix | Example sfid | Notes |
  | --- | --- | --- | --- |
  | Location | `loc_` | `loc_a1` | Physical storage/location (e.g., shelf, bin, room) |

  Additional prefixes will be added here as new entity types are defined.

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
