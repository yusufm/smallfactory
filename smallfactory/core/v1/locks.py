from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
import errno
import os
import time

try:  # pragma: no cover
    import fcntl  # type: ignore
except Exception:  # pragma: no cover
    fcntl = None  # type: ignore

try:  # pragma: no cover
    import msvcrt  # type: ignore
except Exception:  # pragma: no cover
    msvcrt = None  # type: ignore


REPO_LOCK_FILENAME = ".smallfactory.repo.lock"
UPGRADE_MARKER_FILENAME = ".smallfactory.upgrade.in_progress"


def repo_lock_path(repo_path: Path) -> Path:
    p = Path(repo_path).expanduser().resolve() / ".git" / REPO_LOCK_FILENAME
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def upgrade_marker_path(repo_path: Path) -> Path:
    p = Path(repo_path).expanduser().resolve() / ".git" / UPGRADE_MARKER_FILENAME
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def is_upgrade_in_progress(repo_path: Path) -> bool:
    return upgrade_marker_path(repo_path).exists()


def assert_no_upgrade_in_progress(repo_path: Path) -> None:
    if is_upgrade_in_progress(repo_path):
        raise RuntimeError("Repository upgrade in progress; retry after upgrade completes.")


@contextmanager
def repo_process_lock(
    repo_path: Path,
    *,
    timeout_seconds: float = 30.0,
    poll_interval_seconds: float = 0.05,
) -> Iterator[None]:
    """Cross-process advisory lock for repository-wide mutations."""
    lock_path = repo_lock_path(repo_path)
    timeout_sec = max(0.0, float(timeout_seconds))
    poll_sec = max(0.0, float(poll_interval_seconds))
    deadline = time.monotonic() + timeout_sec

    # Keep binary mode for Windows byte-range locking compatibility.
    fh = open(lock_path, "a+b")
    try:
        if os.name == "nt" and msvcrt is not None:  # pragma: no cover
            fh.seek(0, os.SEEK_END)
            if fh.tell() == 0:
                fh.write(b"\0")
                fh.flush()
            while True:
                try:
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError(f"Timed out waiting for repo lock after {timeout_sec:.1f}s")
                    time.sleep(poll_sec)
            try:
                yield
            finally:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            return

        if fcntl is None:  # pragma: no cover
            yield
            return

        while True:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as e:
                lock_busy_errnos = {errno.EAGAIN, errno.EACCES, getattr(errno, "EWOULDBLOCK", errno.EAGAIN)}
                if e.errno not in lock_busy_errnos:
                    raise
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"Timed out waiting for repo lock after {timeout_sec:.1f}s")
                time.sleep(poll_sec)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    finally:
        fh.close()


@contextmanager
def upgrade_in_progress_marker(repo_path: Path) -> Iterator[Path]:
    """Create an upgrade marker while a repo upgrade is in progress."""
    marker = upgrade_marker_path(repo_path)
    payload = (
        f"pid={os.getpid()}\n"
        f"started_at={datetime.now(timezone.utc).isoformat()}\n"
    )
    try:
        fd = os.open(str(marker), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise RuntimeError("Repository upgrade already in progress.")
    try:
        os.write(fd, payload.encode("utf-8"))
    finally:
        os.close(fd)
    try:
        yield marker
    finally:
        try:
            marker.unlink(missing_ok=True)
        except Exception:
            pass
