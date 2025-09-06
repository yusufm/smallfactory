#!/usr/bin/env python3
"""
smallFactory Web UI - Flask application providing a modern web interface
for the Git-native PLM system.
"""

from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, send_file, Response, g, session
from pathlib import Path
import json
import sys
import os
import heapq
import csv
import re
import base64
import io
from PIL import Image
import subprocess
import threading
from datetime import datetime
from typing import List
import time
import atexit
from contextlib import contextmanager
import tarfile
from jinja2 import ChoiceLoader, FileSystemLoader

# Prometheus metrics
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST

# Add the parent directory to Python path to import smallfactory modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from smallfactory.core.v1.config import get_datarepo_path, get_inventory_field_specs, get_entity_field_specs_for_sfid, get_stickers_default_fields, load_datarepo_config, DATAREPO_CONFIG_FILENAME
from smallfactory.core.v1.inventory import (
    inventory_post,
    inventory_onhand,
    inventory_onhand_readonly,
)
from smallfactory.core.v1.entities import (
    list_entities,
    get_entity,
    create_entity,
    update_entity_fields,
    retire_entity,
    # Revisions
    get_revisions,
    bump_revision,
    release_revision,
    # BOM management
    bom_list,
    bom_add_line,
    bom_remove_line,
    bom_set_line,
    bom_alt_add,
    bom_alt_remove,
    resolved_bom_tree as ent_resolved_bom_tree,
)
from smallfactory.core.v1.stickers import (
    generate_sticker_for_entity,
    check_dependencies as stickers_check_deps,
)
from smallfactory.core.v1.vision import (
    ask_image as vlm_ask_image,
    extract_invoice_part as vlm_extract_invoice_part,
)
from smallfactory.core.v1.gitutils import git_push
from smallfactory.core.v1.validate import validate_repo

app = Flask(__name__)
app.secret_key = os.environ.get('SF_WEB_SECRET', 'dev-only-insecure-secret')

# Optionally allow an extra templates directory for SaaS-provided partials
# If SF_EXTRA_TEMPLATES is not set, fall back to the standard mount path.
_extra_tpl_dir = os.environ.get('SF_EXTRA_TEMPLATES') or '/var/lib/smallfactory-saas/templates'
if _extra_tpl_dir and os.path.isdir(_extra_tpl_dir):
    try:
        app.jinja_loader = ChoiceLoader([  # type: ignore[attr-defined]
            app.jinja_loader,              # default loader
            FileSystemLoader(_extra_tpl_dir),
        ])
        try:
            app.logger.info(f"[smallFactory] Added extra templates directory: {_extra_tpl_dir}")
        except Exception:
            pass
    except Exception:
        # If loader setup fails, continue with default loader
        pass

# -----------------------
# App Version (Git) helper
# -----------------------
_APP_VERSION_CACHE: dict | None = None

def _get_project_root() -> Path:
    try:
        return Path(__file__).parent.parent  # repo root directory (parent of web/)
    except Exception:
        return Path.cwd()

def _read_app_version() -> dict:
    """Best-effort: read running code git version (hash/date/dirty/branch).

    Returns a dict with keys: hash, short, date, dirty, branch. Empty values
    on failure or when not in a git repo. Cached at module level.
    """
    global _APP_VERSION_CACHE
    if _APP_VERSION_CACHE is not None:
        return _APP_VERSION_CACHE
    info = {
        'hash': None,
        'short': None,
        'date': None,
        'dirty': False,
        'branch': None,
    }
    root = _get_project_root()
    try:
        # Ensure this codebase is a git repo
        ck = subprocess.run(['git', '-C', str(root), 'rev-parse', '--is-inside-work-tree'], capture_output=True, text=True)
        if ck.returncode != 0:
            _APP_VERSION_CACHE = info
            return info
        # Full and short hash
        h_full = subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'], capture_output=True, text=True)
        if h_full.returncode == 0:
            info['hash'] = (h_full.stdout or '').strip() or None
        h_short = subprocess.run(['git', '-C', str(root), 'rev-parse', '--short', 'HEAD'], capture_output=True, text=True)
        if h_short.returncode == 0:
            info['short'] = (h_short.stdout or '').strip() or None
        # Commit date (ISO)
        dt = subprocess.run(['git', '-C', str(root), 'show', '-s', '--format=%cI', 'HEAD'], capture_output=True, text=True)
        if dt.returncode == 0:
            info['date'] = (dt.stdout or '').strip() or None
        # Branch (may be detached)
        br = subprocess.run(['git', '-C', str(root), 'rev-parse', '--abbrev-ref', 'HEAD'], capture_output=True, text=True)
        if br.returncode == 0:
            info['branch'] = (br.stdout or '').strip() or None
        # Dirty flag
        st = subprocess.run(['git', '-C', str(root), 'status', '--porcelain'], capture_output=True, text=True)
        if st.returncode == 0:
            dirty = bool((st.stdout or '').strip())
            info['dirty'] = dirty
    except Exception:
        # Leave defaults
        pass
    _APP_VERSION_CACHE = info
    return info

@app.context_processor
def inject_app_version():
    try:
        ver = _read_app_version()
    except Exception:
        ver = {'hash': None, 'short': None, 'date': None, 'dirty': False, 'branch': None}
    return {'app_version': ver}

# -----------------------
# Jinja Filters / Helpers
# -----------------------

# -----------------------
# Prometheus instrumentation
# -----------------------
_METRICS_ENV = os.environ.get('METRICS_ENV', 'prod')
_SERVICE_NAME = os.environ.get('SERVICE_NAME', 'app')

HTTP_REQUESTS_TOTAL = Counter(
    'sf_web_http_requests_total',
    'Total HTTP requests',
    ['method', 'path', 'status', 'env', 'service'],
)

HTTP_REQUEST_DURATION_SECONDS = Histogram(
    'sf_web_http_request_duration_seconds',
    'HTTP request duration in seconds',
    ['method', 'path', 'status', 'env', 'service'],
    buckets=(0.05, 0.1, 0.3, 1, 3, 10),
)

@app.before_request
def _metrics_before_request():
    try:
        g._metrics_t0 = time.time()
    except Exception:
        pass

@app.after_request
def _metrics_after_request(response: Response):
    try:
        t0 = getattr(g, '_metrics_t0', None)
        dt = (time.time() - t0) if t0 is not None else None
        method = str(request.method or 'GET')
        # Prefer route rule (stable cardinality); fallback to path
        try:
            rule = request.url_rule.rule if getattr(request, 'url_rule', None) else None
        except Exception:
            rule = None
        path_label = str(rule or request.path or '/')
        status = str(getattr(response, 'status_code', 0))
        HTTP_REQUESTS_TOTAL.labels(method, path_label, status, _METRICS_ENV, _SERVICE_NAME).inc()
        if dt is not None:
            HTTP_REQUEST_DURATION_SECONDS.labels(method, path_label, status, _METRICS_ENV, _SERVICE_NAME).observe(dt)
    except Exception:
        # Never break responses on metrics errors
        pass
    return response

@app.get('/metrics')
def _metrics_endpoint():
    try:
        # Update lightweight internal gauges before scraping
        try:
            _update_internal_gauges()
        except Exception:
            # Never fail the metrics endpoint if internal gauge update errors
            pass
        data = generate_latest()  # default registry
        return Response(response=data, status=200, mimetype=CONTENT_TYPE_LATEST)
    except Exception:
        return Response(response=b'metrics error', status=500, mimetype='text/plain')

def _human_bytes(num: int) -> str:
    """Format a byte count into a human-readable string (KB, MB, GB...)."""
    try:
        n = int(num)
    except Exception:
        return str(num)
    sign = '-' if n < 0 else ''
    if n < 0:
        n = -n
    units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB', 'EB', 'ZB', 'YB']
    for u in units:
        if n < 1024 or u == units[-1]:
            # Use 0 decimals for bytes, 1 decimal for others
            if u == 'B':
                return f"{sign}{n} {u}"
            return f"{sign}{n:.1f} {u}"
        n = n / 1024.0

@app.template_filter('human_bytes')
def _human_bytes_filter(value):
    return _human_bytes(value)

# -----------------------
# Internal metrics (low-cost, TTL-cached)
# -----------------------

# TTL for recomputing internal gauges (in seconds). Override via SF_METRICS_TTL_SEC
try:
    _METRICS_TTL_SEC = int(os.environ.get('SF_METRICS_TTL_SEC', '15') or '15')
except Exception:
    _METRICS_TTL_SEC = 15

# Gauges requested
REPO_COMMITS_TOTAL = Gauge(
    'sf_repo_commits_total',
    'Total git commits in the data repository',
    ['env', 'service'],
)

DATAREPO_TOTAL_FILES = Gauge(
    'sf_datarepo_total_files',
    'Total number of files in the data repository (excludes .git)',
    ['env', 'service'],
)

DATAREPO_SIZE_ONDISK_BYTES = Gauge(
    'sf_datarepo_size_bytes_on_disk',
    'Approximate total size in bytes of the data repository on disk (includes .git)',
    ['env', 'service'],
)

ENTITIES_TOTAL = Gauge(
    'sf_entities_total',
    'Total number of entities',
    ['env', 'service'],
)

# Simple module-level cache to avoid repeated filesystem scans per scrape
_SIMPLE_METRICS_CACHE: dict[str, float | dict] = {
    'ts': 0.0,
    'values': {},
}


def _compute_internal_metrics(datarepo_path: Path) -> dict:
    """Compute low-cost repository metrics.

    Returns a dict with keys: commits, total_files, size_bytes, entities_total.
    """
    root = Path(datarepo_path)
    commits = 0
    try:
        gm = _compute_git_metrics(root)
        commits = int(gm.get('commits') or 0)
    except Exception:
        commits = 0

    total_files = 0

    # Walk working tree excluding .git to count files only (cheap)
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            # skip .git entirely
            dirnames[:] = [d for d in dirnames if d != '.git']
            # lightweight count
            total_files += len(filenames)
    except Exception:
        pass

    # Compute total on-disk size via 'du -sk' (fast, includes .git and honors FS blocks)
    size_bytes_on_disk = 0
    try:
        du = subprocess.run(['du', '-sk', str(root)], capture_output=True, text=True)
        if du.returncode == 0:
            # output: "<kilobytes>\t<path>"
            kb = (du.stdout or '').strip().split()[0]
            size_bytes_on_disk = int(kb) * 1024
    except Exception:
        size_bytes_on_disk = 0

    # Count entities using core API for consistency
    entities_total = 0
    try:
        # list_entities expects a Path to the datarepo root
        ents = list_entities(root)
        entities_total = len(ents) if isinstance(ents, list) else 0
    except Exception:
        # Fallback to 0 on any error
        entities_total = 0

    return {
        'commits': commits,
        'total_files': total_files,
        'size_bytes_on_disk': size_bytes_on_disk,
        'entities_total': entities_total,
    }


def _update_internal_gauges() -> None:
    """Update internal Prometheus gauges with TTL caching."""
    now = time.time()
    try:
        ts = float(_SIMPLE_METRICS_CACHE.get('ts') or 0.0)
    except Exception:
        ts = 0.0
    if now - ts < _METRICS_TTL_SEC:
        vals = _SIMPLE_METRICS_CACHE.get('values') or {}
    else:
        repo_path = get_datarepo_path()
        vals = _compute_internal_metrics(repo_path)
        _SIMPLE_METRICS_CACHE['ts'] = now
        _SIMPLE_METRICS_CACHE['values'] = vals

    # Apply to gauges
    lbls = ( _METRICS_ENV, _SERVICE_NAME )
    try:
        REPO_COMMITS_TOTAL.labels(*lbls).set(float(vals.get('commits') or 0))
        DATAREPO_TOTAL_FILES.labels(*lbls).set(float(vals.get('total_files') or 0))
        DATAREPO_SIZE_ONDISK_BYTES.labels(*lbls).set(float(vals.get('size_bytes_on_disk') or 0))
        ENTITIES_TOTAL.labels(*lbls).set(float(vals.get('entities_total') or 0))
    except Exception:
        # Do not raise in metrics path
        pass

# -----------------------
# Auth / Session helpers
# -----------------------

@app.route('/logout', methods=['GET'])
def http_logout():
    """Clear any server-side session and redirect to a sign-out URL.

    SaaS can set SF_LOGOUT_REDIRECT_URL to an oauth2-proxy sign-out URL, e.g.:
    https://auth.example.com/oauth2/sign_out?rd=https://t-tenant.example.com
    If not set, redirect to '/'.
    """
    try:
        session.clear()
    except Exception:
        pass
    target = os.environ.get('SF_LOGOUT_REDIRECT_URL') or '/'
    return redirect(target)

# -----------------------
# Optional Git auto-commit support (ON by default)
# Disable by setting environment variable: SF_WEB_AUTOCOMMIT=0
# -----------------------
def _autocommit_enabled() -> bool:
    val = os.environ.get('SF_WEB_AUTOCOMMIT')
    if val is None:
        return True
    return val.lower() in ('1', 'true', 'yes', 'on')


def _maybe_git_autocommit(datarepo_path: Path, message: str, paths: List[str]) -> bool:
    """If enabled and inside a git repo, stage the given paths (with -A) and commit.

    Returns True if a commit was created, False otherwise. Never raises.
    """
    try:
        if not _autocommit_enabled():
            return False
        # Ensure we are inside a git repo
        ck = subprocess.run(['git', '-C', str(datarepo_path), 'rev-parse', '--is-inside-work-tree'], capture_output=True)
        if ck.returncode != 0:
            return False
        # Stage with -A to capture deletions within the specified paths
        for p in (paths or []):
            subprocess.run(['git', '-C', str(datarepo_path), 'add', '-A', '--', p], check=False)
        # Create commit
        ts = datetime.now().isoformat(timespec='seconds')
        msg = f"{message} ({ts})"
        cm = subprocess.run(['git', '-C', str(datarepo_path), 'commit', '-m', msg], capture_output=True)
        # If nothing to commit, exit quietly
        if cm.returncode != 0:
            return False
        return True
    except Exception:
        return False


# -----------------------
# Git orchestration helpers (safe pull + txn + autopush)
# -----------------------
def _autopush_enabled() -> bool:
    val = os.environ.get('SF_WEB_AUTOPUSH')
    if val is None:
        return True
    return val.lower() in ('1', 'true', 'yes', 'on')


def _autopush_async_enabled() -> bool:
    val = os.environ.get('SF_WEB_AUTOPUSH_ASYNC')
    if val is None:
        return True
    return val.lower() in ('1', 'true', 'yes', 'on')


def _pull_allow_untracked() -> bool:
    val = os.environ.get('SF_GIT_PULL_ALLOW_UNTRACKED')
    if val is None:
        return True
    return val.lower() in ('1', 'true', 'yes', 'on')


def _git_disabled() -> bool:
    val = os.environ.get('SF_GIT_DISABLED')
    if val is None:
        return False
    return val.lower() in ('1', 'true', 'yes', 'on')


def _debug_git_enabled() -> bool:
    val = os.environ.get('SF_DEBUG_GIT')
    if val is None:
        return True
    return val.lower() in ('1', 'true', 'yes', 'on')


def _fetch_mode_lazy() -> bool:
    """Return True if SF_GIT_FETCH_MODE requests lazy/off behavior."""
    val = os.environ.get('SF_GIT_FETCH_MODE')
    if not val:
        return False
    return val.lower() in ('lazy', 'off', 'none', 'skip')


def _fetch_mode_background() -> bool:
    """Return True if SF_GIT_FETCH_MODE requests background fetch behavior."""
    val = os.environ.get('SF_GIT_FETCH_MODE')
    if val is None:
        return True
    return val.lower() in ('bg', 'background', 'async')


def _dgit(msg: str) -> None:
    if _debug_git_enabled():
        ts = datetime.now().isoformat(timespec='seconds')
        line = f'[smallFactory][git] {ts} {msg}'
        try:
            # Prefer Flask's logger so logs appear in the Flask console
            app.logger.info(line)
        except Exception:
            # Fallback to stdout, unbuffered
            print(line, flush=True)


def _get_proxy_identity_header_names() -> tuple[list[str], list[str]]:
    """Return candidate header names for user and email derived from environment.

    Env vars (comma-separated, case-insensitive header names):
    - SF_WEB_IDENTITY_HEADER_NAME (defaults: X-Forwarded-User,X-Auth-Request-User)
    - SF_WEB_IDENTITY_HEADER_EMAIL (defaults: X-Forwarded-Email,X-Auth-Request-Email)
    """
    user_env = (os.environ.get('SF_WEB_IDENTITY_HEADER_NAME') or '').strip()
    email_env = (os.environ.get('SF_WEB_IDENTITY_HEADER_EMAIL') or '').strip()
    users = [h.strip() for h in user_env.split(',') if h.strip()] or [
        'X-Forwarded-User',
        'X-Auth-Request-User',
    ]
    emails = [h.strip() for h in email_env.split(',') if h.strip()] or [
        'X-Forwarded-Email',
        'X-Auth-Request-Email',
    ]
    return users, emails


def _extract_identity_from_headers(req) -> tuple[str | None, str | None]:
    """Best-effort extraction of (name, email) from proxy headers.

    - If only a single header is present and looks like an email, derive name from local part.
    - If user header present but email header missing and user looks like email, treat as email.
    - Returns (None, None) if insufficient info.
    """
    try:
        user_hdrs, email_hdrs = _get_proxy_identity_header_names()
        user_val = None
        email_val = None
        # Look up headers case-insensitively via Flask's request.headers
        for hn in user_hdrs:
            v = req.headers.get(hn)
            if v:
                user_val = v.strip()
                break
        for hn in email_hdrs:
            v = req.headers.get(hn)
            if v:
                email_val = v.strip()
                break
        # Heuristics
        def _derive_name_from_email(em: str) -> str:
            base = em.split('@', 1)[0]
            # Simple prettify: replace dots/underscores with spaces and title-case
            pretty = base.replace('.', ' ').replace('_', ' ').strip()
            return pretty.title() if pretty else base

        if not email_val and user_val and ('@' in user_val):
            email_val = user_val
        name_val = user_val
        if (not name_val) and email_val:
            name_val = _derive_name_from_email(email_val)

        # Only use identity if we have both
        if name_val and email_val:
            return name_val, email_val
        return None, None
    except Exception:
        return None, None


@contextmanager
def _with_git_identity(name: str, email: str):
    """Temporarily set GIT_AUTHOR_* and GIT_COMMITTER_* for subprocess git commands."""
    keys = ['GIT_AUTHOR_NAME', 'GIT_AUTHOR_EMAIL', 'GIT_COMMITTER_NAME', 'GIT_COMMITTER_EMAIL']
    prev = {k: os.environ.get(k) for k in keys}
    try:
        os.environ['GIT_AUTHOR_NAME'] = name
        os.environ['GIT_COMMITTER_NAME'] = name
        os.environ['GIT_AUTHOR_EMAIL'] = email
        os.environ['GIT_COMMITTER_EMAIL'] = email
        yield
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


_GIT_REMOTE_CACHE: dict[str, tuple[float, bool]] = {}
_GIT_UPSTREAM_CACHE: dict[str, tuple[float, bool]] = {}
_GIT_LAST_FETCH: dict[str, float] = {}

# Background fetch scheduling for non-blocking ref refresh
_BG_FETCH_SCHED_LOCK = threading.Lock()
_BG_FETCH_TIMERS: dict[str, threading.Timer] = {}

def _bg_fetch_worker(datarepo_path: Path) -> None:
    """Background worker to run `git fetch origin` without blocking requests."""
    t0 = time.time()
    repo = str(datarepo_path)
    _dgit('bg fetch: start')
    try:
        ft = subprocess.run(['git', '-C', repo, 'fetch', '--quiet', 'origin'], capture_output=True, text=True)
        if ft.returncode != 0:
            msg = (ft.stderr or ft.stdout or '').strip() or 'git fetch failed'
            low = msg.lower()
            soft_fail = (
                ('no such remote' in low) or
                ('does not appear to be a git repository' in low) or
                ('could not read from remote repository' in low) or
                ('repository not found' in low) or
                ('permission denied' in low)
            )
            if soft_fail:
                _dgit(f"bg fetch: remote error; proceeding with cached refs ({msg})")
                if ('no such remote' in low) or ('does not appear to be a git repository' in low):
                    # Update remote cache to avoid repeated attempts until cache expiry
                    try:
                        _GIT_REMOTE_CACHE[repo] = (time.time(), False)
                    except Exception:
                        pass
            else:
                _dgit(f"bg fetch: failed ({msg})")
        else:
            try:
                _GIT_LAST_FETCH[repo] = time.time()
            except Exception:
                pass
            _dgit(f"bg fetch: done {int((time.time()-t0)*1000)}ms")
    except Exception:
        # Silent background error
        pass
    finally:
        try:
            with _BG_FETCH_SCHED_LOCK:
                _BG_FETCH_TIMERS.pop(repo, None)
        except Exception:
            pass

def _schedule_background_fetch(datarepo_path: Path, delay: float = 0.0) -> None:
    """Schedule a background fetch soon to refresh remote refs.

    Coalesces multiple requests; respects SF_GIT_PULL_TTL_SEC via _GIT_LAST_FETCH.
    """
    repo = str(datarepo_path)
    try:
        with _BG_FETCH_SCHED_LOCK:
            existing = _BG_FETCH_TIMERS.get(repo)
            if existing and existing.is_alive():
                _dgit('bg fetch: coalesced; existing timer pending')
                return
            # Mark a recent attempt to avoid rescheduling within TTL while waiting
            _GIT_LAST_FETCH[repo] = time.time()
            t = threading.Timer(max(0.0, delay), _bg_fetch_worker, args=(datarepo_path,))
            try:
                t.daemon = True  # type: ignore[attr-defined]
            except Exception:
                pass
            _BG_FETCH_TIMERS[repo] = t
            t.start()
        _dgit(f"bg fetch: scheduled in {int(max(0.0, delay)*1000)}ms")
    except Exception:
        # Non-fatal
        pass


def _safe_git_pull(datarepo_path: Path) -> tuple[bool, str | None]:
    """Rate-limited, behind-aware fast-forward pull.

    Optimizations:
    - Remote/upstream checks are cached briefly to avoid repeated subprocess calls.
    - Network fetch is rate-limited via SF_GIT_PULL_TTL_SEC (default: 10s).
    - We only run `git pull --ff-only` when HEAD is actually behind upstream.
    - In background fetch mode, we skip behind-check and pull entirely on the request path and only schedule a background fetch.

    Safety:
    - Honors SF_GIT_PULL_ALLOW_UNTRACKED as before when a pull is needed.
    - If no remote or no upstream: skip pull (fetch is rate-limited when checking upstream).
    """
    try:
        repo = str(datarepo_path)
        now = time.time()
        ttl = int(os.environ.get('SF_GIT_PULL_TTL_SEC', '10') or '10')
        _dgit(f'pull: begin ttl={ttl}s')

        # Ensure repo
        ck = subprocess.run(['git', '-C', repo, 'rev-parse', '--is-inside-work-tree'], capture_output=True)
        if ck.returncode != 0:
            return False, 'Not a git repository'

        # Remote existence (cached ~60s)
        rc_ts, rc_has = _GIT_REMOTE_CACHE.get(repo, (0.0, False))
        if now - rc_ts > 60:
            remotes = subprocess.run(['git', '-C', repo, 'remote'], capture_output=True, text=True)
            rc_has = (remotes.returncode == 0) and ('origin' in (remotes.stdout or '').split())
            _GIT_REMOTE_CACHE[repo] = (now, rc_has)
        if not rc_has:
            return True, None  # No remote -> nothing to pull

        # Upstream existence (cached ~60s)
        uc_ts, uc_has = _GIT_UPSTREAM_CACHE.get(repo, (0.0, False))
        if now - uc_ts > 60:
            up = subprocess.run(['git', '-C', repo, 'rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}'], capture_output=True, text=True)
            uc_has = (up.returncode == 0)
            _GIT_UPSTREAM_CACHE[repo] = (now, uc_has)

        # If no upstream configured, optionally refresh remote info (rate-limited), then skip
        if not uc_has:
            if _fetch_mode_lazy():
                _dgit('pull: no upstream; lazy mode -> skip fetch')
            elif _fetch_mode_background():
                last_fetch = _GIT_LAST_FETCH.get(repo, 0.0)
                if (now - last_fetch > ttl) and rc_has:
                    _dgit('pull: no upstream; scheduling background fetch')
                    _schedule_background_fetch(datarepo_path)
            else:
                last_fetch = _GIT_LAST_FETCH.get(repo, 0.0)
                if (now - last_fetch > ttl) and rc_has:
                    t0f = time.time()
                    ft = subprocess.run(['git', '-C', repo, 'fetch', '--quiet', 'origin'], capture_output=True, text=True)
                    if ft.returncode != 0:
                        msg = (ft.stderr or ft.stdout or '').strip() or 'git fetch failed'
                        low = msg.lower()
                        # Gracefully degrade if remote 'origin' is missing
                        if ('no such remote' in low) or ('does not appear to be a git repository' in low):
                            _GIT_REMOTE_CACHE[repo] = (now, False)
                            _dgit(f"pull: no upstream; origin missing; skip fetch ({msg})")
                            return True, None
                        return False, msg
                    _GIT_LAST_FETCH[repo] = now
                    _dgit(f'pull: fetch (no upstream) {int((time.time()-t0f)*1000)}ms')
            return True, None

        # Ensure remote tracking refs are reasonably fresh (rate-limited fetch)
        if _fetch_mode_lazy():
            _dgit('pull: lazy mode -> skip fetch')
        elif _fetch_mode_background():
            last_fetch = _GIT_LAST_FETCH.get(repo, 0.0)
            if now - last_fetch > ttl:
                _dgit('pull: scheduling background fetch')
                _schedule_background_fetch(datarepo_path)
            # In background mode, avoid any synchronous network operations on request path
            _dgit('pull: background mode -> skip behind-check and pull')
            return True, None
        else:
            last_fetch = _GIT_LAST_FETCH.get(repo, 0.0)
            if now - last_fetch > ttl:
                t0f = time.time()
                ft = subprocess.run(['git', '-C', repo, 'fetch', '--quiet', 'origin'], capture_output=True, text=True)
                if ft.returncode != 0:
                    msg = (ft.stderr or ft.stdout or '').strip() or 'git fetch failed'
                    low = msg.lower()
                    # Gracefully degrade on common remote misconfig/errors even if upstream exists
                    soft_fail = (
                        ('no such remote' in low) or
                        ('does not appear to be a git repository' in low) or
                        ('could not read from remote repository' in low) or
                        ('repository not found' in low) or
                        ('permission denied' in low)
                    )
                    if soft_fail:
                        _dgit(f"pull: fetch skipped due to remote error; proceeding with cached refs ({msg})")
                    else:
                        return False, msg
                else:
                    _GIT_LAST_FETCH[repo] = now
                    _dgit(f'pull: fetch {int((time.time()-t0f)*1000)}ms')

        # Compute if we are behind upstream (requires up-to-date remote refs)
        behind = subprocess.run(['git', '-C', repo, 'rev-list', '--count', 'HEAD..@{u}'], capture_output=True, text=True)
        if behind.returncode != 0:
            # Gracefully handle missing/invalid upstream or remote issues by proceeding as not-behind
            emsg = (behind.stderr or behind.stdout or '').strip()
            low = emsg.lower()
            soft = (
                ('no upstream' in low) or
                ('bad revision' in low and '@{u}' in low) or
                ('unknown revision or path not in the working tree' in low) or
                ('no such ref' in low) or
                ("ambiguous argument '@{u}'" in low) or
                ('not something we can merge' in low)
            )
            if soft:
                _dgit(f"pull: behind-check skipped due to upstream/ref error; proceeding as up-to-date ({emsg})")
                n_behind = 0
            else:
                return False, 'Failed to compare with upstream'
        else:
            try:
                n_behind = int((behind.stdout or '0').strip() or '0')
            except Exception:
                n_behind = 0

        _dgit(f'pull: behind={n_behind}')
        if n_behind <= 0:
            _dgit('pull: up-to-date; skip')
            return True, None  # Up-to-date or ahead; no pull needed

        # We need to pull; ensure working tree cleanliness per policy
        st = subprocess.run(['git', '-C', repo, 'status', '--porcelain'], capture_output=True, text=True)
        if st.returncode != 0:
            return False, 'Failed to get git status'
        lines = [ln.rstrip('\n') for ln in (st.stdout or '').splitlines()]
        if not _pull_allow_untracked():
            if any(lines):
                return False, 'Working tree not clean for pull'
        else:
            for ln in lines:
                if not ln.startswith('?? '):
                    return False, 'Local changes present; commit or stash before pull'

        # Fast-forward only pull (only when actually behind)
        t0p = time.time()
        pl = subprocess.run(['git', '-C', repo, 'pull', '--ff-only'], capture_output=True, text=True)
        if pl.returncode != 0:
            msg = (pl.stderr or pl.stdout or '').strip() or 'git pull failed'
            return False, msg
        _dgit(f'pull: pulled ff-only in {int((time.time()-t0p)*1000)}ms')
        return True, None
    except Exception as e:
        return False, str(e)


_REPO_TXN_LOCK = threading.Lock()
_PUSH_LOCK = threading.Lock()

# Push scheduling/coalescing helpers
_PUSH_SCHED_LOCK = threading.Lock()
_GIT_LAST_PUSH: dict[str, float] = {}
_PUSH_TIMERS: dict[str, threading.Timer] = {}


def _push_worker(datarepo_path: Path) -> None:
    t0 = time.time()
    _dgit('async push: start')
    try:
        with _PUSH_LOCK:
            ok = git_push(datarepo_path)
    except Exception:
        ok = False
    finally:
        dt = int((time.time() - t0) * 1000)
        # Update last-push time on success and clear any scheduled timer for this repo
        try:
            repo = str(datarepo_path)
            if ok:
                _GIT_LAST_PUSH[repo] = time.time()
            with _PUSH_SCHED_LOCK:
                _PUSH_TIMERS.pop(repo, None)
        except Exception:
            pass
        _dgit(f'async push: done {dt}ms ok={ok}')


def _spawn_async_push(datarepo_path: Path) -> None:
    try:
        th = threading.Thread(target=_push_worker, args=(datarepo_path,), daemon=True)
        th.start()
    except Exception:
        print('[smallFactory] Warning: failed to spawn async push')


def _get_push_ttl_sec() -> int:
    try:
        return int(os.environ.get('SF_GIT_PUSH_TTL_SEC', '0') or '0')
    except Exception:
        return 0


def _schedule_delayed_push(datarepo_path: Path) -> None:
    """Schedule a delayed background push to coalesce frequent mutations.

    Respects SF_GIT_PUSH_TTL_SEC. If enough time has already elapsed since the
    last successful push, this will spawn an immediate async push; otherwise it
    schedules a timer to fire after the remaining delay. Multiple calls within
    the TTL window will coalesce into a single pending timer.
    """
    ttl = _get_push_ttl_sec()
    if ttl <= 0:
        _spawn_async_push(datarepo_path)
        return
    repo = str(datarepo_path)
    now = time.time()
    last = _GIT_LAST_PUSH.get(repo, 0.0)
    delay = max(0.0, (last + ttl) - now)
    with _PUSH_SCHED_LOCK:
        existing = _PUSH_TIMERS.get(repo)
        if existing and existing.is_alive():
            # A push is already scheduled; let it run
            _dgit(f'push: coalesced; existing timer pending (~{int(delay*1000)}ms left)')
            return
        if delay <= 0:
            _dgit('push: TTL elapsed; pushing now (async)')
            # Spawn immediate async push
            _spawn_async_push(datarepo_path)
            return
        # Schedule a new timer to push after remaining TTL
        _dgit(f'push: scheduled in {int(delay*1000)}ms')
        t = threading.Timer(delay, _push_worker, args=(datarepo_path,))
        try:
            t.daemon = True  # type: ignore[attr-defined]
        except Exception:
            pass
        _PUSH_TIMERS[repo] = t
        t.start()


def _flush_pending_pushes_on_exit() -> None:
    """Flush any scheduled delayed pushes on process exit.

    Cancels timers and performs a final synchronous push per pending repo to
    avoid losing pushes when using SF_GIT_PUSH_TTL_SEC.
    """
    try:
        if not _autopush_enabled():
            return
        with _PUSH_SCHED_LOCK:
            items = list(_PUSH_TIMERS.items())
            _PUSH_TIMERS.clear()
        if not items:
            _dgit('shutdown: no pending push timers')
            return
        for repo, timer in items:
            try:
                timer.cancel()
            except Exception:
                pass
            try:
                path = Path(repo)
                _dgit('shutdown: flushing pending push')
                t0 = time.time()
                with _PUSH_LOCK:
                    ok = git_push(path)
                _dgit(f'shutdown: push done {int((time.time()-t0)*1000)}ms ok={ok}')
                if ok:
                    _GIT_LAST_PUSH[repo] = time.time()
            except Exception:
                try:
                    print('[smallFactory] Warning: shutdown push failed', file=sys.stderr)
                except Exception:
                    pass
    except Exception:
        # Be silent during interpreter teardown
        pass


# Register process-exit hook
atexit.register(_flush_pending_pushes_on_exit)


def _compute_git_metrics(datarepo_path: Path) -> dict:
    """Gather Git-related metrics for the repository at datarepo_path.

    All operations are read-only and tolerant of non-git directories or errors.
    Returns a dict with keys: is_repo, branch, commits, latest, remotes, status.
    """
    repo = str(datarepo_path)
    result: dict = {
        'is_repo': False,
        'branch': None,
        'commits': 0,
        'latest': {
            'hash': None,
            'short': None,
            'date': None,
            'author': None,
            'email': None,
            'subject': None,
        },
        'remotes': {
            'count': 0,
            'has_origin': False,
            'origin_url': None,
        },
        'status': {
            'changed': 0,
            'untracked': 0,
            'ahead': 0,
            'behind': 0,
        },
    }

    try:
        ck = subprocess.run(['git', '-C', repo, 'rev-parse', '--is-inside-work-tree'], capture_output=True, text=True)
        if ck.returncode != 0:
            return result
        result['is_repo'] = True

        # Current branch name (best-effort)
        br = subprocess.run(['git', '-C', repo, 'rev-parse', '--abbrev-ref', 'HEAD'], capture_output=True, text=True)
        if br.returncode == 0:
            result['branch'] = (br.stdout or '').strip() or None

        # Total commits (across all refs for a quick approximation)
        cm = subprocess.run(['git', '-C', repo, 'rev-list', '--count', '--all'], capture_output=True, text=True)
        if cm.returncode == 0:
            try:
                result['commits'] = int((cm.stdout or '0').strip() or '0')
            except Exception:
                result['commits'] = 0

        # Latest commit metadata skipped (UI no longer displays it)

        # Remotes info
        rm = subprocess.run(['git', '-C', repo, 'remote'], capture_output=True, text=True)
        if rm.returncode == 0:
            names = [(ln or '').strip() for ln in (rm.stdout or '').splitlines() if (ln or '').strip()]
            has_origin = ('origin' in names)
            origin_url = None
            if has_origin:
                try:
                    rurl = subprocess.run(['git', '-C', repo, 'remote', 'get-url', 'origin'], capture_output=True, text=True)
                    if rurl.returncode == 0:
                        origin_url = (rurl.stdout or '').strip() or None
                except Exception:
                    origin_url = None
            result['remotes'] = {
                'count': len(names),
                'has_origin': has_origin,
                'origin_url': origin_url,
            }

        # Working tree status counts
        st = subprocess.run(['git', '-C', repo, 'status', '--porcelain'], capture_output=True, text=True)
        if st.returncode == 0:
            changed = 0
            untracked = 0
            for ln in (st.stdout or '').splitlines():
                s = (ln or '').rstrip('\n')
                if not s:
                    continue
                if s.startswith('?? '):
                    untracked += 1
                else:
                    changed += 1
            result['status']['changed'] = changed
            result['status']['untracked'] = untracked

        # Upstream ahead/behind (best-effort)
        up = subprocess.run(['git', '-C', repo, 'rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}'], capture_output=True, text=True)
        if up.returncode == 0:
            ab = subprocess.run(['git', '-C', repo, 'rev-list', '--left-right', '--count', '@{u}...HEAD'], capture_output=True, text=True)
            if ab.returncode == 0:
                try:
                    left_right = (ab.stdout or '').strip().split()
                    behind = int(left_right[0]) if len(left_right) > 0 else 0
                    ahead = int(left_right[1]) if len(left_right) > 1 else 0
                    result['status']['ahead'] = ahead
                    result['status']['behind'] = behind
                except Exception:
                    pass
    except Exception:
        # Return partials collected so far; never raise
        return result

    return result


def _run_repo_txn(datarepo_path: Path, mutate_fn, *, autocommit_message: str | None = None, autocommit_paths: List[str] | None = None):
    """Serialize repo mutations with: safe pull -> mutate -> autocommit -> conditional push."""
    if _git_disabled():
        return mutate_fn()
    need_async_push = False
    schedule_delayed = False
    with _REPO_TXN_LOCK:
        ok, err = _safe_git_pull(datarepo_path)
        if not ok:
            raise RuntimeError(f"Pre-mutation git pull failed: {err}")
        # Extract per-request identity from proxy headers (if present)
        name, email = _extract_identity_from_headers(request)
        def _do_mutate_and_autocommit():
            r = mutate_fn()
            # Ensure a commit exists if web autocommit is enabled and paths provided
            if autocommit_paths:
                _maybe_git_autocommit(datarepo_path, autocommit_message or '[smallFactory][web] Autocommit', autocommit_paths)
            return r
        if name and email:
            with _with_git_identity(name, email):
                result = _do_mutate_and_autocommit()
        else:
            result = _do_mutate_and_autocommit()
        # Conditional push
        if _autopush_enabled():
            ttl = _get_push_ttl_sec()
            if ttl and ttl > 0:
                # Defer push to batch within TTL window (always async)
                schedule_delayed = True
            else:
                if _autopush_async_enabled():
                    need_async_push = True
                else:
                    try:
                        t0 = time.time()
                        with _PUSH_LOCK:
                            okp = git_push(datarepo_path)
                        _dgit(f'sync push: done {int((time.time()-t0)*1000)}ms ok={okp}')
                        if okp:
                            _GIT_LAST_PUSH[str(datarepo_path)] = time.time()
                    except Exception:
                        # Non-fatal; leave a warning via stderr for logs
                        print('[smallFactory] Warning: autopush failed')
    # If async push is enabled, run it after releasing the txn lock
    if need_async_push:
        _spawn_async_push(datarepo_path)
    if schedule_delayed:
        _schedule_delayed_push(datarepo_path)
    return result


# -----------------------
# Dashboard metrics computation
# -----------------------
def _parse_iso_ts(*values) -> str:
    """Return the first non-empty ISO-like timestamp string from values; fallback to ''.

    We do not parse to datetime to avoid tz pitfalls in templates; lexical sort is ok for ISO.
    """
    for v in values:
        # Treat None/null-like as empty
        if v is None:
            continue
        try:
            s = str(v).strip()
        except Exception:
            s = ""
        if not s:
            continue
        if s.lower() in {"none", "null"}:
            continue
        return s
    return ""


def compute_dashboard_metrics(datarepo_path: Path, *, top_n: int = 5) -> dict:
    """Compute unified dashboard metrics using only public core APIs.

    Returns a dict with keys: inventory, parts, revisions, builds, pipeline.
    """
    # Inventory summary (from caches; computes missing from journals as needed)
    inv_summary = {}
    try:
        inv_summary = inventory_onhand_readonly(datarepo_path) or {}
    except Exception:
        inv_summary = {}
    inv_parts = list(inv_summary.get('parts') or [])
    inv_total_qty = int(inv_summary.get('total', 0) or 0)

    # Entities
    try:
        ents = list_entities(datarepo_path) or []
    except Exception:
        ents = []
    parts = [e for e in ents if str(e.get('sfid', '')).startswith('p_')]
    builds = [e for e in ents if str(e.get('sfid', '')).startswith('b_')]

    # Inventory metrics
    inv_map = {p.get('sfid'): int(p.get('total', 0) or 0) for p in inv_parts if p.get('sfid')}
    parts_total = len(parts)
    parts_in_stock = 0
    parts_zero_stock = 0
    for p in parts:
        sfid = p.get('sfid')
        qty = int(inv_map.get(sfid, 0) or 0)
        if qty > 0:
            parts_in_stock += 1
        else:
            parts_zero_stock += 1
    # Precompute stock coverage percent for UI (avoid heavy inline template math)
    try:
        stock_coverage_pct = int(parts_in_stock * 100 // (parts_total or 1))
    except Exception:
        stock_coverage_pct = 0

    # Top stock items (by quantity desc) with names
    top_stock = sorted(inv_parts, key=lambda x: int(x.get('total', 0) or 0), reverse=True)[:top_n]
    inv_top = []
    for item in top_stock:
        sfid = item.get('sfid')
        name = sfid
        try:
            ent = get_entity(datarepo_path, sfid)
            name = ent.get('name', sfid)
        except Exception:
            pass
        inv_top.append({
            'sfid': sfid,
            'name': name,
            'total': int(item.get('total', 0) or 0),
            'uom': item.get('uom', 'ea') or 'ea',
        })

    # Revisions metrics
    rev_total = 0
    rev_released = 0
    rev_drafts = 0
    parts_with_released = 0
    recent_revs = []
    for p in parts:
        sfid = p.get('sfid')
        try:
            info = get_revisions(datarepo_path, sfid) or {}
        except Exception:
            info = {}
        if info.get('rev'):
            parts_with_released += 1
        metas = list(info.get('revisions') or [])
        rev_total += len(metas)
        for m in metas:
            status = str(m.get('status', '')).lower()
            if status == 'released':
                rev_released += 1
            if status == 'draft':
                rev_drafts += 1
            created_at = _parse_iso_ts(m.get('created_at'), m.get('generated_at'))
            # Capture for recent list
            try:
                name = p.get('name') or get_entity(datarepo_path, sfid).get('name', sfid)
            except Exception:
                name = sfid
            recent_revs.append({
                'sfid': sfid,
                'name': name,
                'rev': m.get('id') or m.get('rev'),
                'status': status or None,
                'created_at': created_at,
            })

    # Sort recent revisions by created_at desc (ISO timestamps sort lexicographically)
    recent_revs = sorted(
        [r for r in recent_revs if r.get('created_at')],
        key=lambda r: r.get('created_at'),
        reverse=True,
    )[:top_n]

    # Builds metrics
    builds_total = len(builds)
    by_status: dict[str, int] = {}
    units_built = 0
    recent_builds = []
    for b in builds:
        sfid = b.get('sfid')
        status = str(b.get('status', 'unknown') or 'unknown').lower()
        by_status[status] = by_status.get(status, 0) + 1
        units = b.get('units')
        try:
            units_built += len(units) if isinstance(units, list) else 1
        except Exception:
            units_built += 1
        # name (best-effort)
        try:
            name = b.get('name') or get_entity(datarepo_path, sfid).get('name', sfid)
        except Exception:
            name = sfid
        opened_at = _parse_iso_ts(b.get('opened_at'), b.get('created_at'), b.get('datetime'))
        closed_at = _parse_iso_ts(b.get('closed_at'))
        sort_ts = _parse_iso_ts(closed_at, opened_at)
        recent_builds.append({
            'sfid': sfid,
            'name': name,
            'status': status,
            'units_count': (len(units) if isinstance(units, list) else 1) if units is not None else 1,
            'opened_at': opened_at or None,
            'closed_at': closed_at or None,
            'sort_ts': sort_ts,
        })

    recent_builds = sorted(
        [x for x in recent_builds if x.get('sort_ts')],
        key=lambda x: x.get('sort_ts'),
        reverse=True,
    )[:top_n]

    metrics = {
        'inventory': {
            'total_quantity': inv_total_qty,
            'parts_with_stock': parts_in_stock,
            'parts_zero_stock': parts_zero_stock,
            'top_stock': inv_top,
            'stock_coverage_pct': stock_coverage_pct,
        },
        'parts': {
            'total': parts_total,
            'with_released': parts_with_released,
            'without_released': max(0, parts_total - parts_with_released),
        },
        'revisions': {
            'total': rev_total,
            'released': rev_released,
            'drafts': rev_drafts,
            'recent': recent_revs,
        },
        'builds': {
            'total': builds_total,
            'by_status': by_status,
            'units_built': units_built,
            'recent': recent_builds,
        },
        'pipeline': {
            'parts_total': parts_total,
            'revisions_total': rev_total,
            'builds_total': builds_total,
        },
    }
    return metrics

# -----------------------
# Repository stats and validation
# -----------------------

def _compute_repo_sizes(datarepo_path: Path) -> dict:
    """Scan key repository areas and compute file counts and total sizes.

    Returns dict:
      {
        'root': str,
        'entities': {'path': str, 'files': int, 'bytes': int},
        'inventory': {'path': str, 'files': int, 'bytes': int},
        'journals': {'path': str, 'files': int, 'bytes': int},
        'config': {
            'sfdatarepo_yml': {'path': str, 'exists': bool, 'bytes': int},
            'gitattributes': {'path': str, 'exists': bool, 'bytes': int},
        },
        'total_bytes': int,
      }
    """
    # Keep a min-heap of the largest files across key areas
    top_n = 10
    largest_heap = []  # (size, path, area, mtime)

    def scan_dir(rel: str) -> dict:
        base = datarepo_path / rel
        count = 0
        total = 0
        if base.exists():
            for root, _, files in os.walk(base):
                for fname in files:
                    try:
                        p = Path(root) / fname
                        st = p.stat()
                        size = int(st.st_size)
                        mtime = float(st.st_mtime)
                        count += 1
                        total += size
                        # Track largest files across areas using a min-heap
                        heapq.heappush(largest_heap, (size, str(p), rel, mtime))
                        if len(largest_heap) > top_n:
                            heapq.heappop(largest_heap)
                    except Exception:
                        # Ignore unreadable entries
                        pass
        return {
            'path': str(base),
            'files': int(count),
            'bytes': int(total),
        }

    entities = scan_dir('entities')
    inventory = scan_dir('inventory')
    journals = scan_dir('journals')

    cfg_path = datarepo_path / DATAREPO_CONFIG_FILENAME
    cfg_bytes = 0
    if cfg_path.exists():
        try:
            cfg_bytes = int(cfg_path.stat().st_size)
        except Exception:
            cfg_bytes = 0
    gitattr_path = datarepo_path / '.gitattributes'
    gitattr_bytes = 0
    if gitattr_path.exists():
        try:
            gitattr_bytes = int(gitattr_path.stat().st_size)
        except Exception:
            gitattr_bytes = 0

    total_bytes = int(entities['bytes'] + inventory['bytes'] + journals['bytes'] + cfg_bytes + gitattr_bytes)


    # Compute total size of the entire data repo directory (includes .git and all files)
    repo_dir_bytes = 0
    repo_dir_bytes_on_disk = 0
    try:
        for root, _, files in os.walk(datarepo_path):
            for fname in files:
                try:
                    p = Path(root) / fname
                    st = p.stat()
                    size = int(st.st_size)
                    repo_dir_bytes += size
                    # Prefer allocated size on disk if available (POSIX st_blocks * 512)
                    blocks = getattr(st, 'st_blocks', None)
                    if blocks is not None and int(blocks) > 0:
                        repo_dir_bytes_on_disk += int(blocks) * 512
                    else:
                        # Fallback: approximate with logical size when blocks is unavailable
                        repo_dir_bytes_on_disk += size
                except Exception:
                    # Ignore unreadable entries
                    pass
    except Exception:
        repo_dir_bytes = 0
        repo_dir_bytes_on_disk = 0

    # Materialize largest files in descending order
    largest = sorted(largest_heap, key=lambda t: t[0], reverse=True)
    largest_files = []
    for size, path, area, mtime in largest:
        try:
            mod = datetime.fromtimestamp(mtime).isoformat()
        except Exception:
            mod = None
        largest_files.append({'path': path, 'bytes': int(size), 'area': area, 'modified': mod})

    # Git repository metrics (best-effort; tolerant of non-git dirs)
    git_info = _compute_git_metrics(datarepo_path)

    return {
        'root': str(datarepo_path),
        'entities': entities,
        'inventory': inventory,
        'journals': journals,
        'config': {
            'sfdatarepo_yml': {
                'path': str(cfg_path),
                'exists': bool(cfg_path.exists()),
                'bytes': int(cfg_bytes),
            },
            'gitattributes': {
                'path': str(gitattr_path),
                'exists': bool(gitattr_path.exists()),
                'bytes': int(gitattr_bytes),
            },
        },
        'total_bytes': int(total_bytes),
        'repo_dir_bytes': int(repo_dir_bytes),
        'repo_dir_bytes_on_disk': int(repo_dir_bytes_on_disk),
        'largest_files': largest_files,
        'git': git_info,
    }


@app.route('/repo/stats', methods=['GET'])
def repo_stats():
    """Render repository stats page."""
    try:
        datarepo_path = get_datarepo_path()
        stats = _compute_repo_sizes(datarepo_path)
        return render_template('repo/stats.html', stats=stats, datarepo_path=str(datarepo_path))
    except Exception as e:
        return render_template('error.html', error=str(e))


@app.route('/repo/stats/validate', methods=['POST'])
def repo_stats_validate():
    """Run repository validation and return JSON results."""
    try:
        datarepo_path = get_datarepo_path()
        payload = request.get_json(silent=True) or {}
        # Fallback to form values if provided
        def as_bool(val, default=True):
            if val is None:
                return bool(default)
            try:
                s = str(val).strip().lower()
            except Exception:
                return bool(default)
            return s in ('1', 'true', 'yes', 'on')

        include_entities = as_bool(payload.get('include_entities', request.form.get('include_entities')))
        include_inventory = as_bool(payload.get('include_inventory', request.form.get('include_inventory')))
        include_git = as_bool(payload.get('include_git', request.form.get('include_git')))
        limit_raw = payload.get('git_commit_limit', request.form.get('git_commit_limit'))
        git_commit_limit = 200
        try:
            if limit_raw is not None and str(limit_raw).strip() != '':
                git_commit_limit = int(limit_raw)
        except Exception:
            git_commit_limit = 200

        result = validate_repo(
            datarepo_path,
            include_entities=include_entities,
            include_inventory=include_inventory,
            include_git=include_git,
            git_commit_limit=git_commit_limit,
        )
        return jsonify({'success': True, 'result': result})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

@app.route('/')
def index():
    """Main dashboard showing overview of the system."""
    try:
        datarepo_path = get_datarepo_path()
        metrics = compute_dashboard_metrics(datarepo_path, top_n=5)
        return render_template(
            'index.html',
            metrics=metrics,
            datarepo_path=str(datarepo_path)
        )
    except Exception as e:
        return render_template('error.html', error=str(e))

@app.route('/vision', methods=['GET'])
def vision_page():
    """Mobile-friendly page to capture/upload an image and extract part info."""
    return render_template('vision.html')

@app.route('/inventory')
def inventory_list():
    """Display all inventory items in a table."""
    try:
        datarepo_path = get_datarepo_path()
        # Inventory summary (existing parts with journals/caches)
        summary = inventory_onhand_readonly(datarepo_path)
        summary_parts = summary.get('parts', []) if isinstance(summary, dict) else []
        inv_totals = {p.get('sfid'): int(p.get('total', 0) or 0) for p in summary_parts if p.get('sfid')}

        # All canonical part entities
        entities = list_entities(datarepo_path) or []
        part_entities = [e for e in entities if str(e.get('sfid', '')).startswith('p_')]

        # Build items list: include every part, defaulting to zero if absent from inventory
        items = []
        for ent in part_entities:
            sfid = ent.get('sfid')
            if not sfid:
                continue
            name = ent.get('name', sfid)
            description = ent.get('description', '')
            category = ent.get('category', '')

            if sfid in inv_totals:
                # Populate full cache details (by-location, uom, as_of) for parts that have inventory
                try:
                    cache = inventory_onhand_readonly(datarepo_path, part=sfid)
                except Exception:
                    cache = {}
                uom = cache.get('uom', ent.get('uom', 'ea') or 'ea')
                total = int(cache.get('total', 0) or 0)
                by_location = cache.get('by_location', {}) or {}
                as_of = cache.get('as_of')
            else:
                # Zero-quantity default for parts without any inventory activity
                uom = ent.get('uom', 'ea') or 'ea'
                total = 0
                by_location = {}
                as_of = None

            items.append({
                'sfid': sfid,
                'name': name,
                'description': description,
                'category': category,
                'uom': uom,
                'total': total,
                'by_location': by_location,
                'as_of': as_of,
            })
        # Optional pre-filtering from dashboard drill-downs
        status = (request.args.get('status') or '').strip().lower()
        if status == 'in_stock':
            items = [it for it in items if int(it.get('total') or 0) > 0]
        elif status in ('zero_stock', 'out_of_stock'):
            items = [it for it in items if int(it.get('total') or 0) <= 0]
        field_specs = get_inventory_field_specs()
        return render_template('inventory/list.html', items=items, field_specs=field_specs, filter_status=status)
    except Exception as e:
        return render_template('error.html', error=str(e))

@app.route('/inventory/<item_id>')
def inventory_view(item_id):
    """View details of a specific inventory item."""
    try:
        datarepo_path = get_datarepo_path()
        cache = inventory_onhand_readonly(datarepo_path, part=item_id)
        # Combine with entity metadata for UX if desired
        entity = get_entity(datarepo_path, item_id)
        field_specs = get_inventory_field_specs()
        item = {
            "sfid": item_id,
            "name": entity.get("name", item_id),
            "description": entity.get("description", ""),
            "category": entity.get("category", ""),
            "uom": cache.get("uom"),
            "total": cache.get("total", 0),
            "by_location": cache.get("by_location", {}),
            "as_of": cache.get("as_of"),
        }
        return render_template('inventory/view.html', item=item, field_specs=field_specs)
    except Exception as e:
        flash(f'Error viewing item: {e}', 'error')
        return redirect(url_for('inventory_list'))

@app.route('/inventory/<item_id>/edit', methods=['GET', 'POST'])
def inventory_edit(item_id):
    """Inventory no longer edits canonical entity metadata per SPEC.

    Redirect users to the item view with an explanatory message.
    """
    try:
        datarepo_path = get_datarepo_path()
        # Ensure item exists for a nicer redirect target
        _ = get_entity(datarepo_path, item_id)
        flash('Editing entity metadata is handled by the Entities module. Inventory only manages quantities per location.', 'error')
        return redirect(url_for('inventory_view', item_id=item_id))
    except Exception as e:
        flash(f'Error loading item: {e}', 'error')
        return redirect(url_for('inventory_list'))

@app.route('/inventory/<item_id>/delete', methods=['POST'])
def inventory_delete(item_id):
    """Delete an inventory item."""
    try:
        # Journal model does not support deleting inventory items; they are derived from journals
        flash('Deleting inventory items is not supported in the journal model. Use negative adjustments instead.', 'error')
        return redirect(url_for('inventory_view', item_id=item_id))
    except Exception as e:
        flash(f'Error deleting item: {e}', 'error')
        return redirect(url_for('inventory_view', item_id=item_id))
# -------------------------------
# Mobile quick adjust (QR-friendly)
@app.route('/inventory/adjust', methods=['GET', 'POST'])
def inventory_adjust():
    """Quick Adjust page using absolute quantity (compute delta server-side).

    - GET: optionally prefill sfid/location; show current qty if available
    - POST: accept absolute quantity, compute delta, apply inventory_post
    """
    field_specs = get_inventory_field_specs()
    form_data = {}
    current_qty = None

    if request.method == 'GET':
        pre_sfid = (request.args.get('sfid') or '').strip()
        pre_l_sfid = (request.args.get('l_sfid') or '').strip() or (request.args.get('location') or '').strip()
        pre_qty = (request.args.get('quantity') or '').strip()
        if pre_sfid:
            form_data['sfid'] = pre_sfid
        if pre_l_sfid:
            form_data['l_sfid'] = pre_l_sfid
        # Determine current qty for initial display
        if pre_sfid:
            try:
                datarepo_path = get_datarepo_path()
                cache = inventory_onhand_readonly(datarepo_path, part=pre_sfid)
                by_loc = cache.get('by_location', {}) or {}
                # Resolve default location if not provided
                loc = pre_l_sfid or (load_datarepo_config(datarepo_path).get('inventory', {}) or {}).get('default_location')
                if loc:
                    try:
                        current_qty = int(by_loc.get(loc, 0) or 0)
                    except Exception:
                        current_qty = 0
                else:
                    current_qty = None
            except Exception:
                current_qty = None
        if pre_qty:
            form_data['quantity'] = pre_qty
        elif current_qty is not None:
            form_data['quantity'] = current_qty

    if request.method == 'POST':
        # Preserve form values on error for re-display
        form_data = {k: v for k, v in request.form.items() if str(v).strip()}
        try:
            sfid = (request.form.get('sfid') or '').strip()
            # Canonical field name is l_sfid; support legacy 'location' as fallback
            location = (request.form.get('l_sfid') or '').strip() or (request.form.get('location') or '').strip() or None
            qty_raw = (request.form.get('quantity') or '').strip()
            if not sfid:
                raise ValueError('Missing required field: sfid')
            if qty_raw == '':
                raise ValueError('Missing required field: quantity')
            try:
                new_qty = int(qty_raw)
            except Exception:
                raise ValueError('quantity must be an integer >= 0')
            if new_qty < 0:
                raise ValueError('quantity must be >= 0')
            # Optional reason passthrough for auditability
            reason = (request.form.get('reason') or '').strip() or None

            datarepo_path = get_datarepo_path()
            # Compute current quantity at target location (resolving default if needed)
            cache = inventory_onhand_readonly(datarepo_path, part=sfid)
            by_loc = cache.get('by_location', {}) or {}
            loc = location or (load_datarepo_config(datarepo_path).get('inventory', {}) or {}).get('default_location')
            if not loc:
                raise ValueError('location is required (or set sfdatarepo.yml: inventory.default_location)')
            try:
                cur_qty = int(by_loc.get(loc, 0) or 0)
            except Exception:
                cur_qty = 0
            delta = int(new_qty - cur_qty)
            if delta == 0:
                # No change to apply
                current_qty = cur_qty
                flash('No change: quantity unchanged.', 'info')
                # fall through to re-render form with info
            else:
                def _mutate():
                    return inventory_post(datarepo_path, sfid, delta, loc, reason=reason)
                _ = _run_repo_txn(
                    datarepo_path,
                    _mutate,
                )
                flash(f"Set '{sfid}' at {loc} to {new_qty} (Δ {delta})", 'success')
                return redirect(url_for('inventory_view', item_id=sfid))
        except Exception as e:
            flash(f'Error adjusting quantity: {e}', 'error')
            # fall through to re-render form with previous values

        # Compute current qty for redisplay if possible
        try:
            sfid = form_data.get('sfid')
            l_sfid = form_data.get('l_sfid') or form_data.get('location')
            if sfid:
                datarepo_path = get_datarepo_path()
                cache = inventory_onhand_readonly(datarepo_path, part=sfid)
                by_loc = cache.get('by_location', {}) or {}
                loc = l_sfid or (load_datarepo_config(datarepo_path).get('inventory', {}) or {}).get('default_location')
                if loc:
                    current_qty = int(by_loc.get(loc, 0) or 0)
        except Exception:
            pass

    return render_template('inventory/adjust.html', field_specs=field_specs, form_data=form_data, current_qty=current_qty)

# -------------------------------
# Entities module (canonical metadata)
# -------------------------------

@app.route('/entities')
def entities_list():
    """Display all canonical entities."""
    try:
        datarepo_path = get_datarepo_path()
        entities = list_entities(datarepo_path) or []
        # Optional type pre-filter (?type=p to show only parts, etc.)
        ftype = (request.args.get('type') or '').strip().lower()
        if ftype and len(ftype) == 1 and ftype.isalpha():
            prefix = f"{ftype}_"
            entities = [e for e in entities if str(e.get('sfid', '')).startswith(prefix)]
        return render_template('entities/list.html', entities=entities, filter_type=ftype)
    except Exception as e:
        return render_template('error.html', error=str(e))


@app.route('/entities/<sfid>')
def entities_view(sfid):
    """View a specific entity's canonical metadata."""
    try:
        datarepo_path = get_datarepo_path()
        entity = get_entity(datarepo_path, sfid)
        # Released revision label (if any)
        released_rev = None
        try:
            info = get_revisions(datarepo_path, sfid)
            released_rev = (info.get('rev') or '').strip() or None
        except Exception:
            pass

        # Enrich BOM for display (if present and valid)
        bom_rows = []
        bom = entity.get('bom')
        if isinstance(bom, list):
            for line in bom:
                if not isinstance(line, dict):
                    continue
                use = str(line.get('use', '')).strip()
                if not use:
                    continue
                qty = line.get('qty', 1) or 1
                rev = line.get('rev', 'released') or 'released'
                # Resolve child name best-effort
                child_name = use
                try:
                    child = get_entity(datarepo_path, use)
                    child_name = child.get('name', use)
                except Exception:
                    pass
                alternates = []
                if isinstance(line.get('alternates'), list):
                    for alt in line['alternates']:
                        if isinstance(alt, dict) and alt.get('use'):
                            alternates.append(str(alt.get('use')))
                alternates_group = line.get('alternates_group')
                try:
                    qty_disp = int(qty)
                except Exception:
                    qty_disp = qty
                bom_rows.append({
                    'use': use,
                    'name': child_name,
                    'qty': qty_disp,
                    'rev': rev,
                    'alternates': alternates,
                    'alternates_group': alternates_group,
                })

        # Inventory on-hand for this entity (if part)
        inv_cache = {}
        try:
            inv = inventory_onhand_readonly(datarepo_path, part=sfid)
            if isinstance(inv, dict):
                inv_cache = {
                    'uom': inv.get('uom'),
                    'total': inv.get('total', 0),
                    'by_location': inv.get('by_location', {}) or {},
                    'as_of': inv.get('as_of'),
                }
        except Exception:
            inv_cache = {}

        return render_template('entities/view.html', entity=entity, bom_rows=bom_rows, released_rev=released_rev, inv=inv_cache)
    except Exception as e:
        flash(f'Error viewing entity: {e}', 'error')
        return redirect(url_for('entities_list'))

@app.route('/entities/<sfid>/bom-tree')
def entities_bom_tree(sfid):
    """Dedicated page to display the deep BOM tree for a product entity.

    Server-side renders the hierarchical tree and provides a CSV download link.
    """
    try:
        datarepo_path = get_datarepo_path()
        entity = get_entity(datarepo_path, sfid)
        # Only meaningful for parts/products, but allow graceful render for others
        nodes = _walk_bom_deep(datarepo_path, sfid, max_depth=None)
        return render_template('entities/bom_tree.html', entity=entity, nodes=nodes)
    except Exception as e:
        flash(f'Error loading BOM tree: {e}', 'error')
        return redirect(url_for('entities_view', sfid=sfid))


@app.route('/entities/<sfid>/bom/import')
def entities_bom_import(sfid):
    """Dedicated BOM CSV import page."""
    try:
        datarepo_path = get_datarepo_path()
        entity = get_entity(datarepo_path, sfid)
        return render_template('entities/bom_import.html', entity=entity)
    except Exception as e:
        flash(f'Error loading BOM import page: {e}', 'error')
        return redirect(url_for('entities_view', sfid=sfid))


@app.route('/entities/<sfid>/build', methods=['GET', 'POST'])
def entities_build(sfid):
    """Quick Build flow for finished goods (p_* entities).

    - GET without qty: render form
    - GET with ?qty=...: compute preview and auto-open confirmation modal
    - POST: perform backflush (consume integer-qty BOM lines) and add FG quantity
    """
    try:
        datarepo_path = get_datarepo_path()

        # Ensure entity exists and is a product-like entity
        entity = get_entity(datarepo_path, sfid)
        is_product = bool(sfid and sfid.startswith('p_'))

        # Determine revisions info for this part (released pointer + list)
        released_rev = None
        revisions = []
        try:
            info = get_revisions(datarepo_path, sfid)
            released_rev = info.get('rev')
            revisions = info.get('revisions', [])
        except Exception:
            pass
        can_build = bool(revisions)

        # Simplified build flow: no backflush/consumption preview.

        # Extract inputs
        if request.method == 'POST':
            if not is_product:
                flash('Build is only available for product entities (sfid starts with p_)', 'error')
                return redirect(url_for('entities_view', sfid=sfid))

            l_sfid = (request.form.get('l_sfid') or '').strip() or None
            notes = (request.form.get('notes') or '').strip()
            rev_sel = (request.form.get('rev') or '').strip()
            # Guard: do not allow build if no revisions exist
            try:
                info_check = get_revisions(datarepo_path, sfid)
                if not info_check.get('revisions'):
                    flash('Cannot build: no revisions exist for this part. Create a revision first.', 'error')
                    return redirect(url_for('entities_build', sfid=sfid))
            except Exception:
                flash('Cannot build: failed to read revisions for this part.', 'error')
                return redirect(url_for('entities_build', sfid=sfid))

            # Create a build record entity: b_<product>_<YYYYMMDDHHMMSS>
            _now = datetime.now()
            ts_label = _now.strftime('%Y%m%d%H%M%S')
            build_sfid = f"b_{sfid}_{ts_label}"
            ts_iso = _now.isoformat(timespec='seconds')
            fields = {
                'product_sfid': sfid,
                'created_at': ts_iso,
                'datetime': ts_iso,
                'serialnumber': ts_label,
                'name': f"Build {entity.get('name', sfid)}",
                'opened_at': ts_iso,
                'status': 'open',
            }
            # Resolve selected revision to a concrete label for traceability
            try:
                info = get_revisions(datarepo_path, sfid)
                current_released = info.get('rev')
            except Exception:
                current_released = None
            rev_label = None
            if rev_sel and rev_sel != 'released':
                rev_label = rev_sel
            elif current_released:
                rev_label = current_released
            if rev_label:
                fields['product_rev'] = rev_label
            if l_sfid:
                fields['l_sfid'] = l_sfid
            if notes:
                fields['notes'] = notes

            try:
                def _mutate():
                    return create_entity(datarepo_path, build_sfid, fields)
                _ = _run_repo_txn(
                    datarepo_path,
                    _mutate,
                    autocommit_message=f"[smallFactory][web] Create build record {build_sfid} for {sfid}",
                    autocommit_paths=[f"entities/{build_sfid}"]
                )
                flash(f"Created build record '{build_sfid}' for {sfid}", 'success')
                return redirect(url_for('entities_view', sfid=build_sfid))
            except Exception as e:
                flash(f"Failed to create build record: {e}", 'error')
                return redirect(url_for('entities_build', sfid=sfid))

        # GET: show form and optional preview if qty provided
        l_sfid = (request.args.get('l_sfid') or '').strip()
        notes = (request.args.get('notes') or '').strip()
        rev_selected = (request.args.get('rev') or ('released' if released_rev else '')).strip()
        # If no released pointer and no explicit selection, default to the latest revision id
        if not rev_selected and revisions:
            try:
                last = revisions[-1]
                rid = (last.get('id') if isinstance(last, dict) else None) or ''
                rev_selected = rid
            except Exception:
                pass

        return render_template(
            'entities/build.html',
            entity=entity,
            released_rev=released_rev,
            revisions=revisions,
            l_sfid=l_sfid,
            notes=notes,
            rev_selected=rev_selected,
            can_build=can_build,
            is_product=is_product,
        )
    except Exception as e:
        flash(f'Error loading build page: {e}', 'error')
        return redirect(url_for('entities_view', sfid=sfid))


@app.route('/entities/<sfid>/build/create-revision', methods=['POST'])
def entities_build_create_revision(sfid):
    """Create a new draft revision for the part and return to Build page.

    Uses bump_revision() to cut the next numeric revision label.
    """
    try:
        datarepo_path = get_datarepo_path()
        # Prefer explicit product_sfid from form, fallback to path param
        target = (request.form.get('product_sfid') or '').strip() or sfid
        if not (target and target.startswith('p_')):
            flash('Revisions are only supported on product entities (p_*)', 'error')
            return redirect(url_for('entities_build', sfid=sfid))
        # Ensure the entity exists
        try:
            get_entity(datarepo_path, target)
        except Exception:
            flash(f"Product '{target}' not found.", 'error')
            return redirect(url_for('entities_build', sfid=sfid))
        def _mutate():
            return bump_revision(datarepo_path, target)
        info = _run_repo_txn(
            datarepo_path,
            _mutate,
            autocommit_message=f"[smallFactory][web] Create draft revision for {target}",
            autocommit_paths=[f"entities/{target}"]
        )
        new_rev = info.get('new_rev') or ''
        if new_rev:
            flash(f"Created draft revision {new_rev} for {target}", 'success')
            return redirect(url_for('entities_build', sfid=target, rev=new_rev))
        else:
            flash('Created a new draft revision.', 'success')
            return redirect(url_for('entities_build', sfid=target))
    except Exception as e:
        flash(f'Failed to create revision: {e}', 'error')
        return redirect(url_for('entities_build', sfid=sfid))

@app.route('/entities/add', methods=['GET', 'POST'])
def entities_add():
    """Create a new canonical entity.

    Supports optional prefill via query string (?sfid=...) and safe return via
    ?next=<path>. If provided, 'next' is echoed back as a hidden field and used
    as the redirect target after successful creation.
    """
    from urllib.parse import urlparse, parse_qs, urlencode

    def _is_safe_next(url: str) -> bool:
        try:
            p = urlparse(url)
            # Only allow relative, same-origin paths (no scheme or netloc)
            return (p.scheme == '' and p.netloc == '' and (p.path or '/').startswith('/'))
        except Exception:
            return False

    form_data = {}
    next_url = None
    update_param = None  # which query param in 'next' should be updated with the final created SFID

    if request.method == 'GET':
        # Prefill from query args (e.g., coming from Adjust page)
        pre_sfid = request.args.get('sfid', '').strip()
        if pre_sfid:
            form_data['sfid'] = pre_sfid
        next_arg = request.args.get('next', '').strip()
        if next_arg and _is_safe_next(next_arg):
            next_url = next_arg
        up = request.args.get('update_param', '').strip()
        if up in ('sfid', 'l_sfid', 'location'):
            update_param = up

    if request.method == 'POST':
        form_data = {k: v for k, v in request.form.items() if str(v).strip()}
        sfid = form_data.get('sfid', '').strip()
        next_url = request.form.get('next', '').strip() or None
        update_param = (request.form.get('update_param', '').strip() or None)
        try:
            if not sfid:
                raise ValueError('sfid is required')
            # Build fields dict excluding sfid and 'next'
            fields = {k: v for k, v in form_data.items() if k not in ('sfid', 'next', 'update_param')}
            datarepo_path = get_datarepo_path()
            # Proactive existence check for better UX
            try:
                _ = get_entity(datarepo_path, sfid)
                flash(f"Entity '{sfid}' already exists. Choose a different SFID.", 'error')
                return render_template('entities/add.html', form_data=form_data, next_url=next_url, update_param=update_param)
            except FileNotFoundError:
                pass
            def _mutate():
                return create_entity(datarepo_path, sfid, fields)
            entity = _run_repo_txn(
                datarepo_path,
                _mutate,
                autocommit_message=f"[smallFactory][web] Create entity {sfid}",
                autocommit_paths=[f"entities/{sfid}"]
            )
            flash(f"Successfully created entity: {sfid}", 'success')
            # If the user clicked "Save & Create Another", return to add form
            action = (request.form.get('action') or '').strip()
            if action == 'create_another':
                # Carry over SFID type prefix (e.g., "p_") to the next create form
                prefix = ''
                try:
                    if sfid:
                        i = sfid.find('_')
                        if i > 0 and sfid[:i].isalpha():
                            prefix = sfid[:i+1].lower()
                except Exception:
                    prefix = ''
                if prefix:
                    return redirect(url_for('entities_add', sfid=prefix))
                return redirect(url_for('entities_add'))
            if next_url and _is_safe_next(next_url):
                # If caller indicated which param to update, rewrite the next URL
                try:
                    if update_param in ('sfid', 'l_sfid', 'location'):
                        parsed = urlparse(next_url)
                        qs = parse_qs(parsed.query)
                        qs[update_param] = [sfid]
                        new_qs = urlencode(qs, doseq=True)
                        next_url = parsed._replace(query=new_qs).geturl()
                except Exception:
                    pass
                return redirect(next_url)
            return redirect(url_for('entities_view', sfid=entity.get('sfid')))
        except Exception as e:
            flash(f'Error creating entity: {e}', 'error')
    return render_template('entities/add.html', form_data=form_data, next_url=next_url, update_param=update_param)


@app.route('/entities/<sfid>/edit', methods=['GET', 'POST'])
def entities_edit(sfid):
    """Deprecated: Inline editing is now supported on the entity view page."""
    try:
        # Ensure entity exists for nicer UX, but always redirect to view
        datarepo_path = get_datarepo_path()
        _ = get_entity(datarepo_path, sfid)
    except Exception:
        # fall through to redirect regardless
        pass
    flash('The Edit page has been removed. Use inline editing on the entity page.', 'info')
    return redirect(url_for('entities_view', sfid=sfid))


@app.route('/entities/<sfid>/retire', methods=['POST'])
def entities_retire(sfid):
    """Soft-delete an entity by marking it as retired."""
    try:
        datarepo_path = get_datarepo_path()
        reason = request.form.get('reason', '').strip() or None
        def _mutate():
            return retire_entity(datarepo_path, sfid, reason=reason)
        _ = _run_repo_txn(
            datarepo_path,
            _mutate,
            autocommit_message=f"[smallFactory][web] Retire entity {sfid}",
            autocommit_paths=[f"entities/{sfid}"]
        )
        flash('Entity retired successfully', 'success')
    except Exception as e:
        flash(f'Error retiring entity: {e}', 'error')
    return redirect(url_for('entities_view', sfid=sfid))

# API endpoints for AJAX requests
@app.route('/api/inventory')
def api_inventory_list():
    """API endpoint to get all inventory items as JSON."""
    try:
        datarepo_path = get_datarepo_path()
        items = list_items(datarepo_path)
        return jsonify({'success': True, 'items': items})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/inventory/<item_id>')
def api_inventory_view(item_id):
    """API endpoint to get a specific inventory item as JSON."""
    try:
        datarepo_path = get_datarepo_path()
        item = view_item(datarepo_path, item_id)
        return jsonify({'success': True, 'item': item})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 404

@app.route('/api/inventory/adjust', methods=['POST'])
def api_inventory_adjust():
    """Adjust inventory using delta or absolute quantity via JSON.

    Request JSON body:
      - sfid: part id (required)
      - l_sfid or location: location id/name (optional; defaults to repo inventory.default_location)
      - quantity: absolute non-negative integer (preferred)
        or
      - delta: signed integer

    Response JSON on success includes updated totals and by_location.
    """
    try:
        payload = request.get_json(silent=True) or {}
        sfid = str(payload.get('sfid', '')).strip()
        location = (str(payload.get('l_sfid', '')).strip() or str(payload.get('location', '')).strip() or None)
        if not sfid:
            return jsonify({'success': False, 'error': 'Missing required field: sfid'}), 400

        datarepo_path = get_datarepo_path()

        # Resolve target location (respect default if not provided)
        loc = location or (load_datarepo_config(datarepo_path).get('inventory', {}) or {}).get('default_location')
        if not loc:
            return jsonify({'success': False, 'error': 'location is required (or set sfdatarepo.yml: inventory.default_location)'}), 400

        # Determine delta
        delta = None
        if 'quantity' in payload and payload.get('quantity') is not None and str(payload.get('quantity')).strip() != '':
            try:
                new_qty = int(payload.get('quantity'))
            except Exception:
                return jsonify({'success': False, 'error': 'quantity must be an integer'}), 400
            if new_qty < 0:
                return jsonify({'success': False, 'error': 'quantity must be >= 0'}), 400
            cache = inventory_onhand_readonly(datarepo_path, part=sfid)
            by_loc = cache.get('by_location', {}) or {}
            try:
                cur_qty = int(by_loc.get(loc, 0) or 0)
            except Exception:
                cur_qty = 0
            delta = int(new_qty - cur_qty)
        else:
            try:
                delta = int(payload.get('delta', 0))
            except Exception:
                return jsonify({'success': False, 'error': 'delta must be an integer'}), 400

        if delta == 0:
            # No-op; return current state
            cache = inventory_onhand_readonly(datarepo_path, part=sfid)
            by_loc = cache.get('by_location', {}) or {}
            new_qty = int(by_loc.get(loc, 0) or 0)
            return jsonify({
                'success': True,
                'sfid': sfid,
                'l_sfid': loc,
                'delta': 0,
                'total': cache.get('total', 0),
                'by_location': by_loc,
                'new_qty': new_qty,
            })

        # Optional reason passthrough for auditability
        reason = payload.get('reason')
        try:
            reason = str(reason).strip()
        except Exception:
            reason = None
        if not reason:
            reason = None

        def _mutate():
            return inventory_post(datarepo_path, sfid, delta, loc, reason=reason)

        _ = _run_repo_txn(
            datarepo_path,
            _mutate,
        )

        cache = inventory_onhand_readonly(datarepo_path, part=sfid)
        by_loc = cache.get('by_location', {}) or {}
        new_qty = int(by_loc.get(loc, 0) or 0)
        return jsonify({
            'success': True,
            'sfid': sfid,
            'l_sfid': loc,
            'delta': delta,
            'total': cache.get('total', 0),
            'by_location': by_loc,
            'new_qty': new_qty,
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

@app.route('/api/inventory/onhand', methods=['GET'])
def api_inventory_onhand():
    """Return current on-hand info for a part and optional location.

    Query params:
      - sfid (required)
      - l_sfid or location (optional)
    """
    try:
        sfid = (request.args.get('sfid') or '').strip()
        if not sfid:
            return jsonify({'success': False, 'error': 'Missing required parameter: sfid'}), 400
        l_sfid = (request.args.get('l_sfid') or '').strip() or (request.args.get('location') or '').strip()
        datarepo_path = get_datarepo_path()
        cache = inventory_onhand_readonly(datarepo_path, part=sfid)
        by_loc = cache.get('by_location', {}) or {}
        # Resolve default location if not provided
        loc = l_sfid or (load_datarepo_config(datarepo_path).get('inventory', {}) or {}).get('default_location')
        loc_qty = None
        if loc:
            try:
                loc_qty = int(by_loc.get(loc, 0) or 0)
            except Exception:
                loc_qty = 0
        return jsonify({
            'success': True,
            'sfid': sfid,
            'l_sfid': loc,
            'uom': cache.get('uom'),
            'total': cache.get('total', 0),
            'by_location': by_loc,
            'location_qty': loc_qty,
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

@app.route('/api/entities')
def api_entities_list():
    try:
        datarepo_path = get_datarepo_path()
        entities = list_entities(datarepo_path)
        return jsonify({'success': True, 'entities': entities})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/entities/search')
def api_entities_search():
    """Search entities by query across sfid and name (case-insensitive).

    Query params:
    - q: search string (required; empty returns empty results)
    - type: optional type prefix (e.g., 'p' or 'l'); matches SFIDs starting with '<type>_'
    - limit: optional max results (default 10, max 50)
    """
    try:
        datarepo_path = get_datarepo_path()
        q = (request.args.get('q') or '').strip()
        # If no query provided, return empty result set (not an error for UX)
        if not q:
            return jsonify({'success': True, 'results': []})
        ql = q.lower()

        # Optional type filter based on SFID prefix before '_'
        type_raw = request.args.get('type')
        type_prefix = None
        if type_raw is not None:
            t = str(type_raw).strip().lower()
            if t.endswith('_'):
                t = t[:-1]
            if t:
                type_prefix = f"{t}_"

        # Optional limit with sane defaults and bounds
        limit_raw = request.args.get('limit')
        limit = 10
        if limit_raw is not None and str(limit_raw).strip() != '':
            try:
                limit = int(limit_raw)
            except Exception:
                limit = 10
        if limit < 1:
            limit = 1
        if limit > 50:
            limit = 50

        ents = list_entities(datarepo_path) or []
        results = []
        for e in ents:
            if not isinstance(e, dict):
                continue
            sfid = str(e.get('sfid', '')).strip()
            if not sfid:
                continue
            if type_prefix and not sfid.startswith(type_prefix):
                continue
            name = str(e.get('name', '')).strip()
            name_l = name.lower() if name else ''
            if (ql in sfid.lower()) or (name_l and (ql in name_l)):
                results.append({'sfid': sfid, 'name': name or sfid})

        results.sort(key=lambda x: x.get('sfid', ''))
        return jsonify({'success': True, 'results': results[:limit]})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/entities/<sfid>')
def api_entities_view(sfid):
    try:
        datarepo_path = get_datarepo_path()
        entity = get_entity(datarepo_path, sfid)
        return jsonify({'success': True, 'entity': entity})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 404

@app.route('/api/entities/<sfid>/update', methods=['POST'])
def api_entities_update(sfid):
    """Update fields for an existing entity via JSON. Returns updated entity.

    Accepts either a top-level object of fields to update, or {"updates": {...}}.
    """
    try:
        datarepo_path = get_datarepo_path()
        payload = request.get_json(force=True, silent=True) or request.form.to_dict(flat=True)
        if not isinstance(payload, dict):
            raise ValueError('Invalid payload')
        updates = payload.get('updates') if isinstance(payload.get('updates'), dict) else payload
        if not isinstance(updates, dict) or not updates:
            raise ValueError('No updates provided')
        # Disallow sfid mutation
        updates.pop('sfid', None)
        # Normalize tags if provided as a comma-separated string
        if 'tags' in updates and isinstance(updates['tags'], str):
            parts = [s.strip() for s in updates['tags'].split(',') if s.strip()]
            updates['tags'] = parts
        def _mutate():
            return update_entity_fields(datarepo_path, sfid, updates)
        updated = _run_repo_txn(
            datarepo_path,
            _mutate,
            autocommit_message=f"[smallFactory][web] Update entity {sfid} fields",
            autocommit_paths=[f"entities/{sfid}"]
        )
        return jsonify({'success': True, 'entity': updated})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

@app.route('/api/entities/<sfid>/revisions', methods=['GET'])
def api_revisions_get(sfid):
    try:
        datarepo_path = get_datarepo_path()
        info = get_revisions(datarepo_path, sfid)
        return jsonify({'success': True, 'rev': info.get('rev'), 'revisions': info.get('revisions', [])})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

@app.route('/api/entities/<sfid>/revisions/bump', methods=['POST'])
def api_revisions_bump(sfid):
    try:
        datarepo_path = get_datarepo_path()
        payload = request.get_json(force=True, silent=True) or request.form.to_dict(flat=True)
        notes = payload.get('notes') if isinstance(payload, dict) else None
        released_at = payload.get('released_at') if isinstance(payload, dict) else None
        # Cut next snapshot, then immediately release it (transaction-guarded)
        def _mutate():
            bumped = bump_revision(datarepo_path, sfid, notes=notes)
            new_rev = bumped.get('new_rev')
            if not new_rev:
                raise RuntimeError('Failed to determine new revision label after bump')
            return release_revision(datarepo_path, sfid, new_rev, released_at=released_at, notes=notes)
        ent = _run_repo_txn(
            datarepo_path,
            _mutate,
            autocommit_message=f"[smallFactory][web] Bump+release revision for {sfid}",
            autocommit_paths=[f"entities/{sfid}"]
        )
        return jsonify({'success': True, 'entity': ent, 'rev': ent.get('rev'), 'revisions': ent.get('revisions', [])})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

@app.route('/api/entities/<sfid>/revisions/<rev>/release', methods=['POST'])
def api_revisions_release(sfid, rev):
    try:
        datarepo_path = get_datarepo_path()
        payload = request.get_json(force=True, silent=True) or request.form.to_dict(flat=True)
        notes = payload.get('notes') if isinstance(payload, dict) else None
        released_at = payload.get('released_at') if isinstance(payload, dict) else None
        def _mutate():
            return release_revision(datarepo_path, sfid, rev, released_at=released_at, notes=notes)
        ent = _run_repo_txn(
            datarepo_path,
            _mutate,
            autocommit_message=f"[smallFactory][web] Release revision {rev} for {sfid}",
            autocommit_paths=[f"entities/{sfid}"]
        )
        return jsonify({'success': True, 'entity': ent, 'rev': ent.get('rev'), 'revisions': ent.get('revisions', [])})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

@app.route('/api/entities/<sfid>/revisions/<rev>/download', methods=['GET'])
def api_revisions_download(sfid, rev):
    """Download a gzipped archive (.tar.gz) of the revision directory.

    Packs the directory at entities/<sfid>/revisions/<rev>/ into a tar.gz and streams it.
    """
    try:
        datarepo_path = get_datarepo_path()
        # Validate numeric revision label (per spec)
        try:
            rev_str = str(int(str(rev).strip()))
        except Exception:
            return jsonify({'success': False, 'error': 'Invalid revision id'}), 400
        rev_dir = datarepo_path / 'entities' / sfid / 'revisions' / rev_str
        if not rev_dir.exists() or not rev_dir.is_dir():
            return jsonify({'success': False, 'error': 'Revision not found'}), 404
        # Create tar.gz in memory
        buf = io.BytesIO()
        with tarfile.open(mode='w:gz', fileobj=buf) as tf:
            arc_root = f"{sfid}_rev{rev_str}"
            tf.add(str(rev_dir), arcname=arc_root)
        buf.seek(0)
        filename = f"{sfid}_rev{rev_str}.tar.gz"
        return send_file(
            buf,
            mimetype='application/gzip',
            as_attachment=True,
            download_name=filename,
        )
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

# -----------------------
# BOM API endpoints (AJAX)
# -----------------------

def _enrich_bom_rows(datarepo_path, bom):
    rows = []
    if isinstance(bom, list):
        for line in bom:
            if not isinstance(line, dict):
                continue
            use = str(line.get('use', '')).strip()
            if not use:
                continue
            qty = line.get('qty', 1) or 1
            rev = line.get('rev', 'released') or 'released'
            # Resolve child name best-effort
            child_name = use
            try:
                child = get_entity(datarepo_path, use)
                child_name = child.get('name', use)
            except Exception:
                pass
            alternates = []
            if isinstance(line.get('alternates'), list):
                for alt in line['alternates']:
                    if isinstance(alt, dict) and alt.get('use'):
                        alternates.append(str(alt.get('use')))
            rows.append({
                'use': use,
                'name': child_name,
                'qty': qty,
                'rev': rev,
                'alternates': alternates,
                'alternates_group': line.get('alternates_group'),
            })
    return rows


@app.route('/api/entities/<sfid>/bom', methods=['GET'])
def api_bom_get(sfid):
    try:
        datarepo_path = get_datarepo_path()
        bom = bom_list(datarepo_path, sfid)
        return jsonify({'success': True, 'bom': bom, 'rows': _enrich_bom_rows(datarepo_path, bom)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@app.route('/api/entities/<sfid>/bom/import/apply', methods=['POST'])
def api_bom_import_apply(sfid):
    """Apply BOM CSV import: sync BOM to provided rows.

    Expects JSON payload with:
      - rows: list of dicts (keys: use, qty, rev, ambiguous, ...)
      - remove_missing: bool (default True) -> remove existing uses not present in rows
      - update_existing: bool (default True) -> update qty/rev for existing uses when changed
    """
    try:
        datarepo_path = get_datarepo_path()
        payload = request.get_json(force=True, silent=True) or {}
        in_rows = payload.get('rows') or []
        remove_missing = bool(payload.get('remove_missing', True))
        update_existing = bool(payload.get('update_existing', True))

        # Normalize desired spec from rows (dedupe by 'use', keep last)
        desired: dict[str, dict] = {}
        # Track any provided names for referenced uses (last occurrence wins)
        names_by_use: dict[str, str] = {}
        # Track full field dictionaries per 'use' to create entities with all CSV attributes
        fields_by_use: dict[str, dict] = {}
        for r in (in_rows if isinstance(in_rows, list) else []):
            try:
                use = str((r or {}).get('use') or '').strip()
                if not use:
                    continue
                # Skip ambiguous preview rows
                if (r or {}).get('ambiguous'):
                    continue
                qty_raw = (r or {}).get('qty', 1)
                try:
                    qty = int(qty_raw)
                except Exception:
                    qty = 1
                if qty <= 0:
                    qty = 1
                rev = str((r or {}).get('rev') or 'released').strip() or 'released'
                desired[use] = {'qty': qty, 'rev': rev}
                # Capture provided name if present for potential entity creation
                nm = (r or {}).get('name')
                if isinstance(nm, str) and nm.strip():
                    names_by_use[use] = nm.strip()
                # Build a field set for entity creation from all available CSV fields, excluding BOM/apply-only keys
                # Keep normalized keys exactly as provided by preview for compatibility with specs
                meta_keys = {'use', 'qty', 'rev', 'ambiguous', 'auto_filled', 'matches'}
                fld: dict[str, str] = {}
                for k, v in (r or {}).items():
                    if k in meta_keys:
                        continue
                    if v is None:
                        continue
                    s = str(v).strip()
                    if s == '':
                        continue
                    # Do not persist sfid field inside entity.yml; create_entity will strip it as well, but be explicit
                    if k == 'sfid':
                        continue
                    fld[k] = s
                # Ensure name captured via names_by_use is present in fields if available
                if use in names_by_use and names_by_use[use]:
                    fld.setdefault('name', names_by_use[use])
                if fld:
                    fields_by_use[use] = fld
            except Exception:
                continue

        def _mutate():
            result = {
                'added': 0,
                'updated': 0,
                'removed': 0,
                'created': 0,
                'created_entities': [],
            }
            # Before syncing BOM lines, ensure all referenced 'use' entities exist.
            # Create any missing ones (best-effort), including name if provided.
            for use in list(desired.keys()):
                try:
                    # If entity exists, this will succeed; otherwise raises
                    _ = get_entity(datarepo_path, use)
                except FileNotFoundError:
                    try:
                        # Prefer full field set collected from CSV row; fall back to name-only if present
                        fields = dict(fields_by_use.get(use, {}))
                        # For new part entities (p_ prefix), split unknown CSV fields into attrs
                        # and keep only known top-level keys on the entity root. This preserves all
                        # CSV data while complying with the spec and UI expectations.
                        if fields and isinstance(use, str) and use.startswith('p_'):
                            known_top = {
                                'name', 'uom', 'policy', 'category', 'description', 'tags'
                            }
                            top: dict = {}
                            attrs: dict = {}
                            for k, v in list(fields.items()):
                                if k in known_top:
                                    top[k] = v
                                elif k == 'attrs' and isinstance(v, dict):
                                    # Merge any provided attrs map
                                    for ak, av in v.items():
                                        attrs[ak] = av
                                else:
                                    attrs[k] = v
                            # Filter out BOM-only fields from attrs (e.g., qty/quantity variants)
                            def _is_qty_like(key: str) -> bool:
                                try:
                                    norm = re.sub(r'[\s_]+', '', str(key or '').strip().lower())
                                except Exception:
                                    return False
                                return norm in ('qty', 'quantity')
                            attrs = {k: v for k, v in attrs.items() if not _is_qty_like(k)}
                            # Normalize tags if provided as a comma-separated string
                            if 'tags' in top and isinstance(top['tags'], str):
                                toks = [t.strip() for t in top['tags'].split(',') if t and t.strip()]
                                if toks:
                                    top['tags'] = toks
                            # Always include attrs (empty dict if none)
                            top['attrs'] = attrs or {}
                            fields = top
                        if not fields:
                            nm = names_by_use.get(use)
                            if nm:
                                fields = {'name': nm}
                        created = create_entity(datarepo_path, use, fields if fields else None)
                        # Track created entity for summary
                        result['created'] += 1
                        result['created_entities'].append({
                            'sfid': created.get('sfid', use),
                            'name': created.get('name') or names_by_use.get(use) or use,
                        })
                    except Exception:
                        # If creation fails, leave it to bom_* operations to surface errors when adding
                        # We do not remove it from desired to preserve caller intent
                        pass
                except Exception:
                    # On other errors while checking existence, skip creation attempt
                    pass
            # Current state
            current = bom_list(datarepo_path, sfid) or []
            cur_by_use: dict[str, dict] = {}
            for i, line in enumerate(current):
                try:
                    u = str(line.get('use') or '').strip()
                    if not u:
                        continue
                    cur_by_use[u] = {'index': i, 'line': line}
                except Exception:
                    continue

            # Remove lines whose use is not in desired
            if remove_missing:
                to_remove = [u for u in cur_by_use.keys() if u not in desired]
                for u in to_remove:
                    bom_remove_line(datarepo_path, sfid, index=None, use=u, remove_all=True)
                    result['removed'] += 1

            # Recompute current after removals (indices may have shifted)
            current = bom_list(datarepo_path, sfid) or []
            cur_by_use = {}
            for i, line in enumerate(current):
                try:
                    u = str(line.get('use') or '').strip()
                    if not u:
                        continue
                    cur_by_use[u] = {'index': i, 'line': line}
                except Exception:
                    continue

            # Add new or update existing
            for use, spec in desired.items():
                qty = spec.get('qty', 1)
                rev = spec.get('rev', 'released')
                if use not in cur_by_use:
                    bom_add_line(datarepo_path, sfid, use=use, qty=qty, rev=rev, alternates=None, alternates_group=None, index=None, check_exists=True)
                    result['added'] += 1
                else:
                    if update_existing:
                        ent = cur_by_use[use]
                        idx = ent['index']
                        line = ent['line'] or {}
                        updates = {}
                        # Compare and update
                        if int(line.get('qty', 1)) != int(qty):
                            updates['qty'] = qty
                        if (line.get('rev') or 'released') != (rev or 'released'):
                            updates['rev'] = rev or 'released'
                        if updates:
                            bom_set_line(datarepo_path, sfid, index=idx, updates=updates, check_exists=True)
                            result['updated'] += 1

            # Return final bom
            final = bom_list(datarepo_path, sfid) or []
            result['bom'] = final
            return result

        res = _run_repo_txn(
            datarepo_path,
            _mutate,
            autocommit_message=f"[smallFactory][web] BOM import apply (sync) for {sfid} remove_missing={remove_missing} update_existing={update_existing}",
            autocommit_paths=[f"entities/{sfid}"]
        )

        bom = res.get('bom')
        # Build enriched rows for UI
        rows = _enrich_bom_rows(datarepo_path, bom)
        summary = {k: res.get(k, 0) for k in ('added', 'updated', 'removed', 'created')}
        created_entities = res.get('created_entities', [])
        return jsonify({'success': True, 'rows': rows, 'summary': summary, 'created_entities': created_entities})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

# Deep BOM traversal using core API
def _walk_bom_deep(datarepo_path: Path, parent_sfid: str, *, max_depth: int | None = None):
    """Return a flat list of deep BOM nodes with metadata via core traversal.

    Preserves web semantics:
    - level: 1 for immediate children (core is 0-based; we add +1)
    - includes resolved_rev (resolved label) alongside rev (spec)
    - enriches with onhand_total
    """
    core_nodes = ent_resolved_bom_tree(datarepo_path, parent_sfid, max_depth=max_depth)
    onhand_cache: dict[str, int | None] = {}

    def _get_onhand_total(sfid: str) -> int | None:
        if not isinstance(sfid, str) or not sfid.startswith('p_'):
            return None
        if sfid in onhand_cache:
            return onhand_cache[sfid]
        try:
            oh = inventory_onhand_readonly(datarepo_path, part=sfid)
            total = int(oh.get('total', 0)) if isinstance(oh, dict) else None
            onhand_cache[sfid] = total
            return total
        except Exception:
            onhand_cache[sfid] = None
            return None

    nodes = []
    for n in core_nodes:
        nodes.append({
            'parent': n.get('parent'),
            'use': n.get('use'),
            'name': n.get('name'),
            'qty': n.get('qty'),
            'rev': n.get('rev_spec', 'released'),
            'resolved_rev': n.get('rev'),
            'level': (n.get('level') or 0) + 1,
            'is_alt': n.get('is_alt', False),
            'alternates_group': n.get('alternates_group'),
            'gross_qty': n.get('gross_qty'),
            'cycle': n.get('cycle', False),
            'onhand_total': _get_onhand_total(n.get('use')),
        })
    return nodes


# -----------------------
# BOM CSV import helpers
# -----------------------
def _norm_token(val: str | None) -> str:
    try:
        return str(val or '').strip().lower()
    except Exception:
        return ''

def _decode_csv_bytes(b: bytes) -> str:
    """Decode uploaded CSV bytes, handling common BOMs and UTF-16 files.

    Strategy:
    - If UTF-8 BOM, decode with 'utf-8-sig' (strips BOM)
    - If UTF-16 BOM, decode with 'utf-16' (auto-detects endianness)
    - If many NUL bytes present, try utf-16-le then utf-16-be
    - Fallback to utf-8 with errors ignored
    """
    try:
        if not b:
            return ''
        if b.startswith(b'\xef\xbb\xbf'):
            return b.decode('utf-8-sig', errors='ignore')
        if b.startswith(b'\xff\xfe') or b.startswith(b'\xfe\xff'):
            return b.decode('utf-16', errors='ignore')
        # Heuristic: lots of NUL bytes => likely UTF-16 without BOM
        nul_ratio = (b.count(b'\x00') / max(1, len(b)))
        if nul_ratio > 0.05:
            try:
                return b.decode('utf-16-le', errors='ignore')
            except Exception:
                try:
                    return b.decode('utf-16-be', errors='ignore')
                except Exception:
                    pass
        return b.decode('utf-8', errors='ignore')
    except Exception:
        try:
            return b.decode('utf-8', errors='ignore')
        except Exception:
            return ''

def _sanitize_csv_text(text: str) -> str:
    """Sanitize CSV text: remove embedded NULs and stray BOM, normalize newlines."""
    if not isinstance(text, str):
        try:
            text = str(text)
        except Exception:
            return ''
    # Strip Unicode BOM if present and embedded NULs from UTF-16 mis-decode
    text = text.replace('\ufeff', '').replace('\x00', '')
    # Normalize CRLF/CR to LF
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    return text


def _index_parts_by_mfg_mpn(datarepo_path: Path) -> dict[tuple[str, str], list[str]]:
    """Return a case-insensitive index mapping (manufacturer, mpn) -> [sfid,...].

    Recognizes common attribute keys on entities: manufacturer/mfr and mpn/mfr_pn.
    Only indexes part entities (sfid starts with 'p_').
    """
    idx: dict[tuple[str, str], list[str]] = {}
    try:
        ents = list_entities(datarepo_path) or []
    except Exception:
        ents = []
    for e in ents:
        try:
            sfid = str(e.get('sfid', '')).strip()
            if not (sfid and sfid.startswith('p_')):
                continue
            mfg = _norm_token(e.get('manufacturer') or e.get('mfr'))
            mpn = _norm_token(e.get('mpn') or e.get('mfr_pn'))
            if not (mfg and mpn):
                continue
            key = (mfg, mpn)
            lst = idx.get(key)
            if lst is None:
                idx[key] = [sfid]
            else:
                lst.append(sfid)
        except Exception:
            continue
    return idx


def _parse_csv_text(text: str) -> list[dict]:
    """Parse CSV/TSV text into list of dict rows (keys from header).

    Auto-detects common delimiters: comma, tab, semicolon, pipe.
    Normalizes header keys to lowercase and trims values.
    """
    # Pre-sanitize in case caller didn't
    text = _sanitize_csv_text(text)
    rows: list[dict] = []
    sample = text[:8192]
    # Detect delimiter
    delim = ','
    try:
        sniffed = csv.Sniffer().sniff(sample, delimiters=[',', '\t', ';', '|'])
        delim = sniffed.delimiter or delim
    except Exception:
        header = sample.splitlines()[0] if sample else ''
        if '\t' in header:
            delim = '\t'
        elif header.count(';') >= header.count(',') and ';' in header:
            delim = ';'
        elif '|' in header and header.count('|') > header.count(','):
            delim = '|'
    def _make_reader(d):
        return csv.DictReader(io.StringIO(text), delimiter=d, skipinitialspace=True)

    reader = _make_reader(delim)
    # If only one or zero headers detected, retry with alternative delimiters (no plain space to avoid corruption)
    fns = reader.fieldnames or []
    if len([fn for fn in fns if fn and fn.strip()]) <= 1:
        for d in ['\t', ';', '|']:
            if d == delim:
                continue
            reader = _make_reader(d)
            fns = reader.fieldnames or []
            if len([fn for fn in fns if fn and fn.strip()]) > 1:
                break
    for r in reader:
        # Ensure plain dict with string keys and string values
        # csv.DictReader can emit a (None, list) entry when there are extra columns beyond headers.
        # We skip empty/None keys and normalize list values by joining their items.
        clean: dict[str, str] = {}
        for k, v in (r or {}).items():
            # Collapse all whitespace (incl. NBSP) to single space, then lowercase
            raw_key = (k or '')
            # Strip control chars (incl. NUL) and stray BOM
            raw_key = raw_key.replace('\ufeff', '')
            raw_key = re.sub(r'[\x00-\x1F]+', '', raw_key)
            # Replace common punctuation/separators with spaces
            raw_key = re.sub(r'[\\/\-#.:()\[\]|]+', ' ', raw_key)
            key = ' '.join(raw_key.split()).strip().lower()
            if not key:
                # Skip entries with no header
                continue
            if isinstance(v, list):
                val = ",".join([str(x).strip() for x in v if x is not None])
            else:
                val = str(v or '').strip()
            clean[key] = val
            # Also provide an underscore variant for convenience (e.g., manufacturer_part)
            if ' ' in key:
                clean[key.replace(' ', '_')] = val
            # Also provide a no-space variant (e.g., mfrpn) to catch tight matches
            nospace = key.replace(' ', '')
            if nospace and nospace not in clean:
                clean[nospace] = val
        rows.append(clean)
    return rows


def _std_field(row: dict, *candidates: str) -> str:
    """Return the first non-empty field value from row for any of the candidate keys.

    Tries multiple key variants per candidate: exact, space<->underscore, no-space.
    Keys in `row` are expected to already be lowercased by _parse_csv_text.
    """
    if not row:
        return ''
    for c in candidates:
        if not c:
            continue
        base = str(c).strip().lower()
        if not base:
            continue
        variants = [base]
        if ' ' in base:
            variants.append(base.replace(' ', '_'))
        if '_' in base:
            variants.append(base.replace('_', ' '))
        variants.append(base.replace(' ', ''))  # no-space
        for k in variants:
            v = row.get(k)
            if v is not None and str(v).strip() != '':
                return str(v).strip()
    return ''


@app.route('/api/entities/<sfid>/bom/import/preview', methods=['POST'])
def api_bom_import_preview(sfid):
    """Preview BOM CSV import and attempt to auto-map lines.

    - Accepts file upload (multipart) or 'csv_text' in form/JSON.
    - Recognized columns: use, qty/quantity, rev, manufacturer/mfr/mfg, mpn/mfr_pn/etc, name (optional).
    - Auto-fills 'use' when a unique (manufacturer, mpn) match exists.
    - Dedupe by stable key (prefer use; else manufacturer+mpn; else row index), keeping last occurrence.
    """
    try:
        datarepo_path = get_datarepo_path()
        # Read CSV content
        text = ''
        if 'file' in request.files:
            f = request.files['file']
            b = f.read()
            text = _decode_csv_bytes(b)
        else:
            payload = request.get_json(silent=True) if request.is_json else None
            text = (request.form.get('csv_text') or (payload or {}).get('csv_text') or '')
        text = _sanitize_csv_text(text)
        if not text:
            return jsonify({'success': False, 'error': 'No CSV provided'}), 400

        # Debug: detect delimiter and raw headers
        sample = text[:8192]
        dbg_delim = ','
        try:
            sniffed = csv.Sniffer().sniff(sample, delimiters=[',', '\t', ';', '|'])
            dbg_delim = sniffed.delimiter or dbg_delim
        except Exception:
            header = sample.splitlines()[0] if sample else ''
            if '\t' in header:
                dbg_delim = '\t'
            elif header.count(';') >= header.count(',') and ';' in header:
                dbg_delim = ';'
            elif '|' in header and header.count('|') > header.count(','):
                dbg_delim = '|'
        dbg_reader = csv.DictReader(io.StringIO(text), delimiter=dbg_delim, skipinitialspace=True)
        dbg_headers = dbg_reader.fieldnames or []
        if len([h for h in (dbg_headers or []) if h and str(h).strip()]) <= 1:
            for d in ['\t', ';', '|']:
                if d == dbg_delim:
                    continue
                dbg_reader = csv.DictReader(io.StringIO(text), delimiter=d, skipinitialspace=True)
                dbg_headers = dbg_reader.fieldnames or []
                if len([h for h in (dbg_headers or []) if h and str(h).strip()]) > 1:
                    dbg_delim = d
                    break

        # Parse rows using robust parser (which normalizes keys per-row)
        raw_rows = _parse_csv_text(text)
        idx = _index_parts_by_mfg_mpn(datarepo_path)

        # Build preview rows and dedupe (keep last occurrence)
        dedupe_map: dict[str, dict] = {}
        for i, r in enumerate(raw_rows):
            use = _std_field(r, 'use', 'child', 'part', 'sfid')
            qty = _std_field(r, 'qty', 'quantity') or '1'
            rev = _std_field(r, 'rev', 'revision') or 'released'
            mfg = _std_field(r, 'manufacturer', 'mfr', 'mfg')
            mpn = _std_field(r, 'mpn', 'mfr_pn', 'pn', 'part_number', 'manufacturer part', 'manufacturer_part', 'mfg_part', 'mfg part')
            # Prefer CSV-provided name when present; otherwise, if we know the child 'use', look up entity name
            name_val = _std_field(r, 'name')

            auto_filled = False
            ambiguous = False
            matches: list[str] = []

            if not use and mfg and mpn:
                key = (_norm_token(mfg), _norm_token(mpn))
                cands = idx.get(key) or []
                seen: set[str] = set()
                for c in cands:
                    if c not in seen:
                        matches.append(c)
                        seen.add(c)
                if len(matches) == 1:
                    use = matches[0]
                    auto_filled = True
                elif len(matches) > 1:
                    ambiguous = True

            # If no CSV name provided and we have a resolved 'use', attempt to resolve the entity's name
            if not name_val and use:
                try:
                    child = get_entity(datarepo_path, use)
                    name_val = child.get('name', use)
                except Exception:
                    # Best-effort only; leave blank if lookup fails
                    pass

            # Stable dedupe key
            k = _norm_token(use)
            if not k:
                nmfg = _norm_token(mfg)
                nmpn = _norm_token(mpn)
                if nmfg or nmpn:
                    k = f"m:{nmfg}|p:{nmpn}"
                else:
                    k = f"row:{i:06d}"
            preview = {
                'use': use,
                'name': name_val,
                'qty': qty,
                'rev': rev,
                'manufacturer': mfg,
                'mpn': mpn,
                'auto_filled': auto_filled,
                'ambiguous': ambiguous,
                'matches': matches if ambiguous else [],
            }
            # Pass through any additional CSV fields so they are available during apply.
            # We keep the preview's canonical keys and avoid overwriting them; unknown keys are preserved.
            try:
                known_keys = {
                    'use', 'name', 'qty', 'rev', 'manufacturer', 'mpn', 'auto_filled', 'ambiguous', 'matches'
                }
                for kk, vv in (r or {}).items():
                    if kk in known_keys:
                        continue
                    if vv is None:
                        continue
                    s = str(vv).strip()
                    if s == '':
                        continue
                    if kk not in preview:
                        preview[kk] = s
            except Exception:
                # Best-effort preservation; ignore any unexpected errors
                pass
            dedupe_map[k] = preview

        rows = list(dedupe_map.values())

        # Build normalized header preview using the same normalization logic
        def _norm_header(h: str) -> list[str]:
            s = str(h or '')
            s = s.replace('\ufeff', '')
            s = re.sub(r'[\x00-\x1F]+', '', s)
            s = re.sub(r'[\\/\-#.:()\[\]|]+', ' ', s)
            base = ' '.join(s.split()).strip().lower()
            outs = []
            if base:
                outs.append(base)
                if ' ' in base:
                    outs.append(base.replace(' ', '_'))
            return outs

        norm_headers: list[str] = []
        seen_h = set()
        for h in (dbg_headers or []):
            for v in _norm_header(h):
                if v and v not in seen_h:
                    norm_headers.append(v)
                    seen_h.add(v)

        return jsonify({
            'success': True,
            'rows': rows,
            'debug': {
                'detected_delimiter': dbg_delim,
                'raw_headers': dbg_headers,
                'norm_headers': norm_headers,
                'parsed_row_count': len(rows),
            }
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@app.route('/api/entities/<sfid>/bom/deep', methods=['GET'])
def api_bom_deep(sfid):
    try:
        datarepo_path = get_datarepo_path()
        # Query param: max_depth (int). 0 => only immediate children (no further recursion)
        md_raw = request.args.get('max_depth')
        max_depth = None
        if md_raw is not None and str(md_raw).strip() != '':
            try:
                max_depth = int(md_raw)
                if max_depth < 0:
                    max_depth = None
            except Exception:
                max_depth = None
        nodes = _walk_bom_deep(datarepo_path, sfid, max_depth=max_depth)
        # Optional CSV output when format=csv
        fmt = (request.args.get('format') or '').lower()
        if fmt == 'csv':
            # Build CSV from nodes
            headers = ['parent', 'use', 'name', 'qty', 'rev', 'level', 'is_alt', 'alternates_group', 'gross_qty', 'cycle', 'onhand_total']
            sio = io.StringIO()
            writer = csv.DictWriter(sio, fieldnames=headers)
            writer.writeheader()
            for n in nodes:
                # Ensure only known headers are written
                row = {k: (n.get('gross_qty') if k == 'gross_qty' else n.get(k)) for k in headers}
                writer.writerow(row)
            csv_text = sio.getvalue()
            return Response(
                csv_text,
                mimetype='text/csv',
                headers={
                    'Content-Disposition': f'attachment; filename="{sfid}_bom_deep.csv"'
                }
            )
        return jsonify({'success': True, 'nodes': nodes})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@app.route('/api/entities/<sfid>/bom/add', methods=['POST'])
def api_bom_add(sfid):
    try:
        datarepo_path = get_datarepo_path()
        payload = request.get_json(force=True, silent=True) or request.form.to_dict(flat=True)
        use = (payload.get('use') or '').strip()
        qty = payload.get('qty', 1)
        rev = payload.get('rev') if 'rev' in payload else 'released'
        alternates_group = (payload.get('alternates_group') or None)
        index = payload.get('index')
        check_exists = payload.get('check_exists')
        if isinstance(check_exists, str):
            check_exists = check_exists.lower() not in ('0', 'false', 'no')
        if check_exists is None:
            check_exists = True
        # alternates may be list[str] or list[{'use': str}] or comma string
        alts_raw = payload.get('alternates')
        alts = None
        if isinstance(alts_raw, str):
            parts = [s.strip() for s in alts_raw.split(',') if s.strip()]
            alts = [{'use': s} for s in parts] if parts else None
        elif isinstance(alts_raw, list):
            tmp = []
            for a in alts_raw:
                if isinstance(a, dict) and a.get('use'):
                    tmp.append({'use': str(a['use'])})
                elif isinstance(a, str) and a.strip():
                    tmp.append({'use': a.strip()})
            alts = tmp or None
        # index may come as string
        if isinstance(index, str) and index.isdigit():
            index = int(index)
        def _mutate():
            return bom_add_line(
                datarepo_path,
                sfid,
                use=use,
                qty=qty,
                rev=rev,
                alternates=alts,
                alternates_group=alternates_group,
                index=index,
                check_exists=bool(check_exists),
            )
        res = _run_repo_txn(
            datarepo_path,
            _mutate,
            autocommit_message=f"[smallFactory][web] BOM add {use} x{qty} rev {rev} -> {sfid}",
            autocommit_paths=[f"entities/{sfid}"]
        )
        bom = res.get('bom')
        return jsonify({'success': True, 'result': res, 'rows': _enrich_bom_rows(datarepo_path, bom)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@app.route('/api/entities/<sfid>/bom/remove', methods=['POST'])
def api_bom_remove(sfid):
    try:
        datarepo_path = get_datarepo_path()
        payload = request.get_json(force=True, silent=True) or request.form.to_dict(flat=True)
        index = payload.get('index')
        use = (payload.get('use') or '').strip() or None
        remove_all = payload.get('remove_all')
        if isinstance(index, str) and index.isdigit():
            index = int(index)
        if isinstance(remove_all, str):
            remove_all = remove_all.lower() in ('1', 'true', 'yes')
        def _mutate():
            return bom_remove_line(
                datarepo_path,
                sfid,
                index=index,
                use=use,
                remove_all=bool(remove_all),
            )
        res = _run_repo_txn(
            datarepo_path,
            _mutate,
            autocommit_message=f"[smallFactory][web] BOM remove index={index} use={use or ''} from {sfid}",
            autocommit_paths=[f"entities/{sfid}"]
        )
        bom = res.get('bom')
        return jsonify({'success': True, 'result': res, 'rows': _enrich_bom_rows(datarepo_path, bom)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@app.route('/api/entities/<sfid>/bom/set', methods=['POST'])
def api_bom_set(sfid):
    try:
        datarepo_path = get_datarepo_path()
        payload = request.get_json(force=True, silent=True) or request.form.to_dict(flat=True)
        index = payload.get('index')
        if isinstance(index, str) and index.isdigit():
            index = int(index)
        updates = {}
        for k in ('use', 'qty', 'rev', 'alternates_group'):
            if k in payload:
                updates[k] = payload.get(k)
        def _mutate():
            return bom_set_line(datarepo_path, sfid, index=index, updates=updates, check_exists=bool(payload.get('check_exists', True)))
        res = _run_repo_txn(
            datarepo_path,
            _mutate,
            autocommit_message=f"[smallFactory][web] BOM set index={index} for {sfid}",
            autocommit_paths=[f"entities/{sfid}"]
        )
        bom = res.get('bom')
        return jsonify({'success': True, 'result': res, 'rows': _enrich_bom_rows(datarepo_path, bom)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


# -----------------------
# Files API endpoints (AJAX)
# -----------------------
from smallfactory.core.v1.files import (
    list_files as files_list,
    mkdir as files_mkdir,
    rmdir as files_rmdir,
    upload_file as files_upload,
    delete_file as files_delete,
    move_file as files_move_file,
    move_dir as files_move_dir,
    stream_file as files_stream_file,
)

def _files_root_name(datarepo_path: Path, sfid: str) -> str:
    # Canonical working root is 'files' only (no legacy support)
    return "files"

@app.route('/api/entities/<sfid>/files', methods=['GET'])
def api_files_list(sfid):
    try:
        datarepo_path = get_datarepo_path()
        path = request.args.get('path') or None
        recursive = request.args.get('recursive', 'false').lower() in ('1', 'true', 'yes', 'on')
        glob = request.args.get('glob') or None
        res = files_list(datarepo_path, sfid, path=path, recursive=recursive, glob=glob)
        return jsonify({'success': True, **res})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@app.route('/api/entities/<sfid>/files/mkdir', methods=['POST'])
def api_files_mkdir(sfid):
    try:
        datarepo_path = get_datarepo_path()
        payload = request.get_json(force=True, silent=True) or request.form.to_dict(flat=True)
        path = (payload.get('path') or '').strip()
        if not path:
            return jsonify({'success': False, 'error': 'Missing path'}), 400
        root_name = _files_root_name(datarepo_path, sfid)
        rel = f"entities/{sfid}/{root_name}/{path}".rstrip('/')
        def _mutate():
            return files_mkdir(datarepo_path, sfid, path=path)
        res = _run_repo_txn(
            datarepo_path,
            _mutate,
            autocommit_message=f"web: mkdir {sfid} {root_name}/{path}",
            autocommit_paths=[rel]
        )
        return jsonify({'success': True, 'result': res, 'autocommit': bool(_autocommit_enabled())})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@app.route('/api/entities/<sfid>/files/rmdir', methods=['POST'])
def api_files_rmdir(sfid):
    try:
        datarepo_path = get_datarepo_path()
        payload = request.get_json(force=True, silent=True) or request.form.to_dict(flat=True)
        path = (payload.get('path') or '').strip()
        if not path:
            return jsonify({'success': False, 'error': 'Missing path'}), 400
        root_name = _files_root_name(datarepo_path, sfid)
        stage_target = f"entities/{sfid}/{root_name}/{path}".rstrip('/')
        def _mutate():
            return files_rmdir(datarepo_path, sfid, path=path)
        res = _run_repo_txn(
            datarepo_path,
            _mutate,
            autocommit_message=f"web: rmdir {sfid} {root_name}/{path}",
            autocommit_paths=[stage_target]
        )
        return jsonify({'success': True, 'result': res, 'autocommit': bool(_autocommit_enabled())})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@app.route('/api/entities/<sfid>/files/upload', methods=['POST'])
def api_files_upload(sfid):
    try:
        datarepo_path = get_datarepo_path()
        path = (request.form.get('path') or '').strip()
        overwrite = (request.form.get('overwrite') or '').lower() in ('1', 'true', 'yes', 'on')
        f = request.files.get('file')
        if not path or not f or not getattr(f, 'filename', None):
            return jsonify({'success': False, 'error': 'Missing path or file'}), 400
        b = f.read()
        root_name = _files_root_name(datarepo_path, sfid)
        rel = f"entities/{sfid}/{root_name}/{path}"
        def _mutate():
            return files_upload(datarepo_path, sfid, path=path, file_bytes=b, overwrite=overwrite)
        res = _run_repo_txn(
            datarepo_path,
            _mutate,
            autocommit_message=f"web: upload {sfid} {root_name}/{path}",
            autocommit_paths=[rel]
        )
        return jsonify({'success': True, 'result': res, 'autocommit': bool(_autocommit_enabled())})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@app.route('/api/entities/<sfid>/files/delete', methods=['POST'])
def api_files_delete(sfid):
    try:
        datarepo_path = get_datarepo_path()
        payload = request.get_json(force=True, silent=True) or request.form.to_dict(flat=True)
        path = (payload.get('path') or '').strip()
        if not path:
            return jsonify({'success': False, 'error': 'Missing path'}), 400
        root_name = _files_root_name(datarepo_path, sfid)
        rel = f"entities/{sfid}/{root_name}/{path}"
        def _mutate():
            return files_delete(datarepo_path, sfid, path=path)
        res = _run_repo_txn(
            datarepo_path,
            _mutate,
            autocommit_message=f"web: delete {sfid} {root_name}/{path}",
            autocommit_paths=[rel]
        )
        return jsonify({'success': True, 'result': res, 'autocommit': bool(_autocommit_enabled())})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@app.route('/api/entities/<sfid>/files/move', methods=['POST'])
def api_files_move(sfid):
    try:
        datarepo_path = get_datarepo_path()
        payload = request.get_json(force=True, silent=True) or request.form.to_dict(flat=True)
        src = (payload.get('src') or '').strip()
        dst = (payload.get('dst') or '').strip()
        is_dir = (payload.get('dir') or payload.get('is_dir') or '').__str__().lower() in ('1','true','yes','on')
        overwrite = (payload.get('overwrite') or '').__str__().lower() in ('1','true','yes','on')
        if not src or not dst:
            return jsonify({'success': False, 'error': 'Missing src or dst'}), 400
        root_name = _files_root_name(datarepo_path, sfid)
        if is_dir:
            stage_paths = [
                f"entities/{sfid}/{root_name}/{src}".rstrip('/'),
                f"entities/{sfid}/{root_name}/{dst}".rstrip('/'),
            ]
        else:
            stage_paths = [f"entities/{sfid}/{root_name}/{src}", f"entities/{sfid}/{root_name}/{dst}"]
        def _mutate():
            if is_dir:
                return files_move_dir(datarepo_path, sfid, src=src, dst=dst, overwrite=overwrite)
            else:
                return files_move_file(datarepo_path, sfid, src=src, dst=dst, overwrite=overwrite)
        res = _run_repo_txn(
            datarepo_path,
            _mutate,
            autocommit_message=f"web: move {sfid} {root_name}: {src} -> {dst}",
            autocommit_paths=stage_paths
        )
        return jsonify({'success': True, 'result': res, 'autocommit': bool(_autocommit_enabled())})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

@app.route('/api/entities/<sfid>/files/download', methods=['GET'])
def api_files_download(sfid):
    try:
        datarepo_path = get_datarepo_path()
        path = (request.args.get('path') or '').strip()
        if not path:
            return jsonify({'success': False, 'error': 'Missing path'}), 400
        res = files_stream_file(datarepo_path, sfid, path=path)
        b = res.get('bytes') or b''
        mt = res.get('mimetype') or 'application/octet-stream'
        fn = res.get('filename') or 'download'
        return Response(b, mimetype=mt, headers={
            'Content-Disposition': f'attachment; filename="{fn}"',
            'Content-Length': str(len(b)),
        })
    except FileNotFoundError:
        return jsonify({'success': False, 'error': 'File not found'}), 404
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

@app.route('/api/entities/<sfid>/bom/alt-add', methods=['POST'])
def api_bom_alt_add(sfid):
    try:
        datarepo_path = get_datarepo_path()
        payload = request.get_json(force=True, silent=True) or request.form.to_dict(flat=True)
        index = payload.get('index')
        alt_use = (payload.get('alt_use') or '').strip()
        check_exists = payload.get('check_exists', True)
        if isinstance(index, str) and index.isdigit():
            index = int(index)
        def _mutate():
            return bom_alt_add(datarepo_path, sfid, index=index, alt_use=alt_use, check_exists=bool(check_exists))
        res = _run_repo_txn(
            datarepo_path,
            _mutate,
            autocommit_message=f"[smallFactory][web] BOM alt-add {alt_use} at index={index} for {sfid}",
            autocommit_paths=[f"entities/{sfid}"]
        )
        bom = res.get('bom')
        return jsonify({'success': True, 'result': res, 'rows': _enrich_bom_rows(datarepo_path, bom)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@app.route('/api/entities/<sfid>/bom/alt-remove', methods=['POST'])
def api_bom_alt_remove(sfid):
    try:
        datarepo_path = get_datarepo_path()
        payload = request.get_json(force=True, silent=True) or request.form.to_dict(flat=True)
        index = payload.get('index')
        alt_index = payload.get('alt_index')
        alt_use = (payload.get('alt_use') or '').strip() or None
        if isinstance(index, str) and index.isdigit():
            index = int(index)
        if isinstance(alt_index, str) and alt_index.isdigit():
            alt_index = int(alt_index)
        def _mutate():
            return bom_alt_remove(datarepo_path, sfid, index=index, alt_index=alt_index, alt_use=alt_use)
        res = _run_repo_txn(
            datarepo_path,
            _mutate,
            autocommit_message=f"[smallFactory][web] BOM alt-remove index={index} alt_index={alt_index} for {sfid}",
            autocommit_paths=[f"entities/{sfid}"]
        )
        bom = res.get('bom')
        return jsonify({'success': True, 'result': res, 'rows': _enrich_bom_rows(datarepo_path, bom)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@app.route('/api/entities/specs/<sfid>')
def api_entities_specs(sfid):
    """Return merged entity field specs for a given SFID (type-aware)."""
    try:
        datarepo_path = get_datarepo_path()
        specs = get_entity_field_specs_for_sfid(sfid, datarepo_path)
        return jsonify({'success': True, 'specs': specs})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# -----------------------
# Vision API (Ollama-backed)
# -----------------------

def _read_image_from_request(req, field_name: str = 'file', max_bytes: int = 10 * 1024 * 1024) -> bytes:
    f = req.files.get(field_name)
    if not f or not getattr(f, 'filename', None):
        raise ValueError("No image file uploaded under field 'file'.")
    # Size guard
    try:
        f.stream.seek(0, io.SEEK_END)
        size = f.stream.tell()
        f.stream.seek(0)
    except Exception:
        size = None
    if size is not None and size > max_bytes:
        raise ValueError("Image too large (max 10MB).")
    # Basic type guard
    ct = (getattr(f, 'mimetype', None) or '').lower()
    if ct and not ct.startswith('image/'):
        raise ValueError("Unsupported file type; expected an image.")
    # Strip EXIF and re-encode to PNG
    try:
        img = Image.open(f.stream)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        out = io.BytesIO()
        img.save(out, format='PNG')
        return out.getvalue()
    except Exception as e:
        raise ValueError(f"Failed to read image: {e}")


@app.route('/api/vision/ask', methods=['POST'])
def api_vision_ask():
    """Generic vision ask endpoint: prompt + image -> model response.

    Form fields:
      - file: image file
      - prompt: text prompt
    """
    try:
        img_bytes = _read_image_from_request(request)
        prompt = (request.form.get('prompt') or '').strip()
        if not prompt:
            return jsonify({'success': False, 'error': 'Missing prompt'}), 400
        result = vlm_ask_image(prompt, img_bytes)
        return jsonify({'success': True, 'result': result})
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        # Friendly guidance for Ollama not running / model not pulled
        hint = (
            "Ensure Ollama is running and the model is available.\n"
            "Install/start: `brew install ollama && ollama serve` (mac) or see https://ollama.com/download\n"
            "Pull model: `ollama pull qwen2.5vl:3b`\n"
            "Set URL (if remote): export SF_OLLAMA_BASE_URL=http://<host>:11434"
        )
        return jsonify({'success': False, 'error': str(e), 'hint': hint}), 500


@app.route('/api/vision/extract/part', methods=['POST'])
def api_vision_extract_part():
    """Extract structured part fields from an invoice image."""
    try:
        img_bytes = _read_image_from_request(request)
        result = vlm_extract_invoice_part(img_bytes)
        return jsonify({'success': True, 'result': result})
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        hint = (
            "Ensure Ollama is running and the model is available.\n"
            "Install/start: `brew install ollama && ollama serve` (mac) or see https://ollama.com/download\n"
            "Pull model: `ollama pull qwen2.5vl:3b`\n"
            "Set URL (if remote): export SF_OLLAMA_BASE_URL=http://<host>:11434"
        )
        return jsonify({'success': False, 'error': str(e), 'hint': hint}), 500

# -----------------------
# Stickers (QR only) routes
# -----------------------

@app.route('/stickers', methods=['GET', 'POST'])
def stickers_index():
    """Default stickers interface is the batch PDF generator."""
    if request.method == 'POST':
        sfid = (request.form.get('sfid') or '').strip()
        # Redirect to batch with prefilled query if provided
        if sfid:
            return redirect(url_for('stickers_batch', sfids=sfid))
    return redirect(url_for('stickers_batch'))


    # Single-sticker routes removed; use /stickers/batch


    # Removed single-sticker PDF route; use /stickers/batch


@app.route('/stickers/batch', methods=['GET', 'POST'])
def stickers_batch():
    """Batch generate a PDF with one sticker per page for multiple SFIDs."""
    deps = stickers_check_deps()
    error = None
    if request.method == 'POST':
        size_text = (request.form.get('size_in') or '2x1').strip()
        dpi_text = (request.form.get('dpi') or '300').strip()
        text_size_text = (request.form.get('text_size') or '24').strip()
        fields_raw = (request.form.get('fields') or '').strip()
        sfids_text = (request.form.get('sfids') or '').strip()
    else:
        size_text = (request.args.get('size_in') or '2x1').strip()
        dpi_text = (request.args.get('dpi') or '300').strip()
        text_size_text = (request.args.get('text_size') or '24').strip()
        # Prefill fields from repo config: sfdatarepo.yml -> stickers.batch.default_fields
        try:
            default_fields = get_stickers_default_fields()
        except Exception:
            default_fields = []
        fields_prefill = ", ".join(default_fields) if default_fields else ""
        fields_raw = (request.args.get('fields') or fields_prefill).strip()
        sfids_text = (request.args.get('sfids') or '').strip()

    if request.method == 'GET':
        return render_template(
            'stickers/batch.html',
            deps=deps,
            error=None,
            size_text=size_text,
            dpi_text=dpi_text,
            text_size_text=text_size_text,
            fields_text=fields_raw,
            sfids_text=sfids_text,
        )

    # POST: parse inputs
    try:
        st = size_text.lower().replace('in', '').strip()
        w_s, h_s = st.split('x', 1)
        w_in, h_in = float(w_s), float(h_s)
        dpi = int(dpi_text)
        tsize = int(text_size_text)
        if w_in <= 0 or h_in <= 0 or dpi <= 0 or tsize <= 0:
            raise ValueError
        size_px = (int(round(w_in * dpi)), int(round(h_in * dpi)))
    except Exception:
        error = 'Invalid size/DPI/text size. Use WIDTHxHEIGHT inches (e.g., 2x1), positive DPI (e.g., 300), and positive text size.'

    # Parse SFIDs
    sfids = []
    if not error:
        raw = sfids_text.replace(',', '\n')
        sfids = [s.strip() for s in raw.split() if s.strip()]
        # de-duplicate preserving order
        seen = set()
        sfids = [s for s in sfids if not (s in seen or seen.add(s))]
        if not sfids:
            error = 'Provide at least one SFID (one per line or comma-separated).'

    # Selected fields
    selected_fields = [s.strip() for s in fields_raw.split(',') if s.strip()] if fields_raw else []

    if error:
        return render_template(
            'stickers/batch.html',
            deps=deps,
            error=error,
            size_text=size_text,
            dpi_text=dpi_text,
            text_size_text=text_size_text,
            fields_text=fields_raw,
            sfids_text=sfids_text,
        )

    # Generate PDF
    try:
        from reportlab.pdfgen import canvas
        from reportlab.lib.units import inch
        from reportlab.lib.utils import ImageReader
    except Exception:
        return render_template(
            'stickers/batch.html',
            deps=deps,
            error='ReportLab is not installed. Install web deps: pip install -r web/requirements.txt',
            size_text=size_text,
            dpi_text=dpi_text,
            fields_text=fields_raw,
            sfids_text=sfids_text,
        )

    try:
        datarepo_path = get_datarepo_path()
        pdf_io = io.BytesIO()
        c = canvas.Canvas(pdf_io, pagesize=(w_in * inch, h_in * inch))

        # Render each SFID on its own page
        for idx, sid in enumerate(sfids):
            try:
                res = generate_sticker_for_entity(
                    datarepo_path,
                    sid,
                    fields=selected_fields or None,
                    size=size_px,
                    dpi=dpi,
                    text_size=tsize,
                )
            except Exception as e:
                # Abort on first failure with a clear message
                return render_template(
                    'stickers/batch.html',
                    deps=deps,
                    error=f"Error generating sticker for SFID '{sid}': {e}",
                    size_text=size_text,
                    dpi_text=dpi_text,
                    text_size_text=text_size_text,
                    fields_text=fields_raw,
                    sfids_text=sfids_text,
                )
            png_b64 = res.get('png_base64')
            img_bytes = base64.b64decode(png_b64)
            img_reader = ImageReader(io.BytesIO(img_bytes))
            c.drawImage(img_reader, 0, 0, width=w_in * inch, height=h_in * inch)
            c.showPage()

        c.save()
        pdf_io.seek(0)
        filename = f"stickers_batch_{len(sfids)}_labels.pdf"
        return send_file(pdf_io, as_attachment=True, download_name=filename, mimetype='application/pdf')
    except Exception as e:
        return render_template(
            'stickers/batch.html',
            deps=deps,
            error=f'Failed to build PDF: {e}',
            size_text=size_text,
            dpi_text=dpi_text,
            fields_text=fields_raw,
            sfids_text=sfids_text,
        )

@app.errorhandler(404)
def not_found(error):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_error(error):
    return render_template('error.html', error='Internal server error'), 500

if __name__ == '__main__':
    import os
    import sys
    
    # Determine port (env PORT or --port flag), default 8080
    port = int(os.environ.get('PORT', '8080'))
    if '--port' in sys.argv:
        try:
            idx = sys.argv.index('--port')
            if idx + 1 < len(sys.argv):
                port = int(sys.argv[idx + 1])
        except Exception:
            pass

    print("🏭 Starting smallFactory Web UI...")
    print(f"📍 Access the interface at: http://localhost:{port}")
    print("🔧 Git-native PLM for 1-4 person teams")
    print("=" * 50)
    
    # Check if we're in development mode
    debug_mode = os.environ.get('FLASK_ENV') == 'development' or '--debug' in sys.argv
    
    try:
        app.run(
            debug=debug_mode,
            host='0.0.0.0',
            port=port,
            use_reloader=debug_mode
        )
    except KeyboardInterrupt:
        print("\n👋 Shutting down smallFactory Web UI...")
    except Exception as e:
        print(f"❌ Error starting web server: {e}")
        sys.exit(1)
