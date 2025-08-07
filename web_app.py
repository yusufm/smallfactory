#!/usr/bin/env python3
"""
Smallfactory Web Interface

A lightweight web interface for the smallfactory CLI tool.
Provides REST API endpoints and a simple web UI for inventory management.
"""

import os
import sys
import json
import subprocess
import tempfile
from pathlib import Path
from flask import Flask, request, jsonify, render_template, redirect, url_for, flash
from werkzeug.exceptions import BadRequest

app = Flask(__name__)
app.secret_key = 'smallfactory-web-interface'

# Path to the sf.py CLI tool
SF_CLI_PATH = Path(__file__).parent / "sf.py"

def run_sf_command(command_args, output_format="json"):
    """
    Execute a smallfactory CLI command and return the result.
    """
    try:
        cmd = [sys.executable, str(SF_CLI_PATH)] + command_args
        if output_format in ["json", "yaml"]:
            cmd.extend(["--output", output_format])
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=SF_CLI_PATH.parent
        )
        
        if result.returncode != 0:
            return {"error": result.stderr.strip() or result.stdout.strip()}, result.returncode
        
        # Try to parse JSON output if format is json
        if output_format == "json" and result.stdout.strip():
            try:
                return json.loads(result.stdout), 0
            except json.JSONDecodeError:
                pass
        
        return {"output": result.stdout.strip()}, 0
    except Exception as e:
        return {"error": str(e)}, 1

# API Routes
@app.route('/api/inventory', methods=['GET'])
def api_list_inventory():
    """List all inventory items"""
    result, code = run_sf_command(["inventory-list"], "json")
    if code != 0:
        return jsonify(result), 400
    return jsonify(result)

@app.route('/api/inventory/<sku>', methods=['GET'])
def api_get_inventory_item(sku):
    """Get a specific inventory item"""
    result, code = run_sf_command(["inventory-view", sku], "json")
    if code != 0:
        return jsonify(result), 404 if "not found" in str(result.get("error", "")).lower() else 400
    return jsonify(result)

@app.route('/api/inventory', methods=['POST'])
def api_add_inventory():
    """Add a new inventory item"""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400
    
    # Required fields
    required_fields = ['sku', 'name', 'quantity', 'location']
    for field in required_fields:
        if field not in data:
            return jsonify({"error": f"Missing required field: {field}"}), 400
    
    # Build command arguments
    args = ["inventory-add"]
    for key, value in data.items():
        args.append(f"{key}={value}")
    
    result, code = run_sf_command(args, "json")
    if code != 0:
        return jsonify(result), 400
    return jsonify(result), 201

@app.route('/api/inventory/<sku>', methods=['PUT'])
def api_update_inventory(sku):
    """Update an inventory item"""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400
    
    # Update each field
    for field, value in data.items():
        result, code = run_sf_command(["inventory-update", sku, field, str(value)], "json")
        if code != 0:
            return jsonify(result), 400
    
    # Return updated item
    return api_get_inventory_item(sku)

@app.route('/api/inventory/<sku>/adjust', methods=['POST'])
def api_adjust_inventory(sku):
    """Adjust inventory quantity"""
    data = request.get_json()
    if not data or 'delta' not in data:
        return jsonify({"error": "Missing delta value"}), 400
    
    try:
        delta = int(data['delta'])
    except ValueError:
        return jsonify({"error": "Delta must be an integer"}), 400
    
    result, code = run_sf_command(["inventory-adjust", sku, str(delta)], "json")
    if code != 0:
        return jsonify(result), 400
    return jsonify(result)

@app.route('/api/inventory/<sku>', methods=['DELETE'])
def api_delete_inventory(sku):
    """Delete an inventory item"""
    result, code = run_sf_command(["inventory-delete", sku], "json")
    if code != 0:
        return jsonify(result), 404 if "not found" in str(result.get("error", "")).lower() else 400
    return jsonify(result)

@app.route('/api/datarepo', methods=['POST'])
def api_create_datarepo():
    """Create a new datarepo"""
    data = request.get_json()
    if not data or 'path' not in data:
        return jsonify({"error": "Missing path"}), 400
    
    result, code = run_sf_command(["create", data['path']])
    if code != 0:
        return jsonify(result), 400
    return jsonify(result), 201

# Web UI Routes
@app.route('/')
def index():
    """Main dashboard"""
    return render_template('index.html')

@app.route('/inventory')
def inventory_list():
    """Inventory list page"""
    result, code = run_sf_command(["inventory-list"], "json")
    items = []
    if code == 0 and isinstance(result, list):
        items = result
    elif code != 0:
        flash(f"Error loading inventory: {result.get('error', 'Unknown error')}", 'error')
    return render_template('inventory.html', items=items)

@app.route('/inventory/add', methods=['GET', 'POST'])
def add_inventory():
    """Add inventory item page"""
    if request.method == 'POST':
        # Get form data
        sku = request.form.get('sku', '').strip()
        name = request.form.get('name', '').strip()
        quantity = request.form.get('quantity', '').strip()
        location = request.form.get('location', '').strip()
        notes = request.form.get('notes', '').strip()
        
        # Validate required fields
        if not all([sku, name, quantity, location]):
            flash('All required fields must be filled', 'error')
            return render_template('add_inventory.html')
        
        # Build command
        args = ["inventory-add", f"sku={sku}", f"name={name}", f"quantity={quantity}", f"location={location}"]
        if notes:
            args.append(f"notes={notes}")
        
        result, code = run_sf_command(args)
        if code == 0:
            flash(f'Successfully added inventory item: {sku}', 'success')
            return redirect(url_for('inventory_list'))
        else:
            flash(f"Error adding item: {result.get('error', 'Unknown error')}", 'error')
    
    return render_template('add_inventory.html')

@app.route('/inventory/<sku>')
def view_inventory(sku):
    """View inventory item details"""
    result, code = run_sf_command(["inventory-view", sku], "json")
    if code != 0:
        flash(f"Error loading item: {result.get('error', 'Item not found')}", 'error')
        return redirect(url_for('inventory_list'))
    return render_template('view_inventory.html', item=result, sku=sku)

@app.route('/inventory/<sku>/edit', methods=['GET', 'POST'])
def edit_inventory(sku):
    """Edit inventory item"""
    if request.method == 'POST':
        # Update fields that were provided
        updates = {}
        for field in ['name', 'quantity', 'location', 'notes']:
            value = request.form.get(field, '').strip()
            if value:
                updates[field] = value
        
        # Apply updates
        success = True
        for field, value in updates.items():
            result, code = run_sf_command(["inventory-update", sku, field, value])
            if code != 0:
                flash(f"Error updating {field}: {result.get('error', 'Unknown error')}", 'error')
                success = False
                break
        
        if success:
            flash(f'Successfully updated inventory item: {sku}', 'success')
            return redirect(url_for('view_inventory', sku=sku))
    
    # Get current item data
    result, code = run_sf_command(["inventory-view", sku], "json")
    if code != 0:
        flash(f"Error loading item: {result.get('error', 'Item not found')}", 'error')
        return redirect(url_for('inventory_list'))
    
    return render_template('edit_inventory.html', item=result, sku=sku)

@app.route('/inventory/<sku>/adjust', methods=['POST'])
def adjust_inventory(sku):
    """Adjust inventory quantity"""
    delta = request.form.get('delta', '').strip()
    if not delta:
        flash('Delta value is required', 'error')
        return redirect(url_for('view_inventory', sku=sku))
    
    try:
        delta = int(delta)
    except ValueError:
        flash('Delta must be a number', 'error')
        return redirect(url_for('view_inventory', sku=sku))
    
    result, code = run_sf_command(["inventory-adjust", sku, str(delta)])
    if code == 0:
        flash(f'Successfully adjusted quantity by {delta}', 'success')
    else:
        flash(f"Error adjusting quantity: {result.get('error', 'Unknown error')}", 'error')
    
    return redirect(url_for('view_inventory', sku=sku))

@app.route('/inventory/<sku>/delete', methods=['POST'])
def delete_inventory(sku):
    """Delete inventory item"""
    result, code = run_sf_command(["inventory-delete", sku])
    if code == 0:
        flash(f'Successfully deleted inventory item: {sku}', 'success')
    else:
        flash(f"Error deleting item: {result.get('error', 'Unknown error')}", 'error')
    
    return redirect(url_for('inventory_list'))

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=8080)
