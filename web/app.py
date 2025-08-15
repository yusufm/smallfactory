#!/usr/bin/env python3
"""
smallFactory Web UI - Flask application providing a modern web interface
for the Git-native PLM system.
"""

from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, send_file, Response
from pathlib import Path
import json
import sys
import os
import csv
import base64
import io
from PIL import Image
import subprocess
import threading
from datetime import datetime
from typing import List
import time
import atexit

# Add the parent directory to Python path to import smallfactory modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from smallfactory.core.v1.config import get_datarepo_path, get_inventory_field_specs, get_entity_field_specs_for_sfid, get_stickers_default_fields
from smallfactory.core.v1.inventory import (
    inventory_post,
    inventory_onhand,
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

app = Flask(__name__)
app.secret_key = os.environ.get('SF_WEB_SECRET', 'dev-only-insecure-secret')

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
        result = mutate_fn()
        # Ensure a commit exists if web autocommit is enabled
        _maybe_git_autocommit(datarepo_path, autocommit_message or '[smallFactory][web] Autocommit', autocommit_paths or [])
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

@app.route('/')
def index():
    """Main dashboard showing overview of the system."""
    try:
        datarepo_path = get_datarepo_path()
        summary = inventory_onhand(datarepo_path)
        parts = summary.get('parts', []) if isinstance(summary, dict) else []
        total_items = len(parts)
        total_quantity = int(summary.get('total', 0)) if isinstance(summary, dict) else 0

        # Recent heuristic: first 5 entries (sorted by sfid already)
        recent_items = parts[:5]

        return render_template(
            'index.html',
            total_items=total_items,
            total_quantity=total_quantity,
            recent_items=recent_items,
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
        summary = inventory_onhand(datarepo_path)
        parts = summary.get('parts', []) if isinstance(summary, dict) else []
        # Enrich with entity names and per-location breakdown
        items = []
        for p in parts:
            sfid = p.get('sfid')
            if not sfid:
                continue
            # Entity metadata for name (best-effort)
            try:
                ent = get_entity(datarepo_path, sfid)
                name = ent.get('name', sfid)
                description = ent.get('description', '')
                category = ent.get('category', '')
            except Exception:
                name = sfid
                description = ''
                category = ''
            # Per-part onhand cache for by-location and total
            try:
                cache = inventory_onhand(datarepo_path, part=sfid)
            except Exception:
                cache = {}
            items.append({
                'sfid': sfid,
                'name': name,
                'description': description,
                'category': category,
                'uom': cache.get('uom', 'ea'),
                'total': int(cache.get('total', 0) or 0),
                'by_location': cache.get('by_location', {}) or {},
                'as_of': cache.get('as_of'),
            })
        field_specs = get_inventory_field_specs()
        return render_template('inventory/list.html', items=items, field_specs=field_specs)
    except Exception as e:
        return render_template('error.html', error=str(e))

@app.route('/inventory/<item_id>')
def inventory_view(item_id):
    """View details of a specific inventory item."""
    try:
        datarepo_path = get_datarepo_path()
        cache = inventory_onhand(datarepo_path, part=item_id)
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

@app.route('/inventory/add', methods=['GET', 'POST'])
def inventory_add():
    """Adjust inventory quantity (global form).

    This page allows adjusting an item's quantity at a specific location
    using a signed delta (positive to add, negative to subtract).
    """
    field_specs = get_inventory_field_specs()
    form_data = {}

    # Prefill from query params on GET (e.g., after creating entity and returning)
    if request.method == 'GET':
        # Canonical field for location is l_sfid; accept legacy 'location' too
        pre_sfid = request.args.get('sfid', '').strip()
        pre_l_sfid = request.args.get('l_sfid', '').strip() or request.args.get('location', '').strip()
        pre_delta = request.args.get('delta', '').strip()
        if pre_sfid:
            form_data['sfid'] = pre_sfid
        if pre_l_sfid:
            form_data['l_sfid'] = pre_l_sfid
        if pre_delta:
            form_data['delta'] = pre_delta
    
    if request.method == 'POST':
        # Always preserve form data for potential re-display
        form_data = {key: value for key, value in request.form.items() if str(value).strip()}

        try:
            # Extract required fields for adjustment
            sfid = request.form.get('sfid', '').strip()
            # Canonical field name is l_sfid; support legacy 'location' as fallback
            location = request.form.get('l_sfid', '').strip() or request.form.get('location', '').strip() or None
            delta_raw = request.form.get('delta', '0').strip()

            if not sfid:
                raise ValueError("Missing required field: sfid")
            try:
                delta = int(delta_raw)
            except Exception:
                raise ValueError("delta must be an integer (can be negative)")

            datarepo_path = get_datarepo_path()
            def _mutate():
                return inventory_post(datarepo_path, sfid, delta, location)
            _ = _run_repo_txn(
                datarepo_path,
                _mutate,
                autocommit_message=f"[smallFactory][web] Inventory post {sfid} @ {location or 'default'} Δ{delta}",
                autocommit_paths=[f"inventory/{sfid}"]
            )
            loc_msg = location or 'default location'
            flash(f"Successfully adjusted '{sfid}' at {loc_msg} by {delta}", 'success')
            return redirect(url_for('inventory_view', item_id=sfid))
        except Exception as e:
            flash(f'Error adjusting quantity: {e}', 'error')
            # fall through to re-render form
    
    return render_template('inventory/add.html', field_specs=field_specs, form_data=form_data)

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

@app.route('/inventory/<item_id>/adjust', methods=['POST'])
def inventory_adjust(item_id):
    """Adjust quantity for an inventory item."""
    try:
        datarepo_path = get_datarepo_path()
        delta = int(request.form.get('delta', 0))
        # Canonical field name is l_sfid; support legacy 'location' as fallback
        location = request.form.get('l_sfid', '').strip() or request.form.get('location', '').strip() or None
        def _mutate():
            return inventory_post(datarepo_path, item_id, delta, location)
        _ = _run_repo_txn(
            datarepo_path,
            _mutate,
            autocommit_message=f"[smallFactory][web] Inventory post {item_id} @ {location or 'default'} Δ{delta}",
            autocommit_paths=[f"inventory/{item_id}"]
        )
        flash(f'Successfully adjusted quantity by {delta}', 'success')
    except Exception as e:
        flash(f'Error adjusting quantity: {e}', 'error')
    
    return redirect(url_for('inventory_view', item_id=item_id))

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
# Entities module (canonical metadata)
# -------------------------------

@app.route('/entities')
def entities_list():
    """Display all canonical entities."""
    try:
        datarepo_path = get_datarepo_path()
        entities = list_entities(datarepo_path)
        return render_template('entities/list.html', entities=entities)
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
            ent_dir = Path(datarepo_path) / "entities" / sfid
            refs_dir = ent_dir / "refs"
            rel_fp = refs_dir / "released"
            if rel_fp.exists():
                released_rev = (rel_fp.read_text() or '').strip() or None
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

        return render_template('entities/view.html', entity=entity, bom_rows=bom_rows, released_rev=released_rev)
    except Exception as e:
        flash(f'Error viewing entity: {e}', 'error')
        return redirect(url_for('entities_list'))


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

# Entities API endpoints
@app.route('/api/entities')
def api_entities_list():
    try:
        datarepo_path = get_datarepo_path()
        entities = list_entities(datarepo_path)
        return jsonify({'success': True, 'entities': entities})
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
            oh = inventory_onhand(datarepo_path, part=sfid)
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
            'cumulative_qty': n.get('cumulative_qty'),
            'cycle': n.get('cycle', False),
            'onhand_total': _get_onhand_total(n.get('use')),
        })
    return nodes


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
            headers = ['parent', 'use', 'name', 'qty', 'rev', 'level', 'is_alt', 'alternates_group', 'cumulative_qty', 'cycle', 'onhand_total']
            sio = io.StringIO()
            writer = csv.DictWriter(sio, fieldnames=headers)
            writer.writeheader()
            for n in nodes:
                # Ensure only known headers are written
                row = {k: n.get(k) for k in headers}
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
