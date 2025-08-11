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
    # BOM management
    bom_list,
    bom_add_line,
    bom_remove_line,
    bom_set_line,
    bom_alt_add,
    bom_alt_remove,
)
from smallfactory.core.v1.stickers import (
    generate_sticker_for_entity,
    check_dependencies as stickers_check_deps,
)
from smallfactory.core.v1.vision import (
    ask_image as vlm_ask_image,
    extract_invoice_part as vlm_extract_invoice_part,
)

app = Flask(__name__)
app.secret_key = os.environ.get('SF_WEB_SECRET', 'dev-only-insecure-secret')

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
            # Use default location from sfdatarepo.yml if location omitted
            inventory_post(datarepo_path, sfid, delta, location)
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
        inventory_post(datarepo_path, item_id, delta, location)
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

        # Structure presence per PLM SPEC (best-effort; directories may be optional)
        ent_dir = Path(datarepo_path) / "entities" / sfid
        design_dir = ent_dir / "design"
        revisions_dir = ent_dir / "revisions"
        refs_dir = ent_dir / "refs"
        structure = {
            'has_design': design_dir.exists(),
            'has_design_src': (design_dir / "src").exists(),
            'has_design_exports': (design_dir / "exports").exists(),
            'has_design_docs': (design_dir / "docs").exists(),
            'has_revisions': revisions_dir.exists(),
            'has_refs': refs_dir.exists(),
            'released_rev': None,
        }
        try:
            rel_fp = refs_dir / "released"
            if rel_fp.exists():
                structure['released_rev'] = (rel_fp.read_text() or '').strip() or None
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

        return render_template('entities/view.html', entity=entity, bom_rows=bom_rows, structure=structure)
    except Exception as e:
        flash(f'Error viewing entity: {e}', 'error')
        return redirect(url_for('entities_list'))


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
            entity = create_entity(datarepo_path, sfid, fields)
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
    """Edit fields for an existing entity (sfid is immutable)."""
    try:
        datarepo_path = get_datarepo_path()
        entity = get_entity(datarepo_path, sfid)
        if request.method == 'POST':
            # Collect updates (exclude sfid). Ignore blank values to avoid accidental clears.
            updates = {}
            for k, v in request.form.items():
                if k == 'sfid':
                    continue
                val = v.strip()
                if val != '':
                    updates[k] = val
            if updates:
                entity = update_entity_fields(datarepo_path, sfid, updates)
                flash('Entity updated successfully', 'success')
                return redirect(url_for('entities_view', sfid=sfid))
            else:
                flash('No changes to update', 'info')
        return render_template('entities/edit.html', entity=entity)
    except Exception as e:
        flash(f'Error editing entity: {e}', 'error')
        return redirect(url_for('entities_view', sfid=sfid))


@app.route('/entities/<sfid>/retire', methods=['POST'])
def entities_retire(sfid):
    """Soft-delete an entity by marking it as retired."""
    try:
        datarepo_path = get_datarepo_path()
        reason = request.form.get('reason', '').strip() or None
        retire_entity(datarepo_path, sfid, reason=reason)
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
        updated = update_entity_fields(datarepo_path, sfid, updates)
        return jsonify({'success': True, 'entity': updated})
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
                'alternates_group': line.get('alternates_group')
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


# Deep BOM traversal (recursive)
def _walk_bom_deep(datarepo_path: Path, parent_sfid: str, *, max_depth: int | None = None):
    """Return a flat list of deep BOM nodes with metadata.

    Each node: parent, use, name, qty, rev, level, is_alt, alternates_group, cumulative_qty, cycle
    - level: 1 for immediate children of parent
    - is_alt: True if node came from an alternate list
    - cumulative_qty: multiplied along the path when quantities are integers, else None
    - cycle: True if 'use' already appears in the current path; recursion stops on cycles
    """
    nodes = []

    def _get_name(sfid: str) -> str:
        try:
            ent = get_entity(datarepo_path, sfid)
            return ent.get('name', sfid)
        except Exception:
            return sfid

    def _mul(a, b):
        try:
            ai = int(a)
            bi = int(b)
            return ai * bi
        except Exception:
            return None

    def _dfs(cur_parent: str, level: int, path_stack: list[str], cum_qty: int | None):
        try:
            blist = bom_list(datarepo_path, cur_parent)
        except Exception:
            blist = []
        if not isinstance(blist, list):
            blist = []

        for line in blist:
            if not isinstance(line, dict):
                continue
            use = str(line.get('use', '')).strip()
            if not use:
                continue
            qty = line.get('qty', 1) or 1
            rev = line.get('rev', 'released') or 'released'
            is_cycle = use in path_stack
            cqty = _mul(cum_qty if cum_qty is not None else 1, qty)
            node = {
                'parent': cur_parent,
                'use': use,
                'name': _get_name(use),
                'qty': qty,
                'rev': rev,
                'level': level,
                'is_alt': False,
                'alternates_group': line.get('alternates_group'),
                'cumulative_qty': cqty,
                'cycle': bool(is_cycle),
            }
            nodes.append(node)

            # Recurse into primary child if part and within depth and not a cycle
            if not is_cycle and use.startswith('p_'):
                if max_depth is None or level < max_depth:
                    _dfs(use, level + 1, path_stack + [use], cqty)

            # Alternates (each as its own node one level deeper)
            alts = line.get('alternates')
            if isinstance(alts, list):
                for alt in alts:
                    if not isinstance(alt, dict):
                        continue
                    aus = str(alt.get('use', '')).strip()
                    if not aus:
                        continue
                    a_cycle = aus in path_stack
                    acqty = _mul(cum_qty if cum_qty is not None else 1, qty)
                    a_node = {
                        'parent': cur_parent,
                        'use': aus,
                        'name': _get_name(aus),
                        'qty': qty,
                        'rev': rev,
                        'level': level + 1,
                        'is_alt': True,
                        'alternates_group': line.get('alternates_group'),
                        'cumulative_qty': acqty,
                        'cycle': bool(a_cycle),
                    }
                    nodes.append(a_node)
                    if not a_cycle and aus.startswith('p_'):
                        if max_depth is None or (level + 1) < max_depth:
                            _dfs(aus, level + 2, path_stack + [aus], acqty)

    # Start traversal at level 1 (children of parent)
    _dfs(parent_sfid, 1, [parent_sfid], 1)
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
            headers = ['parent', 'use', 'name', 'qty', 'rev', 'level', 'is_alt', 'alternates_group', 'cumulative_qty', 'cycle']
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
        res = bom_add_line(
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
        res = bom_remove_line(
            datarepo_path,
            sfid,
            index=index,
            use=use,
            remove_all=bool(remove_all),
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
        res = bom_set_line(datarepo_path, sfid, index=index, updates=updates, check_exists=bool(payload.get('check_exists', True)))
        bom = res.get('bom')
        return jsonify({'success': True, 'result': res, 'rows': _enrich_bom_rows(datarepo_path, bom)})
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
        res = bom_alt_add(datarepo_path, sfid, index=index, alt_use=alt_use, check_exists=bool(check_exists))
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
        res = bom_alt_remove(datarepo_path, sfid, index=index, alt_index=alt_index, alt_use=alt_use)
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
