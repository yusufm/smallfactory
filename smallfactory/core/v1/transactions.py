from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from typing import Any, Callable, ContextManager, TypeVar
import threading

from .locks import assert_no_upgrade_in_progress, repo_process_lock
from .versioning import assert_repo_version_matches_tool


T = TypeVar("T")


_REPO_TXN_LOCK = threading.Lock()


def run_repo_mutation(
    datarepo_path: Path,
    mutate_fn: Callable[[], T],
    *,
    lock_timeout_seconds: float = 30.0,
    lock_poll_interval_seconds: float = 0.05,
    before_mutation: Callable[[Path], None] | None = None,
    mutation_context: Callable[[Path], ContextManager[Any]] | None = None,
    after_mutation_locked: Callable[[Path, T], None] | None = None,
    after_mutation_unlocked: Callable[[Path, T], None] | None = None,
) -> T:
    """Serialize a repository mutation across all interfaces using shared core locks.

    Callers can inject optional orchestration hooks around the actual mutation:
    - ``before_mutation`` runs inside the lock before the mutation.
    - ``mutation_context`` supplies an optional context manager around the mutation.
    - ``after_mutation_locked`` runs after the mutation but before the lock is released.
    - ``after_mutation_unlocked`` runs after the shared lock is released.
    """
    with _REPO_TXN_LOCK:
        with repo_process_lock(
            datarepo_path,
            timeout_seconds=lock_timeout_seconds,
            poll_interval_seconds=lock_poll_interval_seconds,
        ):
            assert_repo_version_matches_tool(datarepo_path)
            assert_no_upgrade_in_progress(datarepo_path)
            if before_mutation is not None:
                before_mutation(datarepo_path)
            ctx = mutation_context(datarepo_path) if mutation_context is not None else nullcontext()
            with ctx:
                result = mutate_fn()
            if after_mutation_locked is not None:
                after_mutation_locked(datarepo_path, result)
    if after_mutation_unlocked is not None:
        after_mutation_unlocked(datarepo_path, result)
    return result
