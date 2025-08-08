#!/usr/bin/env python3
"""
Smallfactory Web UI - Flask application providing a modern web interface
for the Git-native PLM system.
"""

from flask import Flask, render_template, request, jsonify, redirect, url_for, flash
from pathlib import Path
import json
import sys
import os

# Add the parent directory to Python path to import smallfactory modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from smallfactory.core.v1.config import get_datarepo_path, get_inventory_field_specs
from smallfactory.core.v1.inventory import (
    list_items,
    view_item,
    add_item,
    update_item,
    delete_item,
    adjust_quantity
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
    """Add a new inventory item."""
    field_specs = get_inventory_field_specs()
    form_data = {}
    
    if request.method == 'POST':
        # Always preserve form data for potential re-display
        form_data = {key: value for key, value in request.form.items() if value.strip()}
        
        try:
            # Build item dict from form data
            item_data = {}
            for field_name in field_specs.keys():
                value = request.form.get(field_name, '').strip()
                if value:  # Only include non-empty values
                    item_data[field_name] = value
            
            # Add any custom fields
            for key, value in request.form.items():
                if key not in field_specs and key.strip() and value.strip():
                    item_data[key] = value.strip()
            
            datarepo_path = get_datarepo_path()
            # Proactively prevent duplicate IDs for better UX
            candidate_id = item_data.get("id")
            if candidate_id:
                try:
                    _ = view_item(datarepo_path, candidate_id)
                    # If no exception, the item exists already
                    flash(f"Inventory item '{candidate_id}' already exists. Choose a different ID.", 'error')
                    return render_template('inventory/add.html', field_specs=field_specs, form_data=form_data)
                except FileNotFoundError:
                    pass  # OK, proceed to create
                except Exception as ve:
                    # Unexpected error when checking existence
                    flash(f"Error validating ID: {ve}", 'error')
                    return render_template('inventory/add.html', field_specs=field_specs, form_data=form_data)

            add_item(datarepo_path, item_data)
            flash(f'Successfully added inventory item: {item_data.get("id")}', 'success')
            return redirect(url_for('inventory_view', item_id=item_data.get("id")))
        except Exception as e:
            flash(f'Error adding item: {e}', 'error')
            # Form data is already preserved in form_data variable for re-display
    
    return render_template('inventory/add.html', field_specs=field_specs, form_data=form_data)

@app.route('/inventory/<item_id>/edit', methods=['GET', 'POST'])
def inventory_edit(item_id):
    """Edit an existing inventory item's metadata."""
    try:
        datarepo_path = get_datarepo_path()
        item = view_item(datarepo_path, item_id)
        field_specs = get_inventory_field_specs()
        
        if request.method == 'POST':
            field = request.form.get('field')
            value = request.form.get('value', '').strip()
            
            if not field:
                flash('Field name is required', 'error')
            else:
                try:
                    update_item(datarepo_path, item_id, field, value)
                    flash(f'Successfully updated {field}', 'success')
                    return redirect(url_for('inventory_view', item_id=item_id))
                except Exception as e:
                    flash(f'Error updating item: {e}', 'error')
        
        return render_template('inventory/edit.html', item=item, field_specs=field_specs)
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

@app.errorhandler(404)
def not_found(error):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_error(error):
    return render_template('error.html', error='Internal server error'), 500

if __name__ == '__main__':
    import os
    import sys
    
    print("🏭 Starting Smallfactory Web UI...")
    print("📍 Access the interface at: http://localhost:8080")
    print("🔧 Git-native PLM for 1-2 person teams")
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
        print("\n👋 Shutting down Smallfactory Web UI...")
    except Exception as e:
        print(f"❌ Error starting web server: {e}")
        sys.exit(1)
