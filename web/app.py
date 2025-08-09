#!/usr/bin/env python3
"""
smallFactory Web UI - Flask application providing a modern web interface
for the Git-native PLM system.
"""

from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, send_file
from pathlib import Path
import json
import sys
import os
import base64
import io
from PIL import Image

# Add the parent directory to Python path to import smallfactory modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from smallfactory.core.v1.config import get_datarepo_path, get_inventory_field_specs, get_entity_field_specs_for_sfid
from smallfactory.core.v1.inventory import (
    list_items,
    view_item,
    add_item,
    delete_item,
    adjust_quantity
)
from smallfactory.core.v1.entities import (
    list_entities,
    get_entity,
    create_entity,
    update_entity_fields,
    retire_entity,
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
app.secret_key = 'smallfactory-web-ui-secret-key-change-in-production'

@app.route('/')
def index():
    """Main dashboard showing overview of the system."""
    try:
        datarepo_path = get_datarepo_path()
        items = list_items(datarepo_path)
        total_items = len(items)
        total_quantity = sum(item.get('quantity', 0) for item in items)
        
        # Get recent items (last 5)
        recent_items = items[-5:] if items else []
        
        return render_template('index.html', 
                             total_items=total_items,
                             total_quantity=total_quantity,
                             recent_items=recent_items,
                             datarepo_path=str(datarepo_path))
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
        items = list_items(datarepo_path)
        field_specs = get_inventory_field_specs()
        return render_template('inventory/list.html', items=items, field_specs=field_specs)
    except Exception as e:
        return render_template('error.html', error=str(e))

@app.route('/inventory/<item_id>')
def inventory_view(item_id):
    """View details of a specific inventory item."""
    try:
        datarepo_path = get_datarepo_path()
        item = view_item(datarepo_path, item_id)
        field_specs = get_inventory_field_specs()
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
    
    if request.method == 'POST':
        # Always preserve form data for potential re-display
        form_data = {key: value for key, value in request.form.items() if str(value).strip()}

        try:
            # Extract required fields for adjustment
            sfid = request.form.get('sfid', '').strip()
            location = request.form.get('location', '').strip()
            delta_raw = request.form.get('delta', '0').strip()

            if not sfid:
                raise ValueError("Missing required field: sfid")
            if not location:
                raise ValueError("Missing required field: location")
            try:
                delta = int(delta_raw)
            except Exception:
                raise ValueError("delta must be an integer (can be negative)")

            datarepo_path = get_datarepo_path()
            adjust_quantity(datarepo_path, sfid, delta, location)
            flash(f"Successfully adjusted '{sfid}' at {location} by {delta}", 'success')
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
        _ = view_item(datarepo_path, item_id)
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
        location = request.form.get('location', '').strip() or None
        
        adjust_quantity(datarepo_path, item_id, delta, location)
        flash(f'Successfully adjusted quantity by {delta}', 'success')
    except Exception as e:
        flash(f'Error adjusting quantity: {e}', 'error')
    
    return redirect(url_for('inventory_view', item_id=item_id))

@app.route('/inventory/<item_id>/delete', methods=['POST'])
def inventory_delete(item_id):
    """Delete an inventory item."""
    try:
        datarepo_path = get_datarepo_path()
        delete_item(datarepo_path, item_id)
        flash(f'Successfully deleted inventory item: {item_id}', 'success')
        return redirect(url_for('inventory_list'))
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
        return render_template('entities/view.html', entity=entity)
    except Exception as e:
        flash(f'Error viewing entity: {e}', 'error')
        return redirect(url_for('entities_list'))


@app.route('/entities/add', methods=['GET', 'POST'])
def entities_add():
    """Create a new canonical entity."""
    form_data = {}
    if request.method == 'POST':
        form_data = {k: v for k, v in request.form.items() if v.strip()}
        sfid = form_data.get('sfid', '').strip()
        try:
            if not sfid:
                raise ValueError('sfid is required')
            # Build fields dict excluding sfid
            fields = {k: v for k, v in form_data.items() if k != 'sfid'}
            datarepo_path = get_datarepo_path()
            # Proactive existence check for better UX
            try:
                _ = get_entity(datarepo_path, sfid)
                flash(f"Entity '{sfid}' already exists. Choose a different SFID.", 'error')
                return render_template('entities/add.html', form_data=form_data)
            except FileNotFoundError:
                pass
            entity = create_entity(datarepo_path, sfid, fields)
            flash(f"Successfully created entity: {sfid}", 'success')
            return redirect(url_for('entities_view', sfid=entity.get('sfid')))
        except Exception as e:
            flash(f'Error creating entity: {e}', 'error')
    return render_template('entities/add.html', form_data=form_data)


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
        fields_raw = (request.args.get('fields') or '').strip()
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
    
    print("🏭 Starting smallFactory Web UI...")
    print("📍 Access the interface at: http://localhost:8080")
    print("🔧 Git-native PLM for 1-4 person teams")
    print("=" * 50)
    
    # Check if we're in development mode
    debug_mode = os.environ.get('FLASK_ENV') == 'development' or '--debug' in sys.argv
    
    try:
        app.run(
            debug=debug_mode,
            host='0.0.0.0',
            port=8080,
            use_reloader=debug_mode
        )
    except KeyboardInterrupt:
        print("\n👋 Shutting down smallFactory Web UI...")
    except Exception as e:
        print(f"❌ Error starting web server: {e}")
        sys.exit(1)
