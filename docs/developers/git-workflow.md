# Concurrency-Safe Git Workflow (Web/CLI)

This document describes the concurrency-safe git workflow used by the web app and recommended for CLI orchestration.

## Overview

All mutation endpoints in the web app must use the transaction guard `_run_repo_txn` defined in `web/app.py`. This wrapper ensures:

- Safe git pull (fast-forward only)
- Serialized mutations via a process-wide lock
- Optional autocommit against the mutated paths
- Conditional push to the remote (if configured)

Core mutation functions (e.g., in `smallfactory/core/v1/entities.py` and `inventory.py`) are responsible only for making filesystem changes and performing commit-only operations via `git_commit_paths`. They must never push. Push orchestration is handled at the web/CLI layer.

## `_run_repo_txn` signature

```
_run_repo_txn(datarepo_path: Path, mutate_fn, *, autocommit_message: str | None = None, autocommit_paths: List[str] | None = None)
```

- `datarepo_path`: Path to the root of the git data repository.
- `mutate_fn`: A no-arg function that performs the mutation and returns a result.
- `autocommit_message`: Message to use if autocommit is enabled.
- `autocommit_paths`: Relative paths (under `datarepo_path`) to stage and commit. Prefer top-level directories when multiple files are touched (e.g., `entities/<sfid>` or `inventory/<part>`).

Behavior:
1. If `SF_GIT_DISABLED=1`, `mutate_fn()` is executed directly with no git operations.
2. Otherwise, perform a safe pull (`--ff-only`). If there are local changes, behavior depends on `SF_GIT_PULL_ALLOW_UNTRACKED`:
   - Default ON: untracked files are allowed; any other local changes abort the pull.
   - If `SF_GIT_PULL_ALLOW_UNTRACKED=0`: any changes (including untracked) abort the pull.
3. Run `mutate_fn()`.
4. If `SF_WEB_AUTOCOMMIT` is enabled (default ON), stage `autocommit_paths` with `git add -A -- <path>` and commit with timestamped `autocommit_message`.
5. If `SF_WEB_AUTOPUSH` is enabled (default ON), attempt `git push origin HEAD` if a remote named `origin` exists. Failures are non-fatal and logged.

## Environment Variables

- `SF_WEB_AUTOCOMMIT` (default ON)
  - Controls whether the web server performs an autocommit after mutation.
  - Disable with `SF_WEB_AUTOCOMMIT=0`.
- `SF_WEB_AUTOPUSH` (default ON)
  - Controls whether the web server pushes after a successful autocommit.
  - Disable with `SF_WEB_AUTOPUSH=0`.
- `SF_GIT_PULL_ALLOW_UNTRACKED` (default ON)
  - If ON, safe pull ignores untracked files but aborts on any other local changes.
  - If OFF (`0`), any local changes (including untracked) abort the pull.
- `SF_GIT_DISABLED` (default OFF)
  - If ON, disables git orchestration entirely; mutations run without pull/commit/push.

## Core Git Utilities

- `git_commit_paths(repo_path: Path, paths: list[Path], message: str, delete: bool = False)`
  - Stages the specified paths (or removes with `git rm` if `delete=True`) and commits with the message.
  - Commit-only; does not push.
- `git_push(repo_path: Path, remote: str = "origin", ref: str = "HEAD") -> bool`
  - Pushes to the given remote if it exists. Returns False if remote missing or on failure.

Note: The deprecated `git_commit_and_push` function has been removed. Use `git_commit_paths` in core, and orchestrate pushes in the web/CLI layer via `_run_repo_txn`.

## Example (Web Route)

```python
# web/app.py snippet

def _mutate():
    return update_entity_fields(datarepo_path, sfid, updates)

_ = _run_repo_txn(
    datarepo_path,
    _mutate,
    autocommit_message=f"[smallFactory][web] Update entity {sfid} fields",
    autocommit_paths=[f"entities/{sfid}"]
)
```

## Guidance

- Always pass a minimal set of `autocommit_paths` to scope the commit to the module being mutated.
- Prefer directory scopes (e.g., `entities/<sfid>`) to capture additions/deletions reliably.
- Keep core mutation functions idempotent and commit-only; do not call `git push` from core.
- For future endpoints, always wrap mutations with `_run_repo_txn` to ensure safety and consistency.
