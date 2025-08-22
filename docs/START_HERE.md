# Start Here

Welcome to smallFactory — a Git‑native toolset to manage the things you make. It keeps your product data in plain files under Git, so it’s simple, portable, and team‑friendly.

## What you can do
- Design: capture parts/assemblies, attach files, and build BOMs with alternates.
- Snapshot: cut numbered revisions and track what’s released.
- Build & operate: label with QR stickers, scan on mobile, and keep inventory accurate by location.

## Key concepts (quick)
- Entities: parts, assemblies, locations, builds — each has a unique `sfid`.
- Files: an entity’s working folder for drawings/CAD/docs.
- BOM: a part’s list of children (with quantities and alternates).
- Revisions & releases: immutable snapshots and a `released` pointer.
- Inventory: quantities by location (e.g., `l_inbox`, `l_a1`).

## Common workflows
- Add a part and give it a clear name and tags.
- Attach design files under the part’s Files area (organized by folders).
- Build a BOM by adding child parts and alternates.
- Cut a new revision when the design is ready; release it when approved.
- Print QR stickers for parts/locations to speed up scanning.
- Adjust inventory from your phone after moves, picks, or counts.

## Using the app
- Web UI: fast, clean, mobile‑friendly interface for daily work.
  - Entities: view, edit, manage Files, BOM, and Revisions.
  - Inventory: see on‑hand by location; quick add/adjust.
  - Stickers: generate QR labels in batches.
- CLI: automation and scripting when you need it.

## Where your data lives
- Everything is in your Git repository. You can branch, review, and audit like code.
- No database lock‑in; your data stays portable and scriptable.

## Screenshots (optional)
Add images under `docs/img/screenshots/` and they will render here:

![Dashboard](img/screenshots/dashboard.png)
![Entity detail](img/screenshots/entity.png)
![Quick Adjust](img/screenshots/quick_adjust.png)
![Stickers](img/screenshots/stickers.png)

## Go deeper
- Users: Web UI guide and CLI basics — see [docs index](README.md).
- Developers: PLM SPEC and Git workflow — see [Developers docs](developers/README.md).
