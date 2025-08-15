# Concurrency-Safe Git Workflow (Web/CLI)

This document describes the concurrency-safe git workflow used by the web app and recommended for CLI orchestration. It now defaults to background-only fetch and async push to keep requests fast.

## Overview

All mutation endpoints in the web app must use the transaction guard `_run_repo_txn` defined in `web/app.py`. This wrapper ensures:

- Safe pull orchestration with background-only fetch by default
- Serialized mutations via a process-wide lock
- Optional autocommit against the mutated paths
- Conditional push to the remote (if configured), performed asynchronously by default

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
2. Otherwise, safe pull orchestration:
   - Background mode (default): Schedule a background `git fetch` when TTL expires and immediately continue. We skip behind-check and `git pull` on the request path.
   - Sync mode: Perform a rate-limited `git fetch`, then only run `git pull --ff-only` if `HEAD` is behind `@{u}`.
   - Lazy/off mode: Skip network fetch; behind-check uses cached refs and may still fast-forward pull if detected behind.
   - Local changes rule (applies only when a pull is attempted):
     - Default ON (`SF_GIT_PULL_ALLOW_UNTRACKED=1`): untracked files are allowed; other local changes abort the pull.
     - OFF (`SF_GIT_PULL_ALLOW_UNTRACKED=0`): any local changes (including untracked) abort the pull.
3. Run `mutate_fn()`.
4. If `SF_WEB_AUTOCOMMIT` is enabled (default ON), stage `autocommit_paths` and commit with `autocommit_message`.
5. If `SF_WEB_AUTOPUSH` is enabled (default ON), push is performed. By default it happens asynchronously and can be coalesced by a TTL (see below). Failures are non-fatal and logged.

## Environment Variables (knobs and defaults)

- `SF_WEB_AUTOCOMMIT` (default ON)
  - Perform autocommit after mutation. Disable with `SF_WEB_AUTOCOMMIT=0`.
- `SF_WEB_AUTOPUSH` (default ON)
  - Push after autocommit. Disable with `SF_WEB_AUTOPUSH=0`.
- `SF_WEB_AUTOPUSH_ASYNC` (default ON)
  - If ON, push runs in a background thread off the request path.
  - Set `SF_WEB_AUTOPUSH_ASYNC=0` to force synchronous pushes on the request path.
- `SF_GIT_PUSH_TTL_SEC` (default 0)
  - Coalesce frequent pushes by delaying the background push by N seconds.
  - 0 = immediate async push; >0 = schedule and coalesce multiple pushes into one.
- `SF_GIT_FETCH_MODE` (default background)
  - Controls fetch/pull behavior before mutations:
    - `bg`, `background`, `async` (or unset): schedule background fetch; skip behind-check and pull on the request path.
    - `lazy`, `off`, `none`, `skip`: do not fetch; behind-check uses cached refs; may still fast-forward pull if detected behind.
    - Any other value: synchronous mode (rate-limited fetch + behind-aware fast-forward pull when needed).
- `SF_GIT_PULL_TTL_SEC` (default 10)
  - Rate-limits fetches (or background fetch scheduling) used to detect if the repo is behind upstream.
- `SF_GIT_PULL_ALLOW_UNTRACKED` (default ON)
  - If ON, untracked files are allowed during a pull; other local changes abort the pull.
  - If OFF (`0`), any changes (including untracked) abort the pull.
- `SF_GIT_DISABLED` (default OFF)
  - Disable git orchestration entirely; mutations run without pull/commit/push.
- `SF_DEBUG_GIT` (default ON)
  - Enable detailed git operation logs from the web server. Disable with `SF_DEBUG_GIT=0`.

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

### Best Practices (recommended defaults)

- Do not set any git-related env vars; defaults are optimized for low latency and safety:
  - Background fetch enabled by default; no blocking fetch/pull on request path.
  - Autocommit ON; Autopush ON and async by default.
  - `SF_GIT_PULL_TTL_SEC=10` keeps remote refs reasonably fresh.
  - `SF_GIT_PULL_ALLOW_UNTRACKED=1` avoids false positives from untracked files.

### Caveats (background mode)

- Because behind-check/pull are skipped on the request path, if the remote is ahead, an immediate push can be rejected until the background fetch completes and you reconcile. The server logs the failure; retries succeed once refs are updated or after manual intervention.
